"""出图提示词共享底座（模型无关）：文本基元/JSON 抢救/场景清洗与匿名化/动作·事件·视觉·
接触·器材事实闸/第一人称视角帧/字段台账。2026-10-24 审计中期项自单文件拆域；
函数体逐字节未改，仅模块边界与 import 接线新增。"""
from __future__ import annotations
import json
import re
from collections.abc import Mapping
from app.services import image_prompt_extract, prompt_clean


def _diag_list(diag: dict[str, object], key: str) -> list[object]:
    """取诊断字典里的列表字段；缺失/类型不符回退空列表（mypy 可收窄）。"""
    value = diag.get(key)
    return value if isinstance(value, list) else []


def _strip_wrapping(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


_STRAY_PAREN_RE = re.compile(r"\(([^()]{1,48}?)\)")


# 悬空事实片段（2026-09-20 第 21 刀）：`looking at` 这类**只带动词+介词、没有宾语**的片段，
# 补进散文会产出 `The two adult characters are depicted with looking at.` 这种病句。
# 只杀这一形状，**不按词数过滤**——`bedchamber`（1 词地点）是合法事实，必须补。
_DANGLING_FACT_RE = re.compile(
    r"^\s*\w+ing\s+(?:at|to|into|on|onto|toward|towards|for|from|with|over|under)\s*$",
    re.I,
)


def _is_dangling_fact(detail: str) -> bool:
    return bool(_DANGLING_FACT_RE.match(detail or ""))


def _unwrap_stray_parentheses(text: str) -> str:
    """拆掉**碎片式圆括号**（2026-09-20 用户实锤）。

    L1·niji 真机成稿：`a (torn) white shirt … exposing a strip of bare lower back (above her)
    (soaked-through) (thin) (panties) pulled taut` —— 渲染模型把补语/并列词塞进括号，
    读起来是**碎裂的短语**而不是画面描述。用户口径：「出现 () 包住单词的情况要修复」。

    只拆**短碎片**（≤48 字、无冒号）⇒ 不碰权重语法 `(word:1.2)` 这类带冒号的形式。
    """
    def _swap(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if not inner or ":" in inner:
            return match.group(0)
        return inner

    return _STRAY_PAREN_RE.sub(_swap, text or "")


def _escape_bare_control_chars(payload: str) -> str:
    """把 **JSON 字符串字面量内部**的裸控制字符转义（`_json_object` 的补救通道，第 31 刀）。

    只做这一件事：`"` 进出字符串（认 `\\` 转义），字符串内的裸 `\\n` / `\\r` / `\\t` 换成对应的
    转义序列；字符串之外原样透传（那里的换行与缩进本来就是合法 JSON 空白）。
    ⚠ 刻意**不做**其他容错（尾逗号、单引号、中文引号……）：那些形状一旦放手写容错，等于放宽
    结构约束，会把「模型没按协议写」这类真问题掩盖掉（与本项目 fail-closed 的口径相反）。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for char in payload:
        if in_string:
            if escaped:
                out.append(char)
                escaped = False
                continue
            if char == "\\":
                out.append(char)
                escaped = True
                continue
            if char == '"':
                out.append(char)
                in_string = False
                continue
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(char, char))
            continue
        if char == '"':
            in_string = True
        out.append(char)
    return "".join(out)


def _json_object(raw: str) -> dict[str, object]:
    text = _strip_wrapping(raw)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    payload = match.group(0)
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        # 2026-09-21 第 31 刀（anima 兜底波动归因的副产物，零 LLM 复现）：真机 `L2·单人锚点[63]`
        # 首轮＋repair 两轮的 raw 都是**合法形状的 JSON**，唯一毛病是 `content` 字符串里带了
        # **未转义的真实换行**（`…refuses to heed.\n"`）⇒ `json.loads` 直接抛 ⇒ `_json_object`
        # 返回 {} ⇒ 掉进「非 JSON」分支（首行变成 `{`）⇒ 两行结构判据全红 ⇒ 白烧一次 repair
        # 再掉兜底。它不是采样波动，是**解析接缝**。
        # 定案：只对**字符串字面量内部的裸控制字符**做一次转义重试，不放宽任何结构约束
        # ⇒ 合法 JSON 走原路径逐字节不变，其他残破形状照样返回 {}（fail-closed 不变）。
        try:
            value = json.loads(_escape_bare_control_chars(payload))
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


# 画幅声明：`3:4` 与 `3:4 aspect ratio` 都归一成恰好一份 `N by M aspect ratio`。
_ASPECT_RATIO_RE = re.compile(
    r"\b(\d+)\s*:\s*(\d+)\b(\s*[Aa]spect\s+[Rr]atio)?",
)


def _normalize_aspect_ratio(text: str) -> str:
    """`3:4` / `3:4 aspect ratio` → `3 by 4 aspect ratio`，**恰好一份** `aspect ratio`。

    2026-09-20 真机实锤（L2/L3·anima 成稿）：原实现 `\\b(\\d+)\\s*:\\s*(\\d+)\\b` →
    `\\1 by \\2 aspect ratio` 不认模型**已经写过** `aspect ratio` 的情况 ⇒
    `3:4 aspect ratio` 被追加成 `3 by 4 aspect ratio aspect ratio`（画幅声明重复一份）。
    改为**连后缀一起吞掉再补一份**，两种输入都得同一结果。
    """
    return _ASPECT_RATIO_RE.sub(
        lambda m: f"{m.group(1)} by {m.group(2)}{m.group(3) or ' aspect ratio'}",
        text,
    )


# ── 首行 tag 归一（2026-09-21 第 31 刀，用户实锤：「重复词条会导致某个 tag 的权重过高」）─────
# 现场（L3·NSFW多人 · anima_tags 真机首行）：`shy … shy`、`wet pussy … wet pussy`，
# 且 `masterpiece, best quality, high quality, absurdres` 与**程序前置的质量行整段重复**。
# 根因：`_ANIMA_SYSTEM` 虽写明「质量行由程序添加」，但拼装处（`_normalize` 的 anima 分支）把
# 质量行直接前置，全链**没有任何同名去重**；`_drop_generic_anima_tags` 只按黑名单剔通用艺术词。
# 判据：**tag 粒度全等**（大小写与内部空白归一后完全相同）才折叠，保留**首次**出现的拼写与
# 位置 —— 不改任何非重复 tag，**不做同义合并**（同义合并＝改画面事实，越权）。
def _anima_dedupe_tags(tag_line: str) -> str:
    kept: list[str] = []
    seen: set[str] = set()
    for tag in (tag_line or "").split(","):
        cleaned = tag.strip()
        if not cleaned:
            continue
        key = " ".join(cleaned.split()).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(cleaned)
    return ", ".join(kept)


def _resolve_subject_counts(tag_line: str) -> str:
    """族内归一：**逐族**只留最高位计数词；有任一族最高位 ≥2 时统一剔除 `solo`（第 46 刀）。"""
    tags = [tag.strip() for tag in (tag_line or "").split(",")]
    family_max: dict[str, int] = {}
    for tag in tags:
        match = _SUBJECT_FAMILY_RE.match(tag)
        if not match:
            continue
        family = "boys" if match.group(2).lower().startswith("boy") else "girls"
        family_max[family] = max(family_max.get(family, 0), int(match.group(1)))
    if not family_max or max(family_max.values()) < 2:
        return _anima_dedupe_tags(tag_line)
    kept: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            continue
        match = _SUBJECT_FAMILY_RE.match(cleaned)
        if match:
            family = "boys" if match.group(2).lower().startswith("boy") else "girls"
            if int(match.group(1)) != family_max[family]:
                continue
        if cleaned.lower() == "solo":
            continue
        kept.append(cleaned)
    return _anima_dedupe_tags(", ".join(kept))


# 跨族人数词（第 40 刀 · 2026-09-21 用户实报）：girls 族**已把画面主体名额占满**时，
# 多出来的 boys 族计数词是**孤儿**——没有任何画面主体可以对应它。
#
# 真机现场（`backend/data/logs/agent-trace.jsonl` · `illustration.submitted` ·
# 2026-09-21T21:48:20 · slot 43）：`illustration.request` 明写 `actors: ["星织恋", "谢若冰"]`、
# `actor_count: 2`（**叙事者根本不在 `actors` 里**），契约只要求 `2girls`；但交付首行是
# `…, anime coloring, 2girls, 1boy, male viewer pov, first-person view, no viewer face, …`
# —— `1boy` 与 `male viewer pov` 同现 ⇒ 模型把**不入画的叙事者**（男性）写成了第 3 个人。
# 用户口径：「**除非是第三人称，主角相关的 tag 才可能进入提示词中**」。
#
# ⚠ **不是禁词表**（技能 `demiurge-viewpoint-subject-scope` §2 红线：第一人称下画面里
#   完全可能真有别的男性，禁词闸会误杀）：
#   · `1girl, 1boy`（girls 族只占 1 个名额、主体数 2）⇒ **原样保留**；
#   · 纯男性帧 `2boys`（girls 族 0）⇒ **不动**；
#   · `2boys, 1girl`（主体数 2）⇒ girls 族未占满 ⇒ **不动**。
# ⚠ 40 刀当时**只能剔 boys**（整条计数设施按 girls 建），故写「girls 族永远不动」。
# ⚠ **第 46 刀（2026-09-22）对称化**：用户口径原话「**根据剧情判断在场的内容，不是锁死
#   女角色**」——女主人公＋2 男主角就是 `1girl, 2boys`，纯男帧写 `1girl` 同样是孤儿。
#   有了**逐主体性别**（`scene["subject_genders"]`）后，判据改成「**某族真值为 0 ⇒ 整族剔**」；
#   拿不到性别（老调用方 / 解析不全）⇒ **退回 40 刀口径**（只剔 boys），老测试逐格不变。
_SUBJECT_FAMILY_RE = re.compile(r"^(\d+)\s*(girls?|boys?)$", re.I)


def _is_boy_count(tag: str) -> bool:
    match = _SUBJECT_FAMILY_RE.match(tag.strip())
    return bool(match and match.group(2).lower().startswith("boy"))


def _drop_orphan_subject_counts(
    tag_line: str, subject_count: int, genders: list[str] | None = None,
) -> str:
    """跨族人数词孤儿闸：**没有对应画面主体的计数词族整族剔除**（第 40 刀／第 46 刀）。

    · 有逐主体性别（`genders` 与画面主体名单**同长同序**、且全部可解析）⇒ **对称**：
      某族真值为 0 而行内写了该族计数词 ⇒ 剔该族（两女帧 `2girls, 1boy` ⇒ 剔 `1boy`；
      纯男帧 `2boys, 1girl` ⇒ 剔 `1girl`）。
    · 无性别（老调用方 / 解析不全）⇒ **退回第 40 刀口径**：`girls 族计数 ≥ 画面主体数`
      且行内存在 boys 族 ⇒ 剔 boys 族（girls 族不动）—— 该支逐字节等价旧实现。

    ⚠ 零回归锁：`subject_count < 1` ⇒ **逐字节返回原串**；不触发 ⇒ 同样逐字节返回原串。
    ⚠ 只在 `_resolve_subject_counts` **之后**调用（那时整行已归一成 `", "` 连接的非空 tag 串，
    重连接才是字节安全的）。
    """
    if subject_count < 1:
        return tag_line
    tags = [tag.strip() for tag in (tag_line or "").split(",")]
    resolved = (
        genders if genders is not None and len(genders) == subject_count
        and all(str(gender).strip() for gender in genders) else None
    )
    if resolved is not None:
        girls_true = sum(1 for gender in resolved if "女" in str(gender))
        boys_true = sum(1 for gender in resolved if "男" in str(gender))
        drop_girls, drop_boys = girls_true == 0, boys_true == 0
    else:
        girls = 0
        for tag in tags:
            match = _SUBJECT_FAMILY_RE.match(tag)
            if match and not match.group(2).lower().startswith("boy"):
                girls += int(match.group(1))
        if girls < subject_count or not any(_is_boy_count(tag) for tag in tags):
            return tag_line
        drop_girls, drop_boys = False, True
    if not drop_girls and not drop_boys:
        return tag_line
    kept: list[str] = []
    for tag in tags:
        if not tag:
            continue
        match = _SUBJECT_FAMILY_RE.match(tag)
        if match:
            is_boy = match.group(2).lower().startswith("boy")
            if (is_boy and drop_boys) or (not is_boy and drop_girls):
                continue
        kept.append(tag)
    if len(kept) == len([tag for tag in tags if tag]):
        return tag_line
    return ", ".join(kept)


def _contract_subject_genders(scene: Mapping[str, object]) -> list[str]:
    """画面主体的**逐主体性别**，与 `_contract_subject_names(scene)` **同长同序**（第 46 刀）。

    数据源＝`scene["subject_genders"]`（`{角色名: "女"/"男"}`；由接线侧按**世界书条目正文优先、
    通用表「性别」列兜底**的零 LLM 解析后随 `scene_spec` 下传）。
    ⚠ 取不到的名 ⇒ `""`（**宁缺勿猜**：调用方据此退回中性措辞，不写具体性别族）。
    ⚠ 缺键 / 类型不符 ⇒ 全 `""`（老调用方零回归）。
    """
    names = _contract_subject_names(scene)
    table = scene.get("subject_genders")
    if not names or not isinstance(table, Mapping):
        return ["" for _ in names]
    return [str(table.get(name) or "").strip() for name in names]


def _subject_count_words(genders: list[str]) -> str:
    """按画面主体的**实际性别组成**给出人数词（第 46 刀，纯函数、零 LLM）。

    全可解析 ⇒ `2girls` / `2girls, 1boy` / `1girl, 2boys`（**girls 族在前**＝Danbooru 习惯，
    也是真机既有形态 `2girls, 1boy` 的顺序；计数为 0 的族不写）。
    任一体性别解析不出 ⇒ `""`（调用方**退回中性措辞**，不写具体性别族——用户定案「不写」）。
    """
    if not genders:
        return ""
    girls = 0
    boys = 0
    for gender in genders:
        text = str(gender).strip()
        if "女" in text:
            girls += 1
        elif "男" in text:
            boys += 1
        else:
            return ""
    parts: list[str] = []
    if girls:
        parts.append(f"{girls}girl" + ("" if girls == 1 else "s"))
    if boys:
        parts.append(f"{boys}boy" + ("" if boys == 1 else "s"))
    return ", ".join(parts)


# 第 41 刀（2026-09-21 用户口径）：多人契约的人数词**不再硬编码 `2girls`**。
# 用户口径：「第三人称就按剧情正常来就行，无需像第一人称一样删去剧情里出现的主人公的 tag」；
# 「主人公是男的 ⇒ `2girls, 1boy`；主人公是女的 ⇒ `3girls`」。
# 旧判据 `\b(?:2girls|two girls)\b` 把「画面主体**恒为 2** 且全女」写死 ⇒ 三人帧里模型
# **写对的 `3girls` 反被判红**（零 LLM 复现 `_tmp/i41_probe.py` A2／C2 格）⇒ 逼出修复轮，
# 极端情况掉通用兜底模板、把模型真实画面全丢。
# 新判据＝只给 **girls 族**一个上/下界：`2 ≤ girls ≤ 画面主体数`。
# ⚠ **boys 族刻意不进契约**（40 刀真机格 `2girls, 1boy` 于 2 主体帧，旧判据放行；
#   若把 boys 计入总数会判红 ⇒ **给已知真机形态新增判红面**）。
#   boys 族的归属是**归一化侧**的「剔孤儿」（40 刀），契约只负责判、不重复治。
# ⚠ 本刀**不需要**逐主体性别：只约束 girls 族区间，`2girls, 1boy` 与 `3girls` 都合法；
#   性别由模型按注入的 `【性别】X` 画面事实自己写（23 刀定案：不机械拼硬标签）。
# ⚠ 必须与 `_resolve_subject_counts` **同口径**（同族并存只认最高位）：否则
#   `1girl, 2girls` 会被数成 3 ⇒ 给 31 刀的真机残留**新增判红面**。
_COUNT_FAMILY_RE = re.compile(r"(\d+)\s*(girls?|boys?)\b", re.I)


def _subject_count_totals(prompt: str) -> tuple[int, int]:
    """数出提示词里 girls / boys 族的**有效**计数（第 41 刀，纯函数）。

    口径与 `_resolve_subject_counts` 一致：**同族并存只认最高位**
    （`1girl, 2girls` 的有效值是 2，不是 3 —— 那是 31 刀实锤的习惯残留）。
    兼容旧判据的 `two girls` 词形；`two-shot` 这类不带计数的 `shot` 词不算。
    """
    girls = 0
    boys = 0
    text = prompt or ""
    for match in _COUNT_FAMILY_RE.finditer(text):
        value = int(match.group(1))
        if match.group(2).lower().startswith("boy"):
            boys = max(boys, value)
        else:
            girls = max(girls, value)
    if re.search(r"\btwo girls\b", text, re.I):
        girls = max(girls, 2)
    return girls, boys


def _strip_field_quotes(value: str) -> str:
    """剥掉字段值外层的成对引号。

    真机实锤（2026-09-21 第 33 刀 · `D:\\pytest-tmp\\i33\\tp` 的 `L3·NSFW多人` repair 轮）：字段式
    回复里 `content: "…"` 是**照抄 JSON 写法**的，引号留在值上。带着尾引号进散文行会让
    「第二行须是英文自然语言画面描述」的判据失配（`…completes."` 后面的 `.` 不落在句末）⇒ 白红。
    ⚠ 只剥**成对**的外层引号，值内部的引号一律不动。
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def _looks_like_tag_run(text: str) -> bool:
    """像不像一串**逗号分隔的 tag**（第 33 刀，`content` 内部切分用）。

    判据刻意松而稳：至少 4 个逗号段、且**不含句末标点**（有句点就不是 tag 串，是句子）。
    """
    body = text.strip()
    return "," in body and not re.search(r"[.!?]", body) and len([
        token for token in body.split(",") if token.strip()
    ]) >= 4


def _looks_like_prose_sentence(text: str) -> bool:
    """像不像**一句完整英文画面句**（第 33 刀，`content` 内部切分用）。

    与「第二行须是英文自然语言画面描述」（`^[A-Za-z][^.?!]{24,}[.?!]`）同口径：大写字母开头、
    24 字以上、以句末标点收尾，且**句内**不再出现句末标点（多句就不该走这条切分）。
    """
    body = text.strip()
    return bool(re.fullmatch(r"[A-Z][^.?!]{24,}[.!?]", body))


def _looks_like_scene_description(text: str) -> bool:
    """这段 `content` 像**画面描述**还是像一串**裸 tag**？（第 33 刀 · 只服务「要不要拿它当 prose」）

    ⚠ 这条判据是为**保住既有红线**而存在的，不是新放宽：`test_animaJSONcontent只有tags且无额外
    句子字段时仍判死` 锁死「裸 tag 串不得被拿来冒充第二行」。判据＝**至少有一个逗号段长到足以成句**
    （≥ 6 个词）——真机对照：
      · `L1·NSFW单人` 的 content：`the white fluid running down her inner thigh`（8 词）、
        `her head tilted back with her mouth open in exhaustion`（10 词）⇒ 是**描述**（短语里带
        谓语/分词），可以当第二行；
      · 裸 tag 串 `1girl, long black hair, amber eyes, white shirt, black pencil skirt,
        legs together, medium shot, dramatic side light, notice board background`（最长 3 词）
        ⇒ 不是描述，照旧判死。
    """
    return any(len(segment.split()) >= 6 for segment in text.split(","))


def _split_comma_phrase(content: str) -> tuple[str, str]:
    """把「整段纯逗号串」按**段长**切成 (tag 行, 散文行) —— 纯机械切分，**不新增任何字**。

    第 72 刀（2026-09-29）：本函数服务的场景原本是 **`tag_head` 与 `prose` 同串**
    （见 `_looks_like_scene_description` 那条分支），而交付时那是**两行**
    ⇒ 整段逐字出现两遍（真机 [38] 第 1 跑 2340 字里约 1100 字重复；
       零 LLM 复现：同一串出现 2 次、首行以逗号收尾、次行以句号收尾）。

    切分门槛与 `_looks_like_scene_description` **共用同一个 6 词判据**，不另立阈值
    （两处口径必须恒等，否则会出现「判定它是描述、却切不出描述段」的空转）。
    切不动（tag 段不足 6 条 / 没有成句段）⇒ 返回 `(原串, "")`，调用方**保持旧行为**。
    """
    segments = [seg.strip() for seg in content.split(",") if seg.strip()]
    long_parts = [seg for seg in segments if len(seg.split()) >= 6]
    tags = [seg for seg in segments if seg not in long_parts]
    if not long_parts or len(tags) < 6:
        return content.strip(), ""
    return ", ".join(tags), ", ".join(long_parts)


def _restore_scene_value(value: object) -> object:
    if isinstance(value, str):
        return image_prompt_extract.restore_jailbreak(value)
    if isinstance(value, list):
        return [_restore_scene_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _restore_scene_value(item) for key, item in value.items()}
    return value


def _scene_for_facts(scene: Mapping[str, object]) -> dict[str, object]:
    """本地校验使用还原后的事实；防拦截标记不得污染格式和事实检查。"""
    restored = _restore_scene_value(scene)
    assert isinstance(restored, dict)
    protected = scene.get("protected_narrative")
    if isinstance(protected, str) and protected.strip():
        restored["narrative"] = image_prompt_extract.restore_jailbreak(protected)
    restored.pop("protected_narrative", None)
    # 2026-10-30：校验/编译只认**画面主体自己的**外貌段（见 `_subject_scoped_appearance`）。
    restored["appearance"] = _subject_scoped_appearance(restored)
    return restored


def _first_person_narrator(scene: Mapping[str, object]) -> str:
    """第一人称且给了叙事者名 ⇒ 返回该名；否则空串（＝调用方一律不做删改，零回归）。

    第 37 刀（2026-09-21 用户实锤）统一口径：**「叙事者不入画」的唯一判据**。
    `viewpoint` 缺失／非法、或 `narrator` 为空 ⇒ 空串。
    """
    narrator = str(scene.get("narrator") or "").strip()
    if not narrator:
        return ""
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    return narrator


def _strip_narrator_from_model_view(scene: Mapping[str, object]) -> dict[str, object]:
    """第一人称下，从**模型可见副本**里摘掉叙事者（`actors` 名单 ＋ `appearance` 段落）。

    第 37 刀（2026-09-21 用户实锤）：手选第一人称，成图 `2girls` 正确却多写 `1boy`。
    画面主体名单（计划 `subjects` / 判据 `frame_subjects`）是对的 ⇒ 主人公是从
    **模型载荷**漏进去的：`actors` 把他算进 `char_name`，`appearance` 把他的段落塞进
    `char_description`／`worldbook` ⇒ 模型据此把叙事者写进画面。

    ⚠ 只改**载荷副本**，原 `scene` 一字不动 ⇒ `_actor_visual_details` 的句级归因
    （仍按 `actors` 全集，32 刀红线）、字段账本与兜底路径全部逐字节不变。
    """
    result = dict(scene)
    narrator = _first_person_narrator(scene)
    if not narrator:
        return result
    actors = result.get("actors")
    names: list[str] = []
    if isinstance(actors, list):
        names = [str(name).strip() for name in actors if str(name).strip()]
        result["actors"] = [name for name in names if name != narrator]
    appearance = result.get("appearance")
    if isinstance(appearance, str) and appearance.strip():
        # 段落头＝行首「名字[：:]」，只认**原名单**里的名字（叙事者已从 actors 移除，
        # 故用未过滤的那份做 marker），叙事者整段丢弃直到下一个段落头。
        marker = re.compile(
            rf"^\s*({'|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))})"
            r"\s*[：:]",
        ) if names else None
        kept: list[str] = []
        dropping = False
        for line in appearance.splitlines():
            hit = marker.match(line) if marker else None
            if hit:
                dropping = hit.group(1) == narrator
            if not dropping:
                kept.append(line)
        result["appearance"] = "\n".join(kept)
    return result


def _scene_for_model(scene: Mapping[str, object]) -> dict[str, object]:
    """模型输入保留防拦截正文并匿名化角色名；姓名只用于本地关联外貌。

    第 37 刀：第一人称下**先摘掉叙事者**（见 `_strip_narrator_from_model_view`），再匿名化。
    第 43 刀：第一人称下把「叙事者局部肢体入画」写成**显式事实行**（见 `_narrator_limb_facts`）
    ——只靠正文叙事，模型读不出「这是必须画进画面的东西」（`_viewpoint_clause` 那句
    「有出处就按事实写具体」举的例子全是手/臀，器官类没有先例）。
    """
    result = _strip_narrator_from_model_view(scene)
    # 2026-10-30：模型载荷同样只带**画面主体自己的**外貌段——否则同场景非主体的角色条目
    # 会被模型当成主体形象写进画面（男指挥官吃到女角色外貌的直接来源之一）。
    result["appearance"] = _subject_scoped_appearance(result)
    protected = result.pop("protected_narrative", None)
    if isinstance(protected, str) and protected.strip():
        result["narrative"] = protected
    limb_facts = _narrator_limb_facts(result)
    if limb_facts:
        # ⚠ 只改**载荷副本**，原 `scene` 一字不动 ⇒ 字段账本与词表兜底走自己的注入点
        #   （`prompt_field_ledger` / `_concrete_scene_facts` / `_english_scene_details`）。
        result["narrative"] = (
            f"{str(result.get('narrative') or '')}\n"
            "【第一人称·叙事者局部入画事实（只写这些局部，不得写他的脸或全身）；"
            "**一律写成第一人称的呈现形态**——局部**从画面下缘进入**"
            "（`entering from the lower frame edge`／`cropped at the bottom edge`），"
            "**不写**它的根部、躯干、腿】"
            + "；".join(limb_facts)
        )
    return _anonymize_scene_characters(result)


def _character_names(scene: Mapping[str, object]) -> list[str]:
    names: list[str] = []
    actors = scene.get("actors")
    if isinstance(actors, list):
        names.extend(str(name).strip() for name in actors if str(name).strip())
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        names.extend(
            str(item.get("name") or "").strip() for item in subjects
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        )
    return list(dict.fromkeys(names))


def _replace_character_name(text: str, name: str, label: str) -> str:
    if not name:
        return text
    pattern = (
        rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        if name.isascii() else re.escape(name)
    )

    def replacement(match: re.Match[str]) -> str:
        prefix = text[:match.start()]
        if not prefix.strip() or re.search(r"[.!?]\s*$", prefix):
            return label[:1].upper() + label[1:]
        return label

    return re.sub(pattern, replacement, text, flags=re.I if name.isascii() else 0)


_CHARACTER_ORDINALS = (
    "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
)


def _character_labels(count: int) -> list[str]:
    """按出现顺序给角色分配**中性指代**。

    2026-09-20 用户实锤：第 3 名起原为 `adult character 3`——**裸编号直接进最终提示词**，
    生图模型读不懂，看着像程序编号泄漏。改成自然序数词（`the third adult character`），
    仍满足多人契约的「互斥指称 + 角色名词绑定」（`_ACTOR_NOUN_PATTERN` 只认 character 词根）。
    """
    labels = ["the primary adult character", "the second adult character"]
    for index in range(3, count + 1):
        offset = index - 3
        word = _CHARACTER_ORDINALS[offset] if offset < len(_CHARACTER_ORDINALS) else "additional"
        labels.append(f"the {word} adult character")
    return labels


def _subject_label(scene: Mapping[str, object], name: str) -> str:
    """角色名的**中性指代**，与 `_anonymize_prompt_names` 用同一张表（顺序也一致）。

    本地补写句要按人归属，但姓名不得进成稿 ⇒ 复用匿名化映射；角色不在
    `_character_names`（在场表缺项）时按**画面主体顺序**取同一套标签。
    """
    name = str(name or "").strip()
    names = _character_names(scene)
    if name in names:
        return _character_labels(len(names))[names.index(name)]
    subjects = [str(item).strip() for item in _contract_subject_names(scene)]
    if name in subjects:
        return _character_labels(len(subjects))[subjects.index(name)]
    return "the adult character"


def _anonymize_prompt_names(prompt: str, scene: Mapping[str, object]) -> str:
    names = _character_names(scene)
    for name, label in zip(names, _character_labels(len(names)), strict=False):
        prompt = _replace_character_name(prompt, name, label)
    return prompt


def _anonymize_scene_characters(scene: Mapping[str, object]) -> dict[str, object]:
    names = _character_names(scene)
    mapping = dict(zip(names, _character_labels(len(names)), strict=False))

    def rewrite(value: object) -> object:
        if isinstance(value, str):
            for name, label in mapping.items():
                value = _replace_character_name(value, name, label)
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): rewrite(item) for key, item in value.items()}
        return value

    result = rewrite(scene)
    assert isinstance(result, dict)
    return result


