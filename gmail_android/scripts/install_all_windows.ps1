param(
    [switch]$SkipBlueStacks,
    [switch]$SkipPythonDeps,
    [switch]$SkipAppium,
    [switch]$NoLaunchBlueStacks
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== Full Windows install =="

if (-not $SkipBlueStacks) {
    & .\scripts\install_bluestacks.ps1 -NoLaunch:$NoLaunchBlueStacks
}

& .\scripts\install_windows.ps1 -SkipPythonDeps:$SkipPythonDeps -SkipAppium:$SkipAppium

Write-Host "Run .\scripts\start_appium.ps1 and .\scripts\check_env.ps1 next."
