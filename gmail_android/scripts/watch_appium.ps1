# Per-instance Appium watchdog: keep ONE Appium alive on a given port, bound to a dedicated
# adb server port (ANDROID_ADB_SERVER_PORT) so instances don't share one adb server.
# Appium's UiAutomator2 driver can crash the whole process on offline-device cleanup; this
# loop brings it right back.
#
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\watch_appium.ps1 -AppiumPort 4724 -AdbServerPort 5038

param(
    [string]$Address = "127.0.0.1",
    [int]$AppiumPort = 4723,
    [int]$AdbServerPort = 5037,
    [int]$CheckSeconds = 5
)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$LogFile = "logs\appium-$AppiumPort.log"
$statusUrl = "http://${Address}:${AppiumPort}/status"

# All adb commands launched from THIS process (and the Appium it spawns) use this adb server.
$env:ANDROID_ADB_SERVER_PORT = "$AdbServerPort"

$appiumCmd = Join-Path $env:APPDATA "npm\appium.cmd"
if (-not (Test-Path $appiumCmd)) {
    $g = Get-Command appium -ErrorAction SilentlyContinue
    if ($g) { $appiumCmd = $g.Source } else { throw "appium not found" }
}

function Test-AppiumUp {
    try { Invoke-RestMethod -Uri $statusUrl -TimeoutSec 4 | Out-Null; return $true } catch { return $false }
}

function Start-Appium {
    Write-Host "[$(Get-Date -Format HH:mm:ss)] Appium:$AppiumPort (adb $AdbServerPort) DOWN -> starting..." -ForegroundColor Yellow
    # ANDROID_ADB_SERVER_PORT is in $env, inherited by the spawned appium.cmd -> its adb uses it.
    Start-Process -FilePath $appiumCmd `
        -ArgumentList @("--address", $Address, "--port", "$AppiumPort", "--log", $LogFile, "--log-level", "info", "--log-no-colors") `
        -WorkingDirectory $Root -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 2
        if (Test-AppiumUp) { Write-Host "[$(Get-Date -Format HH:mm:ss)] Appium:$AppiumPort UP after ~$($i*2+2)s" -ForegroundColor Green; return $true }
    }
    Write-Host "[$(Get-Date -Format HH:mm:ss)] Appium:$AppiumPort failed to come up in 40s" -ForegroundColor Red
    return $false
}

Write-Host "Watchdog: Appium $statusUrl bound to adb server $AdbServerPort (check ${CheckSeconds}s). Ctrl+C to stop." -ForegroundColor Cyan
$restarts = 0
while ($true) {
    if (-not (Test-AppiumUp)) {
        $restarts++
        Write-Host "restart #$restarts" -ForegroundColor DarkYellow
        Start-Appium | Out-Null
    }
    Start-Sleep -Seconds $CheckSeconds
}
