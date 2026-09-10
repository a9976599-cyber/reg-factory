# -*- coding: utf-8 -*-
"""配置热更新约定锁定测试（2.2.7 回归的防复发锁）。

背景：``from config import X`` 拿到的是**值快照**，WebUI 保存配置后当前进程
若不重绑就看不到新值。2.2.7 曾把 ``common.temp_email`` 移出刷新链导致界面
改临时邮箱配置当场不生效（详见 reg-factory-2.2.7-bug-report.md P2-4/P2-5）。

本文件锁死的约定：

1. 凡「模块级 ``from config import 大写常量``」且常量属于 UI 可编辑键
   （或 star 导入）的模块，若被 WebUI 进程内使用，必须实现
   ``refresh_from_env()``；
2. 实现了钩子的模块必须登记进 ``common/env_refresh._REFRESH_ORDER``；
3. 键元组必须与模块级导入名 1:1（防止加配置忘了进元组）。

``--task`` 子进程脚本豁免：每次由 task_dispatch 起独立进程，启动时读最新
环境变量，不存在热更新需求。
"""

import ast
import unittest
from pathlib import Path

from common import env_refresh
from webui import scripts as schema

ROOT = Path(__file__).resolve().parents[1]

# 豁免清单：全部是 --task 子进程脚本（webui/scripts.py 登记的任务或任务侧模块）。
# 每新增一个「模块级 config 快照 + 无钩子」的文件都必须在这里给出理由。
SUBPROCESS_ALLOWLIST = {
    "oauth_codex.py",                 # webui/scripts.py 任务 "oauth_codex"
    "unlock_outlook.py",              # 任务 "unlock_outlook"
    "register.py",                    # 任务 "register"
    "register_chatgpt.py",            # 任务 "register_chatgpt"
    "register_github.py",             # 任务 "register_github"
    "register_grok.py",               # 任务 "register_grok"
    "register_grok_http.py",          # 任务 "register_grok_http"
    "gmail_android/sms_provider.py",  # gmail 安卓流程任务侧模块
}

BLOCKING = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
SKIP_DIRS = {".git", "__pycache__", ".venv", "tests", "tools", "scripts", "docs", "node_modules"}


def _module_scope_config_names(source):
    """返回 (模块级 from config import 的大写名字集合, 是否 star 导入)。

    只统计模块作用域的导入：函数/类/lambda 体内的导入是调用时现取，不算快照；
    模块级 try/except 里的导入仍是模块作用域（temp_email / adspower 的写法）。
    """
    names = set()
    star = False

    def walk(node, in_func):
        nonlocal names, star
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom) and child.module == "config" and not in_func:
                for alias in child.names:
                    if alias.name == "*":
                        star = True
                    elif alias.name.isupper():
                        names.add(alias.name)
            elif isinstance(child, BLOCKING):
                walk(child, True)
            else:
                walk(child, in_func)

    walk(ast.parse(source), False)
    return names, star


def _scan_snapshot_modules():
    """扫描仓库源码，返回 {相对路径: {"hits": [...], "star": bool, "has_hook": bool}}。"""
    ui_keys = set(schema.env_keys())
    results = {}
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.split("/", 1)[0] in SKIP_DIRS:
            continue
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            names, star = _module_scope_config_names(source)
        except SyntaxError:  # pragma: no cover - 源码树不该有语法错误（CI compileall 会先拦）
            continue
        hits = sorted(names & ui_keys)
        if not hits and not star:
            continue
        results[rel] = {
            "hits": hits,
            "star": star,
            "has_hook": "def refresh_from_env" in source,
        }
    return results


def _module_name(rel):
    stem = rel[:-3]
    return stem.replace("/", ".")


class SnapshotConventionTests(unittest.TestCase):
    def test_inprocess_snapshot_modules_implement_refresh_hook(self):
        """持有 UI 键快照的进程内模块必须实现 refresh_from_env()。"""
        offenders = []
        for rel, info in _scan_snapshot_modules().items():
            if rel in SUBPROCESS_ALLOWLIST:
                continue
            if not info["has_hook"]:
                offenders.append("%s (UI 键: %s%s)" % (
                    rel, ", ".join(info["hits"][:4]), ", *star*" if info["star"] else ""))
        self.assertEqual(
            offenders,
            [],
            "以下进程内模块持有 UI 键快照但没有 refresh_from_env()，"
            "WebUI 保存配置后本进程不会生效（照 common/sms.py 的写法补钩子并登记进 "
            "common/env_refresh._REFRESH_ORDER；若是 --task 子进程脚本，加进豁免清单并注明理由）: %s"
            % offenders,
        )

    def test_every_hook_module_is_registered_in_refresh_order(self):
        """实现了钩子的模块必须登记进 _REFRESH_ORDER，否则钩子永远不会被调用。"""
        offenders = []
        for rel, info in _scan_snapshot_modules().items():
            if rel in SUBPROCESS_ALLOWLIST or not info["has_hook"]:
                continue
            if _module_name(rel) not in env_refresh._REFRESH_ORDER:
                offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            "以下模块实现了 refresh_from_env() 但不在 common/env_refresh._REFRESH_ORDER 里: %s"
            % offenders,
        )

    def test_key_tuples_match_module_scope_imports(self):
        """键元组必须与模块级导入名 1:1，防止加配置忘了进元组。"""
        for rel, attr in (
            ("common/temp_email.py", "_TEMP_EMAIL_CONFIG_KEYS"),
            ("adspower.py", "_ADSPOWER_CONFIG_KEYS"),
            ("bitbrowser.py", "_BITBROWSER_CONFIG_KEYS"),
        ):
            source = (ROOT / rel).read_text(encoding="utf-8-sig")
            names, _ = _module_scope_config_names(source)
            module = __import__(_module_name(rel), fromlist=["x"])
            declared = set(getattr(module, attr))
            self.assertEqual(
                declared, names,
                "%s 的 %s 与模块级 from config import 名单不一致: 缺 %s / 多 %s"
                % (rel, attr, sorted(names - declared), sorted(declared - names)),
            )

    def test_refresh_hooks_rebind_module_constants(self):
        """钩子真的把 config 最新值重绑进模块全局（不是空壳）。"""
        import adspower
        import bitbrowser
        import common.temp_email as temp_email
        import config

        cases = (
            (temp_email, "CFMAIL_BASE_URL", "http://refreshed.example"),
            (adspower, "ADSPOWER_API", "http://refreshed-adspower.example:50325"),
            (bitbrowser, "BITBROWSER_API", "http://refreshed-bitbrowser.example:54345"),
        )
        for module, key, new_value in cases:
            old = getattr(config, key)
            setattr(config, key, new_value)
            try:
                module.refresh_from_env()
                self.assertEqual(
                    getattr(module, key), new_value,
                    "%s.refresh_from_env() 没有重绑 %s" % (module.__name__, key),
                )
            finally:
                setattr(config, key, old)
                module.refresh_from_env()


if __name__ == "__main__":
    unittest.main()
