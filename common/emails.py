# -*- coding: utf-8 -*-
"""
common/emails.py — 邮箱供给（平台独立的占用记录）

读取 emails.txt（email----password----refresh_token----client_id），
每个平台用独立的 emails_used_<platform>.txt 记录已占用，互不干扰。
线程安全。
"""

import os
import sys
import threading

from common.file_lock import append_line, file_lock

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# T22: 把账本路径构造函数化:_email_ledger_path 既读 REG_FACTORY_DATA_DIR
# 也兼容老的 cwd/.reg_factory 探测。后向兼容已有调用方。
def _email_ledger_path():
    """Resolve the on-disk path of the email ledger.

    Order:
    1. ``$REG_FACTORY_DATA_DIR/emails.txt`` (preferred for portable/frozen)
    2. ``$REG_FACTORY_DATA_DIR/emails.txt`` 即使未设置也用 cwd 兜底,避免
       仓库根与 data 目录被算成两个独立账本。
    3. ``cwd/emails.txt`` 回退。
    """
    data_dir = os.environ.get("REG_FACTORY_DATA_DIR", "").strip()
    candidate = (
        os.path.join(data_dir, "emails.txt") if data_dir else "emails.txt"
    )
    if os.path.isfile(candidate):
        return candidate
    fallback = os.path.join(os.getcwd(), "emails.txt")
    if os.path.isfile(fallback):
        return fallback
    # 不存在时返回主候选;append_line 仍会自动建出目录。
    return candidate


EMAILS_FILE = os.environ.get("REG_FACTORY_EMAILS_FILE", "").strip() or _email_ledger_path()
_lock = threading.Lock()

_VALIDATION_TIMEOUT_S = float(
    os.environ.get("REG_FACTORY_EMAIL_VALIDATE_TIMEOUT", "20")
)


def _pre_validate_emails(candidates, platform, validate_token=False):
    """T14: 在 file_lock 之外运行 ``check_mailbox_access``(网络校验)。

    输入是 [(email, password, token, client_id), ...] 列表,返回同样长度
    的 ``validations`` 列表(各项: {ok, permanent, reason, folder_status})。
    加总时长上限 ``_VALIDATION_TIMEOUT_S`` 以避免坏出口卡死。
    """
    if not validate_token or not candidates:
        return [None] * len(candidates)

    from common.mailbox import check_mailbox_access

    deadline = __import__("time").monotonic() + max(1.0, _VALIDATION_TIMEOUT_S)

    def _bounded_check(email_token_cid):
        email, password, token, client_id = email_token_cid
        if not token:
            return {
                "ok": False,
                "permanent": True,
                "reason": "missing_refresh_token",
            }
        try:
            return check_mailbox_access(email, token, client_id)
        except Exception as exc:
            return {
                "ok": False,
                "permanent": False,
                "reason": f"validation_exception:{type(exc).__name__}",
            }

    out = []
    for entry in candidates:
        if __import__("time").monotonic() >= deadline:
            out.append({
                "ok": False,
                "permanent": False,
                "reason": "validation_timeout",
            })
            continue
        out.append(_bounded_check(entry))
    return out

_CHATGPT_RETRYABLE_ERROR_MARKERS = (
    "goto_failed",
    "auth_assets_failed",
    "cf_blocked",
    "email_verification_not_completed",
    "email_verification_",
    "email_submit_stuck",
    "entry_",
)


def _used_file(platform):
    return f"emails_used_{platform}.txt"


def _error_file(platform):
    return f"emails_error_{platform}.txt"


def _retry_claim_status():
    run_id = os.environ.get("REG_FACTORY_RUN_ID", "").strip()
    return f"retrying:{run_id or f'pid:{os.getpid()}'}".lower()


def _outlook_sale_file():
    root = os.environ.get("REG_FACTORY_DATA_DIR", "").strip() or "."
    return os.path.join(root, "runtime", "state", "outlook_sale_emails.txt")


def _outlook_registration_file():
    root = os.environ.get("REG_FACTORY_DATA_DIR", "").strip() or "."
    return os.path.join(root, "runtime", "state", "outlook_registration_emails.txt")


