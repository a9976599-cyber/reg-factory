import os
from pathlib import Path


def load_dotenv(path: str | None = None) -> None:
    candidates = [Path(path)] if path else [Path(__file__).parents[1] / ".env", Path(__file__).with_name(".env")]
    env_path = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
    if not env_path:
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: str = "0") -> bool:
    return env(name, default).lower() in {"1", "true", "yes", "on"}


APPIUM_SERVER = env("APPIUM_SERVER", "http://127.0.0.1:4723")
APPIUM_SYSTEM_PORT = env("APPIUM_SYSTEM_PORT", "")
ANDROID_DEVICE = env("ANDROID_DEVICE", "127.0.0.1:5675")
ADB_PATH = env("ADB_PATH", "adb")
GMAIL_USERNAME_PREFIX = env("GMAIL_USERNAME_PREFIX", "")
PHONE_VERIFICATION_MODE = env("PHONE_VERIFICATION_MODE", "manual").lower()
ACCEPT_TERMS = env("ACCEPT_TERMS", "0").lower() in {"1", "true", "yes", "on"}
SECOND_LOGIN_AFTER_SIGNUP = env_bool("SECOND_LOGIN_AFTER_SIGNUP", "1")
ENABLE_2FA_AFTER_LOGIN = env_bool("ENABLE_2FA_AFTER_LOGIN", "0")
# 二登 reCAPTCHA 用视觉 LLM(common/agent_captcha)自动解;失败仍回退人工。
RECAPTCHA_AUTO_SOLVE = env_bool("RECAPTCHA_AUTO_SOLVE", "1")
RECAPTCHA_SOLVE_ROUNDS = env("RECAPTCHA_SOLVE_ROUNDS", "8")

AUTO_PREPARE_EMULATOR = env_bool("AUTO_PREPARE_EMULATOR", "1")
AUTO_START_APPIUM = env_bool("AUTO_START_APPIUM", "1")
AUTO_SWITCH_NODE = env_bool("AUTO_SWITCH_NODE", "1")
AUTO_STOP_EMULATOR = env_bool("AUTO_STOP_EMULATOR", "1")
KEEP_EMULATOR_ON_MANUAL_HANDOFF = env_bool("KEEP_EMULATOR_ON_MANUAL_HANDOFF", "1")
BLUESTACKS_INSTANCE = env("BLUESTACKS_INSTANCE", "")
BLUESTACKS_ADB_PORT = env("BLUESTACKS_ADB_PORT", "")

SMS_API_BASE = env("SMS_API_BASE", "http://www.firefox.fun/yhapi.ashx")
SMS_TOKEN = env("SMS_TOKEN", "")
SMS_PROJECT_ID_GMAIL = env("SMS_PROJECT_ID_GMAIL", "")
SMS_MAXPRICE_GMAIL = env("SMS_MAXPRICE_GMAIL", "20")
SMS_COUNTRY_BLACKLIST_GMAIL = [
    item.strip() for item in env("SMS_COUNTRY_BLACKLIST_GMAIL", "261,63").split(",") if item.strip()
]
# 可选国家白名单(逗号分隔 firefox.fun 国家码, 如 "33,44" = 法国/英国).
# 空字符串表示不限国家(原有行为).
SMS_COUNTRY_GMAIL = [
    item.strip() for item in env("SMS_COUNTRY_GMAIL", "").split(",") if item.strip()
]

HERO_SMS_API_BASE = env("HERO_SMS_API_BASE", "https://hero-sms.com/stubs/handler_api.php")
HERO_SMS_API_KEY = env("HERO_SMS_API_KEY", "")
HERO_SMS_SERVICE_GMAIL = env("HERO_SMS_SERVICE_GMAIL", "")

SMSMAN_API_BASE = env("SMSMAN_API_BASE", "https://api.sms-man.com/control")
SMSMAN_TOKEN = env("SMSMAN_TOKEN", "")
SMSMAN_APP_ID_GMAIL = env("SMSMAN_APP_ID_GMAIL", "google")
SMSMAN_COUNTRY_GMAIL = env("SMSMAN_COUNTRY_GMAIL", "0")
SMSMAN_MAXPRICE_GMAIL = env("SMSMAN_MAXPRICE_GMAIL", "")
SMSMAN_BLACKLIST_GMAIL = [
    item.strip() for item in env("SMSMAN_BLACKLIST_GMAIL", "").split(",") if item.strip()
]
