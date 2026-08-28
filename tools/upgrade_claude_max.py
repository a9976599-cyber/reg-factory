# -*- coding: utf-8 -*-
"""
Claude.ai Max $20 SEPA 升级脚本
通过注入 Cassia checkout mock + randomiban.com 随机德国 IBAN 完成 SEPA 支付开通 Max。

流程:
  1. 打开已登录的 Claude 浏览器窗口 (通过 sessionKey cookie 或已有 profile)
  2. 注入 Cassia Response Mock (使 checkout_capabilities 返回 cassia flow)
  3. 导航到订阅升级页
  4. 选 Max $20 plan
  5. 选德国 + SEPA 支付
  6. 从 randomiban.com 获取随机德国 IBAN
  7. 填入 IBAN 并提交

用法:
    python tools/upgrade_claude_max.py --session-key "sk-ant-..."
    python tools/upgrade_claude_max.py --cookie-file cookies/claude/xxx.json
    python tools/upgrade_claude_max.py --profile-id "xxxxxxxx"
"""

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

import requests
from playwright.async_api import async_playwright

from bitbrowser import BitBrowser
import config as _config  # noqa: F401  # load the project .env
from common import proxy_switch

CLAUDE_BROWSER_CORE_VERSION = (
    os.environ.get("CLAUDE_BROWSER_CORE_VERSION")
    or os.environ.get("BB_CORE_VERSION")
    or "146"
).strip()
UPGRADE_TIMEOUT = 300

# Cassia Response Mock — 拦截 checkout_capabilities API 使其返回 cassia flow
CASSIA_MOCK_JS = r"""
(() => {
    if (window.__cassiaMockInstalled) return;
    const TARGET_PATH = /^\/api\/organizations\/[^/]+\/subscription\/checkout_capabilities\/?$/;
    const MOCK_DATA = {checkout_flow: "cassia"};
    const MOCK_BODY = JSON.stringify(MOCK_DATA);
    const MOCK_LENGTH = new TextEncoder().encode(MOCK_BODY).byteLength;

    function isTarget(input, method) {
        try {
            let rawUrl;
            if (typeof input === "string" || input instanceof URL) rawUrl = String(input);
            else if (input && typeof input.url === "string") rawUrl = input.url;
            else return false;
            const url = new URL(rawUrl, location.href);
            if (String(method || "GET").toUpperCase() !== "GET") return false;
            const host = url.hostname;
            if (host !== "claude.ai" && !host.endsWith(".claude.ai")) return false;
            return TARGET_PATH.test(url.pathname);
        } catch (e) { return false; }
    }

    const nativeFetch = window.fetch;
    window.fetch = async function(input, init) {
        const method = init?.method || (input instanceof Request ? input.method : "GET");
        const resp = await nativeFetch.apply(this, arguments);
        if (!isTarget(input, method)) return resp;
        const headers = new Headers(resp.headers);
        headers.set("content-type", "application/json; charset=utf-8");
        headers.set("content-length", String(MOCK_LENGTH));
        headers.delete("content-encoding");
        const mock = new Response(MOCK_BODY, {status: 200, statusText: "OK", headers});
        try {
            Object.defineProperties(mock, {
                url: {value: resp.url}, redirected: {value: resp.redirected}, type: {value: resp.type}
            });
        } catch(_) {}
        console.warn("[Cassia Mock] fetch intercepted:", resp.url);
        return mock;
    };

    const xhrInfo = new WeakMap();
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        xhrInfo.set(this, {method, url: new URL(String(url), location.href).href});
        return origOpen.apply(this, arguments);
    };

    function isXhrTarget(xhr) {
        const info = xhrInfo.get(xhr);
        if (!info || xhr.readyState !== 4) return false;
        return isTarget(info.url, info.method);
    }

    const desc = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, "responseText");
    if (desc && desc.configurable) {
        const origGetter = desc.get;
        Object.defineProperty(XMLHttpRequest.prototype, "responseText", {
            ...desc,
            get() { return isXhrTarget(this) ? MOCK_BODY : origGetter.call(this); }
        });
    }
    const descR = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, "response");
    if (descR && descR.configurable) {
        const origGetter = descR.get;
        Object.defineProperty(XMLHttpRequest.prototype, "response", {
            ...descR,
            get() {
                if (!isXhrTarget(this)) return origGetter.call(this);
                return this.responseType === "json" ? MOCK_DATA : MOCK_BODY;
            }
        });
    }

    window.__cassiaMockInstalled = true;
    console.info("[Cassia Mock] armed");
})();
"""


