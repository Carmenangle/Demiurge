"""角色卡格式单一属主：解析 TavernCard V1/V2/V3（JSON 或 PNG 内嵌），归一到内部模型。

对标 SillyTavern：
- V1 扁平；V2/V3 把字段包进 `data:{}`（顶层保留 V1 字段兼容）。
- PNG 卡把整段 JSON base64 存进 tEXt chunk，keyword=`chara`(V2)/`ccv3`(V3)，ccv3 优先。
- 卡可内嵌 `data.character_book`（世界书）与 `data.extensions.regex_scripts`（正则）。

本模块只做解析/归一/校验（纯逻辑，PNG 字节解析无网络 I/O），可直接单测。
落盘与仓库编排不在这里（见 routers/characters.py + repo_meta）。
"""
from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CardParseError(ValueError):
    """卡解析失败（非 PNG、无内嵌数据、JSON 非法、缺必填字段）。"""


@dataclass
class NormalizedCard:
    """归一后的内部角色卡模型。spec 记录来源版本，raw 保留原始 data 便于导出回写。"""
    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    creator_notes: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    creator: str = ""
    character_version: str = ""
    spec: str = "chara_card_v2"
    character_book: dict[str, Any] | None = None      # 内嵌世界书（原样保留，Phase 2 解析）
    regex_scripts: list[dict[str, Any]] = field(default_factory=list)  # 内嵌正则（Phase 3）
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "mes_example": self.mes_example,
            "creator_notes": self.creator_notes,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "alternate_greetings": self.alternate_greetings,
            "tags": self.tags,
            "creator": self.creator,
            "character_version": self.character_version,
            "spec": self.spec,
            "character_book": self.character_book,
            "regex_scripts": self.regex_scripts,
            "extensions": self.extensions,
        }

    @property
    def has_worldbook(self) -> bool:
        book = self.character_book
        return bool(book and book.get("entries"))

    @property
    def has_regex(self) -> bool:
        return bool(self.regex_scripts)


