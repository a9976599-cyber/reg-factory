# -*- coding: utf-8 -*-
"""第四轮全项目审计（2.2.8 之后）修复的回归锁。

覆盖：
- import_plus_codex --delete-input 解析失败不再删源文件（P1 数据丢失）
- task_dispatch 拒绝 --task=../../x.py 越目录执行（P3 本地代码执行）
- update-portable.ps1 回滚用 -Merge 复制，保留健康探测窗口内写入（P3 边缘丢数据）
- webui _write_env_file 净化换行，防 .env 注入（P1 配置注入）
- webui 代理面板/secret 回显掩码（P2 凭证泄露）
"""

import os
import tempfile
import unittest
from pathlib import Path

from tools.import_plus_codex import load_accounts
import task_dispatch

try:
    from webui import server as _server
except Exception:  # pragma: no cover - 测试环境缺 Flask 时跳过
    _server = None

ROOT = Path(__file__).resolve().parents[1]


class FourthRoundFixTests(unittest.TestCase):
    def test_load_accounts_keeps_source_on_parse_error(self):
        """--delete-input 解析失败时不应删除用户账号清单（P1 数据丢失）。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("notanemail\n")  # parse_account_text 会报格式错误
            with self.assertRaises(RuntimeError):
                load_accounts(p, delete_input=True)
            self.assertTrue(
                os.path.isfile(p),
                "--delete-input 解析失败不应删除源文件",
            )

    def test_load_accounts_deletes_only_after_success(self):
        """整批解析成功且 delete_input 为真时才删源文件。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ok.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("a@b.com----pw123\n")
            load_accounts(p, delete_input=True)
            self.assertFalse(os.path.isfile(p), "解析成功应删除源文件")

    def test_run_task_rejects_path_escape(self):
        """--task=../../x.py 不得执行 bundle 根目录外的脚本。"""
        root = task_dispatch.bundle_root()
        outside = os.path.abspath(os.path.join(root, "..", "escape_test_x.py"))
        with open(outside, "w", encoding="utf-8") as f:
            f.write("")
        try:
            with self.assertRaises(SystemExit):
                task_dispatch.run_task(("../escape_test_x.py", []))
        finally:
            os.remove(outside)

    def test_update_portable_rollback_uses_merge(self):
        script = (ROOT / "update-portable.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "param([string]$SourceDir, [string]$TargetDir, [switch]$Merge)",
            script,
            "Restore-UserState 应支持 -Merge 复制语义",
        )
        self.assertIn(
            "Restore-UserState -SourceDir $InstallDir -TargetDir $backupDir -Merge",
            script,
            "回滚路径必须用 -Merge，保留健康探测窗口内的用户写入",
        )


@unittest.skipIf(_server is None, "webui.server 不可用")
class ServerHardeningTests(unittest.TestCase):
    def test_safe_env_value_strips_newlines(self):
        self.assertEqual(_server._safe_env_value("a\nEVIL=1"), "a EVIL=1")
        self.assertEqual(_server._safe_env_value("b\r\nc"), "b  c")

    def test_write_env_file_strips_newline_injection(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            _server._write_env_file(p, {"FOO": "a\nEVIL=1", "BAR": "ok"})
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("\nEVIL=1", text, "换行被当新配置行注入")
            self.assertIn("FOO=a EVIL=1", text)

    def test_redact_proxy_config_masks_credentials(self):
        out = _server._redact_proxy_config({
            "CLASH_SECRET": "topsecret",
            "REG_FACTORY_PROXY": "http://user:pass@proxy.example:8080",
            "REG_FACTORY_PROXY_POOL": "http://u1:p1@h1\nhttp://u2:p2@h2",
            "FOO": "bar",
            "PROXY_MODE": "clash_auto",
        })
        self.assertEqual(out["CLASH_SECRET"], "********")
        self.assertEqual(out["REG_FACTORY_PROXY"], "********")
        self.assertEqual(out["REG_FACTORY_PROXY_POOL"], "********")
        self.assertEqual(out["FOO"], "bar")
        self.assertEqual(out["PROXY_MODE"], "clash_auto")


if __name__ == "__main__":
    unittest.main()
