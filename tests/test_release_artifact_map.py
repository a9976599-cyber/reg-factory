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

        应用主体（config / common.* / webui.server 等）在 exe 内嵌归档里，丢文件进去
        不会生效 —— 把它们写进同步表会给出虚假的安全感，所以显式禁掉。
        """
        allowed_common = {
            "_internal/common/async_batch.py",
            "_internal/common/env_refresh.py",
        }
        offenders = []
        for arc in pkg_sync_map.sync_map():
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