def claude_browser_fingerprint():
    return {
        "ostype": "PC",
        "os": "Win32",
        "coreVersion": CLAUDE_BROWSER_CORE_VERSION,
        "isIpCreateTimeZone": True,
        "isIpCreateLanguage": True,
        "isIpCreateDisplayLanguage": True,
        "isIpCreatePosition": True,
        "isIpCountry": True,
    }


def fetch_random_iban_de():
    """从 randomiban.com 获取随机德国 IBAN。"""
    try:
        resp = requests.get(
            "http://randomiban.com/?country=Germany",
            headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        match = re.search(r"(DE\d{2}\s*\d{4}\s*\d{4}\s*\d{4}\s*\d{4}\s*\d{2})", resp.text)
        if match:
            iban = match.group(1).replace(" ", "")
            print(f"  [iban] fetched: {iban}")
            return iban
    except Exception as e:
        print(f"  [iban] randomiban.com failed: {e}")

    # fallback: 本地生成合规德国 IBAN (DE + 2位校验 + 8位BLZ + 10位账号)
    blz = random.choice([
        "37040044", "10010010", "50010517", "20010020",
        "68050101", "76010085", "30060601", "55050000",
    ])
    account = "".join([str(random.randint(0, 9)) for _ in range(10)])
    bban = blz + account
    # ISO 13616 校验位计算
    check_str = bban + "131400"  # DE = 13,14 ; 00 placeholder
    remainder = int(check_str) % 97
    check_digits = f"{98 - remainder:02d}"
    iban = f"DE{check_digits}{bban}"
    print(f"  [iban] generated locally: {iban}")
    return iban


def generate_sepa_holder_name():
    """生成 SEPA 持卡人姓名。"""
    firsts = ["Max", "Anna", "Lukas", "Sophie", "Felix", "Laura", "Jonas", "Lena",
              "Tim", "Marie", "Paul", "Julia", "Leon", "Sarah", "Niklas", "Emma"]
    lasts = ["Mueller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer",
             "Wagner", "Becker", "Schulz", "Hoffmann", "Koch", "Richter"]
    return f"{random.choice(firsts)} {random.choice(lasts)}"


async def inject_cassia_mock(context, page):
    """注入 Cassia checkout mock，确保所有后续导航也生效。"""
    await context.add_init_script(CASSIA_MOCK_JS)
    try:
        await page.evaluate(CASSIA_MOCK_JS)
    except Exception:
        pass
    print("  [cassia] mock injected")


async def setup_session_cookie(context, session_key):
    """将 sessionKey 写入浏览器 cookie。"""
    await context.add_cookies([{
        "name": "sessionKey",
        "value": session_key,
        "domain": ".claude.ai",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }])
    print(f"  [session] cookie set: {session_key[:40]}...")


async def get_org_id(page):
    """从当前页面获取 organization ID。"""
    try:
        org_id = await page.evaluate(r"""() => {
            // 从 URL 提取
            const m = location.pathname.match(/\/([0-9a-f]{8}-[0-9a-f-]{27,})/);
            if (m) return m[1];
            // 从页面内容/API 响应中提取
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                const t = s.textContent || '';
                const om = t.match(/"organizationId"\s*:\s*"([^"]+)"/);
                if (om) return om[1];
            }
            return null;
        }""")
        return org_id
    except Exception:
        return None


async def navigate_to_billing(page):
    """导航到账单/订阅页面。"""
    # 先去设置页获取 org context
    print("  [nav] going to settings...")
    await page.goto("https://claude.ai/settings", timeout=30000)
    await asyncio.sleep(3)

    # 尝试找到 billing/subscription 入口
    billing_clicked = False
    for label in ["Billing", "Subscription", "Plan", "计费", "订阅", "プラン"]:
        try:
            link = page.locator(f'a:has-text("{label}"), button:has-text("{label}")').first
            if await link.count() > 0:
                await link.click(timeout=5000)
                billing_clicked = True
                print(f"  [nav] clicked '{label}'")
                await asyncio.sleep(3)
                break
        except Exception:
            continue

    if not billing_clicked:
        # 直接访问 billing URL
        print("  [nav] direct navigation to billing page...")
        await page.goto("https://claude.ai/settings/billing", timeout=30000)
        await asyncio.sleep(3)

    print(f"  [nav] URL: {page.url}")


async def select_max_plan(page):
    """选择 Max $20 plan。"""
    print("  [plan] looking for Max plan option...")

    # 查找升级/选择 plan 的按钮
    for attempt in range(3):
        # 尝试点击 Upgrade / Subscribe 按钮
        upgrade_clicked = False
        for label in ["Upgrade", "Subscribe", "Max", "升级", "订阅",
                      "Get Max", "Choose Max", "Select Max"]:
            try:
                btn = page.locator(
                    f'button:has-text("{label}"), a:has-text("{label}"), '
                    f'[role="button"]:has-text("{label}")'
                ).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=8000)
                    upgrade_clicked = True
                    print(f"  [plan] clicked '{label}'")
                    await asyncio.sleep(3)
                    break
            except Exception:
                continue

        if upgrade_clicked:
            break
        await asyncio.sleep(2)

    # 确认在 plan 选择页/checkout 页
    print(f"  [plan] URL: {page.url}")


