"""场景卡（scene_card）：从正文场景窗口确定性提取空间构图信息（2026-11-12）。

目标不是写一句场景提示词，而是先把「这个场景里有什么、各在哪、光从哪来、机位怎么站」
抽成可审计的结构，再交给文生图生成**空场景环境图**。本模块纯函数、0 LLM：
- 只认正文里写出的建筑/陈设/方位/光线/颜色；
- 没有方位的固定物标 `unspecified`，不猜；
- 机位缺失时给 establishing shot 默认值并标 `inferred`；
- `build_scene_prompt` 只拼环境，不写人物/动作。
"""
from __future__ import annotations

import re
from typing import Iterable

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")

# 地点类型：先匹配长词，避免「礼堂」被「厅」抢走。
_SPACE_TYPES: tuple[tuple[str, str], ...] = (
    ("旧仓库", "warehouse"), ("仓库", "warehouse"), ("礼堂", "ceremony hall"),
    ("教堂", "chapel"), ("卧室", "bedroom"), ("房间", "room"),
    ("走廊", "corridor"), ("过道", "corridor"), ("庭院", "courtyard"),
    ("花园", "garden"), ("街巷", "alley"), ("街道", "street"),
    ("车厢", "train carriage"), ("船舱", "ship cabin"), ("办公室", "office"),
    ("教室", "classroom"), ("酒吧", "bar"), ("浴室", "bathroom"),
    ("厨房", "kitchen"), ("大厅", "hall"), ("地下室", "basement"),
    ("阁楼", "attic"), ("码头", "dock"), ("车站", "station"),
)

# 建筑固定结构。
_ARCHITECTURE: tuple[tuple[str, str], ...] = (
    ("侧门", "side door"), ("门框", "door frame"), ("木门", "wooden door"),
    ("大门", "main door"), ("门口", "doorway"), ("拱门", "archway"),
    ("窗户", "window"), ("窗", "window"), ("墙壁", "wall"), ("墙", "wall"),
    ("房梁", "ceiling beam"), ("横梁", "beam"), ("柱子", "pillar"),
    ("台阶", "steps"), ("楼梯", "staircase"), ("地板", "floor"),
    ("天花板", "ceiling"), ("帷幔", "drapery"), ("窗帘", "curtain"),
    ("吊灯", "chandelier"), ("壁灯", "wall lamp"), ("烛台", "candlestick"),
    ("栏杆", "railing"), ("梯子", "ladder"), ("壁炉", "fireplace"),
)

# 可移动但固定摆位的陈设/道具。
_FIXED_PROPS: tuple[tuple[str, str], ...] = (
    ("木板台", "wooden platform"), ("台子", "platform"), ("舞台", "stage"),
    ("红毯", "red carpet"), ("地毯", "carpet"), ("长桌", "long table"),
    ("桌子", "table"), ("床头柜", "nightstand"), ("柜子", "cabinet"),
    ("床", "bed"), ("镜子", "mirror"), ("花桶", "flower bucket"),
    ("花篮", "flower basket"), ("长椅", "bench"), ("十字架", "cross"),
    ("祭坛", "altar"), ("蜡烛", "candles"), ("白纱", "white drapery"),
    ("花环", "flower garland"), ("屏风", "folding screen"),
)

# 方位：长词优先。
_POSITIONS: tuple[tuple[str, str], ...] = (
    ("左侧", "left"), ("左边", "left"), ("左方", "left"), ("左面", "left"),
    ("右侧", "right"), ("右边", "right"), ("右方", "right"), ("右面", "right"),
    ("中央", "center"), ("中间", "center"), ("正中", "center"),
    ("尽头", "back"), ("深处", "back"), ("门口", "entrance"),
    ("入口", "entrance"), ("门前", "entrance"), ("窗边", "window side"),
    ("窗前", "window side"), ("墙角", "corner"), ("上方", "upper area"),
    ("顶部", "upper area"), ("顶上", "upper area"), ("下方", "lower area"),
    ("地面", "lower area"), ("脚下", "lower area"), ("两侧", "both sides"),
    ("对面", "opposite"), ("前面", "foreground"), ("前方", "foreground"),
    ("面前", "foreground"), ("后面", "background"), ("后方", "background"),
    ("背后", "background"),
)

