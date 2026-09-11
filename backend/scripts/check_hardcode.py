"""硬编码门禁：服务地址走 config，本机绝对路径不得进入源码。

把 docs 准入规则「地址走 config 常量」变成可执行检查。跑：
    cd backend && .venv/Scripts/python scripts/check_hardcode.py
命中 config.py 之外的服务地址或任意本机盘符路径时退出码 1。
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
_MACHINE_PATH = re.compile(r'''(?:r|u|b|f|br|rb|fr|rf)?["'][A-Za-z]:\\''', re.I)

_APP_DIR = Path(__file__).resolve().parent.parent / "app"


def scan(app_dir: Path = _APP_DIR) -> list[str]:
    hits: list[str] = []
    for path in app_dir.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            address_hit = path.name not in _ALLOWED and any(p.search(line) for p in _PATTERNS)
            if address_hit or _MACHINE_PATH.search(line):
                hits.append(f"{path.relative_to(app_dir.parent)}:{lineno}: {line.strip()}")
    return hits


def main() -> int:
    hits = scan()
    if hits:
        print("服务地址应走 config 常量，且源码不得包含本机盘符路径：")
        for h in hits:
            print("  " + h)
        return 1
    print("硬编码门禁通过：服务地址集中配置且无本机盘符路径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
