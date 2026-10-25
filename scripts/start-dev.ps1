param([switch]$RestartBackend)
$ErrorActionPreference = "Stop"

# scripts/ 在项目根下一层，父目录即项目根（对齐源项目约定）
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$backendHealth = "http://127.0.0.1:8010/api/health"
$frontendUrl = "http://127.0.0.1:5173"

if (-not (Test-Path -LiteralPath $backendPython)) {
  Write-Host "[ERROR] 缺后端 venv：$backendPython（先建 backend/.venv 并装依赖）" -ForegroundColor Red
  exit 1
}

function Test-PortOpen([int]$Port) {
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  return $null -ne $c
}

function Wait-HttpOk([string]$Url, [int]$Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
      if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  return $false
}

# ── 幂等重启基建（2026-09-06 M1 审计根治）：uvicorn --reload 的 supervisor 死掉后，
# spawn worker 会变孤儿继续持有端口服务旧代码，且其命令行不含 uvicorn/后端路径特征、
# 按 pid 常不可见——历史上多次出现「端口在、进程查无、服务旧代码」的幽灵状态。
# 因此停后端必须：① 按命令行精确杀本项目 uvicorn（连带整棵子进程树，覆盖 spawn worker）；
# ② 端口仍被占则按监听属主 pid 连树补杀；③ 仍杀不动（幽灵监听）就大声报错，绝不带病启动。
function Stop-ProcessTree([int]$ProcessId) {
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-ProcessTree ([int]$_.ProcessId) }
  try { Stop-Process -Id $ProcessId -Force -ErrorAction Stop } catch { }
}

function Get-BackendSupervisorPids {
  # 只精确匹配「本项目后端目录 + uvicorn」的进程（绝不波及 ComfyUI 等无关 python）
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine.Contains($backendDir) -and
      $_.CommandLine -match "uvicorn"
    } |
    ForEach-Object { [int]$_.ProcessId }
}

function Stop-DevBackend {
  $pids = Get-BackendSupervisorPids
  foreach ($p in $pids) {
    Write-Host "停止后端进程树 pid=$p"
    Stop-ProcessTree $p
  }
  # 端口残留兜底（孤儿 worker / 不可见属主）：按监听属主连树补杀，直到释放或超时
  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-Date) -lt $deadline -and (Test-PortOpen 8010)) {
    $owner = (Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
      Select-Object -First 1).OwningProcess
    if (-not $owner) { break }
    Write-Host "端口 8010 仍被 pid=$owner 占用，按属主进程树补杀…"
    Stop-ProcessTree ([int]$owner)
    Start-Sleep -Milliseconds 500
  }
  if (Test-PortOpen 8010) {
    Write-Host "[ERROR] 端口 8010 被幽灵监听占用（属主进程不可见且杀不掉），" -ForegroundColor Red
    Write-Host "为避免旧代码继续服务，本次不启动。请重启机器或手动排查后重试。" -ForegroundColor Red
    return $false
  }
  return $true
}

function Test-BackendHealthy {
  # 端口开着还不够：属主必须是「本项目 uvicorn」且进程可见——防孤儿 worker 幽灵假活
  if (-not (Test-PortOpen 8010)) { return $false }
  $owner = (Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1).OwningProcess
  if (-not $owner) { return $false }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
  if (-not $proc -or -not $proc.CommandLine -or
      -not $proc.CommandLine.Contains($backendDir) -or
      $proc.CommandLine -notmatch "uvicorn") { return $false }
  return $true
}