_LIGHT_SOURCES: tuple[tuple[str, str], ...] = (
    ("吊灯", "chandelier"), ("烛光", "candlelight"), ("蜡烛", "candles"),
    ("月光", "moonlight"), ("阳光", "sunlight"), ("晨光", "morning light"),
    ("夕阳", "sunset light"), ("窗光", "window light"), ("灯光", "lamplight"),
    ("火光", "firelight"), ("霓虹", "neon"), ("灯笼", "lanterns"),
)
_LIGHT_QUALITY: tuple[tuple[str, str], ...] = (
    ("柔和", "soft"), ("刺眼", "harsh"), ("昏暗", "dim"), ("明亮", "bright"),
    ("朦胧", "hazy"), ("阴冷", "cold"), ("温暖", "warm"), ("斑驳", "dappled"),
)
_TIMES: tuple[tuple[str, str], ...] = (
    ("清晨", "dawn"), ("早晨", "morning"), ("正午", "noon"), ("午后", "afternoon"),
    ("黄昏", "dusk"), ("傍晚", "evening"), ("夜晚", "night"), ("深夜", "late night"),
    ("凌晨", "early morning"),
)
_WEATHERS: tuple[tuple[str, str], ...] = (
    ("雨", "rain"), ("雪", "snow"), ("雾", "fog"), ("风", "wind"),
    ("晴", "clear"), ("阴", "overcast"),
)
_PALETTE: tuple[tuple[str, str], ...] = (
    ("红", "red"), ("白", "white"), ("黑", "black"), ("金", "gold"),
    ("银", "silver"), ("灰", "gray"), ("蓝", "blue"), ("绿", "green"),
    ("紫", "purple"), ("粉", "pink"), ("棕", "brown"), ("木色", "wood tone"),
)

_CAMERA_SIZE: tuple[tuple[str, str], ...] = (
    ("大远景", "extreme wide"), ("远景", "wide"), ("全景", "wide"),
    ("广角", "wide"), ("中景", "medium"), ("近景", "close-up"),
    ("特写", "close-up"), ("大特写", "extreme close-up"),
)
_CAMERA_ANGLE: tuple[tuple[str, str], ...] = (
    ("俯拍", "high angle"), ("俯视", "high angle"), ("仰拍", "low angle"),
    ("仰视", "low angle"), ("平视", "eye level"), ("过肩", "over-the-shoulder"),
    ("第一人称", "POV"), ("pov", "POV"),
)
_LIGHT_DIRECTION: tuple[tuple[str, str], ...] = (
    ("从左", "from the left"), ("从右", "from the right"),
    ("从上方", "from above"), ("从门口", "from the doorway"),
    ("从缝里", "from the curtain gap"), ("从窗帘", "from the curtain"),
    ("从窗户", "from the window"), ("逆光", "backlit"), ("侧光", "side light"),
    ("顶光", "top light"), ("底光", "underlight"),
)

# 建筑/陈设的表面状态：只认同一分句里写出的状态词（「木门被拆掉了一半」）。
_CONDITION_CUES: tuple[tuple[str, str], ...] = (
    ("拆掉了一半", "half-dismantled with missing planks"),
    ("拆了一半", "half-dismantled with missing planks"),
    ("拆掉", "half-dismantled with missing planks"),
    ("被拆", "half-dismantled with missing planks"),
    ("破损", "damaged"),
    ("破旧", "worn"),
    ("半掩", "ajar"),
    ("虚掩", "ajar"),
    ("敞开", "wide open"),
    ("打开", "open"),
    ("开着", "open"),
    ("紧闭", "closed"),
    ("关着", "closed"),
    ("剩下的", "remaining"),
)

# 门类建筑自带「入口」语义：正文不写方位时不必标 unspecified。
_DOOR_ITEMS = {"wooden door", "main door", "doorway", "archway", "side door"}
# 路径类物件才允许跨逗号找「铺到/通向」的目标。
_PATH_CUES = ("红毯", "地毯", "台阶", "楼梯", "走廊", "过道", "通道")
_LANDMARK_MARKER = "同场景地标："


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(text or "") if part.strip()]


