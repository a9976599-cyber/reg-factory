# -*- coding: utf-8 -*-
"""
recaptcha_android.py — 解 Android(BlueStacks/Appium)二登时的 Google reCAPTCHA v2。

场景:second_login_flow(gmail_register_local.py)在设备上二登,Google 弹
"Verify it's you -> I'm not a robot"。

关键实测结论(2026-07,活体验证):GMS 的 reCAPTCHA 虽在 WebView 里,但
**accessibility tree 几乎全暴露**——checkbox(rid=recaptcha-anchor,带 checked)、
挑战对话框(rid=rc-imageselect)、指令文字、VERIFY/reload 按钮都是可读节点。只有
九宫格的图片 tile 本身是 canvas 不暴露。所以本模块:
  - 定位/点击/成败判定 全走 accessibility tree(uiautomator dump),坐标精准且随
    页面重排自动跟踪(实测 checkbox bounds 会在两次渲染间移动,硬编码/vision 猜
    坐标必然翻车);
  - vision 只做它擅长的事:看编号九宫格截图,回答"哪些格含目标物"。

失败(无 key / 定位不到 / 超轮数)返回 False,调用方回退人工(second_login_manual)。
"""

from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 先抢先缓存本地 config(见 recaptcha_solver 里对同名 config.py 冲突的说明),
# 再引入 common.agent_captcha 的纯视觉工具。
try:
    import config as _local_config  # noqa: F401
except Exception:
    _local_config = None

_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

try:
    from common.agent_captcha import ask_vision, parse_json_answer
except Exception:  # pragma: no cover - 缺依赖时降级为不可用
    ask_vision = None
    parse_json_answer = None


def _log(message: str) -> None:
    print(message.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def available() -> bool:
    """有 vision 求解器且至少一个 key 才可用。"""
    if ask_vision is None:
        return False
    try:
        from common.agent_captcha import _load_keys

        return bool(_load_keys())
    except Exception:
        return False


# 兼容旧调用名
usable = available


def _adb_path() -> str:
    return os.environ.get("ADB_PATH") or getattr(_local_config, "ADB_PATH", "") or "adb"


# ---------------- accessibility tree / adb ----------------
def _nodes(device: str) -> list[dict[str, str]]:
    """uiautomator dump 当前界面所有节点属性。"""
    adb = _adb_path()
    subprocess.run(
        [adb, "-s", device, "shell", "uiautomator", "dump", "--compressed", "/sdcard/_rc.xml"],
        capture_output=True,
        timeout=25,
    )
    p = subprocess.run(
        [adb, "-s", device, "exec-out", "cat", "/sdcard/_rc.xml"],
        capture_output=True,
        timeout=15,
    )
    raw = p.stdout.decode("utf-8", "replace")
    if not raw.startswith("<?xml"):
        return []
    try:
        return [dict(n.attrib) for n in ET.fromstring(raw).iter("node")]
    except ET.ParseError:
        return []


def _find(nodes, rid: str = "", text: str = ""):
    for n in nodes:
        if rid and n.get("resource-id") != rid:
            continue
        if text and text.lower() not in (n.get("text") or "").lower():
            continue
        return n
    return None


def _bounds(node):
    if not node:
        return None
    vals = [int(v) for v in re.findall(r"-?\d+", node.get("bounds", ""))]
    return vals if len(vals) == 4 else None


def _center(node):
    b = _bounds(node)
    return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) if b else None


def _tap(device: str, x: int, y: int) -> None:
    subprocess.run([_adb_path(), "-s", device, "shell", "input", "tap", str(int(x)), str(int(y))], timeout=15)
    time.sleep(0.5)


def _screenshot(device: str):
    """adb screencap -> PIL.Image RGB。失败返回 None。"""
    try:
        p = subprocess.run([_adb_path(), "-s", device, "exec-out", "screencap", "-p"], capture_output=True, timeout=30)
        from PIL import Image

        return Image.open(io.BytesIO(p.stdout)).convert("RGB")
    except Exception as exc:
        _log(f"[recaptcha-android] screencap failed: {exc}")
        return None


# ---------------- 挑战几何 / 提问 ----------------
def _instruction(nodes) -> str:
    """挑战指令:'Select all images with' + 目标(目标常在下一个 View 节点的 text)。"""
    head = _find(nodes, text="select all")
    parts = []
    if head:
        parts.append((head.get("text") or "").strip())
    # 目标名词节点(如 'bicycles' / 'traffic lights')通常紧随其后、rid 为空的 View
    dlg = _find(nodes, rid="rc-imageselect")
    if dlg:
        # 收集对话框内前几条 text,拼成完整指令
        texts = [(n.get("text") or "").strip() for n in nodes if (n.get("text") or "").strip()]
        # 找到 'select all' 后紧邻的一条作为目标
        for i, t in enumerate(texts):
            if "select all" in t.lower() and i + 1 < len(texts):
                nxt = texts[i + 1]
                if nxt.lower() not in ("", "click verify once there are none left"):
                    parts.append(nxt)
                break
    return " ".join(p for p in parts if p).strip()


