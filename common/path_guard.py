"""Path escape prevention.

Single source of truth for ``safe_join_under`` so task_dispatch and any wrapper
entry can ``from common.path_guard import safe_join_under`` instead of keeping
their own copies (FIX-PLAN §0.4).
"""
from __future__ import annotations

import os


def safe_join_under(base: str, child: str) -> str:
    """Return ``abs(child)`` only when it lies under abs(base).

    Trailing separator is appended for prefix comparison so a sibling
    ``/etc/foo`` cannot be considered under ``/etc/fo``. Raises ``ValueError``
    on traversal.
    """
    base_abs = os.path.realpath(os.path.normcase(base))
    child_abs = os.path.realpath(os.path.normcase(child))
    sep = os.sep
    prefix = base_abs + sep
    if not (child_abs + sep).startswith(prefix):
        raise ValueError(
            f"path escape: {child_abs!r} is not under {base_abs!r}"
        )
    return child_abs
