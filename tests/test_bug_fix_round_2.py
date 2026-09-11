"""test_bug_fix_round_2.py

P2 negative-path regression tests:
* T11 one_attempt finally pre-init
* T12 MANUAL_VERIFY_RETAIN_WINDOW ContextVar 隔离
* T13 asset_scanner write_lock + atomic
* T14 emails 锁外网络校验
* T19 browser_registry atomic write + .bak
* T20 mark_uploaded file_lock
* T21 bundled_browser readline_with_deadline / RLock
* T23 run_full_flow readline_with_deadline
* T25 desktop port+health
* T27 upgrade_claude_max 退出码
* T29 outlook_reg_loop exit 0 on no work
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


try:
    import fastapi  # noqa: F401  — webui.server 依赖
    import requests  # noqa: F401 — tools/upgrade_claude_max 依赖
    _HAS_OPTIONAL_DEPS = True
except ImportError:  # pragma: no cover - 环境缺少可选依赖
    _HAS_OPTIONAL_DEPS = False

try:
    import playwright  # noqa: F401
    _HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    _HAS_PLAYWRIGHT = False

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestManualRetainContextVar(unittest.TestCase):
    """T12: MANUAL_VERIFY_RETAIN_WINDOW 应不跨 worker 泄漏"""

    def test_per_attempt_isolation(self):
        from common.run_context import (
            get_manual_retain,
            reset_manual_retain,
            set_manual_retain,
        )

        self.assertFalse(get_manual_retain())
        token_a = set_manual_retain(True)
        self.assertTrue(get_manual_retain())
        reset_manual_retain(token_a)
        self.assertFalse(get_manual_retain())


class TestAtomicIoRetries(unittest.TestCase):
    """T15/T19 write_json_atomic 在 PermissionError 时换 tmp 名重试"""

    def test_permission_error_triggers_retry(self):
        from common.atomic_io import write_json_atomic

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "registry.json"
            # 让目标被锁住,atomic_io 必须做退避,不应直接抛错。
            with open(target, "w", encoding="utf-8") as fh:
                lock_target = Path(target)
            # 关闭后又立即重写,atomic_io 用单独 tmp 文件 + os.replace,所以
            # 即便目标被读也不影响写入。这里我们让 fake target 在某些 attempt
            # 上读锁 —— 我们使用 retry_backoff=0 加速。
            attempts = {"n": 0}
            original_replace = os.replace

            def flaky_replace(src, dst):
                attempts["n"] += 1
                if attempts["n"] < 3 and str(dst) == str(target):
                    raise PermissionError(13, "simulated")
                return original_replace(src, dst)

            with mock.patch("os.replace", flaky_replace):
                write_json_atomic(target, {"x": 1}, retries=8, retry_backoff=0)
            self.assertTrue(target.is_file())
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["x"], 1)


class TestSafeJoinUnder(unittest.TestCase):
    """T34: common.path_guard.safe_join_under 拒绝逃逸"""

    def test_sibling_prefix_attack_blocked(self):
        from common.path_guard import safe_join_under

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "reg_factory"
            evil = Path(tmp) / "reg_factory_evil" / "leak.txt"
            base.mkdir()
            (Path(tmp) / "reg_factory_evil").mkdir()
            with self.assertRaises(ValueError):
                safe_join_under(str(base), str(evil))

    def test_in_root_passes(self):
        from common.path_guard import safe_join_under

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inner = base / "sub" / "ok.txt"
            inner.parent.mkdir(parents=True)
            inner.write_text("ok", encoding="utf-8")
            result = safe_join_under(str(base), str(inner))
            self.assertTrue(result.endswith("ok.txt"))


@unittest.skipUnless(_HAS_PLAYWRIGHT, "requires playwright (tools/upgrade_claude_max)")
class TestUpgradeExitCodes(unittest.TestCase):
    """T27: upgrade_claude_max 在 UNCLEAR/FAILED 时返回非零"""

    def test_false_yields_exit_one(self):
        # 调用真实的 upgrade_claude_max.main():无 session-key / profile-id 时
        # upgrade_to_max 直接返回 False,main 据此走 T27 分支 sys.exit(1)。
        import asyncio

        from tools import upgrade_claude_max

        captured = {}

        def fake_exit(code, *args, **kwargs):
            captured["code"] = code
            raise SystemExit(code)

        with mock.patch.object(upgrade_claude_max.sys, "exit", fake_exit), \
                mock.patch.object(
                    upgrade_claude_max.sys, "argv", ["upgrade_claude_max.py"]
                ), \
                mock.patch.object(
                    upgrade_claude_max.os, "makedirs", lambda *a, **k: None
                ):
            with contextlib.suppress(SystemExit):
                asyncio.run(upgrade_claude_max.main())
        self.assertEqual(captured.get("code"), 1)

    def test_source_calls_sys_exit_codes(self):
        """源码检查: upgrade_claude_max.main 末尾必须真的调用 sys.exit(0/1/2)"""
        src = Path(PROJECT_ROOT, "tools", "upgrade_claude_max.py").read_text(
            encoding="utf-8"
        )
        self.assertRegex(src, r"sys\.exit\(\s*0\s*\)")
        self.assertRegex(src, r"sys\.exit\(\s*1\s*\)")
        self.assertRegex(src, r"sys\.exit\(\s*2\s*\)")


@unittest.skipUnless(_HAS_OPTIONAL_DEPS, "requires fastapi")
class TestWebuiSafeJson(unittest.TestCase):
    """T32: api_run 应拒绝非法 JSON body 并返回 400"""

    def test_helper_returns_400_on_bad_json(self):
        from fastapi.testclient import TestClient
        from webui.server import app, _safe_json

        client = TestClient(app)
        # 直接验证 helper 行为
        class FakeReq:
            def json(self):
                raise ValueError("invalid JSON body")

        req = FakeReq()
        data, err = _safe_json(req)
        self.assertIsNone(data)
        self.assertIsNotNone(err)
        self.assertEqual(err.status_code, 400)


class TestOutlookExitZeroOnNoWork(unittest.TestCase):
    """T29: outlook_reg_loop 「无事可做」 应返回 0

    直接调用真实的 ``outlook_reg_loop._decide_registration_exit_code``(从
    ``_run_registration_workers`` 抽出、承载 T29 修复逻辑),而非重实现判定分支。
    """

    def _args(self, target_pool=0, count=0):
        import argparse

        return argparse.Namespace(
            target_pool=target_pool,
            count=count,
            success_rate_window=10,
            min_success_rate=50,
        )

    def test_idle_target_pool_full_returns_zero(self):
        from outlook_reg_loop import _decide_registration_exit_code
        import outlook_reg_loop

        args = self._args(target_pool=3, count=0)
        state = {"next": 0, "success": 2, "failed": 0, "stop_reason": ""}
        with mock.patch.object(outlook_reg_loop, "count_pool", lambda: 3):
            self.assertEqual(_decide_registration_exit_code(args, state), 0)

    def test_idle_count_done_returns_zero(self):
        from outlook_reg_loop import _decide_registration_exit_code

        args = self._args(target_pool=0, count=5)
        state = {"next": 5, "success": 3, "failed": 0, "stop_reason": ""}
        self.assertEqual(_decide_registration_exit_code(args, state), 0)

    def test_failure_returns_one(self):
        from outlook_reg_loop import _decide_registration_exit_code

        args = self._args(count=5)
        state = {"next": 1, "success": 0, "failed": 2, "stop_reason": ""}
        self.assertEqual(_decide_registration_exit_code(args, state), 1)

    def test_success_rate_breaker_returns_one(self):
        from outlook_reg_loop import _decide_registration_exit_code
        import outlook_reg_loop

        args = self._args(target_pool=3, count=0)
        state = {"next": 0, "success": 0, "failed": 0, "stop_reason": "rate breaker"}
        with mock.patch.object(outlook_reg_loop, "count_pool", lambda: 0):
            self.assertEqual(_decide_registration_exit_code(args, state), 1)


class TestDesktopPortDetection(unittest.TestCase):
    """T25: find_free_port 全忙时抛错而不是返回 0"""

    def test_full_busy_raises(self):
        from reg_factory_desktop import find_free_port, port_in_use

        with mock.patch.object(sys.modules["reg_factory_desktop"], "port_in_use", return_value=True):
            with self.assertRaises(RuntimeError):
                find_free_port(9000)


@unittest.skipUnless(_HAS_OPTIONAL_DEPS, "requires fastapi")
class TestMultiArgInjection(unittest.TestCase):
    """T18: webui._build_cmd 拒收 multi arg 列表里以 '-' 开头的元素"""

    def test_dash_prefix_blocked(self):
        from webui.server import _build_cmd

        script = {
            "file": "register.py",
            "args": [
                {
                    "flag": "--emails",
                    "type": "multi",
                    "positional": False,
                }
            ],
        }
        with self.assertRaises(ValueError):
            _build_cmd(script, {"--emails": ["foo.txt", "--no-rotate"]})


if __name__ == "__main__":
    unittest.main()
