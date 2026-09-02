"""reg-factory × any-auto-register 融合桥.

- 挂载 AAR 原版 React 界面到 /aar/
- 把 /aar-api/* 转发到本机 8000 的 AAR 后端
- reg-factory 启动时自动拉起 AAR 后端（独立 venv）
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

AAR_ROOT = Path(os.getenv("AAR_ROOT", str(Path.home() / "any-auto-register-src" / "any-auto-register-main")))
AAR_STATIC = AAR_ROOT / "static"
AAR_PY = AAR_ROOT / ".venv_aar" / "Scripts" / "python.exe"
AAR_PORT = int(os.getenv("AAR_PORT", "8000"))
AAR_BASE_URL = f"http://127.0.0.1:{AAR_PORT}"

# outlook-auto-register（Outlook 纯协议批量注册控制台）
OAR_ROOT = Path(os.getenv("OAR_ROOT", str(Path.home() / "outlook-auto-register-src")))
OAR_PY = OAR_ROOT / ".venv_oar" / "Scripts" / "python.exe"
OAR_PORT = int(os.getenv("OAR_PORT", "8890"))
OAR_BASE_URL = f"http://127.0.0.1:{OAR_PORT}"

router = APIRouter()

_proc: subprocess.Popen | None = None
_oar_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_start_lock = threading.Lock()
_oar_start_lock = threading.Lock()

# 健康检查专用客户端：trust_env=False 跳过系统代理探测（Windows 上对 localhost
# 探测代理会卡 ~3s 超时，这就是「服务配置加载半天」的元凶）
_health_client: httpx.Client | None = None


def _get_health_client() -> httpx.Client:
    global _health_client
    if _health_client is None or _health_client.is_closed:
        _health_client = httpx.Client(base_url=AAR_BASE_URL, timeout=2.0, trust_env=False)
    return _health_client


def _log(msg: str) -> None:
    print(f"[aar-bridge] {msg}", flush=True)


def is_aar_alive() -> bool:
    try:
        r = _get_health_client().get("/api/auth/check")
        return r.status_code == 200
    except Exception:
        # 连接失败后连接池里可能留着坏连接，弃用重建
        try:
            _health_client.close()
        except Exception:
            pass
        globals()["_health_client"] = None
        return False


# Any Auto Register 桌面程序的后端固定跑在本机 10086（Electron 壳加载它）。
AAR_DESKTOP_PORT = int(os.getenv("AAR_DESKTOP_PORT", "10086"))
AAR_DESKTOP_BASE_URL = f"http://127.0.0.1:{AAR_DESKTOP_PORT}"


def is_aar_desktop_alive() -> bool:
    """探测 Any Auto Register 桌面程序(10086)是否在运行。

    用于 reg-factory「A 完整控制台」内嵌前判断目标是否可用。
    """
    try:
        r = httpx.get(f"{AAR_DESKTOP_BASE_URL}/api/v1/health", timeout=2.0, trust_env=False)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Outlook 任务验证方式跟随「A 服务配置 → 验证服务」
# ---------------------------------------------------------------------------
# A 服务验证服务的 captcha provider（manual / local_solver / yescaptcha /
# twocaptcha / capsolver / ezcaptcha）作为 Outlook 注册人机验证的统一开关：
#   - manual（人工验证）启用且默认  → OUTLOOK_MANUAL_VERIFY=1（保留窗口真人操作）
#   - 其它 / 未配置                 → 保持本地自动按压（SwiftShader 模拟）
# 云端打码对微软 PerimeterX 按住验证解不出（原实现已移除），故不映射。
_OUTLOOK_VERIFY_DEFAULTS = ("local_solver", "manual")


def fetch_aar_captcha_settings() -> list[dict]:
    """读 A 服务验证服务的 captcha 配置（AAR /api/provider-settings）。

    失败或 AAR 不在线时返回 []，调用方走默认行为，不影响任务启动。
    """
    try:
        if not is_aar_alive():
            return []
        client = httpx.Client(base_url=AAR_BASE_URL, timeout=5.0, trust_env=False)
        try:
            r = client.get("/api/provider-settings", params={"provider_type": "captcha"})
            if r.status_code != 200:
                return []
            data = r.json()
            return data if isinstance(data, list) else []
        finally:
            client.close()
    except Exception:
        return []


def outlook_verify_mode_from_aar() -> dict:
    """按 A 服务验证服务配置决定 Outlook 注册任务的验证方式。

    返回要注入子进程的环境变量。规则：
    - 验证服务里人工打码(manual)启用且为默认/唯一启用 → 人工验证窗口
    - 否则 → 本地自动按压（SwiftShader），并允许 OUTLOOK_REG_MAX_PRESS 收窄
    """
    mode: dict = {}
    try:
        settings = fetch_aar_captcha_settings()
        if not settings:
            return mode
        enabled = [s for s in settings if s.get("enabled")]
        if not enabled:
            return mode
        default = next((s for s in enabled if s.get("is_default")), enabled[0])
        provider_key = str(default.get("provider_key") or "")
        # manual 人工打码 → 微软验证保留窗口人工完成
        if provider_key == "manual":
            mode["OUTLOOK_MANUAL_VERIFY"] = "1"
            mode["OUTLOOK_MANUAL_VERIFY_TIMEOUT"] = str(
                default.get("config", {}).get("manual_verify_timeout") or "300"
            )
            _log(f"outlook verify mode: manual (A 服务验证服务 manual)")
        else:
            mode.pop("OUTLOOK_MANUAL_VERIFY", None)
            _log(f"outlook verify mode: local press (A 服务验证服务 {provider_key})")
    except Exception as exc:  # noqa: BLE001
        _log(f"outlook verify mode resolve failed: {exc}")
    return mode


def ensure_aar_running() -> bool:
    """确保 AAR 后端在跑；没跑就拉起（独立 venv、独立 SQLite）。"""
    global _proc
    if is_aar_alive():
        return True
    with _start_lock:
        if is_aar_alive():
            return True
        if not AAR_PY.exists():
            _log("AAR python not found, skip autostart")
            return False
        _log("starting AAR backend ...")
        env = os.environ.copy()
        env["ACCOUNT_MANAGER_DATABASE_URL"] = "sqlite:///./data/account_manager.db"
        env["REGISTRY_AUTO_OPEN"] = "false"
        env.setdefault("APP_PASSWORD", "")
        try:
            _proc = subprocess.Popen(
                [str(AAR_PY), "main.py"],
                cwd=str(AAR_ROOT),
                env=ensure_data_dir(env),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:  # pragma: no cover
            _log(f"autostart failed: {exc}")
            return False
        for _ in range(40):
            time.sleep(0.5)
            if is_aar_alive():
                _log(f"AAR backend up (pid={_proc.pid})")
                return True
        _log("AAR backend did not answer in 20s")
        return False


def ensure_data_dir(env: dict) -> dict:
    data_dir = AAR_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    return env


# ------------------------------------------------------------------ OAR (outlook-auto-register)
_oar_health_client: httpx.Client | None = None


def _get_oar_health_client() -> httpx.Client:
    global _oar_health_client
    if _oar_health_client is None or _oar_health_client.is_closed:
        _oar_health_client = httpx.Client(base_url=OAR_BASE_URL, timeout=2.0, trust_env=False)
    return _oar_health_client


def is_oar_alive() -> bool:
    global _oar_health_client
    try:
        r = _get_oar_health_client().get("/api/config")
        return r.status_code == 200
    except Exception:
        # 连接失败后连接池里可能留着坏连接，弃用重建
        try:
            _oar_health_client.close()
        except Exception:
            pass
        _oar_health_client = None
        return False


def ensure_oar_running() -> bool:
    """确保 outlook-auto-register 控制台在跑；没跑就拉起（独立 venv）。"""
    global _oar_proc
    if is_oar_alive():
        return True
    with _oar_start_lock:
        if is_oar_alive():
            return True
        if not OAR_PY.exists():
            _log("OAR python not found, skip autostart")
            return False
        _log("starting OAR backend ...")
        try:
            _oar_proc = subprocess.Popen(
                [str(OAR_PY), "-m", "uvicorn", "webapp.server:app",
                 "--host", "127.0.0.1", "--port", str(OAR_PORT)],
                cwd=str(OAR_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:  # pragma: no cover
            _log(f"OAR autostart failed: {exc}")
            return False
        for _ in range(40):
            time.sleep(0.5)
            if is_oar_alive():
                _log(f"OAR backend up (pid={_oar_proc.pid})")
                return True
        _log("OAR backend did not answer in 20s")
        return False


# 他家控制台前端把请求写死成根路径（fetch('/api/...')、EventSource('/api/...')），
# 嵌进面板 /oar/ 后这些会打到 8800 根上（404）。转发 HTML 时注入这段补丁，
# 把所有 /api 开头的请求改道到 /oar/api。
OAR_PATCH = """
<script>
(function(){
  var P='/oar';
  function fix(u){ return typeof u==='string' && u.charAt(0)==='/' && u.indexOf(P+'/')!==0 && u.indexOf('/api')===0 ? P+u : u; }
  var _f=window.fetch;
  window.fetch=function(u,o){ return _f.call(window,fix(u),o); };
  var _E=window.EventSource;
  if(_E){
    window.EventSource=function(u,c){ return new _E(fix(u),c); };
    window.EventSource.prototype=_E.prototype;
  }
  var _o=XMLHttpRequest && XMLHttpRequest.prototype.open;
  if(_o){ XMLHttpRequest.prototype.open=function(){ var a=[].slice.call(arguments); a[1]=fix(a[1]); return _o.apply(this,a); }; }
})();
</script>
"""


@router.api_route("/oar", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/oar/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def oar_proxy(path: str = "", request: Request = None):
    """outlook-auto-register 控制台：页面与 /api/* 全部转发到 8890（含 POST/SSE 流式）."""
    if not is_oar_alive():
        await asyncio.to_thread(ensure_oar_running)
    target = f"/{path}" if path else "/"
    if request.url.query:
        target += f"?{request.url.query}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["host"] = f"127.0.0.1:{OAR_PORT}"
    client = _get_client()
    try:
        req = httpx.Request(
            method=request.method,
            url=f"{OAR_BASE_URL}{target}",
            content=body,
            headers=headers,
        )
        upstream = await client.send(req, stream=True)
        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in HOP_HEADERS and k.lower() != "content-encoding"
        }
        ctype = upstream.headers.get("content-type", "")
        # 页面 HTML：注入路径补丁后整体返回
        if "text/html" in ctype:
            raw = await upstream.aread()
            await upstream.aclose()
            html = raw.decode("utf-8", errors="replace").replace("<head>", "<head>" + OAR_PATCH, 1)
            return Response(
                content=html.encode("utf-8"),
                status_code=upstream.status_code,
                headers=resp_headers,
                media_type=ctype,
            )
        # 其余（API JSON / SSE 进度流）：流式透传
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=resp_headers,
            background=BackgroundResp(upstream),
        )
    except Exception as exc:
        return JSONResponse({"error": f"oar bridge error: {exc}"}, status_code=502)


# ------------------------------------------------------------------ 转发
HOP_HEADERS = {
    "content-length", "host", "connection", "keep-alive",
    "transfer-encoding", "upgrade", "te", "trailers", "proxy-authenticate",
    "proxy-authorization",
}

# 复用连接池：每请求新建 AsyncClient 会把 localhost 连接当代理协商，慢 6 秒+
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=AAR_BASE_URL,
            timeout=httpx.Timeout(60.0),
            # 本机直连，不走系统代理探测
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def _proxy(request: Request, upstream_path: str):
    # 健康检查放线程池里跑（同步 httpx），且只在后端没起来时才阻塞拉起
    if not is_aar_alive():
        await asyncio.to_thread(ensure_aar_running)
    url = upstream_path
    if request.url.query:
        url += f"?{request.url.query}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["host"] = f"127.0.0.1:{AAR_PORT}"

    client = _get_client()
    try:
        req = httpx.Request(
            method=request.method,
            url=f"{AAR_BASE_URL}{url}",
            content=body,
            headers=headers,
        )
        upstream = await client.send(req, stream=True)
        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in HOP_HEADERS and k.lower() != "content-encoding"
        }
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=resp_headers,
            background=BackgroundResp(upstream),
        )
    except Exception as exc:
        return JSONResponse({"error": f"aar bridge error: {exc}"}, status_code=502)


class BackgroundResp:
    def __init__(self, upstream):
        self.upstream = upstream

    async def __call__(self):
        await self.upstream.aclose()





@router.get("/api/aar-desktop-health")
async def aar_desktop_health():
    """同源探测：Any Auto Register 桌面程序(localhost:10086)是否在运行。

    供前端「A 完整控制台」决定内嵌还是提示先开桌面程序。
    """
    return {"alive": is_aar_desktop_alive(), "url": AAR_DESKTOP_BASE_URL}


@router.get("/aar")
async def aar_index():
    """入口：/aar → 他的原版 React 界面."""
    ensure_aar_running()
    idx = AAR_STATIC / "index.html"
    if not idx.exists():
        return JSONResponse({"error": "AAR static not built"}, status_code=404)
    return FileResponse(idx)


@router.get("/aar/{path:path}")
async def aar_static_files(path: str):
    # vite base=/aar/static/ 但文件实体在 static/ 根下，去掉多余的 static/ 前缀
    if path.startswith("static/"):
        path = path[len("static/"):]
    target = (AAR_STATIC / path).resolve()
    if not str(target).startswith(str(AAR_STATIC.resolve())):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if target.is_file():
        return FileResponse(target)
    # SPA fallback：非文件路径回 index.html（React Router 接管）
    return FileResponse(AAR_STATIC / "index.html")


@router.api_route("/aar-api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def aar_api_proxy(path: str, request: Request):
    return await _proxy(request, f"/api/{path}")


# ------------------------------------------------------------------ OAR 原生页 API 转发
@router.api_route("/oar-api", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/oar-api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def oar_api_proxy(path: str = "", request: Request = None):
    """O 邮箱注册台原生页的接口通道：/oar-api/* → 8890 /api/*."""
    if not is_oar_alive():
        await asyncio.to_thread(ensure_oar_running)
    target = f"/api/{path}" if path else "/api/"
    if request.url.query:
        target += f"?{request.url.query}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["host"] = f"127.0.0.1:{OAR_PORT}"
    client = _get_client()
    try:
        req = httpx.Request(
            method=request.method,
            url=f"{OAR_BASE_URL}{target}",
            content=body,
            headers=headers,
        )
        upstream = await client.send(req, stream=True)
        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in HOP_HEADERS and k.lower() != "content-encoding"
        }
        ctype = upstream.headers.get("content-type", "")
        # 导出 TXT 是文件下载，直接整体读回
        if "text/html" in ctype:
            raw = await upstream.aread()
            await upstream.aclose()
            return Response(content=raw, status_code=upstream.status_code,
                            headers=resp_headers, media_type=ctype)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=resp_headers,
            background=BackgroundResp(upstream),
        )
    except Exception as exc:
        return JSONResponse({"error": f"oar api bridge error: {exc}"}, status_code=502)
