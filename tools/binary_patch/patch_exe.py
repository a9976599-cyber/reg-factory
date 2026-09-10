#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给官方 reg-factory.exe 打入口级二进制补丁（保留授权子系统）。

背景
----
官方 Windows 便携包是 PyInstaller onedir 冻结包，入口 ``reg_factory_desktop``
不识别 ``-u --task <script.py>``；而 WebUI 的 ``/api/run`` 在冻结模式下正是用
``[exe, "-u", "--task", X.py, ...]`` 反向唤起 exe 来跑任务。于是“点运行任务”
只会再弹一个 webview，任务不执行、指纹浏览器不启动。

「从源码重新打包」行不通：官方发布包里的授权子系统（``ysq_auth`` /
``yunshouquan_sdk``）是闭源的，公开仓库里没有，重建出来的 exe 授权入口会整体消失。

因此这里只做**最小改动**：把 CArchive 中 ``reg_factory_desktop`` 这一条
PYSOURCE 替换成补丁入口，其余条目（运行时钩子、pyimod*、以及装着授权模块的
整个 PYZ）逐字节原样搬运，bootloader 前缀也原样保留。

补丁入口见 ``wrapper_entry.py``：命中 ``--task`` 就在本进程内 runpy 执行任务，
否则把官方入口 code object 原样 exec，授权校验与 WebUI 完全走官方原路径。

用法
----
    python patch_exe.py --official-exe reg-factory.exe.buggy --out reg-factory.exe

必须用与目标 exe **同一个 Python 次版本号**的解释器运行（官方 2.2.4 是 3.12）；
脚本会在开始时校验并明确报错。

本脚本只依赖标准库。
"""

from __future__ import annotations

import argparse
import marshal
import os
import struct
import sys
import zlib

MAGIC = b"MEI\014\013\012\013\016"
COOKIE_FORMAT = "!8sIIII64s"
COOKIE_LENGTH = struct.calcsize(COOKIE_FORMAT)
TOC_ENTRY_FORMAT = "!IIIIBc"
TOC_ENTRY_LENGTH = struct.calcsize(TOC_ENTRY_FORMAT)

DEFAULT_TARGET = "reg_factory_desktop"
MARKER = "__RF_ORIG_CODE__"

TYPE_RUNTIME_OPTION = "o"


class Archive:
    """极简 PyInstaller CArchive 读取器（只读 cookie + TOC + 条目数据）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        with open(path, "rb") as fp:
            self.blob = fp.read()

        pos = self.blob.rfind(MAGIC)
        if pos < 0:
            raise SystemExit("cookie magic not found: %s" % path)
        (magic, archive_length, toc_offset, toc_length,
         pyvers, pylib) = struct.unpack(COOKIE_FORMAT, self.blob[pos:pos + COOKIE_LENGTH])
        self.end_offset = pos + COOKIE_LENGTH
        self.start_offset = self.end_offset - archive_length
        self.toc_offset = toc_offset
        self.toc_length = toc_length
        self.pyvers = pyvers
        self.pylib = pylib.rstrip(b"\0").decode("ascii", "replace")
        self.prefix = self.blob[:self.start_offset]
        self.entries = self._parse_toc()

    def _parse_toc(self):
        toc_data = self.blob[self.start_offset + self.toc_offset:
                             self.start_offset + self.toc_offset + self.toc_length]
        entries = []
        cur = 0
        while cur < len(toc_data):
            (entry_length, entry_offset, data_length, uncompressed_length,
             compression_flag, typecode) = struct.unpack(
                TOC_ENTRY_FORMAT, toc_data[cur:cur + TOC_ENTRY_LENGTH])
            cur += TOC_ENTRY_LENGTH
            name_length = entry_length - TOC_ENTRY_LENGTH
            name = toc_data[cur:cur + name_length].rstrip(b"\0").decode("utf-8")
            cur += name_length
            entries.append({
                "name": name,
                "type": typecode.decode("ascii"),
                "offset": entry_offset,
                "data_length": data_length,
                "uncompressed_length": uncompressed_length,
                "compress": compression_flag,
            })
        return entries

    def find(self, name: str):
        for e in self.entries:
            if e["name"] == name:
                return e
        return None

    def read_entry(self, entry) -> bytes:
        if entry["type"] == TYPE_RUNTIME_OPTION:
            return b""
        raw = self.blob[self.start_offset + entry["offset"]:
                        self.start_offset + entry["offset"] + entry["data_length"]]
        if entry["compress"]:
            raw = zlib.decompress(raw)
        return raw


def serialize_toc(entries) -> bytes:
    """按 PyInstaller 规则序列化 TOC：名称补齐到 16 字节边界。"""
    out = []
    for e in entries:
        name = e["name"].encode("utf-8")
        name_length = len(name) + 1
        entry_length = TOC_ENTRY_LENGTH + name_length
        if entry_length % 16:
            name_length += 16 - (entry_length % 16)
        out.append(struct.pack(
            TOC_ENTRY_FORMAT + "%ds" % name_length,
            TOC_ENTRY_LENGTH + name_length,
            e["offset"],
            e["data_length"],
            e["uncompressed_length"],
            e["compress"],
            e["type"].encode("ascii"),
            name,
        ))
    return b"".join(out)


