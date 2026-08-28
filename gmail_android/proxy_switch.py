# -*- coding: utf-8 -*-
"""Switch Clash/mihomo to a usable node before an emulator registration run."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _enabled() -> bool:
    return _cfg("SWITCH_NODE", "1").lower() in {"1", "true", "yes", "on"}


def _group() -> str:
    return _cfg("CLASH_GROUP", "GLOBAL")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    secret = _cfg("CLASH_SECRET")
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _get(path: str):
    request = urllib.request.Request(
        f"{_cfg('CLASH_API', 'http://127.0.0.1:9097')}{path}",
        headers=_headers(),
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read())


def _int_cfg(name: str, default: int) -> int:
    try:
        return int(_cfg(name, str(default)))
    except ValueError:
        return default


def list_nodes(group: str | None = None) -> list[str]:
    group = group or _group()
    return _get(f"/proxies/{urllib.parse.quote(group)}").get("all", [])


def current_node(group: str | None = None) -> str | None:
    group = group or _group()
    return _get(f"/proxies/{urllib.parse.quote(group)}").get("now")


def set_node(name: str, group: str | None = None) -> bool:
    group = group or _group()
    data = json.dumps({"name": name}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{_cfg('CLASH_API', 'http://127.0.0.1:9097')}/proxies/{urllib.parse.quote(group)}",
        data=data,
        method="PUT",
        headers=_headers(),
    )
    urllib.request.urlopen(request, timeout=8).read()
    return True


def concrete_nodes(group: str | None = None) -> list[str]:
    group = group or _group()
    skip = {"DIRECT", "REJECT", "PASS", "GLOBAL"}
    return [
        node
        for node in list_nodes(group)
        if node not in skip
        and "\u76f4" not in node
        and "\uff1a" not in node
        and any(ch.isdigit() for ch in node)
    ]


def node_delay(name: str, url: str | None = None, timeout_ms: int | None = None) -> int | None:
    url = url or _cfg("NODE_DELAY_URL", "https://www.google.com/generate_204")
    timeout_ms = timeout_ms or _int_cfg("NODE_DELAY_TIMEOUT_MS", 2000)
    try:
        data = _get(
            f"/proxies/{urllib.parse.quote(name)}/delay"
            f"?timeout={timeout_ms}&url={urllib.parse.quote(url)}"
        )
        delay = data.get("delay")
        return int(delay) if delay is not None else None
    except Exception:
        return None


def _region_keywords() -> list[str]:
    raw = _cfg(
        "NODE_REGION_KEYWORDS",
        "\u7f8e\u56fd,\u65e5\u672c,\u7f8e\u570b,United States,USA,America,Japan,JP",
    )
    keywords = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "\\u" in item:
            try:
                item = item.encode("ascii").decode("unicode_escape")
            except Exception:
                pass
        keywords.append(item.casefold())
    return keywords


def preferred_region_nodes(nodes: list[str]) -> list[str]:
    keywords = _region_keywords()
    if not keywords:
        return list(nodes)
    return [
        name
        for name in nodes
        if any(keyword in name.casefold() for keyword in keywords)
    ]


def _proxy_url() -> str:
    proxy = _cfg("CLASH_PROXY", "http://127.0.0.1:7897").strip()
    if proxy and "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def _max_delay_ms() -> int | None:
    value = _int_cfg("NODE_DELAY_MAX_MS", 2000)
    return value if value > 0 else None


def proxy_probe(url: str | None = None, timeout: float | None = None) -> tuple[bool, str]:
    timeout = timeout if timeout is not None else float(_cfg("NODE_PROBE_TIMEOUT", "8"))
    if url is None:
        raw = _cfg(
            "NODE_PROBE_URLS",
            _cfg("NODE_PROBE_URL", "https://www.google.com/generate_204,https://www.google.com"),
        )
        urls = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        urls = [url]

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": _proxy_url(), "https": _proxy_url()})
    )
    details = []
    for probe_url in urls:
        request = urllib.request.Request(probe_url, headers={"User-Agent": "Mozilla/5.0"})
        start = time.time()
        try:
            with opener.open(request, timeout=timeout) as response:
                elapsed_ms = int((time.time() - start) * 1000)
                if not (200 <= response.status < 400):
                    return False, f"{probe_url}: http={response.status}"
                details.append(f"{urllib.parse.urlparse(probe_url).netloc}:{response.status}/{elapsed_ms}ms")
        except urllib.error.HTTPError as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            if exc.code < 500:
                details.append(f"{urllib.parse.urlparse(probe_url).netloc}:{exc.code}/{elapsed_ms}ms")
                continue
            return False, f"{urllib.parse.urlparse(probe_url).netloc}: http={exc.code}"
        except Exception as exc:
            return False, f"{urllib.parse.urlparse(probe_url).netloc}: {str(exc)[:80]}"
    return True, "; ".join(details)


def switch_random_node(log=print, verify: bool = True, max_try: int | None = None) -> str | None:
    if not _enabled():
        return None
    try:
        nodes = concrete_nodes()
    except Exception as exc:
        log(f"[node] unable to connect to Clash API; skip node switch: {exc}")
        return None
    if not nodes:
        log("[node] no concrete nodes in Clash group; skip node switch")
        return None

    try:
        current = current_node()
    except Exception:
        current = None

    candidates = [node for node in nodes if node != current] or nodes
    preferred = preferred_region_nodes(candidates)
    if preferred:
        candidates = preferred

    max_try = max_try if max_try is not None else _int_cfg("NODE_SWITCH_MAX_TRY", 20)
    if max_try <= 0:
        max_try = len(candidates)

    random.shuffle(candidates)
    max_delay = _max_delay_ms()
    scored = []
    for name in candidates[:max_try]:
        delay = node_delay(name)
        if delay is None:
            log(f"[node] {name} delay probe failed; skip")
            continue
        if max_delay is not None and delay > max_delay:
            log(f"[node] {name} delay={delay}ms > {max_delay}ms; skip")
            continue
        scored.append((delay, name))

    if not scored:
        log("[node] no node passed delay probe; keep current node")
        return None

    scored.sort(key=lambda item: (item[0], item[1].casefold()))
    for delay, name in scored:
        try:
            set_node(name)
        except Exception as exc:
            log(f"[node] switch to {name} failed: {str(exc)[:60]}")
            continue
        if not verify:
            log(f"[node] switched node: {current} -> {name} (delay={delay}ms)")
            return name
        ok, detail = proxy_probe()
        if ok:
            log(f"[node] switched node: {current} -> {name} (delay={delay}ms, probe={detail})")
            return name
        log(f"[node] {name} delay={delay}ms but proxy probe failed: {detail}; try next")

    log("[node] no usable node found; keep current node")
    return None


if __name__ == "__main__":
    switch_random_node()
