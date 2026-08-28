@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Not installed yet. Double-click install.bat first.
  pause
  exit /b 1
)

if "%REG_FACTORY_PORT%"=="" set REG_FACTORY_PORT=8799

echo Starting reg-factory control panel ...
echo Panel: http://127.0.0.1:%REG_FACTORY_PORT%  (browser opens automatically)
echo Codex K12: managed by the panel at http://127.0.0.1:8806
echo Close this window to stop the server.
echo.

start "" /b cmd /c "timeout /t 2 >nul & start http://127.0.0.1:%REG_FACTORY_PORT%"

.venv\Scripts\python.exe -m uvicorn webui.server:app --host 127.0.0.1 --port %REG_FACTORY_PORT%
pause
