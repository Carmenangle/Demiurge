"""角色当前状态卡（2026-11-12）。

把「这一轮这个角色长什么样」编译成一张**可出图**的状态卡：身份来自定妆真源，
当前服装 / 当前状态来自正文与 `<status>`，场景光来自 `scene_card`。

三条边界：

· **身份只读既有真源，零 LLM、不猜不编**。外貌仍走 `character_portrait.collect_appearance`
  （世界书条目 / 角色卡 → 通用表），底图仍用 `characterLoras[name].baseImage`。
  身份是「跨楼层稳定」的那一层，状态卡不重写它，只声明 `do_not_change`。
· **当前状态是原文切片，不是摘要**。`state_text` / `evidence` 一律取正文原句
  （角色名所在句 + 相邻句，且相邻句不得提到别的角色名，防串人）。不做「她看起来有点累」
  这类推断——推断会变成模型编造的依据。
· **服装先看 `[在场]` 括号，再看正文分句**。括号走 `shot_plan.parse_wardrobe_anchors`
  （既有真源）；正文扫描只取**含衣物线索词的短分句**，不整句照搬，避免把「抬手撩头发」
  当成服装写进提示词。

下游用法：`build_state_scene()` 产出的 scene dict 直接喂 `image_prompt_profiles.generate_result`
（与一键定妆同一条编译器，不另立口径），再由 `character_reference` 用图生图模板锁定身份出图。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Sequence

from app.services import character_portrait, prompt_clean, shot_plan

# 定妆底图缺失时的兜底：只写「这是什么图」，不补任何身份细节（身份必须来自真源）。
_FALLBACK_FRAMING = (
    "角色当前状态参考图：{name} 一人出镜，全身入镜，正面朝向镜头。"
    "保持与已有定妆底图一致的脸、发型与体型；服装、伤势与随身物件按本轮剧情。"
    "背景简洁，不叙事，没有其他人物。"
)

# 取景前缀：**每张参考图都要完整全身 + 正面**。2026-11-12 真机两版实锤：
# ① 首图状态与身份都对，但图生图 denoise 0.8 把人物放大后脚被裁出画框；
# ② 补了全身约束后脚回来了，人物却转成背身——参考图要当后续所有出图的身份锚点，
#    看不到脸等于这张图废掉。
# 取景合同与一键定妆（`character_portrait._PORTRAIT_FRAMING`，第 95 刀「正面看向镜头」）同口径。
_FRAMING_PREFIX = (
    "全身竖构图：从头顶到脚底（含鞋子）完整入镜，头顶与脚下各留少量边距，不要裁掉腿和脚；"
    "正面朝向镜头，抬头，眼睛睁开并看向镜头，脸与五官清晰可见，"
    "不要背身、不要侧背、不要低头闭眼。"
)

# 正文清理：`<think>` 是内心推理、`<status>`/`<roll>` 是面板与掷骰数据，
# 都不该进画面描述。`<roll>` 尤其要整块删——凌霄仙母录的片段里它带
# `[TYPE]/[SKILL] 75[基础45（…）]` 这种数值串，留在状态文本里会被画成画面元素。
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_STATUS_RE = re.compile(r"<status>.*?</status>", re.S)
_ROLL_RE = re.compile(r"<roll>.*?</roll>", re.S)
_CONTENT_TAG_RE = re.compile(r"</?content>")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；」』”])|\n+")
_CLAUSE_SPLIT_RE = re.compile(r"[，,、；;。！？]")
# 对白内容：中文「」、直角引号『』、弯引号“”。剥掉它们再判归属——
# 台词里的名字（「腓特烈大人的中指上」）不是该角色的画面，混进来就会画错人。
_DIALOGUE_RE = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"")
_SHE_RE = re.compile(r"她们?")
_HE_RE = re.compile(r"他们?")

# 正文里的「当前状态」线索：伤势 / 体液 / 凌乱 / 配饰 / 姿态。**不含**心理与台词。
# 词表只用于「挑出哪一句值得进画面」，不用于改写原文——命中后仍抄原句。
_STATE_CUES = (
    "血", "伤", "淤", "红印", "汗", "泪", "吻痕", "牙印", "指印", "湿",
    "凌乱", "散乱", "乱发", "半裸", "赤裸", "光着", "衣衫不整", "褪到", "滑落",
    "靠着", "靠墙", "倚", "坐在", "跪", "趴", "躺", "站着", "背对", "低头",
    "戴", "攥", "抓", "握", "捏", "抱", "扶", "按住", "抵",
    "戒", "项圈", "锁", "绳", "镣", "手铐", "单子", "抄本", "文件",
    "尾巴", "耳", "翅", "角", "发饰", "徽章", "肩章",
)


def _clean_text(text: str) -> str:
    """去掉推理块、状态面板与掷骰块，并还原破甲标记，只留可读正文。

    破甲标记（`@嘴@(唇)@`）由 `prompt_clean.restore_jailbreak` 还原——清洗规则只有一个
    属主，这里不另写一套；把它直接喂给编译器会得到一串 `@…@(…)@` 噪声。
    """
    value = _THINK_RE.sub("", str(text or ""))
    value = _STATUS_RE.sub("", value)
    value = _ROLL_RE.sub("", value)
    value = _CONTENT_TAG_RE.sub("", value)
    return prompt_clean.restore_jailbreak(value).strip()


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]


def _resolve_gender(gender: str, state_text: str) -> tuple[str, str]:
    """表里的性别与正文代词冲突时**不采用表值**，返回 `(采用的性别, 冲突说明)`。

    2026-11-12 真机：`堕落魔戒` 通用表把腓特烈记成「男」，正文通篇用「她」，底图也是女性。
    按「显式设定优先 + 冲突只报告」的红线：冲突时清空性别（宁缺勿猜），并把冲突写进
    `missing`，让审阅看见——直接把「男」喂给编译器会出一张性转图。
    """
    value = str(gender or "").strip()
    if value not in {"男", "女"} or not state_text:
        return value, ""
    she = bool(_SHE_RE.search(state_text))
    he = bool(_HE_RE.search(state_text))
    if value == "男" and she and not he:
        return "", "gender_conflict:表=男/正文用她"
    if value == "女" and he and not she:
        return "", "gender_conflict:表=女/正文用他"
    return value, ""


def state_window(
    text: str, name: str, *, others: Sequence[str] = (), max_chars: int = 900,
) -> tuple[str, list[str]]:
    """取该角色在本轮正文里的**原句**窗口（返回 `(拼接文本, 证据句列表)`）。

    归属规则（2026-11-12 真机两轮打磨）：

    · **含角色名的句子**直接收；但「我/他」开头的句子除外——本项目是男主视角，
      「他往后侧了侧头，看埃吉尔」的动作属于男主，不属于被点名的女角色。
    · **「她」开头的句子**只有在**本段落没有别的在场角色**时才收。真机实锤：`堕落魔戒`
      第 19 轮里「她手里捏着那份抄本」紧跟在「奥古斯特从梯口转上来」之后，实际是奥古斯特的
      动作，算到埃吉尔头上就会画成她拿抄本靠栏杆。单人段落（凌霄仙母录的实验片段通篇只有
      妲千沁一人）则必须收「她」句，否则整段状态全丢——**但前提是该片段里点过这个角色的名字**；
      名字一次都没出现时，段落里的「她」不是她（见下面 `target_named_in_fragment`）。
    · 对白一律剥掉再判归属：`「一枚在腓特烈大人的中指上」`是别人台词，留着会让腓特烈
      凭一句话拿到「状态」。
    """
    cleaned = _DIALOGUE_RE.sub("", _clean_text(text))
    target = str(name or "").strip()
    if not cleaned or not target:
        return "", []
    blocked = [item for item in (str(o).strip() for o in others) if item and item != target]
    # 片段里**一次都没点过这个角色的名字** ⇒ 所有「她」句都不能归给她。
    # 2026-11-12 真机（堕落魔戒 SAVE02 turn 19）：腓特烈只在别人台词里被提到
    # （台词已剥），正文里的「她的手指凉」「她那条尾巴」其实是埃吉尔；旧规则把
    # 「段落里没别的角色名」当成单人段落，于是整段串进腓特烈卡，还因为状态非空
    # 挡住了跨轮回退 ⇒ 真机画出了埃吉尔的尾巴 + 奥古斯特的抄本。
    # 凌霄仙母录那种单人片段里妲千沁会被点名，这条不影响它收「她」句。
    target_named_in_fragment = target in cleaned
    evidence: list[str] = []
    used = 0
    # 段落级代词归属：只有「最近被点名的角色」才是本段「她」的主体。
    # 跨段落继承是为了单人片段（上段点名、下段全用「她」）；一旦别的角色被点名，
    # 后面的「她」立刻改归那个人，不再算到 target 头上。
    last_named = ""
    for paragraph in re.split(r"\n\s*\n|\n", cleaned):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        named_here = [item for item in [target, *blocked] if item in paragraph]
        if named_here:
            last_named = max(named_here, key=paragraph.rfind)
        crowded = any(other in paragraph for other in blocked)
        for sentence in _split_sentences(paragraph):
            prose = sentence.strip(" ，,、；;")
            if not prose:
                continue
            named = target in prose
            if prose.startswith(("我", "我们", "两人", "二人", "双方")):
                continue
            if prose.startswith("他"):
                continue
            if prose.startswith("她"):
                # ① 句子里点了 target 的名字却以「她」开头 ⇒ 主体是那个「她」，不是 target
                #    （「她的眼睛从埃吉尔脸上移到他脸上」是别人的视线）。
                # ② 段落里还有别的在场角色 ⇒ 代词归属不可判。两条都不收。
                if named or crowded or not target_named_in_fragment:
                    continue
                if last_named != target:
                    continue
            elif not named:
                continue
            if used + len(prose) > max_chars and evidence:
                break
            if prose in evidence:
                continue
            evidence.append(prose)
            used += len(prose)
    # 证据句之间用换行分隔：直接 `"".join` 会把「…她收了」与下一句「妲千沁把外衫…」
    # 粘成「收了妲千沁把外衫」，编译器会把它当成一个人名。
    return "\n".join(evidence), evidence


# 正文扫描用的服装词表 ＝ `shot_plan.CLOTHING_CUES`（括号锚点的真源）＋ 古风/仙侠补充。
# 为什么加：`凌霄仙母录` 写「外衫褪到石案上，只着月白单衣」，两个词都不在原表里，
# 于是 wardrobe 被判缺失、只能沿用底图。补充词**只收双字以上**（单字「裙/靴」会把
# 「靴底在窄道上连声响」这种动作句误判成服装），且不回写 `shot_plan`——那边是
# `[在场]` 括号的判据，放宽会误把动作括号当服装。
_TEXT_CLOTHING_CUES = tuple(shot_plan.CLOTHING_CUES) + (
    "外衫", "单衣", "里衣", "道袍", "法衣", "襦裙", "罗裙", "亵衣", "肚兜", "披帛",
)


def wardrobe_from_text(text: str, *, max_chars: int = 200) -> str:
    """从正文分句里抽「当前穿着」——只取**最后一个**含衣物线索词的短分句。

    为什么取最后一个：同一片段里服装会变（`凌霄仙母录` 里妲千沁「把外衫褪到石案上」
    之后又「把外衫重新披上」），把两句都写进提示词会让模型收到互相矛盾的穿着。
    当前状态＝最后一次出现的穿着。对白先剥掉：`「你该去量礼服」` 是别人在说礼服。
    """
    latest = ""
    for sentence in _split_sentences(_DIALOGUE_RE.sub("", _clean_text(text))):
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            part = clause.strip()
            if not part or len(part) > 40:
                continue
            if any(cue in part for cue in _TEXT_CLOTHING_CUES):
                latest = part
    return latest[:max_chars].strip("，, ")


def state_clues(text: str, *, max_chars: int = 400) -> str:
    """正文里与「当前状态」相关的短分句（伤势/体液/姿态/随身物件），供 missing 判定与审阅。"""
    hits: list[str] = []
    for sentence in _split_sentences(_DIALOGUE_RE.sub("", _clean_text(text))):
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            part = clause.strip()
            if not part or len(part) > 40:
                continue
            if any(cue in part for cue in _STATE_CUES) and part not in hits:
                hits.append(part)
    return "，".join(hits)[:max_chars].strip("，, ")


def _lighting_text(lighting: Any) -> str:
    if isinstance(lighting, str):
        return lighting.strip()
    if not isinstance(lighting, dict):
        return ""
    parts = [
        str(lighting.get(key) or "").strip()
        for key in ("time", "weather", "sources", "direction", "quality")
    ]
    if isinstance(lighting.get("sources"), (list, tuple)):
        parts[2] = "、".join(str(item).strip() for item in lighting["sources"] if str(item).strip())
    return "，".join(part for part in parts if part)


def extract_state_card(
    *,
    base: str,
    repo_id: str,
    name: str,
    text: str,
    present: str = "",
    scene_key: str = "",
    base_image: str = "",
    locale: str = "",
    lighting: Any = None,
    others: Sequence[str] = (),
    appearance_source: str = character_portrait.APPEARANCE_SOURCE_WORLDBOOK,
    max_state_chars: int = 900,
) -> dict[str, Any]:
    """规则层状态卡（零 LLM）。缺事实就进 `missing`，不编造。"""
    target = str(name or "").strip()
    if not target:
        raise ValueError("角色名为空，无法生成当前状态卡")

    facts = character_portrait.collect_appearance(
        base, repo_id, target, appearance_source=appearance_source,
    )
    appearance = str(facts.get("appearance") or "").strip()
    gender = str(facts.get("gender") or "").strip()

    wardrobe_present = ""
    try:
        wardrobe_present = shot_plan.parse_wardrobe_anchors(str(present or "")).get(target, "")
    except Exception:  # noqa: BLE001 [EXC-保留] 括号解析失败只少一条服装锚点
        wardrobe_present = ""

    state_text, evidence = state_window(
        text, target, others=others, max_chars=max_state_chars,
    )
    gender, gender_conflict = _resolve_gender(gender, state_text)
    # 服装只从**该角色的状态窗口**抽（不再回落到全文）：回落会捡到别人台词里的衣物。
    wardrobe = wardrobe_present or wardrobe_from_text(state_text)
    light = _lighting_text(lighting)

    missing: list[str] = []
    inferred: list[str] = []
    if not appearance:
        missing.append("appearance")
    if not wardrobe:
        missing.append("wardrobe")
    if not state_text:
        missing.append("current_state")
    if not light:
        missing.append("lighting")
    if not base_image:
        missing.append("base_image")
    if gender_conflict:
        missing.append(gender_conflict)
    if state_text and not wardrobe:
        # 正文没写衣服 ⇒ 底图沿用定妆服，这是**推断**，必须让审阅看得见。
        inferred.append("wardrobe_inherits_base_image")

    return {
        "name": target,
        "scene_key": str(scene_key or "").strip(),
        "extractor": "rules",
        "identity": {
            "appearance": appearance,
            "gender": gender,
            "base_image": str(base_image or "").strip(),
            "appearance_source": str(facts.get("source") or "none"),
        },
        "wardrobe": wardrobe,
        "state_text": state_text,
        "state_clues": state_clues(state_text),
        "evidence": evidence,
        "scene_context": {"locale": str(locale or "").strip(), "lighting": light},
        "do_not_change": ["face", "hair", "body type"],
        "missing": missing,
        "inferred": inferred,
    }


def state_hash(card: dict[str, Any]) -> str:
    """缓存键：只对影响画面的字段取哈希（身份/服装/状态/场景光）。"""
    payload = {
        "name": card.get("name"),
        "appearance": (card.get("identity") or {}).get("appearance"),
        "base_image": (card.get("identity") or {}).get("base_image"),
        "wardrobe": card.get("wardrobe"),
        "state_text": card.get("state_text"),
        "scene_context": card.get("scene_context"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_state_scene(
    card: dict[str, Any], *, aspect_ratio: str = character_portrait.DEFAULT_ASPECT_RATIO,
    rating: str = "nsfw",
) -> dict[str, Any]:
    """状态卡 → 编译器 scene dict（与 `character_portrait.portrait_scene` 同结构）。

    与定妆的唯一差别：`narrative` 是本轮**原文**而不是固定取景句，`wardrobe` 是当前服装，
    `locale` 是当前地点与光。`viewpoint`/`narrator`/主体名单仍按定妆口径写死——
    这两处留旋钮就会出「没有人的空镜」（`character_portrait` 模块头已记录该缺陷）。
    """
    target = str(card.get("name") or "").strip()
    if not target:
        raise ValueError("状态卡缺少角色名")
    identity = card.get("identity") or {}
    context = card.get("scene_context") or {}
    narrative = str(card.get("state_text") or "").strip()
    if not narrative:
        narrative = _FALLBACK_FRAMING.format(name=target)
    else:
        narrative = f"{_FRAMING_PREFIX}{narrative}"
    locale = str(context.get("locale") or "").strip()
    lighting = str(context.get("lighting") or "").strip()
    if lighting:
        locale = f"{locale}；光线：{lighting}" if locale else f"光线：{lighting}"

    scene: dict[str, Any] = {
        "narrative": narrative,
        "appearance": str(identity.get("appearance") or "").strip(),
        "wardrobe": str(card.get("wardrobe") or "").strip(),
        "locale": locale,
        "draft_prompt": "",
        "actors": [target],
        "frame_subjects": [target],
        "viewpoint": "third_person",
        "narrator": "",
        "rating": str(rating or "nsfw"),
        "aspect_ratio": str(aspect_ratio or character_portrait.DEFAULT_ASPECT_RATIO),
    }
    gender = str(identity.get("gender") or "").strip()
    if gender:
        scene["subject_genders"] = {target: gender}
    return scene
