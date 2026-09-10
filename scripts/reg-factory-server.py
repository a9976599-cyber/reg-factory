"""Entry point used by the desktop sidecar and PyInstaller build."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import task_dispatch  # noqa: E402 - 必须在 sys.path 调整之后导入

_TASK_DISPATCH = False


def _configure_live_output() -> None:
    """Make frozen task output visible to the WebUI as each line is written.

    实现收敛在 `task_dispatch.configure_live_output`，这里只是保留同名包装。
    """
    task_dispatch.configure_live_output()


def _configure_frozen_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    if sys.platform == "darwin":
        local_root = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        local_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        local_root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    data_root = Path(os.environ.setdefault("REG_FACTORY_DATA_DIR", str(local_root / "RegFactory")))
    env_path = Path(os.environ.setdefault("REG_FACTORY_ENV_FILE", str(data_root / ".env")))
    data_root.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        example = bundle_root / ".env.example"
        if example.is_file():
            shutil.copyfile(example, env_path)

    helper = bundle_root / "common" / "bundled_browser_helper.py"
    if helper.is_file():
        os.environ.setdefault("REG_FACTORY_BROWSER_HELPER", str(helper))


def _port_available(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
        return True
    except OSError:
        return False


def _runtime_version() -> str:
    root = Path(__file__).resolve().parents[1]
    if not getattr(sys, "frozen", False):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    roots = [
        Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)),
        Path(sys.executable).resolve().parent,
        root,
    ]
    for candidate in roots:
        try:
            version = (candidate / "VERSION").read_text(encoding="utf-8").strip()
            if version:
                return version
        except OSError:
            pass
    return "archive"


def _existing_reg_factory(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as response:
            payload = json.load(response)
        if isinstance(payload, dict) and "browser_provider" in payload and "running" in payload:
            return payload
    except Exception:
        pass
    return None


def _running_data_root(requested: int = 8799) -> Path | None:
    """Find a data-bearing root exposed by an already running local instance."""
    for port in range(requested, requested + 21):
        existing = _existing_reg_factory(port)
        if not existing:
            continue
        candidates = (existing.get("data_root"), existing.get("root"))
        for value in candidates:
            if not value:
                continue
            candidate = Path(str(value)).expanduser()
            if candidate.is_dir() and any(
                (candidate / marker).exists() for marker in ("emails.txt", "cookies", "tokens")
            ):
                return candidate.resolve()
    return None


def _portable_ancestor_data_root() -> Path | None:
    """Find an existing data root above an extracted portable package."""
    if not getattr(sys, "frozen", False):
        return None
    install_dir = Path(sys.executable).resolve().parent
    candidates = [install_dir, *list(install_dir.parents)[:3]]
    for candidate in candidates:
        if not (candidate / ".env").is_file():
            continue
        if any(
            (candidate / marker).exists()
            for marker in (".git", "emails.txt", "cookies", "tokens")
        ):
            return candidate.resolve()
    return None


def _adopt_running_data_root() -> None:
    if not getattr(sys, "frozen", False) or "REG_FACTORY_DATA_DIR" in os.environ:
        return
    candidate = _running_data_root()
    source = "现有实例"
    if candidate is None:
        candidate = _portable_ancestor_data_root()
        source = "便携包上级目录"
    if candidate is None:
        return
    os.environ["REG_FACTORY_DATA_DIR"] = str(candidate)
    env_path = candidate / ".env"
    if env_path.is_file() and "REG_FACTORY_ENV_FILE" not in os.environ:
        os.environ["REG_FACTORY_ENV_FILE"] = str(env_path)
    print(f"[reg-factory] 已沿用{source}的资产目录：{candidate}", flush=True)


def _open_when_ready(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    for _ in range(80):
        try:
            with urllib.request.urlopen(url, timeout=1):
                pass
            webbrowser.open(url)
            return
        except Exception:
            time.sleep(0.25)


def _select_port(host: str, requested: int, expected_version: str | None = None) -> tuple[int, bool]:
    expected_version = expected_version or _runtime_version()
    first_available = None
    older_versions = []
    for candidate in range(requested, requested + 21):
        if _port_available(host, candidate):
            if first_available is None:
                first_available = candidate
            continue
        existing = _existing_reg_factory(candidate)
        if not existing:
            continue
        running_version = str(existing.get("version") or "unknown")
        if running_version == expected_version:
            return candidate, True
        older_versions.append((candidate, running_version))
    if first_available is not None:
        if older_versions:
            occupied = ", ".join(f"{port}({version})" for port, version in older_versions)
            print(
                f"[reg-factory] 检测到其他版本 {occupied}；当前版本 {expected_version} "
                f"自动使用端口 {first_available}",
                flush=True,
            )
        elif first_available != requested:
            print(f"[reg-factory] 端口 {requested} 已占用，自动改用 {first_available}", flush=True)
        return first_available, False
    raise RuntimeError(f"端口 {requested}-{requested + 20} 均不可用")


def _pause_after_error() -> None:
    if not getattr(sys, "frozen", False) or _TASK_DISPATCH:
        return
    try:
        input("\n启动失败，请截图保存上面的错误。按回车键退出...")
    except (EOFError, KeyboardInterrupt):
        pass


def main() -> None:
    global _TASK_DISPATCH
    _adopt_running_data_root()
    _configure_frozen_runtime()
    raw_args = list(sys.argv[1:])
    # 解析统一走 task_dispatch：`--task` 后面漏写脚本名时返回 None，
    # 于是一路走到下面的 WebUI 启动分支，而不是把 "--task" 当脚本名报错。
    if task_dispatch.parse_task(raw_args) is not None:
        _TASK_DISPATCH = True
        task_dispatch.dispatch_task(raw_args)
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    version = _runtime_version()
    port, already_running = _select_port(args.host, args.port, version)
    url = f"http://127.0.0.1:{port}/"
    if already_running:
        print(f"[reg-factory] 当前版本 {version} 已在运行：{url}", flush=True)
        webbrowser.open(url)
        time.sleep(1)
        return
    print(f"[reg-factory] 正在启动：{url}", flush=True)
    threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
    uvicorn.run("webui.server:app", host=args.host, port=port, log_level="warning")


def _entrypoint() -> None:
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except BaseException as exc:
        print(f"\n[reg-factory] 启动失败：{exc}", file=sys.stderr, flush=True)
        _pause_after_error()
        raise


if __name__ == "__main__":
    _entrypoint()
