# -*- coding: utf-8 -*-
"""reg_factory_desktop —— 补丁版入口脚本（源码形态，v3）。

用途
----
官方 Windows 便携包（PyInstaller onedir）的入口 ``reg_factory_desktop``
不识别 ``-u --task <script.py>``，而 WebUI 的 ``/api/run`` 在 frozen 模式下
正是用 ``[exe, "-u", "--task", X.py, ...]`` 反向唤起 exe 来跑任务的。

v1/v2 只补了 CLI 派发。v3 追加两个关键能力：

1. **松散模块影子加载**（本版核心）
   官方 PYZ 里冻结的是旧版 ``webui.server`` / ``common.sms`` /
   ``common.session_export``，且 PYZ 的 ``common`` 是常规包 —— 它的
   ``__path__`` 指向 PYZ 内部，使 ``_internal/common/*.py`` 松散修复文件
   （async_batch / env_refresh 等）在冻结模式下**永远不可见**。
   结果是：仓库里修好的 WebUI 掩码/净化、SMS 日志、原子导出……
   在发布包里从未生效过。这里在官方入口运行**之前**，把松散修复版
   预注册进 ``sys.modules``，官方入口随后的 ``import`` 全部命中新版。
   任一影子加载失败都回退官方 PYZ 版本（撤销注册 + 记日志），保证
   「宁可旧版也不白屏」。

2. **--task 目标逃逸检查**
   任务脚本必须是 bundle 根内的真实路径（realpath + normcase 对齐
   Windows 大小写/分隔符），堵住 ``..\\..\\evil.py`` 与符号链接逃逸。

* 其它情况：把官方入口的 code object 原样 ``exec`` 进 ``__main__``，
  授权校验、内嵌后端、WebUI + webview 全部走官方原路径，不做任何改动。

为什么这里有一份 ``task_dispatch`` 的副本
----------------------------------------
解析规则与 ``task_dispatch.py`` 必须一致，但本文件是被编译成 code object
**注入官方冻结包**的，行为一致性由 ``tests/test_task_dispatch.py`` 断言锁定。

注入方式
--------
``__RF_ORIG_CODE__`` 是一个占位常量；构建时由 ``patch_exe.py`` 用官方入口的
code object 替换（见 ``code.replace(co_consts=...)``）。因此本文件**不能**
单独运行，必须经 patch 构建后嵌入 exe。
"""

import os
import sys

_RF_ORIG = "__RF_ORIG_CODE__"

# (PYZ/包内模块名, 相对 bundle 根的松散文件路径)
# 顺序即加载顺序：webui.server 依赖 env_refresh/async_batch，必须排最后。
_RF_SHADOW_MODULES = (
    ("task_dispatch", "task_dispatch.py"),
    ("common.sms", "common/sms.py"),
    ("common.session_export", "common/session_export.py"),
    ("common.env_refresh", "common/env_refresh.py"),
    ("common.async_batch", "common/async_batch.py"),
    ("webui.server", "webui/server.py"),
)


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
    """frozen 下取 _MEIPASS，源码运行时取本文件目录；exec 注入上下文无 __file__ 时退 cwd。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _rf_shadow_log(message):
    """尽力把影子加载结果追加到桌面日志，失败静默。"""
    try:
        path = os.path.join(_rf_bundle_root(), "reg-factory-desktop.log")
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


def _rf_shadow_load():
    """把松散修复版模块预注册进 sys.modules；失败逐个回退官方版本。"""
    import importlib
    import importlib.util

    try:
        root = _rf_bundle_root()
    except Exception:  # noqa: BLE001 - 拿不到根目录就只能全量回退官方版
        return []
    loaded = []
    for modname, rel in _RF_SHADOW_MODULES:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path) or modname in sys.modules:
            continue
        try:
            parent = modname.rpartition(".")[0]
            if parent and parent not in sys.modules:
                importlib.import_module(parent)
            spec = importlib.util.spec_from_file_location(modname, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[modname] = module
            spec.loader.exec_module(module)
            loaded.append(modname)
        except BaseException as exc:  # noqa: BLE001 - 影子失败必须回退
            sys.modules.pop(modname, None)
            _rf_shadow_log("[wrapper] shadow-load failed for %s: %r" % (modname, exc))
    if loaded:
        _rf_shadow_log("[wrapper] shadow-loaded: %s" % ", ".join(loaded))
    return loaded


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


def _rf_safe_task_path(bundle_root, target):
    """任务脚本必须落在 bundle 根内（realpath + normcase 防逃逸）。"""
    root_real = os.path.normcase(os.path.realpath(bundle_root))
    target_real = os.path.normcase(
        os.path.realpath(os.path.join(bundle_root, target))
    )
    if target_real != root_real and not target_real.startswith(root_real + os.sep):
        raise SystemExit("task script outside bundle root: %s" % target)
    return target_real


def _rf_dispatch_task(raw_args):
    """命中任务派发则执行并返回 True，否则返回 False 交给官方入口。"""
    parsed = _rf_parse_task(raw_args)
    if parsed is None:
        return False
    target, rest = parsed

    _rf_configure_live_output()

    bundle_root = _rf_bundle_root()
    target_path = _rf_safe_task_path(bundle_root, target)

    sys.path.insert(0, bundle_root)
    sys.argv = [target_path] + list(rest)

    import runpy
    runpy.run_path(target_path, run_name="__main__")
    return True


_rf_shadow_load()
if not _rf_dispatch_task(sys.argv[1:]):
    exec(_RF_ORIG, sys.modules["__main__"].__dict__)