# 拒答句式识别/清洗收口到 prompt_clean（生图与视频提示词链共用同一套规则）。
_REFUSAL_RE = prompt_clean.REFUSAL_RE


_strip_refusal_suffix = prompt_clean.strip_refusal_suffix


# 渲染模型可能输出 <think>…</think> 推理段（新仓库/新大脑模型 CoT 行为不稳定，
# 2026-08-29 trace 实证：编译产物整段 think 混进提示词直接提交 ComfyUI）。
# 正则对齐 image_prompt_extract._THINK_RE/_THINK_OPEN_TAIL_RE。
_THINK_RE = re.compile(r"\s*<think\b[^>]*>[\s\S]*?</think>\s*", re.I)


_THINK_OPEN_TAIL_RE = re.compile(r"\s*<think\b[^>]*>[\s\S]*\Z", re.I)


def _strip_think(text: str) -> str:
    """剥掉 think 推理段（含未闭合直达结尾的尾部）；think 后的正文才是提示词。"""
    return _THINK_OPEN_TAIL_RE.sub("", _THINK_RE.sub("", text or ""))


_ANIMA_ACTION_RE = re.compile(
    r"\b(?:reaching|gripping|holding|pulling|pushing|walking|running|turning|looking|"
    r"raising|lowering|standing|sitting|kneeling|leaning|bending|lying|embracing|kissing|"
    r"fighting|attacking|swinging|drawing|opening|handing|climbing|falling|jumping|"
    r"carrying|lifting|unloading)\b",
    re.I,
)


# T2（2026-09-23 深夜）：「扣后颈」曾被误升格为「掐喉」。原式 `(?:掐|扣|卡)…(?:脖子|颈部?|喉咙)`
# 里 `颈部?` 会匹配**后颈 / 颈侧 / 颈背 / 颈椎**，且 `扣` 语义本可只是「按住」——
# 真机正文写的是「一手撑住榻底，一手**扣她后颈**」（＝按住后颈，画面里根本没有掐喉），
# 却被确定性层升格成 `one hand gripping the partner's throat`；一旦与 800 字内的插入动词同现，
# 还会连带触发 `_COMPOUND_THROAT_INTERCOURSE`（**删除** `penetrative intercourse` 等真实动作、
# 换成一条编排出来的复合动作）⇒ 属第 22 刀「无出处不得入画」同族。
# 修法＝**解剖位限定**：`(?<!后)颈(?:部)?(?!侧|背|椎)` —— 真正的「掐脖子 / 卡喉咙 / 掐颈」逐字不变。
_THROAT_GRIP_SOURCE = (
    r"(?:掐|扣|卡)(?:着|住|紧)?.{0,8}(?:脖子|喉咙|(?<!后)颈(?:部)?(?!侧|背|椎))|"
    r"(?:脖子|喉咙|(?<!后)颈(?:部)?(?!侧|背|椎)).{0,8}(?:掐|扣|卡)(?:着|住|紧)?"
)


_PENETRATION_SOURCE = r"(?:重新.{0,6}(?:推进|进入)|推进去|推入|插入|抽插|交媾|做爱|性交)"


_COMPOUND_THROAT_INTERCOURSE = (
    "penetrative intercourse while the secondary adult partner grips the primary woman's throat"
)


_SCENE_ACTION_RULES = (
    (
        rf"(?:{_THROAT_GRIP_SOURCE})[\s\S]{{0,800}}(?:{_PENETRATION_SOURCE})|"
        rf"(?:{_PENETRATION_SOURCE})[\s\S]{{0,800}}(?:{_THROAT_GRIP_SOURCE})",
        _COMPOUND_THROAT_INTERCOURSE,
    ),
    (_THROAT_GRIP_SOURCE, "one hand gripping the partner's throat"),
    (_PENETRATION_SOURCE, "penetrative intercourse"),
    (r"传教士|(?:仰卧|躺)[\s\S]{0,80}面对面|面对面[\s\S]{0,80}(?:仰卧|躺)", "face-to-face missionary position"),
    (r"(?:双腿|大腿|腿).{0,8}(?:压|架|搭).{0,10}(?:对方|他的|她的)?肩", "legs raised over the partner's shoulders"),
    (r"火车便当|抱离地面.{0,12}(?:悬空|贴住)|悬空.{0,12}(?:抱|贴住)", "standing suspended carry position"),
    (r"(?:^|[^0-9])69(?:式|姿势|体位)|六九式|上下交叠", "mutual oral 69 position"),
    (r"伸手", "reaching out"),
    (r"抓住.{0,4}手腕|握住.{0,4}手腕", "gripping wrist"),
    (r"拉向|拽向|拖向", "pulling another person"),
    (r"推开|推向|推动", "pushing"),
    (r"走向|走进|步入|行走", "walking"),
    (r"奔跑|跑向|冲向", "running"),
    (r"转身", "turning"),
    (r"回头", "looking back"),
    (r"注视|凝视|看向|望向", "looking at"),
    # 2026-09-21 第 31 刀（用户实锤：「回头妩媚勾引的看向镜头的神情没有在文段环节表示出来」）：
    # 原表只有上面两条**无神态**的动作串，且都以动名词开头 ⇒ 归动作槽、**结构上进不了文段**
    # （第 27 刀定案「动作事实不进散文尾巴」）。用户在源文里能指到出处
    # （`两人带着还没满足的眼神回头看向我。` / `回头望来的明澜和清夏同时屁股一颤。`）
    # ⇒ 是「有出处没落实」，不是编造。
    # 本支产出一条**名词短语**事实（`a … look …`）⇒ 走 `depicted with` 状态槽，能真正落进文段。
    # ⚠ 触发必须**回眸＋媚态**同现（或「媚眼/媚态」＋看向）：孤立「妩媚」可能只是舞姿或衣着，
    #   不得凭空补神情（第 22 刀「无出处不得入画」同族）。
    (r"(?:回头|回眸|扭头)[^。！？!?\n]{0,12}(?:媚|勾引|撩人|挑逗|风情|娇媚|妖媚)|"
     r"(?:媚眼|媚态|勾魂)[^。！？!?\n]{0,10}(?:看|望|瞥|回头|回眸)",
     "a coquettish come-hither look directed back over her shoulder"),
    # 2026-09-20 第 27 刀（用户实锤「第二个角色根本没有描述内容」）：具名角色只剩「旁观」一句时，
    # 旧表**一条都咬不住** ⇒ 逐人分述退化成 `is present in frame`，该角色等于从画面消失
    # （情况①原文：`清夏在旁边看着。`）。两条都只认**同句**句式，跨句不触发。
    (r"(?:在旁|在一旁|在旁边|站在一旁|站在一边)[^。！？!?\n]{0,8}(?:看|望|观|注视|观察|瞧)",
     "watching from the sidelines"),
    (r"(?:看着|瞧着|望着|观看)[^。！？!?\n]{0,8}(?:她|他|她们|他们|两人|二人|这一幕|这一切)",
     "watching them"),
    (r"抬头|仰头", "raising head"),
    (r"低头|垂首", "lowering head"),
    (r"站起|站立|站在", "standing"),
    (r"坐下|坐在", "sitting"),
    (r"跪下|跪在", "kneeling"),
    (r"靠在|倚在", "leaning"),
    (r"俯身|弯腰", "bending forward"),
    (r"躺下|躺在|卧在", "lying down"),
    (r"拥抱|抱住", "embracing"),
    (r"亲吻|吻住", "kissing"),
    (r"攻击|交战|战斗", "fighting"),
    (r"挥剑|挥刀|劈砍", "swinging a weapon"),
    (r"拔剑|拔刀", "drawing a weapon"),
    (r"打开|开启", "opening"),
    (r"递给|交给", "handing over"),
    # 2026-09-19 第 7 项：原为 `取出|拔出|抽出|移除 → removing the visible object`（一条粗规则）。
    # 实测（854 段真机语料）四源词只命中 3 段，且**没有一段是「取出容器里的物件」**：
    #   L1「要拔出来了。」/ L3「我悄悄抽出了正在抚弄阴阜的手指。」/ L3「我猛地抽出了正扒开明澜臀部的手指。」
    # ⇒ 产出「把可见物体拿开」这个**与画面无关**的串（在 NSFW 语境更会被读成「移除遮挡物」）。
    # 规则表是**并列全扫描、无优先级** ⇒ 粗串会与更具体的串**同时**进 tags，不会被后者压掉；
    # 优化项⑤ 的补丁降级又把它经 `complete_field_coverage` **嫁接进模型稿**（同轮追加
    # 「In this instant, the concrete action is …」）⇒ 须按词义拆开（每支＝一个可见动作）。
    (r"取出", "taking an object out of a container"),
    # 2026-09-20 用户实锤：原串 `withdrawing what was sheathed`（抽出鞘中之物）在真机语料里
    # **全是「抽出手指」**（「我悄悄抽出了正在抚弄阴阜的手指。」「我猛地抽出了正扒开明澜臀部的手指。」），
    # 读起来与画面无关。规则表是**并列全扫描、无优先级** ⇒ 用负向先行断言让粗串在手指语境下不触发。
    (r"(?:抽出|拔出)(?=.{0,10}(?:手指|手|胳膊|手臂))", "withdrawing the fingers"),
    (r"(?:拔出|抽出)(?!.{0,10}(?:手指|手|胳膊|手臂))", "pulling out"),
    (r"移除|移开", "moving an obstruction aside"),
    (r"摩擦|磨着|磨动|揉动", "rubbing the contact point"),
    (r"撤开|撤掉|抽回|收回", "withdrawing just before completion"),
    (r"(?:腰|身体).{0,12}(?:追|拱|顶)(?:向|过去|上去)?", "her body following the withdrawn contact"),
    (r"(?:膝盖|双腿|大腿).{0,8}(?:分开|打开)", "knees spread apart"),
    (r"压住|按住", "pressing down"),
    # 2026-09-01 用户实锤：高潮动作未映射，确定性兜底只剩通用 2girls 模板。
    # 补齐本作高频 NSFW 动作词汇，至少让兜底提示词咬住高潮时间点的动作。
    (r"(?:阳具|肉棒|肉柱|柱身).{0,12}(?:掐|握|攥|抓|按)|(?:掐|握|攥|抓|按).{0,12}(?:阳具|肉棒|肉柱|柱身)", "gripping the shaft"),
    (r"(?:拇指|指尖).{0,12}(?:压|按|碾).{0,12}(?:系带|龟头|马眼)|(?:系带|龟头|马眼).{0,12}(?:被|遭).{0,12}(?:压|按|碾)", "thumb pressing the frenulum"),
    (r"(?:顶弄|顶动|挺动|抽送|抽插)", "thrusting"),
    (r"(?:口交|口含|含住|吞入|舔舐|舔净).{0,12}(?:阳具|肉棒|柱身|龟头)|(?:阳具|肉棒|柱身|龟头).{0,12}(?:含住|吞入|舔舐|舔净)", "fellatio with the shaft in the mouth"),
    (r"(?:指尖|手指).{0,12}(?:挑开|挑逗|爱抚|抚摸)", "fingertips teasing"),
    (r"(?:冰凤|灵力|阳元).{0,12}(?:灌入|灌顶|注入)", "channeling spiritual energy into the body"),
    (r"(?:搬|卸).{0,12}木箱|木箱.{0,12}(?:搬|卸)|搬下|搬到|卸下|卸货",
     "unloading a wooden medicine crate"),
    (r"扛着|背着|搬着", "carrying"),
    # 2026-09-20 第 27 刀（用户实锤：「只说了一个角色跪下掰开臀部，但本应该是弯腰翘起屁股掰开臀部」）
    # 体态缺一层：跪姿把臀部朝向镜头必然是**躬身抬臀**，但旧表只认「跪」「掰开」两个词。
    # ⚠ 必须排在下面「掰开」支**之前**：产出顺序＝本表顺序，先「弯腰抬臀」再「掰开」才读得通。
    # 两条都**同句**锚定（`[^。！？!?\n]` 不许跨句），跨句一律不触发（第 21 刀「跨句串味」同族）。
    (r"跪[^。！？!?\n]{0,20}(?:掰开|扒开|撑开|翘起|撅起|献上|亮出|敞开|展露|露出)"
     r"[^。！？!?\n]{0,10}(?:臀|屁股|臀瓣|臀肉|臀部)",
     "bending forward with her buttocks raised"),
    # 「抬高/献上臀部」独立成支（两个语序都要认：「翘起屁股」与「臀部高高翘起」）。
    (r"(?:翘起|撅起|抬高|抬起|挺起)[^。！？!?\n]{0,6}(?:臀|屁股|臀部)|"
     r"(?:臀|屁股|臀部)[^。！？!?\n]{0,6}(?:翘起|撅起|抬高|抬起|挺起)",
     "arching her back with her buttocks raised"),
    # 2026-09-20 第 24 刀（用户实锤）：L3 兜底**丢掉了剧情里的体位动作** ——「两名女主角
    # 翘起屁股、掰开臀瓣」在成稿里一条都没有，只剩 `looking back / looking at`。
    # 根因＝这张表里没有本作高频体位词。下面五支各自**只认明确出处**（反向锁见
    # `test_兜底动作表不凭空触发`），且限定在**同一句内**（`[^。！？!?\n]` 不允许跨句），
    # 避免第 21 刀「跨句动作片段串味」那类事故。
    # ⚠ 「掰开」支带负向断言：`…正扒开明澜臀部的手指` 这类**定语形式**不算画面主体的体位展示
    #   （焦点是「手指被抽出」，与第 17 刀同族）⇒ 保住既有精确断言，只认真正的展示句。
    # 2026-09-20 第 27 刀：触发词去掉 `撅起|翘起`。它们是「**抬高/献上**」而不是「**掰开**」，
    # 旧表会把「臀部高高翘起」误映射成 `spreading her own buttocks apart`；抬高另立一支（见下）。
    # 2026-09-21 第 31 刀（用户实锤）：「把双手贴在臀肉上掰开屁股」**一条源句同时命中两条规则**
    # ⇒ 首行出现 `spreading her own buttocks apart` + `both palms pressed against her own
    # buttocks` 两条**语义重叠**的串。用户原话：「说的词多还不够详细，不如直接说双手扒开臀部」。
    # 定案（用户选定「合并成一条」）：本支改写为 `both hands spreading her buttocks apart`
    # （把「双手」这一层并进同一条），并**删除**原「双掌贴臀」支 ⇒ 同一动作永不产出两条。
    # ⚠ 新串以**施动部位**开头、不是动名词 ⇒ 必须走 `_is_action_fact` 的动作槽，否则会被
    #   拼成 `The two adult characters are depicted with both hands spreading her buttocks
    #   apart.` 这种「复数主语＋单数物主」病句（第 29 刀同族，见 `_ACTION_FACT_LEAD_RE`）。
    (r"(?:掰开|扒开|撑开|分开)(?![^。！？!?\n]{0,8}的(?:手指|手))"
     r"[^。！？!?\n]{0,10}(?:臀瓣|臀肉|屁股|臀部|臀)|"
     r"(?:臀瓣|臀肉|屁股|臀部)[^。！？!?\n]{0,12}(?:掰开|扒开|撑开|分开)",
     "both hands spreading her buttocks apart"),
    (r"(?:臀|屁股|臀部|翘臀)[^。！？!?\n]{0,12}(?:晃|摇|摆动|摇曳)|"
     r"(?:晃|摇)(?:着|动)?[^。！？!?\n]{0,10}(?:臀|屁股)",
     "swaying her hips"),
    (r"(?:献上|亮出|露出|暴露|敞开|展露)[^。！？!?\n]{0,12}(?:小穴|后庭|肛门|穴)|"
     r"(?:小穴|后庭|肛门)[^。！？!?\n]{0,12}(?:露出|裸露|暴露|敞开|展露)",
     "exposing her pussy and anus"),
)


def _scene_action_tags(scene: Mapping[str, object]) -> list[str]:
    """从事实底座保守映射可见动作；只识别明确动词，不为纯肖像编造姿态。"""
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("draft_prompt") or ""),
    ))
    tags = [tag for pattern, tag in _SCENE_ACTION_RULES if re.search(pattern, source)]
    if _COMPOUND_THROAT_INTERCOURSE in tags:
        tags = [tag for tag in tags if tag not in {
            "one hand gripping the partner's throat", "penetrative intercourse", "bending forward",
        }]
    # 英文动作底座逐段判定（2026-09-18 贴合剧情修复）：narrative 是中文正文、draft_prompt
    # 是英文动作底座（同轮 <illustration> 成稿或 build_fallback_content_tags）。旧实现用
    # 整段 source 判 isascii，中文叙事一在场就整体跳过此分支——同一条英文动作事实在
    # 「无叙事」时被采纳、「有中文叙事」时被丢弃，连带 PromptFieldLedger 的 action 塌成
    # 非必需，动作字段退出验收。改为逐段判定：中文段不参与，英文段各自提取。
    for segment in (str(scene.get("draft_prompt") or ""), str(scene.get("narrative") or "")):
        if segment.strip() and segment.isascii():
            tags.extend(match.group(0).lower() for match in _ANIMA_ACTION_RE.finditer(segment))
    return list(dict.fromkeys(tags))


# ── 动作事实的「同部位已写过动作」冲突闸（2026-09-21 第 29 刀，用户定案口径） ──
# 真机实锤（i28 · L3·NSFW多人 | niji_sections / anima_tags）：模型已写
# `sits back with one hand touching a thigh`、`hands gripping and pulling apart their own
# buttocks`，账本仍把 `withdrawing the fingers` / `swaying her hips` /
# `both palms pressed against her own buttocks` 追加到同一句尾 ⇒
#   ① **语义互斥**：同一只手既「触摸大腿」又「抽出手指」；
#   ② **归属缺失**：裸动作串写死 `her`、不带主体，三名角色在场时读者分不清是谁的动作
#      （anima 文段还会产出 `The two adult characters are depicted with both palms pressed
#      against her own buttocks.` 这种复数主语＋单数物主的病句）。
# 用户定案：**同部位已有动作表述 ⇒ 该条动作事实不追加**（事实仍可走 tags 行 / 其他档）。
# ⚠ 零回归前提：**抽不出部位的动作事实一律不跳过**（判不了就不动），故
#   `penetrative intercourse` / `looking at` 这类无部位词的事实逐字节不变。
_ACTION_PART_GROUPS: tuple[tuple[str, str], ...] = (
    # （事实里出现的词干 / 该组在**成稿文本**里的部位写法）
    (r"\bhip|\bbuttock|\bass\b|\bcheek|\brear\b",
     r"hips?|buttocks?|ass|cheeks?|rear"),
    (r"\bfinger|\bhand|\bpalm|\bthumb|\bwrist",
     r"fingers?|hands?|palms?|thumbs?|wrists?|fingertips?"),
    (r"\bleg|\bthigh|\bknee|\bankle|\bfeet\b|\bfoot\b",
     r"legs?|thighs?|knees?|ankles?|feet"),
    (r"\bpussy|\bcunt|\blabia|\bvulva|\banus|\banal\b|\bnipple|\bbreast|\bshaft\b|\bcock\b|"
     r"\bpenis|\bsemen|\bcum\b",
     r"pussy|cunt|labia|vulva|anus|nipples?|breasts?|shaft|cock|penis|semen|cum"),
    (r"\bshoulder|\bneck\b|\bthroat", r"shoulders?|neck|throat"),
    (r"\bhead|\bface|\bmouth|\blip\b|\beye|\bgaze\b",
     r"heads?|faces?|mouths?|lips?|eyes?|gaze"),
    (r"\bwaist\b|\bback\b|\bspine|\bbody\b|\btorso\b", r"waist|back|spine|body|torso"),
    (r"\barm\b|\barms\b|\belbow|\bforearm", r"arms?|elbows?|forearms?"),
)


# 接触 / 位移 / 姿态动作词干：与部位**同时**命中才算「该部位已经写过动作」。
_ACTION_VERB_RE = re.compile(
    r"\b(?:touch\w*|hold\w*|grip\w*|grasp\w*|clutch\w*|press\w*|rest\w*|reach\w*|spread\w*|"
    r"pull\w*|push\w*|slide\w*|slip\w*|insert\w*|penetrat\w*|thrust\w*|withdraw\w*|strok\w*|"
    r"rub\w*|squeez\w*|massag\w*|pinch\w*|part(?:s|ed|ing)?|cup\w*|cradl\w*|caress\w*|"
    r"teas\w*|fondl\w*|brac\w*|hook\w*|wrapp\w*|dangl\w*|drip\w*|kneel\w*|lean\w*|"
    r"bend\w*|arch\w*|sway\w*|rock\w*|grind\w*|glid\w*|trac\w*|trail\w*|hang\w*|expos\w*)\b",
    re.I,
)


# 姿态 / 全身类动作事实：tag 自身没有具体部位词，用**部位覆盖表**兜底。
# 只登记真机成稿里实际出现过的条目；未登记 ⇒ 不参与冲突判定。
_ACTION_FACT_PART_OVERRIDES: dict[str, str] = {
    "leaning": r"\b(?:back|spine|body|shoulders?|weight|elbow|forearm|arm)\b",
    "bending forward": r"\b(?:back|spine|waist|body|torso)\b",
    "bending forward with her buttocks raised": r"\b(?:back|spine|buttocks?)\b",
    "arching her back with her buttocks raised": r"\b(?:back|spine|buttocks?)\b",
    "turning": r"\b(?:body|torso|shoulders?|waist|head)\b",
    "lying down": r"\b(?:bed|mattress|floor|sheets?|surface)\b",
}


# 神态事实**豁免**同部位冲突闸（2026-09-21 第 31 刀）：
# 本条的触发词里带 `shoulder`（回眸必然是「过肩」），按部位表会被划进「头/肩」组 ⇒
# 模型只要别处写过 `in her downward gaze` 之类的视线词 ＋ 任意动词，这条神态事实就会被
# 判成「同部位已写过动作」而整条丢弃 —— 而「写了视线」不等于「写了妩媚勾引」，
# 用户要的正是这一层。故显式豁免：**判不了就当不判**（与第 29 刀「抽不出部位的事实
# 一律不跳过」同一条口径），不改闸的判定逻辑本身。
_ACTION_FACT_NO_PART = frozenset({
    "a coquettish come-hither look directed back over her shoulder",
})


def _action_fact_part_re(fact: str) -> re.Pattern[str] | None:
    """动作事实的**部位模式**；判不出部位 ⇒ `None`（不参与冲突判定 ⇒ 不跳过）。

    部位按**同义组**归并：「臀部轻轻晃着」这条事实写成 `swaying her hips`，而模型稿里
    可能写的是 `spreading her own buttocks` —— 只比字面 `hips` 会漏判，故同组词一并认。
    """
    key = " ".join(str(fact or "").split()).lower()
    if key in _ACTION_FACT_NO_PART or key in _NARRATOR_LIMB_FACT_TEXTS:
        return None
    override = _ACTION_FACT_PART_OVERRIDES.get(key)
    if override:
        return re.compile(override, re.I)
    groups = [
        written for stem, written in _ACTION_PART_GROUPS if re.search(stem, key)
    ]
    if not groups:
        return None
    return re.compile(r"\b(?:" + "|".join(groups) + r")\b", re.I)


def _action_fact_conflicts(text: str, fact: str) -> bool:
    """既有文本是否已在**同一部位**上写过动作 ⇒ 该动作事实视为已覆盖（不追加）。

    真机实锤与定案见上（`_ACTION_PART_GROUPS` 注释块）。判据只做**词面**判断：
    「事实的部位词（同义组）」与「一段接触/位移/姿态动词」在**同一份文本**内共现即算冲突。
    ⚠ 本判据只删「账本候选」，不改任何既有文本、不动合同锁要求的**逐字**落地：
    事实若已由模型写在正文里，逐字锁本来就已经满足。
    """
    part = _action_fact_part_re(fact)
    if part is None:
        return False
    body = text or ""
    return bool(part.search(body)) and bool(_ACTION_VERB_RE.search(body))


def _tag_already_actions_part(tag_line: str, fact: str) -> bool:
    """**同一条 tag 内**该部位已带动作 ⇒ 这条动作事实在兜底模板里不再追加。

    与 `_action_fact_conflicts` 的分工：那条判的是**模型正文**（一句话里「部位＋动词」共现即
    算已写过）；兜底模板面对的却是一串**逗号 tags**，照搬整串共现判据会把 `kneeling,
    soft surface` 这种不相干组合误判成「已写过该部位的动作」⇒ 整批体位词被吞掉。
    故兜底路径改用**逐段（tag）内**共现：只有 `hands gripping and pulling apart their own
    buttocks` 这种**同一个 tag 里部位与动词都在**的串才算已写过。

    2026-09-21 第 31 刀（用户选定「合并成一条」时一并要求）：把第 29 刀的同部位闸接到
    兜底路径 —— 规则表从中文叙事推出的 action tag 不得与 `draft_prompt` 已写出的同部位动作
    重复入画。
    """
    part = _action_fact_part_re(fact)
    if part is None:
        return False
    return any(
        part.search(segment) and _ACTION_VERB_RE.search(segment)
        for segment in (tag_line or "").split(",")
    )


