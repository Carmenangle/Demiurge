$ErrorActionPreference = "Stop"

$packageRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $packageRoot "data\runtime"
$statePath = Join-Path $runtimeRoot "current.json"
$pidPath = Join-Path $runtimeRoot "runtime.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
  Write-Host "Demiurge was not started by this package."
  exit 0
}
if (-not (Test-Path -LiteralPath $statePath)) {
  throw "Missing Runtime state; refusing to stop an unknown PID."
}

$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeExe = Join-Path $runtimeRoot "base\$($state.base_id)\Demiurge-Runtime.exe"
$savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue

if (-not $process) {
  Remove-Item -LiteralPath $pidPath -Force
  Write-Host "Demiurge is not running; stale PID removed."
  exit 0
}
if ($process.Path -ne $runtimeExe) {
  throw "PID $savedPid does not belong to this package; refusing to stop it."
}

Stop-Process -Id $savedPid -Force
Wait-Process -Id $savedPid -Timeout 15 -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Write-Host "Demiurge stopped." -ForegroundColor Green
