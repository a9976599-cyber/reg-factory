# -*- coding: utf-8 -*-
"""同进程内置 A / O 后端（融合单包）—— 影子修复版 v4。

frozen(exe) 模式下，若 exe 同级存在 engine/aar 与 engine/oar 两个源码目录，
就在本进程里用线程把两个后端拉起来：
  - A 后端（any-auto-register account-manager）→ 127.0.0.1:8000
  - O 后端（outlook-auto-register 控制台）    → 127.0.0.1:8890

好处：发布包只有一个 Python 运行时（reg-factory 内嵌），不用再带 500MB 的两套 venv。

设计约定：
- 源码目录放 exe 同级 engine/aar、engine/oar（构建脚本负责拷贝裁剪）。
- 两个后端的数据库都通过环境变量固定到引擎目录内（不依赖 cwd），运行期谁都不碰谁的 cwd。
- 端口已被占用（外部 A/O 后端在跑）→ 跳过该后端，走原 bridge 外部逻辑。
- 开发模式（start.bat / 没有 engine/ 目录）自动跳过，完全不影响原流程。
- 单例幂等，失败只记日志，绝不影响 reg-factory 主服务。

官方 PYZ 版的两个缺陷（本 v4 修复，对外 API 与官方完全一致）：
1. ``_serve`` 固定 30s 看门狗 —— 全新解压包首次启动时 OAR 的重 import
   （无 ``__pycache__`` 冷编译 + 杀软逐文件扫描）普遍超过 30s，O 注册台被
   误判为启动失败且永远不再重试，用户看到「O 邮箱注册台不能用」。
2. ``try_start_embedded`` 同步串行等 A、O 各 30s —— 把主面板的启动一起拖住，
   桌面窗口长时间白屏，授权登录也随之迟到，期间所有功能被授权门禁拦截。

v4 行为：
- A / O 并行后台启动，``try_start_embedded`` 立即返回（面板秒开）；
- 看门狗延长到 180s，30s 起每 15s 记录一次等待进度（冷编译提示）；
- ``status()`` 懒重试：某后端线程已结束但仍未监听端口时，冷却 20s 后自动补启，
  每引擎每进程最多补 3 次（不含首次开机启动），避免真实 bug（如 import 报错）
  被无限重试刷屏。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path

ENGINE_REL = "engine"
AAR_SUBDIR = "aar"
OAR_SUBDIR = "oar"

DEFAULT_AAR_PORT = int(os.getenv("AAR_PORT", "8000"))
DEFAULT_OAR_PORT = int(os.getenv("OAR_PORT", "8890"))

# ---- v4 调参 -------------------------------------------------------------
_WATCHDOG_SECONDS = 180.0    # 单后端启动看门狗（官方 30s 的 6 倍）
_PROGRESS_FIRST = 30.0       # 从这一秒起开始输出等待进度
_PROGRESS_EVERY = 15.0       # 进度日志间隔
_RETRY_COOLDOWN = 20.0       # 懒重试冷却
_MAX_RETRIES = 3             # 每引擎每进程最大懒重试次数（不含首次启动）

_log_lock = threading.Lock()
_LOG_PATH: str | None = None

_state = {"tried": False, "aar": False, "oar": False}
# T9: 改用 RLock 以允许同线程在持锁状态再调用 status()/try_start_embedded()
# 时不会自死锁（比如重试循环里再查询 state）。
_state_lock = threading.RLock()

# v4 内部簿记（官方版本没有的私有状态）
_threads = {"aar": None, "oar": None}          # tag -> 正在跑的启动线程或 None
_attempts = {"aar": 0, "oar": 0}               # 总尝试次数（含首次，日志用）
_retries = {"aar": 0, "oar": 0}                # 懒重试次数（预算单独计）
_last_attempt = {"aar": 0.0, "oar": 0.0}       # time.monotonic()
_missing_dir = {"aar": False, "oar": False}    # 目录缺失时不重试


def _log(msg: str) -> None:
    global _LOG_PATH
    try:
        if _LOG_PATH is None:
            base = _engine_base() or Path.cwd()
            _LOG_PATH = str(base / "reg-factory-embedded.log")
        with _log_lock:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _engine_base() -> Path | None:
    env = (os.getenv("RF_ENGINE_DIR") or "").strip()
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def status() -> dict:
    """对外状态（键与官方一致）：tried / aar / oar。

    v4 额外行为：查询时顺带做一次「懒重试」——若某后端从未成功监听、
    启动线程已结束、冷却已过且尝试次数未耗尽，则自动补启一次（后台）。
    """
    _lazy_retry_engines()
    with _state_lock:
        return {
            "tried": _state["tried"],
            "aar": _state["aar"],
            "oar": _state["oar"],
        }


# ---- v4：并行启动 + 懒重试 -------------------------------------------------


def _spawn_engine(tag: str) -> None:
    """在后台线程里跑对应引擎的官方启动函数，结束回写 _state。"""

    def _run() -> None:
        try:
            base = _engine_base()
            if base is None:
                _log("engine dir not resolvable, skip embedded %s" % tag)
                return
            if tag == "aar":
                aar_dir = base / ENGINE_REL / AAR_SUBDIR
                if not (aar_dir / "main.py").exists():
                    _log("A engine not found at %s" % aar_dir)
                    _missing_dir["aar"] = True
                    return
                _state["aar"] = _start_aar(aar_dir)
            else:
                oar_dir = base / ENGINE_REL / OAR_SUBDIR
                if not (oar_dir / "webapp").is_dir():
                    _log("O engine not found at %s" % oar_dir)
                    _missing_dir["oar"] = True
                    return
                _state["oar"] = _start_oar(oar_dir)
        except BaseException:  # noqa: BLE001 - 后台线程异常只落日志
            _log("[%s] engine thread error:\n%s" % (tag, traceback.format_exc()))
        finally:
            _threads[tag] = None

    t = threading.Thread(target=_run, daemon=True, name="embedded-%s" % tag)
    _threads[tag] = t
    _attempts[tag] += 1
    _last_attempt[tag] = time.monotonic()
    t.start()


def _lazy_retry_engines() -> None:
    """status() 的自愈逻辑：某引擎没起来且不在启动中 → 冷却后补启。"""
    now = time.monotonic()
    for tag, port in (("aar", DEFAULT_AAR_PORT), ("oar", DEFAULT_OAR_PORT)):
        if _state.get(tag):
            continue  # 已在监听
        if _missing_dir.get(tag):
            continue  # 目录都没有，重试无意义
        if _threads.get(tag) is not None and _threads[tag].is_alive():
            continue  # 正在启动（冷编译可能要好几分钟）
        if _retries.get(tag, 0) >= _MAX_RETRIES:
            continue  # 重试预算耗尽（真实 bug 交给人看日志）
        if now - _last_attempt.get(tag, 0.0) < _RETRY_COOLDOWN:
            continue  # 冷却中
        if _port_in_use(port):
            # 端口被外部实例占用：视为正常，不再折腾
            _state[tag] = True
            continue
        _log("[%s] lazy-retry embedded start (attempt %d)" % (tag, _attempts[tag] + 1))
        _retries[tag] += 1
        _spawn_engine(tag)


def try_start_embedded() -> dict:
    """幂等入口：首次调用并行拉起 A / O（后台），立即返回状态。

    官方版在此同步串行等待两个后端各 30s，会把主面板启动拖住；
    v4 改为后台启动 + 立即返回，面板秒开，后端就绪由 status() 懒重试兜底。
    """
    with _state_lock:
        if _state["tried"]:
            return status()
        _state["tried"] = True

    base = _engine_base()
    if base is None:
        _log("engine dir not resolvable, skip embedded backends")
        return status()

    aar_dir = base / ENGINE_REL / AAR_SUBDIR
    oar_dir = base / ENGINE_REL / OAR_SUBDIR

    if (aar_dir / "main.py").exists():
        if _port_in_use(DEFAULT_AAR_PORT):
            _log("port %d already in use, skip embedded A" % DEFAULT_AAR_PORT)
            _state["aar"] = True
        else:
            _log("embed A: launching in background from %s" % aar_dir)
            _spawn_engine("aar")
    else:
        _log("A engine not found at %s" % aar_dir)
        _missing_dir["aar"] = True

    if (oar_dir / "webapp").is_dir():
        if _port_in_use(DEFAULT_OAR_PORT):
            _log("port %d already in use, skip embedded O" % DEFAULT_OAR_PORT)
            _state["oar"] = True
        else:
            _log("embed O: launching in background from %s" % oar_dir)
            _spawn_engine("oar")
    else:
        _log("O engine not found at %s" % oar_dir)
        _missing_dir["oar"] = True

    return status()


def _serve(app, port: int, tag: str) -> bool:
    import uvicorn

    cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(cfg)

    def _run() -> None:
        try:
            server.run()
        except BaseException:  # noqa: BLE001 - uvicorn 线程异常落日志
            _log("[%s] uvicorn thread error:\n%s" % (tag, traceback.format_exc()))

    threading.Thread(target=_run, daemon=True, name="embedded-%s-serve" % tag).start()

    started_at = time.monotonic()
    deadline = started_at + _WATCHDOG_SECONDS
    next_progress = started_at + _PROGRESS_FIRST
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            _log("[%s] up on :%d" % (tag, port))
            return True
        now = time.monotonic()
        if now >= next_progress:
            _log(
                "[%s] still waiting started (%ds elapsed; 全新解压包首次启动需冷编译, "
                "可能要 1-3 分钟, 请勿关闭)"
                % (tag, int(now - started_at))
            )
            next_progress += _PROGRESS_EVERY
        time.sleep(0.3)
    _log("[%s] did not reach started within %ds (线程仍在后台, 可能稍后自动就绪)"
         % (tag, int(_WATCHDOG_SECONDS)))
    return False


def _start_aar(aar_dir: Path) -> bool:
    _log("embed A: import from %s" % aar_dir)
    try:
        data_dir = aar_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(
            "ACCOUNT_MANAGER_DATABASE_URL",
            "sqlite:///%s" % (data_dir / "account_manager.db").as_posix(),
        )
        if str(aar_dir) not in sys.path:
            sys.path.insert(0, str(aar_dir))
        import main as aar_main

        if getattr(aar_main, "app", None) is None:
            _log("A engine has no app, skip")
            return False
        return _serve(aar_main.app, DEFAULT_AAR_PORT, "aar")
    except BaseException:  # noqa: BLE001 - 与官方一致：失败只记日志
        _log("A embed failed:\n%s" % traceback.format_exc())
        return False


def _start_oar(oar_dir: Path) -> bool:
    _log("embed O: import from %s" % oar_dir)
    try:
        accounts_dir = oar_dir / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(
            "OUTLOOK_DB_PATH", str(accounts_dir / "outlook.db")
        )
        if str(oar_dir) not in sys.path:
            sys.path.insert(0, str(oar_dir))
        # 官方语义：绑定模块本身（webapp.server 里没有叫 server 的属性）
        import webapp.server as oar_server

        if getattr(oar_server, "app", None) is None:
            _log("O engine has no app, skip")
            return False
        return _serve(oar_server.app, DEFAULT_OAR_PORT, "oar")
    except BaseException:  # noqa: BLE001 - 与官方一致：失败只记日志
        _log("O embed failed:\n%s" % traceback.format_exc())
        return False
