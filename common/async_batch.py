# -*- coding: utf-8 -*-
"""common/async_batch.py — 批量并发的异常隔离。

``asyncio.gather`` 默认「一错全抛」：任一协程抛异常时 gather 立刻向上抛，
整批作业直接失败；而其余已经在跑的协程不会被取消，继续在后台跑成僵尸任务
（既看不到结果，也不会被回收）。

批量账号类作业（注册 / 解锁 / 导入 / 资格检查）几乎都不希望这样：单个账号的
网络抖动或上游 4xx 不该让整批作废。``gather_settled`` 把单个协程的异常折算成
一条结果，同时保留 ``CancelledError`` 的传播语义（取消是控制流，不是失败）。
"""

from __future__ import annotations

import asyncio

__all__ = ["gather_settled"]


async def gather_settled(awaitables, *, on_error=None):
    """并发执行 ``awaitables``，把单个协程的异常折算成结果。

    :param awaitables: 协程/可等待对象集合，按顺序展开（生成器会被立即消费）。
    :param on_error: ``on_error(exc, index) -> value``。缺省返回 ``None``，
        对「失败即视为该项未完成」的汇总逻辑正好是正确取值。
    :return: 与输入等长的结果列表，顺序与输入一致。
    """
    coros = list(awaitables)

    async def _settle(index, awaitable):
        try:
            return await awaitable
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 见模块 docstring
            if on_error is None:
                return None
            return on_error(exc, index)

    return list(await asyncio.gather(*(_settle(i, coro) for i, coro in enumerate(coros))))
