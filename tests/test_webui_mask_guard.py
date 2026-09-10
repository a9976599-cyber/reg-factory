import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("REG_FACTORY_ENV_FILE", os.path.join(tempfile.mkdtemp(), ".env"))

import webui.server as server  # noqa: E402


def _schema_keys():
    secrets, plain = [], []
    for group in server.schema.ENV_SCHEMA:
        for item in group["items"]:
            (secrets if item.get("secret") else plain).append(item["key"])
    return secrets, plain


class MaskGuardTests(unittest.TestCase):
    """回归锁：面板回显掩码 "********" 绝不能被保存接口写进 .env。

    2.2.9 的 /api/env 与 /api/proxy 会把整表提交原样落盘 —— 前端表单对
    secret 键只回显掩码，用户改任何一项再保存，所有真实凭证就全被字面
    "********" 覆盖（这正是「配置老是坏」的直接元凶）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_env_path, self._old_example = server.ENV_PATH, server.ENV_EXAMPLE
        server.ENV_PATH = os.path.join(self.tmp, ".env")
        server.ENV_EXAMPLE = str(Path(server.ROOT) / ".env.example")
        self._patcher = mock.patch.object(server, "_apply_saved_env", lambda updates: None)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        server.ENV_PATH, server.ENV_EXAMPLE = self._old_env_path, self._old_example

    def test_is_masked(self):
        self.assertTrue(server._is_masked("********"))
        self.assertTrue(server._is_masked("  ********  "))
        self.assertFalse(server._is_masked("********x"))
        self.assertFalse(server._is_masked(""))
        self.assertFalse(server._is_masked(None))

    def test_api_env_save_skips_masked_secrets(self):
        from fastapi.testclient import TestClient

        secrets, plain = _schema_keys()
        self.assertTrue(secrets and plain, "schema 必须同时含 secret 与普通键")
        secret_key, plain_key = secrets[0], plain[0]

        client = TestClient(server.app)
        # 第一笔：写入真实 secret
        r = client.post("/api/env", json={"env": {secret_key: "REAL-SECRET-123"}})
        self.assertEqual(r.status_code, 200)
        body = Path(server.ENV_PATH).read_text(encoding="utf-8")
        self.assertIn("REAL-SECRET-123", body)

        # 第二笔：模拟用户只改一个普通项，secret 保持前端回显的掩码
        r = client.post("/api/env", json={"env": {secret_key: "********", plain_key: "plain-v2"}})
        self.assertEqual(r.status_code, 200)
        body = Path(server.ENV_PATH).read_text(encoding="utf-8")
        self.assertIn("REAL-SECRET-123", body, "掩码提交不得覆盖真实凭证")
        self.assertNotIn("********", body)
        self.assertIn("plain-v2", body)

    def test_api_proxy_save_backfills_masked_credentials(self):
        from fastapi.testclient import TestClient

        client = TestClient(server.app)
        saved = {}
        captured = {}
        original_write = server._write_env_file

        def fake_write_env(path, updates):
            saved.update(updates)
            original_write(path, updates)

        def fake_read_config(key, default=""):
            return {"CLASH_SECRET": "old-clash-secret"}.get(key, default)

        with mock.patch.object(server, "_write_env_file", fake_write_env), \
                mock.patch.object(server, "_read_config_val", fake_read_config), \
                mock.patch.object(server, "_apply_saved_env", lambda updates: captured.update(updates)):
            r = client.post("/api/proxy", json={"config": {
                "PROXY_MODE": "clash_auto",
                "CLASH_SECRET": "********",
                "REG_FACTORY_MAX_CONCURRENCY": "10",
            }})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(saved.get("CLASH_SECRET"), "old-clash-secret", "掩码必须回填当前真实值")
        self.assertNotEqual(saved.get("CLASH_SECRET"), "********")
        self.assertEqual(saved.get("PROXY_MODE"), "clash_auto")


if __name__ == "__main__":
    unittest.main()
