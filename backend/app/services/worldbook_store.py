"""独立世界书落盘：每本世界书 = worldbookDir 下的一个 <安全名>.json（ST 世界书格式）。

区别于 character_store 的卡内嵌世界书（那随卡落在卡文件夹）：这里是用户单独导入的、
可跨卡复用的独立世界书，落在设置里的「世界书文件夹」(worldbookDir)。

- 名称取自上传文件名（ST 世界书 JSON 内部无 name 字段）。
- 同名冲突：save 时目标已存在且 overwrite=False → 抛 FileExistsError（调用方决定覆盖）。
- 卡内嵌世界书导入若与已存独立世界书同名，也复用同一冲突语义（见 routers）。
- 同步：把**卡目录**的 worldbook.json 覆盖进独立库（`sync_card_worldbook`，旧版自动备份）——
  事务（读设置 → 校验 → mkdir → 备份 → 覆盖）的唯一属主，路由只做 HTTP 翻译（2026-09-15 P8）。

只做文件读写；条目解析/检索是 worldbook.py 的事，格式校验在导入路由。
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import re
import shutil
import time

from app.services import repo_meta
from app.services.pathnames import safe_dir

_LOG = logging.getLogger("uvicorn.error")

REPO_WORLDBOOK_FILE = "worldbook.json"


@dataclass
class WorldbookSummary:
    name: str
    file: str
    entries: int


def _path(base: str, name: str) -> Path:
    # safe_dir 保留中文可读性（世界书名常为中文），只挡 Windows 非法字符，与 character_store 一致
    return Path(base) / f"{safe_dir(name)}.json"


def exists(base: str, name: str) -> bool:
    return _path(base, name).is_file()


def _entry_count(book: dict[str, Any]) -> int:
    raw = book.get("entries")
    if isinstance(raw, dict):
        return len(raw)
    if isinstance(raw, list):
        return len(raw)
    return 0


def save(base: str, name: str, book: dict[str, Any], *, overwrite: bool = False) -> WorldbookSummary:
    """把世界书 JSON 写入 <base>/<安全名>.json。同名且 overwrite=False → FileExistsError。"""
    if not base:
        raise ValueError("未设置世界书文件夹路径")
    p = _path(base, name)
    if p.is_file() and not overwrite:
        raise FileExistsError(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return WorldbookSummary(name=name, file=p.name, entries=_entry_count(book))


def list_books(base: str) -> list[WorldbookSummary]:
    root = Path(base)
    if not root.is_dir():
        return []
    out: list[WorldbookSummary] = []
    for child in sorted(root.glob("*.json")):
        try:
            book = json.loads(child.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(book, dict):
            continue
        out.append(WorldbookSummary(name=child.stem, file=child.name, entries=_entry_count(book)))
    return out


def read_book(base: str, name: str) -> dict[str, Any] | None:
    p = _path(base, name)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def delete_book(base: str, name: str) -> bool:
    p = _path(base, name)
    if not p.is_file():
        return False
    p.unlink()
    return not p.is_file()


class WorldbookSyncError(Exception):
    """同步到独立世界书失败的**结果语义**（status + detail 供路由转 HTTPException）。

    与 `collection_artifacts.ArtifactAccessError` 同口径：错误码与中文文案由 service 定、
    路由只做 HTTP 翻译 —— 否则「事务」与「文案」又会裂成两个属主（2026-09-15 P8）。
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class WorldbookSyncResult:
    """同步结果：落点、条目数、本次**真的写出**的备份（无旧版 → None）。"""

    dest: Path
    entries: int
    backup: Path | None = None


def sync_card_worldbook(card_dir: Path) -> WorldbookSyncResult:
    """把**卡目录**的 `worldbook.json` 覆盖同步进独立世界书库（`worldbookDir/<卡名>.json`）。

    2026-09-15 审计 P8：这段事务原先整个摆在 `routers/artifacts.py:294-338` —— 手写
    `json.loads(DATA_DIR / "user_state.json")` 读 `worldbookDir`（**绕过属主**
    `repo_meta.setting_dir_from_state`）、`mkdir`、`copy2` 备份、`copy2` 覆盖、条目计数、
    错误映射，还夹带 `json`/`shutil`/`time`/`DATA_DIR` 四处函数内局部导入。收进本函数后
    事务只有一个属主，路由只剩「域解析 → 调用 → 转 HTTP」。

    **逐字保留**（用户可见，不得抹平）：备份名 `<卡名>.json.bak-<int(time.time())>`、
    400「未配置世界书文件夹（worldbookDir），无法同步」、400「无法从产物路径解析卡目录名」、
    404「卡目录缺少 worldbook.json：<绝对路径>」、500「同步失败：<原因>」
    （含「worldbook.json 缺少 entries」这条业务校验）。

    校验**顺序**同样是契约：配置 → 卡名 → 源文件存在 → 事务 ⇒ 前三级失败**绝不落盘**。
    ⚠ 目标文件名用**卡目录名原文**、不经 `safe_dir` 清洗（与 `save()` 的 `<安全名>.json`
    刻意不同口径）：存量文件名按原文存在，改清洗会让它们对不上。
    """
    wb_dir = repo_meta.setting_dir_from_state("worldbookDir")
    if not wb_dir:
        raise WorldbookSyncError(400, "未配置世界书文件夹（worldbookDir），无法同步")
    card_name = str(card_dir.name or "").strip()
    if not card_name or card_name in (".", ".."):
        raise WorldbookSyncError(400, "无法从产物路径解析卡目录名")
    src = card_dir / REPO_WORLDBOOK_FILE
    if not src.is_file():
        raise WorldbookSyncError(404, f"卡目录缺少 worldbook.json：{src}")
    try:
        book = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(book.get("entries"), (list, dict)):
            raise ValueError("worldbook.json 缺少 entries")
        dst = Path(wb_dir) / f"{card_name}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        if dst.is_file():
            backup = dst.with_name(f"{card_name}.json.bak-{int(time.time())}")
            shutil.copy2(dst, backup)
        shutil.copy2(str(src), str(dst))
        return WorldbookSyncResult(dest=dst, entries=len(book.get("entries") or []), backup=backup)
    except Exception as exc:  # noqa: BLE001 - 统一转「同步失败：<原因>」
        raise WorldbookSyncError(500, f"同步失败：{exc}") from exc


