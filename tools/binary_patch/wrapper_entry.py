# -*- coding: utf-8 -*-
"""reg_factory_desktop —— 补丁版入口脚本（源码形态）。

用途
----
官方 Windows 便携包（PyInstaller onedir）的入口 ``reg_factory_desktop``
不识别 ``-u --task <script.py>``，而 WebUI 的 ``/api/run`` 在 frozen 模式下
正是用 ``[exe, "-u", "--task", X.py, ...]`` 反向唤起 exe 来跑任务的。
结果是：点“运行任务”只弹第二个 webview 窗口，真任务没跑，指纹浏览器也不开。

本入口补上 CLI 派发：

* 命令行是 ``-u --task X.py ...``（或直接给 ``X.py``）：在本进程内用
  ``runpy.run_path`` 执行该脚本，**不再拉起第二个 GUI**；
* 其它情况：把官方入口的 code object 原样 ``exec`` 进 ``__main__``，
  授权校验、内嵌后端、WebUI + webview 全部走官方原路径，不做任何改动。

为什么这里有一份 ``task_dispatch`` 的副本
----------------------------------------
解析规则与 ``task_dispatch.py`` 必须一致，但本文件是被编译成 code object
**注入官方冻结包**的，而官方包的 PYZ 里根本没有 ``task_dispatch`` 这个模块
——补丁只替换 CArchive 中的入口一条，不会重建 PYZ。因此这里只能自带一份等价
实现；两份实现的行为一致性由 ``tests/test_task_dispatch.py`` 断言锁定。

注入方式
--------
``__RF_ORIG_CODE__`` 是一个占位常量；构建时由 ``patch_exe.py`` 用官方入口的
code object 替换（见 ``code.replace(co_consts=...)``）。因此本文件**不能**
单独运行，必须经 patch 构建后嵌入 exe。
"""

import os
import sys

_RF_ORIG = "__RF_ORIG_CODE__"


def _rf_configure_live_output():
    """让任务输出实时可见（WebUI 通过管道读取）。"""
    os.environ["PYTHONUNBUFFERED"] = "1"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(line_buffering=True, write_through=True)
            except Exception:
                pass


def _rf_bundle_root():
    """frozen 下取 _MEIPASS，源码运行时取本文件目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.abspath(__file__))


def _rf_parse_task(raw_args):
    """与 ``task_dispatch.parse_task`` 同形：返回 (target, rest) 或 None。

    ``--task`` 后面没跟脚本名时返回 None（退回官方入口启动 WebUI），
    不把它当成脚本名去报错。
    """
    args = list(raw_args or [])
    if args[:1] == ["-u"]:
        args = args[1:]
    if not args:
        return None
    head = args[0]
    if head == "--task":
        if len(args) < 2 or not args[1]:
            return None
        return args[1], args[2:]
    if head.startswith("--task="):
        # 等号形式 ``--task=X.py``：空值同样退回官方入口
        target = head[len("--task="):]
        if not target:
            return None
        return target, args[1:]
    if head.lower().endswith(".py"):
        return head, args[1:]
    return None


def _rf_dispatch_task(raw_args):
    """命中任务派发则执行并返回 True，否则返回 False 交给官方入口。"""
    parsed = _rf_parse_task(raw_args)
    if parsed is None:
        return False
    target, rest = parsed

    _rf_configure_live_output()

    bundle_root = _rf_bundle_root()
    target_path = os.path.join(bundle_root, target)
    if not os.path.isfile(target_path):
        raise SystemExit("task script not found: %s" % target_path)

    sys.path.insert(0, bundle_root)
    sys.argv = [target_path] + list(rest)

    import runpy
    runpy.run_path(target_path, run_name="__main__")
    return True


if not _rf_dispatch_task(sys.argv[1:]):
    exec(_RF_ORIG, sys.modules["__main__"].__dict__)
