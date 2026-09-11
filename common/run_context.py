"""Per-run context container.

Mutable per-attempt state must live in ContextVars, not module globals; see
FIX-PLAN T12. ``contextvars.ContextVar`` is bound to ``asyncio.Task`` context,
so concurrency --concurrency>1 cannot accidentally share state across workers.
"""
from __future__ import annotations

from contextvars import ContextVar

# MANUAL_VERIFY_RETAIN_WINDOW: replacement for the global flag in
# register_outlook_standalone.py — T12. ``False`` means delete the profile
# during cleanup; ``True`` means keep the BitBrowser profile alive for the
# human to inspect.
_MANUAL_RETAIN: ContextVar[bool] = ContextVar(
    "reg_factory_manual_retain_window", default=False
)


def get_manual_retain() -> bool:
    """Return the current per-attempt ``MANUAL_VERIFY_RETAIN_WINDOW`` value."""
    return bool(_MANUAL_RETAIN.get())


def set_manual_retain(value: bool) -> object:
    """Set ``MANUAL_VERIFY_RETAIN_WINDOW`` for this attempt; returns Token."""
    return _MANUAL_RETAIN.set(bool(value))


def reset_manual_retain(token: object) -> None:
    """Restore ``MANUAL_VERIFY_RETAIN_WINDOW`` to its prior value (Token based)."""
    try:
        _MANUAL_RETAIN.reset(token)  # type: ignore[arg-type]
    except Exception:
        # Token might belong to a different asyncio.Task that already exited;
        # fall back to default reset via re-assignment.
        _MANUAL_RETAIN.set(False)