_CJK_RE = re.compile(r"[\u3400-\u9fff]")


# 「图像表现不出来」的高精度词面（2026-09-20 用户口径）：气味 / 声音 / 台词。只做**观测**，
# 不参与判死 —— 用于在真机 trace 里判读「定格化提示词是否真的减少了不可绘制内容」。
# 刻意只收高精度词：`moan` / `silence` / `breath` 这类既是声音又可能是可见姿态的词**不收**。
_UNPAINTABLE_RE = re.compile(
    r"\b(?:scent|smell|fragrance|aroma|odor|rustle|echo(?:es)?|whisper(?:s|ed|ing)?|"
    r"murmur(?:s|ed|ing)?|the word|dialogue|monologue)\b|[“”\"]",
    re.I,
)


_VAGUE_IDENTITY_RE = re.compile(
    r"\b(?:identified character|bound character|stable appearance|current clothing condition|"
    r"illustrated identity|replacement face|redesign (?:her|his|their) identity|"
    r"defined by the (?:bound )?(?:character )?model|established (?:facial structure|eye shape|"
    r"hairstyle silhouette|body proportions|appearance|identity))\b",
    re.I,
)


# 中文角色条目的高频可视事实：同时用于语义门禁和无模型兜底。规则只翻译视觉属性，
# 不含角色专名；当前 wardrobe/正文中的服装变化会覆盖条目里的基础【穿着】段。
_VISUAL_FACT_RULES: tuple[tuple[str, str, str], ...] = (
    (r"漆黑墨发|华贵墨发|墨发|乌黑(?:长)?发|黑发", "glossy jet-black hair", r"\b(?:jet-black|raven|ebony|black) hair\b"),
    (r"发团|发髻|盘发", "hair gathered into a rounded bun", r"\b(?:hair )?(?:bun|updo|chignon)\b"),
    (r"紫玉金髻|紫玉.{0,4}(?:簪|髻|钗)", "a purple jade and gold hair ornament", r"purple jade.{0,35}(?:hair|ornament|pin)|(?:hair|ornament|pin).{0,35}purple jade"),
    (r"朱唇|红唇", "full crimson lips", r"\b(?:crimson|red|scarlet|vermilion) lips\b"),
    (r"脸颊红润|红润.{0,3}脸颊", "rosy cheeks", r"\b(?:rosy|flushed|warm) cheeks\b"),
    (r"晶亮.{0,8}美目|美目.{0,8}晶亮", "luminous rounded eyes", r"\b(?:luminous|bright|clear|glossy|shining).{0,12}eyes\b"),
    (r"暗红.{0,5}(?:美眸|眼|眸)|(?:美眸|眼|眸).{0,5}暗红", "narrow dark-red eyes", r"\b(?:narrow )?dark-red eyes\b"),
    (r"熟厚双唇|丰厚.{0,3}(?:双唇|嘴唇)", "full mature lips", r"\bfull mature lips\b"),
    (r"成熟风韵|成熟.{0,4}(?:干练|韵味)|久经历练", "a mature and composed gaze", r"\b(?:mature|seasoned|composed|experienced).{0,18}(?:gaze|eyes|expression)\b"),
    (r"前凸后翘|曲线优美|丰腴熟躯|丰腴肥熟|丰腴.{0,4}(?:身材|曲线)", "a voluptuous curvy figure", r"\b(?:voluptuous|curvy|full-figured)\b"),
    (r"爆硕巨乳|丰满胸部|巨乳", "a very full bust", r"\b(?:very |large |ample |full )?(?:bust|breasts)\b"),
    (r"媚软纤腰|纤腰", "a narrow waist", r"\b(?:narrow|slender|slim).{0,10}waist\b"),
    (r"宽厚圆硕.{0,5}(?:臀|屁股)|圆硕.{0,3}(?:臀|屁股)|肥臀", "broad rounded hips", r"\b(?:broad|wide|full|rounded).{0,12}(?:hips|buttocks)\b"),
    (r"蚕丝白袜|白丝|白袜", "white silk stockings", r"\bwhite (?:silk )?(?:stockings|socks)\b"),
    (r"修长.{0,6}(?:美腿|双腿)|厚嫩美腿", "long legs", r"\blong.{0,12}(?:legs|thighs)\b"),
    (r"素紫色?薄纱法衣|紫色?薄纱法衣", "a light purple gauze robe", r"\b(?:light |pale )?purple.{0,15}gauze robe\b"),
    (r"道门徽记|灵符", "Taoist emblems and talisman motifs", r"\b(?:Taoist|Daoist).{0,18}(?:emblem|talisman)|talisman motif"),
    (r"胸口半敞|领口半敞", "a partly open neckline", r"\bpartly open (?:neckline|front|chest)\b"),
    (r"碎花紫长裙|紫色?碎花长裙", "a floral purple long skirt", r"\b(?:floral purple|purple floral).{0,12}(?:long )?(?:skirt|dress)\b"),
    (r"红色?长裙|红裙", "a red long dress", r"\bred (?:long )?(?:dress|skirt)\b"),
    (r"红莲.{0,8}(?:华袍|长袍|袍)[\s\S]{0,400}(?:华袍|衣袍|袍服)?残片|红莲.{0,8}(?:华袍|长袍|袍).{0,12}(?:撕开|毁损|残片)|(?:撕开|毁损|残片).{0,12}红莲.{0,8}(?:华袍|长袍|袍)|red lotus robe.{0,20}(?:torn|remnants)|(?:torn|remnants).{0,20}red lotus robe", "torn remnants of a red lotus patterned robe", r"\btorn remnants of a red lotus patterned robe\b"),
    (r"红莲纹饰华袍|红莲.{0,5}(?:华袍|长袍|袍)", "a red lotus patterned robe", r"\bred lotus.{0,20}(?:patterned )?robe\b"),
    (r"高叉开衩|高开叉|高叉", "a high side slit", r"\bhigh (?:side )?slit\b"),
    (r"(?:衣|裙|袍).{0,4}(?:破碎|破损|撕裂|扯破)|(?:破碎|破损|撕裂|扯破).{0,4}(?:衣|裙|袍)", "torn clothing with stressed folds", r"\btorn.{0,20}(?:clothing|robe|dress|skirt|fabric)\b"),
    (r"赤裸|全裸|一丝不挂", "a nude body with no remaining garments", r"\b(?:nude|naked).{0,18}(?:body|woman|character)?\b"),
    (r"媚药.{0,8}(?:香薰|盘香|香)|(?:香薰|盘香).{0,8}媚药", "aphrodisiac incense releasing sweet dense smoke", r"\baphrodisiac incense\b|\bincense.{0,35}(?:aphrodisiac|sweet dense smoke)"),
    (r"锁链|镣铐", "iron chains restraining the wrists", r"\b(?:iron )?(?:chains?|shackles?).{0,30}(?:wrists?|arms?)|\b(?:wrists?|arms?).{0,30}(?:chains?|shackles?)"),
    (r"石板|石台|石床", "a cold stone slab", r"\bcold stone (?:slab|platform)\b"),
    (r"囚衣", "a pale prisoner's robe", r"\bpale prisoner's robe\b|\bprison (?:robe|garment)\b"),
)


# ── 本轮事件性必现事实（T1，2026-09-23 深夜 · 用户实锤）──────────────────────
# 与 `_VISUAL_FACT_RULES` 的**分工**（两者同层、互补，源不同）：
#   · `_VISUAL_FACT_RULES` ＝**长驻外观/服饰**（发色/唇/胸/袜/袍…），源＝
#     `_current_visual_source`（appearance/wardrobe，且 narrative **只在衣物变化时**才并入）。
#   · 本表 ＝**本轮事件才产生的客观画面事实**（体液 / 睡眠状态），源**恒为 narrative
#     （取材窗口）**，与 `_current_visual_source` 解耦。
#
# ⚠ 为什么必须解耦（真机 2026-09-23 23:26 实锤，用户判定「提取错误」）：
#   取材窗口 1676 字里「第一股射在鼻梁和眼罩边沿／第二股歪了糊在她左颊／第三股落在唇上、
#   下巴」「精液挂在眼罩角上」「睡着的狐不认人」「她还没醒」**全都在**，成稿却
#   `cum/semen/facial/on face/closed eyes/sleeping/asleep` **一个都没有** ⇒ 画面变成
#   「阴茎抵在舌头上」而不是「睡着被颜射的脸」。而该帧 `wardrobe` 为空 ⇒
#   `clothing_changed=False` ⇒ narrative **根本不进** `_current_visual_source`
#   ⇒ 旧通道**结构性扫不到**这两类事实（实测：wardrobe="" 时源仅 25 字）。
#   ⇒ 只把规则加进 `_VISUAL_FACT_RULES` 是**无效**的，必须自带 narrative 源。
#
# 命中面已量（改前，全库所有存档）：体液族仅本作 1 处、睡眠族仅本作 8 处，其余存档全 0
# ⇒ 误伤风险极低。负向断言刻意排掉「不像睡着／睡意全无」这类否定写法。
_EVENT_FACT_RULES: tuple[tuple[str, str, str], ...] = (
    # 体液：颜射/精液在脸上——画面必须有体液（用户原话「师傅脸上应该有精液才是」）。
    # ① 直接写体液名词（最强、零歧义）；② 「射在/射满/射了」＋常见身体部位词
    # （真机窗口写的是「第一股射在鼻梁和眼罩边沿」，无「脸」字 ⇒ 部位词表须含鼻/眼罩等）。
    (r"精液|白浊|颜射|射满|射在[^。\n]{0,12}(?:脸|面|颊|腮|唇|嘴|口|鼻|眼|眉|颈|脖|肩|胸|乳|腹|肚|背|臀|腿|身)"
     r"|射了[^。\n]{0,8}(?:脸|面|颊|唇|嘴|口|胸|身)",
     "cum on face",
     r"\b(?:facial|cum|semen|ejaculat\w*|spunk)\b"),
    # 睡眠状态：睡中/未醒——画面必须闭眼（用户原话「师傅的睡觉…丢失了」）。
    # `(?!不着|不醒|意)` 排掉「睡不着／睡不醒／睡意」；`没醒` 仍算睡中故保留。
    (r"(?:睡(?:着|梦|沉|熟)|沉睡|阖眼|未醒|没醒)(?!不着|不醒|意)",
     "closed eyes",
     r"\bclosed eyes\b|\beyes (?:shut|closed)\b|\bsleeping\b|\basleep\b"),
)


def _event_fact_source(scene: Mapping[str, object]) -> str:
    """事件性必现事实的源＝**取材窗口**（`narrative`），恒扫、与衣物变化解耦。"""
    return image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))


def _required_event_facts(scene: Mapping[str, object]) -> list[str]:
    """窗口里出现的事件性事实（体液/睡眠）⇒ 画面必须体现，返回英文短语列表。"""
    source = _event_fact_source(scene)
    if not source.strip():
        return []
    return list(dict.fromkeys(
        phrase for pattern, phrase, _ in _EVENT_FACT_RULES if re.search(pattern, source)
    ))


def _missing_event_facts(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """窗口有、成稿漏的事件性事实（供报错与补写）；窗口无 ⇒ 空表（零回归）。"""
    source = _event_fact_source(scene)
    if not source.strip():
        return []
    return list(dict.fromkeys(
        phrase for pattern, phrase, output_pattern in _EVENT_FACT_RULES
        if re.search(pattern, source) and not re.search(output_pattern, prompt, re.I)
    ))


# ── 逐角色外貌绑定（2026-10-30 用户定案）─────────────────────────────────────
# 真机/推演实锤：`appearance` 是**在场角色**的拼接文本（上游
# `_filter_illustration_appearance` 按在场名过滤），本层却把它当成「画面主体的外貌」
# ⇒ 单人帧的主体（如男指挥官）被补上同场景女角色的巨乳/肥臀事实
# （`The primary adult woman is depicted with a very full bust, broad rounded hips.`）。
# 用户口径：**条目外貌只属于条目本人；没有外貌条目的主体不拷贝别人的形象**。
# 判据与上游生产者同口径：行首「名字[：:]」段落头；只做**词面归属**，不猜性别、不引词表。
_APPEARANCE_HEAD_RE = re.compile(r"^\s*([^\s：:【]+)\s*[：:]")
# 段落头位置常见**字段标签**（`外貌：…`／`穿着：…`），不是角色名 ⇒ 整块按单段处理（零回归）。
_APPEARANCE_FIELD_LABELS = frozenset({
    "外貌", "身材", "穿着", "服饰", "服装", "发型", "体型", "备注", "特征",
    "当前穿着", "服装状态", "appearance", "outfit", "wardrobe", "body",
})


def _appearance_segments(appearance: str) -> list[tuple[str, str]] | None:
    """按行首「名字[：:]」切段；无角色段落头 ⇒ None（单段自由文本，维持旧口径）。"""
    lines = str(appearance or "").splitlines()
    if not lines:
        return None
    has_header = any(
        match and match.group(1).strip().lower() not in _APPEARANCE_FIELD_LABELS
        for match in (_APPEARANCE_HEAD_RE.match(line) for line in lines)
    )
    if not has_header:
        return None
    segments: list[tuple[str, list[str]]] = []
    owner = ""
    body: list[str] = []
    for line in lines:
        match = _APPEARANCE_HEAD_RE.match(line)
        name = match.group(1).strip() if match else ""
        if name and name.lower() not in _APPEARANCE_FIELD_LABELS:
            if body:
                segments.append((owner, body))
            owner, body = name, [line]
        else:
            body.append(line)
    if body:
        segments.append((owner, body))
    return [(name, "\n".join(items)) for name, items in segments]


def _same_character(left: str, right: str) -> bool:
    """段落头名与主体名是否同一人：全等或**双向包含**（`甲某` ⊂ `甲某元帅`）。

    世界书条目名常比判据给出的主体名长（条目「甲某元帅」/判据「甲某」），
    只做全等会把她自己的条目判成「别人的」而整段丢掉。单字名不参与包含匹配，防误配。
    """
    left, right = str(left or "").strip(), str(right or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 2 and (left in right or right in left)


def _subject_scoped_appearance(scene: Mapping[str, object]) -> str:
    """只保留**画面主体**自己的外观段；没有条目段的主体不借用他人外貌。

    ⚠ 零回归锁：无角色段落头 / 无画面主体信号 ⇒ 原样返回（旧口径逐字节不变）。
    """
    raw = str(scene.get("appearance") or "")
    if not raw.strip():
        return ""
    segments = _appearance_segments(raw)
    if segments is None:
        return raw
    subjects = [str(name).strip() for name in _contract_subject_names(scene) if str(name).strip()]
    if not subjects:
        return raw
    kept = [
        text for owner, text in segments
        if not owner or any(_same_character(owner, subject) for subject in subjects)
    ]
    return "\n".join(kept).strip()


def _subject_appearance_fact_groups(scene: Mapping[str, object]) -> dict[str, list[str]]:
    """每个**画面主体**自己的外观事实（供多人帧逐人分述）；无段落头 ⇒ 空表。"""
    segments = _appearance_segments(str(scene.get("appearance") or ""))
    if segments is None:
        return {}
    groups: dict[str, list[str]] = {}
    for subject in _contract_subject_names(scene):
        name = str(subject).strip()
        blocks = [text for owner, text in segments if _same_character(owner, name)]
        if not blocks:
            continue
        body_lines = [line for block in blocks for line in block.splitlines()]
        # 段落头是**归属标记**、不是外观内容：ASCII 判定与词表映射都只看正文，
        # 否则「中文名＋英文外貌」整段过不了 `isascii()`，英文事实永远进不了账本。
        # ⚠ 条目常写成**一整行**（`名字：【外貌】…`）⇒ 只摘**行首前缀**，不能整行丢弃。
        if body_lines:
            body_lines[0] = _APPEARANCE_HEAD_RE.sub("", body_lines[0], count=1)
        source = "\n".join(body_lines).strip()
        if not source:
            continue
        facts = _mapped_facts_from(source)
        if source.isascii():
            facts.extend(
                part.strip(" ,.;") for part in source.split(",")
                if _FIELD_PATTERNS["appearance"].search(part)
                and not _FIELD_PATTERNS["wardrobe"].search(part)
            )
        facts = list(dict.fromkeys(fact for fact in facts if fact.strip()))
        if facts:
            groups[name] = facts
    return groups


def _current_visual_source(scene: Mapping[str, object]) -> str:
    appearance = _subject_scoped_appearance(scene)
    wardrobe = str(scene.get("wardrobe") or "")
    narrative = str(scene.get("narrative") or "")
    clothing_changed = bool(wardrobe.strip() or re.search(
        r"赤裸|全裸|一丝不挂|衣.{0,6}(?:破|裂|脱|褪)|裙.{0,6}(?:破|裂|脱|褪)|袜.{0,6}(?:破|裂|脱|扯)",
        narrative,
    ))
    if clothing_changed:
        appearance = appearance.split("【穿着】", 1)[0]
    subjects = scene.get("subjects")
    actors = scene.get("actors")
    actor_names = {
        str(name).strip() for name in actors if str(name).strip()
    } if isinstance(actors, list) else set()
    subject_details = "\n".join(
        str(item.get("description") or "") for item in subjects
        if isinstance(item, Mapping) and (
            not actor_names
            or not str(item.get("name") or "").strip()
            or str(item.get("name") or "").strip() in actor_names
        )
    ) if isinstance(subjects, list) else ""
    return "\n".join(filter(None, (
        appearance, wardrobe, narrative if clothing_changed else "", subject_details,
    )))


def _mapped_visual_facts(scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    facts = list(dict.fromkeys(
        phrase for source_pattern, phrase, _ in _VISUAL_FACT_RULES
        if re.search(source_pattern, source)
    ))
    if "torn remnants of a red lotus patterned robe" in facts:
        facts = [fact for fact in facts if fact != "a red lotus patterned robe"]
    return facts


# 句级归属的两个正则（2026-09-20 第 24 刀，第 25 刀修订口径）：
#   · 第一人称 ⇒ **按句内是否点名角色分两种**：点名的（「我紧捏**明澜**的臀部」）按名归到
#     被触及的角色名下（用户 2026-09-20 第 25 刀口径：主角**也有动作**，要判、不能一律丢）；
#     没点名的（「我悄悄抽出了…手指」）才跳过——归属对象答不出来就不写，宁缺勿猜。
#   · 回指代词 ⇒ 只在**有最近点名者**时按就近原则归属，绝不广播给全部角色。
_NARRATOR_SELF_RE = re.compile(r"我|咱|俺")


_ACTOR_PRONOUN_RE = re.compile(r"她|他|对方|二人|两人|彼此|她们|他们")


# ---------------------------------------------------------------------------
# 主角**对某个角色**的接触动作（2026-09-20 第 25 刀 · 用户原话）
#
# 「并不是主人公不吃体位词，而是要判断主角有没有所谓的动作 —— 如紧捏 xxx 角色的臀部，
#   然后你就可以说『（一只手）放在哪个角色的臀部上』；如果没有，那也不需要强调第一或第三人称。」
#
# 三条设计约束（缺一条就会退化成上一刀的「一律丢掉」或「凭空编造」）：
#   ① 与 `_SCENE_ACTION_RULES` **分开成表**：接触是「接触关系」，措辞走 `has …` 而不是
#      `performs …`；也**不得**污染 `_scene_action_tags` 的既有精确断言（第 17／24 刀都锁着它）。
#   ② **只从含第一人称的句子提取** ⇒ 「**她**把双手贴到臀肉上」（自己的手）不产接触 tag，
#      与既有的 `both palms pressed against her own buttocks` 分工明确、互不抢。
#   ③ **必须有被点名的角色**才归属（靠 `_actor_visual_details` 的按名归属天然保证）；
#      只有部位、没有角色 ⇒ 答不出「哪个角色」就**不写**（宁缺勿猜）。
#   ⚠ 词表刻意**窄**（只收臀部/腰/私处这类明确体位词），不收「肩/手/头」等通用部位——
#      否则「我看向明澜，揉了揉自己的肩膀」会被误判成对明澜的接触。
# ---------------------------------------------------------------------------
_SCENE_CONTACT_RULES = (
    (r"(?:捏|掐|揉|抚弄|抚摸|摩挲|按住|捂住|抓住|攥住|握住|摸着|扶着)"
     r"[^。！？!?\n]{0,10}(?:臀部|屁股|臀肉|臀瓣|臀|腰肢|腰)",
     "a hand resting on her buttocks"),
    (r"(?:抚弄|抚摸|揉|按|挑弄|摩挲|插入)"
     r"[^。！？!?\n]{0,10}(?:阴阜|私处|小穴|下体|花穴|腿间|股间)",
     "a hand touching her crotch"),
)


_CONTACT_TAGS = frozenset(tag for _pattern, tag in _SCENE_CONTACT_RULES)


def _scene_contact_tags(clauses: list[str]) -> list[str]:
    """提取「**主角的手**放在哪个角色身上」的可见事实（只认含第一人称的句子）。"""
    tags: list[str] = []
    for clause in clauses:
        if not _NARRATOR_SELF_RE.search(clause):
            continue
        tags.extend(
            tag for pattern, tag in _SCENE_CONTACT_RULES if re.search(pattern, clause)
        )
    return list(dict.fromkeys(tags))


# ---------------------------------------------------------------------------
# 第一人称「叙事者的局部肢体按剧情入画」（2026-09-22 用户实锤 · 第 43 刀）
#
# 用户原话：「第一人称时如果画面有描述肢体是能进入画面的……这个画面中第一人称视角本应该在
#   镜头下方会出现主人公的肉棒拍在画面女主角的脸上挤压的动作，但是却没有这个提示词，可能是
#   前面为了防止主人公完整入镜杀的太狠，导致之前协议的部分肢体可以根据剧情的主人公动作描述
#   进入画面的功能被杀掉了」。
#
# 归因（两条通道同时断，缺一都复现不了该帧）：
#   ① `_scene_contact_tags` 的**唯一调用点**在 `_actor_visual_details`，而后者在
#      `len(scope) < 2` 时早退；第一人称下画面主体只剩女主 ⇒ **单主体帧没有这条通道**；
#      唯一消费方 `_multi_actor_sentences` 同样只在 ≥2 主体时产出。
#      ⇒ 「局部入画」只剩 `_viewpoint_clause` 的 system 条款在喊，**没有任何确定性事实源**，
#        模型不主动写就是一条都没有。
#   ② 既有 `_SCENE_CONTACT_RULES` 只收「手 × 臀/腰/私处」，且要求句内出现第一人称
#      （`我/咱/俺`）——真机正文用的是「那条肉棒从单衣下摆里挣出来…命中她鼻梁以下的整张脸」
#      （主语是器官、叙事者是名字）⇒ 连句级门槛都过不去。
#
# 设计（**按类管、不是禁词表**）：
#   · 只认**部位本身决定归属**的类：男性器官（肉棒/阳具/阴茎/龟头…）不可能是画面里女性主体的
#     部位 ⇒ 归属无歧义，绝不会把女主自己的动作误配给叙事者（这是本表敢放宽句级门槛的前提）；
#   · 只在**同句**出现「器官 + 接触动作 + 被触部位」三元组时产出（第 22 刀同族：无出处不入画）；
#   · 只在 `first_person` 生效 ⇒ 第三人称下叙事者本来就在画面里、走常规事实链，逐字节零回归。
#   ⚠ 输出写成**名词短语**：既进得了 anima 的 `depicted with` 状态槽，也进得了散文通道；
#     动名词片段会被 `_is_dangling_fact` 剔掉，正是第 27 刀踩过的坑。
#   ⚠ 目标侧代词沿用既有 `_SCENE_CONTACT_RULES` 的 `her` 口径（画面主体为女性角色）。
# ---------------------------------------------------------------------------
_PENIS_NOUN = r"(?:肉棒|阳具|阴茎|玉茎|巨根|龟头|柱身)"


_FACE_NOUN = r"(?:脸|面|脸颊|颊|鼻|唇|嘴|口)"


_MOUTH_NOUN = r"(?:口|嘴|唇|口腔|喉咙?)"


_BUTT_NOUN = r"(?:臀|屁股|臀肉|后庭|股沟|腰)"


# ⚠ **必须限定「器官 → 接触动词 → 被触部位」的邻接距离**（2026-09-22 零 LLM 走查实锤）：
#   只判「同句三元组共现」会在真机正文上**过度命中** —— 原句
#   `…龟头顶端撞过眼罩下沿，留下一道黏腻的阳元浊痕，从她脸颊一直挂到嘴角。`
#   里的 `顶`（＝「顶端」的一半）与 `唇/脸` 同句 ⇒ 一次点着「压脸」与「口交」两条规则，
#   产出**正文里根本没有**的动作（＝凭空装置，踩第 22 刀红线）。
#   ⇒ 判据形同既有的 `_SCENE_CONTACT_RULES`：**动词与其宾语部位必须邻近**（≤10 字且不跨句）。
#   ⚠ 宁可漏（少写一条局部）绝不编造（多写一条没发生的动作）。
_LIMB_GAP = r"[^。！？!?\n]{0,10}"


_NARRATOR_LIMB_RULES: tuple[tuple[str, str], ...] = (
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:拍|甩|弹|抽打|击|撞|扇){_LIMB_GAP}{_FACE_NOUN}",
     "an erect penis slapping her face"),
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:贴|抵|压|蹭|摩|擦|挤压|顶){_LIMB_GAP}{_FACE_NOUN}",
     "an erect penis pressing against her face"),
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:插|顶|戳|捅|送入|推进){_LIMB_GAP}{_MOUTH_NOUN}",
     "an erect penis thrusting into her mouth"),
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:顶|抵|蹭|摩|擦|贴|压){_LIMB_GAP}{_BUTT_NOUN}",
     "an erect penis pressing against her buttocks"),
)


