"""回归测试：桌面入口必须把 `--task X.py` 路由到 runpy，不能再开 uvicorn/webview。

对应 bug：v2.0.8 frozen .exe 被 WebUI 反向唤起时只启了第二个「auto free 控制台」窗口，
        没真正执行任务。详见 ``reg_factory_desktop._dispatch_task``。
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "reg_factory_desktop.py"
SPEC = importlib.util.spec_from_file_location("reg_factory_desktop_under_test", SCRIPT)
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


class DesktopEntryTaskDispatchTests(unittest.TestCase):
    def setUp(self):
        # runpy is bound at module load via `import runpy`. Patch it on the launcher
        # object, not sys.modules, otherwise the module-level import wins.
        self._runpy_patch = patch.object(launcher.runpy, "run_path")
        self.mock_runpy = self._runpy_patch.start()
        launcher._TASK_DISPATCH = False

    def tearDown(self):
        self._runpy_patch.stop()

    def test_dispatch_task_routes_to_runpy_and_skips_uvicorn(self):
        target = "outlook_reg_loop.py"
        result = launcher._dispatch_task(["--task", target, "--count", "1"])
        self.assertTrue(result)
        self.mock_runpy.assert_called_once()
        called_path = self.mock_runpy.call_args.args[0]
        self.assertTrue(str(called_path).endswith(target))
        self.assertEqual(self.mock_runpy.call_args.kwargs["run_name"], "__main__")
        # argv must be rewritten so the child script sees itself as sys.argv[0]
        self.assertTrue(sys.argv[0].endswith(target))
        self.assertEqual(sys.argv[1:], ["--count", "1"])
        self.assertTrue(launcher._TASK_DISPATCH)

    def test_dispatch_task_accepts_bare_py_target(self):
        result = launcher._dispatch_task(["register.py", "--count", "1"])
        self.assertTrue(result)
        self.mock_runpy.assert_called_once()
        self.assertEqual(sys.argv[1:], ["--count", "1"])

    def test_dispatch_task_returns_false_when_no_task(self):
        self.assertFalse(launcher._dispatch_task([]))
        self.assertFalse(launcher._dispatch_task(["--host", "127.0.0.1"]))
        self.mock_runpy.assert_not_called()

    def test_dispatch_task_missing_target_raises(self):
        with self.assertRaises(SystemExit):
            launcher._dispatch_task(["--task", "does_not_exist.py"])

    def test_main_runs_task_and_never_starts_uvicorn(self):
        with patch.object(launcher, "_dispatch_task", return_value=True) as dispatch:
            with patch.object(launcher, "setup_log") as setup_log:
                with patch.object(launcher, "start_uvicorn_in_thread") as start_uv:
                    with patch.object(launcher, "wait_for_backend") as wait_backend:
                        launcher.main()
        dispatch.assert_called_once()
        setup_log.assert_not_called()
        start_uv.assert_not_called()
        wait_backend.assert_not_called()

    def test_main_starts_uvicorn_when_no_task_args(self):
        fake_server = object()
        with patch.object(launcher, "_dispatch_task", return_value=False) as dispatch:
            with patch.object(launcher, "setup_log"):
                with patch.object(launcher, "port_in_use", return_value=False):
                    with patch.object(launcher, "start_uvicorn_in_thread", return_value=fake_server):
                        with patch.object(launcher, "wait_for_backend", return_value=True):
                            with patch("builtins.__import__", wraps=__import__) as wrap_import:
                                # let the webview import pass through so msgbox isn't hit
                                def _import(name, *a, **kw):
                                    if name == "webview":
                                        class FakeWebview:
                                            @staticmethod
                                            def create_window(*a, **kw):
                                                return None
                                            @staticmethod
                                            def start(*a, **kw):
                                                return None
                                        return FakeWebview
                                    return wrap_import(name, *a, **kw)
                                wrap_import.side_effect = _import
                                launcher.main()
        dispatch.assert_called_once()

    def test_configure_live_output_uses_line_buffering_and_write_through(self):
        import unittest.mock as mock
        stdout = mock.MagicMock()
        stderr = mock.MagicMock()
        with patch.object(launcher.sys, "stdout", stdout):
            with patch.object(launcher.sys, "stderr", stderr):
                launcher._configure_live_output()
        stdout.reconfigure.assert_called_once_with(line_buffering=True, write_through=True)
        stderr.reconfigure.assert_called_once_with(line_buffering=True, write_through=True)


if __name__ == "__main__":
    unittest.main()