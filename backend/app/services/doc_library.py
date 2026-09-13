"""只读文档库：把仓库 docs/ 下的 Markdown 以纯文本交给前端。

用途：新人引导的「独立文档页」——引导步骤里的 `doc:` 链接点开后，在应用内直接读
仓库里的教学文档（`docs/guide/*.md` 等），不另起一份文档真源。

边界与红线
- 只读：本模块不写任何文件，也不列目录（目录清单另有其主，别在这里加）。
- 白名单：路径必须相对仓库根，且解析后必须落在 `<仓库根>/docs` 之内；只接受 `.md`。
  拒绝绝对路径、盘符、`..` 穿越、以及解析后越界的符号链接。
- 服务深：不含 FastAPI 依赖；路由只做参数校验与错误码映射。
"""
from __future__ import annotations

import re
from pathlib import Path

DOC_ROOT_NAME = "docs"
DOC_SUFFIX = ".md"
# 单篇上限：教学文档量级远小于此，超了说明路径指错或误塞了大文件。
MAX_DOC_BYTES = 512 * 1024

_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:")


def repo_root() -> Path:
    """仓库根：backend/app/services/doc_library.py → parents[3]。"""
    return Path(__file__).resolve().parents[3]


def doc_root(root: Path | None = None) -> Path:
    return (root or repo_root()).resolve() / DOC_ROOT_NAME


def resolve_doc_path(rel_path: str, root: Path | None = None) -> Path:
    """把「相对仓库根的 .md 路径」解析成绝对路径，越界一律抛 ValueError。

    允许：`docs/guide/workflow-template-import.md`。
    拒绝：绝对路径 / 盘符 / 带 `..` / 非 .md / 解析后不在 docs/ 内。
    """
    base = (root or repo_root()).resolve()
    raw = (rel_path or "").strip()
    if not raw:
        raise ValueError("文档路径为空")
    # Windows 反斜杠统一成正斜杠，避免 `docs\guide\x.md` 被当成单段文件名。
    raw = raw.replace("\\", "/")
    if raw.startswith("/") or _ABSOLUTE_RE.match(raw):
        raise ValueError("文档路径必须是相对仓库根的路径")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("文档路径不合法")
    if not raw.lower().endswith(DOC_SUFFIX):
        raise ValueError("只支持 .md 文档")
    target = (base / Path(*parts)).resolve()
    # resolve() 会展开符号链接：链接指向 docs/ 之外时这里拦下。
    if not target.is_relative_to(doc_root(base)):
        raise ValueError("只允许读取 docs/ 目录下的文档")
    return target


def read_doc(rel_path: str, root: Path | None = None) -> dict[str, object]:
    """读一篇文档，返回 {path, title, content}。任何不合法/读取失败都抛 ValueError。"""
    path = resolve_doc_path(rel_path, root)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as e:
        raise ValueError("文档不存在") from e
    except IsADirectoryError as e:
        raise ValueError("该路径是目录，不是文档") from e
    except OSError as e:
        raise ValueError(f"文档读取失败：{e}") from e
    if len(raw) > MAX_DOC_BYTES:
        raise ValueError("文档过大，已拒绝读取")
    content = raw.decode("utf-8", errors="replace")
    base = (root or repo_root()).resolve()
    rel = "/".join(path.relative_to(base).parts)
    return {
        "path": rel,
        "title": doc_title(content, path.stem),
        "content": content,
    }


def doc_title(content: str, fallback: str) -> str:
    """首个 `# 标题` 作标题；没有则回退文件名（去扩展名）。"""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if title:
                return title
    return fallback