# ---------------------------------------------------------------------------
# 第 47 刀 · 手/臂类「叙事者局部肢体入画」（2026-09-22 用户口径纠正）
#
# 用户原话：「肢体例外**不是说洛渊的手这种**，而是**主人公对于画面内容的干涉参与到画面内容**
#   才会显现，比如说**用双手抓住画面角色的巨乳**，或者是**用肉棒拍打画面角色的脸颊**。」
# ⇒ 判据＝「**叙事者（主人公）的身体部位作为施动者、主动作用于画面主体**」，
#   **与文本怎么称呼该部位无关**（喊「我」／喊名字／以部位直接作主语都算）。
#
# 43 刀只建了**器官类**：因为「器官本身决定归属」（男性器官不可能是画面里女性主体的部位）
#   ⇒ 无须句级门槛也判不错。**手/臂类没有这个性质**（「手」既可能是叙事者的、也可能是女主的）
#   ⇒ 本表用三重限定换回确定性（缺一就可能把别人的手算给叙事者）：
#     ① 施动者**不接受第三人称领有**：`她的双手…`／`他伸手…` 这种不认（`_HAND_NO_THIRD_OWNER`）；
#     ② 施动者与目标之间的间隙里不得出现 `自己的／自身`（女主抓自己 ⇒ 不是叙事者的手）；
#     ③ 句级门槛（见 `_hand_facts`）：分句里**有第一人称`我/咱/俺`**，或施动者**之前**没出现过
#        第三人称（第一人称叙事里，未标主的施动者就是叙事者）⇒ 才认。
#   ⚠ 三重限定**只作用于手/臂类**；器官类规则逐条不动（零回归）。
#   ⚠ 目标部位**刻意只收 胸/乳 与 脸** —— 25 刀 `_SCENE_CONTACT_RULES` 已管 臀/腰/私处
#     （多人帧走 `has a hand resting on her buttocks`），此处再收会与之**重复出两条事实**。
#   ⚠ 同 43 刀「宁漏不编」：只在**同一分句内**、**邻接且不跨句**（≤10 字）时产出。
# ---------------------------------------------------------------------------
_HAND_NOUN = r"(?:双手|两只手|一只手|手掌|巴掌|手指|指尖|手臂|双臂|手)"


_BREAST_NOUN = r"(?:巨乳|双乳|乳房|胸部|胸|奶子)"


_HAND_NO_THIRD_OWNER = r"(?<![她他])(?<!她的)(?<!他的)"


_HAND_SELF_DENY = r"(?!自己的|自身)"


_HAND_GAP = rf"(?:{_HAND_SELF_DENY}[^。！？!?\n]){{0,10}}"


# ⚠⚠ 措辞**必须以 `hands` 开头**（不可写 `a hand …`）：`_ACTION_FACT_LEAD_RE`（`:2997`）刻意
#   只认 `both hands?`／`two hands?`／`hands?` 与**男根一族**（认冠词的放宽只给器官类），
#   写成 `a hand grabbing her breasts` 会被判成**状态事实** ⇒ 塞进 `depicted with` 槽 ⇒
#   复述成 `The primary adult woman is depicted with a hand grabbing her breasts.`
#   —— 主语是女性主体、手却属于叙事者（第 29／31／43 刀同族的「主语/物主错配」病句）。
#   单/复数按 tag 惯用形归一（同既有的 `both hands spreading her buttocks apart` 口径）。
_NARRATOR_HAND_RULES: tuple[tuple[str, str], ...] = (
    (rf"{_HAND_NO_THIRD_OWNER}{_HAND_NOUN}{_HAND_GAP}"
     rf"(?:抓|握住|攥|捏|托|捧|揉|揉捏|掐){_HAND_GAP}{_BREAST_NOUN}",
     "hands grabbing her breasts"),
    (rf"{_HAND_NO_THIRD_OWNER}{_HAND_NOUN}{_HAND_GAP}"
     rf"(?:抚摸|抚弄|摩挲|按|压){_HAND_GAP}{_BREAST_NOUN}",
     "hands caressing her breasts"),
    (rf"{_HAND_NO_THIRD_OWNER}{_HAND_NOUN}{_HAND_GAP}"
     rf"(?:拍|扇|抚摸|捧住|托起|托|捧|捏|掐|按){_HAND_GAP}{_FACE_NOUN}",
     "hands touching her face"),
)


_THIRD_PARTY_RE = re.compile(r"她|他")


# ⚠ 这些事实**豁免第 29 刀的同部位冲突闸**（`_action_fact_part_re` 返回 None）：判据会把
#   「her face」并进「头/面」组 ⇒ 模型只要写了 `close-up on lower face` ＋ 任意姿态动词，
#   本事实就被判成「同部位已写过动作」而整条丢弃 —— 可「写了脸的特写」绝不等于「写了器官与
#   脸的接触」。同第 31 刀神态事实口径：**判不了部位就当不判**。
_NARRATOR_LIMB_FACT_TEXTS = frozenset(
    phrase for *_rest, phrase in (*_NARRATOR_LIMB_RULES, *_NARRATOR_HAND_RULES)
)


def _hand_facts(clause: str) -> list[str]:
    """手/臂类「叙事者局部肢体入画」事实（第 47 刀；判据见上表注释）。

    句级门槛（器官类**不设**，见上）：① 分句里有第一人称`我/咱/俺`；或
    ② 施动者**之前**没出现过第三人称（第一人称叙事里，未标主的施动者就是叙事者）。
    ⚠ 已知边界（宁漏不编的代价）：同一逗号分句里若先出现别的施动者
      （`我抱住她，她伸手拍她的脸`），① 仍会放行 ⇒ 手/臂类可能多认一条；器官类无此问题。
    """
    self_narrated = bool(_NARRATOR_SELF_RE.search(clause))
    found: list[str] = []
    for pattern, phrase in _NARRATOR_HAND_RULES:
        for match in re.finditer(pattern, clause):
            if self_narrated or not _THIRD_PARTY_RE.search(clause[: match.start()]):
                found.append(phrase)
    return found


# 第 62 刀（2026-09-28 · 用户定性）：第一人称「叙事者部位事实」的**取舍**。
#
# 用户原话：「2 的问题在于第一人称的**视觉下的取舍**，即第三人称是保证内容提示词选择合适
#   视角**全面展示信息**，那第一人称就是在内容的基础上**用第一人称去进行取舍或修改使其
#   符合第一人称情况下的特色**」。
#
# 真机现场（第 60 刀同一批取证 · 第 4 次重生成）：该帧是**上半身近身帧**（face 命中），
# 首行却**同时**写着 `an erect penis pressing against her buttocks`（下半身）与
# `… her face`（上半身）—— 两条**物理上不可能同现**，且前者根本装不进上半身取景。
# ⇒ 57 刀 ③ 的取景筛分**只覆盖 `appearance`/`wardrobe`**，**不覆盖**本函数产出的动作事实。
#
# ⚠ 判据**复用** 57 刀 ③ 的口径（同一个 4 键 blob ＋ `_VIEW_FACE_CONTACT_RE` 面优先）；
#   刻意**不改** `first_person_frame_filter` / `_first_person_framing` 的既有实现
#   （它们各自保留原 blob 构建 ⇒ 零回归），本函数只服务「叙事者部位事实的取舍」。
# ⚠ 只做**一个方向**（上半身帧剔下半身），反向不猜（与 57 刀 ③ 一致）；带**剔空保护**。
_FP_LOWER_LIMB_RE = re.compile(
    r"\b(?:buttocks?|butt|hips?|thighs?|legs?|waist|groin|lower body|lower frame)\b",
    re.I,
)


def _first_person_upper_frame(scene: Mapping[str, object]) -> bool:
    """本帧是否「上半身近身帧」（第 62 刀）：与 57 刀 ③ **同口径**（4 键 blob ＋ 面优先）。

    ⚠ 只读、纯函数；`viewpoint` 非 `first_person` ⇒ `False`（零回归锁）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return False
    sources: list[str] = []
    for key in ("narrative", "appearance", "wardrobe", "draft_prompt"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(image_prompt_extract.restore_jailbreak(value))
    blob = "\n".join(sources)
    return _view_face_frame(blob)


def _narrator_limb_facts(scene: Mapping[str, object]) -> list[str]:
    """第一人称下「叙事者局部肢体入画」的**确定性事实源**（第 43 ＋ 47 ＋ 62 刀）。

    ⚠ 零回归锁：`viewpoint` 缺失／非 `first_person`、或画面主体为空 ⇒ 返回空表
      ⇒ 所有下游（模型载荷／字段账本／词表兜底）逐字节等于旧行为。
    ⚠ 第 62 刀（用户定性「第一人称＝在内容基础上用第一人称取舍」）：产出**先按本帧取景族
      取舍** —— 上半身近身帧里**装不下**下半身部位事实（真机第 4 次重生成里
      `… against her buttocks` 与 `… her face` 同现、物理上不可能）。
      判据与 57 刀 ③ 同口径（4 键 blob ＋ 面优先）、只做一个方向、剔空保护。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return []
    if not _contract_subject_names(scene):
        return []
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    facts: list[str] = []
    for clause in re.split(r"[。！？!?；;\n]", narrative):
        if not clause.strip():
            continue
        facts.extend(
            phrase for pattern, phrase in _NARRATOR_LIMB_RULES
            if re.search(pattern, clause)
        )
        facts.extend(_hand_facts(clause))
    facts = list(dict.fromkeys(facts))
    if facts and _first_person_upper_frame(scene):
        # 第 62 刀：上半身帧 ⇒ 剔下半身部位事实；**剔空保护**（全被剔 ⇒ 保留原文，
        # 宁缺勿猜 —— 清空会让「叙事者局部入画」整条消失，与 57 刀 ③ 同族）。
        kept = [fact for fact in facts if not _FP_LOWER_LIMB_RE.search(fact)]
        facts = kept or facts
    return facts


def _actor_visual_details(scene: Mapping[str, object]) -> list[tuple[str, list[str], list[str]]]:
    """按高潮人物拆开外貌与动作，供所有 Profile 的多人确定性编译。

    ⚠ **两条名单必须分开**（第 32 刀，2026-09-21 用户实锤）：
      · **归因名单** ＝ `scene["actors"]`（取材窗口解析出的**在场**全集）——「认得出点名句」的依据。
        缩小它会让**点名第三人**的句子变成无名句，紧跟其后的代词句被就近误配给在场角色；
      · **输出名单** ＝ `_contract_subject_names(scene)`（**画面主体**；判据 `frame_subjects` /
        同轮计划 `subjects` 的正向证据优先）—— 画面里有几个人就写几个人。
    真机现场（L3·NSFW多人）：在场 3 人（含叙事者泰宇老师）、画面主体 2 人 ⇒ 原先多写一条
    `An additional adult character at the center of the frame is present in frame.`，
    ①与同稿的人数措辞 `The two adult characters' …` **自相矛盾**（写了 3 个人却说 two）；
    ②把叙事者摆到**正中央**、遮挡两名主体的翘臀掰臀动作（原文他自己也是 `behind them`）。
    无正向证据时 `_contract_subject_names` 退回 `actors` ⇒ 逐字节等于旧行为（零回归前提）。
    """
    actor_values = scene.get("actors")
    actors = [
        str(name).strip() for name in actor_values
        if str(name).strip()
    ] if isinstance(actor_values, list) else []
    scope = [str(name).strip() for name in _contract_subject_names(scene) if str(name).strip()]
    if len(scope) < 2:
        return []
    sources: dict[str, list[str]] = {name: [] for name in actors}
    appearance = str(scene.get("appearance") or "")
    marker = re.compile(
        rf"^\s*({'|'.join(re.escape(name) for name in sorted(actors, key=len, reverse=True))})\s*[：:]",
    )
    current = ""
    for line in appearance.splitlines():
        match = marker.match(line)
        if match:
            current = match.group(1)
        if current:
            sources[current].append(line)
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    # 2026-09-20 第 24 刀：句级归属必须**按出现顺序**做，不能「谁的名字出现在这句里就归谁」。
    # 真机 L3 窗口里「她把双手贴到臀肉上，小心翼翼地掰开屁股。」**没有名字**，只有回指代词
    # ⇒ 旧实现把它整句丢掉（探针实锤：逐人 facts/actions 全空 ⇒ 兜底退化成 `is present in frame`）。
    # 新口径三步：
    #   ① 含角色名的句子按名归属（多个名字同句 ⇒ 同句共享）；
    #   ② 无名的**就近前指代词句**归「最近一次被点名的人」（顺序敏感：必须在①之后判）；
    #   ③ **无名的**第一人称句不归任何角色，也**不改写**「最近点名者」——否则
    #      「我扒开明澜臀部，她轻轻晃着」里的后半句会被错记到上一句的角色名下。
    #      ⚠ 但**点了名的**第一人称句（「我紧捏着明澜的臀部」）必须先走 ① 按名归属
    #      （第 25 刀用户口径：主角也有动作，要判不要一律丢）——所以 ① 必须排在 ③ 之前。
    last_named = ""
    for clause in re.split(r"[。！？!?；;\n]", narrative):
        clause = clause.strip()
        if not clause:
            continue
        named = [actor for actor in actors if actor in clause]
        if named:
            for actor in named:
                sources[actor].append(clause)
            last_named = named[-1]
            continue
        if _NARRATOR_SELF_RE.search(clause):
            continue
        if _COLLECTIVE_SUBJECT_RE.search(clause):
            # 集体句（主语是「两侧/两人/双方…」）不参与就近代词归属：句里的**宾语**代词
            # （「两侧同时献上小穴和后庭，争着要**他**插进来」）会把整句引到某个具体角色名下，
            # 把女性体位动作塞给他（真机 L3 实锤：男主吃到了 swaying hips / exposing pussy）。
            continue
        if last_named and _ACTOR_PRONOUN_RE.search(clause):
            sources[last_named].append(clause)
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        for subject in subjects:
            if not isinstance(subject, Mapping):
                continue
            name = str(subject.get("name") or "").strip()
            description = str(subject.get("description") or "").strip()
            if name in sources and description:
                sources[name].append(description)
    details: list[tuple[str, list[str], list[str]]] = []
    # 输出只列**画面主体**（`scope`）：窗口里被提及但不在画面里的角色（旁观者 / 叙事者）不写进画面。
    # ⚠ 上面那段句级归因仍按 `actors` 全集跑 —— 否则点名第三人的句子会被判成无名句，
    #   紧跟的代词句被就近误配给在场角色（见函数 docstring）。
    for actor in scope:
        source = "\n".join(sources.get(actor, []))
        facts = _mapped_facts_from(source)
        english = source.encode("ascii", "ignore").decode("ascii")
        english = " ".join(english.split()).strip(" :,.;-")
        if english and re.search(r"[A-Za-z]{3}", english):
            facts.append(english)
        actions = _scene_action_tags({"narrative": source})
        # 第 25 刀：主角**对这个角色**的接触动作归到这里——该角色 source 里能出现的第一人称句，
        # 必然是「点了这个角色的名」才被按名归入的（无名的第一人称句在上面的循环里已被跳过）。
        contacts = _scene_contact_tags(source.splitlines())
        details.append((
            actor,
            list(dict.fromkeys(facts)),
            list(dict.fromkeys([*actions, *contacts])),
        ))
    return details


# 多人分述的**位置锚**（2026-09-20 用户实锤）：L3·anima 原成稿「tag 没把两名角色的动作表示出来、
# 没区分人物」。用户口径：「就算她们的动作都一样，你也要说这名角色在镜头左侧翘起臀部掰开屁股，
# 另一名角色在镜头右侧翘起臀部掰开屁股」⇒ 每人一条，**位置 + 该人自己的动作/可见事实**。
_MULTI_ACTOR_POSITIONS = (
    "on the left side of the frame",
    "on the right side of the frame",
    "at the center of the frame",
    "in the foreground of the frame",
    "in the background of the frame",
)


# 集体句（无点名主语、无第一人称，但主语是「两个人」）的指称词（2026-09-20 第 24 刀）。
# ⚠ 故意**不收**单字「都」「两个」以外的宽词：宁可不补齐，也不要把某一个人的动作广播给全员。
_COLLECTIVE_SUBJECT_RE = re.compile(r"两侧|两人|二人|双方|彼此|一起|同时|两个|她们|他们|全都")


def _collective_action_tags(scene: Mapping[str, object]) -> list[str]:
    """只从**集体句**提取动作，供「无独立证据的主体」补齐（2026-09-20 第 24 刀）。

    用户口径：动作一样，也要在各自位置各写一遍。但直接拿**整段**动作 tags 广播会踩
    第 21 刀跨句串味的坑（叙事者「我」的动作、别人的动作都会被塞进来）。这里只认
    **主语为集体**的句子——句中不出现点名的角色、也不出现第一人称，却出现集体指称词
    （`两侧同时献上小穴和后庭…屁股轻轻晃着。`）。
    """
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    actors = scene.get("actors")
    names = [str(name).strip() for name in actors if str(name).strip()] \
        if isinstance(actors, list) else []
    clauses = [
        clause.strip() for clause in re.split(r"[。！？!?；;\n]", narrative)
        if clause.strip()
        and _COLLECTIVE_SUBJECT_RE.search(clause)
        and not _NARRATOR_SELF_RE.search(clause)
        and not any(name in clause for name in names)
    ]
    if not clauses:
        return []
    return _scene_action_tags({"narrative": "\n".join(clauses)})


def _multi_actor_sentences(scene: Mapping[str, object]) -> list[str]:
    """多人逐人分述：每人一条，**位置锚 + 该人自己的可见事实与动作**。

    ⚠ 动作即使相同也**分别写**——两条句子各自带位置，读者不会误认为同一人。
    自己取不到独立证据的主体（真机 L3 常见：窗口里只有集体句）用**集体句动作**补齐，
    而不是丢成 `is present in frame`（用户实锤：兜底成稿里这两名角色等于消失）。
    有独立证据的主体**不许**再吃集体动作——否则会把两人的动作误配给第三人。
    """
    sentences: list[str] = []
    details = _actor_visual_details(scene)
    # 集体句动作只在**全员都取不到独立证据**时才启用。真机 L3 实锤：集体句的语义主体是
    # 两名女主角，宾语「他」指男主 ⇒ 按人补齐会把女性体位动作塞给男主。全员无证据才说明
    # 窗口里只剩集体句，此时逐人各写一遍＝用户口径（「动作一样也要各写一遍」）。
    collective = (
        _collective_action_tags(scene)
        if all(not actions for _actor, _facts, actions in details) else []
    )
    for index, (_actor, facts, actions) in enumerate(details):
        position = _MULTI_ACTOR_POSITIONS[
            index if index < len(_MULTI_ACTOR_POSITIONS) else -1
        ]
        label = f"The primary adult character {position}" if index == 0 else (
            f"The second adult character {position}" if index == 1
            else f"An additional adult character {position}"
        )
        parts: list[str] = []
        if facts:
            parts.append("is visibly defined by " + ", ".join(facts))
        effective_actions = actions or collective
        # 第 25 刀：接触类（「主角的手放在她臀部上」）用 `has …`，动作类用 `performs …`。
        # 无接触 tag 时输出与第 24 刀**逐字节一致**（零回归）。
        contact = [tag for tag in effective_actions if tag in _CONTACT_TAGS]
        performed = [tag for tag in effective_actions if tag not in _CONTACT_TAGS]
        if contact:
            # 接触类是**状态**，不是动作 ⇒ `has A and B`（不用 `then` 串，读起来才是「她身上有一只手」）
            parts.append("has " + " and ".join(contact))
        if performed:
            parts.append("performs " + ", then ".join(performed))
        if not parts:
            # 无事实可写也必须保留**位置锚**，否则这一名主体在画面里等于消失
            parts.append("is present in frame")
        sentences.append(label + " " + " and ".join(parts) + ".")
    return sentences


# 画面主体数的英文措辞（2026-09-20 第 24 刀）。真机 L3 是**三主体帧**，兜底却仍写
# `two adult women` / `two-character`（双口径残留）。2 人帧**逐字节保留**旧措辞＝零回归。
_ACTOR_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _multi_actor_count(scene: Mapping[str, object]) -> tuple[int, str]:
    """返回 (画面主体数, 英文数词)；数词最多到 five，再多一律 several。"""
    count = len(_contract_subject_names(scene))
    return count, _ACTOR_COUNT_WORDS.get(count, "several")


def _fallback_multi_actor_sentences(scene: Mapping[str, object]) -> list[str]:
    """兜底模板的多人分支内容——与多人契约共用同一条「画面主体数」口径。

    `_multi_actor_sentences` 只看 `actors`（取材窗口解析出的**在场**角色）。优化项③
    把**契约**改成按画面主体数判之后，若兜底仍走 actors，就出现自相矛盾的**双口径**：
    契约放行单人定格成稿，兜底却硬写 `two adult women` / `2girls, two-shot` /
    `The two adult characters is …`（2026-09-19 真机实锤：单人帧被观众席式地写成双人）。

    这里统一到 `_contract_subject_names`：一旦有正向证据（同轮计划 `subjects` /
    判据 `frame_subjects`）说这帧只一个主体，返回空表 ⇒ 四个兜底模板全部落到单人分支。

    无正向证据时 `_contract_subject_names` 退回 `actors`，本函数逐字节等于
    `_multi_actor_sentences` —— 这是零回归前提。
    """
    if len(_contract_subject_names(scene)) < 2:
        return []
    return _multi_actor_sentences(scene)


def _mapped_facts_from(source: str) -> list[str]:
    facts = list(dict.fromkeys(
        phrase for source_pattern, phrase, _ in _VISUAL_FACT_RULES
        if re.search(source_pattern, source or "")
    ))
    if "torn remnants of a red lotus patterned robe" in facts:
        facts = [fact for fact in facts if fact != "a red lotus patterned robe"]
    return facts


def _mapped_wardrobe_facts_from(source: str) -> list[str]:
    wardrobe_words = re.compile(
        r"\b(?:robe|dress|skirt|stockings?|socks?|clothing|garments?|neckline|slit|nude|naked|emblems?|talisman)\b",
        re.I,
    )
    return [fact for fact in _mapped_facts_from(source) if wardrobe_words.search(fact)]


def _missing_visual_facts(prompt: str, scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    lowered = prompt.lower()
    missing = list(dict.fromkeys(
        phrase for source_pattern, phrase, output_pattern in _VISUAL_FACT_RULES
        if re.search(source_pattern, source) and not re.search(output_pattern, lowered, re.I)
    ))
    # T1：事件性必现事实（体液/睡眠）走自己的 narrative 源，与外观类共用「漏」的报错出口
    # ⇒ `_visual_fact_errors` 报「未落实」、触发修补重试；窗口无该事实时返回空表（零回归）。
    missing.extend(
        phrase for phrase in _missing_event_facts(prompt, scene) if phrase not in missing
    )
    return missing


def _visual_fact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    # T1：事件性必现事实（体液/睡眠）走自己的 narrative 源，**不受 `source` 是否为空影响**——
    # 真机该帧 `wardrobe` 为空 ⇒ 旧实现在此早退 ⇒ 事件事实连报错机会都没有。
    event_missing = _missing_event_facts(prompt, scene)
    if not source.strip() and not event_missing:
        return []
    errors: list[str] = []
    if source.strip() and _VAGUE_IDENTITY_RE.search(prompt):
        errors.append("角色段落使用了空泛身份锁，必须改写为条目中的具体可视外貌与当前服装")
    errors.extend(
        f"角色条目中的可视事实未落实：{phrase}"
        for phrase in _missing_visual_facts(prompt, scene)
    )
    for name in _character_names(scene):
        if name.isascii() and _replace_character_name(prompt, name, "") != prompt:
            errors.append("最终提示词包含角色姓名；姓名只能用于关联外貌条目")
            break
    return errors


# ---------------------------------------------------------------------------
# 无出处视觉装置闸（第 22 刀，2026-09-20 生产链实锤 ★1）
#
# 现场：L1·anima 取材窗口 44 字、原文没有任何镜面，成稿却写 `wall mirror reflection`
# ＋ 文段 `Behind her, a wall mirror catches a blurred reflection of her face`。
# 与 `_visual_fact_errors` 的分工（两者同层、互补，**方向相反**）：
#   `_visual_fact_errors` 管**漏**——条目里有的可视事实必须落实；
#   本闸管**加**——画面事实里没有的装置不得入画。
# 此前只有「漏」有机械兜底，「加」只写在 system 提示里（【事实边界】），模型忽略后无人拦。
#
# 闸口只按**类**管（反光面 / 发光体 / 显示面 / 画框 / 水面），不列禁词表：
#   ① 出处判定看**输入画面事实**（外貌/服装/正文/地点）⇒ 原文写了镜子就放行，闸门管
#      「无出处」，不管词本身；
#   ② 比喻式提及（`the black lacquer lid acts as a dark mirror`）不引入新物件 ⇒ 放行；
#   ③ 三档共用同一份表（与 `_visual_fact_errors` 同层调用），避免只治一档。
# ⚠ 与 `_body_errors` 的关系：`_body_errors` 用空 scene 调用 ⇒ 出处来源为空 ⇒ 本闸必然
# 产出空表 ⇒ 优化项⑤ 的补丁门槛（判「稿子本体能不能用」）逐字节不受影响。
# ---------------------------------------------------------------------------
_UNSOURCED_DEVICE_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "镜面或其他反光面",
        # ⚠ 必须**带限定词的名词短语**（2026-09-20 真机实测误报）：L3·niji 写
        # `the primary adult character mirrors the same pose`——`mirrors` 是**动词**（同一姿势），
        # 不是新镜子。裸 `\bmirrors?\b` 会把它判红并白跑一次 repair ⇒ 用限定词锚住「作为物件被引入」。
        # ⚠ 第 30 刀（2026-09-21 真机实锤：第 22 刀那层限定词**没打穿**）：限定词与 `mirrors?` 之间
        #   允许 `(?:\w+\s+){0,2}` ⇒ 真机原句 `…on the right mirrors the same pose` 恰好**夹一个名词**
        #   （`right`／`left`），旧式照样命中 ⇒ L3·natural_language 首轮＋repair **各红一次**
        #   （零 LLM 复跑实证：`the right mirrors` / `the left mirrors` 双命中）。
        #   ⇒ 再补一层**宾语侧否定前瞻**：`mirror(s)` 作**动词**时必然接宾语
        #   （`mirrors the same pose` / `mirror each other`）；作**名词**时后面接谓语/介词/句读
        #   （`a wall mirror catches` / `the mirror on the wall` / `… the mirror.`）。
        #   `each other` **单列**：`the mirror each morning` 里的 `each` 只是状语，不能连坐（保召回）。
        #   ⚠ `this|that` **刻意不进宾语清单**：它们是**关系从句**的开头（`a wall mirror that catches …`
        #     是名词式，必须判红），而 `mirrors that pose`（动词＋指示词宾语）在成稿里几乎不出现
        #     ⇒ 两害相权保**召回**（放走编造镜面＝★1 原病，比白跑一次 repair 严重）。
        # 2026-09-21 第 31 刀（用户实锤：两处漏判 + 一处误报，零 LLM 已复现）：
        #   漏判① **裸名词 tag**（`wall mirror reflection`）没有限定词 ⇒ 旧式 `det + …` 整条
        #         不入闸。定案：限定词**改为可选** —— 裸名词短语同样是「把镜面引入了画面」，
        #         按「漏＝编造」优先于「多＝白跑一轮」的红线口径，必须收进来。
        #   漏判② `a full-length mirror catches …`：`full-length` 带连字符，旧式 `(?:\w+\s+)`
        #         被 `-length` 卡住 ⇒ 跨不过去、整条不入闸。定案：词形放宽为 `[\w-]+`。
        #   ⚠ 误报 `a mirror pose`（`mirror` 作**定语**）本轮**刻意不治**：它在词面上与
        #     「限定词 + mirror + 名词」的裸名词真串（`a wall mirror reflection`）**完全同形**，
        #     要治只能二选一——给 `mirror` 的接续名词开槽（＝禁列禁词表，踩红线），或要求后接
        #     谓词/介词（＝掉召回）。按红线「宁可白跑一轮 repair，不可放走编造镜面」⇒ 保召回。
        r"\b(?:(?:a|an|the|his|her|their|its|this|that|another|one|two|three)\s+)?"
        r"(?:[\w-]+\s+){0,2}"
        r"mirrors?\b(?!-like)"
        r"(?!\s+(?:a|an|the|another|his|her|their|its|my|your|our|him|them|it|us|me|you"
        r"|each\s+other|one\s+another)\b)|\bmirror image\b|"
        r"\breflecting (?:surface|glass|pool|water)\b",
        r"镜|倒影|映出|映着|反光|mirror|reflect",
    ),
    (
        "灯具或烛火光源",
        r"\b(?:lamps?|bulbs?|lanterns?|chandeliers?|sconces?|candles?|candlesticks?|torches?|"
        r"streetlights?|neon sign|fluorescent (?:light|lamp|tube))\b",
        r"灯|烛|火把|火炬|焰|萤|灯泡|lamp|bulb|lantern|candle|torch",
    ),
    (
        "屏幕或显示设备",
        r"\b(?:monitors?|screens?|televisions?|tv)\b",
        r"屏|显示器|电视|电脑|监控|monitor|screen|display",
    ),
    (
        "画框或画像",
        r"\b(?:(?:picture|photo|portrait|gilded|ornate) frame|hanging portrait|"
        r"painted portrait)\b",
        r"画框|相框|画像|画作|遗像|挂画|肖像",
    ),
    (
        "水面倒影",
        r"\b(?:reflecting pool|puddle|still water|water mirror)\b",
        r"水面|水洼|积水|水塘|池塘|湖|河|pond|lake|river",
    ),
)


