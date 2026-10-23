"""用「将实际启动 ComfyUI 的那个解释器」探测它能否写安装树。

start-dev 里规划（venv python，可能被沙箱降级）与执行（ComfyUI 解释器，工作区外）
是不同上下文；可写性必须由**执行者**判。与后端 install_tree_writable 同口径：
① 目录内新建/删除探针文件；② 以写方式打开已存在的 main.py（低完整性进程能新建
低标签文件，却打不开中等标签的既有文件——AlekPet 的 .install.lock 即后者）。

用法：<comfy-python> scripts/comfy_probe_write.py <ComfyUI 安装目录>
退出码：0=可写，1=不可写（stderr 给出原因）。
"""
import os
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：comfy_probe_write.py <ComfyUI 安装目录>", file=sys.stderr)
        return 1
    base = sys.argv[1]
    probe = os.path.join(base, f".laf-write-probe-{os.getpid()}")
    try:
        with open(probe, "wb") as fh:
            fh.write(b"")
        os.remove(probe)
    except OSError as exc:
        print(f"目录内不能新建/删除文件：{exc}", file=sys.stderr)
        return 1
    try:
        os.close(os.open(os.path.join(base, "main.py"), os.O_WRONLY))
    except OSError as exc:
        print(f"不能以写方式打开 main.py：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
