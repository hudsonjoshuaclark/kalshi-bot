@echo off
rem Desktop launcher for the Kalshi Bot tracker.
rem Opens the window; the window starts and stops the bot process itself.
cd /d "%~dp0"
if not exist "node_modules\electron\dist\electron.exe" (
  echo Electron is not installed. Run:  npm install
  pause
  exit /b 1
)
start "" "node_modules\electron\dist\electron.exe" .
