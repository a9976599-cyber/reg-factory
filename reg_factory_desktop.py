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
"""
import os
import runpy
import socket
import sys
import threading
import time
import traceback

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


def msgbox(text, title="reg-factory 控制台", icon=0x10):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, icon)
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
    for p in range(start, start + 50):
        if not port_in_use(p):
            return p
    return 0  # 全都占满 → 让系统分配随机端口


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
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            if r.status == 200:
                return True
        except Exception as e:
            log(f"wait backend retry: {type(e).__name__}: {e}")
        time.sleep(0.5)
    return False


def _configure_live_output():
    """WebUI 通过 stdout pipe 实时回显任务输出；强制行缓冲避免批量打印。"""
    os.environ["PYTHONUNBUFFERED"] = "1"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(line_buffering=True, write_through=True)
        except (OSError, ValueError):
            pass


def _dispatch_task(raw_args):
    """如果命令行是 `reg-factory.exe -u --task X.py ...`，直接跑 X.py 并返回 True。

    与上游 2.2.4 的 `scripts/reg-factory-server.py` 同形协议，保证 WebUI `_build_cmd`
    的 frozen 模式构造的命令行能直接落地。
    """
    global _TASK_DISPATCH
    if not raw_args:
        return False
    head = raw_args[0]
    if head == "--task":
        if len(raw_args) < 2:
            return False
        target = raw_args[1]
        arg_offset = 2
    elif head.lower().endswith(".py"):
        target = head
        arg_offset = 1
    else:
        return False
    _TASK_DISPATCH = True
    _configure_live_output()
    if getattr(sys, "frozen", False):
        # PyInstaller onedir 形态：解压后的临时目录包含全部 .py 任务
        bundle_root = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))
    else:
        bundle_root = os.path.dirname(os.path.abspath(__file__))
    target_path = os.path.join(bundle_root, target)
    if not os.path.isfile(target_path):
        raise SystemExit(f"task script not found: {target_path}")
    sys.path.insert(0, bundle_root)
    sys.argv = [target_path, *raw_args[arg_offset:]]
    log(f"task dispatch → {target_path} argv={sys.argv[1:]}")
    runpy.run_path(target_path, run_name="__main__")
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
        log("backend failed to start:\n" + traceback.format_exc())
        msgbox(
            "reg-factory 服务启动失败，无法打开控制台。\n"
            f"详情日志：{LOG_PATH}\n\n"
            "可尝试：关闭本机已开着的 reg-factory / python 相关窗口后重试。"
        )
        return

    log(f"backend ready: {url}")
    try:
        import webview
        webview.create_window(
            "reg-factory 控制台",
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
