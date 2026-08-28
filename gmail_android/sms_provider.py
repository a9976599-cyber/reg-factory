"""
Environment-driven SMS provider for Gmail Android phone verification.

Mirrors the provider shape used by reg-factory/common/sms.py and register.py:
firefox.fun is the primary 接码 service, hero-sms is the fallback. Provider
routing is by the activation_id prefix (hero_ -> hero-sms, otherwise firefox.fun),
matching common/sms.py.

Enable it by filling SMS_TOKEN + SMS_PROJECT_ID_GMAIL (firefox.fun) and/or
HERO_SMS_API_KEY + HERO_SMS_SERVICE_GMAIL (hero-sms) in .env, and running
gmail_register_local.py with --auto-phone (or PHONE_VERIFICATION_MODE=auto).
"""

import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from config import (
    HERO_SMS_API_BASE,
    HERO_SMS_API_KEY,
    HERO_SMS_SERVICE_GMAIL,
    SMS_API_BASE,
    SMS_COUNTRY_BLACKLIST_GMAIL,
    SMS_COUNTRY_GMAIL,
    SMS_MAXPRICE_GMAIL,
    SMS_PROJECT_ID_GMAIL,
    SMS_TOKEN,
    SMSMAN_API_BASE,
    SMSMAN_APP_ID_GMAIL,
    SMSMAN_BLACKLIST_GMAIL,
    SMSMAN_COUNTRY_GMAIL,
    SMSMAN_MAXPRICE_GMAIL,
    SMSMAN_TOKEN,
)


@dataclass
class SmsNumber:
    phone: str
    country_code: str
    activation_id: str
    provider: str
    can_receive_multiple: bool = False
    last_code: str = ""

    @property
    def full_number(self) -> str:
        """International number without a leading +. hero returns it already joined."""
        return f"{self.country_code}{self.phone}" if self.country_code else self.phone


class SmsProviderError(RuntimeError):
    pass


def configured() -> bool:
    return bool(
        (SMSMAN_TOKEN and SMSMAN_APP_ID_GMAIL)
        or (SMS_TOKEN and SMS_PROJECT_ID_GMAIL)
        or (HERO_SMS_API_KEY and HERO_SMS_SERVICE_GMAIL)
    )


def require_configured() -> None:
    if not configured():
        raise SmsProviderError("SMS provider is not configured. Fill .env first.")


def request_number(max_retries: int = 5, prefer_multi: bool = True) -> SmsNumber:
    """Get a number from firefox.fun, with sms-man and hero-sms as fallbacks."""
    require_configured()
    if SMS_TOKEN and SMS_PROJECT_ID_GMAIL:
        number = _request_firefox_number(max_retries=max_retries)
        if number:
            return number
    if SMSMAN_TOKEN and SMSMAN_APP_ID_GMAIL:
        try:
            number = _request_smsman_number(prefer_multi=prefer_multi)
        except SmsProviderError as exc:
            print(f"  [sms-man] unavailable, trying fallback providers: {exc}")
            number = None
        if number:
            return number
    if HERO_SMS_API_KEY and HERO_SMS_SERVICE_GMAIL:
        return _request_hero_number()
    raise SmsProviderError("get phone failed: firefox.fun exhausted and hero-sms not configured")


def get_code(
    activation_id: str,
    provider: str = "",
    max_wait: int = 180,
    interval: int = 5,
    since: str | None = None,
):
    """Poll for the verification code. Routes by activation_id prefix / provider."""
    if str(activation_id).startswith("smsman_") or provider == "smsman":
        return _smsman_get_code(activation_id, max_wait, interval, since=since)
    if str(activation_id).startswith("hero_") or provider == "hero":
        return _hero_get_code(activation_id, max_wait, interval, since=since)
    return _firefox_get_code(activation_id, max_wait, interval, since=since)


def release(activation_id: str, provider: str = "") -> None:
    """Release/cancel a number so it is not billed."""
    if str(activation_id).startswith("smsman_") or provider == "smsman":
        _smsman_release(activation_id)
        return
    if str(activation_id).startswith("hero_") or provider == "hero":
        _hero_release(activation_id)
        return
    _firefox_release(activation_id)


