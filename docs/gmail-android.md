# Gmail Android 本地环境

`gmail_android/` 使用 BlueStacks、ADB 和 Appium 驱动 Gmail Android 注册流程。

## 安全边界

- 默认在手机、短信、CAPTCHA 或额外安全验证处停止，由操作者处理。
- `--resume-after-phone` 只用于人工验证完成后的续跑。
- `--accept-terms` 只在操作者明确同意 Google 条款后使用。
- 接码 provider 默认不接管 Gmail 的安全验证。

## 环境要求

- BlueStacks，并开启 ADB
- Android SDK Platform Tools
- Node.js 20+
- Appium 2.x
- Appium UiAutomator2 driver
- 模拟器内安装 Gmail App

默认设备地址为 `127.0.0.1:5675`，Appium 默认监听 `http://127.0.0.1:4723`。

## 安装与检查

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd gmail_android
.\scripts\install_all_windows.ps1
.\scripts\start_appium.ps1
.\scripts\check_env.ps1
```

复制配置：

```powershell
Copy-Item .env.example .env
```

常用配置：

```env
APPIUM_SERVER=http://127.0.0.1:4723
ANDROID_DEVICE=127.0.0.1:5675
GMAIL_USERNAME_PREFIX=
ACCEPT_TERMS=0
SMS_PROJECT_ID_GMAIL=
HERO_SMS_SERVICE_GMAIL=
```

## 运行

```powershell
# 默认运行到人工验证处
python .\gmail_register_local.py

# 人工完成手机验证后续跑
python .\gmail_register_local.py --resume-after-phone

# 明确同意条款后继续
python .\gmail_register_local.py --resume-after-phone --accept-terms
```

## 构建发布包

```powershell
cd gmail_android
.\scripts\build_release.ps1

# 可选：附带固定版本 BlueStacks 安装器
.\scripts\build_release.ps1 -BlueStacksInstaller C:\path\to\BlueStacksInstaller.exe
```

输出位于 `gmail_android/dist/`。BlueStacks web installer 仍需要联网；完全离线发布必须使用官方 full/offline installer。