# 比喻/借代标记：装置名词落在这种从句里时，说的是**已有物件的一种读法**，不是新增道具。
_DEVICE_SIMILE_RE = re.compile(
    r"\b(?:acts? as|reads as|serves as|functions as|doubles as|becomes|turns into|"
    r"like|as if|as though|almost|resembles)\b",
    re.I,
)


def _device_source(scene: Mapping[str, object]) -> str:
    """装置的**出处来源**＝输入画面事实 ＋ 地点。

    刻意复用 `_current_visual_source`（同一份事实口径，不另抄一份正则），只额外补
    `narrative` 与 `locale`：`_current_visual_source` 只在服装变化时才带正文，而装置
    出处必须**无条件**看正文（「她站在镜子前」正是这种事实），故这里显式再并一次
    （重复拼接只是冗余、不影响判定）。`locale` 是房间/街巷事实的真源，装置常写在那里。
    """
    return "\n".join(filter(None, (
        _current_visual_source(scene),
        str(scene.get("narrative") or ""),
        str(scene.get("locale") or ""),
    )))


def _device_hits_are_simile(prompt: str, pattern: re.Pattern[str]) -> bool:
    """逐处判定装置提及是否全为比喻式：只要有一处是实指，就按实指处理。"""
    hits = list(pattern.finditer(prompt))
    for match in hits:
        start = max(prompt.rfind(sep, 0, match.start()) for sep in ".;!?,\n")
        if not _DEVICE_SIMILE_RE.search(prompt[start + 1:match.start()]):
            return False
    return bool(hits)


# ═══════════════════════════════════════════════════════════════════════════════
# 第 70 刀（2026-09-29 · 与第 69 刀配套）：**隔着布料的器官接触**出处闸
#
# 第 69 刀放开了「隔着衣物接触」的写法（用户原话：「肉棒贴在女角色的小穴口隔着内裤布料
# 磨蹭、女角色内裤湿透透明」）。**放开即须配闸**：这类描述最容易被写到没有出处的帧上，
# 而既有的编造闸（`_UNSOURCED_DEVICE_RULES`）**只管道具、不含身体部位与接触形态**
# ⇒ 本刀按道具闸**同构**补上。
# ⚠ 与道具闸同一条红线：**宁可白跑一轮 repair，不可放走编造**。
# ⚠ 出处侧用**两个前瞻的合取**（「有隔绝/透湿/磨蹭」**且**「有布料」），
#   避免「只要有衣服词就放行」的空转（那样等于没有闸）。
# ⚠ 正则**刻意零反斜杠**（`(?<![A-Za-z])` 替 `\b`、`(?s)` 替 `[\s\S]`）。
_UNSOURCED_CONTACT_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "隔着布料的器官接触",
        r"(?<![A-Za-z])(?:bulge|bulging|outline|erection|erect|penis|shaft|groin|crotch|manhood)"
        r"(?![A-Za-z])[^.]{0,80}?(?<![A-Za-z])(?:through|under|beneath|against|behind)"
        r"(?![A-Za-z])[^.]{0,40}?"
        r"(?<![A-Za-z])(?:cloth|fabric|garment|clothes|clothing|underwear|underpants|"
        r"loincloth|robe|dress|skirt|trousers|pants)(?![A-Za-z])"
        r"|"
        r"(?<![A-Za-z])(?:through|under|beneath|against)(?![A-Za-z])[^.]{0,40}?"
        r"(?<![A-Za-z])(?:cloth|fabric|garment|underwear|robe|loincloth)(?![A-Za-z])"
        r"[^.]{0,60}?(?<![A-Za-z])(?:bulge|outline|erection|penis|shaft|groin|crotch)"
        r"(?![A-Za-z])",
        r"(?s)(?=.*(?:隔|透|湿|渗|洇|蹭|磨|贴|压|碾|rub|grind|press|wet|through))"
        r"(?=.*(?:布|衣|裤|袍|裙|cloth|fabric|robe|underwear|loincloth|garment))",
    ),
)


_UNSOURCED_CONTACT_MESSAGE = (
    "提示词写入了画面事实中没有出处的「隔着布料的器官接触」：{label}；"
    "这类接触只能在画面事实写明时才写（宁可少写也不编造）"
)


def _unsourced_contact_hits(
    prompt: str, scene: Mapping[str, object],
) -> list[tuple[str, re.Pattern[str]]]:
    """列出成稿里**没有出处**的「隔着布料的器官接触」（第 70 刀，与道具闸同构）。

    出处来源复用 `_device_source`（同一份事实口径，不另抄一份正则）。
    """
    source = _device_source(scene)
    if not source.strip():
        return []
    hits: list[tuple[str, re.Pattern[str]]] = []
    for label, prompt_pattern, source_pattern in _UNSOURCED_CONTACT_RULES:
        pattern = re.compile(prompt_pattern, re.I)
        if not pattern.search(prompt):
            continue
        if re.search(source_pattern, source, re.I):
            continue
        hits.append((label, pattern))
    return hits


def _unsourced_contact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """画面事实里没有出处的「隔着布料的器官接触」（第 70 刀）。"""
    return [
        _UNSOURCED_CONTACT_MESSAGE.format(label=label)
        for label, _pattern in _unsourced_contact_hits(prompt, scene)
    ]


_UNSOURCED_DEVICE_MESSAGE = (
    "提示词添加了画面事实中没有出处的视觉装置：{label}；"
    "画面里没有的装置不得入画（宁可少写也不编造）"
)


def _unsourced_device_hits(
    prompt: str, scene: Mapping[str, object],
) -> list[tuple[str, re.Pattern[str]]]:
    """列出成稿里**没有出处**的视觉装置（第 33 刀抽出：判错与「摘掉装置」共用同一份判定）。

    返回 `(类名, 已编译正则)`，判定口径与旧实现逐字相同（**出处放行**／**比喻放行**两个豁免都在）。
    """
    source = _device_source(scene)
    if not source.strip():
        return []
    hits: list[tuple[str, re.Pattern[str]]] = []
    for label, prompt_pattern, source_pattern in _UNSOURCED_DEVICE_RULES:
        pattern = re.compile(prompt_pattern, re.I)
        if not pattern.search(prompt):
            continue
        if re.search(source_pattern, source, re.I):
            continue
        if _device_hits_are_simile(prompt, pattern):
            continue
        hits.append((label, pattern))
    return hits


def _unsourced_visual_device_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """画面事实里没有出处的视觉装置：与 `_visual_fact_errors` 同层、方向互补。"""
    return [
        _UNSOURCED_DEVICE_MESSAGE.format(label=label)
        for label, _pattern in _unsourced_device_hits(prompt, scene)
    ]


def _device_prune_is_token_list(profile: str, index: int) -> bool:
    """这一行是**逗号 tag 串**（可按 token 摘）还是**英文散文**（只能按句摘）？"""
    if profile in ("anima_tags", "krea2"):
        return index == 0
    # niji 前三段都是逗号 tag 串；natural_language 整篇是散文。
    return profile == "niji_sections"


def _drop_unsourced_device_mentions(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> str:
    """把「画面事实里没有出处」的视觉装置从成稿里**摘掉**（第 33 刀 · 第 14 项补救）。

    为什么不做「整稿作废」：`_patch_failed_prompt` 的立意就是**别把模型的真实画面换成词表模板**
    （第 11／12 项用户实锤）；可凭空装置又绝不能随补丁交付（红线 22「宁可白跑一轮 repair，
    不可放走编造镜面」）。两头都要占 ⇒ **只摘装置提及**：
      · tag 串（anima／krea2 首行、niji 前三段）⇒ 按**逗号 token** 摘（tag 是可枚举粒度）；
      · 散文行（anima／krea2 第二行、natural_language 全文）⇒ 按**句子**摘（整句原文保留/丢弃，
        不做词级手术，免得把句子改到语法破碎）。
    摘完仍判红、或成稿已不成立（`_body_errors` 再红）⇒ 调用方照旧作废 ⇒ 失败方向仍是 fail-closed。
    """
    hits = _unsourced_device_hits(prompt, scene)
    if not hits:
        return prompt
    patterns = [pattern for _label, pattern in hits]
    kept_lines: list[str] = []
    for index, line in enumerate(prompt.split("\n")):
        if not line.strip() or line.lstrip().startswith("--"):
            kept_lines.append(line)
            continue
        if _device_prune_is_token_list(profile, index):
            tokens = [token.strip() for token in line.split(",") if token.strip()]
            kept = [token for token in tokens if not any(p.search(token) for p in patterns)]
            suffix = "," if line.rstrip().endswith(",") else ""
            kept_lines.append(", ".join(kept) + suffix)
            continue
        sentences = [seg for seg in re.split(r"(?<=[.!?])\s+", line) if seg.strip()]
        kept = [seg for seg in sentences if not any(p.search(seg) for p in patterns)]
        kept_lines.append(" ".join(seg.strip() for seg in kept))
    return "\n".join(kept_lines)


def _contract_subject_names(scene: Mapping[str, object]) -> list[str]:
    """多人契约的「画面主体名集合」——决定要不要按多人成稿。

    用户口径（2026-09-19 定案）：一律按**画面主体数**判，**不按角色表人数、也不按在场人数**。
    优先级：
      ① 同轮计划 `subjects`（模型已声明的本帧主体，身份真源）；
      ② 判据档下传的 `frame_subjects`（高潮点定位判据判出的本帧主体名集合）；
      ③ 两者都缺 ⇒ **无正向证据**，退回 `actors`（取材窗口解析出的在场角色）。

    ③ 是**零回归前提**：旧行为即「只看 actors」，没有正向证据时逐字节不变。
    反过来，一旦有正向证据（判据/计划说这帧只一个人），契约就必须听它——否则取材窗口里
    被提及的旁观者会把单人定格帧推成多人帧，模型写对的单人成稿反被判死，甚至被硬塞
    「second adult character」污染画面（2026-09-19 真机实锤）。
    """
    narrator = _first_person_narrator(scene)

    def _drop_narrator(names: list[str]) -> list[str]:
        if not narrator:
            return names
        return [name for name in names if name != narrator]

    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        planned = [
            str(item.get("name") or "").strip() for item in subjects
            if isinstance(item, Mapping)
        ]
        planned = [name for name in planned if name]
        if planned:
            # 第 37 刀：① 计划 `subjects` 原本**早返回、不过人称滤** ⇒ 第一人称下叙事者
            # 仍被算成本帧主体（② `frame_subjects` 同理）。判据与 ③ 完全一致。
            return list(dict.fromkeys(_drop_narrator(planned)))
    frame = scene.get("frame_subjects")
    if isinstance(frame, list):
        named = [str(name).strip() for name in frame if str(name).strip()]
        if named:
            return list(dict.fromkeys(_drop_narrator(named)))
    actors = scene.get("actors")
    if not isinstance(actors, list):
        return []
    names = [str(name).strip() for name in actors if str(name).strip()]
    # 2026-09-20 用户定案：第一人称下叙事者不占画面主体数。③ 这条回落路径原本只看 actors，
    # 叙事者若在角色表里就会被算回来 ⇒ 收紧**只在一半路径生效**（第 37 刀把 ①/② 一并补齐）。
    # 只在 `narrator` 键**存在**时剔 ⇒ 老调用方（无该键）逐字节等于旧行为。
    return list(dict.fromkeys(_drop_narrator(names)))


# 多人契约的「语义分述」判据（优化项⑨ 句式放宽，2026-09-19）：
# 契约只要求「两名画面主体被**分开描述**」，不强制模板字样 `primary/second adult character`。
# 只认**词面**句式：命中一对互斥指称，且每个指称都与角色名词绑定（不做语义理解）。
_ACTOR_NOUN_PATTERN = (
    r"(?:woman|women|man|men|girl|girls|boy|boys|character|characters|"
    r"figure|figures|person|people|adult|adults)"
)


# 互斥指称对：同一对的两侧都命中，才说明两个主体被分开指称。
_ACTOR_CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    ("left", "right"),
    ("leftmost", "rightmost"),
    ("first", "second"),
    ("one", "other"),
    ("foreground", "background"),
    ("front", "back"),
    ("nearer", "farther"),
    ("closer", "farther"),
    ("upper", "lower"),
    ("former", "latter"),
)


def _actor_designated_by(prompt: str, marker: str) -> bool:
    """提示词是否用 `marker` 单独指称了一个画面主体（词面判据，不做语义理解）。

    只认三种通用句式，且都必须与角色名词直接绑定（避免把「台灯在左边」当成角色）：
      ① 序数/差集前缀：`first woman` / `the other man` / `one of the women`；
      ② 角色名词 + 方位介词短语：`the woman on the left` / `the man in the background`；
      ③ 方位前缀：`the left woman` / `the nearer figure`。
    """
    noun = _ACTOR_NOUN_PATTERN
    patterns = (
        rf"\b{marker}\b\s+(?:of\s+the\s+)?{noun}\b",
        rf"\b{noun}\b(?:\s+\w+){{0,1}}\s+(?:on|to|at|in)\s+"
        rf"(?:the\s+|his\s+|her\s+|their\s+)?{marker}\b",
        rf"\bthe\s+{marker}\s+{noun}\b",
    )
    return any(re.search(pattern, prompt or "", re.I) for pattern in patterns)


# 外貌／衣着区分式指称（第 44 刀 · 2026-09-22 用户定案「分述判据只认位置词，按你的修复来」）：
# 真机 B2（2026-09-22）成稿按 42 刀·D 的引导写成
#   `The blonde woman in the hollowed-out white wedding dress …`
#   `the silver-haired woman in the black bunny suit …`
# 两名主体**确实被分开指称**（blonde vs silver-haired），却因**一个位置词都没有**
# ⇒ `_ACTOR_CONTRAST_PAIRS` 一对都命中不了 ⇒ 判红 2 条 ⇒ 白烧一次 repair（2/2 复现）。
# ⚠ 与既有位置锚判据同族：**只判词面、不引词表**，靠「定语是否绑在角色名词上」这一句式特征，
#   不去列任何外貌词。
# ⚠ 与角色形象来源完全无关：本组判据只读 `prompt` 字符串，**不从这里反推／回写任何外貌事实**，
#   角色外貌仍一律来自 `scene_illustration` 的**三源融合（剧情表现 > 表格补充 > 角色条目，
#   第 36 刀）**。
_MODIFIER_TOKEN = r"[A-Za-z][A-Za-z'-]+"


# ⚠ ② 的介词是**功能词白名单**（不是外貌词表）：只收「绑定在身上之物」的介词短语，
#   刻意**排除** `in/at/on` —— `in the corridor`、`in the room` 这类是**地点**，
#   纳入就会把场景方位当成角色分述（保精度）。
_APPEARANCE_PREP = r"(?:with|wearing|dressed\s+in|clad\s+in|carrying|holding)"


_APPEARANCE_DESIGNATION_RES: tuple[re.Pattern[str], ...] = (
    # ① 定语在名词前：`the blonde woman` / `a silver-haired girl`
    re.compile(rf"\b(?:the|a|an)\s+({_MODIFIER_TOKEN})\s+{_ACTOR_NOUN_PATTERN}\b", re.I),
    # ② 定语在名词后（衣着／携带／发色）：`the woman with glossy jet-black hair` /
    #   `a girl wearing a black bunny suit` / `the blonde woman wearing a white wedding dress`
    #   （名词前可再带一个定语；短语本体最多 3 个实词，避免把后面的动词吞进来）
    re.compile(
        rf"\b(?:the|a|an)\s+(?:{_MODIFIER_TOKEN}\s+)?{_ACTOR_NOUN_PATTERN}\s+"
        rf"{_APPEARANCE_PREP}\s+"
        rf"(?:the\s+|a\s+|an\s+|his\s+|her\s+|their\s+)?"
        rf"((?:{_MODIFIER_TOKEN})(?:\s+{_MODIFIER_TOKEN}){{0,2}})",
        re.I,
    ),
)


def _appearance_designations(prompt: str) -> set[str]:
    """提取「定语 + 角色名词」式指称（**纯词面、无词表**）；只留**彼此无共享实词**的指称。

    只做**句式**判定：
      · ① 前置定语：取紧贴角色名词前的那**一个** token（`silver-haired woman` → `silver-haired`）；
      · ② 后置短语：取介词短语本体（`with glossy jet-black hair` → `glossy jet-black hair`，≤3 词）。
    ⚠ 两道保精度闸（都靠**结构**、不靠词表）：
      ① ② 的实词数必须 **≥2**（`the woman with a scar` 这种单词短语不算外貌指称）；
      ② 只保留**与其它指称无共享实词**的指称 —— `the woman with blonde hair` 与
         `the girl with blonde hair`（同一个人写了两遍）共享 `blonde/hair` ⇒ 判为同一指称。
    ⚠ 与角色形象来源无关：结果只由 `prompt` 决定，绝不反推／回写任何外貌事实。
    """
    entries: list[tuple[str, frozenset[str]]] = []
    for index, pattern in enumerate(_APPEARANCE_DESIGNATION_RES):
        for match in pattern.finditer(prompt or ""):
            phrase = " ".join(match.group(1).split()).lower()
            tokens = frozenset(token for token in re.split(r"\W+", phrase) if token)
            if not tokens:
                continue
            if index == 1 and len(tokens) < 2:
                continue
            entries.append((phrase, tokens))
    return {
        phrase for position, (phrase, tokens) in enumerate(entries)
        if all(
            tokens.isdisjoint(other_tokens)
            for other_position, (_other, other_tokens) in enumerate(entries)
            if other_position != position
        )
    }


def _has_separate_actor_designations(prompt: str) -> bool:
    """多人契约的「语义分述」等价物（优化项⑨ 句式放宽 2026-09-19；第 44 刀扩外貌锚 2026-09-22）。

    旧口径只认模板字样 `primary/second adult character|woman`；真机 L3·natural 成稿用
    `the woman on the left … the woman on the right …` 已把两名主体**分开描述**，却因不认
    模板句式被判红 2 条（`docs/illustration-prompt-delivery-2026-09-19.md` §3 第 3 项）
    ⇒ 契约验收的是**措辞**而不是**分述**。

    这里把「命中一对互斥指称、且各自绑定角色名词」视为已单独描述每个主体。
    **只放宽句式、不放松人数**：是否走多人契约仍由 `_contract_subject_names` 决定；
    指称对必须互斥（只写「左边」不算），且必须绑定角色名词（纯场景方位不算）。

    **第 44 刀（2026-09-22 用户定案）**：再加一路**外貌／衣着区分式指称** —— 出现
    **≥2 个互不相同**的「定语 + 角色名词」式指称（`_appearance_designations`）即视为已分述。
    理由：42 刀·D 明确**要求**模型用「可辨识身份（发色／发型／瞳色／体型）」区分两名主体，
    而位置锚词表认不出这种写法 ⇒ **我们要求的写法判不过我们自己的契约**。
    ⚠ 三条边界不动：① 仍**只判词面、不引词表**；② **不放松人数**（画面主体 <2 时
    `_multi_actor_contract_errors` 整段不跑；人多不多、人称口径全按 `_contract_subject_names`）；
    ③ **不碰角色形象来源** —— 本函数只读 `prompt` 字符串，角色外貌仍一律来自
    `scene_illustration` 的**三源融合（剧情表现 > 表格补充 > 角色条目，第 36 刀）**，
    绝不从提示词反推或回写外貌事实。
    """
    if any(
        _actor_designated_by(prompt, first) and _actor_designated_by(prompt, second)
        for first, second in _ACTOR_CONTRAST_PAIRS
    ):
        return True
    return len(_appearance_designations(prompt)) >= 2