def _first_match(text: str, table: Iterable[tuple[str, str]]) -> str:
    for cue, value in table:
        if cue in text:
            return value
    return ""


def _matches(text: str, table: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(cue, value) for cue, value in table if cue in text]


_CLAUSE_MARKS = "，,。；;"


def _clause_bounds(sentence: str, index: int) -> tuple[int, int]:
    clause_start = max((sentence.rfind(mark, 0, index) for mark in _CLAUSE_MARKS),
                       default=-1) + 1
    ends = [sentence.find(mark, index) for mark in _CLAUSE_MARKS]
    ends = [pos for pos in ends if pos >= 0]
    return clause_start, min(ends, default=len(sentence))


def _nearest_position(sentence: str, index: int) -> str:
    """只在本分句里找方位。

    跨逗号取「最近」会把 `门框缠着白纱，红毯铺到中央的台子` 里的白纱也判成
    center——方位描述只约束它所在的那一个分句，跨句就是猜。
    """
    clause_start, clause_end = _clause_bounds(sentence, index)
    clause = sentence[clause_start:clause_end]
    best = ""
    best_distance = 10**9
    for cue, value in _POSITIONS:
        start = clause.find(cue)
        while start >= 0:
            distance = abs(start - (index - clause_start))
            if distance < best_distance:
                best, best_distance = value, distance
            start = clause.find(cue, start + 1)
    return best


def _relation_position(sentence: str, cue: str) -> str:
    """`从 X 到 Y` / `X 铺到 Y` / `X 通向 Y` 这类关系句。

    路径类（红毯/台阶…）允许中间隔逗号：「红毯是借的，边角磨得发白，一直铺到中央台子」；
    其他物件仍要求关系词紧跟本分句，否则「木门…红毯铺到台子」会把红毯的走向
    错记到木门上。
    """
    start = sentence.find(cue)
    if start < 0:
        return ""
    tail = sentence[start + len(cue):start + len(cue) + 40]
    if cue in _PATH_CUES:
        hit = re.search(r"(?:铺到|通向|延伸到|一直到)([^，。；]{1,20})", tail)
    else:
        hit = re.search(r"^[^，。；]{0,12}?(?:通向|延伸到)([^，。；]{1,20})", tail)
    if hit is None:
        return ""
    target = hit.group(1).strip()
    target_prop = _first_match(target, _FIXED_PROPS) or _first_match(target, _ARCHITECTURE)
    if target_prop == "platform" and "木板" in target:
        target_prop = "wooden platform"
    target_space = _first_match(target, _SPACE_TYPES)
    target_en = target_prop or target_space or target[:16]
    if not target_en:
        return ""
    if target_en in ("platform", "wooden platform", "stage"):
        head = sentence[:start]
        from_entrance = any(mark in head for mark in ("门前", "门口", "入口", "门边"))
        target_position = _nearest_position(target, 0)
        if from_entrance and target_position == "center":
            return f"from entrance to center ({target_en})"
        if target_position:
            return f"leading to {target_position} ({target_en})"
        return f"leading to {target_en}"
    return f"leading to {target_en}"


def _scan_entries(sentence: str, table: Iterable[tuple[str, str]]) -> list[tuple[str, str, int]]:
    """单句里按「长词优先、跨词不重叠」取命中。

    `木板台子` 同时含 `木板台` 与 `台子`；不压重叠会把同一件陈设写成两条
    （wooden platform + platform），提示词里就出现两个台子。
    """
    taken: list[tuple[int, int]] = []
    hits: list[tuple[str, str, int]] = []
    for cue, english in sorted(table, key=lambda pair: len(pair[0]), reverse=True):
        start = sentence.find(cue)
        if start < 0:
            continue
        end = start + len(cue)
        if any(start < used_end and used_start < end for used_start, used_end in taken):
            continue
        taken.append((start, end))
        hits.append((cue, english, start))
    return hits


def _condition_for(sentence: str, start: int) -> str:
    """取物件所在分句里的状态词（破损/半掩/敞开…），跨分句不认。"""
    clause_start, clause_end = _clause_bounds(sentence, start)
    return _first_match(sentence[clause_start:clause_end], _CONDITION_CUES)


