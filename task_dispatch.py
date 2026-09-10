# -*- coding: utf-8 -*-
"""task_dispatch.py — 「任务派发」协议的单一实现。

WebUI 在 frozen 模式下用 ``<exe> -u --task X.py [args...]``（或直接给 ``X.py``）
反向唤起 exe 来跑任务脚本，而不是再开一个 GUI/uvicorn 实例。所有入口都必须按
同一套规则解析这段命令行：

* ``reg_factory_desktop.py``         —— 源码形态入口（PyInstaller Analysis 入口）
* ``scripts/reg-factory-server.py``  —— macOS/Linux 脚本形态入口
* ``tools/binary_patch/wrapper_entry.py`` —— 已发布便携包的补丁入口

前两者直接使用本模块。第三个是例外：它被编译成 code object 注入到**官方冻结包**
的 CArchive 里，而官方包的 PYZ 中没有本模块，因此必须自带一份等价实现（详见该
文件内注释）；两份实现的行为一致性由 ``tests/test_task_dispatch.py`` 断言锁定。
"""

from __future__ import annotations

import os
import runpy
import sys

TASK_FLAG = "--task"
TASK_EXTENSION = ".py"


def normalize_args(raw_args):
    """剥掉 PyInstaller 引导器插入的 ``-u``（幂等）。"""
    args = list(raw_args or [])
    if args[:1] == ["-u"]:
        args = args[1:]
    return args


def parse_task(raw_args):
    """解析任务派发参数。

    :return: ``(target, rest)``；``target`` 是任务脚本相对路径，``rest`` 是转发给
        脚本的参数。**不是**任务派发、或 ``--task`` 后面没跟脚本名时返回 ``None``
        —— 调用方应当继续走「启动 WebUI」的正常路径，而不是把 ``--task`` 当成
        脚本名去报错（v2.2.6 之前 ``scripts/reg-factory-server.py`` 就是这个毛病）。
    """
    args = normalize_args(raw_args)
    if not args:
        return None
    head = args[0]
    if head == TASK_FLAG:
        if len(args) < 2 or not args[1]:
            return None
        return args[1], args[2:]
    if head.startswith(TASK_FLAG + "="):
        # 等号形式 ``--task=X.py``：空值同样退回正常启动路径
        target = head[len(TASK_FLAG) + 1:]
        if not target:
            return None
        return target, args[1:]
    if head.lower().endswith(TASK_EXTENSION):
        return head, args[1:]
    return None


def bundle_root():
    """任务脚本所在目录：frozen 下是 ``sys._MEIPASS``，源码运行时是项目根目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.abspath(__file__))


def configure_live_output():
    """让任务输出实时可见（WebUI 通过管道逐行读取）。"""
    os.environ["PYTHONUNBUFFERED"] = "1"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(line_buffering=True, write_through=True)
        except (OSError, ValueError):
            pass


def resolve_task_path(root, target):
    """解析任务脚本绝对路径并做防越目录校验，返回通过校验的路径。

    两侧都先 ``realpath``（解析 symlink，防符号链接逃逸），比较时再
    ``normcase``（Windows 大小写归一，避免 ``Root`` vs ``root`` 误判）。
    """
    target_path = os.path.abspath(os.path.join(root, target))
    root_real = os.path.normcase(os.path.realpath(root)) + os.sep
    if not os.path.normcase(os.path.realpath(target_path)).startswith(root_real):
        raise SystemExit("task script escapes bundle root: %s" % target)
    return target_path


def run_task(parsed, log=None):
    """在**本进程内**执行解析出来的任务脚本（不再拉起第二个窗口）。"""
    target, rest = parsed
    configure_live_output()
    root = bundle_root()
    # 防越目录：任务脚本必须落在 bundle 根目录内（--task=../../x.py 会被拒，
    # symlink 指向根外同样会被拒）。
    target_path = resolve_task_path(root, target)
    if not os.path.isfile(target_path):
        raise SystemExit("task script not found: %s" % target_path)
    sys.path.insert(0, root)
    sys.argv = [target_path] + list(rest)
    if callable(log):
        log("task dispatch → %s argv=%s" % (target_path, list(rest)))
    runpy.run_path(target_path, run_name="__main__")


def dispatch_task(raw_args, log=None):
    """命中任务派发则执行并返回 ``True``，否则返回 ``False`` 交给正常启动路径。"""
    parsed = parse_task(raw_args)
    if parsed is None:
        return False
    run_task(parsed, log=log)
    return True
