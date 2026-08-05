"""场景分类：把本轮语义归到一个场景标签，驱动条件选链(P1)与 NSFW 高潮出图(P3)。

纯逻辑模块（无 I/O、无 LLM）：规整模型场景字段；角色卡纯文本直达 Roleplay 时，
用保守关键词判断场景，避免为路由重复提交整段上下文。
"""
from __future__ import annotations

# 合法场景标签（与前端思维链编辑器下拉、select_chains 的 scene 条件一一对应）
SCENES = ("dialogue", "action", "emotion", "conflict", "nsfw", "climax")
DEFAULT_SCENE = "dialogue"

# 中文/别名 → 标准标签（模型可能回中文或近义词，统一归一）
_ALIASES = {
    "对话": "dialogue", "交谈": "dialogue", "日常": "dialogue",
    "动作": "action", "战斗": "action", "打斗": "action",
    "情感": "emotion", "情绪": "emotion", "抒情": "emotion",
    "冲突": "conflict", "对峙": "conflict", "争执": "conflict",
    "色情": "nsfw", "情色": "nsfw", "性": "nsfw", "露骨": "nsfw",
    "高潮": "climax", "转折": "climax", "关键": "climax",
}

_INFER_KEYWORDS = (
    ("climax", ("高潮", "决战", "最终战", "关键转折", "生死关头")),
    ("nsfw", (
        "情色", "成人场景", "裸露", "性行为", "肉戏", "床戏", "做爱", "性交",
        "性爱", "交媾", "饥渴难耐", "淫液", "精液", "插入", "抽插", "射精",
    )),
    ("conflict", ("争吵", "争执", "冲突", "对峙", "威胁", "拒绝")),
    ("action", ("战斗", "打斗", "追逐", "拔剑", "开枪", "冲刺", "逃跑")),
    ("emotion", ("哭泣", "悲伤", "愤怒", "恐惧", "感动", "告白")),
)


def normalize_scene(value: str) -> str:
    """把模型给的场景字段规整成合法标签。识别不了 → 空串（调用方决定是否兜底 default）。"""
    v = (value or "").strip().lower().strip("`'\".,:;，。、")
    if not v:
        return ""
    if v in SCENES:
        return v
    # 中文别名/含子串匹配（模型可能回「这是nsfw场景」这类）
    for alias, tag in _ALIASES.items():
        if alias in v:
            return tag
    for tag in SCENES:
        if tag in v:
            return tag
    return ""


def infer_scene(text: str) -> str:
    """角色卡直达路径的保守场景判断；无明确命中按普通对话处理。"""
    source = (text or "").lower()
    for scene, words in _INFER_KEYWORDS:
        if any(word in source for word in words):
            return scene
    return DEFAULT_SCENE