# ---------------- firefox.fun ----------------
def _request_firefox_number(max_retries: int = 5):
    """Request a firefox.fun number, honoring maxprice, country whitelist, and blacklist.
    SMS_COUNTRY_GMAIL (whitelist) cycles through preferred countries in order; empty = any.
    Mirrors register.py:get_phone_number. Returns SmsNumber or None (no stock)."""
    # Build the list of country values to try: whitelist entries first, then "" (any).
    country_slots = list(SMS_COUNTRY_GMAIL) if SMS_COUNTRY_GMAIL else [""]
    if "" not in country_slots:
        country_slots.append("")  # final fallback: any country

    for attempt in range(max_retries):
        country_param = country_slots[attempt % len(country_slots)]
        try:
            response = requests.get(
                SMS_API_BASE,
                params={
                    "act": "getPhone",
                    "token": SMS_TOKEN,
                    "iid": SMS_PROJECT_ID_GMAIL,
                    "did": "",
                    "country": country_param,
                    "dock": "",
                    "otpmode": "",
                    "maxPrice": str(SMS_MAXPRICE_GMAIL),
                    "mobile": "",
                    "pushUrl": "",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"  [sms] firefox request error: {exc}")
            time.sleep(2)
            continue
        text = response.text.strip()
        label = f"country={country_param or 'any'}"
        print(f"  [sms] firefox api (try={attempt + 1}, {label}): {text}")
        parts = text.split("|")
        if parts[0] == "1" and len(parts) >= 8:
            activation_id, country_code, phone = parts[1], parts[4], parts[7]
            if country_code in SMS_COUNTRY_BLACKLIST_GMAIL:
                print(f"  [sms] +{country_code} blacklisted, releasing and retrying...")
                _firefox_release(activation_id)
                time.sleep(1)
                continue
            if SMS_COUNTRY_GMAIL and country_code not in SMS_COUNTRY_GMAIL:
                print(f"  [sms] +{country_code} not in whitelist {SMS_COUNTRY_GMAIL}, releasing and retrying...")
                _firefox_release(activation_id)
                time.sleep(1)
                continue
            print(f"  [sms] phone: +{country_code}{phone} (id={activation_id})")
            return SmsNumber(phone=phone, country_code=country_code, activation_id=activation_id, provider="firefox")
        # '0|-8' / '0|-4' etc. are transient "no stock"; retry after a short wait.
        time.sleep(8)
    print("  [sms] firefox.fun exhausted")
    return None


def _firefox_get_code(activation_id: str, max_wait: int, interval: int, since: str | None = None):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = requests.get(
                SMS_API_BASE,
                params={"act": "getPhoneCode", "token": SMS_TOKEN, "pkey": activation_id},
                timeout=30,
            )
            parts = resp.text.strip().split("|")
            if parts[0] == "1" and len(parts) >= 2:
                code = parts[1]
                m = re.search(r"\d{4,8}", code)
                code = m.group(0) if m else code
                if since is not None and code == str(since):
                    time.sleep(interval)
                    continue
                print(f"  [sms] code: {code}")
                return code
        except requests.RequestException:
            pass
        print(f"  [sms] waiting sms... ({int(time.time() - start)}s/{max_wait}s)")
        time.sleep(interval)
    return None


def _firefox_release(activation_id: str) -> None:
    try:
        requests.get(
            SMS_API_BASE,
            params={"act": "cancelPhone", "token": SMS_TOKEN, "pkey": activation_id},
            timeout=10,
        )
    except requests.RequestException:
        pass


# ---------------- hero-sms ----------------
def _request_hero_number() -> SmsNumber:
    response = requests.get(
        HERO_SMS_API_BASE,
        params={"api_key": HERO_SMS_API_KEY, "action": "getNumber", "service": HERO_SMS_SERVICE_GMAIL},
        timeout=30,
    )
    text = response.text.strip()
    if text.startswith("ACCESS_NUMBER:"):
        _, activation_id, full_phone = text.split(":")[:3]
        print(f"  [hero-sms] phone: +{full_phone} (id={activation_id})")
        return SmsNumber(phone=full_phone, country_code="", activation_id=f"hero_{activation_id}", provider="hero")
    raise SmsProviderError(f"hero SMS provider returned: {text}")


def _hero_get_code(activation_id: str, max_wait: int, interval: int, since: str | None = None):
    act_id = str(activation_id).replace("hero_", "")
    try:
        requests.get(
            HERO_SMS_API_BASE,
            params={
                "api_key": HERO_SMS_API_KEY,
                "action": "setStatus",
                "id": act_id,
                "status": 3 if since else 1,
            },
            timeout=10,
        )
    except requests.RequestException:
        pass
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(
                HERO_SMS_API_BASE,
                params={"api_key": HERO_SMS_API_KEY, "action": "getStatus", "id": act_id},
                timeout=30,
            )
            text = r.text.strip()
            if text.startswith("STATUS_OK:"):
                code = text.split(":", 1)[1]
                m = re.search(r"\d{4,8}", code)
                code = m.group(0) if m else code
                if since is not None and code == str(since):
                    time.sleep(interval)
                    continue
                print(f"  [hero-sms] code: {code}")
                return code
            if text == "STATUS_CANCEL":
                return None
        except requests.RequestException:
            pass
        print(f"  [hero-sms] waiting... ({int(time.time() - start)}s/{max_wait}s)")
        time.sleep(interval)
    return None


def _hero_release(activation_id: str) -> None:
    act_id = str(activation_id).replace("hero_", "")
    try:
        requests.get(
            HERO_SMS_API_BASE,
            params={"api_key": HERO_SMS_API_KEY, "action": "setStatus", "id": act_id, "status": 8},
            timeout=10,
        )
    except requests.RequestException:
        pass


# ---------------- sms-man (multi-message leases) ----------------

_SMSMAN_APP_CACHE: dict[str, str] = {}


def _smsman_url(path: str) -> str:
    return SMSMAN_API_BASE.rstrip("/") + "/" + path.lstrip("/")


def _smsman_get(path: str, params: dict[str, Any], timeout: int = 30):
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(_smsman_url(path), params=params, timeout=timeout)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise SmsProviderError(f"sms-man {path} failed: {last_error}")


def _smsman_resolve_app(value: str) -> str | None:
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return raw
    if raw in _SMSMAN_APP_CACHE:
        return _SMSMAN_APP_CACHE[raw]
    data = _smsman_get("applications", {"token": SMSMAN_TOKEN}, timeout=20)
    items = list(data.values()) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    low = raw.lower()
    exact = None
    partial = None
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").lower()
        title = str(item.get("title") or item.get("name") or "").lower()
        if code == low and exact is None:
            exact = str(item.get("id") or "")
        if low in title and partial is None:
            partial = str(item.get("id") or "")
    app_id = exact or partial
    if app_id:
        _SMSMAN_APP_CACHE[raw] = app_id
    return app_id or None


def _request_smsman_number(prefer_multi: bool = True) -> SmsNumber | None:
    app_id = _smsman_resolve_app(SMSMAN_APP_ID_GMAIL)
    if not app_id:
        print(f"  [sms-man] application not found: {SMSMAN_APP_ID_GMAIL}")
        return None

    requested_country = str(SMSMAN_COUNTRY_GMAIL or "0").strip()
    if requested_country not in {"", "0"}:
        # 支持逗号分隔的多国 ID，如 "155,100"（法国,英国）
        countries = [c.strip() for c in requested_country.split(",") if c.strip()]
    else:
        multi_first = ["6", "22", "117", "16"]
        fallback = ["7", "36", "4", "12", "14", "10"]
        blocked = set(SMSMAN_BLACKLIST_GMAIL)
        countries = [item for item in multi_first + fallback if item not in blocked]

    ordinary: SmsNumber | None = None
    for country in countries:
        params = {"token": SMSMAN_TOKEN, "country_id": country, "application_id": app_id}
        if str(SMSMAN_MAXPRICE_GMAIL).strip() not in {"", "0"}:
            params.update({"maxPrice": str(SMSMAN_MAXPRICE_GMAIL).strip(), "currency": "USD"})
        try:
            data = _smsman_get("get-number", params)
        except SmsProviderError as exc:
            print(f"  [sms-man] country={country}: {exc}")
            continue
        if not (isinstance(data, dict) and data.get("request_id") and data.get("number")):
            continue
        number = SmsNumber(
            phone=str(data["number"]),
            country_code="",
            activation_id=f"smsman_{data['request_id']}",
            provider="smsman",
            can_receive_multiple=bool(data.get("can_receive_multiple_sms")),
        )
        print(
            f"  [sms-man] phone: +{number.phone} "
            f"(id={data['request_id']}, multi={number.can_receive_multiple})"
        )
        if not prefer_multi or number.can_receive_multiple:
            if ordinary:
                _smsman_release(ordinary.activation_id)
            return number
        if ordinary is None:
            ordinary = number
        else:
            _smsman_release(number.activation_id)
    return ordinary


def _smsman_get_code(
    activation_id: str,
    max_wait: int,
    interval: int,
    since: str | None = None,
):
    request_id = str(activation_id).replace("smsman_", "")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            data = _smsman_get(
                "get-sms",
                {"token": SMSMAN_TOKEN, "request_id": request_id},
                timeout=30,
            )
            if isinstance(data, dict) and data.get("sms_code"):
                raw = str(data["sms_code"])
                match = re.search(r"\d{4,8}", raw)
                code = match.group(0) if match else raw
                if since is not None and code == str(since):
                    time.sleep(interval)
                    continue
                print(f"  [sms-man] code: {code}")
                return code
            error_code = data.get("error_code") if isinstance(data, dict) else None
            if error_code and error_code != "wait_sms":
                print(f"  [sms-man] get-sms stopped: {error_code}")
                return None
        except SmsProviderError:
            pass
        print(f"  [sms-man] waiting... ({int(time.time() - start)}s/{max_wait}s)")
        time.sleep(interval)
    return None


def _smsman_release(activation_id: str) -> None:
    request_id = str(activation_id).replace("smsman_", "")
    try:
        requests.get(
            _smsman_url("set-status"),
            params={"token": SMSMAN_TOKEN, "request_id": request_id, "status": "reject"},
            timeout=10,
        )
    except requests.RequestException:
        pass