# 后端：隐藏窗口后台跑。ComfyUI 不由后端拉起：后端可能跑在受限上下文（低完整性/
# 沙箱），它 Popen 出的 ComfyUI 继承同一完整性级别，写不了安装树——AlekPet 节点的
# FileLock(.install.lock, timeout=600) 会把冷启动拖成 600.1 秒 IMPORT FAILED
# （2026-10-23 实测），端口全程不监听。关掉后端的 autostart，改由本脚本在用户身份下
# 调 scripts\start_comfy.py 拉起（见下方 ComfyUI 段）。
$env:LAF_COMFY_AUTOSTART = "0"
$backendUp = $false
if (Test-PortOpen 8010) {
  if (Test-BackendHealthy) {
    if ($RestartBackend) {
      # 2026-09-11 修复：原来健康分支直接 $backendUp=$true 跳过重启，
      # 导致「强制重启后端」对健康后端无效（改了代码加载不进来）。
      Write-Host "-RestartBackend：后端健康，仍按要求强制重启…" -ForegroundColor Yellow
      if (-not (Stop-DevBackend)) { exit 1 }
    } else {
      $backendUp = $true
      Write-Host "后端已在运行 http://127.0.0.1:8010"
    }
  } else {
    Write-Host "检测到 8010 被非本项目的/不可见进程占用（孤儿 worker 幽灵态），清理后重启…" -ForegroundColor Yellow
    $backendUp = Stop-DevBackend
  }
}
if (-not $backendUp) {
  if ($RestartBackend -and (Test-PortOpen 8010)) {
    if (-not (Stop-DevBackend)) { exit 1 }
  }
  Write-Host "启动后端 http://127.0.0.1:8010（隐藏窗口，ComfyUI 随后端按设置自动拉起）"
  $env:PYTHONUTF8 = "1"
  Start-Process -FilePath $backendPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8010", "--reload", "--reload-dir", "app") `
    -WorkingDirectory $backendDir -WindowStyle Hidden | Out-Null
}

# 前端：隐藏窗口后台跑（vite 热更）
if (Test-PortOpen 5173) {
  Write-Host "前端已在运行 $frontendUrl"
} else {
  Write-Host "启动前端 $frontendUrl（隐藏窗口，热更）"
  Start-Process -FilePath "npm.cmd" `
    -ArgumentList @("run", "dev") `
    -WorkingDirectory $frontendDir -WindowStyle Hidden | Out-Null
}

Write-Host "等待后端就绪…"
if (-not (Wait-HttpOk $backendHealth 30)) {
  Write-Host "后端暂未就绪，可能仍在启动（首次装 Chroma/模型较慢）。" -ForegroundColor Yellow
}

# ── ComfyUI：由本脚本（用户身份）拉起，不能交给后端。后端可能运行在受限上下文
# （低完整性/沙箱），它 Popen 出的 ComfyUI 继承同一完整性级别，写不了安装树：
# AlekPet 节点的 FileLock(.install.lock, timeout=600) 把冷启动拖成 600.1 秒后才
# IMPORT FAILED（2026-10-23 实测），端口全程不监听，用户看到的就是「启动要 9 分钟」。
# 背景（2026-09-23 实锤）：后端 startup 钩子只在后端进程**新启动**时跑一次；若
# ComfyUI 之后退出（OOM／端口竞争／被手动关掉），再跑本脚本会因「后端已在运行」跳过
# 整段启动 ⇒ ComfyUI 永远不再被拉起。前端自动拉起只在发 /s 或手动点按钮时懒触发，
# 所以「启动开发环境」必须自己兜住这一段。
$comfyConfig = Join-Path $backendDir "data\comfy_config.json"
$comfyCfg = $null
$comfyUrl = "http://127.0.0.1:8188"
$comfyPort = 8188
if (Test-Path -LiteralPath $comfyConfig) {
  try {
    $comfyCfg = Get-Content -LiteralPath $comfyConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($comfyCfg.url) { $comfyUrl = [string]$comfyCfg.url }
    $comfyUri = $null
    if ([Uri]::TryCreate($comfyUrl, [UriKind]::Absolute, [ref]$comfyUri) -and $comfyUri.Port -gt 0) {
      $comfyPort = $comfyUri.Port
    }
  } catch {
    Write-Host "读取 $comfyConfig 失败，按默认 8188 处理：$($_.Exception.Message)" -ForegroundColor Yellow
  }
}