def _entry(sentence: str, cue: str, english: str, kind: str, start: int) -> dict:
    position = _relation_position(sentence, cue) or _nearest_position(sentence, start)
    if not position and kind == "architecture" and english in _DOOR_ITEMS:
        position = "entrance"
    return {
        "item": english,
        "kind": kind,
        "position": position or "unspecified",
        "condition": _condition_for(sentence, start),
        "evidence": sentence[:100],
    }


def _merge_entries(entries: list[dict]) -> list[dict]:
    # 同场景同时出现「台子」与「木板台」时按更具体的木质台合并，避免提示词里
    # 出现两个台子。
    has_wooden_platform = any(entry["item"] == "wooden platform" for entry in entries)
    merged: dict[str, dict] = {}
    for entry in entries:
        if has_wooden_platform and entry["item"] == "platform":
            entry = {**entry, "item": "wooden platform"}
        key = str(entry["item"])
        previous = merged.get(key)
        if previous is None:
            merged[key] = entry
            continue
        old = str(previous.get("position") or "unspecified")
        new = str(entry.get("position") or "unspecified")
        if new != "unspecified" and new not in old:
            previous["position"] = f"{old}; {new}" if old != "unspecified" else new
        if len(str(entry.get("evidence") or "")) > len(str(previous.get("evidence") or "")):
            previous["evidence"] = entry["evidence"]
        condition = str(entry.get("condition") or "")
        old_condition = str(previous.get("condition") or "")
        if condition and condition not in old_condition:
            previous["condition"] = f"{old_condition}; {condition}" if old_condition else condition
    return list(merged.values())


