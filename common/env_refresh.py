# -*- coding: utf-8 -*-
"""common/env_refresh.py — 配置热更新调度。

WebUI 保存 .env 之后，需要让**当前进程**立刻看到新值（连通性检查、指纹浏览器
适配器、接码客户端都在本进程内跑）。历史实现是逐个 ``importlib.reload()`` 模块，
有两个硬伤：

1. ``from X import name`` 形式的调用方仍持有旧对象 —— reload 了模块，配置却
   没生效，行为与配置不一致，且极难排查；
2. reload 会把运行期注入的桩/钩子（测试 monkeypatch、插件挂载）一并冲掉，
   重载顺序稍有变化还会让某个模块读到半更新的配置。

改为：由各模块自己实现 ``refresh_from_env()``，就地更新它缓存的模块级常量；
本模块只负责按固定顺序调度。没有实现该钩子的模块会被安全跳过（例如
``common.direct_proxy`` 每次调用都现取 task_environment，本来就没有需要刷新的缓存）。
"""

from __future__ import annotations

import sys

# 顺序即依赖：config 必须最先刷新，其余模块的常量都是从它取的。
# 注意：凡模块级 ``from config import`` 且被 WebUI 进程内使用的模块都必须登记在这里
# 并实现钩子 —— 约定由 tests/test_config_snapshot_convention.py 静态锁死。
_REFRESH_ORDER = (
    "config",
    "common.proxy_switch",
    "common.cloak_browser",
    "common.roxy_browser",
    "common.sms",
    "common.temp_email",
    "adspower",
    "bitbrowser",
)


def refresh_all():
    """刷新已导入模块里缓存的环境派生素材。

    返回 ``{"refreshed": [...], "failed": [...]}``。单个模块刷新失败不会中断
    其余模块：配置已经落盘，最坏情况也只是本次进程内沿用旧值、下次启动生效。
    """
    refreshed = []
    failed = []
    for name in _REFRESH_ORDER:
        module = sys.modules.get(name)
        if module is None:
            continue
        hook = getattr(module, "refresh_from_env", None)
        if not callable(hook):
            continue
        try:
            hook()
        except Exception as exc:  # noqa: BLE001 - 热更新失败不该让保存请求失败
            failed.append("%s: %s" % (name, exc))
        else:
            refreshed.append(name)
    return {"refreshed": refreshed, "failed": failed}