if (Test-PortOpen $comfyPort) {
  Write-Host "ComfyUI 已在运行 $comfyUrl"
} elseif (-not $comfyCfg -or -not $comfyCfg.path) {
  Write-Host "未配置 ComfyUI 目录（设置 → 路径），跳过 ComfyUI 启动。" -ForegroundColor Yellow
} elseif (-not (Test-PortOpen 8010)) {
  Write-Host "后端尚未就绪，跳过 ComfyUI 启动（稍后可在界面手动启动）。" -ForegroundColor Yellow
} else {
  # 降级来自**工作区目录的 Low 强制性标签**（D:\tool\Demiurge，文件继承）：进程完整性取
  # min(父令牌, 映像文件标签)，子进程永不高过父，所以工作区内的后端/venv python 起的
  # ComfyUI 必为 Low、写不动安装树（2026-10-24 实测：Low 后端起工作区外解释器仍 Low）。
  # 工作区标签已在 2026-10-24 改为 Medium（icacls /setintegritylevel），本链路保留为
  # 受限上下文的回退通道：规划（读配置/拼参数）与执行（spawn，必须用 ComfyUI 的
  # 解释器）拆开；可写性也用**将实际启动的那个解释器**探，不能由规划者代判。
  $comfyStarted = $false
  $comfyCliErr = ""
  $comfyPlan = $null
  $comfyPlanScript = Join-Path $projectRoot "scripts\start_comfy.py"
  $comfyProbeScript = Join-Path $projectRoot "scripts\comfy_probe_write.py"
  if (-not (Test-Path -LiteralPath $comfyPlanScript)) {
    $comfyCliErr = "缺 scripts\start_comfy.py，无法生成 ComfyUI 启动规格。"
  } else {
    # start_comfy.py 已把 stdout/stderr 重配置为 UTF-8；PS 5.1 解 native 输出按
    # [Console]::OutputEncoding，不同步会显示成乱码。
    $oldConsoleEncoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $env:PYTHONUTF8 = "1"
    try {
      $planOut = & $backendPython $comfyPlanScript --print-plan 2>&1
      $planCode = $LASTEXITCODE
      $planText = (@($planOut) | ForEach-Object { [string]$_ }) -join "`n"
      if ($planCode -eq 0 -and $planText) {
        $comfyPlan = $planText | ConvertFrom-Json
      } else {
        $comfyCliErr = $planText
      }
    } catch {
      $comfyCliErr = $_.Exception.Message
    } finally {
      [Console]::OutputEncoding = $oldConsoleEncoding
    }
  }

  if ($comfyPlan) {
    $comfyPy = [string]$comfyPlan.python
    $comfyBase = [string]$comfyPlan.cwd
    $probeOut = $null
    $probeCode = 0
    if (-not (Test-Path -LiteralPath $comfyPlanScript)) {
      $probeCode = 1
      $probeOut = @("缺 scripts\start_comfy.py")
    } elseif (-not (Test-Path -LiteralPath $comfyProbeScript)) {
      $probeCode = 1
      $probeOut = @("缺 scripts\comfy_probe_write.py，拒绝盲拉起（否则会被 AlekPet 文件锁拖 9 分钟）")
    } elseif (-not (Test-Path -LiteralPath $comfyPy)) {
      $probeCode = 1
      $probeOut = @("ComfyUI 解释器不存在：$comfyPy")
    } else {
      $probeOut = & $comfyPy $comfyProbeScript $comfyBase 2>&1
      $probeCode = $LASTEXITCODE
    }
    if ($probeCode -ne 0) {
      $probeMsg = (@($probeOut) | ForEach-Object { [string]$_ } | Where-Object { $_ }) -join "`n"
      if (-not $probeMsg) { $probeMsg = "写探测失败（exit=$probeCode）" }
      $comfyCliErr = "当前上下文写不了 ComfyUI 安装树：$comfyBase（$probeMsg）"
    } else {
      $savedEnv = @{}
      $spawnErr = ""
      try {
        foreach ($prop in $comfyPlan.env.PSObject.Properties) {
          $savedEnv[$prop.Name] = [Environment]::GetEnvironmentVariable($prop.Name, "Process")
          Set-Item -Path ("env:" + $prop.Name) -Value ([string]$prop.Value)
        }
        $planArgv = @($comfyPlan.argv | ForEach-Object { [string]$_ })
        # 用 cmd 只负责「追加写日志」：Start-Process 的 -RedirectStandardOutput 会截断，
        # 而 ComfyUI 日志是排查 600 秒锁的唯一现场。cmd 在 System32（工作区外），
        # 不改变 python 的完整性级别。
        $inner = (@($planArgv) | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' '
        $inner = $inner + ' >> "' + [string]$comfyPlan.log_path + '" 2>&1'
        $comfyProc = Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /s /c "' + $inner + '"') `
          -WorkingDirectory $comfyBase -WindowStyle Hidden -PassThru
        $comfyStarted = $true
        Write-Host "ComfyUI 已在用户上下文拉起（pid=$($comfyProc.Id)，解释器：$comfyPy）"
      } catch {
        $spawnErr = $_.Exception.Message
      } finally {
        foreach ($k in @($savedEnv.Keys)) {
          if ($null -eq $savedEnv[$k]) {
            Remove-Item -Path ("env:" + $k) -ErrorAction SilentlyContinue
          } else {
            Set-Item -Path ("env:" + $k) -Value $savedEnv[$k]
          }
        }
      }
      if (-not $comfyStarted) {
        $comfyCliErr = "拉起 ComfyUI 失败：$spawnErr"
      }
    }
  }
}

# ComfyUI 没起来时不要以「完成」收尾：必须把可执行的下一步说清楚。
$comfyFinalErr = ""
if (-not $comfyStarted -and $comfyCfg -and $comfyCfg.path -and -not (Test-PortOpen $comfyPort)) {
  if ($comfyCliErr) { $comfyFinalErr = $comfyCliErr }
  else { $comfyFinalErr = "未生成启动规格（原因未知）" }
}
if ($comfyFinalErr) {
  Write-Host ""
  Write-Host "[WARN] ComfyUI 未启动：$comfyFinalErr" -ForegroundColor Red
  Write-Host "  聊天与云端生图不受影响；本地生图/工作流会不可用。" -ForegroundColor Yellow
  if ($comfyFinalErr -match '受限上下文|安装树|写不了') {
    Write-Host "  当前窗口继承的是受限（低完整性）上下文。" -ForegroundColor Yellow
    Write-Host "  处理：从资源管理器双击 start-dev.bat（普通用户上下文）；若 ComfyUI 装在工作区内，请移到工作区外。" -ForegroundColor Yellow
  } else {
    Write-Host "  按上面的错误排查后重跑 start-dev.bat。" -ForegroundColor Yellow
  }
}

# 残留侦测 + 自动清理（第 59 刀，2026-09-25 用户需求）：命令行含 ComfyUI main.py、却没有任何
# 监听端口的 python 实例 ⇒ 僵尸（占住 ComfyUI\user\comfyui.db 的排他锁，让新实例报
# 「Another ComfyUI process may already be using it」；而 stop 只按 8188 端口杀，清不掉这种残留）。
# ⚠ 自动清理带 **10 分钟宽限窗**：ComfyUI 加载插件要 1~3 分钟才开监听，刚拉起的实例
#   （无监听属正常初始化）绝不能误杀——只清「创建早于 10 分钟前且零监听」的真僵尸。
#   （2026-09-23 实锤：15:45 留下的僵尸挂了 1 小时 20 分，当时只报告不处理。）
$staleComfy = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'ComfyUI' -and $_.CommandLine -match 'main\.py' } |
  ForEach-Object {
    $ownPid = [int]$_.ProcessId
    $staleCutoff = (Get-Date).AddMinutes(-10)
    if ($_.CreationDate -and $_.CreationDate -lt $staleCutoff) {
      $listen = @(Get-NetTCPConnection -State Listen -OwningProcess $ownPid -ErrorAction SilentlyContinue)
      if ($listen.Count -eq 0) { $staleComfy += $_ }
    }
  }
