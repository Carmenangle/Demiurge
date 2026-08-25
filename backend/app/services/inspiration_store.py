"""灵感卡资产库（M1.4）：把会话内灵感卡升级为资产库可管理成员。

存储位置：<output_dir>/_web_materials/inspiration/<card_id>.json
- 每卡一个 JSON 文件（id/title/content/sources/images/created_at）
- 图片是 full_url 经受控下载落盘到 _web_materials/（M1.3 安全链），
  card.images 记录 {url(本地), source_url(来源页), title}
- 删卡只删 JSON（磁盘图片保留，可作独立素材）；删图只改 JSON（图文件保留）

设计对齐 ROADMAP M1.4：
- 资产库是唯一持久真源，会话快照只是展示缓存
- 入库是显式动作（前端保存/发送时调用），不自动刷库
- 遵守 generation RAG 合同：删除资产保留本地文件
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.services.comfyui_client import ComfyError
from app.services import image_store

_CARD_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_LOCK = threading.Lock()


def inspiration_dir(output_dir: str) -> Path:
    """灵感卡资产目录：<output_dir>/_web_materials/inspiration/。"""
    return Path(output_dir) / "_web_materials" / "inspiration"


def _card_path(output_dir: str, card_id: str) -> Path:
    return inspiration_dir(output_dir) / f"{card_id}.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_card(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _list_cards(output_dir: str) -> list[dict]:
    d = inspiration_dir(output_dir)
    if not d.is_dir():
        return []
    cards: list[dict] = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.name, reverse=True):
        card = _read_card(f)
        if card:
            cards.append(card)
    return cards


def _save_card(output_dir: str, card: dict) -> dict:
    d = inspiration_dir(output_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = _card_path(output_dir, str(card["id"]))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(card, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)
    return card


def save_inspiration_card(
    output_dir: str,
    *,
    card_id: str = "",
    title: str = "",
    content: str = "",
    sources: list | None = None,
    images: list | None = None,
    thread_id: str = "",
) -> dict:
    """把一张灵感卡登记为资产。images: [{full_url, source_url, title?}]。

    - 图片 full_url 走 M1.3 受控下载（候选校验/魔数/大小/域名），落盘 _web_materials/
    - thread_id 用于候选校验豁免（重启后候选列表丢失时，快照内灵感卡图片仍可保存）
    - 幂等：card_id 重复时覆盖（同卡多次保存更新图片集）
    """
    if not output_dir:
        raise ComfyError("未配置输出路径", 400)
    if not title.strip() and not content.strip():
        raise ComfyError("灵感卡标题与内容不能同时为空", 400)
    cid = (card_id or "").strip()
    if cid and not _CARD_ID_RE.match(cid):
        raise ComfyError("灵感卡 id 格式非法", 400)
    if not cid:
        cid = f"insp-{uuid4().hex[:12]}"

    card_images: list[dict] = []
    for img in images or []:
        if not isinstance(img, dict):
            continue
        full_url = str(img.get("full_url") or img.get("url") or "").strip()
        if not full_url:
            continue
        # 已是本地留存的 URL（重复保存时跳过重复下载）
        if full_url.startswith("data:"):
            continue
        # 本地 local-view 引用：直接记录
        if "/local-view?" in full_url or full_url.startswith("/api/comfyui/local-view"):
            card_images.append({
                "url": full_url,
                "source_url": str(img.get("source_url") or ""),
                "title": str(img.get("title") or ""),
            })
            continue
        # 远程 URL：受控下载
        try:
            saved = image_store.save_web_material(
                output_dir, full_url,
                source_url=str(img.get("source_url") or ""),
                title=str(img.get("title") or ""),
                thread_id=thread_id,
            )
        except ComfyError as e:
            raise ComfyError(f"下载灵感图失败：{e.detail}", e.status)
        card_images.append({
            "url": saved["url"],
            "source_url": str(img.get("source_url") or ""),
            "title": str(img.get("title") or ""),
        })

    card = {
        "id": cid,
        "title": title.strip(),
        "content": content.strip(),
        "sources": [dict(s) for s in (sources or []) if isinstance(s, dict)],
        "images": card_images,
        "created_at": _now(),
    }
    with _LOCK:
        _save_card(output_dir, card)
    return card


def list_inspiration_cards(output_dir: str) -> list[dict]:
    """列出资产库灵感卡（新→旧），含封面信息（首图 url / 文本预览）。"""
    cards = []
    for card in _list_cards(output_dir):
        images = card.get("images") or []
        cards.append({
            "id": card.get("id", ""),
            "title": card.get("title", ""),
            "content": card.get("content", ""),
            "sources": card.get("sources") or [],
            "images": images,
            "cover_url": images[0]["url"] if images else "",
            "created_at": card.get("created_at", ""),
        })
    return cards


def get_inspiration_card(output_dir: str, card_id: str) -> dict:
    """读取单张灵感卡详情（发送对话框/画布用）。"""
    if not _CARD_ID_RE.match(card_id or ""):
        raise ComfyError("灵感卡 id 格式非法", 400)
    card = _read_card(_card_path(output_dir, card_id))
    if not card:
        raise ComfyError("灵感卡不存在", 404)
    return card


def update_inspiration_card(
    output_dir: str,
    *,
    card_id: str,
    title: str | None = None,
    content: str | None = None,
    remove_image_urls: list | None = None,
) -> dict:
    """编辑灵感卡：改文本 / 删图只留文本。图片文件保留在素材库。"""
    if not _CARD_ID_RE.match(card_id or ""):
        raise ComfyError("灵感卡 id 格式非法", 400)
    with _LOCK:
        path = _card_path(output_dir, card_id)
        card = _read_card(path)
        if not card:
            raise ComfyError("灵感卡不存在", 404)
        if title is not None:
            card["title"] = title.strip()
        if content is not None:
            card["content"] = content.strip()
        if remove_image_urls:
            drop = set(str(u).strip() for u in remove_image_urls if u)
            card["images"] = [
                img for img in (card.get("images") or [])
                if str(img.get("url") or "").strip() not in drop
            ]
        _save_card(output_dir, card)
    return card


def delete_inspiration_card(output_dir: str, card_id: str) -> bool:
    """删除灵感卡资产（只删 JSON，图片文件保留可作独立素材）。"""
    if not _CARD_ID_RE.match(card_id or ""):
        return False
    with _LOCK:
        path = _card_path(output_dir, card_id)
        if not path.is_file():
            return False
        path.unlink(missing_ok=True)
    return True
