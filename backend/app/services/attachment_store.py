"""对话附件存储（2026-09-01 定案 B1：会话级）。

单一属主：附件字节的落盘、会话级生命周期、文本提取、下载寻址都在这里。
前端只拿 file_id 元信息（{file_id, name, mime, size}），不持有原始字节；
agent 链路经 `file_reference_block` 把附件转「文件参考」段落进上下文。

布局：<DATA_DIR>/attachments/<thread_id>/<file_id>-<name>
- file_id = uuid4().hex（32 位十六进制，路径寻址真源）
- 删除会话/快照清理时调用 `delete_thread` 连带删（B1 合同）
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from xml.sax.saxutils import unescape

from app.config import DATA_DIR

ATTACH_ROOT = DATA_DIR / "attachments"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 单文件 50MB 上限（会话级附件，非生图资产）
TEXT_MAX_CHARS = 100_000              # agent 消化封顶：文本类全文进上下文截断线

# 文本类判定：与旧前端白名单对齐 + 常见可读文档。二进制/office/音视频/压缩包一律不进全文。
_TEXT_EXT_RE = re.compile(
    r"\.(md|markdown|txt|text|json|jsonl|csv|tsv|log|ya?ml|yml|xml|html?|"
    r"ts|tsx|js|jsx|mjs|cjs|py|pyw|css|scss|less|ini|cfg|conf|toml|"
    r"srt|vtt|ass|rst|tex|sh|bat|cmd|ps1|sql|diff|patch|env|properties|"
    r"java|go|rs|c|cpp|h|hpp|cs|kt|swift|php|rb|pl|lua|r|dart|scala|groovy)$",
    re.I,
)
_TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml", "application/x-yaml",
                       "application/javascript", "application/x-sh", "application/x-shellscript")
_DOCX_XLSX_EXTS = (".docx", ".xlsx")
_PDF_EXTS = (".pdf",)


def is_text_file(name: str, mime: str = "") -> bool:
    """是否可全文进上下文（文本/代码/docx/xlsx/pdf；pdf 仅尝试提取，失败由调用方降级）。"""
    if mime and mime.lower().startswith(_TEXT_MIME_PREFIXES):
        return True
    low = (name or "").lower()
    if low.endswith(_DOCX_XLSX_EXTS) or low.endswith(_PDF_EXTS):
        return True
    return bool(_TEXT_EXT_RE.search(low))


def _safe_segment(value: str, *, limit: int = 80) -> str:
    """把 thread_id/文件名折成安全目录段：只留 [A-Za-z0-9._-]，截断防超长。"""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value or "").strip("._")
    return cleaned[:limit] or "unnamed"


def _thread_dir(thread_id: str) -> Path:
    return ATTACH_ROOT / _safe_segment(thread_id)


def save_upload(thread_id: str, filename: str, mime: str, data: bytes) -> dict:
    """落盘附件，返回 {file_id, name, mime, size}（前端与 agent 唯一持有关键）。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件过大（{len(data) / 1024 / 1024:.1f}MB），上限 50MB")
    file_id = uuid.uuid4().hex
    safe_name = _safe_segment(filename, limit=120)
    folder = _thread_dir(thread_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{file_id}-{safe_name}"
    dest.write_bytes(data)
    return {
        "file_id": file_id,
        "name": filename or safe_name,
        "mime": mime or "application/octet-stream",
        "size": len(data),
    }


_FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def resolve(file_id: str) -> Path | None:
    """按 file_id 找落盘文件；防路径穿越（只接受 32 位 hex）。"""
    if not file_id or not _FILE_ID_RE.match(file_id):
        return None
    if not ATTACH_ROOT.is_dir():
        return None
    try:
        for folder in ATTACH_ROOT.iterdir():
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if path.name.startswith(file_id + "-") and path.is_file():
                    return path
    except OSError:
        return None
    return None


def delete_thread(thread_id: str) -> int:
    """删除某会话全部附件（B1 连带）。返回删除的文件数。"""
    folder = _thread_dir(thread_id)
    if not folder.is_dir():
        return 0
    count = 0
    try:
        for path in folder.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                count += 1
        folder.rmdir()
    except OSError:
        pass
    return count


def read_bytes(file_id: str) -> bytes | None:
    path = resolve(file_id)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _extract_docx_xlsx(path: Path) -> str | None:
    """零依赖提取 docx/xlsx 纯文本：两者都是 zip，取 sharedStrings/document.xml 的文本。

    docx 文本节点带命名空间前缀（<w:t>），xlsx sharedStrings 是无前缀 <t>；
    统一用 `(?:\w+:)?t` 匹配两种写法。
    """
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            if path.name.lower().endswith(".docx"):
                xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            else:
                xml = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    texts = re.findall(r"<(?:\w+:)?t[^>]*>(.*?)</(?:\w+:)?t>", xml, re.S)
    joined = "\n".join(unescape(t) for t in texts if t.strip())
    return joined or None


def _extract_pdf(path: Path) -> str | None:
    """可选提取 PDF 文本层：pypdf 缺失/解析失败返回 None（调用方降级为元信息）。"""
    try:
        from pypdf import PdfReader  # 可选依赖，非硬性
    except Exception:  # noqa: BLE001 - ImportError 及加载失败都降级
        return None
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text.strip())
            except Exception:  # noqa: BLE001 - 单页失败跳过
                continue
        return "\n\n".join(pages) or None
    except Exception:  # noqa: BLE001 - 解析失败降级
        return None