# PLACEHOLDER_UPGRADE_PT2


async def fill_sepa_payment(page, iban, holder_name):
    """填写 SEPA 支付表单。"""
    print(f"  [sepa] filling IBAN: {iban}")
    print(f"  [sepa] holder: {holder_name}")

    # 选择德国
    country_selectors = [
        'select[name*="country"]', 'select[id*="country"]',
        '[data-testid*="country"]', 'select',
    ]
    for sel in country_selectors:
        try:
            select = page.locator(sel).first
            if await select.count() > 0:
                await select.select_option(label="Germany")
                print("  [sepa] country = Germany (by label)")
                break
        except Exception:
            try:
                await select.select_option(value="DE")
                print("  [sepa] country = DE (by value)")
                break
            except Exception:
                continue

    await asyncio.sleep(2)

    # 选择 SEPA 支付方式
    sepa_clicked = False
    for label in ["SEPA", "Bank transfer", "Banküberweisung", "SEPA Direct Debit",
                  "SEPA Debit", "Bank account", "Bankkonto"]:
        try:
            btn = page.locator(
                f'button:has-text("{label}"), label:has-text("{label}"), '
                f'[role="radio"]:has-text("{label}"), div:has-text("{label}")'
            ).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=5000)
                sepa_clicked = True
                print(f"  [sepa] payment method: '{label}'")
                await asyncio.sleep(2)
                break
        except Exception:
            continue

    if not sepa_clicked:
        # 尝试通过 radio/option 机制
        try:
            radios = page.locator('[role="radio"], input[type="radio"]')
            count = await radios.count()
            for i in range(count):
                text = await radios.nth(i).text_content() or ""
                if "sepa" in text.lower() or "bank" in text.lower():
                    await radios.nth(i).click()
                    sepa_clicked = True
                    print(f"  [sepa] clicked radio: {text.strip()[:40]}")
                    break
        except Exception:
            pass

    await asyncio.sleep(2)

    # 填入持卡人名
    name_filled = False
    for sel in ['input[name*="name"]', 'input[id*="name"]', 'input[placeholder*="name"]',
                'input[placeholder*="Name"]', 'input[autocomplete="name"]',
                'input[data-testid*="name"]']:
        try:
            inp = page.locator(sel).first
            if await inp.count() > 0 and await inp.is_visible():
                await inp.fill("")
                await inp.type(holder_name, delay=random.randint(30, 80))
                name_filled = True
                print(f"  [sepa] name filled")
                break
        except Exception:
            continue

    if not name_filled:
        # 试找 label 含 name 的 input
        try:
            inputs = page.locator("input:visible")
            count = await inputs.count()
            for i in range(count):
                placeholder = await inputs.nth(i).get_attribute("placeholder") or ""
                aria = await inputs.nth(i).get_attribute("aria-label") or ""
                if "name" in placeholder.lower() or "name" in aria.lower():
                    await inputs.nth(i).fill("")
                    await inputs.nth(i).type(holder_name, delay=random.randint(30, 80))
                    name_filled = True
                    print(f"  [sepa] name filled (by scan)")
                    break
        except Exception:
            pass

    await asyncio.sleep(1)

    # 填入 IBAN — SEPA 表单可能在 iframe (Stripe) 中
    iban_filled = False

    # 先检查 Stripe iframe
    stripe_frames = page.frames
    for frame in stripe_frames:
        if "stripe" in (frame.url or "").lower() or "js.stripe.com" in (frame.url or ""):
            try:
                iban_input = frame.locator('input[name*="iban"], input[placeholder*="IBAN"], input[data-elements-stable-field-name="iban"]').first
                if await iban_input.count() > 0:
                    await iban_input.fill("")
                    await iban_input.type(iban, delay=random.randint(20, 60))
                    iban_filled = True
                    print(f"  [sepa] IBAN filled (stripe iframe)")
                    break
            except Exception:
                continue

    if not iban_filled:
        # 直接在主页面找 IBAN input
        for sel in ['input[name*="iban"]', 'input[name*="IBAN"]',
                    'input[placeholder*="IBAN"]', 'input[placeholder*="iban"]',
                    'input[data-testid*="iban"]', 'input[autocomplete*="iban"]',
                    'input[id*="iban"]']:
            try:
                inp = page.locator(sel).first
                if await inp.count() > 0:
                    await inp.fill("")
                    await inp.type(iban, delay=random.randint(20, 60))
                    iban_filled = True
                    print(f"  [sepa] IBAN filled (main page)")
                    break
            except Exception:
                continue

    if not iban_filled:
        # 遍历所有 iframe 找 IBAN field
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                iban_input = frame.locator('input').first
                inputs_count = await frame.locator('input').count()
                for i in range(inputs_count):
                    inp = frame.locator('input').nth(i)
                    name = await inp.get_attribute("name") or ""
                    placeholder = await inp.get_attribute("placeholder") or ""
                    if "iban" in name.lower() or "iban" in placeholder.lower() or "DE" in placeholder:
                        await inp.fill("")
                        await inp.type(iban, delay=random.randint(20, 60))
                        iban_filled = True
                        print(f"  [sepa] IBAN filled (iframe scan)")
                        break
                if iban_filled:
                    break
            except Exception:
                continue

    if not iban_filled:
        print("  [sepa] WARNING: could not find IBAN input field")
        return False

    await asyncio.sleep(2)

    # 勾选 terms/mandate checkbox
    try:
        checkboxes = page.locator('input[type="checkbox"]:not(:checked)')
        count = await checkboxes.count()
        for i in range(count):
            try:
                await checkboxes.nth(i).check(timeout=3000)
            except Exception:
                pass
        if count > 0:
            print(f"  [sepa] checked {count} checkboxes")
    except Exception:
        pass

    await asyncio.sleep(1)
    return True