def _multi_actor_contract_errors(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> list[str]:
    subject_count = len(_contract_subject_names(scene))
    if subject_count < 2:
        return []
    errors: list[str] = []
    if not _has_separate_actor_designations(prompt):
        if not re.search(r"\bprimary adult (?:character|woman)\b", prompt, re.I):
            errors.append("多人提示词必须单独描述第一角色")
        if not re.search(r"\bsecond adult (?:character|woman)\b", prompt, re.I):
            errors.append("多人提示词必须单独描述第二角色")
    if profile == "anima_tags":
        # 第 45 刀（2026-09-22 用户定案）：**删掉「必须包含 2girls」下限**。
        # 用户口径原话：「性别的判断是根据剧情来的」—— 人数与性别由**剧情实际**决定
        # （剧情里几人、什么性别，模型照着写），不得用「画面主体 ≥2 ⇒ 必须 `2girls`」
        # 这种机械规则反推性别。
        # 真机事故（2026-09-22 12:28，slot -47）：第一人称单女帧（师尊在榻边掖被子、
        # 视线落到主人公脸上）被这条下限逼出 `2girls` ⇒ 模型凭空多画一个女人；
        # 且 2 主体帧的区间 `2 ≤ girls ≤ 2` 会把写对的 `1girl, 1boy`（男主＋1 女）判死。
        # 保留**上界**：人数词不得超过画面主体数（仍与 `_contract_subject_names` 同口径）。
        # ⚠ boys 族刻意不进契约（见 `_subject_count_totals` 上方注释）：它的归属是
        # 归一化侧 40 刀的「剔孤儿」。
        girls, _boys = _subject_count_totals(prompt)
        if girls > subject_count:
            errors.append("Anima 提示词人数词不得超过画面主体数")
    return errors


_LENGTH_WARNING = "英文单词数必须为80到500"


def _split_length_warnings(errors: list[str]) -> tuple[list[str], list[str]]:
    """把词数校验从「触发重写」降级为「警告放行」（2026-08-30 成本实锤：一次 repair
    = 一次携带完整防拦截预设的调用 ≈$0.2-0.3；词数偏短不影响提示词合法性，
    长度只用于引导，硬卡它只会多烧一次调用）。返回（需修复错误, 警告）。"""
    warnings = [e for e in errors if e == _LENGTH_WARNING]
    return [e for e in errors if e != _LENGTH_WARNING], warnings


# 「数据视野朝向」的确定性数据源：画面事实里**接触点落在哪个高度带**。
# 用户示例「压在师傅的上半身，肉棒从镜头下半区域露出压在师傅嘴上」⇒ 接触点在**口/面**
# ⇒ 视野上限＝**上半身**（不是全身）。
_VIEW_FACE_CONTACT_RE = re.compile(
    # 第 71 刀 ②（2026-09-29 真机 + 探针双实锤）：本表只收**不可与下身复合**的上半身部位写法。
    # 旧式含裸 `口`／`唇`／`面` 三个**子串**，真机把下列词误当面部接触：
    #   `穴口`／`缝口` 的「口」、`阴唇` 的「唇」、`布面`／`褥面` 的「面」
    # ⇒ `face=True` ⇒ 取景上限锁成「上半身」——而那一帧的画面事实全是下半身动作
    # ⇒ 模型为了把器官接触塞进上半身，**把接触点挪到了脸上**
    #   （探针复现：事实「肉棒贴着阴唇碾」写成 `penis on face` / `glans pressing her cheek`）。
    # ⚠ 裸「面」一并去掉（改 `面部`／`面颊`）：`布面`／`褥面` 在真机窗口里是高频词。
    #   代价＝「压在面上」这类写法不再命中 ⇒ 退回「不给上限」（宁缺勿猜），可接受。
    # ⚠ `口`／`唇` **保留但加负向后顾**（首版把裸字整个删掉 ⇒ 真机/既有锁双双回归：
    #   「压在她**唇**上」「朱**唇**饱满」是**真上半身接触**，删掉就丢召回）。
    #   要排除的只是**下身复合词**：穴口／缝口／伤口／创口／裂口／门口／洞口、阴唇。
    r"(?:嘴|嘴角|嘴唇|唇角|口腔|口交|口水|"
    r"(?<!穴)(?<!缝)(?<!伤)(?<!创)(?<!裂)(?<!门)(?<!洞)口|"
    r"(?<!阴)唇|"
    r"舌|脸颊|脸|面部|面容|面颊|"
    r"鼻|下颌|下巴|颈|脖子|锁骨|胸|乳|肩|咽喉|喉)",
)


# 接触点落在**腰部以下** ⇒ 视野下移但不是推远（仍保持近距离）。
# ⚠ 第 71 刀 ②：裸 `股` 改为复合词 —— `一股接一股`／`那股` 会把上半身帧误判成下半身。
_VIEW_LOWER_CONTACT_RE = re.compile(
    # ⚠ **不变式**：`_VIEW_LOWER_STRICT_RE` ⊆ 本表 —— 否则「下身专有词压过面命中」之后
    #   本表却认不出它 ⇒ 取景上限掉成「不给上限」（第 71 刀自测抓到 `缝口` 这个洞）。
    r"(?:下体|私处|阴|穴|臀|屁股|股间|股沟|胯|裆|腿|脚|足|膝|大腿|小腿|腰腹|"
    r"腿根|膝弯|会阴|缝口|龟头|肉棒)",
)


# 第 71 刀 ③：**下身专有词** —— 命中即**压过**上面的面命中。
# 口径：只收「接触点不可能落在上半身」的词（`阴唇`／`穴口`／`缝口`／`胯`／`裆` …），
# **刻意不收** `臀`／`大腿`／`腿` 这类**可与面部接触同现**的部位
# （反例：「她的大腿搭在他肩上」＋「肉棒压在她脸上」两条同真 ⇒ 不该被压成下半身）。
_VIEW_LOWER_STRICT_RE = re.compile(
    r"(?:阴唇|阴部|阴户|阴道|阴蒂|阴阜|穴口|穴内|缝口|会阴|股间|腿根|胯|裆)",
)


def _view_face_frame(blob: str) -> bool:
    """本帧是否「**上半身**近身帧」（第 57 刀 ③ 口径 ＋ 第 71 刀 ③ 例外）。

    **三处消费者共用同一真源**（`_first_person_framing`／`_first_person_frame_filter`／
    `_first_person_upper_frame`）—— 口径分叉就会出现「提示词声明上半身、筛分却按下半身」
    的自相矛盾（第 57 刀 ③ 的注释里记着这条）。
    """
    if not blob.strip():
        return False
    if _VIEW_LOWER_STRICT_RE.search(blob):
        # 下身专有词 ⇒ 压过面命中（第 71 刀 ③）。
        return False
    return bool(_VIEW_FACE_CONTACT_RE.search(blob))


# 第三方全景机位措辞：**仅在「近身接触帧」**下禁用（它们隐含「有一个站在旁边看的观察者」，
# 与「镜头＝叙事者眼睛」自相矛盾）。
# ⚠ 2026-09-24 第 56 刀（用户第二次纠正）：这些措辞**不得在纯风景/环境帧**里禁用 ——
#   用户原话：「不行，**你不能限制死特写中景**，因为我开着第一人称，**有时候如果只是描写
#   风景**，那被限制成近景/中景**基本彰显不了风景全貌**」。
#   ⇒ 风景帧**没有接触锚点** ⇒ 本来就不该进近身收束态。
_VIEWPOINT_FORBIDDEN_CAMERA = (
    "from behind", "three-quarter back view", "rear view", "back view",
    "full body", "wide shot", "long shot", "full shot", "medium wide shot",
    "environmental composition", "god's eye view", "bird's eye view",
    "looking down at face", "from above",
)


# ⚠ 上述禁表里**只有「俯视/背后」这一半**是身份矛盾（上帝视角 ＝ 旁观者，恒禁）；
#   「full body / wide shot / long shot」这一半是**取景矛盾**，只在近身帧成立。
#   ⇒ 风景帧只用「恒禁那一半」（见 `_first_person_framing`）。
_VIEWPOINT_BYSTANDER_ONLY = (
    "from behind", "three-quarter back view", "rear view", "back view",
    "god's eye view", "bird's eye view", "looking down at face", "from above",
    "environmental composition",
)


_VIEWPOINT_WIDE_ONLY = (
    "full body", "wide shot", "long shot", "full shot", "medium wide shot",
)


# 「纯风景 / 环境帧」的确定性判据（第 56 刀）：**没有人体接触**但**有环境陈设词**。
# 用户口径「有时候如果只是描写风景……基本彰显不了风景全貌」——
#   这类帧必须**放行全景**，不得收成近景/中景。
# ⚠ 与 `_VIEW_FACE_CONTACT_RE` / `_VIEW_LOWER_CONTACT_RE` 互斥使用：
#   两者任一命中 ⇒ 是**近身帧** ⇒ 走取景收束；两者都不命中且本表命中 ⇒ 是**风景帧** ⇒ 放行远景。
_VIEW_ENVIRONMENT_RE = re.compile(
    r"(?:山|峰|岭|崖|谷|林|树|竹|松|枫|槐|柳|梅|湖|河|江|溪|池|潭|海|浪|潮|"
    r"云|霞|雾|霜|雪|风|雨|露|月|星|天|日|阳|霞光|晨光|曦|暮|黄昏|夜|"
    r"宫|殿|楼|阁|亭|台|院|廊|桥|街|巷|道|路|阶|墙|瓦|檐|窗|门|灯|烛|"
    r"屋|房|室|堂|案|桌|榻|床|椅|柜|架|屏|帘|帐|书|剑|琴|棋|画|茶|酒|炭盆|香炉|"
    r"远处|远方|山脚|天际|全貌|风景|景致|四下|四周|屋外|院外|窗外|院中)",
)


def _first_person_framing(scene: Mapping[str, object]) -> str:
    """第一人称「数据视野朝向 ＋ 取景筛分」的确定性推导（第 55／56 刀）。

    **三态**（第 56 刀据用户第二次纠正扩为三态，原来只有「近身」与「不设限」两态）：
      · **近身接触帧**（命中 `_VIEW_FACE_CONTACT_RE`／`_VIEW_LOWER_CONTACT_RE`）
        ⇒ 给**取景上限**（上半身 / 下半身）＋ 筛分口径；
      · **纯风景 / 环境帧**（两边接触都是空、但命中 `_VIEW_ENVIRONMENT_RE`）
        ⇒ **明确放行全景**（用户原话：风景被限成近景/中景「彰显不了风景全貌」）
        —— 只禁**身份矛盾那一半**（俯视/背后 ＝ 上帝视角），**不禁** `full body`/`wide shot`；
      · **其他**（无接触、也无环境词）⇒ 返回空串、**不回上限**（宁缺勿猜，
        调用方跳过整个收束块 ⇒ 逐字节等于旧行为）。
    `viewpoint` 非 `first_person` ⇒ 返回空串（零回归锁）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    sources: list[str] = []
    for key in ("narrative", "appearance", "wardrobe", "draft_prompt"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(image_prompt_extract.restore_jailbreak(value))
    blob = "\n".join(sources)
    if not blob.strip():
        return ""
    face = _view_face_frame(blob)
    # 即使两张表哪天漂移，strict 命中**必然**蕴含 lower（第 71 刀不变式的运行期兜底）。
    lower = (bool(_VIEW_LOWER_CONTACT_RE.search(blob))
             or bool(_VIEW_LOWER_STRICT_RE.search(blob)))
    if not face and not lower:
        if _VIEW_ENVIRONMENT_RE.search(blob):
            # ── 第 56 刀：风景帧——**放行全景**（不得限制死特写/中景）────────────────
            bystander = "、".join(f"`{item}`" for item in _VIEWPOINT_BYSTANDER_ONLY)
            return (
                "【视角·第一人称·本帧为环境/风景镜（不设近景上限）】本帧画面事实里"
                "**没有近身人体接触**、只有环境与陈设 ⇒ **不得**把它收成特写/近景："
                "那样**彰显不了风景全貌**（用户定案）。本帧**允许**远景/全景机位"
                "（`wide shot`／`long shot`／`full shot`／`medium wide shot` 均可），"
                "按画面事实把环境与陈设写全。⚠ 唯有一条仍然成立：本帧**仍是从叙事者的"
                "位置看出去**（不是旁边站着的观察者）⇒ **仍禁止**"
                f"{bystander}这类**俯视/背后/第三旁观**措辞——远景与旁观是两回事。"
                + _FP_PROSE_NO_META_CLAUSE
            )
        # ⚠ 两边都没命中 ⇒ **不给取景上限**（宁缺勿猜：没有接触证据就没有收束依据，
        #   此时按常规景别走）。返回空串 ⇒ 调用方跳过整个收束块、**逐字节等于旧行为**。
        return ""
    if face:
        limit, limit_en = "上半身", "upper body (waist-up, bust shot)"
    else:
        limit, limit_en = "下半身", "waist-down shot"
    return (
        f"【视角·第一人称·数据视野朝向（本帧为{limit}）】本帧取景上限＝**{limit}**"
        f"（{limit_en}）：**先保留**剧情拿到的画面事实，再按第一人称的**有限视野**"
        f"筛分——**只有真正看得见的**才写进画面，装不下的内容按层级**省略**"
        f"（可写进区域内的局部；不得为了「展示全部内容」把视角拉开）。"
        f"⚠ 本上限**只适用于近身接触帧**：若本帧只是描写环境/风景而无人体接触，"
        f"不应套用（那种帧要放行全景）。"
        + _FP_PROSE_NO_META_CLAUSE
    )


# 下半身部位／裤袜类（保守：以多字词为主；裸「臀／腿／胯／袜」保留 —— 本表只作用于
# **外貌/服装这类短名词短语**，不是自由叙事，量词陷阱（第 56 刀「一股」）不适用；
# 而真机 `appearance` 正是「宽厚圆硕的臀」「修长白皙的双腿」这种写法，漏掉裸字就整条失效）。
# ⚠ 与 `_TP_LOWER_RE`（scene_illustration，判**接触高度带**）**不同源**：那张表服务
#   「时点截断」，本表服务「取景筛分」，两者判的不是同一件事 ⇒ 刻意各留一份、不互相 import。
_FP_LOWER_BODY_RE = re.compile(
    r"(?:臀|屁股|股间|股沟|胯|裆|"
    r"大腿|小腿|膝弯|膝窝|腿|足尖|脚踝|脚背|脚趾|脚心|脚掌|双足|脚|"
    r"腰腹|小腹|腹部|腹股沟|腰侧|腰肢|腰间|腰|髋|"
    r"白袜|白丝|裤袜|丝袜|长袜|袜)",
)


# 字段内的**片段分隔符**（外貌/服装都是顿号/逗号/分号串起来的中文短语）。
_FP_SEGMENT_SPLIT_RE = re.compile(r"[、，,；;\n]+")


def _strip_lower_body(text: str) -> tuple[str, list[str]]:
    """按**片段**剔掉含下半身部位/裤袜的短语。返回 `(保留文本, 被剔片段)`。

    只按分隔符切、只丢整条命中片段 ⇒ **不改写、不重组**保留下来的文字（逐字不变）。
    被剔片段非空但保留文本为空 ⇒ 放弃本次剔（宁缺勿猜，见模块注释的「剔空保护」）。
    """
    raw = text or ""
    if not raw.strip():
        return raw, []
    segments = _FP_SEGMENT_SPLIT_RE.split(raw)
    kept: list[str] = []
    removed: list[str] = []
    for segment in segments:
        if not segment.strip():
            continue
        if _FP_LOWER_BODY_RE.search(segment):
            removed.append(segment.strip())
        else:
            kept.append(segment)
    if not removed or not kept:
        return raw, []
    return "、".join(kept), removed


def first_person_frame_filter(scene: Mapping[str, object]) -> dict:
    """第一人称「画面事实筛分」：按取景上限剔掉装不下的外观/服装事实（第 57 刀 ③）。

    返回 `{"applied": bool, "scope": str, "appearance": str, "wardrobe": str,
    "removed": [str, ...]}`；`applied=False` 时 `appearance`/`wardrobe` **原样返回调用方的值**
    （调用方据此判断「没动过」⇒ 逐字节等于旧行为）。
    """
    appearance = str(scene.get("appearance") or "")
    wardrobe = str(scene.get("wardrobe") or "")
    # `dict`（不带参数）⇒ 值按 Any 处理，免得为一次内部返回值引入 TypedDict。
    untouched: dict = {
        "applied": False, "scope": "", "removed": [],
        "appearance": appearance, "wardrobe": wardrobe,
    }
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return untouched
    sources: list[str] = []
    for key in ("narrative", "appearance", "wardrobe", "draft_prompt"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(image_prompt_extract.restore_jailbreak(value))
    blob = "\n".join(sources)
    if not blob.strip():
        return untouched
    # 取景上限的判定必须与 `_first_person_framing` **同口径**（同一个 4 键 blob、同一条
    # 「**面优先**」规则）—— 否则会出现「提示词声明上半身、本函数却按下半身不筛」的
    # 自相矛盾。⚠ 曾经踩过：把闸写成「命中下半身就不筛」，而 `appearance` 里本就有
    # 「臀／双腿」⇒ **被筛字段反过来关掉了筛分**（循环）。面优先之后，
    # `appearance` 里的唇/胸与 narrative 里的口/面共同得出「上半身」，与本函数目的自洽。
    face = _view_face_frame(blob)
    if not face:
        # 未命中「面/颈/胸/肩」⇒ 不是上半身近身帧（下半身帧不做反向筛；风景帧/无接触按
        # 第 56 刀三态要放行全景）⇒ 一律不筛（宁缺勿猜）。
        # ⚠ 刻意**不**把 `_VIEW_LOWER_CONTACT_RE` 也纳入本闸：`_first_person_framing` 用的是
        #   「**面优先**」（face 命中即给上半身上限，lower 只在其后兜底）⇒ 本函数跟着只认 face，
        #   才与提示词声明的取景上限恒一致。
        return untouched
    new_appearance, removed_appearance = _strip_lower_body(appearance)
    new_wardrobe, removed_wardrobe = _strip_lower_body(wardrobe)
    if not removed_appearance and not removed_wardrobe:
        return untouched
    return {
        "applied": True, "scope": "upper_body",
        "appearance": new_appearance, "wardrobe": new_wardrobe,
        "removed": [*removed_appearance, *removed_wardrobe],
    }


def _first_person_camera_ban(scene: Mapping[str, object]) -> tuple[str, ...]:
    """第一人称本帧**禁用的机位词表**（第 60 刀）；无收束依据／非第一人称 ⇒ 空表（零回归锁）。

    三态判据**直接复用** `_first_person_framing` 的返回值（同一个 4 键 blob、同一条「面优先」）：
      · 空串（第三人称／无接触无环境）⇒ `()` ⇒ 调用方逐字节返回原串；
      · 含「不设近景上限」（风景/环境帧）⇒ 只禁**恒禁那一半**
        （`_VIEWPOINT_BYSTANDER_ONLY`：俯视/背后/第三旁观＝身份矛盾，风景帧也成立）；
      · 其余＝近身接触帧 ⇒ 禁**全表** `_VIEWPOINT_FORBIDDEN_CAMERA`
        （旁观 ＋ `full body`/`wide shot` 这一半取景矛盾）。
    """
    framing = _first_person_framing(scene)
    if not framing:
        return ()
    if "不设近景上限" in framing:
        return _VIEWPOINT_BYSTANDER_ONLY
    return _VIEWPOINT_FORBIDDEN_CAMERA


def _strip_camera_tags(tag_line: str, banned: tuple[str, ...]) -> str:
    """从首行 tag 串里剔掉命中 `banned` 的整条 tag（纯剔除，不改写）。

    剔空保护（与 57 刀 `_strip_lower_body` 同族）：整行都是禁词 ⇒ 返回原串，
    避免清空首行后下游质量行与内容 tag 全数丢失。
    """
    if not banned:
        return tag_line
    tags = [tag.strip() for tag in (tag_line or "").split(",")]
    kept: list[str] = []
    dropped = 0
    for tag in tags:
        if not tag:
            continue
        if any(re.search(rf"\b{re.escape(item)}\b", tag, re.I) for item in banned):
            dropped += 1
            continue
        kept.append(tag)
    if not dropped or not kept:
        return tag_line
    return ", ".join(kept)


def _strip_bystander_camera_tags(tag_line: str, scene: Mapping[str, object]) -> str:
    """第一人称下从**首行 tag 串**里剔掉旁观/取景矛盾机位词（第 60 刀）。

    ⚠ 零回归锁（任一成立即**逐字节返回原串**、且不改动空格的既有形态）：
      ① `viewpoint` 非 `first_person`（`_first_person_camera_ban` 回空表）；
      ② 本帧无收束依据（既无近身接触、也无环境词）；
      ③ 行内无命中项。
    ⚠ 剔空保护（与 57 刀 `_strip_lower_body` 同族）：整行都是禁词 ⇒ **放弃本次剔**、
      返回原串 —— 清空首行会让下游质量行与内容 tag 全数丢失（宁缺勿猜）。
    ⚠ 只作用于**首行 tags**：第二行是自由散文，机械剔词必然误伤，刻意不碰。
    """
    return _strip_camera_tags(tag_line, _first_person_camera_ban(scene))


# 第 73 刀（2026-09-29 探针 [38] / 真机 09-29 批次）：第一人称首行 tags 的
# **指向镜头的方位短语**去机位化。
#
# 根因：`_first_person_framing` 的「禁用机位」条款一边禁 `from behind`／`rear view`，
#   **一边亲手给出** `her back arching toward the camera` 作范例——而同一段提示词里的
#   `_FP_PROSE_NO_META_CLAUSE` 又明禁散文行出现 `camera`／`viewer` ⇒ 指令自相矛盾。
#   后果：真机 slot `-54-5` 首行 `back turned toward the camera, back facing viewer`、
#   探针 [38] 首行 `back facing the camera`。第 60 刀是**整条字面禁表**，
#   扫不到「身体部位 ＋ 机位方位短语」这种组合（`back view` 有边界词，`back faced` 没有）。
#
# 口径：**只削方位短语、部位与姿势逐字保留**（`back turned toward the camera` →
#   `back turned`），不发明新词、不改姿势语义 ⇒ 与第 60 刀「不得把整个镜头搬到她背后」互补。
#   与 `_strip_bystander_camera_tags` 分工：那条**剔整条**禁词 tag，本函数**只削短语**。
_FP_TAG_CAMERA_DIRECTION_RE = re.compile(
    r"\s+(?:toward|towards|to|at|into)\s+the\s+camera\b", re.I,
)


def _first_person_strip_camera_direction_tags(
    tag_line: str, scene: Mapping[str, object],
) -> str:
    """第一人称下削掉**首行 tags 里指向镜头的方位短语**（第 73 刀）。

    ⚠ 零回归锁（任一成立即**逐字节返回原串**）：① `viewpoint` 非 `first_person`；
      ② 行内无命中；③ 命中 tag 削完**不足两个词** ⇒ 放弃该条（防悬空动词：
      `looking at the camera` → `looking`）。
    ⚠ 只作用于**首行 tags**：第二行是自由散文，由 `_first_person_prose_meta_guard`
      （第 68 刀）另行收口。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return tag_line
    segments = (tag_line or "").split(",")
    changed = False
    kept: list[str] = []
    for segment in segments:
        body = segment.strip()
        if not body or not _FP_TAG_CAMERA_DIRECTION_RE.search(body):
            kept.append(segment)
            continue
        candidate = re.sub(r"\s{2,}", " ", _FP_TAG_CAMERA_DIRECTION_RE.sub("", body))
        candidate = candidate.strip(" ,;")
        if len(candidate.split()) < 2:
            kept.append(segment)          # 防悬空动词
            continue
        changed = True
        indent = segment[: len(segment) - len(segment.lstrip())]
        kept.append(indent + candidate)
    if not changed:
        return tag_line
    return ",".join(kept)


# ⚠ 识别面**刻意保守**（第 63 刀）：只认**无歧义的第一人称**写法。
#   `from her/his/the man's point of view` 这类**不认** —— 它同样可能是第三人称下
#   「从某角色视角拍」的描述；认错 ⇒ **不补** ⇒ 缺陷反向复发。
#   **方向原则：宁可重复追加（冗余、无害），不可误认为已有（漏补、致命）。**
_FP_VIEW_TAG_RE = re.compile(
    r"\b(?:first[-\s]?person\s+(?:view|pov|perspective)"
    r"|from\s+my\s+point\s+of\s+view"
    r"|male\s+viewer\s+pov|viewer\s+pov|pov)\b",
    re.I,
)


_FP_VIEW_TAGS = "first person view, pov"


# 风景帧／无收束依据帧只补这一条（见 `_ensure_first_person_view_tag` 的三态说明）。
_FP_VIEW_TAG_MIN = "first person view"


def _first_person_frame_kind(scene: Mapping[str, object]) -> str:
    """第一人称本帧的**取景三态**（第 63 刀统一收口）：`"near"`／`"scenery"`／`""`。

    ⚠ 与 `_first_person_camera_ban` **同源**（都从 `_first_person_framing` 派生：同一个返回串、
      同一条「不含『不设近景上限』即近身帧」的判据）—— 下游不得各自重判。
    ⚠ `viewpoint` 非 `first_person` ⇒ `""`（零回归锁）。
    """
    framing = _first_person_framing(scene)
    if not framing:
        return ""
    return "scenery" if "不设近景上限" in framing else "near"


def _ensure_first_person_view_tag(tag_line: str, scene: Mapping[str, object]) -> str:
    """第一人称下确保首行含 **POV 正向声明**（第 61 刀；第 63 刀按三态分级）。

    ⚠ 零回归锁：`viewpoint` 非 `first_person` ⇒ **逐字节返回原串**；
      行内已有等价声明（见 `_FP_VIEW_TAG_RE`）⇒ 同样逐字节返回（**幂等**，不重复追加）。
    ⚠ 空串／纯空白 ⇒ 原样返回（宁缺勿猜：没有位置语义，不补一条孤零零的 tag）。

    第 63 刀（三态分级）—— `pov` 在 Danbooru 里隐含「**视角人物的部分身体可见**」：
      · **近身接触帧**（`kind == "near"`）⇒ `first person view, pov`；
      · **风景帧／无收束依据帧** ⇒ 只补 `first person view`。理由是风景帧的既有定案
        （红线 76）是「**放行全景**、彰显风景全貌」——声明 `pov` 与该帧意图打架
        （可能诱导模型在风景里画进一只手）；而无收束依据帧**没有**「有局部入画」的正向证据
        ⇒ 不声明（宁缺勿猜）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return tag_line
    if not (tag_line or "").strip():
        return tag_line
    if _FP_VIEW_TAG_RE.search(tag_line):
        return tag_line
    tags = _FP_VIEW_TAGS if _first_person_frame_kind(scene) == "near" else _FP_VIEW_TAG_MIN
    return f"{tag_line.rstrip(' ,')}, {tags}"


_FP_TALLER_THAN_NARRATOR_RE = re.compile(
    r"比\s*(?:\{\{\s*user\s*\}\}|user|你|我|男主(?:角|人公)?|主人公|主角|玩家|叙事者)"
    r"\s*(?:还|仍|要)?\s*高"
    r"|高(?:出|过)\s*(?:\{\{\s*user\s*\}\}|user|你|我|男主(?:角|人公)?|主人公|主角|玩家|叙事者)?"
    r"\s*(?:一个头|一头|半个头|半头|一截)"
    r"|\b(?:a\s+head|one\s+head|half\s+a\s+head)\s+taller\s+than\s+"
    r"(?:me|you|the\s+(?:user|protagonist|narrator|player))\b"
    r"|\btaller\s+than\s+(?:me|you|the\s+(?:user|protagonist|narrator|player))\b",
    re.I,
)


_FP_LOW_ANGLE_TAG = "slight low angle, looking up"


_FP_LOW_ANGLE_RE = re.compile(
    r"\b(?:slight\s+)?(?:low[-\s]?angle|upward(?:\s+camera)?\s+angle|"
    r"from\s+below|looking\s+up|worm'?s[-\s]?eye)\b",
    re.I,
)


# 与「仰拍」直接冲突的高机位词：只在身高差成立且相对高度未变时剔（不可动普通 `looking down` 这类动作）。
_FP_HIGH_ANGLE_ONLY = (
    "from above", "looking down at face", "bird's eye view", "god's eye view", "high angle",
)


# 「改变相对高度」的画面事实（2026-10-23 用户纠正）：出现这些动作/姿态时，双方当下的
# 实际相对高度可能与「身高差」相反或相等（被对方抱起＝平视甚至俯视；对方俯身/跪蹲＝平视），
# ⇒ 身高差退为**参考**：不机械补仰拍，也不剔高机位，机位按实际相对位置选最佳呈现。
_FP_RELATIVE_LEVEL_RE = re.compile(
    # 中文：抱起/扛起/打横/公主抱；⚠「抱起双臂」不是位移，用负向断言挡掉。
    r"抱(?:起|起来)(?!(?:双)?(?:臂|手|胳膊))"
    r"|打横|横抱|公主抱|扛(?:在|到)?(?:肩上|肩头)|扛起"
    r"|(?:把|将|被)[^。；;，,\n]{0,10}(?:抱起|扛起|举起|托起|捧起)"
    r"|(?:抱|搂|拥)(?:在|进|入)(?:怀里|怀中)"
    r"|(?:坐|跨坐|骑)(?:在|到|上|回)"
    r"|(?:躺|卧|趴|伏)(?:在|于|到|下)|仰卧|仰面|俯卧|侧卧"
    r"|(?:跪|蹲)(?:在|下|着|到)|俯身|弯腰|弓腰|俯下|屈膝|半跪|单膝(?:跪|着)"
    r"|(?:压|按)在[^。；;，,\n]{0,10}(?:身下|床上|榻上|地上)"
    # 英文
    r"|\b(?:carried|lifted|picked\s+up|hoisted)\b[^.;,\n]{0,30}\b(?:in|on)\s+"
    r"(?:his|her|their)\s+(?:arms|lap)\b"
    r"|\bon\s+(?:his|her|their)\s+lap\b|\bstraddl\w*"
    r"|\b(?:lying|laid)\s+(?:on|in|across)\b"
    r"|\bkneel\w*|\bcrouch\w*|\bbent\s+over\b|\bbend(?:s|ing)?\s+(?:over|down)\b"
    r"|\blean(?:s|ing)?\s+(?:in|down|over|closer)\b"
    r"|\b(?:pinned|pressed)\s+(?:under|beneath|down)\b"
    r"|\b(?:sitting|sat|seated)\s+(?:on|in)\b",
    re.I,
)


def _first_person_match(
    scene: Mapping[str, object], pattern: re.Pattern[str],
) -> str:
    """第一人称下在画面事实里搜一条匹配（非第一人称 ⇒ 空串）。"""
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    for key in ("appearance", "wardrobe", "narrative"):
        value = scene.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        match = pattern.search(image_prompt_extract.restore_jailbreak(value))
        if match:
            return match.group(0)
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        for item in subjects:
            if not isinstance(item, Mapping):
                continue
            match = pattern.search(str(item.get("description") or ""))
            if match:
                return match.group(0)
    return ""


def _first_person_height_evidence(scene: Mapping[str, object]) -> str:
    """本帧有没有「对方比叙事者高」的**明确**证据（第一人称限定）。"""
    return _first_person_match(scene, _FP_TALLER_THAN_NARRATOR_RE)


def _first_person_relative_level(scene: Mapping[str, object]) -> str:
    """本帧有没有**改变相对高度**的动作/姿态（抱起、坐腿上、躺卧、俯身、跪蹲……）。

    命中 ⇒ 身高差只是参考，实际机位应由画面事实决定（可能平视甚至俯视）。
    """
    return _first_person_match(scene, _FP_RELATIVE_LEVEL_RE)


def _first_person_height_angle(scene: Mapping[str, object]) -> bool:
    """是否应给本帧补「叙事者眼高 ＋ 轻微仰拍」（风景帧／相对高度已变 ⇒ 不套）。"""
    if not _first_person_height_evidence(scene):
        return False
    if _first_person_relative_level(scene):
        return False
    return _first_person_frame_kind(scene) != "scenery"


def _ensure_first_person_height_angle(
    head: str, scene: Mapping[str, object],
) -> str:
    """身高差成立且**相对高度未变**时，确保首行含**低机位仰拍** tag，并剔掉冲突的高机位词。

    ⚠ 零回归锁：第三人称／无证据／风景帧／**相对高度已变**／空串 ⇒ 逐字节返回原串。
    ⚠ 幂等：已有 `low angle`／`looking up` 等写法 ⇒ 不重复追加。
    ⚠ 这里**允许**在「整行都是高机位词」时剔空后只留仰拍 tag：身高差已由画面事实确证，
      继续保留俯视会把「她比叙事者高」直接画反（与第 60 刀的剔空保护场景不同）。
    ⚠ 相对高度已变（抱起/坐腿上/躺卧/俯身/跪蹲）⇒ 高机位可能才是 POV 真实所见 ⇒ 不剔。
    """
    if not _first_person_height_angle(scene):
        return head
    raw = head or ""
    if not raw.strip():
        return head
    tags = [tag.strip() for tag in raw.split(",") if tag.strip()]
    kept = [
        tag for tag in tags
        if not any(re.search(rf"\b{re.escape(item)}\b", tag, re.I)
                   for item in _FP_HIGH_ANGLE_ONLY)
    ]
    changed = len(kept) != len(tags)
    if any(_FP_LOW_ANGLE_RE.search(tag) for tag in kept):
        return ", ".join(kept) if changed else head
    kept.append(_FP_LOW_ANGLE_TAG)
    return ", ".join(kept)


def _first_person_height_clause(scene: Mapping[str, object], who: str) -> str:
    """身高差条款（无证据／风景帧 ⇒ 空串）。

    2026-10-23 用户纠正：身高差**只是参考**。画面事实出现改变相对高度的动作/姿态时，
    不写「仰拍」硬规则，改写成「按实际相对位置选最佳机位、只画 POV 看得见的」。
    """
    if not _first_person_height_evidence(scene):
        return ""
    if _first_person_frame_kind(scene) == "scenery":
        return ""
    cue = _first_person_relative_level(scene)
    if cue:
        return (
            f"【视角·第一人称·相对位置优先（身高差只是参考）】画面事实写了对方比{who}更高，"
            f"但本帧还有**改变相对高度**的动作/姿态（「{cue[:40]}」）⇒ **不得**机械套用仰拍，"
            f"也**不得**为了保住身高差而改写动作。**先按画面事实确定双方当下的实际相对高度，"
            f"再选最能呈现画面内容的机位**：平视、俯视、仰拍都可能成立；"
            f"只画{who}在这个位置**真正看得见**的内容。"
        )
    return (
        f"【视角·第一人称·身高差与仰拍】画面事实明确写了对方**比{who}更高**"
        f"（如「高出一个头」这类**明确的身高差**），且本帧**没有改变相对高度**的动作/姿态"
        f"（抱起、坐腿上、躺卧、俯身、跪蹲等）⇒ 此时保持**{who}的眼高**、"
        f"取**轻微仰拍**（`slight low angle`／`looking up`），让对方在画面中显得高于{who}；"
        f"**不得**用俯视、高机位或把镜头抬到对方眼高来抹平身高差。"
        f"⚠ 身高差只是**参考**：一旦画面出现上述改变相对高度的动作，本条不适用，"
        f"机位按实际相对位置取景。仰拍只改变机位高度，**不得**因此新增任何画面事实；"
        f"第一人称只画{who}在该位置**真正看得见**的内容。"
    )


# ═══ 第 65 刀：叙事者部位事实的「呈现形态」（追加式）═══════════════════════════
# 用户定性（原话）：「第一人称就是在内容的基础上**用第一人称去进行取舍或修改**使其符合
#   第一人称情况下的特色」。第 62 刀＝「取舍」；本刀＝「修改」的**机械保证**。
# ⚠ 只**追加**、不改写 —— 理由见 `_first_person_limb_entry_phrasing` 的 docstring
#   （改写会新增账本假红面 ＋ 打红 43／47 刀 12 处锁）。
_FP_LIMB_ENTRY_TAG = "entering from the lower frame edge"


_FP_LIMB_ENTRY_MARK = "lower frame edge"


def _first_person_limb_entry_phrasing(head: str, scene: Mapping[str, object]) -> str:
    """第一人称下给「叙事者部位事实」补一条**从画面下缘进入**的呈现限定（第 65 刀 · **追加式**）。

    用户定性：「第一人称就是在内容的基础上**用第一人称去进行取舍或修改**使其符合第一人称
    情况下的特色」。第 62 刀做了「取舍」；本刀补「修改」的**机械保证**（不再只靠指令自觉）。

    ⚠⚠ **刻意不做「改写」，只做「追加」**（复核后定案）：
      · **改写**（插进原有短语中间）会让字段账本的 `expected` 与成稿形态**不一致** ——
        模型写短形态就判不 `covered` ⇒ **新增假红面**（红线 72 的「永不消失的假红」）；
        且会打红 43／47 刀 **12 处**特征锁（那些锁断言的正是这些精确串）。
      · **追加**（原串**保留**并补一条限定）⇒ 原串**仍是成稿的连续子串** ⇒ 账本判据零影响、
        既有锁全绿，而画面语义完全达成（`entering from the lower frame edge` 就是
        `_viewpoint_clause` 既有的「局部从画面下缘进入」措辞）。

    ⚠ 判据＝**精确匹配 `_NARRATOR_LIMB_FACT_TEXTS`**（43／47 刀的固定产出集）⇒ 零误伤；
    ⚠ 幂等：已有下缘进入字样 ⇒ 逐字节返回原串；非第一人称／无部位事实 ⇒ 逐字节返回原串。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return head
    raw = head or ""
    if not raw.strip():
        return head
    tags = [tag.strip() for tag in raw.split(",")]
    if not any(tag in _NARRATOR_LIMB_FACT_TEXTS for tag in tags):
        return head
    if any(_FP_LIMB_ENTRY_MARK in tag.lower() for tag in tags):
        return head
    kept = [tag for tag in tags if tag]
    kept.append(_FP_LIMB_ENTRY_TAG)
    return ", ".join(kept)


# ═══════════════════════════════════════════════════════════════════════════════
# 第 67 刀（2026-09-29 用户真机审查）：**JSON 残留机械闸**
#
# 真机实锤（turn `d3d32fda` · 槽 `illo-req-…-54-1`）：模型／repair 轮返回**被截断的半 JSON**，
# `_json_object` 与 `_labelled_fields_object` **双双失败** ⇒ 掉进 `_normalize` 的**非 JSON 分支**。
# 该分支 `explicit_tags = ", ".join((lines[0], body))` **无条件吞并**原文各行 ⇒
#   · 首行混进 `{, … "supporting_elements": [, "Her flattened hand on the mattress, ], …`（残片当 tag）；
#   · 第二行变成 `content": "1girl, solo, … anime style.`（**JSON 的 key 标签**进了散文行）；
#   · 而散文判据 `^[A-Za-z][^.?!]{24,}[.?!]` 被 `content": "…` **意外满足**（`c` 是字母、
#     中间无句点、末尾有句点）⇒ **一条格式红都不报** ⇒ `_patch_failed_prompt` 补上通用构图
#     词表后**交付 976 字半 JSON 残留**，而**真正的第二行内容描述整段丢失**。
# 用户口径（原话）：「正常来说这里上半段应该还是 tag」「槽 1 的下一段的内容描述文段还丢失了」。
#
# ⇒ 口径：**结构化残片一律不得当提示词交付**。命中即返回空串 ⇒ 调用方照旧判死并走 repair
#   （与「空稿／拒答」同一条兜底通道），**不新增任何交付路径**。
# ⚠ 只查**组装后的成稿**、不查 raw：raw 若是**合法 JSON**，花括号与引号本就是协议的一部分
#   （那种情况早已被 `_json_object` 解析成 `value`）⇒ 组装结果里不带符号 ⇒ 零误报。
_JSON_RESIDUE_SCHEMA_RE = re.compile(
    r"(?:visual_hook|primary_focus|supporting_elements|content)\s*[\"']?\s*:\s*[\"'\[{]",
    re.I,
)


# 引号**紧邻结构标点**才算残留（`the "crimson eye"` 这种正常引号不算）。
_JSON_RESIDUE_NOISE_RE = re.compile(r"[\"']\s*[,:\]\[]|[,:\[\{]\s*[\"']")


def _json_residue_hits(text: str) -> list[str]:
    """成稿里的 **JSON 残留**特征（空列表＝干净）。第 67 刀，只服务「判死」这一个用途。"""
    body = text or ""
    hits: list[str] = []
    if "{" in body or "}" in body:
        hits.append("花括号")
    match = _JSON_RESIDUE_SCHEMA_RE.search(body)
    if match:
        hits.append(f"规格字段裸标签 {match.group(0).strip()[:40]}")
    if _JSON_RESIDUE_NOISE_RE.search(body):
        hits.append("引号紧邻结构标点")
    return hits


# ═══════════════════════════════════════════════════════════════════════════════
# 第 68 刀（2026-09-29 用户真机审查）：**散文行元语言机械剔**
#
# 用户口径（原话）：「内容描述不是说要明说**镜头即叙述者的双眼**、所以叙述者从不出现在画面里，
# **而是你的内容描述能透露出这一点**，因为**人家模型哪里知道叙述者是谁**，真要说你上面的
# 第一人称视角描述就够用了」。
# ⇒ `narrator`／`camera` 是 **Agent 侧概念**，ComfyUI 侧读不懂；第一人称由**首行 tag** 声明
#   （第 61 刀 `_ensure_first_person_view_tag` 已保证），散文行只写**画面里看得见的东西**。
# 根因（第 55／56 刀指令原文）：中文指令写着「镜头**已经固定**在**叙事者的眼睛位置上**」⇒
#   模型把它**照抄**成英文散文（turn `d3d32fda` 7 份成稿里 **4 份**中招）。
# ⚠ 零回归锁：`viewpoint` 非第一人称 ⇒ **逐字节返回原串**；无命中 ⇒ 同样逐字节返回。
# 第 68 刀：这段**逐字进中文指令**（近身帧与风景帧共用），模型照抄元语言正是本次真机缺陷的根因。
# 口径＝**只禁止，不引导造句**（红线 28 ②：不新增模板引导句），且把「叙事者入画」正确形态
# 说清为「**具体部位在画面里的位置**」——与第 43／65 刀 `_narrator_limb_facts` 同源。
_FP_PROSE_NO_META_CLAUSE = (
    "⚠ **散文行（第二行）不得出现 `camera`／`narrator`／`viewer`／`point of view` "
    "这类元语言**——生图模型不认识「叙事者」是谁，第一人称由首行 tag 的 "
    "`first person view` 声明即可。叙事者入画时只写**具体部位在画面里的位置**"
    "（如 `a hand prying her leg open`／`a single hand entering from the lower frame edge`），"
    "**不得**写「镜头即叙述者的双眼」「叙述者不出现在画面里」这类解释句。"
)


_FP_PROSE_META_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    # ① 「镜头＝叙述者双眼 ＋ 叙述者不入画」整块前缀 → 只保留「谁可见」那半句
    (re.compile(
        r"the\s+camera\s+is\s+the\s+narrator'?s\s+eyes\s*,?\s*"
        r"(?:so\s+)?the\s+narrator\s+never\s+appears\s+in\s+frame\s*;?\s*",
        re.I), ""),
    # ①b 兜底：「镜头＝叙述者双眼」**裸前缀**（真机 slot `-54-4`：「…, looking down at
    #     the woman's sleeping face … from a first-person perspective.」——前半句
    #     没有 `so the narrator never appears…`，① 匹配不到）。必须先于 ④ 执行。
    (re.compile(r"the\s+camera\s+is\s+the\s+narrator'?s\s+eyes\s*,?\s*", re.I), ""),
    # ② 「镜头固定在叙述者眼高／位置」从句
    (re.compile(
        r"the\s+camera\s+(?:holds?|sits?|stays?|rests?)\s+(?:at|to)?\s*the\s+narrator'?s\s+"
        r"(?:eye\s+level|eyes|position)\s*,?\s*", re.I), ""),
    (re.compile(
        r"the\s+camera\s+holds?\s+the\s+narrator'?s\s+position\s+", re.I), ""),
    (re.compile(
        r"the\s+camera\s+(?:is\s+)?(?:fixed|locked)\s+(?:at|on|to)\s+[^,.;]*[,;]?\s*",
        re.I), ""),
    # ③ 指向镜头的方位短语
    (re.compile(r"\s+(?:toward|towards|into|to|at)\s+the\s+camera", re.I), ""),
    # ④ 「从第一人称视角」这类自指视角短语（首行 tag 已声明，散文行不必复述）
    (re.compile(
        r"\s+from\s+a\s+(?:first[-\s]?person|pov|the\s+narrator'?s)\s+"
        r"(?:perspective|view|angle|eyes)", re.I), ""),
    (re.compile(r"\s+in\s+the\s+first[-\s]?person\s+(?:perspective|view)", re.I), ""),
    # ⑤ 「叙述者身体只作为局部可见」→ 只留局部事实本身
    (re.compile(
        r"the\s+narrator'?s\s+(?:body|arm|hand|leg)s?\s+is\s+only\s+visible\s+as\s+",
        re.I), "only "),
    (re.compile(r"the\s+narrator\s+never\s+appears\s+in\s+frame\s*;?\s*", re.I), ""),
    # ⑥ 兜底：任何残留的「叙述者的 X」→ 去主语化成「a X」。真机 slot `-54-3`：
    #     `while the narrator's lower body enters only as a cropped segment from the
    #     bottom edge` —— 语义是对的（局部入画），但 `narrator` 一词必须去掉。
    #     ⚠ 必须排在 ①②⑤ 之后：那几条要连同整个从句一起删，不能被本条的 "a X" 抢走。
    (re.compile(r"the\s+narrator'?s\s+", re.I), "a "),
    (re.compile(r"the\s+narrator\s+", re.I), ""),
)