def _target_object(instruction: str) -> str:
    low = instruction.lower()
    for prefix in ("select all images with", "select all squares with", "select all images containing", "select all"):
        if prefix in low:
            return instruction[low.index(prefix) + len(prefix):].strip(" .:\n") or instruction
    return instruction


def _is_dynamic(nodes) -> bool:
    return bool(_find(nodes, text="none left") or _find(nodes, text="once there are"))


def _grid_box(nodes):
    """从 rc-imageselect 对话框 + VERIFY 按钮栏推出正方形九宫格像素区 (x0,y0,x1,y1)。
    对话框宽度=九宫格宽度;底边贴按钮栏顶上方;顶边=底边-宽度(正方形)。"""
    dlg = _find(nodes, rid="rc-imageselect")
    if not dlg:
        return None
    db = _bounds(dlg)
    if not db:
        return None
    x0, x1 = db[0], db[2]
    side = x1 - x0
    verify = _find(nodes, rid="recaptcha-verify-button")
    reload_btn = _find(nodes, rid="recaptcha-reload-button")
    bar = _bounds(verify) or _bounds(reload_btn)
    bar_top = bar[1] if bar else db[3] - 84
    y1 = bar_top - 4
    y0 = y1 - side
    return (x0, max(db[1], y0), x1, y1)


def _grid_cols(nodes) -> int:
    """3x3(dynamic/reload)最常见;4x4 为静态一次性。reCAPTCHA 无 tile 节点,
    默认 3;若指令含 'square' 或对话框特别高则视作 4。"""
    if _is_dynamic(nodes):
        return 3
    return 3


def _crop_number(im, box, cols=3):
    """裁网格区、叠红 #编号,返回 (b64, [中心绝对像素...])。"""
    from PIL import ImageDraw

    x0, y0, x1, y1 = box
    crop = im.crop((x0, y0, x1, y1)).copy()
    draw = ImageDraw.Draw(crop)
    cw, ch = crop.width / cols, crop.height / cols
    centers = []
    for i in range(cols * cols):
        r, c = divmod(i, cols)
        lx, ly = int(c * cw) + 3, int(r * ch) + 3
        draw.rectangle([lx, ly, lx + 22, ly + 17], fill=(255, 255, 255))
        draw.text((lx + 3, ly + 3), str(i), fill=(220, 0, 0))
        centers.append((x0 + int((c + 0.5) * cw), y0 + int((r + 0.5) * ch)))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), centers


_PROMPT = (
    "You are an accessibility helper solving a Google reCAPTCHA image grid. "
    "The image is a {cols}x{cols} grid, tiles numbered 0..{last} in the RED label at each "
    "tile's top-left (row-major: top-left is 0, left-to-right then down).\n"
    'TASK: "{instruction}"\n'
    "Return EVERY tile number whose image shows ANY visible part of the target: {target}. "
    "Include a tile if any part of the target crosses into it. If none match, return an empty list.\n"
    'Output ONLY JSON on the last line: {{"tiles": [<numbers>]}}'
)


def _ask_tiles(instruction, target, b64, cols, n):
    if ask_vision is None:
        return []
    prompt = _PROMPT.format(cols=cols, last=n - 1, instruction=instruction, target=target)
    txt = ask_vision(prompt, b64, max_tokens=400)
    data = parse_json_answer(txt) if (txt and parse_json_answer) else None
    tiles = []
    if isinstance(data, dict):
        raw = data.get("tiles") or data.get("answer") or []
        if isinstance(raw, list):
            for v in raw:
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    continue
                if 0 <= iv < n and iv not in tiles:
                    tiles.append(iv)
    return tiles


def _recaptcha_present(nodes) -> bool:
    return bool(_find(nodes, rid="recaptcha-anchor") or _find(nodes, rid="rc-imageselect"))


def _anchor_checked(nodes) -> bool:
    cb = _find(nodes, rid="recaptcha-anchor")
    return bool(cb and (cb.get("checked") == "true"))


def _solved(nodes) -> bool:
    """挑战通过判定:图片对话框已消失,且(复选框已勾 或 reCAPTCHA 整个消失)。
    注意:过验证后常回到 anchor 页、复选框变绿(checked=true)但节点仍在——此时算过,
    外层 second_login_flow 再点 Next 推进即可。仍在 rc-imageselect = 未过。"""
    if _find(nodes, rid="rc-imageselect"):
        return False
    if _anchor_checked(nodes):
        return True
    if not _find(nodes, rid="recaptcha-anchor"):
        return True
    return False


