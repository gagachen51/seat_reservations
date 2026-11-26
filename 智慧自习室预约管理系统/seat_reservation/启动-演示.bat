@echo off
rem Demo mode launcher: seeds demo data on first run.
rem The demo password is random and shown ONCE in the startup dialog.
cd /d "%~dp0"
set SEAT_DEMO_MODE=true

python -c "import fastapi, webview" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies, please wait...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed. Run: python -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

python desktop_launcher.py --demo
if errorlevel 1 pause
