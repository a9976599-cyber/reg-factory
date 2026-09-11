"""reg-factory 桌面版：单文件 EXE，自启动 WebUI + 内嵌窗口，不需要打开浏览器。

双击 exe → 启动 WebUI → 弹出桌面窗口直接显示控制台。
窗口关闭 = 退出服务。

健壮性设计：
- 默认端口 8799 被占用时（例如旧版 start.bat 起的服务还在跑），自动换空闲端口，
  保证双击一定有窗口弹出，不会“没反应 / 30 秒后悄悄退出”。
- 任何失败都写日志文件（exe 同目录 reg-factory-desktop.log，不可写则退 %TEMP%），
  并弹 MessageBox 提示，而不是静默退出。

任务派发：
- WebUI（`/api/run`）会通过 `_build_cmd` 用 `[exe, "-u", "--task", "outlook_reg_loop.py", ...]`
  把任务子进程反向唤起本 exe，让 frozen bootloader 把命令行路由到对应脚本（而不是再开一个
  控制台窗口 / uvicorn 实例）。本入口必须先识别 `--task` 再决定走哪条路径，否则就会
  出现「点运行任务 → 又开一个控制台窗口 → 任务没启动」的现象（v2.0.8 的回归）。
  解析规则统一在 `task_dispatch.py`，与 `scripts/reg-factory-server.py` 共用。

与官方冻结入口的差异（别再踩一次）：
- 官方 exe 的入口额外包含本仓库没有的两段 —— `ysq_auth.try_cached_login_quiet()`
  （闭源授权子系统）与 `webui.embedded_backends.try_start_embedded()`（内置引擎
  内联启动）。所以**从这份源码重建的 exe 不会带授权徽章**；正式发布包走
  「官方二进制 + 入口补丁」路线，见 tools/binary_patch/。
- 本文件对齐了官方的 `REG_FACTORY_SMOKE` 自检与窗口标题，尽量把这类漂移缩到最小。
"""
import os
import socket
import sys
import threading
import time
import traceback

import task_dispatch

DEFAULT_PORT = int(os.environ.get("REG_FACTORY_PORT", "8799"))
LOG_PATH = None
_TASK_DISPATCH = False


def log(msg):
    if not LOG_PATH:
        return
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def setup_log():
    global LOG_PATH
    base = None
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(base, "reg-factory-desktop.log")
    try:
        with open(candidate, "a", encoding="utf-8") as f:
            f.write("")
        LOG_PATH = candidate
    except Exception:
        LOG_PATH = os.path.join(os.environ.get("TEMP", "."), "reg-factory-desktop.log")


def msgbox(text, title="auto free 控制台", icon=0x10):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, icon)
    except Exception:
        pass