def mutate_entries(
    load: Callable[[], dict[str, Any]],
    save: Callable[[dict[str, Any]], Any],
    apply: Callable[[dict[str, Any]], tuple[Any, bool]],
) -> tuple[Any, bool]:
    """条目级 CRUD 的**唯一事务接缝**：读 → 改一条 → **有变化才写回**。

    2026-09-15 审计 P7：同一形状此前有两套写法——`routers/worldbook.py` 用
    `_read_book`/`_write_book` helper，`routers/narrative.py` 三个 repo 快照端点**内联**
    手写「读 → `worldbook_edit` 改 → 写回」。落盘判定因此有两个属主。收进本接缝后，
    「无变化不落盘」（越界更新/删除因此天然不写盘）只有这一个属主。

    **只收这一条共同不变量**；定位与失败语义留在调用方——独立世界书 / 卡内嵌 /
    仓库快照三种定位的 400·404 文案本就各不相同（见两个 routers），刻意不统一。

    - ``load()`` → book。失败自行 raise，接缝只透传。
    - ``apply(book)`` → ``(result, changed)``。**显式布尔是刻意的**：新增条目返回的
      index 可以是 ``0``，用真值判定会把「首条新增」误判成没变化而不落盘。
    - ``changed`` 为真才 ``save(book)``；``(result, changed)`` 回给调用方复用
      （add 的 index、update/delete 的越界 404）。
    """
    book = load()
    result, changed = apply(book)
    if changed:
        save(book)
    return result, changed


def repo_snapshot_path(base: str, repo_id: str) -> Path:
    """当前小仓库的世界书快照，与 state/tables 一样按 repo_id 物理隔离
    （2026-09-18 ③ 起与 chat/图/文档同域：<base>/<仓库名>/worldbook.json）。"""
    return repo_meta.engine_repo_dir(base, repo_id) / REPO_WORLDBOOK_FILE


