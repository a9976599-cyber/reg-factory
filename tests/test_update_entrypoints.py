import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class UpdateEntrypointTests(unittest.TestCase):
    def test_windows_updater_checks_tasks_and_restarts_verified_webui(self):
        script = (ROOT / "update.ps1").read_text(encoding="utf-8")
        self.assertIn("Assert-NoRunningTasks", script)
        self.assertIn("git -C $Root pull --ff-only", script)
        self.assertIn("uvicorn\\s+webui\\.server:app", script)
        self.assertIn("ParentProcessId", script)
        self.assertIn("belongs to another reg-factory installation", script)
        self.assertIn("Wait-ForUpdatedPanel", script)

    def test_unix_updater_checks_tasks_and_restarts_verified_webui(self):
        script = (ROOT / "update.sh").read_text(encoding="utf-8")
        self.assertIn("assert_no_running_tasks", script)
        self.assertIn('git -C "$ROOT" pull --ff-only', script)
        self.assertIn("wait_for_panel", script)
        self.assertIn('bash "$ROOT/start.sh"', script)

    def test_bootstrap_scripts_expose_update_action(self):
        powershell = (ROOT / "bootstrap.ps1").read_text(encoding="utf-8")
        shell = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn('"update"', powershell)
        self.assertIn('REG_FACTORY_ACTION must be install, start, or update', powershell)
        self.assertIn('running.root', powershell)
        self.assertIn("update)", shell)
        self.assertIn("Action must be install, start, or update", shell)
        self.assertIn('get("root", "")', shell)

    def test_portable_updater_retries_and_verifies_release_package(self):
        script = (ROOT / "update-portable.ps1").read_text(encoding="utf-8")
        self.assertIn("Invoke-Download", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("update-result.json", (ROOT / "webui/server.py").read_text(encoding="utf-8"))
        self.assertIn("Downloaded package version", script)
        self.assertIn("Updated WebUI did not report version", script)

    def test_portable_updater_migrates_user_state_before_health_probe(self):
        """回归锁：更新器整目录替换时必须迁移用户状态，否则更新=清空数据。"""
        script = (ROOT / "update-portable.ps1").read_text(encoding="utf-8")
        # 白名单必须覆盖：用户配置、账号、授权缓存、浏览器用户配置
        self.assertIn("$UserStatePaths", script)
        for entry in (
            '"_internal\\.env"',
            '"login_extension"',
            '"auto_free_auth.json"',
            '".reg-factory-data"',
            '"cookies"',
        ):
            self.assertIn(entry, script)
        # 迁移发生在新包落位之后、健康探测启动之前
        landed = script.index("$movedNew = $true")
        migrate = script.index("Restore-UserState -SourceDir $backupDir -TargetDir $InstallDir")
        probe = script.index("Start-Process -FilePath (Join-Path $InstallDir")
        self.assertLess(landed, migrate)
        self.assertLess(migrate, probe)
        # 回滚路径：删除新目录之前必须先把用户状态回收进备份
        reclaim = script.index("Restore-UserState -SourceDir $InstallDir -TargetDir $backupDir")
        wipe = script.index("Remove-Item -LiteralPath $InstallDir -Recurse -Force")
        self.assertLess(reclaim, wipe)
        # 备份只在健康探测通过后才删除
        self.assertLess(script.index("if (-not $healthy)"), script.index("Remove-Item -LiteralPath $backupDir"))

    def test_portable_updater_merge_overwrites_existing_children(self):
        """H3 回归锁：回滚 -Merge 时已存在的嵌套 child 也必须复制覆盖。

        否则健康探测 45s 窗口内用户新写入的嵌套文件会在回滚时丢回旧快照。
        """
        script = (ROOT / "update-portable.ps1").read_text(encoding="utf-8")
        # Merge 分支对已存在 child 的覆盖必须走 Copy-Item -Force（用户增量优先）
        self.assertIn(
            "Copy-Item -LiteralPath $child.FullName -Destination $dst -Recurse -Force",
            script,
            "Merge 时已存在的目录 child 应复制覆盖到父目录",
        )
        self.assertIn(
            "Copy-Item -LiteralPath $child.FullName -Destination $dst -Force",
            script,
            "Merge 时已存在的文件 child 应复制覆盖到父目录",
        )

    def test_portable_updater_health_probe_follows_listen_host(self):
        """健康探测地址必须跟随 -ListenHost，通配地址才回落回环。"""
        script = (ROOT / "update-portable.ps1").read_text(encoding="utf-8")
        self.assertNotIn('$statusUrl = "http://127.0.0.1:', script)
        self.assertIn("$probeHost = $ListenHost", script)
        self.assertIn('$probeHost -eq "0.0.0.0"', script)


if __name__ == "__main__":
    unittest.main()
