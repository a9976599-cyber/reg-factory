$ErrorActionPreference = "Stop"
$RepoRoot = "C:\Users\99765\reg-factory"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DistRoot = Join-Path $RepoRoot "dist"
$version = "2.0.8"
$PackageName = "reg-factory-windows-x64-$version"
$PyInstallerOutput = Join-Path $DistRoot "reg-factory"
$PackageRoot = Join-Path $DistRoot $PackageName
$ZipPath = Join-Path $DistRoot "$PackageName.zip"

Set-Location $RepoRoot

if (Test-Path $PyInstallerOutput) { Remove-Item $PyInstallerOutput -Recurse -Force }
if (Test-Path $PackageRoot) { Remove-Item $PackageRoot -Recurse -Force }
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

& $Python -m PyInstaller --noconfirm --clean --distpath $DistRoot --workpath (Join-Path $RepoRoot "build") packaging/reg-factory.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Move-Item $PyInstallerOutput $PackageRoot
Copy-Item (Join-Path $RepoRoot "README.md") $PackageRoot
Copy-Item (Join-Path $RepoRoot "CHANGELOG.md") $PackageRoot
Copy-Item (Join-Path $RepoRoot ".env.example") $PackageRoot
Copy-Item (Join-Path $RepoRoot "VERSION") $PackageRoot
if (Test-Path (Join-Path $RepoRoot "docs")) { Copy-Item (Join-Path $RepoRoot "docs") $PackageRoot -Recurse }

Compress-Archive -Path $PackageRoot -DestinationPath $ZipPath -CompressionLevel Optimal
$hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Path "$ZipPath.sha256.txt" -Value "$hash  $PackageName.zip" -Encoding ascii
Write-Output "BUILD_OK size=$((Get-Item $ZipPath).Length)"
