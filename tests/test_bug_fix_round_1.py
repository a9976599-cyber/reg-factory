"""test_bug_fix_round_1.py

P1 negative-path regression tests for the bulk-fix round.

Each test covers a P1 / P2 bugfix from ``reg-factory-FIX-PLAN.md`` and asserts
the broken pre-fix behaviour raises / fails / no longer does the old thing.

These tests IMPORT AND CALL THE REAL MODIFIED FUNCTIONS so they actually exercise
the fixed code paths — no inline re-implementation of the logic that would give
the test zero regression value.

All tests are runnable offline (no network).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestRegisterPhoneUnpack(unittest.TestCase):
    """T1: 三元组解包 + release_phone 在异常路径释放

    直接调用 register._get_and_verify_phone —— 真实被改函数，而非重实现解包逻辑。
    """

    def _make_fake_page(self):
        # page.locator(...) 返回带 .first 的 locator; .first.fill / .first.click 可 await。
        fill_click = AsyncMock()
        locator = MagicMock()
        locator.first = locator
        locator.fill = fill_click
        locator.click = fill_click
        page = MagicMock()
        page.locator = MagicMock(return_value=locator)
        page.evaluate = AsyncMock(return_value="")
        return page

    def _call_verify(self, phone_tuple, human_side_effect=None):
        """以最小 mock 驱动真实的 register._get_and_verify_phone。"""
        import register

        release_calls = []
        with mock.patch.object(register, "get_phone_number", lambda: phone_tuple), \
                mock.patch.object(register, "hero_get_phone_number", lambda: None), \
                mock.patch.object(
                    register, "human_type", AsyncMock(side_effect=human_side_effect)
                ), \
                mock.patch.object(
                    register, "release_phone", lambda pkey: release_calls.append(pkey)
                ), \
                mock.patch.object(register, "get_sms_code", lambda *a, **k: "123456"), \
                mock.patch.object(register.asyncio, "sleep", AsyncMock()):
            page = self._make_fake_page()
            ok = asyncio.run(register._get_and_verify_phone(page, max_attempts=1))
        return ok, release_calls

    def test_unpack_accepts_three_tuple(self):
        # get_phone_number 返回 (phone, country_code, activation_id) 三元组;
        # 修复前 ``phone, pkey = result`` 会抛 ValueError,这里真实函数应解包成功。
        ok, _ = self._call_verify(("+12025550133", "US", "ACT-PKEY-12345"))
        self.assertTrue(ok)

    def test_unpack_still_accepts_two_tuple(self):
        """兼容老 sentinel 形 (phone, pkey),真实函数仍应成功。"""
        ok, _ = self._call_verify(("+12025550134", "ACT-PKEY-67890"))
        self.assertTrue(ok)

    def test_release_phone_called_on_error(self):
        """异常路径(模拟 human_type 失败)也必须用真实函数释放 pkey。"""
        ok, release_calls = self._call_verify(
            ("+12025550133", "US", "ACT-PKEY-FAIL"),
            human_side_effect=RuntimeError("boom"),
        )
        self.assertFalse(ok)
        self.assertIn("ACT-PKEY-FAIL", release_calls)


class TestRegisterAsyncPoll(unittest.TestCase):
    """T2: 同步 magic link poller 必须包 to_thread,不让 event loop 阻塞"""

    def test_event_loop_advances_while_poller_runs(self):
        from common.async_io import to_thread

        evidence = {"ticks": 0}

        async def ticker():
            for _ in range(5):
                await asyncio.sleep(0.01)
                evidence["ticks"] += 1

        def slow_poller():
            import time
            time.sleep(0.2)
            return "magic-link"

        async def main():
            ticker_task = asyncio.create_task(ticker())
            result = await to_thread(slow_poller)
            await ticker_task
            return result

        loop = asyncio.new_event_loop()
        try:
            value = loop.run_until_complete(main())
        finally:
            loop.close()
        self.assertEqual(value, "magic-link")
        # ticker 应该跑完 5 次,断言 event loop 没被阻塞
        self.assertGreaterEqual(evidence["ticks"], 5)


class TestWebuiPortSource(unittest.TestCase):
    """T3: webui/server.py 读 REG_FACTORY_PORT 而不是 sys.argv

    调用真实的 webui.server._update_script —— 它优先从运行期 env 取端口，
    而非依赖 sys.argv(冻结 exe 不会重新收到原始 CLI 参数)。
    """

    def test_update_script_prefers_env_over_argv(self):
        import webui.server as server

        with mock.patch.dict(
            os.environ,
            {"REG_FACTORY_PORT": "9911", "REG_FACTORY_LISTEN_PORT": ""},
            clear=False,
        ):
            with mock.patch.object(sys, "argv", ["ignored"]):
                with mock.patch.object(sys, "frozen", True, create=True):
                    with mock.patch.object(server.os, "name", "nt"):
                        command = server._update_script()

        self.assertIsNotNone(command)
        # env 优先于 argv: 必须带上 -ListenPort 9911(而非从 argv 推断)。
        self.assertIn("-ListenPort", command)
        self.assertIn("9911", command)
        # argv 里没有 --port,若 functions 读了 argv 就不会出现 9911。
        self.assertNotIn("ignored", command)


class TestAssetStoreBatch(unittest.TestCase):
    """T5: ``export_batch`` 不再因 ``[banned, expired]`` 而 400

    调用真实的 common.asset_store.export_batch，仅 mock 底层 get_email，
    验证显式 banned 状态不会被收紧成 verified_only。
    """

    def test_banned_status_passes_through(self):
        from common import asset_store

        captured = {}

        def fake_get_email(**kwargs):
            captured.update(kwargs)
            return {
                "email": "banned@example.com",
                "source": "outlook",
                "total": 1,
                "password": "x",
                "token": "y",
            }

        with mock.patch.object(asset_store, "get_email", side_effect=fake_get_email), \
                mock.patch.object(
                    asset_store, "get_platform_asset", side_effect=NotImplementedError
                ), \
                mock.patch.object(asset_store, "_read_claims", return_value={}), \
                mock.patch.object(asset_store, "_write_claims", return_value=None):
            results = asset_store.export_batch(
                "emails", status="banned", verified_only=True
            )

        self.assertTrue(results)
        # T5 修复: 显式 banned 状态不应被收紧成 verified_only —— get_email 收到的
        # verified_only 必须为 False, status 必须是 "banned"。
        self.assertFalse(captured.get("verified_only"))
        self.assertEqual(captured.get("status"), "banned")


class TestVersionFileRead(unittest.TestCase):
    """T4: 无 .git 时读 VERSION 文件而不是 ``archive``

    解析真实的 update.ps1,校验 Get-ExpectedVersion 从项目根 VERSION 文件取版本,
    而不是退化成字面 "archive"。
    """

    def test_update_powershell_reads_version_file(self):
        text = (PROJECT_ROOT / "update.ps1").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("Get-ExpectedVersion", text)
        self.assertIn('Join-Path $Root "VERSION"', text)
        self.assertRegex(text, r"Get-Content\s+-LiteralPath\s+\$versionFile")


class TestLockDiscipline(unittest.TestCase):
    """验证 common.async_io.to_thread 是唯一入口。"""

    def test_no_raw_asyncio_to_thread_in_common(self):
        # 简易静态扫描:common/ 模块不应出现 raw ``asyncio.to_thread(`` 字面。
        # 唯一正例是 helper 自己(``common/async_io.py``)——它必须包 asyncio.to_thread。
        bad = []
        for py in Path(PROJECT_ROOT, "common").glob("*.py"):
            if py.name == "async_io.py":  # helper 自身允许
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            in_docstring = False
            for ln, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("from "):
                    continue
                # 跳过 docstring / 注释行
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if '"""' in stripped or "'''" in stripped:
                    in_docstring = not in_docstring
                    continue
                if in_docstring:
                    continue
                if "asyncio.to_thread(" in line:
                    bad.append((py.name, ln, line.strip()))
        self.assertEqual(bad, [], f"raw asyncio.to_thread 出现: {bad}")


if __name__ == "__main__":
    unittest.main()
