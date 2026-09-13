$ErrorActionPreference = "Stop"

$packageRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $packageRoot "data\runtime"
$statePath = Join-Path $runtimeRoot "current.json"
$dataDir = Join-Path $packageRoot "data\userdata"
$pidPath = Join-Path $runtimeRoot "runtime.pid"
$port = 8010
if ($env:DEMIURGE_PORT) {
  $port = [int]$env:DEMIURGE_PORT
}
if ($port -lt 1 -or $port -gt 65535) {
  throw "DEMIURGE_PORT must be between 1 and 65535."
}
$url = "http://127.0.0.1:$port"

function Test-PortOpen([int]$Port) {
  $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  return $null -ne $connection
}

function Wait-HttpReady([string]$Uri, [int]$Seconds, [System.Diagnostics.Process]$Process) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if ($Process.HasExited) { return $false }
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri "$Uri/api/health" -TimeoutSec 2
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  return $false
}

if (-not (Test-Path -LiteralPath $statePath)) {
  throw "Missing Runtime state: $statePath"
}
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeExe = Join-Path $runtimeRoot "base\$($state.base_id)\Demiurge-Runtime.exe"
if (-not (Test-Path -LiteralPath $runtimeExe)) {
  throw "Missing Runtime: $runtimeExe"
}

if (Test-Path -LiteralPath $pidPath) {
  $savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
  $existing = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
  if ($existing -and $existing.Path -eq $runtimeExe) {
    Write-Host "Demiurge is already running: $url"
    Start-Process $url | Out-Null
    exit 0
  }
  Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

if (Test-PortOpen $port) {
  throw "Port $port is already used by another process."
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$env:LAF_RUNTIME_ROOT = $runtimeRoot
$env:LAF_RUNTIME_STATE = $statePath
$env:LAF_DATA_DIR = $dataDir
$env:LAF_NO_BROWSER = "1"
$env:LAF_RUNTIME_PORT = [string]$port
$process = Start-Process -FilePath $runtimeExe -WorkingDirectory $runtimeRoot `
  -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII

Write-Host "Starting Demiurge: $url"
if (-not (Wait-HttpReady $url 120 $process)) {
  Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
  if ($process.HasExited) {
    throw "Demiurge Runtime failed with exit code $($process.ExitCode)."
  }
  throw "Demiurge Runtime was not ready within 120 seconds."
}

if ($env:DEMIURGE_NO_BROWSER -ne "1") {
  Start-Process $url | Out-Null
}
Write-Host "Demiurge started." -ForegroundColor Green
