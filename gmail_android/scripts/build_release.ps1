param(
    [string]$OutputDir = "dist",
    [string]$BlueStacksInstaller = ""
)

$ErrorActionPreference = "Stop"
$ModuleRoot = Split-Path -Parent $PSScriptRoot
$RepoRoot = Split-Path -Parent $ModuleRoot
$OutRoot = Join-Path $ModuleRoot $OutputDir
$PackageName = "gmail-android-local"
$PackageRoot = Join-Path $OutRoot $PackageName
$ZipPath = Join-Path $OutRoot "$PackageName.zip"

Set-Location $RepoRoot

if (Test-Path -LiteralPath $PackageRoot) {
    Remove-Item -LiteralPath $PackageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "scripts") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "offline\bluestacks") | Out-Null

$files = @(
    "gmail_register_local.py",
    "appium_api.py",
    "bluestacks.py",
    "config.py",
    "coordinator.py",
    "proxy_switch.py",
    "sms_provider.py",
    "requirements.txt",
    ".env.example",
    "README.md"
)

foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $ModuleRoot $file) -Destination (Join-Path $PackageRoot $file) -Force
}

Get-ChildItem -LiteralPath (Join-Path $ModuleRoot "scripts") -File -Filter "*.ps1" |
    Where-Object { $_.Name -ne "build_release.ps1" } |
    Copy-Item -Destination (Join-Path $PackageRoot "scripts") -Force

if ($BlueStacksInstaller) {
    if (-not (Test-Path -LiteralPath $BlueStacksInstaller)) {
        throw "BlueStacks installer not found: $BlueStacksInstaller"
    }
    Copy-Item -LiteralPath $BlueStacksInstaller -Destination (Join-Path $PackageRoot "offline\bluestacks") -Force
}

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $PackageRoot -DestinationPath $ZipPath -Force
Get-Item -LiteralPath $ZipPath | Select-Object FullName,Length,LastWriteTime
