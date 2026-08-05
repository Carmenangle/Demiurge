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

# 后端：隐藏窗口后台跑。ComfyUI 由后端 startup 钩子(comfy_launcher.autostart)按
# data/comfy_config.json 的路径自动在后台拉起，脚本不重复管（避免两处维护/冲突）。
if (Test-PortOpen 8010) {
  Write-Host "后端已在运行 http://127.0.0.1:8010"
} else {
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

Write-Host "打开浏览器：$frontendUrl"
Start-Process $frontendUrl | Out-Null
Write-Host "完成。停止请运行 stop-dev.bat。" -ForegroundColor Green
