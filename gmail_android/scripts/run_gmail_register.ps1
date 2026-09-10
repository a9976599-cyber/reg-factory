param(
    [switch]$WaitPhoneVerification,
    [switch]$ResumeAfterPhone,
    [switch]$ResumeSecurity,
    [switch]$NoSecondLogin,
    [switch]$Enable2FA,
    [switch]$AutoPhone,
    [switch]$AcceptTerms,
    [string]$Prefix = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$argsList = @()
if ($WaitPhoneVerification) { $argsList += "--wait-phone-verification" }
if ($ResumeAfterPhone) { $argsList += "--resume-after-phone" }
if ($ResumeSecurity) { $argsList += "--resume-security" }
if ($NoSecondLogin) { $argsList += "--no-second-login" }
if ($Enable2FA) { $argsList += "--enable-2fa" }
if ($AutoPhone) { $argsList += "--auto-phone" }
if ($AcceptTerms) { $argsList += "--accept-terms" }
if ($Prefix) {
    $argsList += "--prefix"
    $argsList += $Prefix
}

python .\gmail_register_local.py @argsList