def _exclude_from_outlook_sale(platform, email):
    if str(platform or "").strip().lower() in {"", "email", "outlook"}:
        return
    append_line(_outlook_registration_file(), str(email or "").strip().lower())


def mark_registration_started(platform, email, password=""):
    """Permanently reserve an explicit mailbox for a platform registration."""
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform in {"", "email", "outlook"}:
        return
    _exclude_from_outlook_sale(normalized_platform, email)
    append_line(_used_file(normalized_platform), f"{email}----{password}----reserved")


def _load_used(platform):
    used = set()
    # A sale claim may be followed by registration. Registration reservations
    # remain one-way exclusions so the same mailbox cannot be sold afterward.
    for fp in [
        _used_file(platform),
        _error_file(platform),
        _outlook_registration_file(),
    ]:
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        used.add(line.split("----")[0].strip().lower())
    return used


def next_email(platform):
    """取下一个未被该平台占用的邮箱，返回 (email, password, refresh_token, client_id) 或 None。
    取出即标记 reserved，防止并发重复。"""
    with _lock, file_lock(f"{EMAILS_FILE}.{platform}.reserve"):
        if not os.path.exists(EMAILS_FILE):
            print(f"  [email] {EMAILS_FILE} not found")
            return None
        used = _load_used(platform)
        with open(EMAILS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("----")
                email = parts[0].strip()
                if email.lower() in used:
                    continue
                password = parts[1].strip() if len(parts) >= 2 else ""
                token = parts[2].strip() if len(parts) >= 3 else ""
                client_id = parts[3].strip() if len(parts) >= 4 else ""
                append_line(_used_file(platform), f"{email}----{password}----reserved")
                _exclude_from_outlook_sale(platform, email)
                print(f"  [email] picked for {platform}: {email}")
                return email, password, token, client_id
        print(f"  [email] no unused emails left for {platform}")
        return None


def latest_email(platform, require_token=False, validate_token=False):
    """Reserve the newest unused mailbox, optionally requiring a working Graph RT."""
    # T14: 把 ``check_mailbox_access``(网络 IO)从 file_lock 块里挪出去,
    # 否则并发 8 个 worker 调 ``validate_token=True`` 时,前一个慢校验会把
    # file_lock 持到 30s timeout,触发邮箱供给彻底卡住。
    used = _load_used(platform)
    rows = []
    if not os.path.exists(EMAILS_FILE):
        print(f"  [email] {EMAILS_FILE} not found")
        return None
    with open(EMAILS_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    for line in reversed(lines):
        parts = line.split("----")
        email = parts[0].strip()
        if email.lower() in used:
            continue
        password = parts[1].strip() if len(parts) >= 2 else ""
        token = parts[2].strip() if len(parts) >= 3 else ""
        client_id = parts[3].strip() if len(parts) >= 4 else ""
        if require_token and (not token or not client_id):
            continue
        rows.append((email, password, token, client_id))

    # T14: 预校验在锁外完成,返回 (row, validation) 列表。
    if validate_token and rows:
        validations = _pre_validate_emails(rows, platform, validate_token=True)
    else:
        validations = [None] * len(rows)

    with _lock, file_lock(f"{EMAILS_FILE}.{platform}.reserve"):
        # 双检 used,防止窗口期被别的 worker 抢走。
        used_now = _load_used(platform)
        for (email, password, token, client_id), validation in zip(rows, validations):
            if email.lower() in used_now:
                continue
            if validation is not None and not validation.get("ok"):
                reason = validation.get("reason") or "refresh_token_unusable"
                if validation.get("permanent"):
                    append_line(
                        _error_file(platform),
                        f"{email}----{password}----{reason}",
                    )
                else:
                    print(
                        f"  [email] skip mailbox after transient rt check: {email} ({reason})"
                    )
                continue
            folders = (validation or {}).get("folder_status") or {}
            folder_log = ", ".join(
                f"{name}={status}" for name, status in folders.items()
            )
            if folder_log:
                print(f"  [email] Graph mailbox readable: {email} ({folder_log})")
            append_line(_used_file(platform), f"{email}----{password}----reserved")
            _exclude_from_outlook_sale(platform, email)
            print(
                f"  [email] picked latest for {platform}: {email} "
                f"(rt={'yes' if token else 'no'})"
            )
            return email, password, token, client_id
    print(
        f"  [email] no unused latest mailbox for {platform} "
        f"(require_token={require_token}, validate_token={validate_token})"
    )
    return None


def retryable_email(platform, require_token=False, validate_token=False):
    """Reserve a mailbox whose platform flow failed but whose mailbox may be reused.

    This deliberately ignores the cross-platform registration exclusion: it is a
    retry of the same platform, not a return to the pristine Outlook sale pool.
    Successful platform records still win. Sale claims do not block a mailbox
    from later entering a registration flow.
    """
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform != "chatgpt":
        return None
    if not os.path.exists(EMAILS_FILE):
        return None
    # T14: 把网络校验移到 file_lock 之外。Pre-stage 数据结构后再 short-hold 锁。
    retry_candidates = []
    latest_status = {}
    latest_errors = {}
    used_path = _used_file(platform)
    error_path = _error_file(platform)
    if os.path.exists(used_path):
        with open(used_path, "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split("----")
                if parts and parts[0].strip():
                    latest_status[parts[0].strip().lower()] = (
                        parts[2].strip().lower() if len(parts) >= 3 else ""
                    )
    if os.path.exists(error_path):
        with open(error_path, "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split("----")
                if len(parts) < 3:
                    continue
                email = parts[0].strip().lower()
                reason = "----".join(parts[2:]).strip().lower()
                # Reinsert so dictionary order also follows the latest
                # observation for each mailbox.
                latest_errors.pop(email, None)
                latest_errors[email] = reason
    retry_candidates = [
        email
        for email, reason in latest_errors.items()
        if any(marker in reason for marker in _CHATGPT_RETRYABLE_ERROR_MARKERS)
    ]
    if not retry_candidates:
        return None
    records = {}
    with open(EMAILS_FILE, "r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            parts = raw.split("----")
            records[parts[0].strip().lower()] = (parts, raw)

    # T14: 顺序在锁外完成候选 / 校验。validate_token=False 时,网络未发起。
    ordered = []
    for email in reversed(retry_candidates):
        record = records.get(email)
        if not record:
            continue
        parts, _raw = record
        password = parts[1].strip() if len(parts) >= 2 else ""
        token = parts[2].strip() if len(parts) >= 3 else ""
        client_id = parts[3].strip() if len(parts) >= 4 else ""
        if require_token and (not token or not client_id):
            continue
        ordered.append((email, password, token, client_id))
    if validate_token:
        validations = _pre_validate_emails(ordered, platform, validate_token=True)
    else:
        validations = [None] * len(ordered)
    retry_claim = _retry_claim_status()

    with _lock, file_lock(f"{EMAILS_FILE}.{platform}.retry"):
        latest_status_now = {}
        if os.path.exists(used_path):
            with open(used_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    parts = line.strip().split("----")
                    if parts and parts[0].strip():
                        latest_status_now[parts[0].strip().lower()] = (
                            parts[2].strip().lower() if len(parts) >= 3 else ""
                        )
        for (email, password, token, client_id), validation in zip(ordered, validations):
            if latest_status_now.get(email) in {"ok", retry_claim}:
                continue
            if validation is not None and not validation.get("ok"):
                if validation.get("permanent"):
                    append_line(
                        error_path,
                        f"{email}----{password}----{validation.get('reason') or 'refresh_token_unusable'}",
                    )
                continue
            append_line(used_path, f"{email}----{password}----{retry_claim}")
            _exclude_from_outlook_sale(platform, email)
            print(f"  [email] retrying failed ChatGPT mailbox: {email}")
            return email, password, token, client_id
    return None


def mark_used(platform, email, password=""):
    append_line(_used_file(platform), f"{email}----{password}----ok")
    _exclude_from_outlook_sale(platform, email)


def mark_error(platform, email, password="", reason=""):
    append_line(_error_file(platform), f"{email}----{password}----{reason}")
    _exclude_from_outlook_sale(platform, email)
