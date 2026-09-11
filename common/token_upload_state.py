# -*- coding: utf-8 -*-
"""本地 token 上传幂等标记。"""

import os

try:
    from config import TOKEN_OUTPUT_DIR
except Exception:
    TOKEN_OUTPUT_DIR = "tokens"

from common.file_lock import file_lock
from common.atomic_io import write_json_atomic


def uploaded_set(platform, target):
    path = os.path.join(TOKEN_OUTPUT_DIR, platform, f"uploaded_{target}.txt")
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def mark_uploaded(platform, target, key):
    """T20: 读-判-写在 file_lock 串行化,避免并发调 mark_uploaded 给同 key 追加两次。

    file_lock 的代价小,而写冲突在多进程 worker 并发上传 token 时真实存在:
    重复行会让远端「上传成功」计数翻倍。
    """
    key = str(key or "").strip()
    if not key:
        return
    pdir = os.path.join(TOKEN_OUTPUT_DIR, platform)
    os.makedirs(pdir, exist_ok=True)
    path = os.path.join(pdir, f"uploaded_{target}.txt")
    with file_lock(path):
        existing = uploaded_set(platform, target)
        if key in existing:
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}\n")
