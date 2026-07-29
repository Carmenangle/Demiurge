"""硬编码门禁：ComfyUI/后端地址字面量只允许出现在 config.py。

把 docs 准入规则「地址走 config 常量」变成可执行检查。跑：
    cd backend && .venv/Scripts/python scripts/check_hardcode.py
命中(config.py 之外出现字面地址)退出码 1，供 CI/pre-commit 拦截。
零依赖，纯标准库，Windows 与 Linux CI 通用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 只允许出现在这里的“单一真源”
_ALLOWED = {"config.py"}
# 要盯住的地址字面量（后端自身/ComfyUI），按需扩充
_PATTERNS = [re.compile(r"127\.0\.0\.1:8188"), re.compile(r"127\.0\.0\.1:8010")]

_APP_DIR = Path(__file__).resolve().parent.parent / "app"


def scan() -> list[str]:
    hits: list[str] = []
    for path in _APP_DIR.rglob("*.py"):
        if path.name in _ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(line) for p in _PATTERNS):
                hits.append(f"{path.relative_to(_APP_DIR.parent)}:{lineno}: {line.strip()}")
    return hits


def main() -> int:
    hits = scan()
    if hits:
        print("硬编码地址应走 config 常量（COMFYUI_BASE_URL/BACKEND_BASE_URL）：")
        for h in hits:
            print("  " + h)
        return 1
    print("硬编码门禁通过：地址字面量仅在 config.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
