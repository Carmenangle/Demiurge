param([string]$DestinationDir = "")

$ErrorActionPreference = "Stop"

$packageRoot = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $packageRoot "Demiurge.exe"
if (-not (Test-Path -LiteralPath $exe)) {
  throw "Missing desktop shell: $exe"
}

$folders = @()
if ($DestinationDir) {
  $folders += (New-Item -ItemType Directory -Force -Path $DestinationDir).FullName
} else {
  $folders += [Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
  $folders += [Environment]::GetFolderPath([System.Environment+SpecialFolder]::Programs)
}

$wshell = New-Object -ComObject WScript.Shell
foreach ($folder in $folders) {
  if (-not $folder) { continue }
  New-Item -ItemType Directory -Force -Path $folder | Out-Null
  $link = Join-Path $folder "Demiurge.lnk"
  $shortcut = $wshell.CreateShortcut($link)
  $shortcut.TargetPath = $exe
  $shortcut.WorkingDirectory = $packageRoot
  $shortcut.IconLocation = "$exe,0"
  $shortcut.Description = "Demiurge"
  $shortcut.Save()
  Write-Host "Shortcut created: $link"
}
