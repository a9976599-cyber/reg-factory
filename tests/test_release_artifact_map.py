"""发布物同步表的不变量。

2.2.7 的翻车方式是「仓库改了、用户下载的包没改」，而当时 621 个测试全跑在源码树上，
没有一条检查 zip。这里把 `tools/release/pkg_sync_map.py` 的同步表本身变成断言：
源文件必须存在、护栏内容必须在源里、上游标识必须清干净。真正对 zip 的端到端断言由
`tools/release/assert_release_artifact.py` 承担（需要产物，跑在发布前）。
"""
import unittest
from pathlib import Path

from tools.release import pkg_sync_map

ROOT = Path(__file__).resolve().parents[1]


class ReleaseArtifactMapTests(unittest.TestCase):
    def test_every_sync_target_has_an_existing_source(self):
        missing = [
            "%s <- %s" % (arc, src)
            for arc, src in sorted(pkg_sync_map.sync_map().items())
            if not (ROOT / src).is_file()
        ]
        self.assertEqual(missing, [], "同步表指向了不存在的源文件：%s" % missing)

    def test_sync_map_covers_the_loose_layer_only(self):
        """同步表只能包含「文件覆盖能生效」的路径。

        应用主体（config / common.sms / webui.aar_bridge 等）在 exe 内嵌归档里，
        丢文件进去不会生效 —— 把它们写进同步表会给出虚假的安全感，所以显式禁掉。
        例外：`_internal/webui/server.py` 由 wrapper_entry v3 影子加载（见
        tests/test_wrapper_shadow_modules.py），文件覆盖经由 sys.modules 预注册生效。
        """
        # 2.3.3：这 4 个是审计新增、官方 PYZ 里确实【没有】的 common 模块。
        # 真机验证（reg-factory.exe -u --task 探针在冻结进程内 import）：未随包发出
        # 时报 ModuleNotFoundError；放进 _internal/common/ 后全部可导入 —— 证明
        # 冻结 common 包的 __path__ 确实覆盖 _internal/common，松散文件生效。
        # 依赖来源：async_io <- mailbox_broker/register/register_outlook_standalone；
        # atomic_io <- common/session_export；run_context <- outlook_reg_loop/
        # register_outlook_standalone；path_guard <- wrapper_entry(T34)。
        allowed_common = {
            "_internal/common/async_batch.py",
            "_internal/common/env_refresh.py",
            "_internal/common/sms.py",
            "_internal/common/session_export.py",
            "_internal/common/async_io.py",
            "_internal/common/atomic_io.py",
            "_internal/common/run_context.py",
            "_internal/common/path_guard.py",
        }
        # 影子加载名单（必须与 tools/binary_patch/wrapper_entry.py 的
        # _RF_SHADOW_MODULES 一致；webui/server.py 同时也在同步表）
        shadow_loaded = {
            "_internal/webui/embedded_backends.py",
            "_internal/webui/server.py",
        }
        offenders = []
        for arc in pkg_sync_map.sync_map():
            if arc in shadow_loaded:
                continue
            if arc.startswith("_internal/common/") and arc not in allowed_common:
                offenders.append(arc)
            elif arc.startswith("_internal/webui/") and not arc.startswith(
                "_internal/webui/static/"
            ):
                offenders.append(arc)
        self.assertEqual(
            offenders,
            [],
            "这些路径在冻结归档里，文件覆盖无效，不应出现在同步表：%s" % offenders,
        )

    def test_every_content_guard_points_at_a_synced_package_path(self):
        synced = set(pkg_sync_map.sync_map())
        stray = [arc for arc, _ in pkg_sync_map.CONTENT_GUARDS if arc not in synced]
        self.assertEqual(stray, [], "内容护栏指向了未同步的路径：%s" % stray)

    def test_content_guards_are_present_in_the_source(self):
        """护栏字符串必须在仓库源里真的存在，否则断言永远失败（或形同虚设）。"""
        missing = []
        for arc, needle in pkg_sync_map.CONTENT_GUARDS:
            src = ROOT / pkg_sync_map.sync_map()[arc]
            if needle not in src.read_bytes():
                missing.append("%s 里没有 %r" % (src.relative_to(ROOT), needle))
        self.assertEqual(missing, [], "护栏字符串在源文件里缺失：%s" % missing)

    def test_loose_task_scripts_are_all_declared(self):
        """包内确实以松散文件存在的任务脚本，必须在同步表里，避免漏更新。"""
        for name in pkg_sync_map.TASK_SCRIPTS:
            self.assertIn("_internal/" + name, pkg_sync_map.sync_map())
        for name in pkg_sync_map.TOOLS:
            self.assertIn("_internal/tools/" + name, pkg_sync_map.sync_map())

    def test_repo_version_file_ends_with_newline(self):
        """VERSION 逐字节约定：内容恒为 b\"<version>\\n\"。

        发布物断言对 VERSION 是逐字节比对（不开 strip 容错口），打包器只需
        逐字节复制仓库 VERSION 即可满足；本测试锁住房子的另一端 —— 仓库
        VERSION 自身必须符合约定，否则打包器怎么复制都是错。
        """
        data = (ROOT / "VERSION").read_bytes()
        self.assertTrue(data.endswith(b"\n"), "VERSION 必须以换行结尾：%r" % data)
        body = data[:-1]
        self.assertNotIn(b"\n", body)
        self.assertNotIn(b"\r", data)
        self.assertEqual(body, body.strip(), "VERSION 版本号两侧不能有空白：%r" % data)

    def test_no_upstream_marker_in_synced_scripts(self):
        """脚本类同步源不能指向上游仓库。

        只查脚本扩展名：CHANGELOG / 文档里的历史沿革与出处说明是正当内容，
        一律禁掉会逼着人删掉真实信息。
        """
        hits = []
        for arc, src in sorted(pkg_sync_map.sync_map().items()):
            if Path(src).suffix.lower() not in pkg_sync_map.SCRIPT_EXT:
                continue
            data = (ROOT / src).read_bytes()
            for needle in pkg_sync_map.FORBIDDEN_SUBSTR:
                if needle in data:
                    hits.append("%s (%s)" % (src, needle.decode()))
        self.assertEqual(hits, [], "同步的脚本里仍有上游标识：%s" % hits)

    def test_runtime_artifacts_are_detected_and_clean_tree_passes(self):
        """发布闸门必须能拦住运行期产物。

        2.3.3 打包实测：运行期往暂存包写了
        `_internal/runtime/state/custom_sms_pool.json(.lock)`、
        `engine/aar/data/account_manager.db`、`engine/oar/accounts/outlook.db(-wal/-shm)`，
        而旧闸门只查「顶层 .log」，于是 6 个文件全部漏过、仍报 PASS。
        这里锁定加固后的行为：脏树全命中，干净树不误伤。
        """
        import tempfile

        from tools.release import assert_release_artifact as gate

        dirty = [
            "_internal/runtime/state/custom_sms_pool.json",
            "_internal/runtime/state/custom_sms_pool.json.lock",
            "engine/aar/data/account_manager.db",
            "engine/oar/accounts/outlook.db-wal",
            "engine/oar/accounts/outlook.db-shm",
            "reg-factory-desktop.log",
            "_internal/deep/nested/server.log",
            ".env",
            "auto_free_auth.json",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for rel in dirty:
                p = Path(tmp) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
            hits = {rel for _kind, rel in gate.runtime_artifact_hits(tmp)}
            self.assertEqual(
                hits, set(dirty), "运行期产物漏检：%s" % sorted(set(dirty) - hits)
            )

        # 官方包真实存在的合法文件不能被误伤（.env.example 是随包模板，
        # _internal/common/*.py 是松散模块，账本类 .json 无扩展名冲突）。
        legit = [
            "VERSION",
            ".env.example",
            "_internal/.env.example",
            "_internal/common/async_io.py",
            "engine/aar/data/seed_accounts.json",
            "_internal/python312.dll",
            "reg-factory.exe",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for rel in legit:
                p = Path(tmp) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
            self.assertEqual(
                gate.runtime_artifact_hits(tmp), [], "干净包被误伤"
            )

    def test_negative_guards_are_synced_and_absent_from_source(self):
        missing = [
            arc
            for arc, _ in pkg_sync_map.NEGATIVE_GUARDS
            if arc not in pkg_sync_map.sync_map()
        ]
        self.assertEqual(missing, [], "负向护栏指向未同步的路径：%s" % missing)
        violated = []
        for arc, needle in pkg_sync_map.NEGATIVE_GUARDS:
            src = ROOT / pkg_sync_map.sync_map()[arc]
            if needle in src.read_bytes():
                violated.append("%s: %r" % (arc, needle.decode()))
        self.assertEqual(violated, [], "负向护栏被违反：%s" % violated)


if __name__ == "__main__":
    unittest.main()