def _first_person_prose_meta_guard(prose: str, scene: Mapping[str, object]) -> str:
    """第一人称下把**散文行里的元语言镜头句**机械剔掉（第 68 刀）。

    ⚠ 只动**指向镜头/叙述者的元语言**，**不动任何画面事实**：命中短语被删后重排标点与大小写，
    句子其余部分逐字保留。删完若不再像「英文画面句」，下游既有判据会照旧判死 ⇒ 走 repair，
    不会交付残句（fail-closed，不新增交付路径）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return prose
    body = prose or ""
    if not body.strip():
        return prose
    guarded = body
    for pattern, replacement in _FP_PROSE_META_REWRITES:
        guarded = pattern.sub(replacement, guarded)
    if guarded == body:
        return prose          # 零命中 ⇒ 逐字节返回（幂等锁）
    # 重排：压空白 → 修标点 → 修大小写
    guarded = re.sub(r"\s{2,}", " ", guarded)
    guarded = re.sub(r"\s+([,;.])", r"", guarded)
    guarded = re.sub(r"[,;]\s*([,;.])", r"", guarded)
    guarded = re.sub(r"([,;])\s*$", "", guarded).strip()
    guarded = re.sub(r"^[,;.\s]+", "", guarded).strip()
    if guarded:
        guarded = guarded[0].upper() + guarded[1:]
    if guarded and guarded[-1] not in ".!?":
        guarded += "."
    if not guarded.strip():
        return prose          # 全被剔空 ⇒ 交回原串（下游判据接手，不做二次发明）
    return guarded


def _first_person_head_guards(head: str, scene: Mapping[str, object]) -> str:
    """第一人称「首行 tags」统一收口：**剔旁观机位词 → 补下缘进入限定 → 补身高差仰拍
    → 补 POV 声明**（第 60／61／63／65 刀 ＋ 2026-10-23 身高差仰拍）。

    ⚠ 与 `_normalize` 的 anima 分支**同序同语义**（那几行因接线锁必须显式留在 `_normalize` 里
      —— 见 `test_第60刀_闸已接进anima归一化收口`／`test_第61刀_接线锁_闸与补都接在归一化收口`）。
    ⚠ 每一步各自带零回归锁与非第一人称短路 ⇒ 本函数对第三人称**逐字节返回原串**。
    """
    head = _first_person_strip_camera_direction_tags(head, scene)
    head = _strip_bystander_camera_tags(head, scene)
    head = _first_person_limb_entry_phrasing(head, scene)
    head = _ensure_first_person_height_angle(head, scene)
    return _ensure_first_person_view_tag(head, scene)


# 第 60～65 刀已把 POV 保证与禁词闸覆盖到 `anima_tags`／`krea2`／`niji_sections`
# （按各自输出结构落点）。下面这张表是**已知未覆盖**的 profile —— **白名单式**：
# 只对它告警，不给空值/未知 profile 制造噪音。
# ⚠ 将来新增 profile 时必须回来评估：做了机械层就不必登记，没做就在这里加一项。
_FP_UNCOVERED_PROFILES = frozenset({"natural_language", "moss_caption"})


def viewpoint_coverage_warning(profile: str, scene: Mapping[str, object]) -> str:
    """第一人称下**该 profile 有没有 POV 机械保证**的诊断告警（第 66 刀 · 防呆登记）。

    ⚠ 只**返回诊断串**、**不改任何输出**：非第一人称／不在未覆盖表里 ⇒ 空串（零回归锁）。
    ⚠ 语义是「**防呆**」而不是「修复」：第 64 刀刻意不给 `natural_language` 做机械层
      （单段散文：机械剔词伤语法/语义，追加声明句违反红线 28「交付文本只写画面」）。
      本函数让这件事**可观测** —— 第一人称下若真的用了它，trace 里能看到告警，
      而不是静默回到「**第三人称人物在场**」的缺陷态。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    if str(profile or "").strip() not in _FP_UNCOVERED_PROFILES:
        return ""
    return (
        f"profile `{profile}` 在第一人称下**没有 POV 机械保证**"
        "（无 POV 声明注入、无旁观机位词剔除）；详见技术手册 "
        "`A-roleplay-illustration/03` §5 红线 79 ④"
    )


def _viewpoint_clause(scene: Mapping[str, object]) -> str:
    """视角口径（生图模式，2026-09-20 用户定案）。

    第一人称：镜头＝叙事者的眼睛，叙事者本人不入画，但**身体局部要入画**（NSFW 桥段必然有
    身体部位）；第三人称：叙事者是画面里的一个人物，正常参与主体数。

    ⚠ **零回归锁**：`viewpoint` 键缺失/非法 ⇒ 返回空串 ⇒ 三档提示词**逐字节等于旧行为**。
    """
    mode = str(scene.get("viewpoint") or "").strip()
    if mode not in ("first_person", "third_person"):
        return ""
    narrator = str(scene.get("narrator") or "").strip()
    if mode == "third_person":
        return (
            "【视角·第三人称】叙事者是画面里的一个人物，正常参与画面主体数；该出现在画面里时，"
            "按与其他角色**完全相同**的规格写全（外貌、当前服装、动作与可见接触点），"
            "不得因为他是叙事者而省略、含糊或降格成背景。"
            # 2026-09-24 第 55 刀（用户定案「把内容优先、机位是因变量**划分给第三人称**」）：
            # 第 38 刀那条因果**只在第三人称成立** ⇒ 在此**显式绑定到第三人称**，
            # 免得它被误当成全局通则而在第一人称下把镜头推远（本刀的真机事故成因）。
            # ⚠ 措辞只是把既有限定补明，不改第 38 刀的判定内容 ⇒ 第三人称侧零行为变化。
            "【视角·第三人称·机位是自变量】本帧机位**因变量**成立的前提是「观察者可以自由"
            "选择站位」——第三人称下观察者在画面之外，**可以**退远到容纳全部画面事实，"
            "故：**先列全并保留剧情拿到的画面事实，再据此选择最能呈现它们的机位与景别**。"
            # 2026-09-21 第 31 刀（用户实锤）：本条款原先只规定「谁入画」，**没有视线与朝向的
            # 方向口径** ⇒ 真机 L3·natural 在第三人称下写 `exposes … directly toward the
            # camera`，而主人公就站在画面里；当时落成「第三人称＝看向主人公」。
            # ⚠ 2026-09-22 用户纠正：**不得强制**视线指向叙事者 —— 视线方向**按剧情**，
            #   只有剧情明写「看向/注视/回望叙事者」时才写；人称只决定**翻译方向**。
            "【视角·视线与朝向（第三人称）】**视线方向按本轮画面事实写**——只有当剧情/画面事实明确写到"
            "某角色看向、注视、回望叙事者时，才把该角色的视线写成指向叙事者本人；"
            "**不得默认或强制**让角色看向叙事者（没写就不写）。**身体朝向按画面事实写**——"
            "事实写明「撅起臀部、掰开臀瓣呈现给某人」这类**背向式**姿势时，身体是"
            "**背对叙事者本人**的（臀部朝事实指明的那一方）；头与视线也按画面事实写"
            "（事实写回望才回望）；"
            # 2026-09-21 第 33 刀（用户实锤 · L3·NSFW多人 natural_language）：第 31 刀把「身体朝向」
            # 也一刀切钉成「必须指向叙事者本人」，真机据此把两名**背向**撅臀的角色写成
            # 「both facing toward him / 面向 + 跪着」⇒ 空间关系被读成面对面，与「背对主角、只回头」
            # 的剧情矛盾。定案：**身体朝向**改由画面事实决定（背向式姿势＝背对）；
            # ⚠ 2026-09-22 再纠正：**视线也一并交回画面事实**（不得强制指向叙事者）。
            "不得把这写成「面向叙事者」「面向镜头」「跪在他面前」等**正对式**措辞——那会把空间"
            "关系写成面对面。也不得凭空用 toward the camera、at the viewer 之类把方向交给镜头"
            "（只有本轮事实明写看向镜头时才可写）。"
        )
    who = f"叙事者（{narrator}）" if narrator else "叙事者"
    # 第 42 刀·C（2026-09-22 用户实锤「手选第一人称，成图仍含主人公」）：第一人称的**数据层**
    # 保护（`filter_anchor_subjects`／`_strip_narrator_from_model_view`／`_contract_subject_names`）
    # 全部挂在 `narrator` 上，而它来自用户设置的人设名 ⇒ 为空时四条判据集体空转，只剩本条款在挡。
    # 原条款只说「叙事者不入画」，**说不出谁是叙事者** ⇒ 模型读到正文里被角色点名的男主人公
    # （真机：`洛渊`），照样把他画成画面里的人（成稿含 `adult man`，女主服装被绑到他身上）。
    # ⇒ 当时把身份判据从**名字**换成**叙述人称**：以第一人称「我」叙述／行动的那个人就是叙事者。
    #   ⚠ **2026-09-22 再纠正**（真机复现：第三人称叙述的正文点名主人公「洛渊」10 次、
    #   「他」8 次，一个「我」都没有 ⇒ 该规则**整体失效**、主人公被画进画面）：
    #   **文段级判断机械做不了，语境理解得靠 LLM** ⇒ 条款改为「人称由用户手选确定、
    #   不得据正文叙述人称推翻」＋「第三人称点名主人公仍是第一人称、主人公＝镜头」
    #   ＋「请按语境认出他」。⚠ 零回归锁不变（`viewpoint` 缺失/非法 ⇒ 空串）。
    framing = _first_person_framing(scene)
    # ⚠ 三态分流（第 56 刀）：
    #   · `framing` 为空（无接触、也无环境词）⇒ 整个取景块不写，避免出现
    #     「取景上限以上一条为准」这种**指向空条款**的悬空引用（零回归锁）。
    #   · `framing` 是**环境/风景镜**条款（含「不设近景上限」）⇒ **只写它**、
    #     **不写**「近身三步顺序／禁用机位／叙事者从画面下缘进入」三块——
    #     那三块全都以「近身接触」为前提（真机踩过：风景帧被套上禁用
    #     `full body`/`wide shot` 的取景矛盾条款，与「放行风景全貌」直接对撞）。
    #   · 否则＝近身接触帧 ⇒ 写全部四块（第 55 刀原样）。
    if not framing:
        scope = ""
    elif "不设近景上限" in framing:
        scope = framing
    else:
        scope = framing + (
        f"【视角·第一人称·三步顺序（不可颠倒）】① **先内容优先**——先把剧情拿到的画面事实"
        f"（人物、动作、接触点、服装当前状态）**读全**，这一步与第三人称相同；"
        f"② **再判数据视野朝向**——但本帧镜头**已固定**在{who}的眼睛上，**不是**可以自由退远的"
        f"旁观机位 ⇒ **不得**用「内容装不下就把镜头推远」来解决（那是第三人称的做法）；"
        f"③ **最后筛分镜头里该保留的东西**——按上一条给出的取景上限，**只有真正看得见的**"
        f"才写进画面，凡超出该视野的画面事实一律**不进画面**（不是删剧情，是**这一帧看不见**）。"
        f"【视角·第一人称·禁用机位】本帧**禁止**第三方全景机位与旁观视角措辞——包括 "
        f"`from behind`、`three-quarter back view`、`rear view`、`full body`、`wide shot`、"
        f"`long shot`、`environmental composition`、`bird's eye view`，以及"
        f"**`looking down at face`/`from above` 这类俯视**（俯视隐含「有一个站在上方看的人」"
        f"＝旁观者，与「镜头＝{who}的眼睛」矛盾）。若剧情确需表现某个背向部位，"
        f"改用**该部位本身＋当前姿态**的措辞（如 `her bare back arched, facing away, "
        f"spine and shoulder blades visible`），"
        f"**不得**把整个镜头搬到她背后。"
        f"【视角·第一人称·叙事者从画面下缘进入】{who}的身体局部**从画面下缘/下侧边缘进入**"
        f"（`enters from the lower frame edge`／`cropped at the bottom edge`），"
        f"**只看见接触所需的那一小段**——例如「压在{who}的面上」写成"
        f"「器官从画面下缘进入、压在她唇上」，**不写**它的根部、躯干、腿，"
        f"**不写**{who}的脸、肩、视线或任何可以反推出全身的部位。"
        f"⚠ 局部入画**不是**把{who}画到镜头里来——他是**镜头本身**，"
        f"故**永远不出现**他的躯干与面部。"
        )
    height_clause = _first_person_height_clause(scene, who)
    return (
        f"【视角·第一人称】镜头就是{who}的眼睛，{who}本人**不入画**。"
        # ═══ 2026-09-24 第 55 刀：第一人称的**三步因果**（用户定案原话）═══════════════
        # 「如果是第一人称，那就是**先内容优先，再判断第一人称的数据视野朝向，
        #   最后筛分镜头里面应该保留的东西**。」
        # ⇒ 三步必须**按此顺序**写进条款（顺序本身是口径的一部分，不可重排）：
        #   ① 内容优先（与第三人称共有的前提，但要明说它**不推导机位**）；
        #   ② 数据视野朝向（镜头已固定 ⇒ 视野有限，见 `_first_person_framing`）；
        #   ③ 取景筛分（装不下的按层级省略）。
        # ⚠ 与第 38 刀的关系：第 38 刀「内容优先、机位是因变量」已在上方**限定给第三人称**
        #   （用户原话「划分给第三人称」）⇒ 第一人称走本条，不再复用那条的「机位自由」含义。
        #   **不是推翻第 38 刀**，而是补它的**人称分支**。
        # ⚠ `scope` 为空（无接触证据）⇒ 只保留「不入画」底款 ⇒ 零回归锁。
        + scope
        # 2026-10-23：画面事实写明对方比叙事者高 ⇒ 眼高 ＋ 轻微仰拍（无证据 ⇒ 空串）。
        + height_clause
        + f"【视角·第一人称·谁是{who}】本帧人称由**用户手选**确定＝第一人称，"
        f"**不得据正文的叙述人称推翻它**：正文**不一定**以「我」叙述——它可能用**第三人称**"
        f"点名{who}、或只用「他」指代他，**这仍然是第一人称**：正文里的主人公就是{who}"
        f"＝**镜头本身**。请**按语境**认出他（他可能被其他角色点名、也可能只以「他」"
        f"出现在动作里），**无论他叫什么、正文用第几人称写他、输入画面事实里是否写了他的"
        f"外貌与服装，都不得把他算作画面主体**。"
        f"{who}的**身体局部**"
        f"（手、手臂、腿、腰、臀、**下体/男根**等）**只在输入画面事实写明时才入画**"
        f"（输入画面事实＝本帧摘录正文，以及其中以「叙事者局部入画事实」标注的行）——"
        f"有出处就按事实写具体"
        f"（窗口写了「抓住她的臀部」⇒ 写可见的双手；写了「肉棒拍在她脸上」⇒ 就要把这段器官"
        f"写进画面、**从画面下缘进入**压在她脸部接触），**没写就一律不写**；"
        f"⚠ 「有出处就写」**仍受本帧取景上限约束**（上一条）：有出处 ≠ 可以因此把镜头推远、"
        f"把{who}的躯干或面部带进画面；超出取景上限的部分**只写接触点那一小段、其余省略**。"
        f"不得为了体现第一人称而凭空"
        f"补一只手或任何身体部位。"
        # ═══ 第 69 刀（2026-09-29 用户口径）：**接触形态的具体写法** ═══════════════
        # 用户原话：「你的描述应该是一个腿被手掰开、肉棒贴在女角色的小穴口隔着内裤布料磨蹭、
        #   女角色内裤湿透透明。这种描述就没有说主人公完整入场，只是**描述有器官在对应的位置**」。
        # 动机：第 68 刀只做了「**剔掉元语言**」这一半（减法），剔完会出现**描述真空**
        #   （真机 slot `-54-2` 剔完只剩「Only the woman is visible, her lower face …
        #   draw the eye downward」）⇒ 必须补上「**该怎么写**」这一半（加法）。
        # ⚠ 三条自我约束（与第 68 刀同一批口径）：
        #   ① 例子**一律条件式**：只有画面事实写明才写，承接上一条「没写就一律不写」；
        #   ② **只引身体部位与接触词，不引任何机位词**（否则会诱发第 60 刀的禁用机位）；
        #   ③ 无出处的编造由第 70 刀的出处闸机械兜底。
        f"【视角·第一人称·接触形态的具体写法】{who}的部位入画时**只写器官在对应位置**——"
        f"写清**哪一段器官、在谁身上的哪个位置、是隔着衣物还是直接接触**；"
        f"本帧取景上限与「只写接触点那一小段」的约束**仍然全部适用**。"
        f"两种形态（**仅在输入画面事实写明时才写**，没写一律不写）："
        f"①**撬开／推动肢体**——事实写了把她的腿拨开、架起或压向某处 ⇒ 写成"
        f"`a hand prying her leg open`／`her leg lifted and pressed aside by a hand`"
        f"（只写手与腿，**不写这只手的主人**，也不写它从哪来）；"
        f"②**隔着衣物的接触**——事实写了器官贴上去隔着布磨蹭、布面洇湿 ⇒ 写成"
        f"`the shaft pressed against her through the damp fabric`／"
        f"`the wet cloth clinging translucent to the skin`，"
        f"⚠ **只写衣物本身可见的状态**（湿透、透出底色、被顶起、贴出形状），"
        f"**不得**补写衣物之下的器官形态或内部构造。"
        f"不得写{who}的脸或全身，也不得把{who}写成画面里的第二个人；画面主体数只算其他角色。"
        # 2026-09-21 第 31 刀（当时定案「第一人称＝看向镜头」）：镜头就是叙事者的眼睛 ⇒
        # 剧情写「看向叙事者」时翻译成「看向镜头」；把叙事者另写成画面里的第三方人物
        # （真机 L3·natural 曾写 `a dominant third figure who stands just beyond the frame`）
        # 与「叙事者不入画」自相矛盾，必须一并禁掉。
        # ⚠ 2026-09-22 用户纠正：**不得强制**视线指向镜头 —— 视线方向**按剧情**。
        # 2026-09-21 第 33 刀（与第三人称同口径）：**身体朝向**按画面事实——
        # 背向式姿势（撅臀、掰开臀瓣呈现给某人）身体是**背对镜头**的。
        f"【视角·视线与朝向（第一人称）】**视线方向按本轮画面事实写**——只有当剧情/画面事实明确写到"
        f"某角色看向、注视、回望{who}（＝镜头）时，才写成看向镜头；**不得默认或强制**让角色看向镜头"
        f"（没写就不写）。**身体朝向按画面事实写**——「撅起臀部、掰开臀瓣」这类**背向式**姿势，"
        f"身体是**背对镜头**的（臀部朝事实指明的那一方）；头与视线也按画面事实写"
        f"（事实写回望才回望）；"
        f"不得写成「面向镜头」的正对式措辞。不得把{who}另写成画面里的第三方人物、旁观者或"
        f"「画面之外的某个人」，也不得写他站在画面里或占据画面位置。"
        # 第 51 刀 · FP6（2026-09-22 登记）：「看向/亲吻/接触主人公」＝「看向/亲吻/接触镜头」。
        f"【视角·接触镜头（第一人称）】正文里其他角色「看向/亲吻/接触{who}」"
        f"＝「看向/亲吻/接触**镜头**」——因为镜头就是{who}的眼睛；不得把这种接触写成"
        f"「接触画面里的另一个人」或「接触第三方」，也不得因此把主人公画进画面。"
    )


