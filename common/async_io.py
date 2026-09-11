"""Async helpers for blocking IO wrapped behind ``to_thread`` / ``to_thread_sleep``.

Concrete: see FIX-PLAN §0.2. The whole project must funnel blocking IO that runs
inside an ``async def`` coroutine through ``to_thread`` so the event loop is
never blocked by sync HTTP, sync DB calls, or long ``time.sleep`` waits.
"""
from __future__ import annotations

import asyncio
import functools
import time
from typing import Callable, TypeVar

T = TypeVar("T")


async def to_thread(func: Callable[..., T], /, *args, **kwargs) -> T:
    """Run a sync callable in the default executor.

    This is the only sanctioned wrapper for ``asyncio.to_thread`` across the
    codebase; raw ``asyncio.to_thread(...)`` literals are forbidden by review.
    """
    return await asyncio.to_thread(functools.partial(func, *args, **kwargs))


async def to_thread_sleep(seconds: float) -> None:
    """Async-friendly ``time.sleep`` replacement.

    Unlike ``time.sleep`` this yields back to the event loop and supports
    cancellation. ``seconds`` may be 0 to fast-yield once.
    """
    if seconds <= 0:
        await asyncio.sleep(0)
        return
    await asyncio.sleep(seconds)


def poll_until(condition: Callable[[], bool], interval: float,
               timeout: float) -> bool:
    """Poll ``condition`` every ``interval`` seconds until True or ``timeout``.

    Must be wrapped in ``to_thread`` when called from async code so the loop is
    not blocked. ``condition`` is invoked in the worker thread.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    interval = max(0.0, float(interval))
    while time.monotonic() < deadline:
        try:
            if condition():
                return True
        except Exception:
            pass
        if interval:
            time.sleep(interval)
    try:
        return bool(condition())
    except Exception:
        return False
