# Puts a "Kalshi Bot" shortcut on the Desktop.
#   powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1
#   powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1 -Remove
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'Kalshi Bot.lnk'

if ($Remove) {
  if (Test-Path $link) { Remove-Item $link -Force; Write-Host "Removed $link" }
  else { Write-Host "No shortcut at $link" }
  exit 0
}

$target = Join-Path $root 'Kalshi Bot.cmd'
if (-not (Test-Path $target)) { throw "Launcher not found: $target" }

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $target
$sc.WorkingDirectory = $root
$sc.Description = 'Kalshi 15-minute bot tracker'
$sc.WindowStyle = 7          # start minimised: the console is only a launcher

# Use Electron's own icon so the shortcut is recognisable on the desktop.
$icon = Join-Path $root 'node_modules\electron\dist\electron.exe'
if (Test-Path $icon) { $sc.IconLocation = "$icon,0" }

$sc.Save()
Write-Host "Created $link"
Write-Host "  -> $target"
