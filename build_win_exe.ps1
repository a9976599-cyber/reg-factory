# 兼容入口。真正的发布构建在 scripts/build_release.ps1 —— 它从 VERSION 读版本、
# 校验与请求版本一致、跑 Python 测试与 JS 语法检查、产 zip + .sha256.txt，
# 并在打包前扫描敏感/运行期文件。
#
# 历史版本这里硬编码了别人的机器路径（C:\Users\99765\reg-factory）和写死的版本号
# （2.0.8）：换台机器直接失败，产物名也永远和 VERSION 对不上。根目录只保留这个
# 转发壳，避免两套构建脚本各自漂移。

param(
    [switch]$SkipTests,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = (Get-Content -LiteralPath (Join-Path $RepoRoot "VERSION") -Raw).Trim()

$forward = @{ Version = $Version }
if ($SkipTests) { $forward["SkipTests"] = $true }
if ($SkipInstall) { $forward["SkipInstall"] = $true }

& (Join-Path $RepoRoot "scripts\build_release.ps1") @forward
exit $LASTEXITCODE
