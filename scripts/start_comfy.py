"""在「当前进程的上下文」里拉起 ComfyUI（供 start-dev 以用户身份调用）。

为什么不走后端 POST /api/comfyui/start：后端可能运行在受限上下文（低完整性/沙箱），
它 Popen 出的 ComfyUI 继承同一完整性级别，写不了 ComfyUI 安装树——AlekPet 节点
`FileLock(.install.lock, timeout=600)` 会把冷启动拖到 600.1 秒后才失败、端口全程不监听。
本脚本由用户身份启动，读同一份 data/comfy_config.json，复用 comfy_launcher 的唯一
spawn 规格，但不把进程句柄交给后端（launch_detached）。

用法：
  <backend-venv>python scripts/start_comfy.py                # 当前上下文直接拉起
  <backend-venv>python scripts/start_comfy.py --print-plan   # 只输出 spawn 规格 JSON
      （供 start-dev 用 ComfyUI 自带解释器在工作区外实际 spawn——本机沙箱会把
        工作区内映像降为 Low，经 venv python 拉起的 ComfyUI 写不了安装树）
退出码：0=成功，1=启动失败，2=未配置 ComfyUI 路径。
"""
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import comfy_launcher  # noqa: E402  (依赖上面的 sys.path)


def main() -> int:
    cfg = comfy_launcher.load_config()
    path = (cfg.get("path") or "").strip()
    if not path:
        print("未配置 ComfyUI 路径（设置 → 路径），跳过 ComfyUI 启动。")
        return 2
    python_path = cfg.get("python_path", "")
    if "--print-plan" in sys.argv[1:]:
        try:
            plan = comfy_launcher.build_plan(path, python_path)
        except comfy_launcher.LaunchError as exc:
            print(f"生成启动规格失败：{exc.detail}", file=sys.stderr)
            return 1
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    try:
        result = comfy_launcher.launch_detached(path, cfg.get("url", ""), python_path)
    except comfy_launcher.LaunchError as exc:
        print(f"拉起 ComfyUI 失败：{exc.detail}", file=sys.stderr)
        return 1
    print(result.get("message") or "已发出启动指令")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
