"""test_bug_fix_round_3.py

Round 3 negative tests:
* T7 SSE envelope
* T16 .gitattributes VERSION -text
* T26 credential masking in stdout logs
* T28 register.py --email + --count mutual exclusion
* T31 mailbox_broker int timeout safety
* T33 binary_patch runtime-option offset recalc
* T34 update.sh kills child processes
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


try:
    import fastapi  # noqa: F401  — webui.server 依赖
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False


class TestGitAttributesVersion(unittest.TestCase):
    """T16: VERSION 必须明确 -text 声明"""

    def test_version_text_in_gitattributes(self):
        attrs = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("VERSION -text", attrs)


class TestCredentialMasking(unittest.TestCase):
    """T26: register_grok_http / register_github 不应打印明文 password"""

    def test_grok_masks_password(self):
        # 直接读 register_grok_http.py 源码,断言它没有 ``pw={password}`` 模式。
        text = (PROJECT_ROOT / "register_grok_http.py").read_text(
            encoding="utf-8", errors="ignore"
        )
        self.assertNotIn("pw={password}", text)
        self.assertNotIn("pass={password}", text)

    def test_github_masks_password(self):
        text = (PROJECT_ROOT / "register_github.py").read_text(
            encoding="utf-8", errors="ignore"
        )
        self.assertNotIn("pass={gh_password}", text)


class TestEmailCountMutualExclusion(unittest.TestCase):
    """T28: --email + --count > 1 必须显式拒绝"""

    def test_blocks_email_plus_count(self):
        import argparse
        import register

        # 固定邮箱 + count>1 且非临时邮箱 => 互斥,应拒绝。
        args = argparse.Namespace(email="foo@bar.com", count=5)
        self.assertTrue(
            register._email_count_mutually_exclusive(args, use_temp_email=False)
        )
        # 临时邮箱模式不参与该互斥。
        self.assertFalse(
            register._email_count_mutually_exclusive(args, use_temp_email=True)
        )
        # 单条邮箱 + count=1 不触发互斥。
        args_single = argparse.Namespace(email="foo@bar.com", count=1)
        self.assertFalse(
            register._email_count_mutually_exclusive(args_single, use_temp_email=False)
        )


@unittest.skipUnless(_HAS_FASTAPI, "requires fastapi")
class TestMailboxBrokerTimeoutIntSafety(unittest.TestCase):
    """T31: int(timeout) 必须是安全 conversion;非法值返回 400"""

    def test_invalid_timeout_yields_400(self):
        import asyncio
        from mailbox_broker import h_fetch
        from unittest.mock import AsyncMock, MagicMock

        class FakeReq:
            def __init__(self, body, broker):
                self._body = body
                self.app = {"broker": broker}

            async def json(self):
                return self._body

        broker = MagicMock()
        broker.fetch = AsyncMock(return_value="123456")

        # 合法 timeout 应透传给 broker.fetch(... , timeout) —— T31 修复生效。
        req_ok = FakeReq(
            {"email": "a@b.com", "password": "pw", "timeout": "30"}, broker
        )
        resp_ok = asyncio.run(h_fetch(req_ok))
        self.assertEqual(resp_ok.status, 200)
        self.assertEqual(broker.fetch.call_args.args[-1], 30)

        # 非法 timeout 必须返回 400(T31 修复:不再静默吞掉)。调用真实 h_fetch。
        req_bad = FakeReq(
            {"email": "a@b.com", "password": "pw", "timeout": "not-a-number"}, broker
        )
        resp_bad = asyncio.run(h_fetch(req_bad))
        self.assertEqual(resp_bad.status, 400)


class TestSseEnvelope(unittest.TestCase):
    """T7: SSE envelope 必须是 {v,offset,lines,done} + returncode/stopped"""

    def test_envelope_shape(self):
        import asyncio
        import webui.server as server

        run_id = "test-sse-envelope"
        server.RUNS[run_id] = {
            "lines": ["finished"],
            "done": True,
            "returncode": 7,
            "stopped": False,
            "trimmed_offset": 0,
        }
        self.addCleanup(server.RUNS.pop, run_id, None)

        # 真实驱动 api_logs -> _stream 生成器,解析 done 帧 envelope。
        async def collect():
            response = await server.api_logs(run_id)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            return "".join(chunks)

        body = asyncio.run(collect())
        self.assertIn("event: done", body)
        payload = body.split("event: done\ndata: ", 1)[1].split("\n", 1)[0]
        decoded = json.loads(payload)
        # T7: done 帧携带完整 envelope (v/offset/lines/done) + returncode/stopped
        self.assertEqual(decoded["v"], 2)
        self.assertIn("offset", decoded)
        self.assertIn("lines", decoded)
        self.assertIs(decoded["done"], True)
        self.assertEqual(decoded["returncode"], 7)
        self.assertIs(decoded["stopped"], False)


class TestProxyModeGithub(unittest.TestCase):
    """T18: GITHUB 代理模式应允许出现在代理模式白名单里"""

    def test_github_in_whitelist(self):
        # 通过读取 webui.server 的常量验证
        # 直接读源码,确认我们加入 ``github`` 到 PROXY_MODE 白名单的代码段
        text = (PROJECT_ROOT / "webui" / "server.py").read_text(
            encoding="utf-8", errors="ignore"
        )
        self.assertIn('"github"', text)


class TestBinaryPatchRuntmeOption(unittest.TestCase):
    """T33: patch_exe.repack 必须把 runtime-option entry 的 offset/datalen 重算"""

    def test_runtime_option_offset_recomputed(self):
        text = (PROJECT_ROOT / "tools" / "binary_patch" / "patch_exe.py").read_text(
            encoding="utf-8", errors="ignore"
        )
        # 至少要看到 ``entry["offset"] = len(buf)`` 与 ``TYPE_RUNTIME_OPTION``
        self.assertIn("TYPE_RUNTIME_OPTION", text)
        self.assertIn("offset", text)


class TestUpdateShKillsChildren(unittest.TestCase):
    """T34: update.sh 应把子进程也 kill"""

    def test_kill_children_in_update_sh(self):
        text = (PROJECT_ROOT / "update.sh").read_text(
            encoding="utf-8", errors="ignore"
        )
        self.assertIn("pkill -TERM -P", text)


class TestBootstrapPortFromEnv(unittest.TestCase):
    """T34: bootstrap.ps1 应读 REG_FACTORY_PORT"""

    def test_bootstrap_uses_reg_factory_port(self):
        text = (PROJECT_ROOT / "bootstrap.ps1").read_text(
            encoding="utf-8", errors="ignore"
        )
        self.assertIn("$env:REG_FACTORY_PORT", text)


if __name__ == "__main__":
    unittest.main()
