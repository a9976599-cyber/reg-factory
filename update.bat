@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*
if errorlevel 1 (
  echo.
  echo [ERROR] Update failed. Review the message above.
  if not "%REG_FACTORY_NONINTERACTIVE%"=="1" pause
  exit /b 1
)
