# -*- coding: utf-8 -*-
"""
tools/export_chatgpt2api.py — 把注册落下的普通 ChatGPT 网页号聚合成 chatgpt2api
(basketikun/chatgpt2api)的一键批量导入格式。

数据源:tokens/chatgpt/c2a-*.json(注册成功时由 session_export.save_chatgpt_tokens 落盘)。
若 c2a-*.json 缺失,回退到 *.session.json 现算(build_chatgpt2api_account)。

用法:
    python tools/export_chatgpt2api.py
        -> 生成 tokens/chatgpt/chatgpt2api-tokens.txt(一行一个 access_token),
           粘进 chatgpt2api 的 access_token 批量导入框。

    python tools/export_chatgpt2api.py --json
        -> 生成 chatgpt2api-import.json({accounts:[...]}),走 /api/accounts 导入。

    python tools/export_chatgpt2api.py --post https://your-host --key <ADMIN_KEY>
        -> 直接 POST {accounts:[...]} 到 <host>/api/accounts 一键批量导入。

对端口径(已核对 chatgpt2api 源码):
    - POST /api/accounts,body {"accounts":[{...}]},Authorization: Bearer <admin key>。
    - 每个对象只有 access_token 必需;普通号**不要带 type:"codex"**(会被当 codex 源)。
    - 重复 access_token 对端按 skipped 处理,幂等。
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from config import TOKEN_OUTPUT_DIR
except Exception:
    TOKEN_OUTPUT_DIR = "tokens"

from common.session_export import build_chatgpt2api_account


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def collect_accounts():
    """聚合普通号导入对象,按 access_token 去重。返回 list[dict]。"""
    cdir = os.path.join(TOKEN_OUTPUT_DIR, "chatgpt")
    by_token = {}

    # 1) 优先用注册时落下的 c2a-*.json
    for path in sorted(glob.glob(os.path.join(cdir, "c2a-*.json"))):
        try:
            item = _read_json(path)
            tok = str(item.get("access_token") or "").strip()
            if tok:
                by_token[tok] = item
        except Exception as e:
            print(f"  [skip] {os.path.basename(path)}: {e}")

    # 2) 回退:没有对应 c2a 的 session 文件现算
    for path in sorted(glob.glob(os.path.join(cdir, "*.session.json"))):
        try:
            sess = _read_json(path)
            name = os.path.basename(path)[: -len(".session.json")]
            item = build_chatgpt2api_account(sess, email=name)
            tok = str(item.get("access_token") or "").strip()
            if tok and tok not in by_token:
                by_token[tok] = item
        except Exception as e:
            print(f"  [skip] {os.path.basename(path)}: {e}")

    return list(by_token.values())


def post_accounts(host, key, accounts):
    """直接 POST 到 chatgpt2api。需要 requests。"""
    import requests

    url = host.rstrip("/") + "/api/accounts"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"accounts": accounts},
        timeout=120,
    )
    print(f"[post] {url} -> HTTP {resp.status_code}")
    try:
        data = resp.json()
        print(f"  added={data.get('added')} skipped={data.get('skipped')} "
              f"refreshed={data.get('refreshed')}")
        if data.get("errors"):
            print(f"  errors: {data['errors']}")
    except Exception:
        print(f"  body: {resp.text[:500]}")
    resp.raise_for_status()


def import_accounts(host, key, accounts):
    """程序内调用的导入：POST {accounts:[...]} 到 <host>/api/accounts。
    与 post_accounts 不同——不抛异常、不打印，返回 (ok: bool, summary: str)，
    供 register_chatgpt.py 注册成功后逐个上传时用（单号失败不应中断注册流程）。
    出口代理偶发 TLS/连接抖动(Max retries exceeded)，对连接类异常退避重试几次(对齐 _sub2api_request)。"""
    import time
    import requests

    if not (host and key):
        return False, "CHATGPT2API_URL/KEY 未配置"
    if not accounts:
        return False, "无 account 可导入"
    url = host.rstrip("/") + "/api/accounts"
    last_err = ""
    for attempt in range(4):
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"accounts": accounts},
                timeout=120,
            )
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = str(e)[:100]
            if attempt < 3:
                wait = 2 + attempt * 2
                print(f"  [c2a] 连接抖动重试 {attempt+1}/4（{wait}s 后）: {last_err[:60]}")
                time.sleep(wait)
                continue
            return False, f"请求失败(重试用尽): {last_err}"
        except Exception as e:
            return False, f"请求失败: {str(e)[:100]}"
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    try:
        data = resp.json()
        summary = (f"added={data.get('added')} skipped={data.get('skipped')} "
                   f"refreshed={data.get('refreshed')}")
        if data.get("errors"):
            summary += f" errors={data['errors']}"
        return True, summary
    except Exception:
        return True, f"HTTP {resp.status_code}: {resp.text[:120]}"


def main():
    ap = argparse.ArgumentParser(description="导出/上传 chatgpt2api 普通号 token")
    ap.add_argument("--post", metavar="HOST", help="直接 POST 到 chatgpt2api host(如 http://1.2.3.4:8000)")
    ap.add_argument("--key", help="chatgpt2api admin key(配合 --post)")
    ap.add_argument("--json", action="store_true", help="输出 {accounts:[...]} JSON(默认输出一行一个 access_token 的 txt)")
    ap.add_argument("-o", "--out", help="输出文件路径(默认 tokens/chatgpt/chatgpt2api-tokens.txt)")
    args = ap.parse_args()

    accounts = collect_accounts()
    if not accounts:
        print("[chatgpt2api] 无可导出的普通号(tokens/chatgpt/ 下没有 c2a-*.json / *.session.json)")
        return

    print(f"[chatgpt2api] 聚合到 {len(accounts)} 个普通号")

    if args.post:
        if not args.key:
            print("[error] --post 需要同时提供 --key <admin key>")
            sys.exit(2)
        post_accounts(args.post, args.key, accounts)
        return

    out = args.out or os.path.join(
        TOKEN_OUTPUT_DIR, "chatgpt",
        "chatgpt2api-import.json" if args.json else "chatgpt2api-tokens.txt",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if args.json:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"accounts": accounts}, f, ensure_ascii=False, indent=2)
        print(f"[chatgpt2api] 已写出 {out}({len(accounts)} 个 accounts 对象)")
        print("  导入: POST 此文件到 <host>/api/accounts (Authorization: Bearer <admin key>)")
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(a["access_token"] for a in accounts) + "\n")
        print(f"[chatgpt2api] 已写出 {out}(一行一个 access_token,共 {len(accounts)} 个)")
        print("  导入: 粘贴进 chatgpt2api 的 access_token 批量导入框")
    print(f"  或直接上传: python tools/export_chatgpt2api.py --post <host> --key <admin key>")


if __name__ == "__main__":
    main()
