param(
    [string]$Installer = "",
    [string]$Instance = "Pie64_12",
    [int]$AdbPort = 5675,
    [int]$Width = 900,
    [int]$Height = 1600,
    [int]$Dpi = 240,
    [int]$Cpus = 4,
    [int]$RamMb = 4096,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Get-BlueStacksInstall {
    $paths = @(
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($path in $paths) {
        $item = Get-ItemProperty $path -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -eq "BlueStacks" } |
            Select-Object -First 1
        if ($item) { return $item }
    }
    return $null
}

function Set-ConfValue($Path, $Key, $Value) {
    $lines = @()
    if (Test-Path -LiteralPath $Path) {
        $lines = Get-Content -LiteralPath $Path
    }
    $escaped = [regex]::Escape($Key)
    $line = "$Key=`"$Value`""
    $found = $false
    $updated = foreach ($existing in $lines) {
        if ($existing -match "^$escaped=") {
            $found = $true
            $line
        } else {
            $existing
        }
    }
    if (-not $found) {
        $updated += $line
    }
    Set-Content -LiteralPath $Path -Value $updated -Encoding UTF8
}

if (-not $Installer) {
    $Installer = Get-ChildItem -LiteralPath (Join-Path $Root "offline\bluestacks") -Filter "*.exe" -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

$installed = Get-BlueStacksInstall
if ($installed) {
    Write-Host "BlueStacks installed: $($installed.DisplayVersion)"
} else {
    if (-not $Installer -or -not (Test-Path -LiteralPath $Installer)) {
        throw "BlueStacks installer not found. Put BlueStacksInstaller_*.exe under offline\bluestacks or pass -Installer."
    }
    Write-Host "Installing BlueStacks from $Installer"
    $proc = Start-Process -FilePath $Installer -ArgumentList @("--defaultImageName", "Pie64") -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        Write-Warning "BlueStacks installer returned exit code $($proc.ExitCode). Some BlueStacks installers return non-zero after launching UI; continue with config check."
    }
}

$conf = "C:\ProgramData\BlueStacks_nxt\bluestacks.conf"
if (-not (Test-Path -LiteralPath $conf)) {
    throw "BlueStacks config not found at $conf. Open BlueStacks once, create a Pie 64-bit instance, then rerun."
}

Write-Host "Configuring BlueStacks ADB and display settings..."
Set-ConfValue $conf "bst.enable_adb_access" "1"
Set-ConfValue $conf "bst.enable_adb_remote_access" "0"
Set-ConfValue $conf "bst.instance.$Instance.adb_port" "$AdbPort"
Set-ConfValue $conf "bst.instance.$Instance.status.adb_port" "$AdbPort"
Set-ConfValue $conf "bst.instance.$Instance.fb_width" "$Width"
Set-ConfValue $conf "bst.instance.$Instance.fb_height" "$Height"
Set-ConfValue $conf "bst.instance.$Instance.dpi" "$Dpi"
Set-ConfValue $conf "bst.instance.$Instance.cpus" "$Cpus"
Set-ConfValue $conf "bst.instance.$Instance.ram" "$RamMb"

if ($NoLaunch) {
    Write-Host "BlueStacks configured. Launch skipped."
    exit 0
}

$hdPlayer = "C:\Program Files\BlueStacks_nxt\HD-Player.exe"
if (-not (Test-Path -LiteralPath $hdPlayer)) {
    throw "HD-Player.exe not found at $hdPlayer"
}

Write-Host "Launching BlueStacks instance $Instance"
Start-Process -FilePath $hdPlayer -ArgumentList @("--instance", $Instance) -WindowStyle Hidden
Start-Sleep -Seconds 25

$adb = Get-Command adb -ErrorAction SilentlyContinue
if ($adb) {
    adb connect "127.0.0.1:$AdbPort"
    adb devices
} else {
    Write-Warning "adb not found in PATH. Install Android SDK Platform Tools or set ADB_PATH."
}
