"""构建可单独上传的 Demiurge 预设与正则资源包。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

STRIP_FIELDS = {
    "api_key",
    "apiKey",
    "reverse_proxy",
    "proxy_password",
    "proxy_preset",
    "custom_url",
    "custom_include_headers",
    "custom_include_body",
    "custom_exclude_body",
    "api_url_scale",
    "authorization",
    "access_token",
}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def sanitize(value: Any) -> Any:
    """递归剥离连接、鉴权字段，保留预设和正则语义。"""
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if key not in STRIP_FIELDS}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_clean(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise ValueError(f"资源包仍含疑似密钥: {path}")


def build(root: Path, output: Path) -> list[Path]:
    preset_source = root / "presets" / "GrayWill-0.46-demiurge.json"
    global_regex_source = root / "backend" / "data" / "regex_scripts.json"
    if not preset_source.is_file():
        raise FileNotFoundError(preset_source)
    if not global_regex_source.is_file():
        raise FileNotFoundError(global_regex_source)

    preset = sanitize(_read_json(preset_source))
    extensions = preset.get("extensions") if isinstance(preset, dict) else None
    embedded = extensions.get("regex_scripts") if isinstance(extensions, dict) else None
    global_regex = sanitize(_read_json(global_regex_source))
    if not isinstance(embedded, list) or not all(isinstance(item, dict) for item in embedded):
        raise ValueError("预设缺少 extensions.regex_scripts")
    if not isinstance(global_regex, list) or not all(
        isinstance(item, dict) for item in global_regex
    ):
        raise ValueError("全局正则文件格式无效")

    preset_target = output / "preset" / "GrayWill-0.46-demiurge.json"
    embedded_target = output / "regex" / "GrayWill-0.46-embedded-regex.json"
    global_target = output / "regex" / "Demiurge-global-regex.json"
    _write_json(preset_target, preset)
    _write_json(embedded_target, embedded)
    _write_json(global_target, global_regex)

    readme = output / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        """# Demiurge 预设与正则资源包

## 内容

- `preset/GrayWill-0.46-demiurge.json`：Demiurge 当前适配预设，保留 33 条内嵌 ST 正则。
- `regex/GrayWill-0.46-embedded-regex.json`：上述 33 条正则的独立导出，供只导入正则时使用。
- `regex/Demiurge-global-regex.json`：Demiurge 当前使用的 24 条全局正则。

## 使用

在 Demiurge 中导入预设后，再按需要导入全局正则。预设内嵌正则导出主要用于 SillyTavern 或手工恢复；不要把内嵌正则和同名全局正则重复启用，否则文本可能被处理两次。

本包不包含角色卡专属正则、会话、世界书、图片、API 配置或原始 `GrayWill-0.46-ex` 参考文件。所有连接与鉴权字段已递归移除。

公开分发前仍需确认 GrayWill 预设及其正则的上游授权；清洗密钥不等于取得再分发许可。
""",
        encoding="utf-8",
    )

    files = [preset_target, embedded_target, global_target, readme]
    for path in files:
        _assert_clean(path)
    manifest = output / "manifest.json"
    _write_json(
        manifest,
        {
            "package": "Demiurge-presets-regex",
            "preset": "GrayWill-0.46-demiurge",
            "embedded_regex_count": len(embedded),
            "global_regex_count": len(global_regex),
            "files": [
                {
                    "path": path.relative_to(output).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in files
            ],
        },
    )
    _assert_clean(manifest)
    return [*files, manifest]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output = root / "presets" / "Demiurge-presets-regex"
    files = build(root, output)
    total = sum(path.stat().st_size for path in files)
    print(f"资源包已生成: {output}")
    print(f"文件: {len(files)}，大小: {total / 1024:.1f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
