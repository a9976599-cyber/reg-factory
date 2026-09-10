$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== Versions =="
python --version
node --version
npm --version

Write-Host "`n== Python imports =="
$env:PYTHONDONTWRITEBYTECODE = "1"
python -c "import appium, selenium, requests; print('python deps ok')"

Write-Host "`n== Appium =="
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:4723/status" -TimeoutSec 5 | ConvertTo-Json -Depth 8
} catch {
    Write-Warning "Appium is not reachable at http://127.0.0.1:4723. Run scripts\start_appium.ps1."
}

Write-Host "`n== ADB devices =="
$adb = Get-Command adb -ErrorAction SilentlyContinue
if ($adb) {
    adb devices
} else {
    Write-Warning "adb not found in PATH. Set ADB_PATH in .env or install Android SDK Platform Tools."
}

Write-Host "`n== Script import =="
python -c "import gmail_register_local; print('gmail_register_local import ok')"
