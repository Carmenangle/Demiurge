"""MOSS-Image 官方完整布局提示词（2026-11-07）。

GBC 场景图只给「图里有什么、在哪」；官方 MOSS layout 还要三段文字：画风（style，
40–100 词、以 `Palette: …` 收尾）、整体描述（summary.caption，120–250 词七步法）、
每个实体的逐项描述（30–80 词），以及角色卡（跨格/跨次一致的稳定外貌）。官方交付格式
没有独立的 style/cast 字段 ⇒ 按参考实现 Penna `mossLayoutJson` 的折法：style 与
`Characters: …` 角色卡行接在 summary.caption 里，角色卡再前置到对应 character 项。

分工（2026-11-07 定案）：**物体/服装/配饰只认 WD14 标签，构图/光线/场景以图为准，
几何由 GBC 给，文字由视觉模型写**。模型只按实体名回描述，后端用 GBC 的
label/bbox 组装（模型不得增删实体、不得改坐标）；匹配不上的实体退回 GBC 原描述。
写出来的文字再过一道输出闸（手持物/画面内文字/泳装款型），违规先纠正重试一次。
解析失败或纠正无效抛 MossLayoutFailed（路由 502），视觉模型未配置抛 MossLayoutUnavailable（503）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.services import gbc_tagger, llm as _llm, vlm_batch

# 姿态/朝向白名单（2026-11-07）：GBC 摘要由视觉模型写，姿势词会错（俯卧被写成
# sitting）；WD14 的 on_stomach/lying 是独立分类器结果，只把这组标签喂给布局模型。
# 词表属主在 gbc_tagger（同一份也用于过滤种子描述），这里只是复用。
POSE_TAGS = gbc_tagger.POSE_TAGS


def select_pose_tags(tags: object) -> list[str]:
    """任意标签序列 → 姿态/朝向子集（小写、下划线归一、保序去重）。"""
    out: list[str] = []
    if not isinstance(tags, (list, tuple)):
        return out
    for tag in tags:
        name = re.sub(r"[\s-]+", "_", str(tag).strip().lower())
        if name in POSE_TAGS and name not in out:
            out.append(name)
    return out


def pose_hint(tags: object) -> str:
    """姿态标签 → 可执行的姿态判定（明确到 caption 与 items 该写什么）。"""
    picked = set(select_pose_tags(tags))
    if picked & {"on_stomach", "prone"}:
        return "lying face-down / on her stomach（俯卧，胸腹贴地）"
    if picked & {"on_back", "supine"}:
        return "lying face-up / on her back（仰卧，背贴地）"
    if "on_side" in picked:
        return "lying on her side（侧卧）"
    if "lying" in picked:
        return "lying（卧姿，身体水平贴地）"
    if picked & {"sitting", "seiza", "wariza", "indian_style", "straddling"}:
        return "sitting（坐姿）"
    if picked & {"kneeling", "all_fours", "on_all_fours"}:
        return "kneeling or on all fours（跪姿/四肢着地）"
    if "standing" in picked:
        return "standing（站姿）"
    return ""


SYSTEM = (
    "你是 MOSS-Image 的官方布局提示词编译器。输入是一张已经画好的插画的 GBC 实体清单"
    "（实体名、类型、每项已有观察、关系句、整体描述）、WD14 标签以及这张图本身。"
    "几何（框与 label）由程序按实体名填，你只写文字。\n"
    "硬约束：\n"
    "①实体只能逐字使用输入给出的 name，不得改名、合并、拆分、增加或删除；"
    "items 必须覆盖全部实体，每个实体恰好一条。\n"
    "②所有值一律英文；只输出一个 JSON 对象，不要 Markdown 代码块、不要解释、不要前后缀。\n"
    "③物体、服装、配饰、手持物的唯一真源是「WD14 标签」：标签里没有的物体一律不得写；"
    "标签与 GBC 观察冲突时以标签为准。构图（位置/占比/遮挡/裁切/贴边）、朝向、光线、"
    "场景与颜色才以图片为准——WD14 不产这些。\n"
    '输出契约（键固定）：{"style":"...","caption":"...","lighting":"...",'
    '"cast":[{"name":"...","card":"..."}],"items":[{"name":"...","caption":"..."}]}\n'
    "各字段写法：\n"
    "style：40–100 词，只写画风与画法——媒介、线条、上色方式、材质与质感、整体色调；"
    "最后一句必须是 `Palette: …`，写主色与配色关系。不写 masterpiece／8k 等质量词、"
    "不写画师名、相机型号、标签串、权重括号。\n"
    "caption：120–250 词英文单段，按七步顺序展开：（1）是什么：开头点明这是一张动漫插画；"
    "（2）构图与视角：主体在画面哪一侧、占画面多大比例、景别、机位高低与俯仰；"
    "（3）主体：逐个点名，写各自动作、可见身体部位、服装与当前状态——服装与配饰只写"
    "标签里有的，标签写 white_one-piece_swimsuit 就不得写成 bikini，标签里没有的手持物、"
    "额外饰品、标题文字一律不写；"
    "（4）场景与背景：在哪里、背景里有什么——WD14 不产场景词，这里按图片写；"
    "（5）光线：光从哪来、质感、强弱、色温；（6）色调、画风与情绪；"
    "（7）画面内文字：不得凭想象编标题；只有图中确实能看清的字才写进双引号，且需要标签里"
    "出现 text / english_text / speech_bubble / signature 等文字类标签——例外是图上确实印着"
    "的画师署名与引流块（fanbox / patreon / pixiv / @handle 等），可原样转写，WD14 常漏打它们。"
    "MOSS 看不到坐标与图层 ⇒ 前后遮挡、裁切、贴边必须写成句子，不许只靠词序暗示；"
    "不用标签串、质量词、权重。\n"
    "lighting：英文一句，写主光源方向、光质、照亮对象与阴影落向；按图里实际能看到的"
    "光线写，不新增图中不存在的灯具。\n"
    "cast：只给类型为 character 的实体，每项一张角色卡；card 40–70 词英文，只写稳定的"
    "外貌（发色发型、瞳色、肤色、体型、标志性服饰与配饰），服饰配饰同样只认标签；"
    "不写姿势、朝向、位置或动作。没有 character 时给空数组。\n"
    "items：每个实体一条，caption 30–80 词英文：外观、姿态、朝向、在画面中的位置与占比、"
    "被遮挡或被边缘裁切的部分；与 caption 里同一对象用同一个叫法。姿态必须与 caption 一致"
    "（以图和姿态标签为准），不得照抄 GBC 观察里的姿势词。\n"
    "写之前先看图，但按上面的真源分工取信息：GBC 的实体观察可能把物体认错（例如把角认成"
    "铜管乐器、把一体式泳衣认成比基尼）；它只用于确认「这个实体在画面里的位置与遮挡」，"
    "不用来重新识别它是什么。\n"
    "姿态与朝向：先看图和「姿态标签」，再看 GBC 的已有观察。身体与地面的接触（脚、膝、"
    "髋、胸、肩、手哪些承重或贴地）决定姿势；GBC 的观察可能把俯卧错记成坐姿，不得原样"
    "照抄。俯卧（胸腹贴地、躯干水平）写 lying face-down / on her stomach，不得写成 sitting。"
    "给了姿态标签时，它们是独立分类器的可靠结果，必须优先采用。"
)


class MossLayoutUnavailable(RuntimeError):
    """视觉模型未配置（路由 503）。"""


class MossLayoutFailed(RuntimeError):
    """模型没给出可用 JSON（路由 502）。"""


def _sentence(text: str) -> str:
    s = text.strip()
    if not s or re.search(r"[.!?。!?]$", s):
        return s
    return s + "."


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _key(name: str) -> str:
    return name.strip().lower()


# 坐姿词与可靠姿态标签冲突时的兜底改写（只用于模型漏写的 character 项）。
_POSE_PROSE = {
    "lying face-down / on her stomach": ("lying face-down on her stomach",
                                         "lies face-down on her stomach"),
    "lying face-up / on her back": ("lying face-up on her back", "lies face-up on her back"),
    "lying on her side": ("lying on her side", "lies on her side"),
    "lying": ("lying", "lies"),
}
_SIT_ADJ = re.compile(r"\b(sitting|seated)\b", re.I)
_SIT_VERB = re.compile(r"\b(sits|sit)\b", re.I)


def _same_case(word: str, replacement: str) -> str:
    return replacement[:1].upper() + replacement[1:] if word[:1].isupper() else replacement


def align_character_pose(text: str, tags: object) -> str:
    """GBC 旧描述里的坐姿词与姿态标签冲突时按标签改写（仅 character 兜底文本）。"""
    pair = _POSE_PROSE.get(pose_hint(tags).split("（", 1)[0].strip())
    if pair is None:
        return text
    adj, verb = pair
    text = _SIT_ADJ.sub(lambda m: _same_case(m.group(0), adj), text)
    return _SIT_VERB.sub(lambda m: _same_case(m.group(0), verb), text)


def skeleton(graph: dict) -> list[dict]:
    """GBC 实体 → [{name,label,caption,bbox}]（官方交付 bbox；无名实体补 entity_N）。

    直接复用 `gbc_tagger.to_moss` 的坐标换算，避免第二份 bbox 数学。
    """
    moss = gbc_tagger.to_moss(graph, include_name=True)
    out: list[dict] = []
    for index, item in enumerate(moss.get("items") or []):
        name = _text(item.get("name")) or f"entity_{index + 1}"
        out.append({
            "name": name,
            "label": str(item.get("label") or "object"),
            "caption": _text(item.get("caption")),
            "bbox": item.get("bbox") or [],
        })
    return out


def digest(graph: dict, tags: object = None) -> str:
    """给模型看的实体清单（用户消息主体）。"""
    size = graph.get("img_size") or [0, 0]
    lines = [f"画布：{int(size[0] or 0)}×{int(size[1] or 0)}",
             f"GBC 整体描述（仅作构图参考）：{_text(graph.get('detail_caption')) or '（无）'}"]
    tag_list: list[str] = []
    if isinstance(tags, (list, tuple)):
        for tag in tags:
            name = re.sub(r"[\s-]+", "_", str(tag).strip().lower())
            if name and name not in tag_list:
                tag_list.append(name)
    if tag_list:
        # 全量标签是布局模型判断「图里有什么」的唯一词表（2026-11-07 用户定案）：
        # 只给姿态子集时，模型会跟着 GBC 的错误观察把角写成铜管乐器、凭空写剑和标题。
        lines.append("WD14 标签（独立分类器；物体/服装/配饰的唯一真源，按置信度排序）："
                     + ", ".join(tag_list))
    pose_tags = select_pose_tags(tags)
    if pose_tags:
        lines.append(f"姿态标签（WD14 分类器，可靠）：{', '.join(pose_tags)}")
        hint = pose_hint(pose_tags)
        if hint:
            lines.append("姿态判定（按标签，优先于 GBC 观察）：" + hint
                         + "；caption 与所有 items 都必须与之一致，不得照抄 GBC 的姿势词。")
    lines.append("实体（name / 类型 / 已有观察）：")
    for entry in skeleton(graph):
        lines.append(f"- {entry['name']} / {entry['label']} / {entry['caption'] or '（无）'}")
    relations = gbc_tagger.outline(graph).get("relations") or []
    if relations:
        lines.append("关系：")
        lines.extend(f"- {text}" for text in relations)
    lines.append("请按系统要求只输出 JSON。")
    return "\n".join(lines)


def parse_written(raw: str) -> dict:
    """模型输出 → {style,caption,lighting,cast,items}；去掉代码围栏再取首个 JSON 对象。"""
    text = _text(raw)
    if not text:
        raise MossLayoutFailed("模型没有返回内容（可能不支持视觉）")
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise MossLayoutFailed("模型没有输出 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise MossLayoutFailed(f"模型输出的 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise MossLayoutFailed("模型输出的不是 JSON 对象")
    style = _text(data.get("style"))
    caption = _text(data.get("caption"))
    if not style or not caption:
        raise MossLayoutFailed("模型输出缺 style 或 caption")
    cast_raw = data.get("cast")
    cast: list[dict] = []
    for entry in cast_raw if isinstance(cast_raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name, card = _text(entry.get("name")), _text(entry.get("card"))
        if name and card:
            cast.append({"name": name, "card": card})
    items_raw = data.get("items")
    items: list[dict] = []
    for entry in items_raw if isinstance(items_raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name, body = _text(entry.get("name")), _text(entry.get("caption"))
        if name and body:
            items.append({"name": name, "caption": body})
    if not items:
        raise MossLayoutFailed("模型没有给出任何逐项描述（items 为空）")
    return {"style": style, "caption": caption,
            "lighting": _text(data.get("lighting")), "cast": cast, "items": items}


# ─────────────────── 输出闸：标签没说的东西不许写（2026-11-07） ───────────────────
# 真机实锤：布局模型把 GBC 的错误观察又放大了一轮——剑出现 10 次、凭空写出
# "Sword of the Sea" 标题、把 white_one-piece_swimsuit 写成 bikini，生成器照单全收。
# 提示词之外再上一道确定性闸：违规先回灌纠正一次，仍违规则整轮判失败。

_HOLD_RE = re.compile(
    r"\b(?:holding|holds|hold|gripping|grips|grip|grasping|grasps|wielding|"
    r"wields|carrying|carries|clutching|clutches)\s+"
    r"(?:a|an|the|her|his|their|its|one|two)\s+"
    r"([a-z][a-z'\-]*(?:\s+[a-z][a-z'\-]*){0,3})",
    re.I,
)
_HOLD_STOP_WORDS = frozenset({
    "and", "with", "while", "as", "that", "which", "to", "at", "against",
    "over", "in", "on", "across", "near", "by", "from", "around",
})
#: 不是「手持物」的头部（身体/表情/抽象名词）——避免 "holding her breath" 误报
_HOLD_SKIP_HEADS = frozenset({
    "breath", "expression", "pose", "gaze", "look", "smile", "stance",
    "balance", "moment", "position", "posture", "attention", "shape", "form",
    "weight", "place", "ground", "hand", "hands", "arm", "arms", "finger",
    "fingers", "head", "shoulder", "shoulders",
    # 自然/抽象宾语：不是可被生成的物件，不拦（"the breeze carrying her hair"）
    "hair", "strands", "breeze", "wind", "light", "sound", "scent", "motion",
})
#: 文字类标签：只有它们在场，才允许写「画面内文字」
_TEXT_TAGS = frozenset({
    "text", "english text", "speech bubble", "thought bubble", "signature",
    "watermark", "artist name", "character name", "logo", "title",
})
_QUOTED_RE = re.compile(r'["“]([^"”\n]{2,80})["”]')
_QUOTED_TITLE_RE = re.compile(r"'([A-Z][^'\n]{2,60})'")
#: 署名/引流块：WD14 常漏打 signature/watermark 标签，但字确实印在图上 ⇒ 不算凭空
_CREDIT_MARKERS = (
    "patreon", "fanbox", "fansky", "fantia", "pixiv", "twitter", "instagram",
    "deviantart", "artstation", "gumroad", "skeb", "tumblr", "ko-fi",
    "http", "www.", "@", "©",
)
#: 两件式 vs 一体式：只在标签明说了另一款时才判冲突（slingshot 等未归族标签不算）
_TWO_PIECE_TERMS = ("bikini", "bikini top", "bikini bottom",
                    "two-piece swimsuit", "two piece swimsuit")
_ONE_PIECE_TERMS = ("one-piece swimsuit", "one piece swimsuit", "school swimsuit")


def _tag_phrase(tag: object) -> str:
    text = re.sub(r"\s*\(.*?\)", "", str(tag).replace("_", " ")).strip().lower()
    return re.sub(r"\s+", " ", text)


def _has_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and bool(re.search(r"\b" + re.escape(phrase) + r"\b", text, re.I))


def _tag_backed(phrase: str, phrases: list[str]) -> bool:
    """手持物是否被某个标签覆盖（标签含它、或它是标签短语的一部分）。"""
    return any(_has_phrase(tag, phrase) or _has_phrase(phrase, tag) for tag in phrases)


def _held_objects(text: str) -> list[str]:
    out: list[str] = []
    for match in _HOLD_RE.finditer(text):
        words: list[str] = []
        for word in match.group(1).split():
            if word.lower() in _HOLD_STOP_WORDS:
                break
            words.append(word)
        phrase = " ".join(words).strip().lower()
        if not phrase or phrase.split()[-1] in _HOLD_SKIP_HEADS:
            continue
        if phrase not in out:
            out.append(phrase)
    return out


def _is_credit_text(quote: str) -> bool:
    lowered = quote.lower()
    return any(marker in lowered for marker in _CREDIT_MARKERS)


def _quoted_text(text: str) -> str:
    """第一段不属于署名/引流块的引号内文字（空 = 没有可判的画面内文字）。"""
    for pattern in (_QUOTED_RE, _QUOTED_TITLE_RE):
        for match in pattern.finditer(text):
            quote = match.group(1).strip()
            if quote and not _is_credit_text(quote):
                return quote
    return ""


def _swimwear_conflict(text: str, tag_text: str) -> str:
    claims_two = any(_has_phrase(text, term) for term in _TWO_PIECE_TERMS)
    claims_one = any(_has_phrase(text, term) for term in _ONE_PIECE_TERMS)
    has_two = any(_has_phrase(tag_text, term) for term in _TWO_PIECE_TERMS)
    has_one = any(_has_phrase(tag_text, term) for term in _ONE_PIECE_TERMS)
    if claims_two and has_one:
        return "bikini（两件式）"
    if claims_one and has_two:
        return "one-piece swimsuit（一体式）"
    return ""


def find_violations(written: dict, tags: object = None) -> list[str]:
    """输出文字里「标签没说的物体」→ 可读的违规清单（空 = 通过）。

    只查三类会直接改变画面的断言：手持物、画面内文字、泳装款型冲突。
    构图/光线/场景词不查——那些本来就以图为准。
    """
    phrases = [_tag_phrase(tag) for tag in tags] if isinstance(tags, (list, tuple)) else []
    phrases = [text for text in phrases if text]
    if not phrases:
        # 老资产没存 WD14 标签 ⇒ 没有权威词表，闸无从判定（保持原行为）
        return []
    tag_text = " ".join(phrases)
    has_text_tag = any(_has_phrase(tag_text, name) for name in _TEXT_TAGS)
    problems: list[str] = []

    def check(label: str, value: object) -> None:
        body = _text(value)
        if not body:
            return
        for held in _held_objects(body):
            if not _tag_backed(held, phrases):
                problems.append(f"{label} 写了手持物 “{held}”，但 WD14 标签里没有它")
        quote = _quoted_text(body)
        if quote and not has_text_tag:
            problems.append(f"{label} 写了画面内文字 “{quote}”，但 WD14 标签里没有文字类标签")
        swim = _swimwear_conflict(body, tag_text)
        if swim:
            authority = "、".join(
                tag for tag in phrases
                if any(_has_phrase(tag, term) for term in _TWO_PIECE_TERMS + _ONE_PIECE_TERMS)
            )
            problems.append(f"{label} 写了 {swim}，与 WD14 标签冲突（标签里是：{authority}）")

    check("caption", written.get("caption"))
    for item in written.get("items") or []:
        if isinstance(item, dict):
            check(f"items[{item.get('name') or '?'}]", item.get("caption"))
    for entry in written.get("cast") or []:
        if isinstance(entry, dict):
            check(f"cast[{entry.get('name') or '?'}]", entry.get("card"))
    return problems


def compose(graph: dict, written: dict, tags: object = None) -> dict:
    """模型文字 + GBC 几何 → 官方交付 layout JSON；同时回一份可读 parts。

    折法同 Penna：summary.caption = style + caption + `Characters: …` + `Lighting: …`；
    character 项的 caption 前置角色卡（已包含则不重复）。
    """
    entries = skeleton(graph)
    queue: dict[str, list[str]] = {}
    for item in written.get("items") or []:
        queue.setdefault(_key(item["name"]), []).append(item["caption"])
    cards = {_key(entry["name"]): entry["card"] for entry in written.get("cast") or []}

    out_items: list[dict] = []
    parts_items: list[dict] = []
    cast_line: list[str] = []
    for entry in entries:
        key = _key(entry["name"])
        pending = queue.get(key) or []
        if pending:
            body = pending.pop(0)
        else:
            body = entry["caption"]
            if entry["label"] == "character":
                body = align_character_pose(body, tags)
        card = cards.get(key) if entry["label"] == "character" else None
        final = body
        if card:
            if card.rstrip(".。") not in cast_line:
                cast_line.append(card.rstrip(".。"))
            if card not in body:
                final = " ".join(s for s in [_sentence(card), body] if s)
        out_items.append({"label": entry["label"], "caption": final, "bbox": entry["bbox"]})
        parts_items.append({"name": entry["name"], "caption": final})

    style = _text(written.get("style"))
    caption = _text(written.get("caption"))
    lighting = _text(written.get("lighting"))
    head: list[str] = []
    if style and style not in caption:
        head.append(_sentence(style))
    head.append(caption)
    characters = f"Characters: {'; '.join(cast_line)}." if cast_line else ""
    if characters and characters not in caption:
        head.append(characters)
    full = " ".join(s for s in head if s)
    if lighting:
        full = f"{full} Lighting: {_sentence(lighting)}"

    layout = {
        "summary": {"canvas": "single", "caption": full},
        "items": out_items,
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
    }
    parts_cast = [{"name": entry["name"], "card": cards[_key(entry["name"])]}
                  for entry in entries
                  if entry["label"] == "character" and _key(entry["name"]) in cards]
    return {"layout": layout,
            "parts": {"style": style, "caption": caption, "lighting": lighting,
                      "cast": parts_cast, "items": parts_items}}


def write(graph: dict, image_path: Path | str, *, base_url: str, api_key: str,
          model: str, proxy: str = "", hint: str = "",
          tags: object = None) -> dict:
    """看图 + GBC 清单 → parse_written 的结构（模型/解析失败抛 MossLayoutFailed）。

    输出会过 `find_violations`；命中手持物/画面文字/泳装款型冲突时把违规清单回灌
    纠正一次（仍违规 ⇒ 判失败，不把标签没说的物体交给生成器）。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    data_uri = vlm_batch.file_to_datauri(image_path)
    if not data_uri:
        raise ValueError("读不到图片字节（仅支持本机留存图片）")
    user = digest(graph, tags)
    if _text(hint):
        user += f"\n补充要求：{_text(hint)}"
    content: list = [{"type": "text", "text": user},
                     {"type": "image_url", "image_url": {"url": data_uri}}]
    try:
        # 云端推理模型会先烧思考 tokens（GBC 侧实测 512 不够用），输出预算放到 4096。
        chat = _llm.build_model(base_url, api_key, model, 0.4, False,
                                proxy=proxy, max_tokens=4096)
        resp = chat.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=content)])
    except Exception as exc:  # noqa: BLE001 - 网络/鉴权/多模态不支持统一收成 502
        raise MossLayoutFailed(f"官方布局生成失败（模型需支持视觉）：{exc}") from exc
    text = _llm.flatten_content(resp.content)
    try:
        written = parse_written(text)
    except MossLayoutFailed as exc:
        raise MossLayoutFailed(f"{exc}；模型原文开头：{text[:300]}") from exc

    issues = find_violations(written, tags)
    if not issues:
        return written

    correction = (
        "上一次输出与权威标签冲突。请按标签改正后重新输出**完整** JSON（键与格式不变）：\n"
        + "\n".join(f"- {issue}" for issue in issues[:6])
        + "\n改法：删掉标签里没有的物体/手持物/画面文字，或改用标签里的叫法；"
          "构图、朝向、光线仍按图片写。\n\n上一次输出：\n" + text
    )
    try:
        resp = chat.invoke([SystemMessage(content=SYSTEM), HumanMessage(
            content=[*content, {"type": "text", "text": correction}])])
    except Exception as exc:  # noqa: BLE001 - 同上
        raise MossLayoutFailed(f"官方布局纠正重试失败：{exc}") from exc
    retry_text = _llm.flatten_content(resp.content)
    try:
        retried = parse_written(retry_text)
    except MossLayoutFailed as exc:
        raise MossLayoutFailed(
            f"纠正重试后仍不可用：{exc}；模型原文开头：{retry_text[:300]}") from exc
    remaining = find_violations(retried, tags)
    if remaining:
        raise MossLayoutFailed(
            "官方布局与 WD14 标签冲突（纠正重试后仍存在）：" + "；".join(remaining[:6]))
    return retried
