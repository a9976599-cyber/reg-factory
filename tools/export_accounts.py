# -*- coding: utf-8 -*-
"""
导出已注册账号的 cookie 为 Chrome 扩展可用的 accounts.json

扫描 cookies/ 下的 full_*.json（Claude）以及 cookies/<platform>/full_*.json
（chatgpt / grok），转换成扩展直登用的统一格式。

用法:
    python tools/export_accounts.py                # 导出全部平台
    python tools/export_accounts.py claude chatgpt # 只导出指定平台
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

COOKIE_DIR = "cookies"
OUT_FILE = os.path.join("login_extension", "accounts.json")

# 每个平台：cookie 所在目录、直登 URL、用于判断"已登录"的关键 cookie 名、
# 只保留这些域的 cookie（None=全保留）
PLATFORMS = {
    "claude": {
        "dir": COOKIE_DIR,                       # Claude 直接在 cookies/ 根目录
        "url": "https://claude.ai/new",
        "key_cookies": ["sessionKey"],
        "domains": [".claude.ai", "claude.ai"],
    },
    "chatgpt": {
        "dir": os.path.join(COOKIE_DIR, "chatgpt"),
        "url": "https://chatgpt.com/",
        "key_cookies": ["__Secure-next-auth.session-token"],
        "domains": [".chatgpt.com", "chatgpt.com", ".openai.com", "auth.openai.com"],
    },
    "grok": {
        "dir": os.path.join(COOKIE_DIR, "grok"),
        "url": "https://grok.com/",
        "key_cookies": ["sso", "sso-rw", "__Secure-next-auth.session-token"],
        "domains": [".grok.com", "grok.com", ".x.ai", "accounts.x.ai"],
    },
}

# Playwright sameSite -> chrome.cookies API sameSite
SAMESITE_MAP = {
    "None": "no_restriction",
    "Lax": "lax",
    "Strict": "strict",
    "no_restriction": "no_restriction",
    "lax": "lax",
    "strict": "strict",
}


def convert_cookie(c):
    """Playwright cookie -> chrome.cookies.set 兼容格式"""
    same = SAMESITE_MAP.get(c.get("sameSite"), "lax")
    secure = bool(c.get("secure", False))
    # chrome 要求 no_restriction 必须 secure
    if same == "no_restriction":
        secure = True
    out = {
        "name": c.get("name"),
        "value": c.get("value"),
        "domain": c.get("domain"),
        "path": c.get("path", "/"),
        "secure": secure,
        "httpOnly": bool(c.get("httpOnly", False)),
        "sameSite": same,
    }
    # expires(秒) -> expirationDate;会话 cookie(无 expires 或 -1)则省略
    exp = c.get("expires", c.get("expirationDate"))
    if exp is not None and exp != -1:
        out["expirationDate"] = float(exp)
    return out


def domain_match(cookie_domain, allowed):
    if allowed is None:
        return True
    cd = (cookie_domain or "").lstrip(".")
    for a in allowed:
        a = a.lstrip(".")
        if cd == a or cd.endswith("." + a):
            return True
    return False


def find_email_for_file(full_path, accounts_lines):
    """尽量从同目录 accounts.txt 反查这份 cookie 对应的邮箱。
    full_*.json 文件名里带的是 profile_id，accounts.txt 里没有 profile_id，
    所以无法精确对应——这里只在单账号时退化处理，多账号用文件名兜底。"""
    base = os.path.basename(full_path)
    # full_<profileid>_<ts>.json -> 取时间戳做 label
    return base.replace("full_", "").replace(".json", "")


def load_platform(name, cfg):
    pdir = cfg["dir"]
    if not os.path.isdir(pdir):
        return []
    full_files = sorted(glob.glob(os.path.join(pdir, "full_*.json")))
    # 读 accounts.txt 拿 email|pass|key 顺序列表，用于配对
    acc_file = os.path.join(pdir, "accounts.txt")
    acc_entries = []
    if os.path.exists(acc_file):
        with open(acc_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "|" in line:
                    parts = line.split("|")
                    acc_entries.append({"email": parts[0], "key": parts[2] if len(parts) > 2 else ""})

    results = []
    seen_keys = set()  # 按关键 cookie 值去重
    for fp in full_files:
        try:
            cookies = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            print(f"  skip {fp}: {e}")
            continue
        # 过滤域 + 转换
        conv = [convert_cookie(c) for c in cookies if domain_match(c.get("domain"), cfg["domains"])]
        # 找关键 cookie 值
        key_val = None
        for c in conv:
            if c["name"] in cfg["key_cookies"]:
                key_val = c["value"]
                break
        if not key_val:
            continue  # 没有登录态，跳过
        if key_val in seen_keys:
            continue
        seen_keys.add(key_val)
        # 按 key 反查邮箱
        email = next((a["email"] for a in acc_entries if a["key"] and a["key"] == key_val), None)
        if not email:
            email = find_email_for_file(fp, acc_entries)
        results.append({
            "platform": name,
            "email": email,
            "label": email,
            "url": cfg["url"],
            "cookies": conv,
        })
    return results


def _output_path(value, default):
    path = Path(value or default)
    if not path.is_absolute():
        data_root = Path(os.environ.get("REG_FACTORY_DATA_DIR") or Path.cwd())
        path = data_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main():
    parser = argparse.ArgumentParser(description="Export registered platform cookies")
    parser.add_argument("platforms", nargs="*", choices=sorted(PLATFORMS), help=argparse.SUPPRESS)
    parser.add_argument(
        "--platform", choices=("all", *PLATFORMS), default="all",
        help="Platform to export",
    )
    parser.add_argument(
        "--format", choices=("accounts", "cookies"), default="accounts",
        help="accounts=extension account bundle; cookies=standard browser cookie array",
    )
    parser.add_argument(
        "--index", type=int, default=-1,
        help="Account index; -1 exports all account bundles or the first cookie record",
    )
    parser.add_argument("--out", default="", help="Output JSON path")
    args = parser.parse_args()

    if args.format == "cookies":
        if args.platform == "all" or args.platforms:
            parser.error("--format cookies requires one --platform")
        from common import asset_store

        selected = max(0, args.index)
        result = asset_store.get_platform_asset(args.platform, "cookies", index=selected)
        path = _output_path(
            args.out,
            f"cookie_exports/{args.platform}_cookies_{selected}.json",
        )
        path.write_text(json.dumps(result["data"], indent=2, ensure_ascii=False), encoding="utf-8")
        print(
            f"  exported {len(result['data'])} standard cookies "
            f"for {args.platform}[{selected}] -> {path}"
        )
        return 0

    targets = args.platforms or (
        list(PLATFORMS.keys()) if args.platform == "all" else [args.platform]
    )
    all_accounts = []
    for name in targets:
        if name not in PLATFORMS:
            print(f"unknown platform: {name}")
            continue
        accs = load_platform(name, PLATFORMS[name])
        print(f"  {name}: {len(accs)} accounts")
        if args.index >= 0:
            accs = accs[args.index:args.index + 1]
        all_accounts.extend(accs)

    path = _output_path(args.out, OUT_FILE)
    path.write_text(json.dumps(all_accounts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  exported {len(all_accounts)} accounts -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
