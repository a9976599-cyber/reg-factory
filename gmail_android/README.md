# Gmail Android Local Automation

This package drives the Gmail Android signup flow through Appium and a local Android emulator.

## Safety Boundary

By default the script stops at phone/SMS/CAPTCHA or additional Google security verification, and those steps are completed by an operator.

Optionally, `--auto-phone` (or `PHONE_VERIFICATION_MODE=auto`) completes Google's phone step through a configured SMS provider. For the post-registration second login, sms-man numbers that support multiple messages are preferred so the same number can receive another code. firefox.fun and hero-sms remain fallbacks. Use this only for accounts and numbers you are authorized to manage and in line with the relevant terms of service. CAPTCHA and unknown Google security prompts are left to the operator.

## Requirements

- Windows 10/11
- Python 3.11+
- Node.js 20+
- Android SDK Platform Tools (`adb`)
- BlueStacks or another Android emulator with ADB enabled
- Appium 2.x with `uiautomator2` driver
- Gmail installed in the emulator

Known local working versions:

- Python 3.12.4
- Node v20.20.2
- npm 10.8.2
- Appium 2.19.0
- UiAutomator2 driver 4.2.9

## Install

Run PowerShell from the project root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_all_windows.ps1
```

If your Android SDK is not in PATH, set `ADB_PATH` in `.env` or add `platform-tools` to PATH.

### BlueStacks Installer Bundle

The distributable may include a fixed BlueStacks installer under:

```text
offline\bluestacks\BlueStacksInstaller_*.exe
```

`scripts\install_all_windows.ps1` calls `scripts\install_bluestacks.ps1`, which installs or detects BlueStacks, enables ADB, configures a Pie 64-bit instance, sets the target display to `900x1600 @ 240 dpi`, and connects ADB on `127.0.0.1:5675`.

If the bundled installer is a small web/micro installer, the target machine still needs network access to download BlueStacks components. For fully offline installation, replace it with the approved full/offline BlueStacks installer before building the zip.

If the installer is not bundled, put the approved BlueStacks installer in `offline\bluestacks\` before running the script.

Direct BlueStacks-only install/config:

```powershell
.\scripts\install_bluestacks.ps1
```

## Build And Upload Release

Build the distributable zip from the repository root:

```powershell
cd gmail_android
.\scripts\build_release.ps1

# Bundle a fixed BlueStacks installer
.\scripts\build_release.ps1 -BlueStacksInstaller C:\path\to\BlueStacksInstaller.exe
```

The output file is:

```text
gmail_android\dist\gmail-android-local.zip
```

Upload it from the GitHub web UI:

1. Open the repository on GitHub.
2. Go to `Releases` -> `Draft a new release`.
3. Create a tag such as `gmail-android-v2026.06.06`.
4. Attach `gmail_android\dist\gmail-android-local.zip` and publish the release.

Or upload with GitHub CLI:

```powershell
cd E:\reg-factory
gh auth login
gh release create gmail-android-v2026.06.06 `
  .\gmail_android\dist\gmail-android-local.zip `
  --title "Gmail Android Local Package" `
  --notes "Gmail Android/Appium local installer package."

# If the release already exists
gh release upload gmail-android-v2026.06.06 `
  .\gmail_android\dist\gmail-android-local.zip `
  --clobber
```

## Emulator Setup

1. Run `.\scripts\install_all_windows.ps1`, or install BlueStacks manually.
2. Enable Android Debug Bridge in BlueStacks settings if the script did not do it.
3. Confirm the device:

```powershell
adb devices
```

Set `ANDROID_DEVICE` in `.env` if the device id is not `127.0.0.1:5675`.

## Start Appium

```powershell
.\scripts\start_appium.ps1
.\scripts\check_env.ps1
```

## Run

Default run now performs the local lifecycle first: switch to a healthy Clash node when configured, start/connect the BlueStacks instance, clear Google/Gmail/Play state, run the registration flow, then close the instance when the script exits.

After registration, the script removes the newly created Android account, signs in again through Gmail and Google Play Services, and verifies that Android AccountManager contains the account. The second-login account is then left on the BlueStacks instance, so its Gmail login state survives an emulator stop and restart.

```powershell
python .\gmail_register_local.py
```

Skip the second-login check for diagnosis:

```powershell
python .\gmail_register_local.py --no-second-login
```

Explicitly opt in to phone-based Google 2-Step Verification after second login:

```powershell
python .\gmail_register_local.py --auto-phone --enable-2fa
```

2FA is never enabled by default. If Android presents an unrecognized security page, passkey flow, or CAPTCHA, the script leaves the emulator open for manual completion.

