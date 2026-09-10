# 入口级二进制补丁（官方便携包 · 保留授权子系统）

## 现象

官方 Windows 便携包里，WebUI 点「运行任务」后：

1. 又弹出一个 reg-factory 窗口；
2. 真正的注册任务没有启动，BitBrowser 也不打开；
3. 偶尔看到端口冲突（8799 被占，回退 8800）。

## 根因

`webui/aar_bridge.py` 与 `webui/server.py` 的 `_build_cmd` 在冻结（frozen）
模式下拼的是：

```text
[<exe>, "-u", "--task", "<任务脚本.py>", ...]
```

也就是**反向唤起 exe 自己**来跑任务。但官方 2.2.4 的入口脚本
`reg_factory_desktop` 里没有任何 `--task` 分支——入口 pyc 里搜 `--task` 命中 0。
于是 bootloader 把参数当普通启动参数丢掉，照常执行主模块：

- 拉起第二个 uvicorn + webview（现象 1、3）
- 任务脚本从未被执行（现象 2）

同一个 bug 在上游 2.2.4 的源码里也存在：`scripts/reg-factory-server.py`
有任务派发协议，但 `packaging/reg-factory.spec` 把入口换成了
`reg_factory_desktop.py`，没把这段逻辑带过去。

## 为什么不能「从源码重新打包」

官方发布包里的**授权（激活）子系统是闭源的**——模块 `ysq_auth` 与
`yunshouquan_sdk`（云授权 SDK），配套接口 `/api/auth-info`、`/api/auth/machine-code`、
`/api/auth/diag`、`/api/auth/activate`、`/api/auth/logout`，支持账号密码或卡密激活、
心跳续期、到期状态。公开仓库里 grep `api/auth`、`auto_free_auth` 全部 0 命中。

按公开源码重建 exe，授权徽章与激活入口会整体消失。因此只能在**官方原包**上做
最小改动。

## 方案

PyInstaller onedir 的 exe 由一个 bootloader 前缀 + 一个 CArchive 组成。
CArchive 里 `reg_factory_desktop` 这一条（`PYSOURCE`，类型 `s`）就是入口脚本
的 code object（zlib + marshal）。

本工具链**只替换这一条**：

- 入口换成 `wrapper_entry.py` 编译出的补丁入口；
- 其余条目（6 个运行时钩子、`pyimod01..04`、装着授权模块的整个 `PYZ`）逐字节搬运；
- bootloader 前缀原样保留；
- TOC 与 cookie 按 PyInstaller 的二进制格式重新序列化（名称补齐到 16 字节边界）。

补丁入口的行为：

```text
命中 -u --task X.py（或直接给 X.py）
    -> 在本进程内 runpy.run_path 执行 X.py，不再拉起第二个 GUI
其它情况
    -> 把官方入口的 code object 原样 exec 进 __main__
       （授权校验、内嵌后端、WebUI + webview 全部走官方原路径，无任何改动）
```

也就是说：**只在「跑任务」这条路上改道，授权链路一个字节都没动。**

## 用法

```bash
python patch_exe.py --official-exe <官方原版 reg-factory.exe> --out <输出的 reg-factory.exe>
```

放到官方便携包同一目录（与 `_internal/` 同级）即可运行。

**必须用与目标 exe 相同的 Python 次版本号运行本脚本。** 官方 2.2.4 是
Python 3.12（`pyvers=312`、`pylib=python312.dll`）。版本不符时脚本会直接报错
退出，不会产出坏包。

脚本只依赖标准库（自带极简 CArchive 读写）。

## 验证

```bash
# 1. 任务派发：应打印探针内容且不弹 GUI 窗口
printf 'import os, sys\nprint("PROBE-DISPATCH-OK", os.getpid(), sys.argv, flush=True)\n' \
  > _internal/probe_task.py
./reg-factory.exe -u --task probe_task.py

# 2. GUI + 授权：应返回 authorized=true 且能看到账号/机器码
curl http://127.0.0.1:8799/api/auth-info
curl http://127.0.0.1:8799/api/auth/machine-code
```

## 回滚

补丁前先备份官方 `reg-factory.exe`。回滚只需把备份文件覆盖回去，
`_internal/` 完全不受影响（补丁不触碰它）。

## 发布记录

- `v2.2.6`（`reg-factory-windows-x64-2.2.6.zip`）：补丁基线为官方 2.2.4 便携包，
  包内 `VERSION` / `_internal/VERSION` 已设为 `2.2.6`。官方源包备份可继续用于回滚。
