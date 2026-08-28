import argparse
import json
import os
import random
import string
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By

try:
    from config import (
        ACCEPT_TERMS,
        ANDROID_DEVICE,
        APPIUM_SERVER,
        APPIUM_SYSTEM_PORT,
        AUTO_PREPARE_EMULATOR,
        AUTO_START_APPIUM,
        AUTO_STOP_EMULATOR,
        AUTO_SWITCH_NODE,
        BLUESTACKS_ADB_PORT,
        BLUESTACKS_INSTANCE,
        GMAIL_USERNAME_PREFIX,
        KEEP_EMULATOR_ON_MANUAL_HANDOFF,
        PHONE_VERIFICATION_MODE,
        SECOND_LOGIN_AFTER_SIGNUP,
        ENABLE_2FA_AFTER_LOGIN,
    )
except ImportError:
    ACCEPT_TERMS = False
    ANDROID_DEVICE = "127.0.0.1:5675"
    APPIUM_SERVER = "http://127.0.0.1:4723"
    APPIUM_SYSTEM_PORT = ""
    AUTO_PREPARE_EMULATOR = True
    AUTO_START_APPIUM = True
    AUTO_STOP_EMULATOR = True
    AUTO_SWITCH_NODE = True
    BLUESTACKS_ADB_PORT = ""
    BLUESTACKS_INSTANCE = ""
    GMAIL_USERNAME_PREFIX = ""
    KEEP_EMULATOR_ON_MANUAL_HANDOFF = True
    PHONE_VERIFICATION_MODE = "manual"
    SECOND_LOGIN_AFTER_SIGNUP = True
    ENABLE_2FA_AFTER_LOGIN = False


FIRST_NAMES = [
    "Alex",
    "Casey",
    "Drew",
    "Jordan",
    "Morgan",
    "Riley",
    "Taylor",
]

LAST_NAMES = [
    "Hayes",
    "Lane",
    "Parker",
    "Reed",
    "Stone",
    "Wells",
    "Young",
]

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


MANUAL_HANDOFF_RESULTS = {
    "phone_verification",
    "manual_verification",
    "terms_waiting_for_user",
    "second_login_manual",
    "second_login_phone_manual",
    "second_login_failed",
    "two_factor_manual",
    "two_factor_phone_manual",
    "security_check_manual",
    "recovery_manual",
}


def normalized_text(value: str) -> str:
    """Normalize punctuation variants emitted by Google WebView pages."""
    return (
        value.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00a0", " ")
        .lower()
    )

SECOND_LOGIN_NEEDLES = (
    "sign in again",
    "sign in to continue",
    "enter your password",
    "welcome back",
)

TWO_FACTOR_NEEDLES = (
    "2-step verification",
    "2 factor authentication",
    "two-factor authentication",
    "google authenticator",
    "authenticator app",
    "security key",
    "passkey",
)

SECURITY_CHECK_NEEDLES = (
    "verify it's you",
    "to help keep your account secure",
    "unusual about your activity",
    "suspicious activity",
    "try another way",
    "confirm you're not a robot",
)

RECOVERY_NEEDLES = (
    "recovery email",
    "recovery phone",
    "add phone number",
    "add a recovery",
    "secure your account",
)


@dataclass
class Account:
    username: str
    password: str
    first_name: str
    last_name: str
    month: str
    day: int
    year: int


STATE_PATH = Path(__file__).with_name(".runstate") / "pending-account.json"
COMPLETED_PATH = Path(__file__).with_name(".runstate") / "completed-accounts.jsonl"


def save_account_state(account: Account, stage: str, phone=None) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": stage,
        "updated_at": time.time(),
        "account": {
            "username": account.username,
            "password": account.password,
            "first_name": account.first_name,
            "last_name": account.last_name,
            "month": account.month,
            "day": account.day,
            "year": account.year,
        },
    }
    if phone:
        payload["phone"] = {
            "phone": phone.phone,
            "country_code": phone.country_code,
            "activation_id": phone.activation_id,
            "provider": phone.provider,
            "can_receive_multiple": phone.can_receive_multiple,
            "last_code": phone.last_code,
        }
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)


def load_account_state():
    if not STATE_PATH.is_file():
        return None, None, ""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        account = Account(**data["account"])
        phone = None
        if data.get("phone"):
            import sms_provider

            phone = sms_provider.SmsNumber(**data["phone"])
        return account, phone, str(data.get("stage") or "")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        log(f"Could not load pending account state: {exc}")
        return None, None, ""


