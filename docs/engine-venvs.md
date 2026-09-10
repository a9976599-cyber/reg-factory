# 内置引擎的 venv（A / O 两个后端）

发布包里的 `engine/` 带两个独立后端，**各自的源码在包内，但 venv 需要本机自建**：

| 引擎 | 目录 | 端口 | 健康检查 | 启动命令 | 桌面端期望的解释器 |
|---|---|---|---|---|---|
| A（any-auto-register） | `engine/aar` | 8000 | `/api/auth/check` | `python main.py` | `engine/aar/.venv_aar/Scripts/python.exe` |
| O（outlook-auto-register） | `engine/oar` | 8890 | `/api/config` | `python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8890` | `engine/oar/.venv_oar/Scripts/python.exe` |

venv **必须叫这两个名字、放在这两个位置**——桌面端 `webui/aar_bridge.py` 是按
`<引擎目录>/.venv_aar|.venv_oar/Scripts/python.exe` 硬编码查找的。找不到就会在
启动日志里打印：

```text
AAR python not found, skip autostart
OAR python not found, skip autostart
```

此时「A 完整控制台」「O 邮箱注册台」会显示为不可用（`/api/deps-status` 返回
`present=false`），但主 WebUI 本身不受影响。

## 建 venv

用 **Python 3.12**（引擎依赖在这些包上对 3.12 兼容性最好）：

```bash
cd engine/aar
python -m venv .venv_aar
.venv_aar/Scripts/python.exe -m pip install -r requirements.txt

cd ../oar
python -m venv .venv_oar
.venv_oar/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env          # 按需填写打码 Key、恢复邮箱等
```

`engine/oar` 的 `requirements.txt` 只含协议注册所需的 4 个包
（`requests` / `python-dotenv` / `fastapi` / `uvicorn`）；浏览器兜底路径
（`px_solver/` 下的 playwright 方案）另需：

```bash
.venv_oar/Scripts/python.exe -m pip install playwright patchright
```

`engine/aar` 的 `requirements.txt` 已含 `playwright` / `patchright` / `camoufox`
等，装完约 400 MB。

## 浏览器二进制（可选，但注册流程需要）

venv 装完只是 Python 侧就绪，浏览器二进制还要单独拉：

```bash
.venv_aar/Scripts/python.exe -m playwright install chromium
.venv_oar/Scripts/python.exe -m playwright install chromium
.venv_aar/Scripts/python.exe -m camoufox fetch
```

这些会装到 `%LOCALAPPDATA%` 下（不占发布包目录）。

## 验证

```bash
# 各自直连
curl http://127.0.0.1:8000/api/auth/check
curl http://127.0.0.1:8890/api/config

# 桌面端视角（present=本机能提供，alive=此刻在线）
curl http://127.0.0.1:8799/api/deps-status
```

`deps-status` 的 `present` 为 true，说明 venv 已被正确识别；首次访问
`/aar` 或 `/oar` 时桌面端会自动把对应引擎拉起来，随后 `alive` 变为 true。
