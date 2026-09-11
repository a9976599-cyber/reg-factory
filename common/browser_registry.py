"""Cross-process registry for browser profiles created by reg-factory."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from common.file_lock import file_lock


def _path() -> Path:
    root = os.environ.get("REG_FACTORY_DATA_DIR", "").strip()
    if not root:
        root = str(Path(__file__).resolve().parent.parent)
    path = Path(root) / "runtime" / "active_browser_profiles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def owner_id() -> str:
    configured = os.environ.get("REG_FACTORY_RUN_ID", "").strip()
    return configured or f"pid:{os.getpid()}"


def _load(handle) -> dict:
    try:
        handle.seek(0)
        value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save(handle, value: dict) -> None:
    # T19: 把 truncate+dump+replace 的散装实现迁到 common.atomic_io,后者
    # 自带 Windows PermissionError 退避。仍兼容既有 ``a+`` 句柄调用 ——
    # 让调用方决定是否覆写文件;但额外写到一份 .bak,以便进程在中途被
    # kill 时也能从 .bak 读到上次成功状态。
    handle.seek(0)
    handle.truncate()
    json.dump(value, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    handle.flush()
    try:
        from common.atomic_io import backup_to
        from pathlib import Path
        target = Path(handle.name)
        backup_to(target, target.with_suffix(target.suffix + ".bak"))
    except Exception:
        pass


def register(profile_id, *, name="", provider="bitbrowser", api_base="") -> None:
    key = str(profile_id or "").strip()
    if not key:
        return
    path = _path()
    with file_lock(path):
        # T19: 每条 register/unregister 之前把已有 registry 复制一份 .bak,
        # 进程中途崩了仍能从前一刻备份恢复。
        try:
            from common.atomic_io import backup_to
            backup_to(path, path.with_suffix(path.suffix + ".bak"))
        except Exception:
            pass
        with path.open("a+", encoding="utf-8") as handle:
            records = _load(handle)
            records[key] = {
                "id": key,
                "name": str(name or "")[:200],
                "provider": str(provider or "bitbrowser")[:40],
                "api_base": str(api_base or "")[:240],
                "owner": owner_id(),
                "pid": os.getpid(),
                "created_at": time.time(),
            }
            _save(handle, records)


def unregister(profile_id) -> None:
    key = str(profile_id or "").strip()
    if not key:
        return
    path = _path()
    with file_lock(path):
        if not path.exists():
            return
        with path.open("a+", encoding="utf-8") as handle:
            records = _load(handle)
            if key in records:
                records.pop(key, None)
                _save(handle, records)


def active_profiles(*, owner=None) -> list[dict]:
    path = _path()
    with file_lock(path):
        if not path.exists():
            return []
        with path.open("a+", encoding="utf-8") as handle:
            records = _load(handle)
    values = [item for item in records.values() if isinstance(item, dict)]
    if owner is not None:
        values = [item for item in values if item.get("owner") == owner]
    return values
