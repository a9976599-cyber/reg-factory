param(
    [switch]$SkipPythonDeps,
    [switch]$SkipAppium
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Require-Command($Name, $InstallHint) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "$Name not found. $InstallHint"
    }
    return $cmd
}

Write-Host "== Gmail Android automation installer =="

Require-Command "python" "Install Python 3.11+ and add it to PATH."
Require-Command "node" "Install Node.js 20+ and add it to PATH."
Require-Command "npm" "Install Node.js 20+ and add npm to PATH."

if (-not $SkipPythonDeps) {
    Write-Host "Installing Python dependencies..."
    python -m pip install -r requirements.txt
}

if (-not $SkipAppium) {
    $appium = Get-Command appium -ErrorAction SilentlyContinue
    if (-not $appium) {
        Write-Host "Installing Appium globally..."
        npm install -g appium
    }

    $drivers = appium driver list --installed
    if ($drivers -notmatch "uiautomator2") {
        Write-Host "Installing Appium UiAutomator2 driver..."
        appium driver install uiautomator2
    }
}

$adb = Get-Command adb -ErrorAction SilentlyContinue
if (-not $adb) {
    Write-Warning "adb not found in PATH. Install Android SDK Platform Tools, then add platform-tools to PATH or set ADB_PATH in .env."
} else {
    Write-Host "adb: $($adb.Source)"
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

Write-Host "Done. Start Appium with scripts\\start_appium.ps1, then run scripts\\check_env.ps1."