Use a non-default emulator port:

```powershell
python .\gmail_register_local.py --device 127.0.0.1:5735
```

Keep the emulator open after the run:

```powershell
python .\gmail_register_local.py --keep-emulator
```

Skip lifecycle work for debugging:

```powershell
python .\gmail_register_local.py --no-prepare-emulator --no-switch-node --keep-emulator
```

Wait while you manually complete phone verification, then continue:

```powershell
python .\gmail_register_local.py --wait-phone-verification
```

Resume after manual phone verification:

```powershell
python .\gmail_register_local.py --resume-after-phone
```

Resume after manually completing an Android second-login or 2FA security prompt. The current Google Play Services screen is reused:

```powershell
python .\gmail_register_local.py --resume-security
```

Complete phone verification automatically via the firefox.fun SMS provider
(hero-sms fallback). Requires `SMS_TOKEN` + `SMS_PROJECT_ID_GMAIL` in `.env`:

```powershell
python .\gmail_register_local.py --auto-phone
```

To let the script click the final Privacy and Terms and Google services confirmation after explicit operator consent:

```powershell
python .\gmail_register_local.py --resume-after-phone --accept-terms
```

## Environment

Copy `.env.example` to `.env`.

Important values:

- `APPIUM_SERVER`: default `http://127.0.0.1:4723`
- `ANDROID_DEVICE`: default `127.0.0.1:5675`
- `GMAIL_USERNAME_PREFIX`: optional username prefix
- `ACCEPT_TERMS`: `1` only after explicit consent
- `PHONE_VERIFICATION_MODE`: `manual` (default) or `auto` (see `--auto-phone`)
- `SECOND_LOGIN_AFTER_SIGNUP`: `1` by default; set `0` to skip the Android second-login check
- `ENABLE_2FA_AFTER_LOGIN`: `0` by default; set `1` only to explicitly enable phone-based 2FA

SMS provider values drive `--auto-phone` / `PHONE_VERIFICATION_MODE=auto`:

- `SMSMAN_TOKEN`, `SMSMAN_APP_ID_GMAIL` - sms-man, preferred for multi-message leases
- `SMSMAN_COUNTRY_GMAIL` - comma-separated country IDs (e.g., `"155,100"` for France/UK); `0` = any
- `SMSMAN_MAXPRICE_GMAIL`, `SMSMAN_BLACKLIST_GMAIL` - sms-man filters
- `SMS_TOKEN`, `SMS_PROJECT_ID_GMAIL` - firefox.fun fallback
- `SMS_COUNTRY_GMAIL` - comma-separated country codes (e.g., `"33,44"` for France/UK); empty = any
- `SMS_MAXPRICE_GMAIL`, `SMS_COUNTRY_BLACKLIST_GMAIL` - firefox.fun filters
- `HERO_SMS_API_KEY`, `HERO_SMS_SERVICE_GMAIL` - hero-sms fallback

Provider priority: **firefox.fun** (if stock available and passes whitelist/blacklist) → **sms-man** (by configured country list) → **hero-sms** (fallback).

Leave them empty to keep phone verification manual.

**SMS country filtering**: Set `SMS_COUNTRY_GMAIL` (firefox.fun country codes) and/or `SMSMAN_COUNTRY_GMAIL` (sms-man country IDs) to prefer specific countries. Example:

```powershell
$env:SMS_COUNTRY_GMAIL = "33,44"        # firefox.fun: France (33), UK (44)
$env:SMSMAN_COUNTRY_GMAIL = "155,100"   # sms-man: France (155), UK (100)
python .\gmail_register_local.py --auto-phone
```

Note: Country filtering only works when the SMS provider has stock and the number passes Google's verification (some virtual number ranges may be rejected or not receive SMS codes).

Pending account credentials and the retained SMS lease are stored in `.runstate/pending-account.json` so a manual security handoff can resume. Completed credentials are appended to `.runstate/completed-accounts.jsonl`. The Android login itself remains in the BlueStacks instance. The run-state directory is excluded from git and release packages; protect it as sensitive local data.

## Package Contents

- `gmail_register_local.py`: main Appium flow
- `appium_api.py`: local Appium helper API
- `config.py`: `.env` loader
- `.env.example`: configuration template
- `requirements.txt`: Python dependencies
- `scripts/`: install, check, start, run helpers
- `offline/bluestacks/`: optional fixed BlueStacks installer

Runtime logs, screenshots, XML dumps, `.env`, and `__pycache__` are excluded from the release zip.
