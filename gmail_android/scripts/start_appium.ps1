param(
    [string]$Address = "127.0.0.1",
    [int]$Port = 4723,
    [string]$LogFile = "logs\appium_server.log"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null

$appium = Get-Command appium -ErrorAction SilentlyContinue
if (-not $appium) {
    throw "appium not found. Run scripts\install_windows.ps1 first."
}

$statusUrl = "http://${Address}:${Port}/status"
try {
    $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 3
    Write-Host "Appium is already running: $($status.value.build.version)"
    exit 0
} catch {
}

Write-Host "Starting Appium on $statusUrl"
Start-Process `
    -FilePath $appium.Source `
    -ArgumentList @("--address", $Address, "--port", "$Port", "--log", $LogFile, "--log-level", "info", "--log-no-colors") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden

Start-Sleep -Seconds 5
try {
    Invoke-RestMethod -Uri $statusUrl -TimeoutSec 10 | ConvertTo-Json -Depth 10
} catch {
    throw "Appium did not become ready. Check $LogFile"
}