def read_repo_snapshot(base: str, repo_id: str) -> dict[str, Any] | None:
    if not (base and repo_id):
        return None
    p = repo_snapshot_path(base, repo_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_repo_snapshot(base: str, repo_id: str, book: dict[str, Any]) -> bool:
    """把 worldbook dict 写回小仓库快照文件。快照不存在 → False。"""
    if not (base and repo_id):
        return False
    p = repo_snapshot_path(base, repo_id)
    if not p.parent.exists():
        return False
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _raw_entries(book: dict[str, Any]) -> list[dict[str, Any]]:
    raw = book.get("entries")
    values = raw.values() if isinstance(raw, dict) else raw
    return [deepcopy(item) for item in (values or []) if isinstance(item, dict)]


def ensure_repo_snapshot(base: str, repo_id: str,
                         source_books: list[dict[str, Any]]) -> dict[str, Any] | None:
    """首次使用时合并来源并快照；已有快照永不被源库覆盖。"""
    existing = read_repo_snapshot(base, repo_id)
    if existing is not None:
        return existing
    entries: list[dict[str, Any]] = []
    for book in source_books:
        if isinstance(book, dict):
            entries.extend(_raw_entries(book))
    if not source_books or not (base and repo_id):
        return None
    snapshot = {"entries": entries}
    p = repo_snapshot_path(base, repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return deepcopy(snapshot)


def _entry_matches(item: dict[str, Any], query: str) -> bool:
    if not query:
        return False
    terms = [str(item.get("comment") or "").strip()]
    keys = item.get("keys") or item.get("key") or []
    if isinstance(keys, str):
        keys = [keys]
    terms.extend(str(term).strip() for term in keys if str(term).strip())
    return any(term and term in query for term in terms)


# ── 角色外貌提取（2026-10-23 重构）──────────────────────────────────────────────
# 旧实现只认「comment 含『角色卡·』＋ 行首【外貌/身材/穿着】」：
#   · 导入的世界书若没有这套结构（【外观】、`外貌：`、纯散文）⇒ 一个字都提不出来；
#   · 只取整行 ⇒ 简介里的「暗黄色的眼睛」这类外貌事实被丢掉；
#   · 单条超过 max_chars ⇒ 直接 break，连裁剪都没有。
# 新口径＝**确定性三层抽取，零新增 LLM 调用**（省价格）：
#   ① 结构化标题 / 行标签（外貌、外观、身材、穿着、Appearance…）整段保留；
#   ② 其余正文里**带外貌线索的整句**（发色/瞳色/身高/服装/饰品…）；
#   ③ 两者都抽不到、但确属角色条目 ⇒ 整条正文兜底（限预算裁剪）。
# 提示词编译器（下游 LLM）本来就要从这段文字里写英文外貌，无需再单独调一次模型。
_VISUAL_CUE_CN_RE = re.compile(
    "|".join((
        "外貌", "外观", "容貌", "长相", "面容", "样貌", "外表", "形象", "气质", "神态",
        "身高", "体型", "体态", "身形", "体格", "身材",
        "肌肤", "皮肤", "肤色", "发色", "发型", "头发", "长发", "短发", "卷发", "直发",
        "秀发", "银发", "金发", "黑发", "红发", "白发", "棕发", "褐发", "青发", "蓝发",
        "粉发", "紫发", "马尾", "辫子", "刘海",
        "眼眸", "眼睛", "双眼", "瞳孔", "瞳色", "双眸", "眼瞳", "眸子", "眉眼", "眉毛",
        "睫毛", "鼻子", "鼻梁", "嘴唇", "唇色", "朱唇", "脸颊", "脸型", "五官",
        "胸部", "乳房", "巨乳", "丰乳", "乳沟", "乳贴", "溢乳",
        "腰", "臀", "腿", "脚踝", "手指", "指甲",
        "纹身", "刺青", "疤痕", "胎记", "痣",
        "耳环", "耳饰", "项链", "发簪", "发饰", "头饰", "饰品",
        "穿着", "衣着", "服装", "服饰", "衣装", "装扮", "打扮",
        "裙子", "长裙", "短裙", "连衣裙", "裙", "袍", "衣", "裤", "袜", "鞋", "靴",
        "手套", "披风", "斗篷", "制服", "铠甲",
        "高挑", "娇小", "丰满", "纤细", "修长", "白皙", "黝黑", "肌肉", "曲线",
        "年龄", "雀斑", "酒窝", "眼罩", "眼镜", "面具", "锁骨", "脖颈", "手腕",
        "马甲", "衬衣", "衬衫", "腰带", "披肩", "围巾", "戒指", "手镯", "胸针",
        "宝石", "纹章",
    ))
)
_VISUAL_CUE_EN_RE = re.compile(
    r"\b(?:appearance|body|build|height|tall|hair|eyes?|pupils?|"
    r"skin|complexion|face|facial|lips?|cheeks?|nose|brows?|lashes|"
    r"breasts?|bust|waist|hips?|legs?|thighs?|feet|foot|tattoo|scar|birthmark|"
    r"clothing|clothes|outfit|attire|dress|robe|skirt|shirt|pants|trousers|"
    r"stockings|socks|shoes|boots|gloves|cloak|cape|uniform|armor|"
    r"accessor(?:y|ies)|ornament|jewel(?:ry|s)?|necklace|earrings?|bracelet|hairpin|"
    r"voluptuous|slender|slim|curvy|muscular|pale|tanned)\b",
    re.I,
)
# 只用来判断「这条像不像角色条目」：必须是人物身体/仪容词，不能只靠服装词
# （否则「【外观】黑色长裙」这类服装/道具条目也会被当成角色）。
_PERSON_BODY_CUE_RE = re.compile(
    r"外貌|容貌|长相|面容|样貌|外表|身高|体型|体态|身形|体格|身材|"
    r"肌肤|皮肤|肤色|发色|发型|头发|长发|短发|卷发|直发|秀发|马尾|辫子|刘海|"
    r"银发|金发|黑发|红发|白发|棕发|褐发|青发|蓝发|粉发|紫发|"
    r"眼眸|眼睛|双眼|瞳孔|瞳色|双眸|眼瞳|眸子|眉眼|眉毛|睫毛|"
    r"金眼|银眼|红眼|蓝眼|绿眼|紫眼|异色瞳|"
    r"鼻子|鼻梁|嘴唇|唇色|朱唇|脸颊|脸型|脸|五官|胸部|乳房|巨乳|丰乳|乳沟|胸|"
    r"腰|臀|腿|脚踝|手指|指甲|纹身|刺青|疤痕|胎记|痣|锁骨|脖颈|手腕|"
    r"\b(?:appearance|hair|eyes?|pupil|skin|complexion|face|facial|lips?|cheek|nose|"
    r"brow|breast|bust|waist|hips?|legs?|thigh|tattoo|scar|birthmark)\b",
    re.I,
)
_PERSON_HEADING_RE = re.compile(
    r"外貌|容貌|长相|面容|样貌|外表|形象|身高|体型|体态|身形|体格|身材|"
    r"穿着|衣着|服装|服饰|衣装|装扮|打扮|发型|发色|眼睛|眼眸|瞳色|肤色|皮肤|"
    r"\b(?:appearance|body|build|height|hair|eyes?|skin|complexion|clothing|"
    r"outfit|attire|dress)\b",
    re.I,
)
_PERSON_PRONOUN_RE = re.compile(r"[她他]|她们|他们|\b(?:she|he|her|his|him)\b", re.I)
_CHARACTER_MARKER_RE = re.compile(
    r"角色卡|人物卡|角色条目|^(?:角色|人物)\s*[：:·]",
)
# 只认**标题/小标题**声明的角色卡：系统指令正文里顺带提到「角色条目」不能算
# （真机 2026-10-23：`系统判定机制·AI叙事核心指令` 因此被当成角色画像）。

# 条目**标题**（comment）明确属于事件/势力/系统/地点/物品等非角色类别 ⇒ 整条不收。
# 真机 2026-10-23：`大事件·…`、`势力与种族·…` 都因正文顺带提到某人发色而混入画像。
_NON_CHARACTER_TITLE_RE = re.compile(
    r"大事件|事件|剧情|时间线|时间轴|年表|速览|名录|一览|列表|清单|"
    r"系统|机制|规则|指令|设定集|世界观|"
    r"势力|种族|宗门|皇室|组织|阵营|体系|境界|心决|功法|法术|技能|"
    r"地点|场景|地图|区域|城池|遗迹|物品|道具|装备|资源|经济|"
    r"任务|玩法|指南|说明|前言|目录|索引",
    re.I,
)
# 剧情/性格/好感等段落里也会有「金眸」「红发」这类外貌词，但它们是**动态或非外貌**事实；
# 把它们当稳定画像会污染提示词（真机 2026-10-23：开场白/命定危机整段被收进来）。
_DENIED_SECTION_RE = re.compile(
    r"剧情|动态|开场白|场景|好感|命定|性格|反差|情爱|性癖|玩法|机制|状态|关系|"
    r"历史|事件|触发|危机|阶段|性爱|床戏|"
    r"\b(?:progress|dynamic|scene|history|relationship|status)\b",
    re.I,
)
# 命中这些标题 ⇒ 重新进入「收外貌线索」态（把上一段动态段的拒绝态收回来）。
_SECTION_RESET_RE = re.compile(
    r"角色卡|人物卡|人物设定|角色设定|身份|简介|介绍|背景|"
    r"外貌|外观|容貌|长相|面容|样貌|外表|形象|身高|体型|体态|身形|体格|身材|"
    r"穿着|衣着|服装|服饰|衣装|装扮|打扮|"
    r"\b(?:appearance|profile|identity|description|background|outfit|clothing)\b",
    re.I,
)
_HEADING_RE = re.compile(
    r"^(?:【(?P<cn>[^】\n]{1,30})】|#{1,6}\s*(?P<md>[^\n]{1,40})|"
    r"\*\*(?P<bold>[^*\n]{1,30})\*\*\s*[：:]?)\s*(?P<tail>.*)$"
)
_LABEL_RE = re.compile(
    r"^(?:[-*+]\s*)?(?P<label>【[^】\n]{1,24}】|\*\*[^*\n]{1,24}\*\*|[^：:\n]{1,20})"
    r"\s*[：:]\s*(?P<body>.*)$"
)
_SENTENCE_RE = re.compile(r"[^\n。！？!?；;]+[。！？!?；;]?")
_CLIP_BOUNDARY = "。！？；\n，,;"


def _has_visual_cue(text: str) -> bool:
    return bool(_VISUAL_CUE_CN_RE.search(text) or _VISUAL_CUE_EN_RE.search(text))


def _visual_cue_hits(text: str) -> set[str]:
    hits = {match.group(0) for match in _VISUAL_CUE_CN_RE.finditer(text)}
    hits.update(match.group(0).lower() for match in _VISUAL_CUE_EN_RE.finditer(text))
    return hits


def _heading_of(line: str) -> tuple[str, str] | None:
    match = _HEADING_RE.match(line)
    if not match:
        return None
    label = match.group("cn") or match.group("md") or match.group("bold") or ""
    return label.strip().strip("#").strip(), match.group("tail").strip()


def _label_of(line: str) -> tuple[str, str] | None:
    match = _LABEL_RE.match(line)
    if not match:
        return None
    label = match.group("label").strip().strip("【】[]*#").strip()
    if not label or not _has_visual_cue(label):
        return None
    return label, match.group("body").strip()


def _character_marker(item: dict[str, Any], content: str) -> bool:
    comment = str(item.get("comment") or "").strip()
    if comment and _CHARACTER_MARKER_RE.search(comment):
        return True
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = _heading_of(stripped)
        if heading and _CHARACTER_MARKER_RE.search(heading[0]):
            return True
    return False


def _section_denied(label: str, denied: bool) -> bool:
    """标题之后的「是否拒绝外貌线索」状态。

    命中动态/剧情类 ⇒ 拒绝；命中画像/外观类 ⇒ 恢复；中性标题 ⇒ **沿用上一段状态**。
    中性标题不能解拒绝态：`【命定危机】` 下的 `【第一次是怎么被迫的】` 不含任何关键词，
    若按标题各自判定就会把正文里的外貌句重新收进来（真机 2026-10-23 实测泄漏）。
    """
    if _DENIED_SECTION_RE.search(label):
        return True
    if _SECTION_RESET_RE.search(label):
        return False
    return denied


def _section_boundaries(content: str) -> list[tuple[int, bool]]:
    """每个 `【…】`/Markdown 标题的起点与该标题之后的 sticky 拒绝态。"""
    boundaries: list[tuple[int, bool]] = []
    pos = 0
    denied = False
    for line in content.splitlines(keepends=True):
        heading = _heading_of(line.strip())
        if heading:
            denied = _section_denied(heading[0], denied)
            boundaries.append((pos, denied))
        pos += len(line)
    return boundaries


def _section_denied_at(offset: int, boundaries: list[tuple[int, bool]]) -> bool:
    denied = False
    for start, state in boundaries:
        if start > offset:
            break
        denied = state
    return denied


def _block_body(text: str) -> str:
    first, _, rest = text.partition("\n")
    heading = _heading_of(first.strip())
    if not heading:
        return text.strip()
    inline = heading[1].strip()
    return "\n".join(part for part in (inline, rest) if part.strip()).strip()


def _visual_block_spans(content: str) -> list[tuple[int, int, str]]:
    """结构化外貌块：`【外貌】…`、`外观：…`、`## Appearance`、`**穿着**：…`。"""
    lines = content.splitlines(keepends=True)
    offsets: list[tuple[int, int]] = []
    pos = 0
    for line in lines:
        offsets.append((pos, pos + len(line)))
        pos += len(line)
    spans: list[tuple[int, int, str]] = []
    boundaries = _section_boundaries(content)
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        heading = _heading_of(line)
        if heading:
            if (
                not _has_visual_cue(heading[0])
                or _section_denied_at(offsets[i][0], boundaries)
            ):
                i += 1
                continue
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if _heading_of(nxt) or _label_of(nxt):
                    break
                j += 1
            end = offsets[j - 1][1] if j > i else offsets[i][1]
            text = content[offsets[i][0]:end].strip()
            if _block_body(text):
                spans.append((offsets[i][0], end, text))
            i = j
            continue
        label = _label_of(line)
        if (
            label and label[1]
            and not _section_denied_at(offsets[i][0], boundaries)
        ):
            end = offsets[i][1]
            text = content[offsets[i][0]:end].strip()
            if text:
                spans.append((offsets[i][0], end, text))
        i += 1
    return spans


def _cue_spans(
    content: str, blocks: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """结构化块之外的**带外貌线索整句**（简介里的瞳色、散落的身高/服装描述）。"""
    spans: list[tuple[int, int, str]] = []
    boundaries = _section_boundaries(content)
    for match in _SENTENCE_RE.finditer(content):
        raw_start = match.start()
        start, end = match.span()
        if _section_denied_at(raw_start, boundaries):
            continue
        raw = match.group(0)
        start += len(raw) - len(raw.lstrip())
        text = raw.strip()
        if len(text) < 4:
            continue
        prefix = re.match(
            r"^(?:【[^】\n]{1,30}】|#{1,6}\s*|\*\*[^*\n]{1,30}\*\*)\s*", text,
        )
        if prefix:
            start += prefix.end()
            text = text[prefix.end():].strip()
        if len(text) < 4:
            continue
        if any(not (end <= block_start or start >= block_end)
               for block_start, block_end, _ in blocks):
            continue
        if _has_visual_cue(text):
            spans.append((start, end, text))
    return spans


def _visual_profile_name(item: dict[str, Any], content: str) -> str:
    def _clean(value: str) -> str:
        return re.sub(r"^[【\[]\s*|\s*[】\]]$", "", value.strip()).strip()

    comment = str(item.get("comment") or "").strip()
    if "角色卡·" in comment:
        return _clean(comment.split("角色卡·", 1)[-1]) or "角色"
    titled = re.search(r"【角色卡·([^】\n]{1,30})】", content)
    if titled:
        return _clean(titled.group(1)) or "角色"
    titled = re.search(r"【(?:角色|人物)\s*[：:·]\s*([^】\n]{1,30})】", content)
    if titled:
        return _clean(titled.group(1)) or "角色"
    if comment and comment not in {"角色", "人物", "新条目", "条目"}:
        return _clean(comment)
    keys = item.get("keys") or []
    if isinstance(keys, str):
        keys = [keys]
    for key in keys:
        value = str(key).strip()
        if value:
            return _clean(value)
    return "角色"


def _visual_profile_candidate(item: dict[str, Any]) -> str:
    """单条条目的外貌画像文本；抽不到外貌信号 ⇒ 空串（不注入）。"""
    content = str(item.get("content") or "").strip()
    if not content:
        return ""
    marker = _character_marker(item, content)
    # 条目标题自身的类别优先于正文里的角色字样：系统/事件条目常带
    # `【角色条目规则】` 这类小标题，不能反过来把整条事件叙述放进画像。
    comment = str(item.get("comment") or "").strip()
    if (
        not _CHARACTER_MARKER_RE.search(comment)
        and _NON_CHARACTER_TITLE_RE.search(comment)
    ):
        return ""
    blocks = _visual_block_spans(content)
    spans = sorted([*blocks, *_cue_spans(content, blocks)], key=lambda span: span[0])
    parts = list(dict.fromkeys(text for _, _, text in spans))
    person_heading = any(
        (heading := _heading_of(line.strip())) and _PERSON_HEADING_RE.search(heading[0])
        for line in content.splitlines()
    )
    if not parts:
        # 结构化与线索句都抽不到：只有「无标题的纯散文角色条目 + 正文确有外貌信号」
        # 才整条兜底；有条目结构却只在动态/性格段出现外貌词 ⇒ 宁可空，不把剧情当稳定外貌。
        if not (marker and _has_visual_cue(content)):
            return ""
        if any(_heading_of(line.strip()) for line in content.splitlines()):
            return ""
        parts = [content]
    elif not marker and not person_heading:
        # 非角色卡命名、也没有人物标题：必须再有人物身体信号，避免把服装/道具/地点
        # 条目误当角色外观。
        hits = _visual_cue_hits(content)
        if not (
            _PERSON_BODY_CUE_RE.search(content)
            and (_PERSON_PRONOUN_RE.search(content) or len(hits) >= 2)
        ):
            return ""
    body = "；".join(parts).strip()
    if not body:
        return ""
    return f"{_visual_profile_name(item, content)}：{body}"


def _clip_visual_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(char) for char in _CLIP_BOUNDARY)
    if cut >= max(16, limit // 2):
        return window[:cut + 1].rstrip()
    return window.rstrip()


def repo_visual_profiles(base: str, repo_id: str, query: str, *, max_chars: int = 4_000) -> str:
    """提取本轮命中角色的稳定视觉锚点，供主模型与当前状态合并。

    结构不再依赖「角色卡·」前缀或【外貌/身材/穿着】行首锚点：任意导入格式都走
    「结构化块 → 外貌线索句 → 整条兜底」三层抽取；超出预算时**裁剪**而不是整条丢弃。
    """
    book = read_repo_snapshot(base, repo_id)
    if not book or not query or max_chars <= 0:
        return ""
    from app.services import worldbook_edit
    profiles: list[str] = []
    used = 0
    for item in worldbook_edit.list_entries(book):
        if not _entry_matches(item, query):
            continue
        candidate = _visual_profile_candidate(item)
        if not candidate:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(candidate) > remaining:
            candidate = _clip_visual_text(candidate, remaining)
        if not candidate.strip():
            continue
        profiles.append(candidate)
        used += len(candidate) + 1
        if used >= max_chars:
            break
    return "\n".join(profiles)


def repo_snapshot_context(base: str, repo_id: str, *, query: str = "",
                          max_chars: int = 20_000,
                          allowed_indices: set[int] | frozenset[int] | None = None) -> str:
    """给 Curator 的带稳定 index 条目视图；限制长度避免重复灌入整本大书。"""
    book = read_repo_snapshot(base, repo_id)
    if not book:
        return ""
    from app.services import worldbook_edit
    indexed = worldbook_edit.list_entries(book)
    if allowed_indices is not None:
        indexed = [item for item in indexed if item["index"] in allowed_indices]
    if query:
        indexed.sort(key=lambda item: not _entry_matches(item, query))
    selected: list[dict[str, Any]] = []
    for item in indexed:
        candidate = json.dumps([*selected, item], ensure_ascii=False, separators=(",", ":"))
        if len(candidate) > max_chars:
            if not selected:
                clipped = dict(item)
                clipped["content"] = str(clipped.get("content") or "")[:max(0, max_chars - 256)]
                selected.append(clipped)
            break
        selected.append(item)
    return json.dumps(selected, ensure_ascii=False, separators=(",", ":"))


# ── 主人公性别（零 LLM 机械解析，2026-09-20）──────────────────────────────────
# 用户定案：性别**只作画面事实注入给模型**（不机械拼 1girl/1boy 硬标签），且只服务**主人公**；
# 其他角色性别沿用「世界书条目兜底、不做代码约束」的既有定案。解析一律「宁缺勿猜」：
# 拿不准返回空串，调用方不注入。
#
# ⚠ 属主说明（importlinter `roleplay-agency-stack`）：文本解析器**必须留在本模块**。
# 链路 `character_state → repo_meta → character_store → worldbook_store` 与 `scene_illustration`
# 同层，worldbook_store 反向 import scene_illustration 即构成同层依赖违规（2026-09-20 实测
# lint-imports BROKEN）。故解析器随**条目正文属主**落在本模块；scene_illustration 只保留
# 「通用表读取」与「外观段注入」——那两件都不碰条目正文。
GENDER_FEMALE = "女"
GENDER_MALE = "男"

# 三级按序短路（先命中先返回），不做统计投票：
# ① 显式声明（最可靠）：`【性别】女` / `性别：男`
_GENDER_DECLARED_RE = re.compile(
    r"【性别】\s*([^\r\n【】]{1,10})"
    r"|性别\s*[：:]\s*([^\r\n，,。；;、\s]{1,6})"
)
# ② 系词句：`他是…少年` / `她是一位…女性`（名字或代词作主语，短窗口内命中）
# ③ 身份名词：与名字/代词相邻的 `王女` / `公主` / `女仆` / `王子` / `国王` …
#    ⚠ ②③ 都**必须**有主语（名字或代词）在场 —— 全文裸扫身份名词会把男主人公条目里
#    顺带提到的「贵妇」误判成主人公性别（真机误判比缺省更糟）。
_GENDER_SUBJECT_PRONOUNS = r"[他她](?:们)?"

_GENDER_NOUNS = (
    "女性", "女人", "少女", "女子", "王女", "公主", "女王", "皇后",
    "熟妇", "贵妇", "侍女", "女仆", "母亲", "妈妈",
    "男性", "男人", "少年", "男子", "王子", "国王", "皇帝", "父亲", "爸爸",
)
_GENDER_FEMALE_WORDS = frozenset({
    "女性", "女人", "少女", "女子", "王女", "公主", "女王", "皇后",
    "熟妇", "贵妇", "侍女", "女仆", "母亲", "妈妈",
})
_GENDER_MALE_WORDS = frozenset({
    "男性", "男人", "少年", "男子", "王子", "国王", "皇帝", "父亲", "爸爸",
})


def _gender_of_noun(noun: str) -> str:
    if noun in _GENDER_FEMALE_WORDS:
        return GENDER_FEMALE
    if noun in _GENDER_MALE_WORDS:
        return GENDER_MALE
    return ""


def _gender_regexes(name: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """构造 ②系词句 / ③身份名词 的正则：主语＝名字（若有）或第三人称代词。"""
    subject = f"(?:{re.escape(name)}|{_GENDER_SUBJECT_PRONOUNS})" if name else _GENDER_SUBJECT_PRONOUNS
    copula = re.compile(
        subject + r"(?:是|为|乃)[^。\r\n]{0,12}?(" + "|".join(_GENDER_NOUNS) + r")"
    )
    noun = re.compile(subject + r"[^。\r\n]{0,8}?(" + "|".join(_GENDER_NOUNS) + r")")
    return copula, noun


def gender_from_text(text: str, name: str = "") -> str:
    """从条目正文机械解析性别（零 LLM，确定性）：`女` / `男` / `""`（无信号）。

    三级按序短路，先命中先返回；都取不到 ⇒ 空串（调用方不注入）。
    ⚠ `name` 为空时②③只认第三人称代词作主语，仍**不做**全文裸扫身份名词。
    """
    if not text:
        return ""
    declared = _GENDER_DECLARED_RE.search(text)
    if declared:
        raw = (declared.group(1) or declared.group(2) or "").strip()
        if "女" in raw:
            return GENDER_FEMALE
        if "男" in raw:
            return GENDER_MALE
        return raw
    copula, noun = _gender_regexes(str(name or "").strip())
    for pattern in (copula, noun):
        match = pattern.search(text)
        if match:
            gender = _gender_of_noun(match.group(1))
            if gender:
                return gender
    return ""


def repo_character_gender(base: str, repo_id: str, name: str) -> str:
    """从作品世界书快照解析**任意角色**的性别（零 LLM，确定性）：`女` / `男` / `""`。

    第 46 刀（2026-09-22）新增：这是 `repo_protagonist_gender` 的**通用版**，共用同一套
    条目扫描与三级解析口径（不只是主人公）。
    ⚠ **用途仅限**「按画面主体的实际性别组成写人数词」这类**结构化计数**；**画面事实注入
      仍只对主人公**（23 刀红线：性别只对主人公、只作画面事实注入、宁缺勿猜）。
    查找顺序＝① comment 恰为「角色卡·<名>」的条目优先；② 正文或 keys 提及该名的条目按序兜底。
    读不到快照 / 名为空 / 都无信号 ⇒ `""`（调用方不注入，宁缺勿猜）。
    """
    target = str(name or "").strip()
    if not target:
        return ""
    book = read_repo_snapshot(base, repo_id)
    if not book:
        return ""
    from app.services import worldbook_edit
    exact_comment = f"角色卡·{target}"
    preferred: list[str] = []
    fallback: list[str] = []
    for item in worldbook_edit.list_entries(book):
        comment = str(item.get("comment") or "").strip()
        content = str(item.get("content") or "")
        keys = item.get("keys") or item.get("key") or []
        if isinstance(keys, str):
            keys = [keys]
        if comment == exact_comment:
            preferred.append(content)
            continue
        if target in content or any(target == str(key).strip() for key in keys):
            fallback.append(content)
    for content in [*preferred, *fallback]:
        gender = gender_from_text(content, target)
        if gender:
            return gender
    return ""


def repo_protagonist_gender(base: str, repo_id: str, name: str) -> str:
    """从作品世界书快照解析**主人公**性别（零 LLM，确定性）：`女` / `男` / `""`。

    2026-09-20 用户定案：**画面事实注入**只服务主人公（其他角色性别沿用条目兜底，
    不做代码约束）；第 46 刀起实现委托给通用版 `repo_character_gender`（同一口径），
    本函数保留为「主人公语义」的具名入口。
    """
    return repo_character_gender(base, repo_id, name)


def repo_protagonist_name(
    base: str, repo_id: str, user_name: str = "",
) -> tuple[str, str]:
    """从三段来源解析**主人公姓名**（零 LLM，确定性）：返回 `(name, source)`。

    第 51 刀（2026-09-22 用户定案「给 FP2 建真源」）：修复 **D4／D5** ——
    叙事者真源不能只靠 `ctx["user_name"]`（生产里常空 ⇒ 第 42 刀数据层保护集体空转），
    也不存在反向解析主角名的函数。三段定序（取第一个非空者）：

      ① `user_name` 非空 ⇒ `(user_name, "user_name")`；
      ② 通用表「主角信息表」姓名列（单卡）非空 ⇒ `(name, "protagonist_table")`；
      ③ 世界书「主角」条目兜底：comment／keys 含「主角」或「主人公」且形如
         `角色卡·<名>` 的条目 ⇒ `(<名>, "worldbook")`。

    三段都取不到 ⇒ `("", "")`（**宁缺勿猜**）。读盘／解析失败一律降级为空，不抛。

    ⚠ `source` 只用于观察与审计，不进入任何判定分支；姓名空 ⇒ 一律返回空串。
    """
    uname = str(user_name or "").strip()
    if uname:
        return uname, "user_name"
    if base and repo_id:
        # ② 通用表「主角信息表」姓名列（单卡表即该卡的值）。
        try:
            from app.services import table_store
            name = table_store._first_cell(
                table_store.load(base, repo_id),
                table_store.PROTAGONIST_TABLE, "姓名",
            )
        except Exception as exc:  # noqa: BLE001 — 表格读失败不得打断出图链
            _LOG.warning("repo_protagonist_name 主角信息表读取失败 base=%s repo=%s: %s", base, repo_id, exc)
            name = ""
        if name:
            return name, "protagonist_table"
        # ③ 世界书「主角」条目兜底（只认 `角色卡·<名>` 这种明确身份，不猜正文首行）。
        try:
            from app.services import worldbook_edit
            book = read_repo_snapshot(base, repo_id)
            if book:
                for item in worldbook_edit.list_entries(book):
                    comment = str(item.get("comment") or "").strip()
                    keys = item.get("keys") or item.get("key") or []
                    if isinstance(keys, str):
                        keys = [keys]
                    keytext = " ".join(str(k) for k in keys)
                    # 明确的「主角/主人公」标记；排除「角色卡·X」（含「主角」子串但只是角色卡，
                    # 不是主角标记）——否则会误把任意角色卡当主人公。
                    is_protagonist = (
                        comment in ("主角", "主人公", "主角信息", "主人公信息")
                        or ("主角" in comment and "角色卡" not in comment)
                        or ("主角" in keytext or "主人公" in keytext)
                    )
                    if not is_protagonist:
                        continue
                    if comment.startswith("角色卡·"):
                        cand = comment[len("角色卡·"):].strip()
                        if cand:
                            return cand, "worldbook"
        except Exception as exc:  # noqa: BLE001 — 世界书读失败不得打断出图链
            _LOG.warning("repo_protagonist_name 世界书兜底读取失败 base=%s repo=%s: %s", base, repo_id, exc)
    return "", ""


# 机制层标题前缀（`系统判定机制·` / `全局机制·` / `局部机制·`）。
# 2026-09-23：这些层是**建卡时由用户设计**的，运行时 curator 不得自造同名层条目——
# 真机实锤「全局机制·{{user}}(少宗主)」「全局机制·龙阳决与双修」「全局机制·龙阳双修采补突破」
# 三条（175 / 39 / 444 字符）正文全是**角色状态与剧情流水**（本该进对应角色卡的动态区），
# 却常年挂 `constant=True`（全程恒开、全文注入、永不截断）⇒ 成了「经脉」等说法的恒定注入源。
_MECHANISM_TITLE_PREFIXES = ("系统判定机制", "全局机制", "局部机制")


def _merge_character_dynamic(existing: str, update: str) -> str:
    """保留原文底座 + 替换/追加末尾唯一的【剧情进展·动态】区。

    与「角色卡」无关，对所有世界书条目通用（2026-09-12 起 apply_repo_ops 统一走这里）。
    关键性质：底座逐字节不变，只让**末尾**动态区变化——上游前缀缓存的复用点因此位于
    条目末尾而非条目起始。
    """
    marker = "【剧情进展·动态】"
    base = existing.split(marker, 1)[0].rstrip()
    dynamic = update.split(marker, 1)[-1].strip()
    return f"{base}\n\n{marker}\n{dynamic}"


def apply_repo_ops(base: str, repo_id: str, ops: list[dict[str, Any]], *,
                   allowed_update_indices: set[int] | frozenset[int] | None = None,
                   rejections: list[dict[str, Any]] | None = None) -> int:
    """对当前小仓库快照执行 Curator 增改；更新需 evidence，删除类 op 一律拒绝。

    **所有条目**的 worldbook_update 都只允许按剧情进度改正文末尾的动态段
    （_merge_character_dynamic：保留原文底座 + 替换/追加唯一的【剧情进展·动态】区）；
    身份键（comment/keys/constant/enabled）不随 ops 变更并上报 rejections，理由是 comment
    仍是角色名单/主角识别与视觉画像**命名**锚点，改名会让角色识别与画像段落名失配
    （2026-08-31 实锤：舞姬恋↔舞柔条目连不上 → 空泛提示词；2026-10-23 起外貌提取本身
    不再依赖「角色卡·」前缀，但画像名字仍取 comment 前缀）。

    ⚠ 2026-09-12 修：底座保护原先只对「comment 含『角色卡·』」的条目生效，非角色卡条目走
    `patch = {"content": text}` **整体覆盖**，导致 curator 一次 update 就把机制/设定/地点条目
    的正文顶掉——实测「全局机制·世界自转与事件登门」（constant，1,659 字符，自述「本卡最高级
    驱动机制」）被剧情快照覆盖，原文丢失；且因世界书稳定序列按内容 hash 解析（worldbook.py），
    该槽位整条塌陷、其后条目全部左移，跨轮前缀只剩 9.0%。现口径与角色卡统一。

    拒绝明细写入 rejections（调用方负责 trace），不抛错不中断整批。
    """
    book = read_repo_snapshot(base, repo_id)
    if not book:
        return 0
    from app.services import worldbook_edit
    applied = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "").strip()
        if kind in {"worldbook_delete", "delete", "worldbook_remove", "remove"}:
            if rejections is not None:
                record: dict[str, Any] = {"op": kind, "reason": "delete_forbidden"}
                if op.get("index") is not None:
                    record["index"] = op.get("index")
                rejections.append(record)
            continue
        text = str(op.get("text") or "").strip()
        patch: dict[str, Any] = {"content": text}
        if "title" in op:
            patch["comment"] = str(op.get("title") or "").strip()
        if "keys" in op and isinstance(op.get("keys"), list):
            patch["keys"] = op["keys"]
        if "constant" in op:
            patch["constant"] = bool(op["constant"])
        if "enabled" in op:
            patch["enabled"] = bool(op["enabled"])
        if kind == "worldbook_add" and text:
            # 2026-09-23（C 约束）：
            #  ① 机制层标题前缀属**建卡时用户设计**的层，运行时新增一律拒绝（无法可靠改名，
            #     宁可漏记也不要把角色状态挂进「全局机制·X」这类恒开条目）；
            #  ② 运行时新增条目**一律 constant=False**——constant ⇒ 全程恒开、全文注入、
            #     永不截断，代价极高，只能由用户在卡里显式设置。
            title = str(patch.get("comment") or "").strip()
            if title.split("·", 1)[0].strip() in _MECHANISM_TITLE_PREFIXES:
                if rejections is not None:
                    rejections.append({
                        "op": kind, "title": title,
                        "reason": "mechanism_layer_forbidden",
                    })
                continue
            if bool(op.get("constant")):
                if rejections is not None:
                    rejections.append({
                        "op": kind, "title": title, "reason": "constant_forced_off",
                    })
            patch["constant"] = False
            # L1 写入闸（2026-10-22）：新增与既有条目重复 ⇒ 拒绝并点名既有条目，
            # 让 curator 拿到「改为 update 该条」的明确反馈（与机制层/constant 拒绝同口径）。
            try:
                worldbook_edit.add_entry(book, patch)
            except worldbook_edit.DuplicateEntryError as dup:
                if rejections is not None:
                    rejections.append({
                        "op": kind, "title": title,
                        "reason": "duplicate_entry",
                        "existing": dup.existing_comment,
                        "existing_index": dup.existing_index,
                        "detail": dup.reason,
                    })
                continue
            applied += 1
        elif kind == "worldbook_update" and text and str(op.get("evidence") or "").strip():
            try:
                index = int(op.get("index"))
            except (TypeError, ValueError):
                continue
            if allowed_update_indices is not None and index not in allowed_update_indices:
                continue
            entries = worldbook_edit.list_entries(book)
            if 0 <= index < len(entries):
                current = entries[index]
                content = str(current.get("content") or "")
                # 2026-09-12：底座保护对所有条目生效（原先只对「角色卡·」生效）。
                # 统一口径＝原文底座 + 末尾唯一【剧情进展·动态】区，身份键一律不改。
                merged = _merge_character_dynamic(content, text)
                stripped = sorted(key for key in patch if key != "content")
                patch = {"content": merged}
                if rejections is not None and stripped:
                    rejections.append({
                        "op": kind, "index": index,
                        "reason": "identity_keys_forbidden",
                        "fields": stripped,
                    })
            if worldbook_edit.update_entry(book, index, patch):
                applied += 1
    if applied:
        p = repo_snapshot_path(base, repo_id)
        p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return applied
