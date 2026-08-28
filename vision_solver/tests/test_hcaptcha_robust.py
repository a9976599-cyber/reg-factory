# -*- coding: utf-8 -*-
"""hCaptcha agent-captcha 鲁棒性测试：对 simple/medium/hard 三个 sitekey 各跑 N 轮，
统计过码率、记录每题类型/题干/投票/耗时，复盘图存 screenshots_vision/<level>_t<n>/。

用法: python vision_solver/tests/test_hcaptcha_robust.py [trials_per_key]   默认每个 key 3 轮
"""
import asyncio
import os
import sys
import time

from playwright.async_api import async_playwright

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from vision_solver import solve, CaptchaSpec

_PRESET = os.path.join(_ROOT, "vision_solver", "presets", "hcaptcha.json")
KEYS = {
    "simple": "7250b680-16e0-457e-8ec2-bddcc48a367c",
    "medium": "131c2f7a-7d26-486a-a0cc-56dc16ca9a62",
    "hard":   "368e0cd8-885c-498a-b35e-e2ccc3f4dcfc",
}
TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


async def click_checkbox(page):
    for _ in range(12):
        for f in page.frames:
            if "frame=checkbox" in f.url:
                try:
                    await f.locator("#checkbox").first.click(timeout=3000)
                    return True
                except Exception:
                    pass
        await asyncio.sleep(0.5)
    return False


async def token_len(page):
    try:
        return await page.evaluate(
            "()=>{const t=document.querySelector('textarea[name=h-captcha-response]');return t&&t.value?t.value.length:0;}"
        )
    except Exception:
        return 0


async def run_one(p, level, sitekey, trial):
    demo = f"https://accounts.hcaptcha.com/demo?sitekey={sitekey}"
    b = await p.chromium.launch(headless=False, args=["--lang=en-US"])
    ctx = await b.new_context(locale="en-US")
    page = await ctx.new_page()
    t0 = time.time()
    result = {"level": level, "trial": trial, "ok": False, "token": 0, "secs": 0, "err": ""}
    try:
        await page.goto(demo, wait_until="domcontentloaded")
        await asyncio.sleep(2.0)
        await click_checkbox(page)
        await asyncio.sleep(3)
        spec = CaptchaSpec.from_json(_PRESET)
        ok = await solve(page, spec, shot_dir=f"screenshots_vision/{level}_t{trial}")
        await asyncio.sleep(1.5)
        tok = await token_len(page)
        result.update(ok=bool(ok), token=tok, secs=round(time.time() - t0, 1))
    except Exception as e:
        result["err"] = str(e)[:80]
    finally:
        await b.close()
    return result


async def main():
    rows = []
    async with async_playwright() as p:
        for level, sitekey in KEYS.items():
            for t in range(TRIALS):
                print(f"\n########## {level.upper()} trial {t} (sitekey {sitekey[:8]}) ##########")
                r = await run_one(p, level, sitekey, t)
                # 过码判定：solve 返回 True 或 拿到 token
                r["pass"] = r["ok"] or r["token"] > 0
                rows.append(r)
                print(f">>> {level} t{t}: pass={r['pass']} ok={r['ok']} token={r['token']} {r['secs']}s {r['err']}")

    print("\n\n================ 鲁棒性汇总 ================")
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        a = agg[r["level"]]
        a[0] += 1
        a[1] += 1 if r["pass"] else 0
        a[2] += r["secs"]
    for level in KEYS:
        n, ok, secs = agg[level]
        if n:
            print(f"{level:8s}: {ok}/{n} 通过  平均 {secs/n:.1f}s/题")
    total = len(rows)
    passed = sum(1 for r in rows if r["pass"])
    print(f"{'TOTAL':8s}: {passed}/{total} 通过  ({100*passed/max(1,total):.0f}%)")


asyncio.run(main())