if ($staleComfy.Count -gt 0) {
  $cleaned = @()
  foreach ($proc in $staleComfy) {
    $ownPid = [int]$proc.ProcessId
    taskkill /F /T /PID $ownPid 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
      $cleaned += "$ownPid（启动于 $($proc.CreationDate)）"
    } else {
      Write-Host "[WARN] 僵尸 ComfyUI pid=$ownPid 清理失败（可能权限不足），请手动：taskkill /F /T /PID $ownPid" -ForegroundColor Yellow
    }
  }
  if ($cleaned.Count -gt 0) {
    Write-Host "[INFO] 已自动清理 $($cleaned.Count) 个 ComfyUI 僵尸（无监听、创建早于 10 分钟前）：$($cleaned -join '；')" -ForegroundColor DarkGray
  }
}
$aiHelper = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)
if ($aiHelper.Count -gt 0) {
  Write-Host "[INFO] 8080 被 pid=$($aiHelper[0].OwningProcess) 占用：Comfyui-zhenzhen 插件的 AiHelper 辅助服务（端口硬编码，不致命）。" -ForegroundColor DarkGray
}

Write-Host "打开浏览器：$frontendUrl"
Start-Process $frontendUrl | Out-Null
if ($comfyFinalErr) {
  Write-Host "开发环境已启动（ComfyUI 未启动，见上方 WARN）。停止请运行 stop-dev.bat" -ForegroundColor Yellow
} else {
  Write-Host "完成。停止请运行 stop-dev.bat；强制重启后端：scripts/start-dev.ps1 -RestartBackend" -ForegroundColor Green
}
