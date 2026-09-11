# -*- coding: utf-8 -*-
"""embedded_backends v4 影子修复版的行为回归锁。

官方 PYZ 版缺陷（2.3.2 修复动机）：
  1. ``_serve`` 30s 看门狗误杀全新解压包 OAR 的长冷编译（实测 >100s）；
  2. ``try_start_embedded`` 同步串行等 A、O 各 30s，把主面板启动拖住，
     授权登录随之迟到，期间所有功能被 license_guard 402 拦截。

这里锁住 v4 的四个关键行为：
  - try_start_embedded 立即返回（不阻塞面板启动）；
  - status() 返回键形状与官方一致（tried/aar/oar）；
  - 引擎在后台并行启动，结果经由 status() 可见；
  - 懒重试自愈 + 重试预算封顶。
"""
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webui import embedded_backends as eb  # noqa: E402


class EmbeddedBackendsV4Tests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.base = base
        self._old = {
            "engine_base": eb._engine_base,
            "start_aar": eb._start_aar,
            "start_oar": eb._start_oar,
            "state": dict(eb._state),
            "threads": dict(eb._threads),
            "attempts": dict(eb._attempts),
            "retries": dict(eb._retries),
            "last_attempt": dict(eb._last_attempt),
            "missing_dir": dict(eb._missing_dir),
            "cooldown": eb._RETRY_COOLDOWN,
        }
        eb._engine_base = lambda: base
        # 重置单例状态，模拟全新进程
        eb._state.update({"tried": False, "aar": False, "oar": False})
        eb._threads.update({"aar": None, "oar": None})
        eb._attempts.update({"aar": 0, "oar": 0})
        eb._retries.update({"aar": 0, "oar": 0})
        eb._last_attempt.update({"aar": 0.0, "oar": 0.0})
        eb._missing_dir.update({"aar": False, "oar": False})
        # 引擎目录布局
        aar_dir = base / eb.ENGINE_REL / eb.AAR_SUBDIR
        aar_dir.mkdir(parents=True)
        (aar_dir / "main.py").touch()
        oar_dir = base / eb.ENGINE_REL / eb.OAR_SUBDIR
        oar_dir.mkdir(parents=True)
        (oar_dir / "webapp").mkdir()
        self._old_cwd = None

    def tearDown(self):
        eb._engine_base = self._old["engine_base"]
        eb._start_aar = self._old["start_aar"]
        eb._start_oar = self._old["start_oar"]
        eb._state.update(self._old["state"])
        eb._threads.update(self._old["threads"])
        eb._attempts.update(self._old["attempts"])
        eb._retries.update(self._old["retries"])
        eb._last_attempt.update(self._old["last_attempt"])
        eb._missing_dir.update(self._old["missing_dir"])
        eb._RETRY_COOLDOWN = self._old["cooldown"]
        self._tmp.cleanup()

    # ------------------------------------------------------------------

    def test_status_keys_match_official_shape(self):
        st = eb.try_start_embedded()
        self.assertEqual(set(st.keys()), {"tried", "aar", "oar"})
        self.assertTrue(st["tried"])

    def test_try_start_returns_immediately(self):
        started = time.monotonic()
        eb.try_start_embedded()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0, "try_start_embedded 不应阻塞面板启动")

    def test_engines_start_in_background_and_become_ready(self):
        def fake_start(engine_dir, tag_box, tag):
            time.sleep(0.3)
            tag_box[tag] = True
            return True

        box = {}
        eb._start_aar = lambda d: fake_start(d, box, "aar")
        eb._start_oar = lambda d: fake_start(d, box, "oar")
        eb.try_start_embedded()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            st = eb.status()
            if st["aar"] and st["oar"]:
                break
            time.sleep(0.05)
        self.assertTrue(st["aar"], "A 后端未在后台就绪")
        self.assertTrue(st["oar"], "O 后端未在后台就绪")

    def test_lazy_retry_recovers_failed_engine(self):
        calls = {"oar": 0}

        def flaky_start(oar_dir):
            calls["oar"] += 1
            if calls["oar"] < 3:
                raise RuntimeError("simulated cold-start failure")
            return True

        # 模拟：首启已尝试过一次且失败，冷却已过
        eb._state["tried"] = True
        eb._attempts["oar"] = 1
        eb._last_attempt["oar"] = time.monotonic() - 999.0
        eb._RETRY_COOLDOWN = 0.05
        eb._start_aar = lambda d: True
        eb._start_oar = flaky_start

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if eb.status()["oar"]:
                break
            time.sleep(0.05)
        self.assertTrue(eb._state["oar"], "懒重试未能自愈 O 后端")
        self.assertEqual(calls["oar"], 3, "应恰好重试到第 3 次成功")

    def test_retry_budget_is_capped(self):
        calls = {"oar": 0}

        def always_fail(oar_dir):
            calls["oar"] += 1
            raise RuntimeError("permanent failure")

        eb._state["tried"] = True
        eb._attempts["oar"] = 1
        eb._last_attempt["oar"] = time.monotonic() - 999.0
        eb._RETRY_COOLDOWN = 0.05
        eb._start_oar = always_fail

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and calls["oar"] < 1 + eb._MAX_RETRIES:
            eb.status()
            time.sleep(0.05)
        time.sleep(0.3)
        calls_after_settle = calls["oar"]
        for _ in range(10):
            eb.status()
            time.sleep(0.02)
        self.assertEqual(calls["oar"], calls_after_settle, "重试预算未封顶")
        self.assertLessEqual(calls["oar"], 1 + eb._MAX_RETRIES)


if __name__ == "__main__":
    unittest.main()
