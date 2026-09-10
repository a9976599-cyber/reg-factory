# -*- coding: utf-8 -*-
"""便携包「仓库源码 -> 包内文件」同步表（打包器与发布物断言共用）。

背景（2026-09-11 复检结论）：
    便携包的 `reg-factory.exe` 是官方 2.2.4 冻结包 + 入口级二进制补丁。
    冻结进程的 import 走向经实测确认（在冻结进程内打印 `__file__` 与 `isfile()`）：
      * `config` / `common.*` / `webui.*` -> `PyiFrozenLoader`，来自 exe 内嵌归档，
        落盘路径是**合成**的（`isfile=False`），丢文件进 `_internal` 不会生效；
      * `_internal/*.py`、`_internal/tools/*.py` 是**真实松散文件**，`runpy` 直接执行；
      * `_internal/webui/static/*`、`update-portable.ps1`、`.env.example` 由冻结代码
        在运行时用 `open()` 从磁盘读取。
    因此「文件覆盖」只能修好第二、三类。本表就是**唯一可被文件覆盖生效**的清单。

2.2.7 的教训：打包脚本只覆盖了 `VERSION` / `CHANGELOG`，于是发布包与官方 2.2.4
逐字节相同 —— 仓库里修好的 `btn-guide`、更新器仓库名一个都没到用户手里。
本表让打包器与断言共用同一份事实，缺项即拒绝出包。
"""

# 包内松散任务脚本（runpy 直接执行）
TASK_SCRIPTS = [
    "mailbox_broker.py",
    "oauth_codex.py",
    "outlook_reg_loop.py",
    "register.py",
    "register_chatgpt.py",
    "register_github.py",
    "register_grok.py",
    "register_grok_http.py",
    "register_kiro.py",
    "register_outlook_standalone.py",
    "register_three_platforms.py",
    "run_full_flow.py",
    "unlock_outlook.py",
]

# 包内松散工具脚本
TOOLS = [
    "export_accounts.py",
    "export_chatgpt2api.py",
    "export_kiro_credentials.py",
    "extract_graph_tokens.py",
    "import_plus_codex.py",
    "run_protocol_payment_batch.py",
    "upload_tokens.py",
]

# 运行时从磁盘读取的 WebUI 静态资源（icon.png 仓库没有，保持官方原样）
WEBUI_STATIC = ["index.html", "app.js", "style.css", "oar_page.js"]

# 文档
DOCS = [
    "api.md",
    "architecture.md",
    "cli.md",
    "configuration.md",
    "getting-started.md",
    "gmail-android.md",
    "troubleshooting.md",
]

# 2.2.7 新增的松散子模块：
#   common.async_batch  —— 被仓库版任务脚本 `from common.async_batch import gather_settled`
#                          引用（frozen 的 common 包 __path__ 指向 _internal/common，
#                          可被 PathFinder 命中，实测可导入）
#   common.env_refresh  —— 冻结版 webui.server 不会调用它，一并带上以便将来归档重建
NEW_LOOSE_MODULES = [
    "common/async_batch.py",
    "common/env_refresh.py",
]


def sync_map():
    """返回 {包内相对路径: 仓库相对路径}（相对包根 / 仓库根）。"""
    m = {
        "VERSION": "VERSION",
        "_internal/VERSION": "VERSION",
        "CHANGELOG.md": "CHANGELOG.md",
        "_internal/CHANGELOG.md": "CHANGELOG.md",
        "README.md": "README.md",
        ".env.example": ".env.example",
        "_internal/.env.example": ".env.example",
        "_internal/update-portable.ps1": "update-portable.ps1",
    }
    for rel in TASK_SCRIPTS:
        m["_internal/" + rel] = rel
    for rel in TOOLS:
        m["_internal/tools/" + rel] = "tools/" + rel
    for rel in NEW_LOOSE_MODULES:
        m["_internal/" + rel] = rel
    for rel in WEBUI_STATIC:
        m["_internal/webui/static/" + rel] = "webui/static/" + rel
    for rel in DOCS:
        m["docs/" + rel] = "docs/" + rel
    return m


# 必须在产物里断言的“用户可见修复”，与 sync_map 互补（防回归）
CONTENT_GUARDS = [
    ("_internal/webui/static/index.html", b"btn-guide"),
    ("_internal/webui/static/app.js", b"btn-guide"),
    ("_internal/update-portable.ps1", b"a9976599-cyber"),
    ("_internal/common/async_batch.py", b"def gather_settled"),
    (".env.example", b"OUTLOOK_MANUAL_VERIFY"),
    ("_internal/.env.example", b"CHATGPT2API_URL"),
]

# 不得出现在发布物【脚本】里的上游标识。
# 只扫脚本类扩展名：CHANGELOG / 文档里的历史沿革与出处说明是正常且应当保留的，
# 真正危险的是「可执行的东西还在指向上游」。
FORBIDDEN_SUBSTR = [b"tiantianGPU"]
SCRIPT_EXT = {".py", ".ps1", ".sh", ".bat", ".cmd"}

# 定向负向护栏：(包内路径, 不得出现的字符串)
NEGATIVE_GUARDS = [
    ("_internal/update-portable.ps1", b"tiantianGPU"),
]
