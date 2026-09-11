"""Atomic JSON write helpers with Windows-PermissionError retry.

Tied to FIX-PLAN §0.3: every JSON state file we own must survive concurrent
readers (AV scanners on Windows, BitBrowser tooling holding short locks) by
retrying with random tmp names instead of overwriting the live target.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import tempfile
import threading
import time
from typing import Any


def write_json_atomic(path: Any, value: Any, *, retries: int = 6,
                      retry_backoff: float = 0.05,
                      ensure_ascii: bool = False) -> None:
    """Atomically replace ``path`` with ``value`` as JSON.

    ``retries`` gives us room to recover from intermittent ``os.replace``
    ``PermissionError`` on Windows (the canonical AV/reader lock race). Each
    attempt uses a fresh random tmp filename under the same directory.
    """
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pid, tid = os.getpid(), threading.get_ident()
    last_err: Exception | None = None
    payload = json.dumps(value, indent=2, ensure_ascii=ensure_ascii)
    for attempt in range(max(1, int(retries))):
        suffix = f"{pid}-{tid}-{random.randint(0, 0xFFFF):04x}-{attempt}"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=target.name + ".",
            suffix=f".tmp-{suffix}",
            dir=str(target.parent),
            delete=False,
        ) as fh:
            tmp_path = fh.name
            try:
                fh.write(payload)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            except BaseException:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
        try:
            os.replace(tmp_path, str(target))
            return
        except PermissionError as e:  # Windows short reader lock race
            last_err = e
            time.sleep(retry_backoff * (2 ** attempt))
            continue
        except OSError:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
    try:
        os.remove(tmp_path)  # type: ignore[name-defined]
    except (OSError, NameError):
        pass
    raise last_err or OSError("atomic write failed")


def read_json(path: Any, default: Any = None) -> Any:
    """Return JSON-decoded contents of ``path`` or ``default`` if missing."""
    target = pathlib.Path(path)
    if not target.is_file():
        return default
    try:
        with open(target, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def backup_to(target: Any, backup: Any) -> None:
    """Copy ``target`` to ``backup`` before mutation when ``target`` exists."""
    if not target:
        return
    src = pathlib.Path(target)
    if not src.is_file():
        return
    dst = pathlib.Path(backup)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        import shutil

        shutil.copy2(src, dst)
    except OSError:
        pass
