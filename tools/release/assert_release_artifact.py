# -*- coding: utf-8 -*-
"""发布物断言：断言「用户下载到的包」确实等于仓库源码。

用法：
    python tools/release/assert_release_artifact.py <包目录 或 zip> \\
        --repo <仓库目录> [--expect-version X] [--expect-exe-md5 <md5>]

为什么需要它：
    621 个单测全部跑在源码树上，没有一条检查 zip 里的东西。
    2.2.7 就是这么翻车的 —— 仓库绿、发布包旧。
本脚本把 `pkg_sync_map` 的同步表变成可执行断言，可直接进 CI 或发布前手动跑。
"""
import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pkg_sync_map  # noqa: E402

TEXT_EXT = {
    ".py", ".ps1", ".sh", ".bat", ".cmd", ".md", ".txt", ".json", ".js", ".css",
    ".html", ".yml", ".yaml", ".example", ".cfg", ".ini", ".toml",
}
TEXT_NAMES = {"VERSION", ".env.example"}

fails = []
checks = 0


def check(cond, label, detail=""):
    global checks
    checks += 1
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s%s" % (label, ("  -> " + detail) if detail else ""))
        fails.append(label)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(root, rel):
    p = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--expect-version", default=None)
    ap.add_argument("--expect-exe-md5", default=None)
    args = ap.parse_args()

    tmp = None
    pkg = args.pkg
    if os.path.isfile(pkg) and pkg.lower().endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="assert_pkg_")
        with zipfile.ZipFile(pkg) as zf:
            zf.extractall(tmp)
        entries = os.listdir(tmp)
        pkg = os.path.join(tmp, entries[0]) if len(entries) == 1 else tmp
        print("解压到临时目录: %s" % pkg)

    if not os.path.isfile(os.path.join(pkg, "VERSION")):
        print("[FATAL] 目录里没有 VERSION，包根目录不对？")
        sys.exit(2)

    print("\n[1] 同步表：包内文件 == 仓库源码 (%d 项)" % len(pkg_sync_map.sync_map()))
    for arc, repo_rel in sorted(pkg_sync_map.sync_map().items()):
        got = read(pkg, arc)
        if got is None:
            check(False, "存在性 %s" % arc, "包内缺失")
            continue
        want = read(args.repo, repo_rel)
        if want is None:
            check(False, "源存在 %s <- %s" % (arc, repo_rel), "仓库缺源文件")
            continue
        if repo_rel == "VERSION":
            ok = got.strip() == want.strip()
        else:
            ok = got == want
        check(ok, "同步 %s" % arc, "与 %s 不一致" % repo_rel)

    print("\n[2] 用户可见修复的内容护栏")
    for arc, needle in pkg_sync_map.CONTENT_GUARDS:
        got = read(pkg, arc)
        check(got is not None and needle in got,
              "%s 含 %r" % (arc, needle.decode("utf-8", "replace")))

    print("\n[3] 版本与校验")
    if args.expect_version:
        v = read(pkg, "VERSION")
        iv = read(pkg, "_internal/VERSION")
        check(v is not None and v.strip().decode() == args.expect_version,
              "VERSION == %s" % args.expect_version)
        check(iv is not None and iv.strip().decode() == args.expect_version,
              "_internal/VERSION == %s" % args.expect_version)
    if args.expect_exe_md5:
        exe = os.path.join(pkg, "reg-factory.exe")
        check(os.path.isfile(exe) and md5(exe) == args.expect_exe_md5,
              "exe md5 == %s" % args.expect_exe_md5)

    print("\n[4] 卫生：不该进包的东西")
    bad = []
    for dirpath, dirnames, filenames in os.walk(pkg):
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), pkg).replace("\\", "/")
            if f == ".env":
                bad.append(("真实 .env", rel))
            elif f.startswith("auto_free_auth"):
                bad.append(("授权缓存", rel))
            elif f.lower().endswith(".log") and rel.count("/") <= 1:
                bad.append(("顶层日志", rel))
    check(not bad, "无 .env / 授权缓存 / 顶层日志", str(bad[:5]))

    print("\n[5] 脚本里不得残留上游标识")
    hits = []
    for dirpath, dirnames, filenames in os.walk(pkg):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if os.path.splitext(f)[1].lower() not in pkg_sync_map.SCRIPT_EXT:
                continue
            full = os.path.join(dirpath, f)
            try:
                if os.path.getsize(full) > (8 << 20):
                    continue
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            for needle in pkg_sync_map.FORBIDDEN_SUBSTR:
                if needle in data:
                    hits.append((os.path.relpath(full, pkg).replace("\\", "/"),
                                 needle.decode()))
    check(not hits, "脚本类文件无 tiantianGPU 残留", str(hits[:5]))

    for arc, needle in pkg_sync_map.NEGATIVE_GUARDS:
        got = read(pkg, arc)
        check(got is not None and needle not in got,
              "%s 不含 %r" % (arc, needle.decode()))

    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print("检查项 %d，失败 %d" % (checks, len(fails)))
    if fails:
        for f in fails:
            print("  -", f)
        print("结论：FAIL")
        sys.exit(1)
    print("结论：PASS —— 发布物与仓库源码一致")


if __name__ == "__main__":
    main()