def extract_scene_card(
    window: str, *, scene_key: str = "", locale: str = "", framing: str = "",
) -> dict:
    """从场景窗口抽 scene_card（确定性；无证据的项留空/标 inferred）。"""
    landmarks: list[str] = []
    if _LANDMARK_MARKER in window:
        # `scene_anchor` 末尾拼的是「同场景地标：木门框、木板台…」，那是召回提示，
        # 不是本段事实：当成正文会把每个地标都写成无证据的 unspecified 条目。
        window, _, tail = window.partition(_LANDMARK_MARKER)
        landmarks = [
            part.strip(" 。；;、") for part in re.split(r"[、,，;；]", tail)
            if part.strip(" 。；;、")
        ]
        window = window.rstrip(" ；;、")
    sentences = _sentences(window)
    architecture: list[dict] = []
    fixed_props: list[dict] = []
    lighting_sources: list[str] = []
    light_quality = ""
    time_of_day = ""
    weather = ""
    palette: list[str] = []
    light_direction = ""

    for sentence in sentences:
        for cue, english, start in _scan_entries(sentence, _ARCHITECTURE):
            architecture.append(_entry(sentence, cue, english, "architecture", start))
        for cue, english, start in _scan_entries(sentence, _FIXED_PROPS):
            if english == "platform":
                clause_start, clause_end = _clause_bounds(sentence, start)
                if "木板" in sentence[clause_start:clause_end]:
                    english = "wooden platform"
            fixed_props.append(_entry(sentence, cue, english, "fixed_prop", start))
        for cue, english in _LIGHT_SOURCES:
            if cue in sentence and english not in lighting_sources:
                lighting_sources.append(english)
        light_quality = light_quality or _first_match(sentence, _LIGHT_QUALITY)
        time_of_day = time_of_day or _first_match(sentence, _TIMES)
        weather = weather or _first_match(sentence, _WEATHERS)
        light_direction = light_direction or _first_match(sentence, _LIGHT_DIRECTION)
        for cue, english in _PALETTE:
            if english in palette:
                continue
            for match in re.finditer(cue, sentence):
                clause_start, clause_end = _clause_bounds(sentence, match.start())
                clause = sentence[clause_start:clause_end]
                # 颜色只在建筑/陈设分句里算环境色：「粉色项圈」不能把整间卧室染成粉色。
                if (any(cue_name in clause for cue_name, _ in _ARCHITECTURE)
                        or any(cue_name in clause for cue_name, _ in _FIXED_PROPS)):
                    palette.append(english)
                    break

    # 同场景地标是同场景其他镜提到的固定物（如「侧门」只出现在入场镜）：
    # 只收进建筑/陈设已知表，不把「台前」「走廊」这类方位/区域词当物件。
    for landmark in landmarks:
        for cue, english, start in _scan_entries(landmark, _ARCHITECTURE):
            architecture.append(_entry(landmark, cue, english, "architecture", start))
        for cue, english, start in _scan_entries(landmark, _FIXED_PROPS):
            fixed_props.append(_entry(landmark, cue, english, "fixed_prop", start))

    architecture = _merge_entries(architecture)
    fixed_props = _merge_entries(fixed_props)
    all_props = [*architecture, *fixed_props]
    prop_items = {str(entry["item"]) for entry in all_props}
    has_platform = bool(prop_items & {"wooden platform", "platform", "stage"})

    layout: dict[str, str] = {}
    layout_keys = ("left", "right", "center", "back", "entrance", "foreground", "background")
    # 精确方位优先：「木板台在中央」应占住 center，而不是被「红毯从门口铺到中央」
    # 这种跨越式描述先抢走。
    for entry in all_props:
        position = str(entry.get("position") or "")
        if position in layout_keys and position not in layout:
            layout[position] = str(entry["item"])
    for entry in all_props:
        item = str(entry["item"])
        position = str(entry.get("position") or "")
        for key in layout_keys:
            if key in position and key not in layout:
                layout[key] = item

    camera_size = _first_match(framing, _CAMERA_SIZE) or "wide"
    camera_angle = _first_match(framing, _CAMERA_ANGLE) or "eye level"
    inferred: list[str] = []
    if not _first_match(framing, _CAMERA_SIZE) and not _first_match(framing, _CAMERA_ANGLE):
        inferred.append("camera")
    if has_platform and not any(cue in window for cue in ("台阶", "阶梯", "踏步", "拾级")):
        # 正文写「用木板垫高的台子」但没写怎么上去：出图必须给台阶，否则红毯会悬空。
        inferred.append("platform_steps")

    space_type = _first_match(window, _SPACE_TYPES)
    missing: list[str] = []
    if not space_type:
        missing.append("space_type")
    if not architecture:
        missing.append("architecture")
    if not fixed_props:
        missing.append("fixed_props")
    if not (lighting_sources or light_quality or light_direction or time_of_day):
        missing.append("lighting")
    if not palette:
        missing.append("palette")
    if "camera" in inferred:
        missing.append("camera")
    if not (lighting_sources or light_quality or time_of_day or light_direction):
        # 正文完全没写光：不编造具体光源，但出图必须有可用照明，标成 inferred。
        inferred.append("lighting")

    do_not_change = list(dict.fromkeys([
        str(entry["item"]) for entry in all_props
    ] + landmarks))
    return {
        "scene_key": scene_key,
        "locale": locale.strip(),
        "window": window[:1200],
        "extractor": "rules",
        "space_type": space_type,
        "architecture": architecture,
        "fixed_props": fixed_props,
        "layout": layout,
        "relations": [],
        "lighting": {
            "sources": lighting_sources,
            "direction": light_direction,
            "quality": light_quality,
            "time": time_of_day,
            "weather": weather,
        },
        "palette": palette,
        "camera": {
            "shot_size": camera_size,
            "height": "eye level",
            "angle": camera_angle,
            "depth": "deep",
        },
        "landmarks": landmarks,
        "do_not_change": do_not_change,
        "missing": missing,
        "inferred": inferred,
    }


# 家具补足可见细节：只写 bed/nightstand 时，广角图容易把床省掉或画成空房。
_ITEM_DETAILS: dict[str, str] = {
    "bed": "bed with pillows and blanket",
    "nightstand": "nightstand beside the bed",
    "curtain": "window curtains",
    "wooden platform": "raised wooden platform",
    "red carpet": "red carpet",
    "white drapery": "white drapery",
    "ladder": "wooden ladder",
    "beam": "wooden ceiling beams",
    "door frame": "door frame",
    "wooden door": "wooden door",
    "side door": "side door",
    "table": "table",
    "mirror": "mirror",
    "candles": "candles",
}


