"""Demiurge 源码上传前检查。只读，不暂存、不提交、不推送。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 50 * 1024 * 1024
FORBIDDEN_PREFIXES = ("backend/data/", "userdata/", "docs/memory/")
ALLOWED_PRESET_PREFIX = "presets/Demiurge-presets-regex/"
FORBIDDEN_SUFFIXES = (
    ".safetensors",
    ".ckpt",
    ".pt",
    ".bin",
    ".gguf",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
)
REQUIRED_PATHS = (
    ".gitignore",
    "AGENTS.md",
    "ARCHITECTURE.md",
    "README.md",
    "backend/requirements.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
)
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
TEXT_SUFFIXES = {
    "",
    ".bat",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True
    )
    return result.stdout


def candidate_paths(root: Path) -> list[Path]:
    """返回 Git 已追踪和未忽略的未追踪文件，即可能被上传的工作树内容。"""
    raw = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths: list[Path] = []
    for item in raw.decode("utf-8", errors="strict").split("\0"):
        if not item:
            continue
        path = root / item
        if path.is_file():
            paths.append(path)
    return sorted(set(paths))


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def missing_css_assets(css_file: Path, public_dir: Path) -> list[str]:
    if not css_file.is_file():
        return []
    refs = re.findall(r"url\(\s*['\"]?(/[^)'\"]+)", css_file.read_text(encoding="utf-8"))
    missing = {
        ref.lstrip("/")
        for ref in refs
        if not ref.startswith("//") and not (public_dir / ref.lstrip("/")).is_file()
    }
    return sorted(missing)


def ignored_release_paths(root: Path, paths: set[Path]) -> list[str]:
    ignored: list[str] = []
    for path in sorted(paths):
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)], cwd=root, check=False
        )
        if result.returncode == 0:
            ignored.append(relative(root, path))
    return ignored


def _contains_secret(path: Path) -> bool:
    if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 5 * 1024 * 1024:
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return any(pattern.search(content) for pattern in SECRET_PATTERNS)


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    paths = candidate_paths(root)
    rel_paths = {relative(root, path) for path in paths}

    for required in REQUIRED_PATHS:
        if required not in rel_paths:
            errors.append(f"缺少上传必需文件: {required}")

    for path in paths:
        rel = relative(root, path)
        lowered = rel.lower()
        if lowered.startswith(FORBIDDEN_PREFIXES):
            errors.append(f"禁止上传的本机数据: {rel}")
        if lowered.startswith("presets/") and not lowered.startswith(
            ALLOWED_PRESET_PREFIX.lower()
        ):
            errors.append(f"禁止上传的未清洗预设: {rel}")
        if lowered.endswith(FORBIDDEN_SUFFIXES) or Path(lowered).name in {
            ".env",
            "user_state.json",
        }:
            errors.append(f"禁止上传的运行文件: {rel}")
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"单文件超过 50 MiB: {rel}")
        if _contains_secret(path):
            errors.append(f"疑似真实密钥或私钥: {rel}")

    css = root / "frontend" / "src" / "styles.css"
    public = root / "frontend" / "public"
    for missing in missing_css_assets(css, public):
        errors.append(f"CSS 引用资产缺失: frontend/public/{missing}")

    public_assets = {path for path in public.rglob("*") if path.is_file()}
    for ignored in ignored_release_paths(root, public_assets):
        errors.append(f"运行所需前端资产被忽略: {ignored}")
    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate(root)
    paths = candidate_paths(root)
    total = sum(path.stat().st_size for path in paths)
    if errors:
        print("发布前检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"发布前检查通过：{len(paths)} 个候选文件，{total / 1024 / 1024:.1f} MiB。")
    print("此结果不代表已暂存；提交前仍需逐项检查 git status。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
