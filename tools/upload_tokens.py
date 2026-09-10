# -*- coding: utf-8 -*-
"""
tools/upload_tokens.py — 把本地标准 token 批量上传到下游接口（CPA / SUB2API / webchat2api）。

注册脚本只负责把 token 落到 tokens/ 目录；上传由本脚本单独触发。

用法:
    python tools/upload_tokens.py                # all（chatgpt + grok）
    python tools/upload_tokens.py chatgpt        # 只传 ChatGPT（CPA + SUB2API）
    python tools/upload_tokens.py grok           # 只传 Grok（SUB2API + webchat2api）
    python tools/upload_tokens.py grok --force   # 强制重导 Grok 到 SUB2API，修复已有账号凭据

幂等: 上传成功的 email 会记到 tokens/<platform>/uploaded_<target>.txt，下次跳过。
配置缺失的 target 自动跳过（不报错）。
"""

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

from config import (
    TOKEN_OUTPUT_DIR,
    CPA_URL, CPA_MGMT_KEY,
    SUB2API_URL, SUB2API_EMAIL, SUB2API_PASSWORD, SUB2API_GROUP,
    SUB2API_GROK_GROUP, SUB2API_GROK_PROXY_ID,
    WEBCHAT2API_URL, WEBCHAT2API_KEY,
)
from common.session_export import build_cpa_codex_json, build_sub2api_content, sub2api_expires_at
from common import uploaders
from common.token_upload_state import mark_uploaded, uploaded_set


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def upload_chatgpt():
    """从已存网页 session 批量灌 CPA / SUB2API（Path A，兜底用）。

    ⚠️ 网页 session 无 refresh_token，CPA 用合成 id_token，下游过期后无法续期。
    Codex 进 SUB2API/CPA 的**正路是 oauth_codex.py（Path B，带真 refresh_token）**；
    本路径仅用于没走 OAuth 的批量兜底。
    """
    files = sorted(glob.glob(os.path.join(TOKEN_OUTPUT_DIR, "chatgpt", "*.session.json")))
    if not files:
        print("[chatgpt] 无 *.session.json，跳过")
        return

    cpa_on = bool(CPA_URL and CPA_MGMT_KEY)
    sub_on = bool(SUB2API_URL and SUB2API_EMAIL and SUB2API_PASSWORD)
    if not cpa_on:
        print("[chatgpt] CPA 未配置（CPA_URL/CPA_MGMT_KEY），跳过 CPA 上传")
    if not sub_on:
        print("[chatgpt] SUB2API 未配置（SUB2API_URL/EMAIL/PASSWORD），跳过 SUB2API 上传")
    if not cpa_on and not sub_on:
        return

    cpa_done = uploaded_set("chatgpt", "cpa")
    sub_done = uploaded_set("chatgpt", "sub2api")

    for path in files:
        name = os.path.basename(path)[: -len(".session.json")]
        try:
            session = _read_json(path)
        except Exception as e:
            print(f"[chatgpt] {name} 读取失败: {e}")
            continue
        email = session.get("user", {}).get("email") or name

        if cpa_on and email not in cpa_done:
            try:
                cpa = build_cpa_codex_json(session, email=email)
                ok, msg = uploaders.upload_cpa(CPA_URL, CPA_MGMT_KEY, cpa["auth_json"], cpa["file_name"])
            except Exception as e:
                ok, msg = False, str(e)
            print(f"[chatgpt][CPA] {email}: {'OK' if ok else 'FAIL'} - {msg}")
            if ok:
                mark_uploaded("chatgpt", "cpa", email)

        if sub_on and email not in sub_done:
            try:
                content = build_sub2api_content(session)
                ok, msg = uploaders.upload_sub2api(
                    SUB2API_URL, SUB2API_EMAIL, SUB2API_PASSWORD, SUB2API_GROUP,
                    content, expires_at=sub2api_expires_at(session),
                )
            except Exception as e:
                ok, msg = False, str(e)
            print(f"[chatgpt][SUB2API] {email}: {'OK' if ok else 'FAIL'} - {msg}")
            if ok:
                mark_uploaded("chatgpt", "sub2api", email)


def upload_grok(force_sub2api=False):
    files = sorted(glob.glob(os.path.join(TOKEN_OUTPUT_DIR, "grok", "*.sso.json")))
    if not files:
        print("[grok] 无 *.sso.json，跳过")
        return
    sub_on = bool(SUB2API_URL and SUB2API_EMAIL and SUB2API_PASSWORD)
    webchat_on = bool(WEBCHAT2API_URL and WEBCHAT2API_KEY)
    if not sub_on:
        print("[grok] SUB2API 未配置（SUB2API_URL/EMAIL/PASSWORD），跳过 SUB2API 上传")
    if not webchat_on:
        print("[grok] webchat2api 未配置（WEBCHAT2API_URL/KEY），跳过")
    if not sub_on and not webchat_on:
        return

    sub_done = uploaded_set("grok", "sub2api")
    webchat_done = uploaded_set("grok", "webchat2api")
    for path in files:
        try:
            data = _read_json(path)
        except Exception as e:
            print(f"[grok] {os.path.basename(path)} 读取失败: {e}")
            continue
        email = data.get("email") or os.path.basename(path)[: -len(".sso.json")]
        sso = data.get("sso")
        if sub_on and (force_sub2api or email not in sub_done):
            ok, msg = uploaders.upload_sub2api_grok(
                SUB2API_URL,
                SUB2API_EMAIL,
                SUB2API_PASSWORD,
                SUB2API_GROK_GROUP,
                sso,
                account_email=email,
                proxy_id=SUB2API_GROK_PROXY_ID,
                local_proxy=os.environ.get("CLASH_PROXY", "http://127.0.0.1:7897"),
            )
            print(f"[grok][SUB2API] {email}: {'OK' if ok else 'FAIL'} - {msg}")
            if ok:
                mark_uploaded("grok", "sub2api", email)

        if webchat_on and email not in webchat_done:
            ok, msg = uploaders.upload_webchat2api(WEBCHAT2API_URL, WEBCHAT2API_KEY, sso)
            print(f"[grok][webchat2api] {email}: {'OK' if ok else 'FAIL'} - {msg}")
            if ok:
                mark_uploaded("grok", "webchat2api", email)


def main():
    args = sys.argv[1:]
    if any(arg in ("-h", "--help", "help") for arg in args):
        print(__doc__)
        return
    unknown_options = [arg for arg in args if arg.startswith("-") and arg != "--force"]
    if unknown_options:
        print(f"未知参数: {unknown_options[0]}")
        sys.exit(1)
    targets = [arg for arg in args if not arg.startswith("-")]
    if len(targets) > 1:
        print("只能指定一个目标（all|chatgpt|grok）")
        sys.exit(1)
    target = targets[0].lower() if targets else "all"
    force_sub2api = "--force" in args
    if target not in ("all", "chatgpt", "grok"):
        print(f"未知目标: {target}（可选 all|chatgpt|grok）")
        sys.exit(1)
    if target in ("all", "chatgpt"):
        upload_chatgpt()
    if target in ("all", "grok"):
        upload_grok(force_sub2api=force_sub2api)


if __name__ == "__main__":
    main()