_OUTDOOR_SPACE_WORDS = (
    "courtyard", "garden", "street", "alley", "dock", "station", "yard", "field",
    "forest", "beach", "shore", "square", "plaza", "bridge", "outdoor",
)

# 状态词的可视化补写：只写 half-dismantled/half-withered 时，模型仍会画完整门/茂盛树。
_CONDITION_DETAILS: tuple[tuple[str, str], ...] = (
    ("half-dismantled",
     "the half-dismantled door leaf is broken and half missing: jagged planks, "
     "torn hinges, a large open gap"),
    ("half-withered",
     "one side of the tree is bare and dead, dry cracked bark, sparse withered leaves"),
    ("broken", "shattered with jagged broken edges"),
    ("worn", "faded surface, scratched and worn edges"),
    ("ajar", "the door is slightly ajar"),
    ("open", "the door is wide open"),
    ("closed", "the door is closed"),
    ("remaining", "only the remaining frame is left"),
)


def build_scene_prompt(card: dict, *, style: str = "") -> str:
    """把 scene_card 拼成**空场景**文生图提示词（不写人物/动作）。"""
    space_type = str(card.get("space_type") or "").strip()
    is_outdoor = any(word in space_type.lower() for word in _OUTDOOR_SPACE_WORDS)
    # 只禁人物，不禁家具：「empty scene」会被模型理解成空房间（真机实锤：卧室只剩床头柜）。
    parts: list[str] = [
        "unpopulated scene" if is_outdoor else "unpopulated interior",
        "no people",
        "no characters",
    ]
    if space_type:
        parts.append(space_type)
    inventory: list[str] = []
    inventory_items: set[str] = set()
    conditions: list[str] = []
    for entry in [*(card.get("architecture") or []), *(card.get("fixed_props") or [])]:
        item = str(entry.get("item") or "").strip()
        position = str(entry.get("position") or "").strip()
        condition = str(entry.get("condition") or "").strip()
        if not item:
            continue
        inventory_items.add(item)
        if condition:
            conditions.append(condition)
        label = _ITEM_DETAILS.get(item, item)
        if condition and "half-dismantled" in condition.lower() and "door" in item.lower():
            # 「wooden door」是强概念，模型会补成完好门；换成「只剩半扇门的破门洞」。
            label = "broken doorway with half a wooden door leaf"
        if condition:
            label = f"{label} ({condition})"
        render_position = position
        if (not is_outdoor and position == "entrance"
                and "door" in item.lower() and "side door" not in item.lower()):
            # 室内全景里「entrance」会被模型理解成前景门框；把普通门挪到墙上。
            render_position = "on the room wall, not in the foreground"
        if not render_position or render_position == "unspecified":
            inventory.append(label)
        elif render_position.startswith(
            ("from ", "leading to ", "on ", "in ", "near ", "behind ", "inside "),
        ):
            inventory.append(f"{label} {render_position}")
        else:
            inventory.append(f"{label} at {render_position}")
    if inventory:
        # 清单前置：广角提示词太长时，先出现家具名才不会被稀释掉。
        parts.append("room contents: " + ", ".join(inventory))
        if card.get("fixed_props"):
            parts.append("fully furnished, every listed item visible")
        else:
            parts.append("every listed architectural element visible")
    # 稀疏正文里模型补的通用结构（混凝土地面/钢梁/池沿…）要紧跟清单，否则会丢细节。
    inferred_details = [
        str(item).strip() for item in (card.get("inferred") or [])
        if str(item).strip()
        and str(item).strip() not in {"camera", "lighting", "platform_steps"}
        and "light" not in str(item).lower()
        and "daylight" not in str(item).lower()
    ]
    if inferred_details:
        parts.append("inferred environment details: " + ", ".join(inferred_details))
    # 排列信息：物件之间的空间关系（红毯从侧门到台前台阶、梯子靠在横梁下…）。
    for relation in card.get("relations") or []:
        if isinstance(relation, dict):
            text = str(relation.get("text") or "").strip()
        else:
            text = str(relation or "").strip()
        if text:
            parts.append(text)
    if inventory:
        condition_details: list[str] = []
        for condition in conditions:
            low = condition.lower()
            for key, detail in _CONDITION_DETAILS:
                if key in low and detail not in condition_details:
                    condition_details.append(detail)
        parts.extend(condition_details)
        low_items = {item.lower() for item in inventory_items}
        # 词根识别：LLM 会写 raised wooden platform / stone platform，不能只认精确词。
        has_platform = any("platform" in item or item == "stage" for item in low_items)
        has_door = any("door" in item or item in {"doorway", "archway"} for item in low_items)
        has_carpet = any("carpet" in item for item in low_items)
        has_side_door = any("side door" in item for item in low_items)
        if has_platform and has_door:
            # 真机实锤：只说「门在入口、台在中央」时，模型把门画到台子正后方，
            # 看起来像门长在台上；必须显式写「台独立于墙、门嵌在墙里」。
            parts.append("central platform is freestanding with clear floor around it")
            parts.append(
                "no door stands on the platform, doors are set into the surrounding walls"
            )
        if has_platform and has_carpet:
            parts.append("wooden steps at the middle of the platform front edge")
            parts.append("red carpet leads up those steps onto the platform")
        if has_platform and has_carpet and has_side_door:
            # 红毯必须是一条直线，且起点要看得见侧门：给确定性舞台调度（前景左侧门），
            # 否则模型会把地毯画成两段/绕台一圈，或让地毯从画面外凭空出现。
            parts.append("one single continuous straight red carpet")
            parts.append("side door in the foreground left wall, open and clearly visible")
            parts.append(
                "red carpet runs in a straight diagonal line from that side door "
                "to the middle of the platform front edge"
            )
            parts.append("red carpet does not split, branch, bend, or run around the platform")
            parts.append("the other door is closed and has no carpet")
    # 场景图是「整场全景」而不是从门口看进去：16:9、无遮挡机位、整间/整院可见。
    if is_outdoor:
        parts.extend([
            "wide-angle exterior panorama",
            "full exterior view from an unobstructed corner of the space",
            "entire scene layout visible",
            "ground and skyline included",
            "complete scene inventory",
        ])
    else:
        parts.extend([
            "wide-angle interior panorama",
            "full room view from a corner inside the room",
            "camera stands inside the room, no doorway frame in the foreground",
            "any door is on the side or back wall, not in the foreground",
            "roomy composition with visible floor area around the furniture",
            "entire room layout visible",
            "unobstructed view, doorway at the room edge",
            "floor and ceiling included",
            "complete room inventory",
        ])
    layout = card.get("layout") or {}
    layout_labels = {
        "entrance": "entrance area", "center": "central area", "left": "left side",
        "right": "right side", "back": "back area", "foreground": "foreground",
        "midground": "midground", "background": "background", "upper": "upper area",
        "lower": "lower area",
    }
    for key, label in layout_labels.items():
        value = str(layout.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    lighting = card.get("lighting") or {}
    has_light = any(
        lighting.get(key) for key in ("sources", "direction", "quality", "time")
    )
    for key in ("sources", "quality", "time", "weather"):
        light_value = lighting.get(key)
        if isinstance(light_value, list):
            parts.extend(str(item) for item in light_value if item)
        elif light_value:
            parts.append(str(light_value))
    direction = str(lighting.get("direction") or "")
    if direction:
        parts.append(f"light {direction}" if direction.startswith("from ") else direction)
    if not has_light:
        # 场景卡已标 inferred=lighting：给一个中性照明，避免出图死黑/纯平光。
        parts.append("soft ambient lighting")
    parts.extend(str(item) for item in (card.get("palette") or []) if item)
    camera = card.get("camera") or {}
    # 环境图是全场景共用底图：统一给广角全景，不沿用某一镜的中近景。
    parts.append("ultra-wide panoramic establishing shot, 16:9 landscape composition")
    for key in ("angle", "height", "depth"):
        if camera.get(key):
            parts.append(str(camera[key]))
    if style:
        parts.append(style)
    parts.append("highly detailed environment, coherent perspective, cinematic composition")
    return ", ".join(dict.fromkeys(part for part in parts if part))
