# -*- coding: utf-8 -*-
"""Small cross-process run-state helpers for local Gmail runs."""

from __future__ import annotations

import contextlib
import json
import os
import time


_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runstate")
STALE_SECONDS = int(os.environ.get("RUNSTATE_STALE_SECONDS", "1800"))


def _ensure_dir() -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)


@contextlib.contextmanager
def file_lock(name: str, timeout: float = 30.0, poll: float = 0.2):
    _ensure_dir()
    lock_path = os.path.join(_STATE_DIR, f"{name}.lock")
    deadline = time.time() + timeout
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
                if age > timeout:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(lock_path)
                    continue
            except FileNotFoundError:
                continue
            if time.time() > deadline:
                raise TimeoutError(f"could not acquire lock {name} in {timeout}s")
            time.sleep(poll)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.remove(lock_path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == still_active
    except Exception:
        return True


def _active_path(instance: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in instance)
    return os.path.join(_STATE_DIR, f"active-{safe}.json")


def _list_active() -> list[tuple[str, dict]]:
    _ensure_dir()
    active = []
    for filename in os.listdir(_STATE_DIR):
        if not (filename.startswith("active-") and filename.endswith(".json")):
            continue
        path = os.path.join(_STATE_DIR, filename)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            stale = (time.time() - os.path.getmtime(path)) > STALE_SECONDS
            if stale or not _pid_alive(int(data.get("pid", 0))):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(path)
                continue
            active.append((path, data))
        except (json.JSONDecodeError, FileNotFoundError, OSError, ValueError):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
    return active


def begin_task(instance: str) -> bool:
    """Mark this instance active and return True if no other instance is active."""
    with file_lock("runstate"):
        others = [data for _, data in _list_active() if data.get("instance") != instance]
        with open(_active_path(instance), "w", encoding="utf-8") as handle:
            json.dump({"instance": instance, "pid": os.getpid(), "ts": time.time()}, handle)
        return not others


def end_task(instance: str) -> None:
    with contextlib.suppress(Exception):
        with file_lock("runstate"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(_active_path(instance))


def active_count() -> int:
    return len(_list_active())
