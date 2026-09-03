"""reg-factory 桌面版：单文件 EXE，自启动 WebUI + 内嵌窗口，不需要打开浏览器。

双击 exe → 启动 WebUI → 弹出桌面窗口直接显示控制台。
窗口关闭 = 退出服务。

健壮性设计：
- 默认端口 8799 被占用时（例如旧版 start.bat 起的服务还在跑），自动换空闲端口，
  保证双击一定有窗口弹出，不会“没反应 / 30 秒后悄悄退出”。
- 任何失败都写日志文件（exe 同目录 reg-factory-desktop.log，不可写则退 %TEMP%），
  并弹 MessageBox 提示，而不是静默退出。
"""
import os
import socket
import sys
import threading
import time
import traceback

DEFAULT_PORT = int(os.environ.get("REG_FACTORY_PORT", "8799"))
LOG_PATH = None


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


def main():
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