def _iter_png_text_chunks(image: bytes) -> list[tuple[str, str]]:
    """遍历 PNG，返回所有 tEXt chunk 的 (keyword, text)。text 是 chunk 里的原始字节按 latin-1 解出。

    PNG 布局：8 字节签名 + 若干 chunk [4B 长度][4B 类型][data][4B CRC]。
    tEXt data = keyword + \\x00 + text（latin-1）。忽略非 tEXt chunk。
    """
    if not image.startswith(PNG_SIGNATURE):
        raise CardParseError("不是有效的 PNG 图片")
    chunks: list[tuple[str, str]] = []
    pos = len(PNG_SIGNATURE)
    n = len(image)
    while pos + 8 <= n:
        (length,) = struct.unpack(">I", image[pos:pos + 4])
        ctype = image[pos + 4:pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        if data_end > n:
            break  # 截断/损坏，尽力而为
        if ctype == b"tEXt":
            raw = image[data_start:data_end]
            sep = raw.find(b"\x00")
            if sep != -1:
                keyword = raw[:sep].decode("latin-1", "replace")
                text = raw[sep + 1:].decode("latin-1", "replace")
                chunks.append((keyword, text))
        pos = data_end + 4  # 跳过 CRC
        if ctype == b"IEND":
            break
    return chunks


def read_png_card_json(image: bytes) -> str:
    """从 PNG 卡读出内嵌的角色卡 JSON 字符串。ccv3 优先，回退 chara。"""
    chunks = _iter_png_text_chunks(image)
    if not chunks:
        raise CardParseError("PNG 不含任何文本元数据")
    by_key = {kw.lower(): text for kw, text in chunks}
    encoded = by_key.get("ccv3") or by_key.get("chara")
    if not encoded:
        raise CardParseError("PNG 不含角色卡数据（缺 chara/ccv3 chunk）")
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CardParseError(f"角色卡 base64 解码失败：{exc}") from exc


def _s(value: Any) -> str:
    """归一为字符串：None→''，非字符串转 str。"""
    return "" if value is None else value if isinstance(value, str) else str(value)


def _slist(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_s(v) for v in value]


def normalize_card(obj: dict[str, Any]) -> NormalizedCard:
    """把 V1/V2/V3 的 JSON 对象归一到 NormalizedCard。

    V2/V3 字段在 `data` 下；V1 在顶层。优先读 `data`，回退顶层（兼容混合卡）。
    """
    if not isinstance(obj, dict):
        raise CardParseError("角色卡 JSON 根不是对象")
    spec = _s(obj.get("spec")) or "chara_card_v1"
    data = obj.get("data") if isinstance(obj.get("data"), dict) else obj

    def pick(key: str) -> Any:
        # data 优先，缺失回退顶层
        if key in data and data[key] not in (None, ""):
            return data[key]
        return obj.get(key)

    name = _s(pick("name")).strip()
    if not name:
        raise CardParseError("角色卡缺少 name")

    ext = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    book = pick("character_book")
    regex = ext.get("regex_scripts") if isinstance(ext.get("regex_scripts"), list) else []

    return NormalizedCard(
        name=name,
        description=_s(pick("description")),
        personality=_s(pick("personality")),
        scenario=_s(pick("scenario")),
        first_mes=_s(pick("first_mes")),
        mes_example=_s(pick("mes_example")),
        creator_notes=_s(pick("creator_notes")),
        system_prompt=_s(pick("system_prompt")),
        post_history_instructions=_s(pick("post_history_instructions")),
        alternate_greetings=_slist(pick("alternate_greetings")),
        tags=_slist(pick("tags")),
        creator=_s(pick("creator")),
        character_version=_s(pick("character_version")),
        spec=spec if spec.startswith("chara_card_v") else "chara_card_v2",
        character_book=book if isinstance(book, dict) else None,
        regex_scripts=[r for r in regex if isinstance(r, dict)],
        extensions=ext,
    )


def parse_card_json(text: str) -> NormalizedCard:
    """解析 JSON 文本形式的角色卡。"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CardParseError(f"角色卡 JSON 非法：{exc}") from exc
    return normalize_card(obj)


def parse_card_bytes(data: bytes, filename: str = "") -> NormalizedCard:
    """按内容/扩展名分派：PNG→读 tEXt，其余按 JSON（含 UTF-8 BOM）。"""
    if data.startswith(PNG_SIGNATURE) or filename.lower().endswith(".png"):
        return parse_card_json(read_png_card_json(data))
    text = data.decode("utf-8-sig", "replace")
    return parse_card_json(text)


def build_persona_system(card: dict[str, Any]) -> str:
    """把角色卡组装成剧情扮演的系统提示词片段（纯逻辑，可单测）。

    只取扮演相关字段（name/description/personality/scenario），空字段跳过。
    system_prompt（卡作者写的越权指令）单列在最前，最贴近 ST 的 {{charPrompt}} 语义。
    不含 first_mes（那是开场白，由对话侧作为首条消息，不进 system）。
    """
    parts: list[str] = []
    sp = (card.get("system_prompt") or "").strip()
    if sp:
        parts.append(sp)
    name = (card.get("name") or "").strip()
    if name:
        parts.append(f"你现在扮演「{name}」，始终以第一人称沉浸式出演，不得跳出角色。")
    for label, key in (("角色设定", "description"), ("性格", "personality"), ("场景", "scenario")):
        val = (card.get(key) or "").strip()
        if val:
            parts.append(f"【{label}】\n{val}")
    mes = (card.get("mes_example") or "").strip()
    if mes:
        parts.append(f"【对话范例】\n{mes}")
    return "\n\n".join(parts)


def opening_message(card: dict[str, Any]) -> str:
    """开场白：卡的 first_mes。空则返回空串（对话侧据此决定是否插首条消息）。"""
    return (card.get("first_mes") or "").strip()
