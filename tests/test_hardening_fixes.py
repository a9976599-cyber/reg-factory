# -*- coding: utf-8 -*-
"""第四轮修复之后的补强（H1-H5）回归锁。

覆盖：
- H1 common/sms.py 两个 helper（_hero_get_code/_smsman_get_code）首异常打印根因
- H2 task_dispatch 防越目录校验改为 realpath + normcase（防 symlink 逃逸/大小写误判）
- H4 tools/import_plus_codex.py 源文件删除延迟到整批导入成功之后
- H5 common/session_export.py _write_json_atomic dump 失败清理 .tmp 残片
（H3 update-portable.ps1 的 Merge 覆盖断言在 test_update_entrypoints.py）
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import task_dispatch
from common import session_export, sms
from tools.import_plus_codex import (
    delete_input_file,
    load_accounts,
    should_delete_input,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HeroSmsGetCodeLoggingTests(unittest.TestCase):
    """H1：_hero_get_code 轮询异常不再静默——首异常打印根因，后续不刷屏。"""

    def test_first_exception_logged_once(self):
        with mock.patch.object(sms.requests, "get", side_effect=RuntimeError("boom-hero")):
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                result = sms._hero_get_code("hero_123", max_wait=0.4, interval=0.05)
        self.assertIsNone(result, "全部请求失败时按超时返回 None")
        out = captured.getvalue()
        self.assertIn("boom-hero", out, "首次异常应打印根因")
        self.assertEqual(out.count("首次异常"), 1, "后续轮询不应重复刷屏")

    def test_success_still_works(self):
        """正常取码路径不受影响：STATUS_OK 立即返回。"""
        fake = mock.Mock()
        fake.text = "STATUS_OK:123456"
        with mock.patch.object(sms.requests, "get", return_value=fake):
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                result = sms._hero_get_code("hero_123", max_wait=1, interval=0.05)
        self.assertEqual(result, "123456")


class SmsmanGetCodeLoggingTests(unittest.TestCase):
    """H1：_smsman_get_code 轮询异常不再静默——首异常打印根因，后续不刷屏。"""

    def test_first_exception_logged_once(self):
        with mock.patch.object(sms.requests, "get", side_effect=RuntimeError("boom-smsman")):
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                result = sms._smsman_get_code("smsman_42", max_wait=0.4, interval=0.05)
        self.assertIsNone(result, "全部请求失败时按超时返回 None")
        out = captured.getvalue()
        self.assertIn("boom-smsman", out, "首次异常应打印根因")
        self.assertEqual(out.count("首次异常"), 1, "后续轮询不应重复刷屏")

    def test_error_code_abort_still_works(self):
        """业务错误（非 wait_sms）直接放弃的既有行为不变。"""
        fake = mock.Mock()
        fake.json.return_value = {"error_code": "bad_request"}
        with mock.patch.object(sms.requests, "get", return_value=fake):
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                result = sms._smsman_get_code("smsman_42", max_wait=1, interval=0.05)
        self.assertIsNone(result)
        self.assertIn("终止", captured.getvalue())


class ResolveTaskPathTests(unittest.TestCase):
    """H2：防越目录校验 realpath + normcase。"""

    def test_rejects_dotdot_escape(self):
        root = task_dispatch.bundle_root()
        with self.assertRaises(SystemExit):
            task_dispatch.resolve_task_path(root, "../escape.py")
        with self.assertRaises(SystemExit):
            task_dispatch.resolve_task_path(root, "sub/../../escape.py")

    def test_rejects_absolute_target_escape(self):
        root = task_dispatch.bundle_root()
        with tempfile.TemporaryDirectory() as d:
            outside = os.path.join(d, "evil.py")
            with open(outside, "w", encoding="utf-8") as f:
                f.write("")
            # Windows 下 abspath(join(root, "C:\\...")) 会直接落到绝对路径
            with self.assertRaises(SystemExit):
                task_dispatch.resolve_task_path(root, outside)

    def test_accepts_case_variant_of_root_child(self):
        """大小写变体不应被误判为越目录（Windows normcase 归一）。"""
        root = task_dispatch.bundle_root()
        probe = os.path.join(root, "H2CaseProbe_X.py")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        try:
            resolved = task_dispatch.resolve_task_path(root, "h2caseprobe_x.py")
            self.assertTrue(
                os.path.normcase(resolved).startswith(
                    os.path.normcase(os.path.realpath(root)) + os.sep
                ),
                "大小写变体应通过校验且落在根目录内",
            )
        finally:
            os.remove(probe)

    def test_rejects_symlink_escape(self):
        """指向根目录外的 symlink 必须被拒（realpath 解析）。"""
        link_dir = tempfile.mkdtemp(prefix="h2_link_")
        target_dir = tempfile.mkdtemp(prefix="h2_target_")
        link_root = os.path.join(link_dir, "bundle")
        os.mkdir(link_root)
        outside = os.path.join(target_dir, "evil.py")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("")
        link_name = "linked.py"
        try:
            os.symlink(outside, os.path.join(link_root, link_name))
        except (OSError, NotImplementedError):
            for path in (link_root, link_dir, target_dir):
                os.rmdir(path) if os.path.isdir(path) and not os.listdir(path) else None
            self.skipTest("当前环境不支持 symlink")
        try:
            with self.assertRaises(SystemExit):
                task_dispatch.resolve_task_path(link_root, link_name)
        finally:
            os.remove(os.path.join(link_root, link_name))
            os.rmdir(link_root)
            os.rmdir(link_dir)
            os.remove(outside)
            os.rmdir(target_dir)


class ImportDeleteInputTests(unittest.TestCase):
    """H4：--delete-input 只在整批导入成功后删除源文件。"""

    def test_load_accounts_keeps_source_even_on_success(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ok.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("a@b.com----pw123\n")
            records = load_accounts(p, delete_input=True)
            self.assertTrue(records)
            self.assertTrue(os.path.isfile(p), "load_accounts 只读不删")

    def test_should_delete_input_requires_full_success(self):
        full = SimpleNamespace(delete_input=True, dry_run=False)
        self.assertTrue(should_delete_input(full, 3, 3))
        self.assertFalse(should_delete_input(full, 2, 3), "部分失败不得删除")
        self.assertFalse(should_delete_input(full, 0, 0), "空批次不得删除")
        no_flag = SimpleNamespace(delete_input=False, dry_run=False)
        self.assertFalse(should_delete_input(no_flag, 3, 3))
        dry = SimpleNamespace(delete_input=True, dry_run=True)
        self.assertFalse(should_delete_input(dry, 3, 3), "dry-run 绝不删除")

    def test_delete_input_file_removes_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "gone.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("a@b.com----pw123\n")
            delete_input_file(p)
            self.assertFalse(os.path.isfile(p))
            # 源文件已不存在时不应抛错（missing_ok）
            delete_input_file(p)

    def test_delete_only_after_batch_success_in_run(self):
        """run 尾部的删除决策必须由 should_delete_input 收口（源码级回归锁）。"""
        import tools.import_plus_codex as ipc

        import pathlib

        source = pathlib.Path(ipc.__file__).read_text(encoding="utf-8")
        self.assertIn("if should_delete_input(args, success, len(results)):", source)
        self.assertNotIn(
            "source_path.unlink",
            source,
            "load_accounts 内不得再删除源文件",
        )


class WriteJsonAtomicCleanupTests(unittest.TestCase):
    """H5：_write_json_atomic dump 失败清理 .tmp 残片、原文件不受损。"""

    def test_tmp_cleaned_and_original_intact_on_dump_failure(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "state.json")
            with open(target, "w", encoding="utf-8") as f:
                f.write('{"old": true}')
            boom = RuntimeError("dump-boom")
            with mock.patch.object(session_export.json, "dump", side_effect=boom):
                with self.assertRaises(RuntimeError):
                    session_export._write_json_atomic(target, {"new": 1})
            leftovers = [name for name in os.listdir(d) if name != "state.json"]
            self.assertEqual(leftovers, [], "dump 失败后不得残留 .tmp 文件")
            with open(target, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"old": True}, "原文件应保持原样")

    def test_success_path_replaces_file(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "state.json")
            session_export._write_json_atomic(target, {"a": 1})
            with open(target, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"a": 1})
            leftovers = [name for name in os.listdir(d) if name != "state.json"]
            self.assertEqual(leftovers, [], "成功路径同样不残留 .tmp")


if __name__ == "__main__":
    unittest.main()
