# -*- coding: utf-8 -*-
"""
mailbox_broker.py — 共享取码服务（一号三用并行流水线的核心）

单独一个常驻进程，对每个 outlook 邮箱**只登录一次** Outlook（noproxy BitBrowser），
轮询收件箱+垃圾箱，按发件人/正则把验证码(ChatGPT/Grok)或 magic-link(Claude)分发给
并行运行的三个注册子进程 —— 从而避开"三个浏览器同时密码登录同一账号被微软判并发登录"。

每个 email 一把 asyncio.Lock：同号的并发 /fetch 串行化（单浏览器不能同时切两个文件夹）；
不同 email 之间并行。idle reaper 自动回收空闲会话。

端点:
    POST /fetch    {email,password,sender_hint[],subject_hint[],regex,kind:"code"|"link",timeout}
                   -> {ok:bool, value:str|None, error?:str}
    POST /release  {email}  -> 关闭并删除该号 BitBrowser 会话
    GET  /health   -> {ok, sessions:[email...]}

用法:
    python mailbox_broker.py [--host 127.0.0.1] [--port 8765] [--idle 480]
"""

import argparse
import asyncio
import re
import sys
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from aiohttp import web
from playwright.async_api import async_playwright

from bitbrowser import BitBrowser
from common.browser import inject_stealth, create_browser_with_retry
from common.mailbox import _outlook_login, _click_folder, _scan_current_folder

INBOX_NAMES = ["收件箱", "Inbox", "受信トレイ"]
JUNK_NAMES = ["垃圾邮件", "Junk Email", "Junk", "迷惑メール"]

# magic-link 提取 JS（移植自 register.py get_magic_link_outlook_pw 的 _find_in_current_folder）
_LINK_JS = """
() => {
    const items = document.querySelectorAll('[role="listitem"], [role="option"], [aria-label*="Anthropic"], [aria-label*="Claude"], [aria-label*="anthropic"]');
    let opened = false;
    for (const item of items) {
        const text = (item.textContent || '').toLowerCase();
        if (text.includes('anthropic') || text.includes('claude') || text.includes('magic') || text.includes('verification')) {
            item.click();
            opened = true;
            break;
        }
    }
    if (!opened) return null;
    return '__OPENED__';
}
"""

_LINK_EXTRACT_JS = """
() => {
    const allLinks = document.querySelectorAll('a');
    for (const a of allLinks) {
        const href = (a.href || '');
        const hrefLower = href.toLowerCase();
        if (hrefLower.includes('claude.ai/magic-link')) return href;
        if (hrefLower.includes('safelinks') && hrefLower.includes('claude')) {
            try {
                const url = new URL(href);
                const original = url.searchParams.get('url');
                if (original) return original;
            } catch(e) {}
            return href;
        }
    }
    const body = document.body.innerHTML;
    const m = body.match(/https:\\/\\/claude\\.ai\\/magic-link[^"'<\\s]+/);
    return m ? m[0] : null;
}
"""


# 统计当前文件夹里匹配 hints 的邮件数（用于"等新邮件到达"的基线判定，
# 避免返回收件箱里预先存在的旧邮件：Microsoft 欢迎/安全码、上一轮注册的旧验证码/旧 magic-link）
_COUNT_JS = """(hints) => {
    let c = 0;
    document.querySelectorAll('[role="option"]').forEach(it => {
        const t = (it.textContent || '').toLowerCase();
        if (hints.some(h => h && t.includes(h))) c++;
    });
    return c;
}"""


class Session:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.pid = None
        self.browser = None
        self.ctx = None
        self.page = None
        self.lock = asyncio.Lock()
        self.last_used = time.time()
        self.seen = set()          # 已返回过的值，防两平台抢同一封
        self.logged_in = False
        self.just_created = False  # 本次 ensure_session 是否触发了新登录(用于规避基线 race)