# ---------------- 主流程 ----------------
def solve(driver, adb_port, max_rounds: int = 8) -> bool:
    """在 Android 上解 reCAPTCHA v2。全程走 accessibility tree 定位,vision 仅选格。
    返回 True = reCAPTCHA 已从页面消失(挑战通过)。driver 参数保留兼容,实际用 adb。

    成败判定:tree 里 recaptcha-anchor / rc-imageselect 都不在 = 通过。
    """
    if not available():
        _log("[recaptcha-android] vision solver unavailable (no key); skip")
        return False
    if not adb_port:
        _log("[recaptcha-android] no adb_port; cannot drive tree")
        return False
    device = f"127.0.0.1:{adb_port}"

    # WebView 需要几秒才把 reCAPTCHA 节点注入 accessibility tree；
    # 检测到 "confirm you're not a robot" 文字时节点可能还没出现，等最多 12s。
    deadline = time.time() + 12
    while True:
        nodes = _nodes(device)
        if _recaptcha_present(nodes):
            break
        if time.time() >= deadline:
            _log("[recaptcha-android] no reCAPTCHA in tree after 12s wait; nothing to do")
            return False
        _log("[recaptcha-android] waiting for reCAPTCHA tree nodes to load...")
        time.sleep(2)

    if _solved(nodes):
        _log("[recaptcha-android] already solved on entry (anchor checked)")
        return True

    # 1) 若在 anchor 页(有 checkbox 但还没弹挑战、且未勾),点复选框
    if _find(nodes, rid="recaptcha-anchor") and not _find(nodes, rid="rc-imageselect") and not _anchor_checked(nodes):
        cb = _find(nodes, rid="recaptcha-anchor")
        c = _center(cb)
        if not c:
            _log("[recaptcha-android] checkbox has no bounds; stop")
            return False
        _log(f"[recaptcha-android] tapping checkbox at {c[0]},{c[1]} (bounds={cb.get('bounds')})")
        _tap(device, *c)
        time.sleep(3)
        nodes = _nodes(device)
        # 点完:要么直接过(anchor 消失/变绿勾),要么弹九宫格
        if _solved(nodes):
            _log("[recaptcha-android] passed with checkbox only")
            return True

    # 2) 图片挑战循环
    for rnd in range(max_rounds):
        nodes = _nodes(device)
        if _solved(nodes):
            _log(f"[recaptcha-android] reCAPTCHA solved after {rnd} round(s)")
            return True
        if not _find(nodes, rid="rc-imageselect"):
            # 回到 anchor 页(挑战被重置);再点一次复选框
            cb = _find(nodes, rid="recaptcha-anchor")
            c = _center(cb)
            if c:
                _tap(device, *c)
                time.sleep(3)
            continue

        box = _grid_box(nodes)
        if not box or box[3] - box[1] < 60:
            _log(f"[recaptcha-android] bad grid box {box}; stop")
            return False
        cols = _grid_cols(nodes)
        dynamic = _is_dynamic(nodes)
        instruction = _instruction(nodes)
        target = _target_object(instruction)
        _log(f"[recaptcha-android] R{rnd} {cols}x{cols} dynamic={dynamic} box={box} instr={instruction!r}")

        im = _screenshot(device)
        if im is None:
            _log("[recaptcha-android] screenshot failed; stop")
            return False
        b64, centers = _crop_number(im, box, cols)
        tiles = _ask_tiles(instruction, target, b64, cols, cols * cols)
        _log(f"[recaptcha-android] R{rnd} vision picked {tiles}")

        if not tiles:
            # 空答:动态题=已清空,点 VERIFY;静态题=换一批
            if dynamic:
                v = _find(nodes, rid="recaptcha-verify-button")
                vc = _center(v)
                if vc:
                    _tap(device, *vc)
                    _log(f"[recaptcha-android] tapped VERIFY at {vc[0]},{vc[1]}")
                time.sleep(3)
                continue
            rl = _find(nodes, rid="recaptcha-reload-button")
            rc = _center(rl)
            if rc:
                _tap(device, *rc)
            time.sleep(2)
            continue

        for t in tiles:
            if 0 <= t < len(centers):
                _tap(device, *centers[t])
                time.sleep(0.5 if dynamic else 0.3)

        if dynamic:
            # 动态题:点完等淡出/加载,再看当前网格还有没有新目标;连续空答才 VERIFY
            time.sleep(2.5)
            for _extra in range(4):
                nodes2 = _nodes(device)
                if not _find(nodes2, rid="rc-imageselect"):
                    break
                box2 = _grid_box(nodes2)
                im2 = _screenshot(device)
                if not box2 or im2 is None:
                    break
                b64b, centers2 = _crop_number(im2, box2, cols)
                more = _ask_tiles(instruction, target, b64b, cols, cols * cols)
                if not more:
                    break
                _log(f"[recaptcha-android] R{rnd} extra picked {more}")
                for t in more:
                    if 0 <= t < len(centers2):
                        _tap(device, *centers2[t])
                        time.sleep(0.5)
                time.sleep(2.5)
            nodes = _nodes(device)
            v = _find(nodes, rid="recaptcha-verify-button")
            vc = _center(v)
            if vc:
                _tap(device, *vc)
                _log(f"[recaptcha-android] tapped VERIFY at {vc[0]},{vc[1]}")
        else:
            v = _find(nodes, rid="recaptcha-verify-button")
            vc = _center(v)
            if vc:
                _tap(device, *vc)
        time.sleep(3)

        nodes = _nodes(device)
        if _solved(nodes):
            _log(f"[recaptcha-android] solved after round {rnd}")
            return True

    nodes = _nodes(device)
    passed = _solved(nodes)
    _log(f"[recaptcha-android] finished {max_rounds} rounds; passed={passed}")
    return passed
