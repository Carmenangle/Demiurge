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

Stop-PortProcess 5173    # 前端
Stop-PortProcess 8010    # 后端
Stop-BackendByPath       # 补杀父 reloader
Stop-PortProcess 8188    # ComfyUI（后端 autostart 拉起的）
Write-Host "完成。" -ForegroundColor Green
