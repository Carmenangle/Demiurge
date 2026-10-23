$ErrorActionPreference = "Continue"

$projectRoot = Split-Path -Parent $PSScriptRoot

# 杀某 PID 的子进程：uvicorn --reload 的父 reloader 持 socket，真正跑的是 spawn 子 worker；
# 父变幽灵后按 PID 杀报「找不到」，但子 worker 继承 socket 句柄导致端口不释放 → 杀子 worker 端口即放。
function Stop-ChildProcesses([int]$ParentId) {
  $children = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ParentProcessId -eq $ParentId }
  foreach ($c in $children) {
    try { Stop-Process -Id $c.ProcessId -Force -ErrorAction Stop }
    catch { Write-Host "  杀子进程 PID $($c.ProcessId) 失败：$_" }
  }
  return ($children | Measure-Object).Count
}

function Stop-PortProcess([int]$Port) {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if (-not $conns) { Write-Host "端口 $Port 无监听进程"; return }
  foreach ($processId in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
    try {
      $p = Get-Process -Id $processId -ErrorAction Stop
      Write-Host "停止 $($p.ProcessName) PID $processId（端口 $Port）"
      Stop-Process -Id $processId -Force
    } catch {
      Write-Host "PID $processId 疑似幽灵父进程，改杀其子 worker…"
      Stop-ChildProcesses $processId | Out-Null
    }
  }
  Start-Sleep -Milliseconds 300
  $still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($still) {
    foreach ($pidLeft in ($still | Select-Object -ExpandProperty OwningProcess -Unique)) {
      Stop-ChildProcesses $pidLeft | Out-Null
      try { Stop-Process -Id $pidLeft -Force -ErrorAction SilentlyContinue } catch {}
    }
  }
}

# 补杀不监听端口的 uvicorn 父 reloader（按端口杀会漏，残留占用 .venv）
function Stop-BackendByPath {
  $rootPattern = [Regex]::Escape($projectRoot)
  $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match '^python' -and $_.CommandLine -and
    $_.CommandLine -match $rootPattern -and $_.CommandLine -match 'uvicorn|app\.main'
  }
  foreach ($p in $procs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Host "停止后端 python PID $($p.ProcessId)" }
    catch { Write-Host "停止 PID $($p.ProcessId) 失败：$_" }
  }
}

# 补杀「启动中、还没监听 8188」的 ComfyUI：冷启动 import 阶段可长达数分钟，
# 此时按端口杀不掉，进程会继续占着安装树锁与 comfyui.db，下次启动又被拖 600 秒。
# 判据与后端 comfy_launcher.find_install_processes 同口径：python 进程 + main.py
# + （本安装目录或本安装解释器路径）；路径边界避免 ComfyUI 误伤 ComfyUI_TE。
function Stop-ComfyByIdentity {
  $cfgPath = Join-Path $projectRoot "backend\data\comfy_config.json"
  if (-not (Test-Path -LiteralPath $cfgPath)) { return }
  try {
    $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    Write-Host "读取 $cfgPath 失败，跳过命令行补杀：$($_.Exception.Message)" -ForegroundColor Yellow
    return
  }
  $base = ([string]$cfg.path).Trim().TrimEnd('\')
  $configured = ([string]$cfg.python_path).Trim()
  # 解释器候选与 comfy_launcher.find_python 同口径：默认 python_path 为空时，
  # launcher 子进程命令行里只有解释器路径（cwd=<base> + 相对 main.py）。
  $pyCandidates = @()
  if ($configured) {
    $pyCandidates = @($configured)
  } elseif ($base) {
    $parent = Split-Path -Parent $base
    $pyCandidates = @(
      (Join-Path $base ".venv\Scripts\python.exe"),
      (Join-Path $base "venv\Scripts\python.exe"),
      (Join-Path $base "python\python.exe")
    )
    if ($parent) {
      $pyCandidates += @(
        (Join-Path $parent "python_embeded\python.exe"),
        (Join-Path $parent "python\python.exe"),
        (Join-Path $parent "python312\python.exe")
      )
    }
  }
  $needles = @()
  foreach ($t in $pyCandidates) {
    if ($t -and (Test-Path -LiteralPath $t)) { $needles += [Regex]::Escape($t.TrimEnd('\')) }
  }
  if ($base) { $needles += [Regex]::Escape($base) }
  if ($needles.Count -eq 0) { return }
  foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    if ($p.Name -notmatch '^python' -or -not $p.CommandLine) { continue }
    if ($p.CommandLine -notmatch 'main\.py') { continue }
    $hit = $false
    foreach ($n in $needles) {
      if ($p.CommandLine -match ('(?i)' + $n + '(?=[\\"''\s]|$)')) { $hit = $true; break }
    }
    if (-not $hit) { continue }
    try {
      Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
      Write-Host "停止 ComfyUI python PID $($p.ProcessId)（命令行匹配，未监听端口）"
    } catch {
      Write-Host "停止 ComfyUI PID $($p.ProcessId) 失败：$_"
    }
  }
}

Stop-PortProcess 5173    # 前端
Stop-PortProcess 8010    # 后端
Stop-BackendByPath       # 补杀父 reloader
Stop-ComfyByIdentity     # 补杀冷启动中的 ComfyUI
Stop-PortProcess 8188    # ComfyUI（监听端口的实例）
Write-Host "完成。" -ForegroundColor Green
