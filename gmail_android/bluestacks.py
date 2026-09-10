# -*- coding: utf-8 -*-
"""BlueStacks lifecycle and Google app-state cleanup helpers."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

try:
    from config import load_dotenv

    load_dotenv()
except Exception:
    pass


HD_PLAYER = os.environ.get("BLUESTACKS_HD_PLAYER") or r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"
ADB_PATH = os.environ.get("ADB_PATH") or "adb"
BOOT_WAIT = int(os.environ.get("BLUESTACKS_BOOT_WAIT", "35"))
CONNECT_ATTEMPTS = int(os.environ.get("BLUESTACKS_CONNECT_ATTEMPTS", "10"))
CONNECT_RETRY_SLEEP = float(os.environ.get("BLUESTACKS_CONNECT_RETRY_SLEEP", "6"))
ADB_ALLOW_KILL_SERVER = os.environ.get("ADB_ALLOW_KILL_SERVER", "0").lower() in {"1", "true", "yes", "on"}

GOOGLE_PKGS = (
    "com.google.android.gms",
    "com.google.android.gsf",
    "com.google.android.gm",
    "com.android.vending",
)

APPIUM_PKGS = (
    "io.appium.uiautomator2.server",
    "io.appium.uiautomator2.server.test",
    "io.appium.settings",
)

APPIUM_SERVER_PKGS = (
    "io.appium.uiautomator2.server",
    "io.appium.uiautomator2.server.test",
)


def log(message: str) -> None:
    print(f"[bluestacks] {message}", flush=True)


def run_cmd(args: list[str], timeout: int = 30) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode == 0, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError as exc:
        return False, "", f"command not found: {args[0]} ({exc})"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else "timeout"
        return False, stdout.strip(), stderr.strip()


def adb(*args: str, timeout: int = 30) -> tuple[bool, str, str]:
    return run_cmd([ADB_PATH, *args], timeout=timeout)


def parse_adb_port(device: str | None, default: int | None = None) -> int | None:
    if device:
        match = re.search(r":(\d+)$", str(device).strip())
        if match:
            return int(match.group(1))
    if default is not None:
        return default
    raw_pool = os.environ.get("GQ_POOL", "").strip()
    if raw_pool:
        first = raw_pool.split(",", 1)[0].strip()
        _, _, port = first.partition(":")
        if port.strip().isdigit():
            return int(port.strip())
    raw_port = os.environ.get("BLUESTACKS_ADB_PORT", "").strip()
    if raw_port.isdigit():
        return int(raw_port)
    raw_device = os.environ.get("ANDROID_DEVICE", "").strip()
    if raw_device and raw_device != device:
        return parse_adb_port(raw_device, default)
    return None


def default_instance() -> str:
    value = os.environ.get("BLUESTACKS_INSTANCE", "").strip()
    if value:
        return value
    raw_pool = os.environ.get("GQ_POOL", "").strip()
    if raw_pool:
        first = raw_pool.split(",", 1)[0].strip()
        name, _, _ = first.partition(":")
        if name.strip():
            return name.strip()
    return "Pie64"


def udid(adb_port: int) -> str:
    return f"127.0.0.1:{adb_port}"


def adb_sh(adb_port: int, *args: str, timeout: int = 30) -> tuple[bool, str, str]:
    return adb("-s", udid(adb_port), "shell", *args, timeout=timeout)


def _device_online(adb_port: int) -> bool:
    ok, out, _ = adb("devices", timeout=10)
    if not ok:
        return False
    target = udid(adb_port)
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == target and parts[1] == "device":
            return True
    return False


def _boot_completed(adb_port: int) -> bool:
    ok, out, _ = adb_sh(adb_port, "getprop", "sys.boot_completed", timeout=10)
    return ok and out.strip() == "1"


def _active_count() -> int:
    try:
        import coordinator

        return coordinator.active_count()
    except Exception:
        return 0


def _connect_with_retry(adb_port: int, attempts: int | None = None) -> bool:
    target = udid(adb_port)
    attempts = attempts or CONNECT_ATTEMPTS
    for attempt in range(1, attempts + 1):
        ok, out, err = adb("connect", target, timeout=20)
        blob = f"{out} {err}".lower()
        if ok and ("connected" in blob or "already" in blob) and "10061" not in blob and "refused" not in blob:
            if _device_online(adb_port):
                log(f"adb connected: {target}")
                return True
        log(f"adb connect attempt {attempt}/{attempts} not ready: {out or err}")
        adb("disconnect", target, timeout=10)
        if ADB_ALLOW_KILL_SERVER or _active_count() <= 1:
            adb("kill-server", timeout=10)
        adb("start-server", timeout=15)
        time.sleep(CONNECT_RETRY_SLEEP)
    return False


def _pid_on_port(adb_port: int) -> int | None:
    script = (
        f"$c = Get-NetTCPConnection -LocalPort {adb_port} -State Listen -ErrorAction SilentlyContinue; "
        "foreach ($x in $c) { $p = Get-Process -Id $x.OwningProcess -ErrorAction SilentlyContinue; "
        "if ($p -and $p.ProcessName -eq 'HD-Player') { $x.OwningProcess } }"
    )
    ok, out, _ = run_cmd(["powershell", "-NoProfile", "-Command", script], timeout=20)
    if not ok or not out:
        return None
    for token in out.split():
        if token.strip().isdigit():
            return int(token)
    return None


def _instance_pids(instance: str, adb_port: int | None = None) -> list[int]:
    if adb_port is not None:
        pid = _pid_on_port(adb_port)
        if pid:
            return [pid]
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='HD-Player.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*--instance*{instance}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    ok, out, _ = run_cmd(["powershell", "-NoProfile", "-Command", script], timeout=20)
    if not ok or not out:
        return []
    return [int(token) for token in out.split() if token.strip().isdigit()]


def ensure_instance(instance: str | None = None, adb_port: int | None = None, wait_boot: bool = True) -> str:
    instance = instance or default_instance()
    if adb_port is None:
        raise RuntimeError("adb port is required to start/connect BlueStacks")
    target = udid(adb_port)
    if _device_online(adb_port):
        log(f"instance {instance} already online at {target}")
        return target

    if not os.path.exists(HD_PLAYER):
        raise RuntimeError(f"HD-Player.exe not found at {HD_PLAYER}; set BLUESTACKS_HD_PLAYER")

    if not _instance_pids(instance, adb_port):
        log(f"launching instance {instance}")
        subprocess.Popen([HD_PLAYER, "--instance", instance])
        log(f"waiting {BOOT_WAIT}s for boot")
        time.sleep(BOOT_WAIT)
    else:
        log(f"instance {instance} process already running; reconnecting adb")

    if not _connect_with_retry(adb_port):
        log(f"adb did not become ready for {target}; relaunching {instance} once")
        stop_instance(instance, adb_port)
        subprocess.Popen([HD_PLAYER, "--instance", instance])
        log(f"waiting {BOOT_WAIT}s for boot after relaunch")
        time.sleep(BOOT_WAIT)
        if not _connect_with_retry(adb_port):
            raise RuntimeError(f"could not adb-connect to {target} for instance {instance}")

    if wait_boot:
        for _ in range(30):
            if _boot_completed(adb_port):
                break
            time.sleep(3)
        else:
            log("warning: sys.boot_completed != 1 after wait; continuing")
    return target


def stop_instance(instance: str | None = None, adb_port: int | None = None) -> None:
    instance = instance or default_instance()
    if adb_port is None:
        raise RuntimeError("adb port is required to stop BlueStacks")
    target = udid(adb_port)
    adb("disconnect", target, timeout=10)
    pids = _instance_pids(instance, adb_port)
    for pid in pids:
        log(f"killing HD-Player pid {pid} ({instance}:{adb_port})")
        run_cmd(["taskkill", "/F", "/PID", str(pid)], timeout=15)
    if not pids:
        log(f"no HD-Player process found for {instance}:{adb_port}")
    for _ in range(30):
        if not _instance_pids(instance, adb_port):
            break
        time.sleep(1)
    time.sleep(3)


def clean_appium_packages(adb_port: int) -> None:
    target = udid(adb_port)
    for package in APPIUM_PKGS:
        ok, out, err = adb("-s", target, "uninstall", package, timeout=45)
        msg = (out or err).strip()
        if "Success" in msg:
            log(f"uninstalled stale Appium package {package}")
    time.sleep(3)


def _patched_appium_apk_paths() -> list[Path]:
    runstate = Path(__file__).with_name(".runstate")
    server = sorted(runstate.glob("appium-uiautomator2-server-v*-resigned.apk"))
    test = runstate / "appium-uiautomator2-server-debug-androidTest-patched.apk"
    if server and test.is_file():
        return [server[-1], test]
    return []


def _appium_apk_paths() -> list[Path]:
    patched = _patched_appium_apk_paths()
    if patched:
        return patched
    server_apks_dir = (
        Path.home()
        / ".appium"
        / "node_modules"
        / "appium-uiautomator2-driver"
        / "node_modules"
        / "appium-uiautomator2-server"
        / "apks"
    )
    apks: list[Path] = []
    if server_apks_dir.is_dir():
        server = sorted(server_apks_dir.glob("appium-uiautomator2-server-v*.apk"))
        test = sorted(server_apks_dir.glob("appium-uiautomator2-server-debug-androidTest.apk"))
        apks.extend(([server[-1]] if server else []) + ([test[-1]] if test else []))
    return apks


def install_appium_packages(adb_port: int) -> None:
    target = udid(adb_port)
    apks = _appium_apk_paths()
    if not apks:
        log("Appium UiAutomator2 APKs not found; Appium will try its bundled installer")
        return
    if _patched_appium_apk_paths():
        for package in APPIUM_SERVER_PKGS:
            adb("-s", target, "uninstall", package, timeout=45)
        log("using patched Appium UiAutomator2 server APKs for BlueStacks")
    for apk in apks:
        args = ["-s", target, "install", "--no-streaming", "-r"]
        if "androidTest" in apk.name:
            args.append("-t")
        args.append(str(apk))
        ok, out, err = adb(*args, timeout=90)
        msg = (out or err).strip()
        if ok and "Success" in msg:
            log(f"installed Appium package {apk.name}")
        elif not ok:
            log(f"Appium package install failed for {apk.name}: {msg[:120]}")


def appium_server_packages_ready(adb_port: int) -> bool:
    ok, out, _ = adb_sh(adb_port, "pm", "list", "packages", timeout=20)
    if not ok:
        return False
    return (
        "package:io.appium.uiautomator2.server" in out
        and "package:io.appium.uiautomator2.server.test" in out
    )


def _list_accounts(adb_port: int) -> list[str]:
    ok, out, _ = adb_sh(adb_port, "dumpsys", "account", timeout=20)
    if not ok:
        return []
    return [line for line in out.splitlines() if "Account {name=" in line]


def account_names(adb_port: int) -> list[str]:
    names = []
    for line in _list_accounts(adb_port):
        match = re.search(r"Account \{name=([^,}]+)", line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _dump_nodes(adb_port: int) -> list[tuple[str, int, int]]:
    adb_sh(adb_port, "uiautomator", "dump", "/sdcard/_reset.xml", timeout=20)
    ok, out, _ = adb("-s", udid(adb_port), "exec-out", "cat", "/sdcard/_reset.xml", timeout=15)
    if not ok:
        return []
    nodes = []
    for match in re.finditer(r"<node[^>]*>", out):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', match.group(0)))
        text = (attrs.get("text") or attrs.get("content-desc") or "").strip()
        bounds = list(map(int, re.findall(r"\d+", attrs.get("bounds", ""))))
        if text and len(bounds) == 4:
            nodes.append((text, (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2))
    return nodes


def _tap(adb_port: int, x: int, y: int) -> None:
    adb_sh(adb_port, "input", "tap", str(x), str(y), timeout=10)


def remove_all_accounts(adb_port: int, max_rounds: int = 8, account_name: str = "") -> int:
    removed = 0
    for _ in range(max_rounds):
        names = account_names(adb_port)
        if not names or (account_name and account_name not in names):
            break
        adb_sh(adb_port, "am", "start", "-a", "android.settings.SYNC_SETTINGS", timeout=15)
        time.sleep(2.5)
        nodes = _dump_nodes(adb_port)
        account_row = next(
            (
                (x, y)
                for text, x, y in nodes
                if text == account_name or (not account_name and "@" in text)
            ),
            None,
        )
        if not account_row:
            log("account row not found in settings UI")
            break
        _tap(adb_port, *account_row)
        time.sleep(2)
        nodes = _dump_nodes(adb_port)
        remove_button = next(((x, y) for text, x, y in nodes if text.upper() == "REMOVE ACCOUNT"), None)
        if not remove_button:
            log("REMOVE ACCOUNT button not found")
            break
        _tap(adb_port, *remove_button)
        time.sleep(2)
        nodes = _dump_nodes(adb_port)
        confirms = [(x, y) for text, x, y in nodes if text.upper() == "REMOVE ACCOUNT"]
        if confirms:
            _tap(adb_port, *max(confirms, key=lambda item: item[1]))
            time.sleep(3)
        removed += 1
        log(f"removed an account via UI (total {removed})")
    if removed:
        adb_sh(adb_port, "input", "keyevent", "KEYCODE_HOME", timeout=10)
    return removed


def remove_account_for_relogin(adb_port: int, account_name: str) -> bool:
    """Remove one Android account without clearing Play Services sign-in state."""
    if account_name not in account_names(adb_port):
        log(f"account is not present before second login: {account_name}")
        return False
    removed = remove_all_accounts(adb_port, max_rounds=2, account_name=account_name)
    if account_name in account_names(adb_port):
        log(f"account still present after removal attempt: {account_name}")
        return False
    # Reset only Gmail onboarding. Clearing GMS/GSF here would turn a second login
    # into another factory-fresh registration environment.
    adb_sh(adb_port, "am", "force-stop", "com.google.android.gm", timeout=20)
    ok, out, err = adb_sh(adb_port, "pm", "clear", "com.google.android.gm", timeout=60)
    log(f"pm clear com.google.android.gm for second login: {(out or err).strip()[:80]}")
    return removed > 0 and ok


def factory_reset_google(adb_port: int) -> int:
    for package in GOOGLE_PKGS:
        adb_sh(adb_port, "am", "force-stop", package, timeout=20)
    log("force-stopped Google/Gmail apps")
    remove_all_accounts(adb_port)
    for package in GOOGLE_PKGS:
        ok, out, err = adb_sh(adb_port, "pm", "clear", package, timeout=60)
        log(f"pm clear {package}: {(out or err).strip()[:80]}")
    # pm clear can restart Play Services components; force-stop again so Appium
    # does not attach to a stale Google sign-in activity from the previous run.
    for package in GOOGLE_PKGS:
        adb_sh(adb_port, "am", "force-stop", package, timeout=20)
    adb_sh(adb_port, "am", "kill-all", timeout=20)
    adb_sh(adb_port, "input", "keyevent", "KEYCODE_HOME", timeout=10)
    remaining = _list_accounts(adb_port)
    log(f"accounts remaining after reset: {len(remaining)}")
    for line in remaining:
        log(f"  still present: {line.strip()[:100]}")
    return len(remaining)


def prepare_instance(instance: str | None = None, adb_port: int | None = None) -> str:
    instance = instance or default_instance()
    if adb_port is None:
        raise RuntimeError("adb port is required to prepare BlueStacks")
    log(f"preparing fresh instance {instance}:{adb_port}")
    target = ensure_instance(instance, adb_port, wait_boot=True)
    factory_reset_google(adb_port)
    log(f"instance {instance} ready and clean at {target}")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BlueStacks lifecycle/reset control")
    parser.add_argument("action", choices=["start", "stop", "status", "reset", "prepare", "clean-appium"])
    parser.add_argument("--instance", default=default_instance())
    parser.add_argument("--port", type=int, default=parse_adb_port(None, 5675))
    args = parser.parse_args()

    if args.action == "start":
        print(ensure_instance(args.instance, args.port))
    elif args.action == "stop":
        stop_instance(args.instance, args.port)
    elif args.action == "reset":
        count = factory_reset_google(args.port)
        print("RESET OK, accounts removed (0 remaining)" if count == 0 else f"RESET done but {count} account(s) still present")
    elif args.action == "prepare":
        print(prepare_instance(args.instance, args.port))
    elif args.action == "clean-appium":
        clean_appium_packages(args.port)
    else:
        print(
            "online" if _device_online(args.port) else "offline",
            udid(args.port),
            "boot_completed" if _boot_completed(args.port) else "",
        )
