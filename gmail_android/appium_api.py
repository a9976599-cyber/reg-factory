import argparse
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_UDID = "127.0.0.1:5555"
DEFAULT_APPIUM_URL = "http://127.0.0.1:4723"
APPIUM_PACKAGES = (
    "io.appium.uiautomator2.server",
    "io.appium.uiautomator2.server.test",
    "io.appium.settings",
)


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def run_cmd(args: list[str], timeout: int = 30) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "elapsedMs": int((time.time() - started) * 1000),
            "command": args,
        }
    except FileNotFoundError as exc:
        raise ApiError(500, f"Command not found: {args[0]}", str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "elapsedMs": int((time.time() - started) * 1000),
            "command": args,
            "timeout": True,
        }


def adb_args(server: "ApiServer", *args: str, udid: str | None = None) -> list[str]:
    cmd = [server.adb_path]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(args)
    return cmd


def appium_request(
    server: "ApiServer",
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    url = server.appium_url.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else None
            return {
                "ok": 200 <= resp.status < 300,
                "status": resp.status,
                "body": parsed,
                "elapsedMs": int((time.time() - started) * 1000),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return {
            "ok": False,
            "status": exc.code,
            "body": parsed,
            "elapsedMs": int((time.time() - started) * 1000),
        }
    except urllib.error.URLError as exc:
        raise ApiError(502, f"Cannot reach Appium server at {server.appium_url}", str(exc)) from exc
    except TimeoutError as exc:
        raise ApiError(504, "Appium request timed out", str(exc)) from exc


def local_request(method: str, url: str, body: dict[str, Any] | None = None, timeout: int = 15) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {
                "status": resp.status,
                "body": json.loads(raw) if raw else None,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else raw
        except json.JSONDecodeError:
            parsed = raw
        return {"status": exc.code, "body": parsed}


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "Request body must be valid JSON", str(exc)) from exc
    if not isinstance(value, dict):
        raise ApiError(400, "Request body must be a JSON object")
    return value


def base_caps(udid: str) -> dict[str, Any]:
    return {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": "BlueStacks",
        "appium:udid": udid,
        "appium:noReset": True,
        "appium:fullReset": False,
        "appium:autoGrantPermissions": False,
        "appium:newCommandTimeout": 120,
        "appium:adbExecTimeout": 120000,
        "appium:uiautomator2ServerInstallTimeout": 180000,
        "appium:uiautomator2ServerLaunchTimeout": 180000,
        "appium:ignoreHiddenApiPolicyError": True,
    }


class ApiServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        adb_path: str,
        appium_url: str,
        default_udid: str,
    ):
        super().__init__(server_address, handler_class)
        self.adb_path = adb_path
        self.appium_url = appium_url
        self.default_udid = default_udid


class Handler(BaseHTTPRequestHandler):
    server: ApiServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self.send_json(200, {"ok": True})

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def do_DELETE(self) -> None:
        self.handle_request("DELETE")

    def handle_request(self, method: str) -> None:
        try:
            result = self.route(method)
            self.send_json(200, {"ok": True, **result})
        except ApiError as exc:
            self.send_json(
                exc.status,
                {"ok": False, "error": exc.message, "detail": exc.detail},
            )
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def route(self, method: str) -> dict[str, Any]:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if method == "GET" and path == "/health":
            return {
                "service": "appium-api",
                "adbPath": self.server.adb_path,
                "appiumUrl": self.server.appium_url,
                "defaultUdid": self.server.default_udid,
            }

        if method == "GET" and path == "/adb/devices":
            return {"result": run_cmd(adb_args(self.server, "devices"), timeout=10)}

        if method == "POST" and path == "/adb/connect":
            body = read_json(self)
            udid = body.get("udid") or self.server.default_udid
            return {"result": run_cmd(adb_args(self.server, "connect", udid), timeout=20)}

        if method == "GET" and path == "/device/status":
            udid = query.get("udid", [self.server.default_udid])[0]
            return {
                "udid": udid,
                "devices": run_cmd(adb_args(self.server, "devices"), timeout=10),
                "sdk": run_cmd(adb_args(self.server, "shell", "getprop", "ro.build.version.sdk", udid=udid), timeout=10),
                "release": run_cmd(adb_args(self.server, "shell", "getprop", "ro.build.version.release", udid=udid), timeout=10),
                "settings": run_cmd(adb_args(self.server, "shell", "pm", "path", "io.appium.settings", udid=udid), timeout=10),
                "uia2Server": run_cmd(adb_args(self.server, "shell", "pm", "path", "io.appium.uiautomator2.server", udid=udid), timeout=10),
                "uia2Test": run_cmd(adb_args(self.server, "shell", "pm", "path", "io.appium.uiautomator2.server.test", udid=udid), timeout=10),
            }

        if method == "POST" and path == "/appium/packages/clean":
            body = read_json(self)
            udid = body.get("udid") or self.server.default_udid
            results = []
            for package in APPIUM_PACKAGES:
                results.append(
                    {
                        "package": package,
                        "result": run_cmd(
                            adb_args(self.server, "uninstall", package, udid=udid),
                            timeout=30,
                        ),
                    }
                )
            return {"udid": udid, "results": results}

        if method == "GET" and path == "/appium/status":
            return {"result": appium_request(self.server, "GET", "/status", timeout=10)}

        if method == "GET" and path == "/appium/sessions":
            result = appium_request(self.server, "GET", "/sessions", timeout=10)
            if result["status"] == 404:
                result["hint"] = "Some Appium 3 setups do not expose GET /sessions."
            return {"result": result}

        if method == "POST" and path == "/appium/session":
            body = read_json(self)
            udid = body.get("udid") or self.server.default_udid
            caps = base_caps(udid)
            extra_caps = body.get("caps") or {}
            if not isinstance(extra_caps, dict):
                raise ApiError(400, "'caps' must be a JSON object")
            caps.update(extra_caps)
            payload = {"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}}
            timeout = int(body.get("timeout", 240))
            result = appium_request(self.server, "POST", "/session", payload, timeout=timeout)
            return {"request": payload, "result": result}

        if method == "DELETE" and path.startswith("/appium/session/"):
            session_id = path.removeprefix("/appium/session/")
            if not session_id:
                raise ApiError(400, "Missing session id")
            return {
                "sessionId": session_id,
                "result": appium_request(self.server, "DELETE", f"/session/{session_id}", timeout=30),
            }

        raise ApiError(404, f"No route for {method} {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Small local API for ADB/Appium checks.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--appium-url", default=DEFAULT_APPIUM_URL)
    parser.add_argument("--udid", default=DEFAULT_UDID)
    parser.add_argument("--self-test", action="store_true", help="Start the API, call safe endpoints, then exit.")
    args = parser.parse_args()

    server = ApiServer((args.host, args.port), Handler, args.adb, args.appium_url, args.udid)
    if args.self_test:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://{args.host}:{args.port}"
        try:
            for method, path in (
                ("GET", "/health"),
                ("GET", "/adb/devices"),
                ("GET", "/appium/status"),
            ):
                print(f"{method} {path}")
                print(json.dumps(local_request(method, base_url + path), ensure_ascii=False, indent=2))
        finally:
            server.shutdown()
            server.server_close()
        return

    print(f"API listening on http://{args.host}:{args.port}")
    print(f"ADB: {args.adb}")
    print(f"Appium: {args.appium_url}")
    print(f"Default UDID: {args.udid}")
    server.serve_forever()


if __name__ == "__main__":
    main()
