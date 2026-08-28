<div align="center">

# 🏭 reg-factory · 全能注册工作台

### Outlook · Gmail · ChatGPT · Grok · Claude · Gemini · GitHub · Google One

**邮箱注册、平台授权、凭据导出与下游导入的一体化本地控制台**

</div>

---

## 一、安装

### 方式 A：Windows 便携包（推荐）

1. 从 Releases 下载 `reg-factory-windows-x64-<版本>.zip`
2. 完整解压到任意目录（不要在压缩包预览里直接运行）
3. 双击 `reg-factory.exe`，浏览器会自动打开控制台
4. 默认地址 `http://127.0.0.1:8799/`，无需安装 Python

> 配置和运行数据保存在 `%LOCALAPPDATA%\RegFactory`，升级时直接替换程序目录即可。

### 方式 B：从源码运行

**前置条件：**
- Python 3.10+
- [BitBrowser](https://www.bitbrowser.cn/download)（默认指纹浏览器），或 AdsPower / 内置 Chromium
- [Clash Verge](https://github.com/clash-verge-rev/clash-verge-rev/releases)（Clash 出口模式），或一个住宅代理服务

**Windows：**
```text
1. 双击 install.bat          ← 创建虚拟环境、安装依赖
2. 启动 BitBrowser（和 Clash Verge）
3. 双击 start.bat
4. 打开 http://127.0.0.1:8799/
```

**macOS / Linux：**
```bash
./install.sh
./start.sh
```

---

## 二、首次配置

首次打开控制台会弹出新手指南，按顺序完成以下配置即可开始注册：

### 1. 网络出口（左侧「网络出口」）

控制所有注册任务使用的代理出口。

| 模式 | 说明 | 适用场景 |
|---|---|---|
| **Clash 自动轮换** | 跟随 Clash Verge 当前节点 | 日常注册 |
| **Clash 固定节点** | 锁定某个节点不切换 | 需要固定 IP |
| **动态住宅 IP** | 使用住宅代理池 | 高成功率需求 |

- 填入 Clash 控制器地址（默认 `http://127.0.0.1:9097`）、混合代理地址（默认 `http://127.0.0.1:7897`）
- 可为每个平台单独设置出口覆盖（Outlook / Claude / ChatGPT / Grok / Kiro / GitHub）
- 点击「保存并应用」→「应用并测试 IP」验证出口是否生效

### 2. 指纹浏览器（左侧「环境配置」→ 指纹浏览器分组）

- 默认使用 BitBrowser，本地 API 通常为 `http://127.0.0.1:54345`
- 也支持 AdsPower、内置 Chromium、自定义 Chrome
- 如使用自定义指纹浏览器，填写 API 根地址和可选 Key

### 3. 打码服务（左侧「A 服务配置」→ 验证服务）

O 邮箱注册台的打码方式可选：

| 方式 | 说明 |
|---|---|
| **自动** | 按后端已存的 Key 自动选择 |
| **本地打码** | SwiftShader 本地求解，免 Key |
| **captcha.run** | 需填 captcha.run Bearer Key |
| **EzCaptcha** | 需在服务配置页填 Key |
| **CapSolver** | 需在服务配置页填 Key |

> 选「本地打码」时无需任何 Key，开箱即用。

### 4. 邮箱服务（左侧「A 服务配置」→ 邮箱服务）

- 配置注册用邮箱来源（YYDS 临时邮箱 / iCloud 接码 / 自有 Outlook 等）
- Outlook Graph 注册需配置可接收验证码的辅助邮箱

---

## 三、功能使用

### O 邮箱注册台（左侧「O 邮箱注册台」）

批量注册 Outlook 邮箱账号。

**操作步骤：**
1. **代理来源**：选「跟随网络出口（推荐）」→ 自动使用网络出口页 Outlook 配置的出口；也可选 O 代理池或手动代理
2. **打码方式**：选本地打码（免 Key）或在线打码服务
3. **captcha.run Key**：选本地打码可留空
4. **注册数量 / 并发度**：建议从并发 1 开始
5. **邮箱后缀 / 国家代码**：按需选择
6. **产出格式**：Graph 四段式 / 六段式 / 双令牌
7. 点击「开始批次注册」→ 日志区实时显示进度
8. 需要中止时点旁边的「停止」按钮

**代理池页（O 邮箱注册台 → 代理池 tab）：**
- 添加代理后点「预检」可检测每个代理的出口 IP
- 预检结果展示：**IP 地址 · 国家 · 机房/住宅 · 纯净度 · ISP**
- 纯净度绿色为佳（≥70），橙色次之，红色风险高

### A 自动注册（左侧「A 自动注册」）

注册 Claude / ChatGPT / Grok / Kiro / GitHub 等平台账号。

1. 选择平台 / 注册身份 / 执行方式
2. 执行方式可选：协议模式 / 后台浏览器自动 / 可视浏览器自动
3. 「A 服务配置」→「通用策略」可设置默认注册身份、第三方入口和执行方式

### 任务库（左侧「任务库」）

- 按流程分类选择任务卡片，点击直接进入运行页
- 只展示常用参数，低频参数收进「更多设置」

### 账号资产（左侧「账号资产」）

- 查看本机全部账号凭据（含密码）
- 支持导出全部、刷新

### 资产 API（左侧「资产 API」）

通过本地 HTTP 接口领取邮箱和平台凭据，供下游程序调用。

```bash
# 按顺序取下一个邮箱
curl "http://127.0.0.1:8799/api/assets/emails?format=json"

# 只领取最近扫描为正常的邮箱
curl "http://127.0.0.1:8799/api/assets/emails?normal_only=true"

# 领取第 3 个 ChatGPT 账号，输出 SUB2API 格式
curl "http://127.0.0.1:8799/api/assets/cookies/chatgpt?format=sub2api&index=2"

# 配置 API Key 后增加鉴权
curl "http://127.0.0.1:8799/api/assets/cookies/chatgpt?format=cpa" -H "X-API-Key: your-key"
```

- 支持格式：`json` / `cookies` / `sub2api` / `cpa` / `chatgpt2api`
- 领取后自动记账，同一账号跨格式不重复返回
- 在资产 API 页可配置访问密钥、生成调用命令

### Plus 导入（左侧「Plus 导入」）

使用已开通 Plus 的 ChatGPT 账号 → 手机号接码验证 → Codex OAuth → 导入 SUB2API。

- 支持批量粘贴 Outlook/Hotmail/Live/MSN、iCloud、ChatGPT session Cookie/token 和完整 Codex OAuth JSON
- 兼容 RT/client_id 正反顺序及多种分隔符

### 邮箱池导入（左侧「邮箱池导入」）

批量导入外部邮箱直接入池，每行一个，支持 Outlook 各地域域名、Hotmail/Live/MSN、iCloud 和自定义邮箱。

---

## 四、常用命令行

除 Web 控制台外，也可用命令行执行：

```bash
# Outlook → Claude / ChatGPT / Grok / Kiro / GitHub 端到端
python run_full_flow.py --platforms claude chatgpt grok kiro github

# 同时处理 3 个邮箱，每个邮箱内所选平台默认并行
python run_full_flow.py --rounds 12 --concurrency 3 --platforms claude chatgpt kiro

# 使用已有邮箱池并行注册多个平台
python register_three_platforms.py --from-pool --parallel

# 常驻注册 Outlook
python outlook_reg_loop.py

# Claude 使用最新 Outlook refresh token
python register.py --count 1 --node auto --latest-rt

# ChatGPT 使用 iCloud 接码邮箱
python register_chatgpt.py --count 1 --email-provider icloud

# Grok 浏览器注册并导入 SUB2API
python register_grok.py --count 1 --sub2api

# Kiro Builder ID 注册并导出长期凭据
python register_kiro.py --count 1

# 已开通 Plus 账号：接码验证 → Codex OAuth → SUB2API
python tools/import_plus_codex.py --accounts-file accounts.txt --sms-provider auto --phone-attempts 3
```

---

## 五、运行数据

以下文件由程序生成，包含敏感信息，**不要提交到仓库或发给他人**：

| 路径 | 内容 |
|---|---|
| `.env` | 本机密钥与服务地址 |
| `emails.txt` | 邮箱池 |
| `cookies/` | 平台 Cookie |
| `tokens/` | Token 与上传状态 |
| `_outlook_pool/` | Outlook 待用账号 |
| `outlook_accounts/` | Outlook 账号与 Graph Token |
| `runtime/logs/` | 任务日志 |
| `runtime/secrets/` | 本地临时凭据 |

---

## 六、升级与更新

- **便携包**：使用顶栏「一键更新」，或从 Releases 下载新版覆盖程序目录
- **源码**：运行 `update.bat`（Windows）或 `./update.sh`（macOS/Linux）
- 更新脚本会先检查运行中的任务，再更新依赖、重启并验证版本
- 数据目录不会随程序目录替换而丢失

---

## 七、支持

- QQ 群：``
- Telegram：

---

> 仅用于学习、开发和经授权的测试。密钥、账号、Cookie、Token 和运行日志均应保留在本机。