async def upgrade_to_max(session_key=None, cookie_file=None, profile_id=None,
                         proxy_node=None, timeout=300):
    """核心升级流程：用已有 session 升级到 Max $20 (SEPA)。

    入口方式三选一：
      - session_key: 直接传 sessionKey 字符串
      - cookie_file: claude cookie JSON 文件路径
      - profile_id: 已有 BitBrowser profile (已登录态)
    """
    global UPGRADE_TIMEOUT
    UPGRADE_TIMEOUT = timeout
    start_time = time.time()

    def check_timeout():
        if time.time() - start_time > UPGRADE_TIMEOUT:
            raise TimeoutError(f"upgrade timeout ({UPGRADE_TIMEOUT}s)")

    # 解析 session
    if cookie_file and not session_key:
        try:
            with open(cookie_file, "r", encoding="utf-8") as f:
                cookies_data = json.load(f)
            if isinstance(cookies_data, list):
                sk = next((c["value"] for c in cookies_data if c.get("name") == "sessionKey"), None)
            else:
                sk = cookies_data.get("sessionKey") or cookies_data.get("session_key")
            if sk:
                session_key = sk
                print(f"  [init] session from cookie file: {session_key[:40]}...")
        except Exception as e:
            print(f"  [init] cookie file error: {e}")
            return False

    if not session_key and not profile_id:
        print("  ERROR: need --session-key, --cookie-file, or --profile-id")
        return False

    bb = BitBrowser()
    own_profile = False
    pid = profile_id

    try:
        # 如果没有现成 profile，创建一个临时的
        if not pid:
            ts = time.strftime("%m%d_%H%M%S")
            name = f"claude_upgrade_{ts}"
            print(f"\n  [browser] creating: {name}")
            fp = claude_browser_fingerprint()
            use_proxy = str(proxy_node or "auto").lower() not in {"none", "off", "direct"}
            browser_proxy = proxy_switch.browser_proxy_fields() if use_proxy else {}
            pid = bb.create_browser(name=name, browserFingerPrint=fp, **browser_proxy)
            own_profile = True

            if browser_proxy.get("host"):
                print(f"  [proxy] {browser_proxy['host']}:{browser_proxy['port']}")

        # 启动浏览器
        print("  [browser] opening...")
        resp = bb.open_browser(pid)
        ws = resp.get("ws") or resp.get("webSocketDebuggerUrl", "")
        if not ws:
            raise Exception("no websocket endpoint from BitBrowser")

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()

            # 注入 cassia mock (必须在导航前)
            await inject_cassia_mock(context, page)

            # 设置 session cookie
            if session_key:
                await setup_session_cookie(context, session_key)

            # 导航到 Claude
            print("\n  [upgrade] navigating to claude.ai...")
            await page.goto("https://claude.ai/settings/billing", timeout=60000)
            await asyncio.sleep(5)
            print(f"  URL: {page.url}")
            check_timeout()

            # 检查是否已登录
            if "/login" in page.url:
                print("  ERROR: session invalid, redirected to login")
                return False

            # 等页面稳定
            await asyncio.sleep(3)
            page_text = await page.evaluate("() => document.body?.innerText || ''")
            print(f"  page preview: {page_text[:150]}")

            # 寻找升级/subscribe 按钮
            print("\n  [upgrade] looking for upgrade option...")
            upgrade_clicked = False

            for label in ["Upgrade", "Subscribe", "Max", "升级", "Get Max",
                          "Upgrade to Max", "Choose Max", "Pro", "Get Pro"]:
                try:
                    btn = page.locator(
                        f'button:has-text("{label}"), a:has-text("{label}"), '
                        f'[role="button"]:has-text("{label}")'
                    ).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click(timeout=8000)
                        upgrade_clicked = True
                        print(f"  [upgrade] clicked: '{label}'")
                        break
                except Exception:
                    continue

            if not upgrade_clicked:
                # 直接导航到升级 URL
                print("  [upgrade] no button found, navigating to upgrade URL...")
                await page.goto("https://claude.ai/upgrade", timeout=30000)
                await asyncio.sleep(5)
                print(f"  URL: {page.url}")

            await asyncio.sleep(5)
            check_timeout()

            # 选择 Max $20 plan (如果有多档选项)
            page_text = await page.evaluate("() => document.body?.innerText || ''")
            for label in ["Max", "$20", "Max $20", "USD 20"]:
                try:
                    btn = page.locator(
                        f'button:has-text("{label}"), [role="radio"]:has-text("{label}"), '
                        f'label:has-text("{label}"), div[role="option"]:has-text("{label}")'
                    ).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click(timeout=5000)
                        print(f"  [plan] selected: '{label}'")
                        await asyncio.sleep(3)
                        break
                except Exception:
                    continue

            check_timeout()

            # 点击 Subscribe / Continue 进入付款页
            for label in ["Subscribe", "Continue", "Next", "继续", "Weiter",
                          "Confirm", "Proceed to payment", "Pay"]:
                try:
                    btn = page.locator(
                        f'button:has-text("{label}"):visible'
                    ).first
                    if await btn.count() > 0:
                        await btn.click(timeout=8000)
                        print(f"  [checkout] clicked: '{label}'")
                        await asyncio.sleep(5)
                        break
                except Exception:
                    continue

            check_timeout()
            print(f"  URL: {page.url}")

            # 生成支付信息
            iban = fetch_random_iban_de()
            holder_name = generate_sepa_holder_name()

            # 填写 SEPA 表单
            success = await fill_sepa_payment(page, iban, holder_name)
            if not success:
                # 截图留证
                try:
                    await page.screenshot(path="screenshots_unlock/upgrade_sepa_fail.png")
                except Exception:
                    pass
                print("  [sepa] form fill failed")
                return False

            check_timeout()

            # 提交支付
            print("\n  [submit] submitting payment...")
            submitted = False
            for label in ["Subscribe", "Pay", "Confirm", "Submit", "完成",
                          "Bestätigen", "Pay now", "Start subscription",
                          "Confirm payment", "Subscribe now"]:
                try:
                    btn = page.locator(
                        f'button:has-text("{label}"):visible'
                    ).first
                    if await btn.count() > 0:
                        await btn.click(timeout=10000)
                        submitted = True
                        print(f"  [submit] clicked: '{label}'")
                        break
                except Exception:
                    continue

            if not submitted:
                # 兜底: 点最后一个可见的 primary/submit button
                try:
                    btn = page.locator(
                        'button[type="submit"]:visible, button.primary:visible'
                    ).last
                    if await btn.count() > 0:
                        await btn.click(timeout=10000)
                        submitted = True
                        print("  [submit] clicked fallback submit button")
                except Exception:
                    pass

            if not submitted:
                print("  [submit] WARNING: no submit button found")
                try:
                    await page.screenshot(path="screenshots_unlock/upgrade_no_submit.png")
                except Exception:
                    pass
                return False

            # 等待结果
            await asyncio.sleep(10)
            print(f"  URL: {page.url}")

            # 检查是否成功
            page_text = await page.evaluate("() => document.body?.innerText || ''")
            success_markers = ["success", "thank", "welcome to max", "subscription active",
                               "erfolgreich", "Max plan", "current plan"]
            fail_markers = ["failed", "error", "declined", "invalid", "fehler"]

            text_lower = page_text.lower()
            is_success = any(m in text_lower for m in success_markers)
            is_fail = any(m in text_lower for m in fail_markers)

            if is_success and not is_fail:
                print("\n  [OK] Max $20 upgrade successful!")
                try:
                    await page.screenshot(path="screenshots_unlock/upgrade_success.png")
                except Exception:
                    pass
                return True
            elif is_fail:
                print(f"\n  [FAIL] payment error detected")
                print(f"  page: {page_text[:200]}")
                try:
                    await page.screenshot(path="screenshots_unlock/upgrade_fail.png")
                except Exception:
                    pass
                return False
            else:
                print(f"\n  [?] unclear result, check manually")
                print(f"  page: {page_text[:200]}")
                try:
                    await page.screenshot(path="screenshots_unlock/upgrade_unclear.png")
                except Exception:
                    pass
                return None

    except TimeoutError as e:
        print(f"\n  TIMEOUT: {e}")
        return False
    except Exception as e:
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if pid:
            try:
                bb.close_browser(pid)
            except Exception:
                pass
            await asyncio.sleep(2)
            if own_profile:
                try:
                    bb.delete_browser(pid)
                except Exception:
                    pass


