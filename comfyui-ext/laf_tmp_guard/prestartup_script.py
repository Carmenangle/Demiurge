"""ComfyUI prestartup guard: break tempfile's unbounded PermissionError retry
(2026-10-21 真机实锤，Demiurge launcher path).

背景：本工具拉起的 ComfyUI 子进程可能运行在**低强制性完整性级别**（用户环境沙箱化，
token 带 `Mandatory Label\\Low Mandatory Level`，经 Popen 继承）。Windows MIC 的
NO_WRITE_DOWN 规则下，低完整性进程无法写入无标签（=中等）的目录——包括 ComfyUI
安装树与用户 profile。任何想在启动期往这些位置写缓存的库都会拿到 PermissionError：

- numba（comfy_extras/mesh3d/uv_unwrap/pack.py 的 @njit(cache=True)）——
  已由启动器设 NUMBA_CACHE_DIR 重定向到可写数据目录解决；
- yapf（语法 pickle 缓存写 %LOCALAPPDATA%\\Google\\YAPF\\Cache\\<ver>）等。

而 CPython 的 `tempfile._mkstemp_inner` 把 PermissionError 当作**可重试**错误
（目录看起来可写就 continue），本机 `os.TMP_MAX = 2**31-1` ⇒ 重试循环实质无界，
进程 ~1 核空转数天，ComfyUI 永远卡在自定义节点 import 阶段（8188 无监听）。

本守卫用「PermissionError 不重试、首次即抛出」的等价实现替换 `_mkstemp_inner`：
调用方库各自已有降级路径（numba 跳过缓存、yapf 抛错、所在节点被记
"Cannot import" 后继续启动），不再把整个服务器挂死。对正常（有写权限的）进程，
行为与原实现一致——随机名碰撞（FileExistsError）仍会重试。
"""
import os
import sys

try:
    import tempfile
except Exception:  # pragma: no cover - tempfile 不可用则无从守起
    tempfile = None

if tempfile is not None and hasattr(tempfile, "_get_candidate_names"):
    _orig = tempfile._mkstemp_inner

    def _guarded_mkstemp_inner(dir, pre, suf, flags, output_type):
        """_mkstemp_inner 的等价实现，但 PermissionError 不进入重试循环。

        与原版的唯一差异：PermissionError（本机即低完整性进程被 MIC 拒绝写入）
        首次失败即向上抛，不在 `for seq in range(TMP_MAX)` 里空转。
        FileExistsError（真名碰撞，概率 ~37^-8）保持原语义重试。
        """
        dir = os.path.abspath(dir)
        names = tempfile._get_candidate_names()
        if output_type is bytes:
            names = map(os.fsencode, names)
        for _ in range(8):
            name = next(names)
            file = os.path.join(dir, pre + name + suf)
            sys.audit("tempfile.mkstemp", file)
            try:
                fd = os.open(file, flags, 0o600)
            except FileExistsError:
                continue
            return fd, file
        raise FileExistsError(
            17, "No usable temporary file name found")

    tempfile._mkstemp_inner = _guarded_mkstemp_inner
