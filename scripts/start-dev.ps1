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

# 后端：隐藏窗口后台跑。ComfyUI 由后端 startup 钩子(comfy_launcher.autostart)按
# data/comfy_config.json 的路径自动在后台拉起，脚本不重复管（避免两处维护/冲突）。
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

# ── ComfyUI：真源仍是后端 comfy_launcher（唯一持有子进程句柄的地方），脚本只负责
# 「探活 + 在后端已在跑时补发一次启动指令」。背景（2026-09-23 实锤）：后端 startup 钩子
# 只在后端进程**新启动**时跑一次；若 ComfyUI 之后退出（OOM／端口竞争／被手动关掉），再跑
# 本脚本会因「后端已在运行」跳过整段启动 ⇒ ComfyUI 永远不再被拉起。前端的自动拉起只在
# 发 /s 或手动点按钮时懒触发，所以「启动开发环境」这一步此前是空缺。
$comfyCheckStart = Get-Date
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
  Write-Host "ComfyUI 未运行，通知后端拉起（真源：comfy_launcher）…"
  $comfyJson = @{ path = [string]$comfyCfg.path; url = $comfyUrl; python_path = [string]$comfyCfg.python_path } | ConvertTo-Json -Compress
  # PS 5.1 的 -Body <string> 固定按 ISO-8859-1 发送，路径含中文会静默损坏 ⇒ 显式传 UTF-8 字节
  $comfyBytes = [System.Text.Encoding]::UTF8.GetBytes($comfyJson)
  # 必须用 Invoke-WebRequest + 手动 UTF-8 解码：PS 5.1 的 Invoke-RestMethod 在响应头不带
  # charset 时按 ISO-8859-1 解码 JSON 响应体，而 FastAPI 的 application/json 恰好不带 charset
  # ⇒ 中文 message 会落成「å·²å¯å¨」这类乱码（2026-09-23 实测）。
  try {
    $comfyResp = Invoke-WebRequest -Method Post -Uri "http://127.0.0.1:8010/api/comfyui/start" -ContentType "application/json; charset=utf-8" -Body $comfyBytes -TimeoutSec 20 -UseBasicParsing
    $comfyMsg = ""
    try {
      $comfyText = [System.Text.Encoding]::UTF8.GetString($comfyResp.RawContentStream.ToArray())
      $comfyObj = $comfyText | ConvertFrom-Json
      $comfyMsg = [string]$comfyObj.message
    } catch { }
    if (-not $comfyMsg) { $comfyMsg = "已发出启动指令" }
    Write-Host "ComfyUI：$comfyMsg（插件加载慢，通常 1~3 分钟才能访问）"
  } catch {
    $comfyErr = ""
    try {
      $comfyStream = $_.Exception.Response.GetResponseStream()
      if ($comfyStream) {
        $comfyErr = [System.IO.StreamReader]::new($comfyStream, [System.Text.Encoding]::UTF8).ReadToEnd()
      }
    } catch { }
    if (-not $comfyErr) { $comfyErr = $_.Exception.Message }
    Write-Host "拉起 ComfyUI 失败：$comfyErr" -ForegroundColor Yellow
  }
}

# 残留侦测（只报告、不处理）：命令行含 ComfyUI main.py、创建时间早于本次脚本、却没有任何
# 监听端口的 python 实例，多半是启动早期卡死或被强杀的僵尸。它会占住 ComfyUI\user\comfyui.db
# 的排他锁，让新实例报「Another ComfyUI process may already be using it」；而 stop 只按 8188
# 端口杀，恰恰清不掉这种不监听端口的残留。（2026-09-23 实锤：15:45 留下的僵尸挂了 1 小时 20 分。）
$staleComfy = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'ComfyUI' -and $_.CommandLine -match 'main\.py' } |
  ForEach-Object {
    $ownPid = [int]$_.ProcessId
    if ($_.CreationDate -and $_.CreationDate -lt $comfyCheckStart) {
      $listen = @(Get-NetTCPConnection -State Listen -OwningProcess $ownPid -ErrorAction SilentlyContinue)
      if ($listen.Count -eq 0) { $staleComfy += "$ownPid（启动于 $($_.CreationDate)）" }
    }
  }
if ($staleComfy.Count -gt 0) {
  Write-Host "[WARN] 发现 $($staleComfy.Count) 个未监听端口的 ComfyUI 残留：$($staleComfy -join '；')" -ForegroundColor Yellow
  Write-Host "       确认无用后可清理：taskkill /F /T /PID <pid>（它们会锁住 ComfyUI\user\comfyui.db）。" -ForegroundColor Yellow
}
$aiHelper = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)
if ($aiHelper.Count -gt 0) {
  Write-Host "[INFO] 8080 被 pid=$($aiHelper[0].OwningProcess) 占用：Comfyui-zhenzhen 插件的 AiHelper 辅助服务（端口硬编码，不致命）。" -ForegroundColor DarkGray
}

Write-Host "打开浏览器：$frontendUrl"
Start-Process $frontendUrl | Out-Null
Write-Host "完成。停止请运行 stop-dev.bat；强制重启后端：scripts/start-dev.ps1 -RestartBackend" -ForegroundColor Green