class Broker:
    def __init__(self, idle_timeout=480):
        self.bb = BitBrowser()
        self.pw = None
        self.p = None
        self.sessions = {}            # email -> Session
        self._create_lock = asyncio.Lock()
        self.idle_timeout = idle_timeout

    async def start(self):
        self.pw = async_playwright()
        self.p = await self.pw.start()

    async def ensure_session(self, email, password):
        """取得（必要时创建并登录）该邮箱的常驻 Outlook 会话。"""
        s = self.sessions.get(email)
        if s and s.logged_in:
            s.just_created = False
            return s
        async with self._create_lock:
            # 双检
            s = self.sessions.get(email)
            if s and s.logged_in:
                s.just_created = False
                return s
            if not s:
                s = Session(email, password)
                self.sessions[email] = s
            print(f"  [broker] creating Outlook session for {email}")
            try:
                pid = create_browser_with_retry(self.bb, f"mbx_{int(time.time())}")
                if not pid:
                    raise RuntimeError("create_browser failed")
                s.pid = pid
                self.bb._post("/browser/update", {
                    "id": pid, "proxyMethod": 2, "proxyType": "noproxy",
                    "browserFingerPrint": {"coreVersion": "130"},
                })
                data = None
                for _ in range(8):
                    try:
                        data = self.bb.open_browser(pid)
                        break
                    except Exception:
                        await asyncio.sleep(4)
                if not data:
                    raise RuntimeError("open_browser failed")
                s.browser = await self.p.chromium.connect_over_cdp(data["ws"])
                s.ctx = s.browser.contexts[0]
                s.page = s.ctx.pages[0] if s.ctx.pages else await s.ctx.new_page()
                await inject_stealth(s.ctx, s.page)
                ok = await _outlook_login(s.page, email, password)
                if not ok:
                    raise RuntimeError("outlook login failed")
                # 进收件箱完整加载一次
                try:
                    await s.page.goto("https://outlook.live.com/mail/0/", timeout=60000)
                    await asyncio.sleep(6)
                except Exception:
                    pass
                s.logged_in = True
                s.just_created = True
                print(f"  [broker] session ready: {email}")
                return s
            except Exception as e:
                print(f"  [broker] session init failed for {email}: {e}")
                await self._close_session(email)
                raise

    async def _scan_link(self, page):
        """在当前文件夹找 anthropic/claude 邮件并提 magic link。"""
        try:
            opened = await page.evaluate(_LINK_JS)
        except Exception:
            opened = None
        if opened != "__OPENED__":
            return None
        for _ in range(4):
            await asyncio.sleep(2)
            try:
                link = await page.evaluate(_LINK_EXTRACT_JS)
            except Exception:
                link = None
            if link:
                return link
        return None

    async def _count_matching(self, page, hints):
        """当前已打开文件夹里，匹配 hints 的邮件条目数。"""
        n = 0
        for _ in range(4):
            await asyncio.sleep(1)
            try:
                n = await page.evaluate("() => document.querySelectorAll('[role=\"option\"]').length")
            except Exception:
                n = 0
            if n > 0:
                break
        try:
            return await page.evaluate(_COUNT_JS, hints)
        except Exception:
            return 0

    async def fetch(self, email, password, sender_hint, subject_hint, regex, kind, timeout):
        s = await self.ensure_session(email, password)
        async with s.lock:
            s.last_used = time.time()
            pat = re.compile(regex) if kind == "code" else None
            hints = [h.lower() for h in (tuple(sender_hint) + tuple(subject_hint)) if h]
            folders = [("inbox", INBOX_NAMES), ("junk", JUNK_NAMES)]

            # 基线：记录每个文件夹"当前"匹配邮件数。注册脚本是先触发发码/发链接、再调 /fetch，
            # 故此刻收件箱里的匹配邮件都是【旧的】(MS 欢迎/安全码、上一轮旧验证码)，全部计入基线并忽略。
            baseline = {}
            for key, names in folders:
                await _click_folder(s.page, names)
                await asyncio.sleep(1.5)
                baseline[key] = await self._count_matching(s.page, hints)
            print(f"  [broker] {email} baseline {kind} counts: {baseline}")

            # 规避基线 race：broker 登录 Outlook 要 20~30s，注册脚本是"先触发发码、再调 /fetch"，
            # 等 broker 登进来数基线时，本轮验证码/链接往往【已经到达】并被计入基线 → 死等"数量增加"必然超时。
            # 故当本次 ensure_session 触发了新登录(just_created)时，直接扫一遍当前【最新】匹配邮件取值；
            # _scan_* 只取列表顶部(最新)那封，配合 seen 去重，能抓到登录期间到达的那封。
            if s.just_created:
                for key, names in folders:
                    await _click_folder(s.page, names)
                    await asyncio.sleep(2)
                    if kind == "link":
                        val = await self._scan_link(s.page)
                    else:
                        val = await _scan_current_folder(s.page, pat, tuple(sender_hint), tuple(subject_hint))
                    if val and val not in s.seen:
                        s.seen.add(val)
                        s.last_used = time.time()
                        print(f"  [broker] {email} {kind} (first-scan after fresh login, {key}) -> {val[:60]}")
                        return val

            start = time.time()
            while time.time() - start < timeout:
                for key, names in folders:
                    await _click_folder(s.page, names)
                    await asyncio.sleep(2)
                    cur = await self._count_matching(s.page, hints)
                    if cur <= baseline[key]:
                        continue  # 没有新邮件到达此文件夹 -> 不取(防返回旧邮件)
                    # 有新邮件到达 -> 取顶部(列表时间倒序，第一封=最新)那封提码/链接
                    if kind == "link":
                        val = await self._scan_link(s.page)
                    else:
                        val = await _scan_current_folder(s.page, pat, tuple(sender_hint), tuple(subject_hint))
                    if val and val not in s.seen:
                        s.seen.add(val)
                        s.last_used = time.time()
                        print(f"  [broker] {email} {kind} (fresh, {key} {baseline[key]}->{cur}) -> {val[:60]}")
                        return val
                    # 取到空/已 seen：抬高基线，避免同一封反复触发
                    baseline[key] = cur
                elapsed = int(time.time() - start)
                print(f"  [broker] {email} waiting NEW {kind} ({elapsed}s/{timeout}s)")
                await asyncio.sleep(6)
            print(f"  [broker] {email} {kind} timeout (no new email arrived)")
            return None

    async def _close_session(self, email):
        s = self.sessions.pop(email, None)
        if not s:
            return
        try:
            if s.browser:
                await s.browser.close()
        except Exception:
            pass
        if s.pid:
            try:
                self.bb.close_browser(s.pid)
            except Exception:
                pass
            await asyncio.sleep(1)
            try:
                self.bb.delete_browser(s.pid)
            except Exception:
                pass
        print(f"  [broker] released session: {email}")

    async def reaper(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            stale = [e for e, s in list(self.sessions.items())
                     if now - s.last_used > self.idle_timeout and not s.lock.locked()]
            for e in stale:
                print(f"  [broker] idle reap: {e}")
                await self._close_session(e)


# ---------- HTTP handlers ----------

async def h_fetch(request):
    broker = request.app["broker"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        return web.json_response({"ok": False, "error": "email/password required"}, status=400)
    sender_hint = body.get("sender_hint") or ("noreply", "no-reply")
    subject_hint = body.get("subject_hint") or ("code", "verify", "verification", "confirm")
    regex = body.get("regex") or r"\b(\d{6})\b"
    kind = body.get("kind") or "code"
    timeout = int(body.get("timeout") or 150)
    try:
        val = await broker.fetch(email, password, sender_hint, subject_hint, regex, kind, timeout)
        return web.json_response({"ok": bool(val), "value": val})
    except Exception as e:
        return web.json_response({"ok": False, "value": None, "error": str(e)})


async def h_release(request):
    broker = request.app["broker"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = (body.get("email") or "").strip()
    if email:
        await broker._close_session(email)
    return web.json_response({"ok": True})


async def h_health(request):
    broker = request.app["broker"]
    return web.json_response({"ok": True, "sessions": list(broker.sessions.keys())})


async def on_startup(app):
    app["broker"] = Broker(idle_timeout=app["idle_timeout"])
    await app["broker"].start()
    app["reaper_task"] = asyncio.create_task(app["broker"].reaper())
    print(f"  [broker] started, idle_timeout={app['idle_timeout']}s")


async def on_cleanup(app):
    task = app.get("reaper_task")
    if task:
        task.cancel()
    broker = app.get("broker")
    if broker:
        for e in list(broker.sessions.keys()):
            await broker._close_session(e)


def main():
    ap = argparse.ArgumentParser(description="Shared Outlook mailbox code/link broker")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--idle", type=int, default=480, help="idle session reap seconds")
    args = ap.parse_args()

    app = web.Application()
    app["idle_timeout"] = args.idle
    app.router.add_post("/fetch", h_fetch)
    app.router.add_post("/release", h_release)
    app.router.add_get("/health", h_health)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    print(f"=== mailbox_broker on http://{args.host}:{args.port} ===")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