def extract_text(file_id: str, name: str, *, max_chars: int = TEXT_MAX_CHARS) -> str | None:
    """附件 → 可进上下文的文本（截断封顶）。二进制/office 提取失败返回 None。"""
    path = resolve(file_id)
    if path is None:
        return None
    low = (name or path.name).lower()
    if low.endswith(_DOCX_XLSX_EXTS):
        text = _extract_docx_xlsx(path)
    elif low.endswith(_PDF_EXTS):
        text = _extract_pdf(path)
    elif not is_text_file(low):
        return None  # 二进制（音视频/zip/模型/office 提取失败等）不进全文
    else:
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            return None
    if not text:
        return None
    text = text.strip()
    return text[:max_chars] if len(text) > max_chars else text


def file_reference_block(meta: dict, *, max_chars: int = TEXT_MAX_CHARS) -> str:
    """把单条附件元信息转「文件参考」段落：文本类全文（截断注明），二进制只元信息。

    - 文本/代码/docx/xlsx/pdf → 【文件参考：name】（共 N 字）\n<text>
    - 纯二进制（音视频/zip/模型等）→ 【文件附件：name】MIME=… 大小=…（不进全文）
    与 agent_graph 预读本地路径的 buildFileAttachmentText 语义对齐，只是数据源换成附件。
    """
    name = str(meta.get("name") or "附件")
    mime = str(meta.get("mime") or "")
    size = int(meta.get("size") or 0)
    file_id = str(meta.get("file_id") or "")
    if file_id and is_text_file(name, mime):
        text = extract_text(file_id, name, max_chars=max_chars)
        if text is not None:
            truncated = len(text) >= max_chars
            note = f"（共 {size} 字节，已截断至前 {max_chars} 字）" if truncated else f"（共 {size} 字节）"
            return f"【文件参考：{name}】{note}\n{text}\n【文件参考结束：{name}】"
    return (
        f"【文件附件：{name}】MIME={mime or '未知'}，大小={size} 字节。"
        "该类型无法直接阅读全文；如模型需要其内容，请提示用户粘贴关键片段或另存为文本。"
    )


def file_reference_blocks(metas: list[dict] | None, *, max_chars: int = TEXT_MAX_CHARS) -> list[str]:
    """批量转「文件参考」段落（空附件返回空列表）。"""
    if not metas:
        return []
    return [file_reference_block(m, max_chars=max_chars) for m in metas]