async def main():
    parser = argparse.ArgumentParser(description="Claude Max $20 SEPA Upgrade")
    parser.add_argument("--session-key", "-s", type=str, help="Claude sessionKey")
    parser.add_argument("--cookie-file", "-f", type=str, help="Claude cookie JSON file")
    parser.add_argument("--profile-id", "-p", type=str, help="existing BitBrowser profile ID")
    parser.add_argument("--node", type=str, default="auto",
                        help="Clash proxy node (none/auto/name)")
    parser.add_argument("--timeout", "-t", type=int, default=300,
                        help="timeout seconds (default 300)")
    args = parser.parse_args()

    # 确保截图目录存在
    os.makedirs("screenshots_unlock", exist_ok=True)

    print("=" * 50)
    print("  Claude Max $20 SEPA Upgrade")
    print("=" * 50)

    result = await upgrade_to_max(
        session_key=args.session_key,
        cookie_file=args.cookie_file,
        profile_id=args.profile_id,
        proxy_node=args.node,
        timeout=args.timeout,
    )

    if result is True:
        print("\n  RESULT: SUCCESS")
    elif result is False:
        print("\n  RESULT: FAILED")
    else:
        print("\n  RESULT: UNCLEAR (manual check needed)")


if __name__ == "__main__":
    asyncio.run(main())