def append_completed_account(account: Account, result: str) -> None:
    COMPLETED_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "email": f"{account.username}@gmail.com",
        "password": account.password,
        "result": result,
        "completed_at": time.time(),
    }
    with COMPLETED_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def log(message: str) -> None:
    print(message.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def generate_account(prefix: str = "") -> Account:
    alphabet = string.ascii_lowercase + string.digits
    stem = prefix or "gm" + "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    suffix = "".join(random.choice(alphabet) for _ in range(8))
    username = (stem + suffix)[:28]
    password = (
        random.choice(string.ascii_uppercase)
        + "".join(random.choice(string.ascii_lowercase) for _ in range(5))
        + "".join(random.choice(string.digits) for _ in range(4))
        + random.choice("!@#")
    )
    return Account(
        username=username,
        password=password,
        first_name=random.choice(FIRST_NAMES),
        last_name=random.choice(LAST_NAMES),
        month=random.choice(MONTHS),
        day=random.randint(1, 28),
        year=random.randint(1985, 2000),
    )


def make_driver(
    server_url: str,
    device_name: str,
    no_reset: bool,
    launch_gmail: bool = True,
    system_port: int | None = None,
    skip_server_install: bool = False,
) -> webdriver.Remote:
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.device_name = device_name
    options.automation_name = "UiAutomator2"
    options.no_reset = no_reset
    options.new_command_timeout = 600
    raw_adb_server_port = (os.environ.get("ANDROID_ADB_SERVER_PORT") or "").strip()
    if raw_adb_server_port.isdigit():
        options.set_capability("adbPort", int(raw_adb_server_port))
    if system_port:
        options.set_capability("systemPort", int(system_port))
        options.set_capability("mjpegServerPort", int(system_port) + 100)
    if launch_gmail:
        options.app_package = "com.google.android.gm"
        options.app_activity = "com.google.android.gm.ConversationListActivityGmail"
        options.set_capability(
            "appWaitActivity",
            ",".join(
                [
                    "com.google.android.gm.ConversationListActivityGmail",
                    "com.google.android.gm.welcome.*",
                    "com.google.android.gm.welcome.WelcomeTourActivity",
                    "com.google.android.gm.welcome.SetupAddressesActivity",
                    "com.google.android.gms.*",
                    "*",
                ]
            ),
        )
        options.set_capability("appWaitDuration", 90000)
    options.set_capability("adbExecTimeout", 90000)
    options.set_capability("uiautomator2ServerInstallTimeout", 90000)
    options.set_capability("uiautomator2ServerLaunchTimeout", 90000)
    options.set_capability("ignoreHiddenApiPolicyError", True)
    options.set_capability("disableWindowAnimation", True)
    options.set_capability("skipLogcatCapture", True)
    options.set_capability("uiautomator2ServerReadTimeout", 90000)
    if skip_server_install:
        options.set_capability("skipServerInstallation", True)
    return webdriver.Remote(server_url, options=options)


def ensure_appium_server(server_url: str) -> None:
    parsed = urlparse(server_url)
    port = parsed.port or 4723
    status_url = f"{parsed.scheme or 'http'}://{parsed.hostname or '127.0.0.1'}:{port}/status"
    try:
        with urlopen(status_url, timeout=3):
            return
    except Exception:
        pass

    script = Path(__file__).with_name("scripts") / "watch_appium.ps1"
    if not script.is_file():
        log(f"Appium is not responding at {status_url}, and watchdog script is missing: {script}")
        return
    raw_adb_server_port = (os.environ.get("ANDROID_ADB_SERVER_PORT") or "5037").strip()
    adb_server_port = int(raw_adb_server_port) if raw_adb_server_port.isdigit() else 5037
    log(f"Appium is not responding at {status_url}; starting watchdog on adb server {adb_server_port}.")
    subprocess.Popen(
        [
            "powershell",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-AppiumPort",
            str(port),
            "-AdbServerPort",
            str(adb_server_port),
        ],
        cwd=str(Path(__file__).resolve().parent),
    )
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            with urlopen(status_url, timeout=3):
                log(f"Appium ready at {status_url}")
                return
        except Exception:
            time.sleep(2)
    log(f"warning: Appium did not become ready at {status_url} within 45s")


def is_element_visible(el) -> bool:
    try:
        rect = el.rect
        return rect.get("width", 0) > 0 and rect.get("height", 0) > 0
    except (StaleElementReferenceException, WebDriverException):
        return False


def visible_texts(driver: webdriver.Remote) -> list[str]:
    for _ in range(4):
        texts: list[str] = []
        try:
            for el in driver.find_elements(By.XPATH, "//*[@text or @content-desc]"):
                if not is_element_visible(el):
                    continue
                text = (el.get_attribute("text") or el.get_attribute("content-desc") or "").strip()
                if text and text not in texts:
                    texts.append(text)
            return texts
        except (StaleElementReferenceException, WebDriverException) as exc:
            if not is_stale_error(exc):
                raise
            time.sleep(1)
    return []


def manual_handoff_result(texts: list[str]) -> str | None:
    joined = normalized_text("\n".join(texts))
    if any(needle in joined for needle in TWO_FACTOR_NEEDLES):
        return "two_factor_manual"
    if any(needle in joined for needle in SECOND_LOGIN_NEEDLES):
        return "second_login_manual"
    if any(needle in joined for needle in SECURITY_CHECK_NEEDLES):
        return "security_check_manual"
    if any(needle in joined for needle in RECOVERY_NEEDLES):
        return "recovery_manual"
    return None


def dump_state(driver: webdriver.Remote, title: str) -> list[str]:
    texts = visible_texts(driver)
    log(f"\n--- {title} ---")
    log(" | ".join(texts[:80]))
    return texts


def find_text(driver: webdriver.Remote, text: str, contains: bool = False):
    if contains:
        xpath = (
            "//*[contains(translate(@text,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
            f"'{text.lower()}') or contains(translate(@content-desc,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
            f"'{text.lower()}')]"
        )
    else:
        xpath = f"//*[@text={xpath_literal(text)} or @content-desc={xpath_literal(text)}]"
    els = driver.find_elements(By.XPATH, xpath)
    visible = [el for el in els if is_element_visible(el)]
    return visible[0] if visible else None


def find_resource_id(driver: webdriver.Remote, resource_id: str):
    xpath = f"//*[@resource-id={xpath_literal(resource_id)}]"
    els = driver.find_elements(By.XPATH, xpath)
    visible = [el for el in els if is_element_visible(el)]
    return visible[0] if visible else None


def is_stale_error(exc: Exception) -> bool:
    return isinstance(exc, StaleElementReferenceException) or "stale element reference" in str(exc).lower()


def xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def click_text(driver: webdriver.Remote, text: str, contains: bool = False, timeout: int = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        el = find_text(driver, text, contains=contains)
        if el:
            try:
                log(f"click: {text}")
                el.click()
                time.sleep(2)
                return True
            except (StaleElementReferenceException, WebDriverException) as exc:
                if not is_stale_error(exc):
                    raise
                time.sleep(1)
        time.sleep(1)
    return False


def tap_text_center(driver: webdriver.Remote, text: str, contains: bool = False, timeout: int = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        el = find_text(driver, text, contains=contains)
        if el:
            rect = el.rect
            x = int(rect["x"] + rect["width"] / 2)
            y = int(rect["y"] + rect["height"] / 2)
            log(f"tap: {text} at {x},{y}")
            driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
            time.sleep(2)
            return True
        time.sleep(1)
    return False


def click_or_tap_text(driver: webdriver.Remote, text: str, contains: bool = False, timeout: int = 20) -> bool:
    before = visible_texts(driver)
    if not click_text(driver, text, contains=contains, timeout=timeout):
        return False
    time.sleep(1)
    after = visible_texts(driver)
    if after != before:
        return True
    return tap_text_center(driver, text, contains=contains, timeout=3)


def click_next(driver: webdriver.Remote, timeout: int = 20) -> bool:
    return click_text(driver, "NEXT", timeout=timeout) or click_text(driver, "Next", timeout=timeout)


def submit_next(driver: webdriver.Remote, timeout: int = 20) -> bool:
    """Advance a Google form even when the soft keyboard covers the Next button."""
    if click_next(driver, timeout=min(timeout, 6)):
        return True
    before = visible_texts(driver)
    try:
        driver.execute_script("mobile: performEditorAction", {"action": "next"})
    except WebDriverException:
        try:
            driver.press_keycode(66)
        except WebDriverException:
            pass
    time.sleep(3)
    if visible_texts(driver) != before:
        return True
    try:
        driver.hide_keyboard()
    except WebDriverException:
        try:
            driver.press_keycode(4)
        except WebDriverException:
            pass
    time.sleep(1)
    return click_next(driver, timeout=max(3, timeout - 6))


def require_page(driver: webdriver.Remote, needles: list[str], title: str, timeout: int = 30) -> list[str]:
    texts = wait_until_any(driver, needles, timeout=timeout)
    joined = "\n".join(texts).lower()
    if not any(needle.lower() in joined for needle in needles):
        dump_state(driver, f"unexpected page while waiting for {title}")
        raise RuntimeError(f"Expected {title}, but none of these were visible: {', '.join(needles)}")
    return texts


def input_by_text_hint(driver: webdriver.Remote, hint: str, value: str, timeout: int = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        el = find_text(driver, hint, contains=True)
        if el:
            log(f"type into {hint}: {mask(value)}")
            el.click()
            time.sleep(0.5)
            el.send_keys(value)
            time.sleep(0.5)
            return True
        time.sleep(1)
    return False


def input_by_resource_id(driver: webdriver.Remote, resource_id: str, value: str, timeout: int = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        el = find_resource_id(driver, resource_id)
        if el:
            log(f"type into {resource_id}: {mask(value)}")
            el.click()
            time.sleep(0.5)
            try:
                el.clear()
            except WebDriverException:
                pass
            el.send_keys(value)
            time.sleep(0.5)
            return True
        time.sleep(1)
    return False


def input_name_fields(driver: webdriver.Remote, first_name: str, last_name: str) -> bool:
    end = time.time() + 25
    while time.time() < end:
        if input_by_resource_id(driver, "firstName", first_name, timeout=3):
            if not input_by_resource_id(driver, "lastName", last_name, timeout=3):
                input_edittext_index(driver, 1, last_name, timeout=3)
            return True
        edits = [el for el in driver.find_elements(By.CLASS_NAME, "android.widget.EditText") if is_element_visible(el)]
        if edits:
            if len(edits) >= 2:
                return input_edittexts(driver, [first_name, last_name])
            if input_edittext_index(driver, 0, first_name, timeout=3):
                return True
        time.sleep(1)
    return False


def input_edittexts(driver: webdriver.Remote, values: list[str]) -> bool:
    edits = [el for el in driver.find_elements(By.CLASS_NAME, "android.widget.EditText") if is_element_visible(el)]
    if len(edits) < len(values):
        return False
    for el, value in zip(edits, values):
        el.click()
        time.sleep(0.4)
        log(f"type: {mask(value)}")
        el.send_keys(value)
        time.sleep(0.4)
    return True


def input_edittext_index(driver: webdriver.Remote, index: int, value: str, timeout: int = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        edits = [el for el in driver.find_elements(By.CLASS_NAME, "android.widget.EditText") if is_element_visible(el)]
        if len(edits) > index:
            el = edits[index]
            el.click()
            time.sleep(0.4)
            try:
                el.clear()
            except WebDriverException:
                pass
            log(f"type into edittext[{index}]: {mask(value)}")
            el.send_keys(value)
            time.sleep(0.4)
            return True
        time.sleep(1)
    return False


def mask(value: str) -> str:
    if len(value) >= 8 and any(ch.isdigit() for ch in value):
        return "*" * len(value)
    return value


def wait_until_any(driver: webdriver.Remote, needles: list[str], timeout: int = 60) -> list[str]:
    end = time.time() + timeout
    while time.time() < end:
        texts = visible_texts(driver)
        joined = "\n".join(texts).lower()
        if any(needle.lower() in joined for needle in needles):
            return texts
        time.sleep(2)
    return visible_texts(driver)


def select_spinner_item(driver: webdriver.Remote, field_text: str, item_text: str) -> bool:
    if not click_or_tap_text(driver, field_text, contains=True, timeout=10):
        return False
    time.sleep(1)
    return click_or_tap_text(driver, item_text, contains=True, timeout=10)


def select_spinner_by_index(driver: webdriver.Remote, index: int, item_text: str) -> bool:
    spinners = [el for el in driver.find_elements(By.CLASS_NAME, "android.widget.Spinner") if is_element_visible(el)]
    if len(spinners) <= index:
        return False
    spinners[index].click()
    time.sleep(1)
    return click_or_tap_text(driver, item_text, contains=True, timeout=10)


def click_button(driver: webdriver.Remote, text: str, timeout: int = 20) -> bool:
    return click_or_tap_text(driver, text, contains=False, timeout=timeout)


def click_create_account_personal(driver: webdriver.Remote) -> bool:
    def advanced_to_signup() -> bool:
        joined = "\n".join(visible_texts(driver))
        return (
            "Enter your name" in joined
            or "First name" in joined
            or "Create a Google Account" in joined
            or "Create your Google Account" in joined
            or "Basic information" in joined
        )

    for attempt in range(1, 4):
        if advanced_to_signup():
            return True
        el = find_text(driver, "Create account") or find_text(driver, "Create account", contains=True)
        if not el:
            time.sleep(1)
            continue
        rect = el.rect
        cx = int(rect["x"] + rect["width"] / 2)
        cy = int(rect["y"] + rect["height"] / 2)
        log(f"open Create account menu (attempt {attempt})")
        driver.execute_script("mobile: clickGesture", {"x": cx, "y": cy})
        time.sleep(2)
        try:
            driver.press_keycode(20)  # DPAD_DOWN selects the first menu item.
            time.sleep(0.6)
            driver.press_keycode(66)  # ENTER
        except Exception as exc:
            log(f"press_keycode failed: {exc}")
        time.sleep(2.5)
        if advanced_to_signup():
            return True
        log("menu select did not advance, retrying...")
    return False


def proceed_gmail_onboarding(driver: webdriver.Remote) -> None:
    for _ in range(12):
        texts = dump_state(driver, "gmail onboarding")
        joined = "\n".join(texts)
        if "Update your device to stay secure" in joined and "Dismiss" in texts:
            click_text(driver, "Dismiss", timeout=5)
        elif "Welcome to Gmail" in joined and "SKIP" in texts:
            click_text(driver, "SKIP", timeout=5)
        elif "Welcome to Gmail" in joined and "Next" in texts:
            click_text(driver, "Next", timeout=5)
        elif "GOT IT" in texts:
            click_text(driver, "GOT IT", timeout=5)
        elif "OK" in texts and "Please add at least one email address." in joined:
            click_text(driver, "OK", timeout=5)
        elif "Add an email address" in texts:
            click_text(driver, "Add an email address", timeout=5)
        elif "Google" in texts:
            click_text(driver, "Google", timeout=5)
            wait_until_any(driver, ["Sign in", "Create account", "Checking info"], timeout=60)
            return
        elif "Create account" in joined or "Sign in" in joined:
            return
        elif "Basic information" in joined or "Enter your name" in joined:
            return
        else:
            break


def complete_post_phone_flow(driver: webdriver.Remote, accept_terms: bool) -> str:
    terms_clicked = False
    services_clicked = False
    for _ in range(30):
        texts = dump_state(driver, "post-phone flow")
        joined = "\n".join(texts).lower()

        handoff = manual_handoff_result(texts)
        if handoff:
            return handoff

        if "conversationlistactivitygmail" in driver.current_activity.lower():
            return "gmail_opened"

        if "review your account info" in joined:
            click_next(driver, timeout=10)
            continue

        if "privacy and terms" in joined:
            if not accept_terms:
                return "terms_waiting_for_user"
            if terms_clicked or "loading" in joined or "indeterminate" in joined:
                time.sleep(5)
                continue
            for _ in range(6):
                if click_button(driver, "I agree", timeout=3):
                    terms_clicked = True
                    time.sleep(5)
                    break
                driver.swipe(450, 1450, 450, 350, 700)
                time.sleep(1)
            else:
                raise RuntimeError("Could not find I agree on Privacy and Terms page")
            continue

        if "google services" in joined:
            if services_clicked or "loading" in joined or "indeterminate" in joined:
                time.sleep(5)
                continue
            if click_button(driver, "ACCEPT", timeout=10):
                services_clicked = True
                time.sleep(5)
            else:
                driver.swipe(450, 1450, 450, 350, 700)
                if click_button(driver, "ACCEPT", timeout=10):
                    services_clicked = True
                    time.sleep(5)
            continue

        if "take me to gmail" in joined:
            click_button(driver, "TAKE ME TO GMAIL", timeout=10)
            continue

        if "add another email address" in joined and any("@gmail.com" in text for text in texts):
            click_button(driver, "TAKE ME TO GMAIL", timeout=10)
            continue

        if "this may take a few moments" in joined or "loading" in joined:
            time.sleep(5)
            continue

        if any("phone number" in text.lower() or "verification code" in text.lower() for text in texts):
            return "phone_verification"

        time.sleep(2)

    return "unknown_post_phone_step"


def post_login_manual_check(driver: webdriver.Remote, seconds: int = 30) -> str:
    end = time.time() + seconds
    result = "gmail_opened"
    while time.time() < end:
        texts = dump_state(driver, "post-login check")
        handoff = manual_handoff_result(texts)
        if handoff:
            return handoff
        joined = "\n".join(texts).lower()
        if "take me to gmail" in joined:
            click_button(driver, "TAKE ME TO GMAIL", timeout=5)
        elif "add another email address" in joined and any("@gmail.com" in text for text in texts):
            click_button(driver, "TAKE ME TO GMAIL", timeout=5)
        elif "open navigation drawer" in joined or "compose" in joined or "search in mail" in joined:
            result = "gmail_opened"
        time.sleep(3)
    return result


PHONE_REJECT_NEEDLES = [
    "couldn't verify",
    "could not verify",
    "can't be used",
    "cannot be used",
    "this phone number cannot",
    "invalid",
    "wrong number",
    "try another",
    "too many",
    "problem verifying",
    "enter a valid",
]

PHONE_CODE_NEEDLES = [
    "verification code",
    "enter the code",
    "enter code",
    "6-digit code",
    "enter the 6",
    "g-",
    "confirm you're not a robot",
    "we sent",
    "sent a text",
]

POST_PHONE_SUCCESS_NEEDLES = [
    "Review your account info",
    "Privacy and Terms",
    "Google services",
    "TAKE ME TO GMAIL",
]


def return_to_phone_entry(driver: webdriver.Remote) -> bool:
    def phone_field_visible() -> bool:
        joined = normalized_text("\n".join(visible_texts(driver)))
        return "phone number" in joined and not any(needle in joined for needle in PHONE_CODE_NEEDLES)

    if not phone_field_visible():
        try:
            driver.back()
        except WebDriverException:
            driver.press_keycode(4)

    end = time.time() + 12
    while time.time() < end:
        if phone_field_visible():
            edits = [
                el
                for el in driver.find_elements(By.CLASS_NAME, "android.widget.EditText")
                if is_element_visible(el)
            ]
            if edits:
                try:
                    edits[0].clear()
                except WebDriverException:
                    pass
            return True
        time.sleep(1)
    return False


def enter_phone_number(driver: webdriver.Remote, full_number: str) -> bool:
    """Fill the phone-entry field with an international number (+CC...).
    Best-effort: tries the visible EditText first, then common hints."""
    if input_edittext_index(driver, 0, full_number, timeout=10):
        return True
    for hint in ("Phone number", "phone number", "Phone"):
        if input_by_text_hint(driver, hint, full_number, timeout=5):
            return True
    return False


def auto_phone_verification(
    driver: webdriver.Remote,
    accept_terms: bool,
    max_number_tries: int = 5,
    lease_holder: list | None = None,
) -> str:
    """Complete Google phone verification using the firefox.fun SMS provider
    (hero-sms fallback). Returns a post-phone flow result on success, or
    'phone_verification' if it exhausts attempts so manual handling still works."""
    import sms_provider

    if not sms_provider.configured():
        log("Auto phone verification requested but SMS provider is not configured; falling back to manual.")
        return "phone_verification"

    for attempt in range(1, max_number_tries + 1):
        number = None
        try:
            number = sms_provider.request_number()
        except sms_provider.SmsProviderError as exc:
            log(f"[sms] request_number failed (attempt {attempt}): {exc}")
            time.sleep(3)
            continue

        # hero returns a full number already carrying the country code; firefox
        # returns national digits + a separate country_code.
        if number.country_code:
            full_number = f"+{number.country_code}{number.phone}"
        else:
            full_number = number.phone if number.phone.startswith("+") else f"+{number.phone}"
        log(f"[sms] using {full_number} (provider={number.provider}, id={number.activation_id})")

        try:
            if not enter_phone_number(driver, full_number):
                dump_state(driver, "phone entry (no field found)")
                sms_provider.release(number.activation_id, number.provider)
                return "phone_verification"

            click_next(driver, timeout=20)
            texts = wait_until_any(
                driver,
                PHONE_CODE_NEEDLES + PHONE_REJECT_NEEDLES,
                timeout=60,
            )
            joined = "\n".join(texts).lower()

            if any(needle in joined for needle in PHONE_REJECT_NEEDLES):
                dump_state(driver, f"phone number rejected (attempt {attempt})")
                sms_provider.release(number.activation_id, number.provider)
                # Clear the field for the next number before retrying.
                edits = [el for el in driver.find_elements(By.CLASS_NAME, "android.widget.EditText") if is_element_visible(el)]
                if edits:
                    try:
                        edits[0].clear()
                    except WebDriverException:
                        pass
                time.sleep(2)
                continue

            if not any(needle in joined for needle in PHONE_CODE_NEEDLES):
                dump_state(driver, "unexpected page after submitting phone")
                sms_provider.release(number.activation_id, number.provider)
                return "phone_verification"

            code = sms_provider.get_code(number.activation_id, number.provider, max_wait=180, interval=5)
            if not code:
                log("[sms] no code received before timeout; releasing and retrying.")
                sms_provider.release(number.activation_id, number.provider)
                if not return_to_phone_entry(driver):
                    return "phone_verification"
                time.sleep(2)
                continue

            if not input_edittext_index(driver, 0, code, timeout=15):
                if not input_by_text_hint(driver, "code", code, timeout=10):
                    dump_state(driver, "code entry (no field found)")
                    sms_provider.release(number.activation_id, number.provider)
                    return "phone_verification"
            click_next(driver, timeout=20) or click_button(driver, "Verify", timeout=10)
            number.last_code = str(code)

            texts = wait_until_any(
                driver,
                POST_PHONE_SUCCESS_NEEDLES + PHONE_REJECT_NEEDLES,
                timeout=90,
            )
            joined = "\n".join(texts).lower()
            if any(needle in joined for needle in PHONE_REJECT_NEEDLES):
                dump_state(driver, f"phone rejected after SMS code (attempt {attempt})")
                sms_provider.release(number.activation_id, number.provider)
                if not return_to_phone_entry(driver):
                    return "phone_verification"
                time.sleep(2)
                continue

            if lease_holder is not None:
                lease_holder.clear()
                lease_holder.append(number)
            else:
                sms_provider.release(number.activation_id, number.provider)
            return complete_post_phone_flow(driver, accept_terms=accept_terms)
        except WebDriverException as exc:
            log(f"[sms] WebDriver error during phone verification: {exc}")
            if number:
                sms_provider.release(number.activation_id, number.provider)
            return "phone_verification"

    log("[sms] exhausted phone number attempts; falling back to manual verification.")
    return "phone_verification"


def create_account_flow(
    driver: webdriver.Remote,
    account: Account,
    stop_after_create_account: bool = False,
    wait_phone_verification: bool = False,
    accept_terms: bool = False,
    auto_phone: bool = False,
    lease_holder: list | None = None,
) -> str:
    proceed_gmail_onboarding(driver)

    wait_until_any(
        driver,
        ["Create account", "Forgot email", "Enter your name", "First name", "Sign in - Google Accounts"],
        timeout=60,
    )
    texts = dump_state(driver, "google sign-in")
    joined = "\n".join(texts)
    if "Create account" in joined or "Forgot email" in joined or "Sign in" in joined:
        if not click_create_account_personal(driver):
            raise RuntimeError("Could not open Create account / For my personal use on Google sign-in page")
        if stop_after_create_account:
            dump_state(driver, "after create account")
            return "stopped_after_create_account"

    require_page(driver, ["First name", "Enter your name", "Basic information"], "name or birthday page", timeout=45)
    texts = dump_state(driver, "name page")
    if "First name" in "\n".join(texts) or "Enter your name" in "\n".join(texts):
        if not input_name_fields(driver, account.first_name, account.last_name):
            dump_state(driver, "name page (input failed)")
            raise RuntimeError("Could not fill required name fields")
        if not submit_next(driver, timeout=20):
            raise RuntimeError("Could not submit the name page")

    require_page(driver, ["Basic information", "birthday", "gender"], "birthday page", timeout=45)
    dump_state(driver, "birthday page")
    if not select_spinner_item(driver, "Month", account.month):
        if not select_spinner_by_index(driver, 0, account.month):
            raise RuntimeError("Could not select birth month")
    if not input_by_resource_id(driver, "day", str(account.day), timeout=5):
        if not input_by_text_hint(driver, "Day", str(account.day), timeout=8):
            raise RuntimeError("Could not fill birth day")
    if not input_by_resource_id(driver, "year", str(account.year), timeout=5):
        if not input_by_text_hint(driver, "Year", str(account.year), timeout=8):
            raise RuntimeError("Could not fill birth year")
    if not select_spinner_item(driver, "Gender", "Rather not say"):
        if not select_spinner_by_index(driver, 1, "Rather not say"):
            select_spinner_by_index(driver, 1, "Prefer not to say")
    if not submit_next(driver, timeout=20):
        raise RuntimeError("Could not submit the birthday page")

    require_page(
        driver,
        ["Create an email address", "Gmail address", "Choose your Gmail address", "How you'll sign in"],
        "Gmail address page",
        timeout=90,
    )
    texts = dump_state(driver, "gmail address page")
    desired = f"{account.username}@gmail.com"
    if any(desired == text for text in texts):
        click_text(driver, desired, timeout=10)
    elif find_text(driver, "Create your own Gmail address", contains=True):
        click_or_tap_text(driver, "Create your own Gmail address", contains=True, timeout=10)
        wait_until_any(driver, ["Gmail address", "Username", "How you'll sign in"], timeout=20)
        time.sleep(1)
        if not input_edittext_index(driver, 0, account.username, timeout=10):
            if not input_by_text_hint(driver, "Gmail address", account.username, timeout=5):
                if not input_by_text_hint(driver, "Username", account.username, timeout=5):
                    input_edittexts(driver, [account.username])
    elif "How you'll sign in" in "\n".join(texts) or any("@gmail.com" in text for text in texts):
        if not input_edittext_index(driver, 0, account.username, timeout=10):
            if not input_by_text_hint(driver, "Gmail address", account.username, timeout=5):
                input_by_text_hint(driver, "Username", account.username, timeout=5)
    else:
        candidate = next((text for text in texts if text.endswith("@gmail.com")), "")
        if not candidate:
            raise RuntimeError("Could not find or enter a Gmail address")
        account.username = candidate.removesuffix("@gmail.com")
        click_text(driver, candidate, timeout=10)
    if not submit_next(driver, timeout=20):
        raise RuntimeError("Could not submit the Gmail address page")

    require_page(driver, ["Create a strong password", "Password", "Show password"], "password page", timeout=90)
    dump_state(driver, "password page")
    edits = [el for el in driver.find_elements(By.CLASS_NAME, "android.widget.EditText") if is_element_visible(el)]
    if len(edits) >= 2:
        if not input_edittexts(driver, [account.password, account.password]):
            raise RuntimeError("Could not fill password fields")
    elif not input_edittext_index(driver, 0, account.password, timeout=10):
        if not input_by_text_hint(driver, "Password", account.password, timeout=10):
            raise RuntimeError("Could not fill password")
        input_by_text_hint(driver, "Confirm", account.password, timeout=5)
    if not submit_next(driver, timeout=20):
        raise RuntimeError("Could not submit the password page")

    texts = wait_until_any(
        driver,
        ["Verify your phone number", "Get a verification code", "phone number", "Confirm you're not a robot", "Skip"],
        timeout=90,
    )
    texts = dump_state(driver, "after password")
    if any("phone number" in text.lower() or "verification code" in text.lower() for text in texts):
        if auto_phone:
            result = auto_phone_verification(
                driver,
                accept_terms=accept_terms,
                lease_holder=lease_holder,
            )
            if result != "phone_verification":
                return result
            if not wait_phone_verification:
                return "phone_verification"
        if not wait_phone_verification:
            return "phone_verification"
        log("Waiting for manual phone/SMS verification to complete...")
        wait_until_any(
            driver,
            ["Review your account info", "Privacy and Terms", "Google services", "TAKE ME TO GMAIL"],
            timeout=900,
        )
        return complete_post_phone_flow(driver, accept_terms=accept_terms)
    if any("Skip" == text or "Skip" in text for text in texts):
        return "optional_phone_or_recovery"
    return complete_post_phone_flow(driver, accept_terms=accept_terms)


def resume_registration_flow(
    driver: webdriver.Remote,
    accept_terms: bool,
    auto_phone: bool = False,
    lease_holder: list | None = None,
) -> str:
    texts = dump_state(driver, "resume registration")
    joined = "\n".join(texts).lower()
    at_phone_entry = "phone number" in joined and any(
        marker in joined for marker in ("verification code", "confirm you're not a robot", "verify")
    )
    if at_phone_entry and auto_phone:
        return auto_phone_verification(
            driver,
            accept_terms=accept_terms,
            lease_holder=lease_holder,
        )
    if at_phone_entry:
        return "phone_verification"
    return complete_post_phone_flow(driver, accept_terms=accept_terms)


def phone_full_number(phone) -> str:
    if not phone:
        return ""
    raw = f"{phone.country_code}{phone.phone}" if phone.country_code else str(phone.phone)
    return raw if raw.startswith("+") else f"+{raw}"


def adb_run(device: str, *args: str, timeout: int = 30) -> tuple[bool, str]:
    adb_path = os.environ.get("ADB_PATH") or "adb"
    try:
        proc = subprocess.run(
            [adb_path, "-s", device, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode == 0, (proc.stdout or proc.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def adb_ui_nodes(device: str) -> list[dict[str, str]]:
    adb_run(device, "shell", "uiautomator", "dump", "--compressed", "/sdcard/_gmail.xml", timeout=25)
    ok, raw = adb_run(device, "exec-out", "cat", "/sdcard/_gmail.xml", timeout=15)
    if not ok or not raw.startswith("<?xml"):
        return []
    try:
        return [dict(node.attrib) for node in ET.fromstring(raw).iter("node")]
    except ET.ParseError:
        return []


def adb_node_center(node: dict[str, str]) -> tuple[int, int] | None:
    values = [int(value) for value in __import__("re").findall(r"\d+", node.get("bounds", ""))]
    if len(values) != 4:
        return None
    return (values[0] + values[2]) // 2, (values[1] + values[3]) // 2


def adb_tap_node(device: str, node: dict[str, str]) -> bool:
    center = adb_node_center(node)
    if not center:
        return False
    ok, _ = adb_run(device, "shell", "input", "tap", str(center[0]), str(center[1]), timeout=10)
    time.sleep(1.5)
    return ok


def adb_find_node(
    nodes: list[dict[str, str]],
    texts: tuple[str, ...] = (),
    resource_ids: tuple[str, ...] = (),
    class_name: str = "",
) -> dict[str, str] | None:
    lowered = tuple(text.lower() for text in texts)
    for node in nodes:
        node_text = (node.get("text") or node.get("content-desc") or "").strip().lower()
        resource_id = node.get("resource-id", "")
        if resource_ids and resource_id not in resource_ids:
            continue
        if class_name and node.get("class") != class_name:
            continue
        if lowered and not any(text == node_text or text in node_text for text in lowered):
            continue
        if adb_node_center(node):
            return node
    return None


def adb_fill_node(device: str, node: dict[str, str], value: str) -> bool:
    if not adb_tap_node(device, node):
        return False
    adb_run(device, "shell", "input", "keyevent", "KEYCODE_MOVE_END", timeout=10)
    adb_run(
        device,
        "shell",
        "sh",
        "-c",
        "i=0; while [ $i -lt 32 ]; do input keyevent KEYCODE_DEL; i=$((i+1)); done",
        timeout=20,
    )
    if value.startswith("+"):
        adb_run(device, "shell", "input", "keyevent", "KEYCODE_PLUS", timeout=10)
        value = value[1:]
    ok, _ = adb_run(device, "shell", "input", "text", value, timeout=15)
    time.sleep(1)
    return ok


def adb_page_state(device: str) -> tuple[list[dict[str, str]], str]:
    nodes = adb_ui_nodes(device)
    texts = []
    for node in nodes:
        text = (node.get("text") or node.get("content-desc") or "").strip()
        if text and text not in texts:
            texts.append(text)
    return nodes, "\n".join(texts).lower()


def adb_wait_phone_state(device: str, timeout: int = 75) -> tuple[str, list[dict[str, str]]]:
    end = time.time() + timeout
    while time.time() < end:
        nodes, joined = adb_page_state(device)
        if any(needle in joined for needle in PHONE_REJECT_NEEDLES):
            return "rejected", nodes
        if any(needle.lower() in joined for needle in POST_PHONE_SUCCESS_NEEDLES):
            return "success", nodes
        has_phone = any(node.get("resource-id") == "phoneNumberId" for node in nodes)
        if not has_phone and any(
            marker in joined for marker in ("enter the code", "verification code", "6-digit code", "we sent")
        ):
            return "code", nodes
        time.sleep(2)
    return "timeout", adb_ui_nodes(device)


def adb_auto_phone_verification(
    device: str,
    lease_holder: list | None = None,
    max_number_tries: int = 5,
) -> str:
    import sms_provider

    for attempt in range(1, max_number_tries + 1):
        nodes, joined = adb_page_state(device)
        phone_field = adb_find_node(nodes, resource_ids=("phoneNumberId",), class_name="android.widget.EditText")
        if not phone_field:
            log(f"[adb-sms] phone field not found on attempt {attempt}: {joined[:160]}")
            return "phone_verification"
        try:
            number = sms_provider.request_number(prefer_multi=True)
        except sms_provider.SmsProviderError as exc:
            log(f"[adb-sms] number request failed: {exc}")
            continue
        full_number = phone_full_number(number)
        log(f"[adb-sms] trying {full_number} ({number.provider}, attempt {attempt})")
        if not adb_fill_node(device, phone_field, full_number):
            sms_provider.release(number.activation_id, number.provider)
            continue
        nodes = adb_ui_nodes(device)
        next_button = adb_find_node(nodes, texts=("Next", "Send", "Verify"))
        if not next_button or not adb_tap_node(device, next_button):
            sms_provider.release(number.activation_id, number.provider)
            continue

        state, nodes = adb_wait_phone_state(device)
        if state == "rejected":
            log(f"[adb-sms] Google rejected {full_number}; switching number")
            sms_provider.release(number.activation_id, number.provider)
            continue
        if state != "code":
            sms_provider.release(number.activation_id, number.provider)
            if state == "success":
                return "phone_verified"
            continue

        code = sms_provider.get_code(number.activation_id, number.provider, max_wait=180, interval=5)
        if not code:
            sms_provider.release(number.activation_id, number.provider)
            continue
        code_field = adb_find_node(nodes, class_name="android.widget.EditText")
        if not code_field or not adb_fill_node(device, code_field, str(code)):
            sms_provider.release(number.activation_id, number.provider)
            continue
        nodes = adb_ui_nodes(device)
        verify_button = adb_find_node(nodes, texts=("Next", "Verify", "Continue"))
        if not verify_button or not adb_tap_node(device, verify_button):
            sms_provider.release(number.activation_id, number.provider)
            continue
        state, _ = adb_wait_phone_state(device, timeout=90)
        if state == "rejected":
            log(f"[adb-sms] Google rejected {full_number} after the SMS code; switching number")
            sms_provider.release(number.activation_id, number.provider)
            nodes, _ = adb_page_state(device)
            if not any(node.get("resource-id") == "phoneNumberId" for node in nodes):
                adb_run(device, "shell", "input", "keyevent", "KEYCODE_BACK", timeout=10)
                time.sleep(2)
            continue
        if state == "success":
            number.last_code = str(code)
            if lease_holder is not None:
                lease_holder.clear()
                lease_holder.append(number)
            return "phone_verified"
        sms_provider.release(number.activation_id, number.provider)
    return "phone_verification"


def adb_complete_post_phone_flow(device: str, accept_terms: bool) -> str:
    for _ in range(50):
        nodes, joined = adb_page_state(device)
        ok, activity = adb_run(device, "shell", "dumpsys", "activity", "activities", timeout=15)
        if ok and "conversationlistactivitygmail" in activity.lower():
            return "gmail_opened"
        if any(marker in joined for marker in ("search in mail", "compose", "open navigation drawer")):
            return "gmail_opened"
        if "privacy and terms" in joined:
            if not accept_terms:
                return "terms_waiting_for_user"
            button = adb_find_node(nodes, texts=("I agree",))
            if button:
                adb_tap_node(device, button)
            else:
                adb_run(device, "shell", "input", "swipe", "450", "1400", "450", "350", "700", timeout=10)
            time.sleep(3)
            continue
        if "google services" in joined:
            button = adb_find_node(nodes, texts=("ACCEPT",))
            if button:
                adb_tap_node(device, button)
            else:
                adb_run(device, "shell", "input", "swipe", "450", "1400", "450", "350", "700", timeout=10)
            time.sleep(3)
            continue
        for labels in (("Next",), ("TAKE ME TO GMAIL",), ("GOT IT",)):
            button = adb_find_node(nodes, texts=labels)
            if button:
                adb_tap_node(device, button)
                break
        else:
            if any(marker in joined for marker in ("verify it's you", "captcha", "security key", "passkey")):
                return "manual_verification"
            time.sleep(2)
    return "unknown_post_phone_step"


def enter_sms_code(driver: webdriver.Remote, code: str) -> bool:
    if input_edittext_index(driver, 0, code, timeout=15):
        return True
    return input_by_text_hint(driver, "code", code, timeout=10)


def wait_for_new_sms(phone, timeout: int = 180) -> str | None:
    if not phone:
        return None
    import sms_provider

    code = sms_provider.get_code(
        phone.activation_id,
        phone.provider,
        max_wait=timeout,
        interval=5,
        since=phone.last_code or None,
    )
    if code:
        phone.last_code = str(code)
    return code


def click_first_text(driver: webdriver.Remote, labels: tuple[str, ...], timeout: int = 3) -> bool:
    for label in labels:
        if click_or_tap_text(driver, label, contains=True, timeout=timeout):
            return True
    return False


def second_login_flow(driver: webdriver.Remote, account: Account, adb_port: int, phone=None) -> str:
    """Sign into Gmail after the Android account was removed from the device."""
    email = f"{account.username}@gmail.com"
    current_package = ""
    try:
        current_package = driver.current_package or ""
    except WebDriverException:
        pass
    # Preserve an in-progress Google security WebView when resuming. Relaunching
    # Gmail here invalidates a reCAPTCHA that was just completed.
    if "com.google.android.gms" not in current_package:
        try:
            driver.activate_app("com.google.android.gm")
            time.sleep(4)
            current_package = driver.current_package or current_package
        except WebDriverException:
            pass
    if current_package != "com.google.android.gm" and "com.google.android.gms" not in current_package:
        adb_run(
            f"127.0.0.1:{adb_port}",
            "shell",
            "am",
            "start",
            "-n",
            "com.google.android.gm/com.google.android.gm.ConversationListActivityGmail",
            timeout=20,
        )
        time.sleep(4)
    try:
        import bluestacks

        if email in bluestacks.account_names(adb_port):
            try:
                driver.activate_app("com.google.android.gm")
            except WebDriverException:
                pass
            proceed_gmail_onboarding(driver)
            return "second_login_ok"
    except Exception:
        pass

    proceed_gmail_onboarding(driver)
    texts = dump_state(driver, "second login: account")
    joined = normalized_text("\n".join(texts))
    login_or_challenge = (
        "sign in",
        "email or phone",
        "forgot email",
        "checking info",
        "password",
        "verification code",
        "enter the code",
        "verify it's you",
        "phone number",
    )
    if not any(marker in joined for marker in login_or_challenge):
        texts = wait_until_any(driver, list(login_or_challenge), timeout=90)
        joined = normalized_text("\n".join(texts))
    if "checking info" in joined:
        texts = wait_until_any(driver, ["Email or phone", "Forgot email", "Sign in"], timeout=90)
        joined = normalized_text("\n".join(texts))

    if "email or phone" in joined or "forgot email" in joined or "sign in" in joined:
        if not input_edittext_index(driver, 0, email, timeout=15):
            if not input_by_text_hint(driver, "Email or phone", email, timeout=10):
                return "second_login_manual"
        click_next(driver, timeout=20)

    password_sent = False
    phone_sent = False
    code_sent = False
    captcha_tried = False
    for _ in range(45):
        texts = dump_state(driver, "second login")
        joined = normalized_text("\n".join(texts))

        try:
            import bluestacks

            if email in bluestacks.account_names(adb_port):
                if "google services" not in joined and "take me to gmail" not in joined:
                    try:
                        driver.activate_app("com.google.android.gm")
                    except WebDriverException:
                        pass
                    proceed_gmail_onboarding(driver)
                    return "second_login_ok"
        except Exception:
            pass

        # Gmail can render its shell before Android AccountManager has committed
        # the account. Completion is confirmed only by account_names() above.

        if "couldn't sign you in" in joined or "wrong password" in joined or "invalid password" in joined:
            return "second_login_failed"

        # Google 风控直接封号(注册/二登被判为 bot):终态,无需人工/保号。
        if "account has been disabled" in joined or "account was disabled" in joined:
            log("Google disabled this account (flagged as bot/policy violation).")
            return "account_disabled"

        if "enter your password" in joined or ("password" in joined and "show password" in joined):
            if password_sent:
                return "second_login_failed"
            if not input_edittext_index(driver, 0, account.password, timeout=15):
                if not input_by_text_hint(driver, "Password", account.password, timeout=10):
                    return "second_login_manual"
            click_next(driver, timeout=20)
            password_sent = True
            continue

        if "google services" in joined:
            if click_button(driver, "ACCEPT", timeout=5):
                time.sleep(5)
                continue
            driver.swipe(450, 1450, 450, 350, 700)
            click_button(driver, "ACCEPT", timeout=5)
            continue

        if "take me to gmail" in joined:
            click_button(driver, "TAKE ME TO GMAIL", timeout=5)
            continue

        # Google may ask to confirm the registered number before sending a code.
        if "verify your phone number" in joined and "continue" in joined:
            if click_first_text(driver, ("Continue",), timeout=5):
                time.sleep(3)
                continue

        asks_for_phone = (
            "phone number" in joined
            and any(key in joined for key in ("verify", "confirm", "verification code", "robot"))
            and "enter the code" not in joined
        )
        if asks_for_phone:
            if not phone:
                return "second_login_phone_manual"
            if not phone_sent:
                if not enter_phone_number(driver, phone_full_number(phone)):
                    return "second_login_phone_manual"
                click_next(driver, timeout=20) or click_button(driver, "Send", timeout=5)
                phone_sent = True
                continue

        is_code_page = any(
            key in joined
            for key in ("enter the code", "verification code", "6-digit code", "we sent a code", "text message")
        ) and not asks_for_phone
        if is_code_page:
            if not phone:
                return "second_login_phone_manual"
            if code_sent:
                time.sleep(3)
                continue
            code = wait_for_new_sms(phone)
            if not code:
                return "second_login_phone_manual"
            if not enter_sms_code(driver, code):
                return "second_login_phone_manual"
            click_next(driver, timeout=20) or click_button(driver, "Verify", timeout=10)
            code_sent = True
            continue

        if any(key in joined for key in ("get a verification code", "send a text", "text message")):
            if click_first_text(driver, ("Get a verification code", "Send a text", "Text message", "Text")):
                continue

        if "skip" in joined and any(key in joined for key in ("recovery", "phone", "account security")):
            click_first_text(driver, ("Skip", "Not now"))
            continue

        if any(key in joined for key in ("confirm you're not a robot", "recaptcha", "i'm not a robot")):
            if not captcha_tried:
                captcha_tried = True
                try:
                    import recaptcha_android

                    if recaptcha_android.usable():
                        log("reCAPTCHA detected on Android second login; attempting vision auto-solve")
                        if recaptcha_android.solve(driver, adb_port):
                            log("reCAPTCHA solved; advancing second login")
                            click_next(driver, timeout=10)
                            time.sleep(3)
                            continue
                    else:
                        log("Vision solver unavailable (no key); manual handoff")
                except Exception as exc:
                    log(f"reCAPTCHA solve error: {exc}")
            return "second_login_manual"
        if any(key in joined for key in ("captcha", "security key", "passkey")):
            return "second_login_manual"
        if "verify it's you" in joined and not phone:
            return "second_login_manual"
        time.sleep(3)

    return "second_login_manual"


def enable_phone_2fa(driver: webdriver.Remote, adb_port: int, account: Account, phone=None) -> str:
    """Best-effort Google 2-Step Verification setup using the retained phone lease."""
    import bluestacks

    url = "https://myaccount.google.com/signinoptions/two-step-verification"
    ok, out, err = bluestacks.adb_sh(
        adb_port,
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        url,
        timeout=20,
    )
    if not ok:
        log(f"Could not open Google 2-Step Verification: {(out or err)[:120]}")
        return "two_factor_manual"

    password_sent = False
    phone_sent = False
    code_sent = False
    for _ in range(50):
        texts = dump_state(driver, "2FA setup")
        joined = "\n".join(texts).lower()
        if any(key in joined for key in ("2-step verification is on", "2-step verification on")):
            return "two_factor_enabled"

        if "enter your password" in joined or ("password" in joined and "show password" in joined):
            if password_sent:
                return "two_factor_manual"
            if not input_edittext_index(driver, 0, account.password, timeout=15):
                return "two_factor_manual"
            click_next(driver, timeout=15)
            password_sent = True
            continue

        if any(key in joined for key in ("get started", "start setup")):
            click_first_text(driver, ("Get started", "Start setup"), timeout=5)
            continue

        if "phone number" in joined and "verification code" not in joined:
            if not phone:
                return "two_factor_phone_manual"
            if not phone_sent:
                if not enter_phone_number(driver, phone_full_number(phone)):
                    return "two_factor_phone_manual"
                click_first_text(driver, ("Next", "Send", "Continue"), timeout=5)
                phone_sent = True
                continue

        if any(key in joined for key in ("enter the code", "verification code", "6-digit code")):
            if not phone:
                return "two_factor_phone_manual"
            if not code_sent:
                code = wait_for_new_sms(phone)
                if not code or not enter_sms_code(driver, code):
                    return "two_factor_phone_manual"
                click_first_text(driver, ("Next", "Verify", "Continue"), timeout=5)
                code_sent = True
                continue

        if any(key in joined for key in ("turn on", "enable")):
            if click_first_text(driver, ("Turn on", "Enable"), timeout=5):
                time.sleep(5)
                continue

        if any(key in joined for key in ("try another way", "security key", "passkey", "authenticator")):
            return "two_factor_manual"
        time.sleep(3)
    return "two_factor_manual"


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive Gmail account signup locally.")
    parser.add_argument("--server", default=APPIUM_SERVER)
    parser.add_argument("--device", default=ANDROID_DEVICE)
    parser.add_argument("--prefix", default=GMAIL_USERNAME_PREFIX)
    parser.add_argument("--instance", default=BLUESTACKS_INSTANCE)
    parser.add_argument("--adb-port", type=int, default=int(BLUESTACKS_ADB_PORT) if str(BLUESTACKS_ADB_PORT).isdigit() else None)
    parser.add_argument("--system-port", type=int, default=int(APPIUM_SYSTEM_PORT) if str(APPIUM_SYSTEM_PORT).isdigit() else None)
    parser.add_argument("--no-reset", action="store_true", default=True)
    parser.add_argument("--no-prepare-emulator", action="store_true", help="Skip BlueStacks start/connect and Google app-state cleanup.")
    parser.add_argument("--no-switch-node", action="store_true", help="Skip Clash node switching before a fresh run.")
    parser.add_argument("--keep-emulator", action="store_true", help="Do not close the BlueStacks instance after this run.")
    parser.add_argument("--stop-after-create-account", action="store_true")
    parser.add_argument(
        "--wait-phone-verification",
        action="store_true",
        help="Wait up to 15 minutes for manual phone/SMS verification, then continue.",
    )
    parser.add_argument(
        "--resume-after-phone",
        action="store_true",
        help="Attach to the current screen and continue after manual phone/SMS verification.",
    )
    parser.add_argument(
        "--resume-security",
        action="store_true",
        help="Resume a pending second-login or 2FA security stage from .runstate.",
    )
    parser.add_argument(
        "--auto-phone",
        action="store_true",
        default=(PHONE_VERIFICATION_MODE == "auto"),
        help="Automatically complete phone verification. Prefers a multi-SMS sms-man lease, "
        "then falls back to firefox.fun or hero-sms.",
    )
    parser.add_argument(
        "--second-login",
        action=argparse.BooleanOptionalAction,
        default=SECOND_LOGIN_AFTER_SIGNUP,
        help="Remove the new Android account and sign in again inside BlueStacks (default: enabled).",
    )
    parser.add_argument(
        "--enable-2fa",
        action="store_true",
        default=ENABLE_2FA_AFTER_LOGIN,
        help="After a successful second login, explicitly opt in to phone-based Google 2-Step Verification.",
    )
    parser.add_argument(
        "--accept-terms",
        action="store_true",
        default=ACCEPT_TERMS,
        help="Click I agree on Privacy and Terms and ACCEPT on Google services.",
    )
    args = parser.parse_args()
    if args.auto_phone:
        args.accept_terms = True
    if args.enable_2fa:
        args.second_login = True

    import bluestacks

    instance = args.instance or bluestacks.default_instance()
    adb_port = args.adb_port or bluestacks.parse_adb_port(args.device)
    run_registered = False
    emulator_prepared = False
    result = ""

    account = None
    retained_phone = None
    pending_stage = ""
    if args.resume_after_phone or args.resume_security:
        account, retained_phone, pending_stage = load_account_state()
        log(f"Resuming from the current emulator screen (saved stage: {pending_stage or 'unknown'}).")
        if args.resume_security and account is None:
            log(f"No pending account state found at {STATE_PATH}")
            return 1
    else:
        account = generate_account(args.prefix)
        save_account_state(account, "signup")
        log("Generated account:")
        log(f"  email: {account.username}@gmail.com")
        log(f"  password: {account.password}")
        log(f"  name: {account.first_name} {account.last_name}")
        log(f"  birthday: {account.month} {account.day}, {account.year}")
        if args.auto_phone:
            log("The script will auto-complete phone verification and final terms pages.")
            log("SMS provider: firefox.fun primary, sms-man and hero-sms fallback.")
            log("CAPTCHA and any additional Google security checks still require manual handling.")
        elif args.wait_phone_verification:
            log("The script will wait at phone/SMS/CAPTCHA verification and continue after you complete it manually.")
        else:
            log("The script will stop at phone/SMS/CAPTCHA verification.")

    driver = None
    appium_preinstalled = False
    lease_holder = [retained_phone] if retained_phone else []
    try:
        if args.resume_after_phone or args.resume_security:
            log("Skipping emulator preparation while resuming an in-progress flow.")
        elif AUTO_PREPARE_EMULATOR and not args.no_prepare_emulator:
            if adb_port is None:
                raise RuntimeError("Could not determine adb port from --device; pass --adb-port explicitly.")
            try:
                import coordinator

                run_registered = True
                is_first = coordinator.begin_task(instance)
            except Exception as exc:
                is_first = True
                log(f"Run-state marker unavailable; continuing without coordination: {exc}")
            if AUTO_SWITCH_NODE and not args.no_switch_node:
                if is_first:
                    try:
                        import proxy_switch

                        proxy_switch.switch_random_node(log=log)
                    except Exception as exc:
                        log(f"Node switch error ignored: {exc}")
                else:
                    log("Another local Gmail run is active; keeping the current Clash node.")
            args.device = bluestacks.prepare_instance(instance, adb_port)
            emulator_prepared = True
        elif AUTO_SWITCH_NODE and not args.no_switch_node and not args.resume_after_phone and not args.resume_security:
            try:
                import proxy_switch

                proxy_switch.switch_random_node(log=log)
            except Exception as exc:
                log(f"Node switch error ignored: {exc}")

        if AUTO_START_APPIUM:
            ensure_appium_server(args.server)

        if emulator_prepared and adb_port is not None:
            bluestacks.install_appium_packages(adb_port)
            appium_preinstalled = bluestacks.appium_server_packages_ready(adb_port)
            if not appium_preinstalled:
                log("warning: Appium server packages are missing before session; Appium may try its bundled installer.")

        try:
            driver = make_driver(
                args.server,
                args.device,
                args.no_reset,
                launch_gmail=not (args.resume_after_phone or args.resume_security),
                system_port=args.system_port,
                skip_server_install=appium_preinstalled,
            )
        except WebDriverException as exc:
            message = str(exc).lower()
            retryable_appium = any(
                needle in message
                for needle in (
                    "uiautomator2",
                    "instrumentation",
                    "failed to install",
                    "socket hang up",
                    "cannot be proxied",
                    "appium settings app",
                )
            )
            if not (emulator_prepared and adb_port is not None and retryable_appium):
                raise
            log("Appium session init failed; reinstalling Appium server packages and retrying once.")
            args.device = bluestacks.ensure_instance(instance, adb_port, wait_boot=True)
            bluestacks.install_appium_packages(adb_port)
            time.sleep(2)
            for _ in range(2):
                args.device = bluestacks.ensure_instance(instance, adb_port, wait_boot=True)
                if bluestacks.appium_server_packages_ready(adb_port):
                    break
                log("Appium server packages are missing after reconnect; reinstalling.")
                bluestacks.install_appium_packages(adb_port)
                time.sleep(2)
            if not bluestacks.appium_server_packages_ready(adb_port):
                log("warning: Appium server packages still missing before retry session.")
            driver = make_driver(
                args.server,
                args.device,
                args.no_reset,
                launch_gmail=not (args.resume_after_phone or args.resume_security),
                system_port=args.system_port,
                skip_server_install=True,
            )
        if args.resume_security:
            if account is None or adb_port is None:
                raise RuntimeError("Pending account state and adb port are required to resume security flow")
            if pending_stage.startswith("two_factor"):
                result = enable_phone_2fa(driver, adb_port, account, retained_phone)
            elif pending_stage == "second_login_remove_account":
                driver.quit()
                driver = None
                email = f"{account.username}@gmail.com"
                if email in bluestacks.account_names(adb_port):
                    if not bluestacks.remove_account_for_relogin(adb_port, email):
                        result = "second_login_manual"
                    else:
                        result = "second_login"
                else:
                    result = "second_login"
                if result == "second_login":
                    save_account_state(account, "second_login", retained_phone)
                    driver = make_driver(
                        args.server,
                        args.device,
                        args.no_reset,
                        launch_gmail=True,
                        system_port=args.system_port,
                        skip_server_install=True,
                    )
                    result = second_login_flow(driver, account, adb_port, retained_phone)
            else:
                result = second_login_flow(driver, account, adb_port, retained_phone)
        elif args.resume_after_phone:
            result = resume_registration_flow(
                driver,
                accept_terms=args.accept_terms,
                auto_phone=args.auto_phone or PHONE_VERIFICATION_MODE == "auto",
                lease_holder=lease_holder,
            )
        else:
            if account is None:
                raise RuntimeError("Account generation failed")
            result = create_account_flow(
                driver,
                account,
                stop_after_create_account=args.stop_after_create_account,
                wait_phone_verification=args.wait_phone_verification,
                accept_terms=args.accept_terms,
                auto_phone=args.auto_phone or PHONE_VERIFICATION_MODE == "auto",
                lease_holder=lease_holder,
            )
        if result == "gmail_opened":
            result = post_login_manual_check(driver)

        retained_phone = lease_holder[0] if lease_holder else retained_phone
        if account and result == "gmail_opened" and args.second_login:
            if adb_port is None:
                raise RuntimeError("Android second login requires an adb port")
            save_account_state(account, "second_login_remove_account", retained_phone)
            driver.quit()
            driver = None
            email = f"{account.username}@gmail.com"
            log(f"Starting Android second login for {email}")
            if not bluestacks.remove_account_for_relogin(adb_port, email):
                result = "second_login_manual"
            else:
                save_account_state(account, "second_login", retained_phone)
                driver = make_driver(
                    args.server,
                    args.device,
                    args.no_reset,
                    launch_gmail=True,
                    system_port=args.system_port,
                    skip_server_install=True,
                )
                result = second_login_flow(driver, account, adb_port, retained_phone)

        if account and result == "second_login_ok" and args.enable_2fa:
            if adb_port is None:
                raise RuntimeError("2FA setup requires an adb port")
            save_account_state(account, "two_factor", retained_phone)
            result = enable_phone_2fa(driver, adb_port, account, retained_phone)

        completed_results = {
            "gmail_opened",
            "second_login_ok",
            "two_factor_enabled",
        }
        if account and result in completed_results:
            append_completed_account(account, result)
            try:
                STATE_PATH.unlink(missing_ok=True)
            except OSError:
                pass
        elif account:
            save_account_state(account, result, retained_phone)
        log(f"\nResult: {result}")
        if account:
            log(f"Email: {account.username}@gmail.com")
            log(f"Password: {account.password}")
        if result == "phone_verification":
            log("Please complete phone verification manually in the emulator, then continue from the UI.")
            log("After verification, run again with --resume-after-phone. Add --accept-terms if you want the script to finish the consent pages.")
        elif result == "manual_verification":
            log("Google is asking for an additional manual verification step. Complete it in the emulator, then re-run with --resume-after-phone.")
        elif result == "terms_waiting_for_user":
            log("Privacy and Terms is waiting for confirmation. Re-run with --resume-after-phone --accept-terms to continue.")
        elif result == "account_disabled":
            log("Google DISABLED this account during second-login verification (flagged as bot/policy).")
            log("This is Google's risk engine, not a script failure. The SMS lease was released and no")
            log("handoff is kept. To improve survival: residential proxy, aged/warmed device, avoid")
            log("high-risk SMS country pools, and space out registrations.")
        elif result in MANUAL_HANDOFF_RESULTS:
            log("Manual handoff required in the emulator; it will stay open for the security step.")
            log("After finishing it manually, re-run with --resume-security to continue from the saved account state.")
        return 0
    except WebDriverException as exc:
        retained_phone = lease_holder[0] if lease_holder else retained_phone
        if account:
            save_account_state(account, result or "webdriver_error", retained_phone)
        log(f"Appium/WebDriver error: {exc}")
        return 2
    except Exception as exc:
        retained_phone = lease_holder[0] if lease_holder else retained_phone
        if account:
            save_account_state(account, result or "error", retained_phone)
        log(f"Error: {exc}")
        return 1
    finally:
        retained_phone = lease_holder[0] if lease_holder else retained_phone
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        if run_registered:
            try:
                import coordinator

                coordinator.end_task(instance)
            except Exception:
                pass
        if retained_phone and result not in MANUAL_HANDOFF_RESULTS:
            try:
                import sms_provider

                sms_provider.release(retained_phone.activation_id, retained_phone.provider)
            except Exception as exc:
                log(f"Could not release retained SMS lease: {exc}")
        keep_for_handoff = (
            result in MANUAL_HANDOFF_RESULTS
            and KEEP_EMULATOR_ON_MANUAL_HANDOFF
        )
        if keep_for_handoff:
            log("Keeping emulator open for manual handoff.")
        if emulator_prepared and AUTO_STOP_EMULATOR and not args.keep_emulator and not keep_for_handoff:
            try:
                bluestacks.stop_instance(instance, adb_port)
            except Exception as exc:
                log(f"Could not stop emulator: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
