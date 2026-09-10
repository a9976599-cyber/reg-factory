import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "tools" / "binary_patch" / "wrapper_entry.py"


class WrapperShadowModuleTests(unittest.TestCase):
    """回归锁：v3 入口的影子加载机制。

    PYZ 里的 webui.server / common.sms / common.session_export 是官方旧版，
    PYZ 的 common 常规包还会遮蔽 _internal/common 下的松散修复文件。
    仓库侧对这些文件的修复只能靠入口影子加载送达冻结进程 —— 这里锁住
    影子清单与失败回退语义，防止后续改动把机制悄悄拆掉。
    """

    @classmethod
    def setUpClass(cls):
        cls.source = WRAPPER.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _find_tuple_of_pairs(self, assign_name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == assign_name for t in node.targets
            ):
                return node.value
        return None

    def test_shadow_list_covers_all_patched_frozen_modules(self):
        value = self._find_tuple_of_pairs("_RF_SHADOW_MODULES")
        self.assertIsNotNone(value, "_RF_SHADOW_MODULES 常量缺失")
        pairs = {
            pair.args[0].value if isinstance(pair, ast.Call) else pair.elts[0].value
            for pair in value.elts
        }
        for required in (
            "task_dispatch",
            "common.sms",
            "common.session_export",
            "common.env_refresh",
            "common.async_batch",
            "webui.server",
        ):
            self.assertIn(required, pairs, "影子清单缺少 %s" % required)

    def test_webui_server_shadows_after_its_loose_dependencies(self):
        value = self._find_tuple_of_pairs("_RF_SHADOW_MODULES")
        order = [
            pair.elts[0].value if not isinstance(pair, ast.Call) else pair.args[0].value
            for pair in value.elts
        ]
        self.assertLess(
            order.index("common.env_refresh"), order.index("webui.server"),
            "webui.server 依赖 env_refresh，必须晚于它加载",
        )
        self.assertLess(
            order.index("common.async_batch"), order.index("webui.server"),
            "webui.server 依赖 async_batch，必须晚于它加载",
        )

    def test_shadow_failure_falls_back_to_frozen_module(self):
        self.assertIn("sys.modules.pop(modname, None)", self.source)
        self.assertIn("shadow-load failed", self.source)
        # 失败只允许跳过该项，绝不允许中断入口流程
        self.assertIn("except BaseException as exc:", self.source)

    def test_task_target_escape_check_uses_realpath_containment(self):
        self.assertIn("def _rf_safe_task_path", self.source)
        self.assertIn("os.path.normcase", self.source)
        self.assertIn("os.path.realpath", self.source)
        self.assertIn("root_real + os.sep", self.source)
        self.assertIn("task script outside bundle root", self.source)

    def test_entry_still_delegates_to_official_code_object(self):
        # test_task_dispatch.py 按 "if not _rf_dispatch_task(" 切片加载，
        # 这一行必须保留且在影子加载之后。
        self.assertIn("_rf_shadow_load()", self.source)
        self.assertLess(
            self.source.index("_rf_shadow_load()"),
            self.source.index("if not _rf_dispatch_task("),
        )
        self.assertIn('exec(_RF_ORIG, sys.modules["__main__"].__dict__)', self.source)


if __name__ == "__main__":
    unittest.main()