def build_payload(wrapper_path: str, official_code, target_name: str) -> bytes:
    """编译 wrapper 源码并把官方入口 code object 注入占位常量。"""
    with open(wrapper_path, "rb") as fp:
        source = fp.read()

    code = compile(source, target_name + ".py", "exec")
    consts = list(code.co_consts)
    if MARKER not in consts:
        raise SystemExit("wrapper 里找不到占位常量 %s" % MARKER)

    # 官方入口 code object 能被本解释器 marshal 往返 = marshal 格式兼容，
    # 否则本脚本编出来的字节码版本与目标 exe 不一致，嵌进去会直接崩。
    try:
        marshal.loads(marshal.dumps(official_code))
    except Exception as exc:
        raise SystemExit(
            "marshal 格式不兼容：请用与目标 exe 相同的 Python 次版本号运行本脚本"
            "（当前 %s）— %s" % (sys.version.split()[0], exc))

    idx = consts.index(MARKER)
    consts[idx] = official_code
    return marshal.dumps(code.replace(co_consts=tuple(consts))), idx


def repack(archive: Archive, payload: bytes, out_path: str, target_name: str) -> int:
    buf = bytearray()
    new_entries = []
    replaced = 0

    for e in archive.entries:
        if e["type"] == TYPE_RUNTIME_OPTION:
            new_entries.append(dict(e))
            continue

        raw = archive.read_entry(e)
        if e["name"] == target_name:
            if e["type"] != "s":
                raise SystemExit("目标条目 %s 类型不是 's'，实际为 %r"
                                 % (target_name, e["type"]))
            raw = payload
            replaced += 1

        data = zlib.compress(raw, 9) if e["compress"] else raw
        new_entries.append({
            "name": e["name"],
            "type": e["type"],
            "offset": len(buf),
            "data_length": len(data),
            "uncompressed_length": len(raw),
            "compress": e["compress"],
        })
        buf.extend(data)

    if replaced != 1:
        raise SystemExit("应替换 1 条入口，实际替换 %d 条（目标名 %r 是否存在？）"
                         % (replaced, target_name))

    toc_new = serialize_toc(new_entries)
    toc_offset_new = len(buf)
    archive_length = toc_offset_new + len(toc_new) + COOKIE_LENGTH
    cookie = struct.pack(COOKIE_FORMAT, MAGIC, archive_length, toc_offset_new,
                         len(toc_new), archive.pyvers, archive.pylib.encode("ascii"))

    with open(out_path, "wb") as out:
        out.write(archive.prefix)
        out.write(bytes(buf))
        out.write(toc_new)
        out.write(cookie)

    return len(archive.prefix) + archive_length


def verify(patched_path: str, payload: bytes, target_name: str, entry_count: int) -> bool:
    a = Archive(patched_path)
    ok = True
    if len(a.entries) != entry_count:
        print("  ✗ 条目数变了：%d -> %d" % (entry_count, len(a.entries)))
        ok = False
    got = a.read_entry(a.find(target_name))
    if got != payload:
        print("  ✗ 入口回读与 payload 不一致")
        ok = False
    try:
        code = marshal.loads(got)
        nested = [c for c in code.co_consts if hasattr(c, "co_consts")]
        print("  入口 code: name=%r consts=%d 嵌套 code=%d"
              % (code.co_name, len(code.co_consts), len(nested)))
        if not nested:
            print("  ✗ 未找到注入的官方入口 code object")
            ok = False
    except Exception as exc:
        print("  ! 回读 marshal 跳过（%s）" % type(exc).__name__)
    return ok


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="给官方 reg-factory.exe 打入口级补丁（保留授权子系统）")
    parser.add_argument("--official-exe", required=True,
                        help="官方原版 exe（未打补丁）")
    parser.add_argument("--out", required=True, help="输出的补丁版 exe")
    parser.add_argument("--wrapper", default=os.path.join(here, "wrapper_entry.py"),
                        help="补丁入口源码，默认同目录 wrapper_entry.py")
    parser.add_argument("--target", default=DEFAULT_TARGET,
                        help="要替换的 CArchive 条目名，默认 %s" % DEFAULT_TARGET)
    args = parser.parse_args()

    print("解释器: %s" % sys.version.split()[0])
    archive = Archive(args.official_exe)
    print("官方 exe: %s" % args.official_exe)
    print("  bootloader 前缀: %d 字节" % len(archive.prefix))
    print("  pyvers=%d pylib=%r 条目=%d" % (archive.pyvers, archive.pylib, len(archive.entries)))

    entry = archive.find(args.target)
    if entry is None:
        raise SystemExit("找不到条目 %r" % args.target)
    print("  目标条目: %r type=%s compress=%d data=%d"
          % (entry["name"], entry["type"], entry["compress"], entry["data_length"]))

    official_code = marshal.loads(archive.read_entry(entry))
    print("  官方入口 code: name=%r consts=%d" % (official_code.co_name, len(official_code.co_consts)))

    payload, idx = build_payload(args.wrapper, official_code, args.target)
    print("补丁 payload: %d 字节（官方入口注入 co_consts[%d]）" % (len(payload), idx))

    size = repack(archive, payload, args.out, args.target)
    print("已写出: %s (%d 字节)" % (args.out, size))

    print("校验:")
    ok = verify(args.out, payload, args.target, len(archive.entries))
    print("结果:", "通过 ✅" if ok else "失败 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