def _english_scene_details(scene: Mapping[str, object]) -> list[str]:
    """从混合语言 SceneSpec 中保留可直接使用的英文高潮、外貌与艺术事实。"""
    english_details: list[str] = []
    art_direction = scene.get("art_direction")
    candidates = [
        _current_visual_source(scene), scene.get("locale"),
        scene.get("camera"), scene.get("composition"), scene.get("draft_prompt"),
    ]
    subjects = scene.get("subjects")
    actors = scene.get("actors")
    actor_names = {
        str(name).strip() for name in actors if str(name).strip()
    } if isinstance(actors, list) else set()
    if isinstance(subjects, list):
        candidates.extend(
            item.get("description") for item in subjects
            if isinstance(item, Mapping)
            and (
                not actor_names
                or not str(item.get("name") or "").strip()
                or str(item.get("name") or "").strip() in actor_names
            )
        )
    if isinstance(art_direction, Mapping):
        candidates.extend(art_direction.values())
    encounter = scene.get("encounter")
    if isinstance(encounter, Mapping):
        candidates.extend(encounter.values())
    for value in candidates:
        detail = " ".join(str(value or "").split()).strip(" ,.;")
        # 角色外貌常是“中文名: English tags”；兜底不能因中文身份前缀丢掉后面的精确英文特征。
        if _CJK_RE.search(detail):
            detail = detail.encode("ascii", "ignore").decode("ascii")
            detail = " ".join(detail.split()).strip(" :,.;-")
        if detail and re.search(r"[A-Za-z]{3}", detail) and not _REFUSAL_RE.search(detail):
            english_details.append(detail)
    return list(dict.fromkeys([
        *english_details, *_mapped_visual_facts(scene), *_evidenced_visual_facts(scene),
        *_required_event_facts(scene), *_narrator_limb_facts(scene),
    ]))


def _concrete_scene_facts(
    scene: Mapping[str, object], *, include_draft_prompt: bool = True,
) -> list[str]:
    """统一事实底座：只收人物、当前服装、动作、道具、地点与空间关系。"""
    direct_facts: list[str] = []
    for key in ("appearance", "wardrobe"):
        value = _subject_scoped_appearance(scene) if key == "appearance" \
            else str(scene.get(key) or "")
        detail = " ".join(value.split()).strip(" ,.;")
        if detail and detail.isascii() and re.search(r"[A-Za-z]{3}", detail):
            direct_facts.append(detail)
    facts = [*direct_facts, *_mapped_visual_facts(scene), *_scene_action_tags(scene),
             *_evidenced_visual_facts(scene), *_required_event_facts(scene),
             *_narrator_limb_facts(scene)]
    locations = [
        phrase for pattern, phrase in _LOCATION_RULES
        if re.search(pattern, str(scene.get("locale") or ""))
    ]
    facts.extend(locations)
    subjects = scene.get("subjects")
    actors = set(_character_names({"actors": scene.get("actors", [])}))
    if isinstance(subjects, list):
        for item in subjects:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if actors and name and name not in actors:
                continue
            detail = " ".join(str(item.get("description") or "").split()).strip(" ,.;")
            if detail and detail.isascii() and detail not in facts:
                facts.append(detail)
    keys = ["appearance", "camera", "composition"]
    if include_draft_prompt:
        keys.insert(1, "draft_prompt")
    for key in keys:
        raw_detail = _subject_scoped_appearance(scene) if key == "appearance" \
            else str(scene.get(key) or "")
        if key == "draft_prompt":
            # 同轮草稿常包含独立质量行；事实底座只取内容行，不能把质量词重复塞进正文。
            detail_lines = [line.strip() for line in raw_detail.splitlines() if line.strip()]
            raw_detail = detail_lines[-1] if detail_lines else ""
        detail = " ".join(raw_detail.split()).strip(" ,.;")
        if _CJK_RE.search(detail):
            detail = " ".join(detail.encode("ascii", "ignore").decode("ascii").split()).strip(" ,.;:-")
        if detail and re.search(r"[A-Za-z]{3}", detail) and detail not in facts:
            facts.append(detail)
    return list(dict.fromkeys(fact for fact in facts if fact.strip()))


def _scene_camera_tag(scene: Mapping[str, object]) -> str:
    """景别服从要展示的内容，不固定套 medium shot。

    ⚠ 2026-09-24 第 55 刀（用户定案）：第一人称下**镜头已固定在叙事者眼睛上** ⇒ 景别不再
    「服从要展示的内容」（那是第三人称的因变量逻辑），而受**取景上限**约束
    （`_first_person_framing`）。真机事故：turn 52 写 `close-up` 却同时要求
    `three-quarter back view` + 女主全身侧躺 + 抬腿 + 白袜 ⇒ 自相矛盾、物理装不下。
    ⇒ 第一人称下：先取判据/计划的显式景别，**再按取景上限收窄**（只收不放——
    显式要求的更近景别保留，更远景别一律收窄到上限）。
    `viewpoint` 非 `first_person`（含缺失）⇒ **逐字节等于旧行为**（零回归锁）。
    """
    source = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    explicit = str(scene.get("camera") or "").strip()
    tag = ""
    if explicit and explicit.isascii():
        match = re.search(
            r"\b(?:extreme close-up|close-up|close shot|medium close-up|medium shot|"
            r"medium wide shot|wide shot|long shot|full shot)\b", explicit, re.I,
        )
        if match:
            tag = match.group(0).lower()
    if not tag:
        if re.search(r"亲吻|眼睛|瞳|嘴唇|内裤|局部|特写|脸部", source):
            tag = "close-up"
        elif re.search(r"远景|全景|城市全貌|群山|天际线|辽阔|远处风景", source):
            tag = "wide shot"
        else:
            tag = "medium shot"
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return tag
    # ── 第 55／56 刀：第一人称取景收束（只收不放）───────────────────────────
    # ⚠ 第 56 刀（用户第二次纠正）：**只有近身接触帧才收**。风景/环境帧
    #   （`framing` 给出的是「放行全景」条款）**绝不能被收成 medium close-up**——
    #   用户原话「你不能限制死特写中景……只是描写风景……基本彰显不了风景全貌」。
    framing = _first_person_framing(scene)
    if not framing or "不设近景上限" in framing:
        # 无接触证据 / 风景帧 ⇒ 不收景别（逐字节等于旧行为，或按画面事实放行全景）。
        return tag
    cap = "medium close-up"
    _ORDER = (
        "extreme close-up", "close-up", "close shot", "medium close-up",
        "medium shot", "medium wide shot", "wide shot", "long shot", "full shot",
    )
    try:
        if _ORDER.index(tag) > _ORDER.index(cap):
            return cap
    except ValueError:
        pass
    return tag


# 动名词事实（2026-09-20 第 27 刀）：`withdrawing the fingers` / `swaying her hips` 这类**动作片段**
# 只能出现在动作槽；`depicted with` 是名词槽（名词短语 / 独立主格），接动名词即病句。
_GERUND_FACT_RE = re.compile(r"^[A-Za-z]+ing\b", re.I)


# 动作事实的**施动者先行**形状（2026-09-21 第 31 刀）：`both hands spreading her buttocks
# apart` 这类以**施动部位**开头的串，句法上是**动作**（该落 tags 行 / 动作槽），不是
# `depicted with` 槽要的名词短语。旧口径只认动名词（`_GERUND_FACT_RE`）⇒ 新串会被误判成状态，
# 被拼成 `The two adult characters are depicted with both hands spreading her buttocks apart.`
# 这种「复数主语＋单数物主」病句（第 29 刀同族）。
# 2026-09-22 第 43 刀：并入**男根一族**（`an erect penis slapping her face`，第一人称「叙事者
# 局部入画」的措辞）。同一条理由——它是本帧的**动作**，若留在状态槽会被复述成
# `The primary adult woman is depicted with an erect penis slapping her face.`
# （主语是女性主体、器官却属于画外的叙事者 ⇒ 与第 29 刀同族的「主语/物主错配」病句）。
# ⚠ 认冠词 `a/an/the` 的放宽**只作用在男根一族**：hand 一族维持原样 ⇒ 既有事实零影响
#   （`a hand resting on her buttocks` 这类接触措辞仍不进动作槽）。
_ACTION_FACT_LEAD_RE = re.compile(
    r"^\s*(?:both\s+hands?|two\s+hands?|hands?|"
    r"(?:(?:a|an|the)\s+)?(?:erect\s+)?(?:penis|shaft|cock))\b",
    re.I,
)


def _is_action_fact(fact: str) -> bool:
    """动作事实＝动名词开头（既有口径）**或**施动部位先行（第 31 刀补，第 43 刀并入男根一族）。"""
    value = fact or ""
    return bool(_GERUND_FACT_RE.match(value) or _ACTION_FACT_LEAD_RE.match(value))


def _environment_phrase(
    scene: Mapping[str, object], *, default: str = "a layered environmental background",
) -> str:
    """环境短语：**有地点事实就用地点**，没有才落回分层背景（2026-09-20 用户口径）。

    地点来源的优先级由调用方保证（本轮正文/状态 → 数据表的上一轮旧值）。这里只做两件事：
    ① 中文地点按 `_LOCATION_RULES` 映射成英文（映射不到 → 退回默认值，不硬塞中文）；
    ② 过长或带分隔符的地点串不塞进句子（会读成病句）→ 退回默认值。
    """
    locale = " ".join(str(scene.get("locale") or "").split()).strip(" ,.;")
    if locale and not locale.isascii():
        mapped = [
            phrase for pattern, phrase in _LOCATION_RULES if re.search(pattern, locale)
        ]
        locale = mapped[0] if mapped else ""
    if not locale or len(locale) > 48 or "," in locale or ";" in locale:
        return default
    return f"a layered {locale} environment"


def _two_subject_phrase(scene: Mapping[str, object]) -> str:
    """两人帧的**主体名词短语**，按画面主体的实际性别组成（第 46 刀，纯函数）。

    · 全女 ⇒ `two adult women`（＝第 24 刀旧措辞**逐字节一致**，零回归）；
    · 一男一女 ⇒ `one adult woman and one adult man`；
    · 全男 ⇒ `two adult men`；
    · 性别解析不全（缺键 / 有空 / 长度不符）⇒ 中性 `two adult characters`
      （用户定案「**不写**具体性别族」，宁缺勿猜）。
    """
    genders = _contract_subject_genders(scene)
    if len(genders) != 2 or any(not str(gender).strip() for gender in genders):
        return "two adult characters"
    girls = sum(1 for gender in genders if "女" in str(gender))
    boys = sum(1 for gender in genders if "男" in str(gender))
    if girls + boys != 2:
        return "two adult characters"
    if girls == 2:
        return "two adult women"
    if boys == 2:
        return "two adult men"
    return "one adult woman and one adult man"


_FIELD_PATTERNS = {
    "appearance": re.compile(r"\b(?:hair|eyes?|gaze|face|lips?|cheeks?|figure|bust|waist|hips?|legs?)\b", re.I),
    "wardrobe": re.compile(r"\b(?:dress|robe|skirt|shirt|jacket|coat|stockings?|socks?|fabric|garment|clothing)\b", re.I),
    "location": re.compile(r"\b(?:bedchamber|chamber|room|cell|corridor|railing|gate|entrance|forest|street|hall|enclosure|village|outdoors?)\b", re.I),
    "camera": re.compile(r"\b(?:shot|view|angle|lens|focal|perspective|depth of field|camera)\b", re.I),
    "composition": re.compile(r"\b(?:composition|foreground|background|negative space|visual focus|frame|thirds?|diagonal)\b", re.I),
    "lighting": re.compile(r"\b(?:light|lighting|shadow|highlight|backlit|rim light|color temperature)\b", re.I),
    "material": re.compile(r"\b(?:material|texture|fabric|silk|gauze|folds?|skin|hair|wood|metal|wet|moisture)\b", re.I),
    "quality": re.compile(r"\b(?:anatomy|contours?|clean edges?|fine detail|fidelity|polished|best quality|masterpiece)\b", re.I),
}


_LOCATION_RULES = (
    (r"寝殿|卧室", "bedchamber"),
    (r"天牢|牢房|囚室|禁闭室", "confinement cell"),
    (r"长廊|走廊", "corridor"),
    (r"栏杆", "railing"),
    (r"山门", "mountain gate"),
    (r"孤儿院.{0,8}门|门.{0,8}孤儿院", "orphanage entrance"),
    (r"森林|树林", "forest"),
)


def _evidenced_visual_facts(scene: Mapping[str, object]) -> list[str]:
    """开放类型视觉事实：只信任带本轮正文证据且为英文成稿的条目。"""
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    values = scene.get("visual_facts")
    if not isinstance(values, list):
        return []
    facts: list[str] = []
    for item in values[:12]:
        if not isinstance(item, Mapping):
            continue
        fact = str(item.get("fact") or "").strip()
        evidence = image_prompt_extract.restore_jailbreak(
            str(item.get("evidence") or ""),
        ).strip()
        if fact and fact.isascii() and evidence and evidence in narrative:
            facts.append(fact)
    return list(dict.fromkeys(facts))


def _expected_present(prompt: str, phrase: str) -> bool:
    # T1：事件性必现事实（体液/睡眠）也在这张配对表里找自己的 output_pattern，
    # 否则会退化成按词拆的粗判（`cum on face` 要求 cum+face 至少 2 词同时命中）。
    for _source_pattern, known_phrase, output_pattern in (*_VISUAL_FACT_RULES, *_EVENT_FACT_RULES):
        if known_phrase == phrase:
            return bool(re.search(output_pattern, prompt, re.I))
    words = [word for word in re.findall(r"[a-z]{3,}", phrase.lower())
             if word not in {"with", "into", "from", "that", "this", "very", "adult"}]
    if not words:
        return True
    def matches(word: str) -> bool:
        variants = [word]
        if word.endswith("ing") and len(word) > 5:
            stem = word[:-3]
            if len(stem) > 2 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            variants.append(stem)
        return any(re.search(rf"\b{re.escape(value)}\w*\b", prompt, re.I) for value in variants)

    present = sum(1 for word in dict.fromkeys(words) if matches(word))
    return present >= max(1, (len(set(words)) + 1) // 2)


def prompt_field_ledger(prompt: str, scene: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """逐字段验收最终成稿；只记录可由 SceneSpec 客观验证的事实。"""
    fact_scene = _scene_for_facts(scene)
    appearance_source = str(fact_scene.get("appearance") or "")
    subjects = fact_scene.get("subjects")
    subject_visuals = "\n".join(
        str(item.get("description") or "") for item in subjects
        if isinstance(item, Mapping)
    ) if isinstance(subjects, list) else ""
    wardrobe_source = "\n".join((
        appearance_source,
        str(fact_scene.get("wardrobe") or ""),
        str(fact_scene.get("narrative") or "") if re.search(
            r"衣|裙|袍|袜|dress|robe|skirt|stocking", str(fact_scene.get("narrative") or ""), re.I,
        ) else "",
        subject_visuals,
    ))
    appearance_expected = [
        fact for fact in _mapped_facts_from(appearance_source)
        if fact not in _mapped_wardrobe_facts_from(appearance_source)
    ]
    wardrobe_expected = _mapped_wardrobe_facts_from(wardrobe_source)
    # 2026-10-30：有角色段落头 ⇒ 英文外观事实**按主体条目**取（含「中文名＋英文外貌」
    # 这种 `isascii()` 判假、旧实现整段漏掉的情形）；无段落头才走旧的整块拆分。
    scoped_groups = _subject_appearance_fact_groups(fact_scene)
    if scoped_groups:
        appearance_expected.extend(
            fact for facts in scoped_groups.values() for fact in facts
            if fact not in _mapped_wardrobe_facts_from(fact)
        )
    elif appearance_source.isascii():
        appearance_expected.extend(
            part.strip(" ,.;") for part in appearance_source.split(",")
            if _FIELD_PATTERNS["appearance"].search(part)
            and not _FIELD_PATTERNS["wardrobe"].search(part)
        )
    direct_wardrobe = str(fact_scene.get("wardrobe") or "")
    if direct_wardrobe.isascii():
        wardrobe_expected.extend(
            part.strip(" ,.;") for part in direct_wardrobe.split(",")
            if part.strip(" ,.;") and _FIELD_PATTERNS["wardrobe"].search(part)
        )
    appearance_expected = list(dict.fromkeys(filter(None, appearance_expected)))
    wardrobe_expected = list(dict.fromkeys(filter(None, wardrobe_expected)))
    # 第 43 刀：第一人称「叙事者局部入画」事实并入**账本可验证的动作期望** ⇒ 模型漏写时由
    # `complete_field_coverage` 补进成稿（anima/niji 进 tags 行、散文档进正文尾巴），
    # 不再只靠模型自觉。归**动作槽**而非状态槽：它是本帧的决定性动作，且状态槽会被
    # `_anima_content_description` 复述成 `The primary adult woman is depicted with … penis …`
    # 这种「主语不是器官持有者」的病句（第 27/29 刀同族）。
    actions = list(dict.fromkeys([
        *_scene_action_tags(fact_scene),
        *_narrator_limb_facts(fact_scene),
    ]))
    visual_facts = list(dict.fromkeys([
        *_evidenced_visual_facts(fact_scene),
        # T1：事件性必现事实（体液/睡眠）——提取器常漏写 `scene.visual_facts`，
        # 若只认它，`required` 会恒为 False ⇒ 这类事实零校验零补充（真机实锤）。
        # 并进同一槽位 ⇒ 既进 `required`、又进 `expected`，由 `complete_field_coverage` 补写。
        *_required_event_facts(fact_scene),
    ]))
    locale = str(fact_scene.get("locale") or "")
    locations = [phrase for pattern, phrase in _LOCATION_RULES if re.search(pattern, locale)]
    if locale.isascii() and re.search(r"[A-Za-z]{3}", locale):
        locations.append(locale.strip(" ,.;"))
    requirements = {
        "appearance": bool(appearance_source.strip()),
        "wardrobe": bool(str(fact_scene.get("wardrobe") or "").strip() or wardrobe_expected),
        "action": bool(actions),
        "visual_facts": bool(visual_facts),
        "location": bool(locale.strip()),
        "camera": True,
        "composition": True,
        "lighting": True,
        "material": True,
        "quality": True,
    }
    expected = {
        "appearance": appearance_expected,
        "wardrobe": wardrobe_expected,
        "action": actions,
        "visual_facts": visual_facts,
        "location": locations,
    }
    ledger: dict[str, dict[str, object]] = {}
    for field, required in requirements.items():
        phrases = expected.get(field, [])
        # T3（2026-09-23 真机实锤）：`covered` 必须与**修补层同判据**。
        # `complete_field_coverage` 会把「同部位已写过动作」的候选交给第 29 刀的冲突闸剔除
        # （`_action_fact_conflicts`）；若这里仍按**未过滤**的候选做 `all()`，被剔的那条
        # **永远不会命中**（模型本来就不该再写它）⇒ `action.covered` 变成**永不消失的假红**，
        # `missing_fields` 里恒挂 `action`、把 trace 观测变成噪声（真机：6 条候选里 3 条被豁免、
        # 余 3 条全已命中，却仍报 `covered=False`）。
        # ⚠ 适用范围限定：**只对 action 生效**，其余字段（含 appearance/location 的
        # 「无可验证期望 ⇒ 不判覆盖」设计）逐字不动。
        applicable = phrases
        if field == "action" and phrases:
            applicable = [item for item in phrases if not _action_fact_conflicts(prompt, item)]
        if applicable:
            covered = all(_expected_present(prompt, phrase) for phrase in applicable)
            if field == "location" and not covered:
                covered = bool(_FIELD_PATTERNS["location"].search(prompt))
        elif phrases and field == "action":
            # 候选**全部**被第 29 刀的冲突闸豁免 ⇒ 名单为空，但事实源仍在：本帧没有
            # 「该补而未补」的动作 ⇒ 视为已覆盖（不能落进下面的 `covered = False`）。
            covered = True
        elif required and field in {
            "appearance", "wardrobe", "action", "location", "visual_facts",
        }:
            # 有事实来源却没有可验证的英文期望时，通用 hair/room/action 等词不能
            # 冒充已覆盖；否则本地词典漏项会把空泛模板放行到 ComfyUI。
            covered = False
        elif field in _FIELD_PATTERNS:
            covered = bool(_FIELD_PATTERNS[field].search(prompt))
        else:
            covered = not required
        if field == "appearance" and _VAGUE_IDENTITY_RE.search(prompt):
            covered = False
        ledger[field] = {
            "required": required,
            "covered": bool(covered or not required),
            "expected": phrases,
        }
    return ledger
