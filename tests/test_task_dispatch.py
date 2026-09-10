import asyncio
import unittest
from pathlib import Path

import task_dispatch
from common.async_batch import gather_settled


ROOT = Path(__file__).resolve().parents[1]

# <exe> -u --task X.py / 裸 .py / 各种残缺写法 都要解析成同一套结果。
PARSE_CASES = (
    (),
    ("-u",),
    ("--task",),
    ("-u", "--task"),
    ("--task", ""),
    ("-u", "--task", "outlook_reg_loop.py"),
    ("-u", "--task", "outlook_reg_loop.py", "--count", "2"),
    ("register.py",),
    ("-u", "register.py", "--flag", "1"),
    ("--host", "127.0.0.1", "--port", "8799"),
    ("--task", "--flag"),
)


def _load_patched_entry_parser():
    """加载补丁入口里的 _rf_parse_task。

    wrapper_entry.py 的最后一行会 ``exec`` 官方入口的 code object，不能整体
    执行；只取到函数定义为止的那一段来编译。
    """
    source = (ROOT / "tools" / "binary_patch" / "wrapper_entry.py").read_text(encoding="utf-8")
    head, _, _ = source.partition("if not _rf_dispatch_task(")
    if not head:
        raise AssertionError("wrapper_entry.py 的入口调用行找不到了")
    namespace = {}
    exec(compile(head, "wrapper_entry.py", "exec"), namespace)  # noqa: S102 - 测试用途
    return namespace["_rf_parse_task"]


class TaskDispatchTests(unittest.TestCase):
    def test_parse_task_contract(self):
        self.assertIsNone(task_dispatch.parse_task([]))
        self.assertIsNone(task_dispatch.parse_task(["-u"]))
        self.assertIsNone(task_dispatch.parse_task(["--host", "127.0.0.1"]))
        # 缺目标时必须是 None（退回启动 WebUI），而不是把 "--task" 当脚本名。
        self.assertIsNone(task_dispatch.parse_task(["--task"]))
        self.assertIsNone(task_dispatch.parse_task(["-u", "--task"]))
        self.assertIsNone(task_dispatch.parse_task(["--task", ""]))

        self.assertEqual(
            task_dispatch.parse_task(["-u", "--task", "a.py", "--x", "1"]),
            ("a.py", ["--x", "1"]),
        )
        self.assertEqual(task_dispatch.parse_task(["a.py", "--x"]), ("a.py", ["--x"]))
        self.assertEqual(task_dispatch.parse_task(["-u", "a.py"]), ("a.py", []))

    def test_patched_entry_parser_matches_shared_implementation(self):
        rf_parse = _load_patched_entry_parser()
        for raw in PARSE_CASES:
            with self.subTest(raw=raw):
                self.assertEqual(
                    task_dispatch.parse_task(list(raw)),
                    rf_parse(list(raw)),
                    "补丁入口与 task_dispatch 的解析结果分叉了",
                )

    def test_source_entrypoints_delegate_to_shared_module(self):
        desktop = (ROOT / "reg_factory_desktop.py").read_text(encoding="utf-8")
        server = (ROOT / "scripts" / "reg-factory-server.py").read_text(encoding="utf-8")
        for name, text in (("reg_factory_desktop.py", desktop), ("scripts/reg-factory-server.py", server)):
            with self.subTest(entry=name):
                self.assertIn("import task_dispatch", text)
                self.assertIn("task_dispatch.parse_task(", text)
                self.assertIn("task_dispatch.dispatch_task(", text)
                # 不应该再各自内联一份 --task 解析。
                self.assertNotIn('== "--task"', text)

    def test_bundle_root_is_project_root_outside_frozen(self):
        self.assertEqual(Path(task_dispatch.bundle_root()).resolve(), ROOT)


class GatherSettledTests(unittest.TestCase):
    def test_exception_becomes_none_instead_of_aborting_the_batch(self):
        async def ok(value):
            return value

        async def boom():
            raise RuntimeError("upstream 502")

        async def exercise():
            return await gather_settled([ok("a"), boom(), ok("c")])

        self.assertEqual(asyncio.run(exercise()), ["a", None, "c"])

    def test_on_error_receives_the_exception_and_index(self):
        async def boom(tag):
            raise ValueError(tag)

        seen = []

        def on_error(exc, index):
            seen.append((type(exc).__name__, str(exc), index))
            return {"status": "failed", "index": index}

        async def exercise():
            return await gather_settled((boom(f"e{i}") for i in range(3)), on_error=on_error)

        results = asyncio.run(exercise())
        self.assertEqual([item["index"] for item in results], [0, 1, 2])
        self.assertEqual([item["status"] for item in results], ["failed"] * 3)
        self.assertEqual(seen, [("ValueError", "e0", 0), ("ValueError", "e1", 1), ("ValueError", "e2", 2)])

    def test_generator_input_is_consumed_eagerly_and_order_is_kept(self):
        async def later(value, delay):
            await asyncio.sleep(delay)
            return value

        async def exercise():
            return await gather_settled(
                later(value, delay) for value, delay in (("first", 0.02), ("second", 0.0))
            )

        self.assertEqual(asyncio.run(exercise()), ["first", "second"])

    def test_cancellation_is_not_swallowed(self):
        async def cancelled():
            raise asyncio.CancelledError("stop")

        async def exercise():
            return await gather_settled([cancelled()])

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