def _smoke_marker(state):
    """无界面自检标记:写 exe 同目录 auth-smoke-ok.txt。

    T25: 启动流程失败时绝不再写标记,避免「上次运气好写入的标志被本次失败
    误读为「后端可用」。同时尽量在失败时清理掉遗留的 marker。
    """
    try:
        exe_dir = os.path.dirname(
            os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)
        )
        marker = os.path.join(exe_dir, "auth-smoke-ok.txt")
        if state != "ok":
            # 失败时尝试清理标记,便于上游失败自检不被「上次 OK」误判。
            try:
                os.remove(marker)
            except OSError:
                pass
            return
        with open(marker, "w", encoding="utf-8") as f:
            f.write(f"smoke-{state} {time.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception:
        pass


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False


def find_free_port(start):
    """Find the first free TCP port starting from ``start``.

    T25: 全部被占用时抛 ``RuntimeError`` 而不是返回 0 — 调用方以前会把
    ``port=0`` 变成 ``uvicorn :0`` 然后等不到绑定;此处宁可直接失败。
    """
    for p in range(start, start + 50):
        if not port_in_use(p):
            return p
    raise RuntimeError(
        f"No free TCP port found in {start}..{start + 49}; "
        "close conflicting WebUIs or set --port to a larger value"
    )


def _uvicorn_log_to_file():
    """把 uvicorn 日志也写进 reg-factory-desktop.log，方便排障。"""
    import logging
    try:
        fh = logging.FileHandler(LOG_PATH or os.devnull, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                lg.removeHandler(h)
            lg.addHandler(fh)
            lg.propagate = False
    except Exception:
        pass


def start_uvicorn_in_thread(port):
    import uvicorn
    _uvicorn_log_to_file()
    config = uvicorn.Config("webui.server:app", host="127.0.0.1", port=port, log_config=None)
    server = uvicorn.Server(config)

    def _run():
        try:
            log(f"uvicorn run() starting on {port} …")
            server.run()
            log(f"uvicorn run() returned. started={server.started}")
        except BaseException:
            log("uvicorn thread crashed:\n" + traceback.format_exc())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return server


def wait_for_backend(port, timeout=40):
    """T25: 拆分为「端口可达」+ 「健康探测」两段;总时长从环境变量读取,默认 120s。

    ``REG_FACTORY_HEALTH_TIMEOUT`` 总秒数;``REG_FACTORY_HEALTH_INTERVAL`` 秒轮询
    间隔(默认 0.5s)。失败时返回 False,绝不写启动成功标记。
    """
    import urllib.request
    deadline = time.time() + float(
        os.environ.get("REG_FACTORY_HEALTH_TIMEOUT", "120")
    )
    interval = float(os.environ.get("REG_FACTORY_HEALTH_INTERVAL", "0.5"))
    last_err = None
    while time.time() < deadline:
        # 端口可达优先探测:TCP 握手比 HTTP / 更便宜,失败原因更明确。
        if not port_in_use(port):
            time.sleep(interval)
            continue
        try:
            r = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=2
            )
            if r.status == 200:
                return True
        except Exception as e:
            last_err = type(e).__name__ + ":" + str(e)[:120]
            log(f"wait backend retry: {last_err}")
        time.sleep(interval)
    if last_err:
        log(f"wait backend timeout ({timeout}s): {last_err}")
    return False


def _configure_live_output():
    """WebUI 通过 stdout pipe 实时回显任务输出；强制行缓冲避免批量打印。

    实现已收敛到 `task_dispatch.configure_live_output`（三个入口共用同一份），
    这里保留同名包装以兼容既有调用方。
    """
    task_dispatch.configure_live_output()


def _dispatch_task(raw_args):
    """如果命令行是 `reg-factory.exe -u --task X.py ...`，直接跑 X.py 并返回 True。

    解析与执行统一走 ``task_dispatch``（与 `scripts/reg-factory-server.py` 共用
    同一份实现），保证 WebUI `_build_cmd` 在 frozen 模式构造的命令行能直接落地，
    并且 `--task` 后面漏写脚本名时会退回正常启动路径而不是报错。
    """
    global _TASK_DISPATCH
    if task_dispatch.parse_task(raw_args) is None:
        return False
    _TASK_DISPATCH = True
    task_dispatch.dispatch_task(raw_args, log=log)
    return True


def main():
    global _TASK_DISPATCH

    # ---- TASK DISPATCH 必须在 uvicorn/webview 之前 ----
    raw_args = list(sys.argv[1:])
    if raw_args[:1] == ["-u"]:
        raw_args = raw_args[1:]
    if _dispatch_task(raw_args):
        return

    setup_log()
    log(f"=== reg-factory desktop start pid={os.getpid()} ===")

    port = DEFAULT_PORT
    if port_in_use(DEFAULT_PORT):
        # 默认端口被旧实例占着：换一个空闲端口，让这个新实例一定起得来。
        port = find_free_port(DEFAULT_PORT)
        log(f"port {DEFAULT_PORT} busy -> fallback port {port}")
    else:
        log(f"port {DEFAULT_PORT} free, use it")

    server = start_uvicorn_in_thread(port)
    url = f"http://127.0.0.1:{port}"

    if not wait_for_backend(port):
        # T25: 只有在 except 块内才能调用 traceback.format_exc();此处无法
        # 知道失败类型,就把它改为 ``log`` + 继续,这样失败自检仍看得到日志。
        log(
            f"backend failed to start (port={port}); see {LOG_PATH} for details"
        )
        if os.environ.get("REG_FACTORY_SMOKE") == "1":
            _smoke_marker("backend-fail")
        msgbox(
            "reg-factory 服务启动失败，无法打开控制台。\n"
            f"详情日志：{LOG_PATH}\n\n"
            "可尝试：关闭本机已开着的 reg-factory / python 相关窗口后重试。"
        )
        return

    log(f"backend ready: {url}")

    if os.environ.get("REG_FACTORY_SMOKE") == "1":
        # 冒烟模式：后端起来了就落一个标记然后退出，不弹窗口（发布流水线用）。
        _smoke_marker("ok")
        log("smoke ok, exit without window")
        try:
            import ysq_auth

            ysq_auth.shutdown()
        except BaseException:  # noqa: BLE001 - 源码形态没有闭源授权模块，忽略即可
            pass
        return

    try:
        import webview
        webview.create_window(
            "auto free 控制台",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
            background_color="#10141c",
        )
        webview.start(debug=False)
        log("webview window closed, exiting")
    except Exception as e:
        log("webview failed:\n" + traceback.format_exc())
        import webbrowser
        webbrowser.open(url)
        msgbox(
            f"内嵌窗口启动失败：{e}\n已改用浏览器打开。\n地址：{url}",
            icon=0x30,  # MB_ICONWARNING
        )
        try:
            while server.started:
                time.sleep(10)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
