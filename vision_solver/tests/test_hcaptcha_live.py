# -*- coding: utf-8 -*-
"""hCaptcha 过码端到端测试：打开 demo -> 点 checkbox 触发 -> vision_solver.solve 求解。
用法: python vision_solver/tests/test_hcaptcha_live.py [sitekey]
跑完会在 screenshots_vision/ 留 REVIEW_r*.png 复盘。"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

# 把仓库根加进 sys.path，从任意目录都能跑
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from vision_solver import solve, CaptchaSpec

_PRESET = os.path.join(_ROOT, "vision_solver", "presets", "hcaptcha.json")
SITEKEY = sys.argv[1] if len(sys.argv) > 1 else "7250b680-16e0-457e-8ec2-bddcc48a367c"
DEMO = f"https://accounts.hcaptcha.com/demo?sitekey={SITEKEY}"


async def click_checkbox(page):
    """点 anchor frame 的 checkbox 触发挑战。"""
    for _ in range(10):
        for f in page.frames:
            if "frame=checkbox" in f.url:
                try:
                    await f.locator("#checkbox").first.click(timeout=3000)
                    return True
                except Exception:
                    pass
        await asyncio.sleep(0.6)
    return False


async def hcaptcha_token(page):
    """读 hCaptcha 是否已产出 token（过码成功标志）。"""
    try:
        return await page.evaluate(
            "()=>{const t=document.querySelector('textarea[name=h-captcha-response],textarea[name=g-recaptcha-response]');return t&&t.value?t.value.length:0;}"
        )
    except Exception:
        return 0


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=False, args=["--lang=en-US"])
        ctx = await b.new_context(locale="en-US")
        page = await ctx.new_page()
        print(f"打开 {DEMO}")
        await page.goto(DEMO, wait_until="domcontentloaded")
        await asyncio.sleep(2.5)

        if not await click_checkbox(page):
            print("未能点到 checkbox")
        await asyncio.sleep(3)

        # 已有 challenge frame 才求解；否则可能直接过(无感)
        has_challenge = any("frame=challenge" in f.url for f in page.frames)
        print(f"challenge 出现={has_challenge}, 初始 token len={await hcaptcha_token(page)}")

        spec = CaptchaSpec.from_json(_PRESET)
        ok = await solve(page, spec)
        await asyncio.sleep(2)
        tok = await hcaptcha_token(page)
        print(f"\n==== solve 返回 {ok} | token len={tok} | {'PASS' if (ok or tok) else 'FAIL'} ====")
        await asyncio.sleep(3)
        await b.close()


asyncio.run(main())
