"""从 OpenAI、Anthropic 与 LangChain 消息块中提取标准图片地址。"""
import re
from typing import Any, Optional


def extract_image_url(block: Any) -> Optional[str]:
    if not isinstance(block, dict):
        return None
    block_type = block.get("type")
    if block_type == "image_url":
        value = block.get("image_url")
        return _clean_and_format(value.get("url") if isinstance(value, dict) else value)
    if block_type == "image":
        if block.get("source_type") == "base64":
            return _build_data_uri(block.get("data"), block.get("mime_type"))
        if block.get("source_type") == "url":
            return _clean_and_format(block.get("url"))
        source = block.get("source")
        if isinstance(source, dict):
            if source.get("type") == "base64":
                return _build_data_uri(source.get("data"), source.get("media_type"))
            if source.get("type") == "url":
                return _clean_and_format(source.get("url"))
    return None


def _build_data_uri(raw_b64: Optional[str], mime: Optional[str]) -> Optional[str]:
    clean = _clean_b64(raw_b64)
    if not clean:
        return None
    return f"data:{mime or _guess_mime(clean)};base64,{clean}"


def _clean_b64(value: Optional[str]) -> str:
    return re.sub(r"\s+", "", value) if value else ""


def _clean_and_format(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    return re.sub(r"\s+", "", url) if url.startswith("data:") else url


def _guess_mime(value: str) -> str:
    head = value[:10]
    if head.startswith("/9j/"):
        return "image/jpeg"
    if head.startswith("iVBOR"):
        return "image/png"
    if head.startswith("R0lGOD"):
        return "image/gif"
    if head.startswith("UklGR"):
        return "image/webp"
    return "image/png"
