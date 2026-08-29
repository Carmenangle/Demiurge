"""从能力注册表导出 Autopilot manifest（对标 scripts/ln.py 模式）。

用法：
    python scripts/generate_capability_manifest.py          # 重新生成 manifest JSON
    python scripts/generate_capability_manifest.py --check  # 校验 manifest 与注册表一致

manifest 生成物（backend/app/generated/capability_manifest.json）随源码发布，
必须提交；终端 Runtime 不运行生成器。--check 进门禁，防清单与注册表漂移。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import capability_registry  # noqa: E402

MANIFEST_OUT = ROOT / "backend" / "app" / "generated" / "capability_manifest.json"


def render() -> str:
    data = capability_registry.build_manifest()
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def check() -> int:
    errors = capability_registry.validate_handlers()
    if errors:
        for error in errors:
            print(f"handler 校验失败：{error}", file=sys.stderr)
        return 1
    if not MANIFEST_OUT.is_file():
        print(f"manifest 不存在：{MANIFEST_OUT}，请先运行本脚本重新生成", file=sys.stderr)
        return 1
    current = MANIFEST_OUT.read_text(encoding="utf-8")
    expected = render()
    if current != expected:
        print("manifest 与注册表不一致，请运行本脚本重新生成并提交", file=sys.stderr)
        return 1
    print(f"manifest OK：{len(capability_registry.all_capabilities())} 条能力与注册表一致")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="校验 manifest 与注册表一致")
    args = parser.parse_args()
    if args.check:
        return check()
    errors = capability_registry.validate_handlers()
    if errors:
        for error in errors:
            print(f"handler 校验失败：{error}", file=sys.stderr)
        return 1
    MANIFEST_OUT.write_text(render(), encoding="utf-8")
    print(f"已生成 {MANIFEST_OUT}（{len(capability_registry.all_capabilities())} 条能力）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
