$ErrorActionPreference = "Stop"

$Action = $env:REG_FACTORY_ACTION
if ([string]::IsNullOrWhiteSpace($Action)) { $Action = "install" }
$Action = $Action.ToLowerInvariant()
if ($Action -notin @("install", "start", "update")) {
    throw "REG_FACTORY_ACTION must be install, start, or update"
}

$InstallDir = $env:REG_FACTORY_DIR
if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    try {
        $running = Invoke-RestMethod -Uri "http://127.0.0.1:8799/api/status" -TimeoutSec 3
        if ($running.root -and (Test-Path $running.root)) {
            $InstallDir = $running.root
        }
    } catch {}
    if ([string]::IsNullOrWhiteSpace($InstallDir)) {
        $InstallDir = Join-Path $HOME "reg-factory"
    }
}
$Repo = "https://github.com/tiantianGPU/reg-factory.git"
$Archive = "https://github.com/tiantianGPU/reg-factory/archive/refs/heads/main.zip"

function Install-Repository {
    if (Test-Path (Join-Path $InstallDir ".git")) {
        & git -C $InstallDir pull --ff-only
        if ($LASTEXITCODE -ne 0) { throw "git pull failed" }
        return
    }
    if (Test-Path $InstallDir) {
        $entries = @(Get-ChildItem -LiteralPath $InstallDir -Force)
        if ($entries.Count -gt 0) {
            throw "Install directory exists and is not a git checkout: $InstallDir"
        }
    }
    if (Get-Command git -ErrorAction SilentlyContinue) {
        & git clone $Repo $InstallDir
        if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
        return
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("reg-factory-" + [guid]::NewGuid())
    $zipPath = Join-Path $tempRoot "main.zip"
    $extractPath = Join-Path $tempRoot "extract"
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null
    try {
        Invoke-WebRequest -Uri $Archive -OutFile $zipPath
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force
        $source = Join-Path $extractPath "reg-factory-main"
        if (-not (Test-Path $source)) { throw "GitHub archive layout is invalid" }
        if (Test-Path $InstallDir) {
            Copy-Item -Path (Join-Path $source "*") -Destination $InstallDir -Recurse -Force
        } else {
            Move-Item -LiteralPath $source -Destination $InstallDir
        }
    } finally {
        if (Test-Path $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force
        }
    }
}

if ($Action -eq "install") {
    Install-Repository
    $env:REG_FACTORY_NONINTERACTIVE = "1"
    $installer = Join-Path $InstallDir "install.bat"
    & cmd.exe /d /c ('call "' + $installer + '"')
    if ($LASTEXITCODE -ne 0) { throw "reg-factory install failed" }
    Write-Host "Installed at $InstallDir" -ForegroundColor Green
    Write-Host "Run the one-click start command when BitBrowser/AdsPower and Clash are ready."
    return
}

if ($Action -eq "update") {
    if (-not (Test-Path $InstallDir)) {
        throw "reg-factory is not installed at $InstallDir. Run the install command first."
    }
    $tempUpdater = Join-Path ([System.IO.Path]::GetTempPath()) ("reg-factory-update-" + [guid]::NewGuid() + ".ps1")
    try {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/update.ps1" -OutFile $tempUpdater
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $tempUpdater -Root $InstallDir
        if ($LASTEXITCODE -ne 0) { throw "reg-factory update failed" }
    } finally {
        Remove-Item -LiteralPath $tempUpdater -Force -ErrorAction SilentlyContinue
    }
    return
}

$starter = Join-Path $InstallDir "start.bat"
if (-not (Test-Path $starter)) {
    throw "reg-factory is not installed at $InstallDir. Run the install command first."
}
Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", ('call "' + $starter + '"')) -WorkingDirectory $InstallDir
Write-Host "Starting reg-factory from $InstallDir" -ForegroundColor Green
