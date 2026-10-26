param(
  [string]$Token = "",
  [int]$Port = 8010,
  [switch]$NewToken,
  [switch]$OpenFirewall,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"

# 联机同库 PC 端（0.30.0）：把后端绑到 0.0.0.0 + 设 LAF_ALLOW_REMOTE=1 与
# LAF_LOCAL_TOKEN，供手机「设置 → 离线资源同步 → 联机同库」连接。
# 安全口径：allow_remote 只放开**入站来源**（回环/Origin 闸），身份仍由令牌强制；
# 危险端点由 backend/app/services/remote_gate.py 按「非回环对端」拒绝。
# 本脚本绝不动已在运行的后端（8010 被占即退出）——那是桌面端/开发环境的活进程。

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$tokenFile = Join-Path $backendDir "data\lan_token.txt"

if (-not (Test-Path -LiteralPath $backendPython)) {
  Write-Host "[ERROR] 缺后端 venv：$backendPython（先建 backend/.venv 并装依赖）" -ForegroundColor Red
  exit 1
}

function Test-PortOpen([int]$Port) {
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  return $null -ne $c
}

if (Test-PortOpen $Port) {
  Write-Host "[ERROR] 端口 $Port 已被占用，本次不启动（不会结束正在运行的后端）。" -ForegroundColor Red
  Write-Host "  占用者可能是桌面端或 start-dev；先关闭它们，或换个端口："
  Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-lan.ps1 -Port 8011"
  Write-Host "  （换端口后手机端「电脑地址」也要填对应端口）"
  exit 1
}

# ── 令牌：优先 -Token；否则复用 backend/data/lan_token.txt（重启后手机不用重填）；
# 都没有才生成。-NewToken 强制轮换（令牌泄露时用）。
if ($NewToken) { $Token = "" }
if (-not $Token -and (Test-Path -LiteralPath $tokenFile)) {
  $Token = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
}
$generated = $false
if (-not $Token) {
  $bytes = New-Object byte[] 24
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  $Token = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
  $generated = $true
  $dataDir = Split-Path -Parent $tokenFile
  if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
  }
  Set-Content -LiteralPath $tokenFile -Value $Token -Encoding ASCII -NoNewline
}
if ($Token.Length -lt 8) {
  Write-Host "[ERROR] -Token 至少 8 位（手机端校验同一口径）。" -ForegroundColor Red
  exit 1
}

# ── 防火墙：默认只打印提示；-OpenFirewall 尝试添加「专用网络」入站规则（需管理员）。
if ($OpenFirewall) {
  $ruleName = "Demiurge LAN $Port"
  try {
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
      Write-Host "防火墙入站规则已存在：$ruleName"
    } else {
      New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Action Allow -Profile Private | Out-Null
      Write-Host "已添加防火墙入站规则：$ruleName（仅专用网络）" -ForegroundColor Green
    }
  } catch {
    Write-Host "[WARN] 添加防火墙规则失败（通常需要管理员）：$($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  请以管理员身份重跑并带 -OpenFirewall，或手动放行 TCP $Port。" -ForegroundColor Yellow
  }
}

# 多网卡（Wi-Fi/以太网 + Hyper-V/WSL 虚拟网卡）都列出来，并标注网卡名：
# 手机要填能通到家里的那块（通常 Wi-Fi / 以太网），虚拟网卡地址手机会连不上。
$addrs = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
  Sort-Object @{ Expression = { if ($_.InterfaceAlias -match 'Wi-?Fi|WLAN|以太网|Ethernet') { 0 } else { 1 } } })

$env:PYTHONUTF8 = "1"
$env:LAF_ALLOW_REMOTE = "1"
$env:LAF_LOCAL_TOKEN = $Token

Write-Host ""
Write-Host "=== Demiurge 联机同库（PC 端）===" -ForegroundColor Cyan
Write-Host "监听：0.0.0.0:$Port（同一 Wi-Fi 内可访问）"
if ($addrs.Count -gt 0) {
  Write-Host "手机端「电脑地址」填以下任一（括号里是网卡名，优先 Wi-Fi / 以太网）："
  foreach ($a in $addrs) {
    Write-Host ("  http://{0}:{1}  ({2})" -f $a.IPAddress, $Port, $a.InterfaceAlias) -ForegroundColor Green
  }
} else {
  Write-Host "[WARN] 没找到局域网 IPv4，检查网络连接后重试。" -ForegroundColor Yellow
}
Write-Host -NoNewline "手机端「访问令牌」填："
Write-Host " $Token" -ForegroundColor Yellow
if ($generated) {
  Write-Host "（本次新生成，已存到 backend\data\lan_token.txt；下次启动自动复用）" -ForegroundColor DarkGray
} elseif (-not $PSBoundParameters.ContainsKey("Token")) {
  Write-Host "（复用 backend\data\lan_token.txt；轮换令牌加 -NewToken）" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "手机操作：设置 → 离线资源同步 → 联机同库 → 填地址与令牌 → 保存并连接 → 刷新页面"
Write-Host "安全：令牌可访问电脑端 API，只发给自己的手机；泄露后 Ctrl+C 并以 -NewToken 重启。"
Write-Host "提示：本脚本运行期间桌面端/start-dev 不能再占用同一端口；停止按 Ctrl+C。"
Write-Host ""

if ($DryRun) {
  Write-Host "（-DryRun：只打印配置，不启动后端）" -ForegroundColor DarkGray
  exit 0
}

Push-Location $backendDir
try {
  & $backendPython -m uvicorn app.main:app --host 0.0.0.0 --port $Port
} finally {
  Pop-Location
}
