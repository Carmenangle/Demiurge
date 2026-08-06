param([Parameter(Mandatory = $true)][string]$Manifest)
$ErrorActionPreference = "Stop"
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$base = Split-Path -Parent $manifestPath
$info = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
$output = Join-Path $base $info.archive
$stream = [System.IO.File]::Create($output)
try {
  foreach ($part in $info.parts) {
    $input = [System.IO.File]::OpenRead((Join-Path $base $part))
    try { $input.CopyTo($stream) } finally { $input.Dispose() }
  }
} finally { $stream.Dispose() }
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash.ToLowerInvariant()
if ($hash -ne $info.sha256) { throw "合并后的 SHA256 不匹配" }
Write-Host "已还原：$output"
