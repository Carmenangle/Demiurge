"""剧情正文去 AI 味 lint：确定性文风检查（纯函数，0 I/O、0 LLM）。

词表是**单一属主，一份两用**：本模块的 lint 用它检测，S1 生成侧预防的 system
约束段也从它编译（见 docs/PLAN-DEAIFLAVOR-ROLEPLAY.md）。两处消费禁止各自
维护一份（漂移即打回）。

检测不涂改：只产出诊断 dict（code/message/evidence/severity），不返回改写文本；
turn/id/status 由 narrative_ci.diagnostic_entry 统一补齐。lint 前先还原防拦截拆字
（@()@ 只读还原，不落库）。

误报控制两档：EXACT_PHRASES 单轮出现即报；密度类（破折号/省略号/「不是…而是…」）
须超阈值才报。词表小而准优先，用户增删走设置（落用户态文件，待 S1）。
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from difflib import SequenceMatcher
from pathlib import Path

from app.config import DATA_DIR
from app.services import json_file_store
from app.services.prompt_clean import restore_jailbreak

# ── 诊断码（本模块是文风诊断码的属主，narrative_ci 并流时直接引用）────────────
CODE_STYLE_BANNED_PHRASE = "style_banned_phrase"      # 套路句式/空洞大词/讨好腔
CODE_STYLE_PUNCT_DENSITY = "style_punct_density"      # 破折号/省略号密度超标
CODE_STYLE_RHYTHM_METRONOME = "style_rhythm_metronome"  # 句长节拍器感
CODE_STYLE_SELF_QA = "style_self_qa"                  # 自问自答（难道…？不，…）
CODE_STYLE_PATTERN_REPEAT = "style_pattern_repeat"    # 同一句式模板单轮重复
CODE_STYLE_OPENING_CUE = "style_opening_cue"          # 跨轮开场趋同
CODE_STYLE_LIVING_REVIEW = "style_living_review"      # S2 LLM 活人感通审综合判定
CODE_STYLE_WORD_ECHO = "style_word_echo"              # 同词连用排比（没有，没有，没有）
CODE_STYLE_MICRO_PARAGRAPH = "style_micro_paragraph"  # 两三字短语单独成段（左手。手松了。）
CODE_STYLE_TERM_RATCHET = "style_term_ratchet"        # 跨来源术语棘轮（同一多字词被反复确认）

# 单轮出现即报（warning）：空洞大词 / 套话 / 讨好腔 / 起手式。
EXACT_PHRASES: tuple[str, ...] = (
    # 空洞大词
    "赋能", "闭环", "抓手", "底层逻辑", "颗粒度", "顶层设计",
    "降维打击", "组合拳", "护城河",
    # 套话
    "值得注意的是", "不难发现", "众所周知", "综上所述", "总而言之",
    "毋庸置疑", "不得不说", "先说答案",
    # 讨好腔
    "稳稳地接住", "稳稳的接住", "你的观察很敏锐", "你的观察很明锐",
    "我理解你的感受", "这是一个很好的问题",
)

# 破折号「——」与省略号「……」密度阈值：每千字出现次数（同时要求绝对次数 ≥2 才计）。
_DASH = "——"
_ELLIPSIS = "……"
_DENSITY_PER_KILO = 2.5

# 「不是A，而是B／不是A，是B」模板族：单轮 ≥2 次才报（单次在正常中文里合法）。
_NOT_BUT_RE = re.compile(r"不是[^。！？；\n]{1,18}，?(?:而是|是)")
# 同词连用排比：「没有，没有，没有」——同一 1-6 字词紧邻重复 ≥3 次。
_WORD_ECHO_RE = re.compile(r"([\u4e00-\u9fff]{1,6})(?:[，、,\s]+\1){2}")
# 微段落：两三字短语单独成段（「左手。」「手松了。」）——单轮 ≥2 段即模板化。
_MICRO_PARAGRAPH_MIN_REPEAT = 2

# 自问自答：难道…？不/并非/未必…
_SELF_QA_RE = re.compile(r"难道[^。！？\n]{1,30}[？?]\s*(?:不|并非|未必|当然不)")

# 节拍器：句数足够多且句长方差极小（每句约等长）才报。
_MIN_SENTENCES = 8
_STD_THRESHOLD = 4.0

# 跨轮开场趋同：开头窗口长度与判定参数。
_OPENING_WINDOW = 15
_OPENING_MIN_HISTORY = 2
_OPENING_SIMILARITY = 0.8
_OPENING_PREFIX = 4  # 归一化后公共前缀 ≥4 字（如「夜色深沉…」连用）即视为同款开场

_SENTENCE_END_RE = re.compile(r"[。！？!?…]+")
_STRIP_PUNCT_RE = re.compile(r"[\s，。！？；：、「」『』（）()·…—\-\"]+")


def lint(text: str, *, recent_openings: list[str] | tuple[str, ...] = (),
         banned_phrases: tuple[str, ...] | None = None,
         ratchet_injected: tuple[str, ...] = (),
         ratchet_bodies: tuple[str, ...] = (),
         ratchet_exclude: tuple[str, ...] = (),
         ratchet_setting: tuple[str, ...] = ()) -> list[dict]:
    """返回文风诊断列表；不阻断、不改写正文。每项含 code/message/evidence/severity。

    banned_phrases 为 None 时用内置词表；调用方（agent_graph）传
    ``effective_phrases(load_config())`` 即启用用户增删词表。

    ratchet_injected / ratchet_bodies / ratchet_exclude / ratchet_setting 为**可选**的
    跨来源术语棘轮输入（见 ``term_ratchet_report``）：默认四者皆空 ⇒ 不产生该码诊断，
    与改造前逐字节同行为（等价矩阵锁）。ratchet_setting 传设定存量来源
    （世界书设定条目 + 角色卡静态底座），用于把「设定支撑的高频」与「无锚点膨胀」分开。
    """
    body = restore_jailbreak(text or "")
    if not body.strip():
        return []
    diagnostics: list[dict] = []
    for phrase in (EXACT_PHRASES if banned_phrases is None else banned_phrases):
        index = body.find(phrase)
        if index >= 0:
            diagnostics.append(_diagnostic(
                CODE_STYLE_BANNED_PHRASE,
                f"正文出现 AI 味固定搭配「{phrase}」。",
                _context(body, index, index + len(phrase)),
            ))
    diagnostics.extend(_check_punct_density(body))
    diagnostics.extend(_check_pattern_repeat(body))
    diagnostics.extend(_check_word_echo(body))
    diagnostics.extend(_check_micro_paragraph(body))
    diagnostics.extend(_check_rhythm(body))
    diagnostics.extend(_check_opening_cue(body, recent_openings))
    diagnostics.extend(term_ratchet_report(
        injected=ratchet_injected, bodies=ratchet_bodies, exclude=ratchet_exclude,
        setting=ratchet_setting))
    return diagnostics


def _diagnostic(code: str, message: str, evidence: str, severity: str = "warning") -> dict:
    return {"code": code, "message": message, "evidence": evidence, "severity": severity}


def _context(body: str, start: int, end: int, pad: int = 8) -> str:
    return body[max(0, start - pad):min(len(body), end + pad)]


def _check_punct_density(body: str) -> list[dict]:
    out: list[dict] = []
    kilo = max(len(body), 1) / 1000.0
    for mark, name, min_count in ((_DASH, "破折号", 2), (_ELLIPSIS, "省略号", 2)):
        count = body.count(mark)
        # 破折号有棘轮效应：AI 用一次后续断句就越来越依赖（2026-08-30 用户实锤），
        # ≥2 次即报，不再看密度；省略号维持密度阈值。
        exceeded = (count >= min_count and name == "破折号") or (
            count >= 2 and count / kilo > _DENSITY_PER_KILO)
        if exceeded:
            first = body.find(mark)
            out.append(_diagnostic(
                CODE_STYLE_PUNCT_DENSITY,
                f"{name}使用过度（{count} 次），正文应以逗号/句号断句。",
                _context(body, first, first + len(mark)),
            ))
    return out


def _check_pattern_repeat(body: str) -> list[dict]:
    out: list[dict] = []
    matches = list(_NOT_BUT_RE.finditer(body))
    if len(matches) >= 2:
        out.append(_diagnostic(
            CODE_STYLE_PATTERN_REPEAT,
            f"「不是…而是…」句式单轮重复 {len(matches)} 次（≥2 即模板化）。",
            _context(body, matches[0].start(), matches[0].end()),
        ))
    qa = _SELF_QA_RE.search(body)
    if qa:
        out.append(_diagnostic(
            CODE_STYLE_SELF_QA,
            "自问自答句式（难道…？不/并非…）。",
            _context(body, qa.start(), qa.end()),
        ))
    # 对话回声变体：「知道了吗？」「我知道了。」「不，你不知道」——问句被复述再否定
    echo = re.search(
        r"(?:你知道了吗|你知道吗|明白了吗|懂了吗)[？?][\s\S]{0,80}?不[，,]\s*你不知道", body)
    if echo:
        out.append(_diagnostic(
            CODE_STYLE_SELF_QA,
            "对话回声句式（…吗？…了。不，你…）。",
            _context(body, echo.start(), echo.end()),
        ))
    return out


def _check_word_echo(body: str) -> list[dict]:
    """同词连用排比：同一 1-6 字词紧邻重复 ≥3 次（没有，没有，没有）。"""
    out: list[dict] = []
    m = _WORD_ECHO_RE.search(body)
    if m:
        out.append(_diagnostic(
            CODE_STYLE_WORD_ECHO,
            f"同词连用排比：「{m.group(1)}」连续重复 3 次以上。",
            _context(body, m.start(), m.end()),
        ))
    return out


def _check_micro_paragraph(body: str) -> list[dict]:
    """两三字短语单独成段：「左手。」「手松了。」——单轮 ≥2 段即模板化。"""
    out: list[dict] = []
    hits: list[str] = []
    for para in re.split(r"(?:\r?\n){2,}", body):
        seg = para.strip()
        if not seg or seg.startswith(("<", "「", '"', "[", "＼")):
            continue  # 标签块/对话/列表不是强调微段
        core = _STRIP_PUNCT_RE.sub("", seg)
        if 0 < len(core) <= 6:
            hits.append(seg)
    if len(hits) >= _MICRO_PARAGRAPH_MIN_REPEAT:
        out.append(_diagnostic(
            CODE_STYLE_MICRO_PARAGRAPH,
            f"超短强调段单轮 {len(hits)} 处（如「{hits[0]}」「{hits[1]}」），观感碎裂。",
            " / ".join(hits[:3]),
        ))
    return out


def _check_rhythm(body: str) -> list[dict]:
    lengths = [len(seg.strip()) for seg in _SENTENCE_END_RE.split(body) if seg.strip()]
    if len(lengths) < _MIN_SENTENCES:
        return []
    mean = sum(lengths) / len(lengths)
    if not (6.0 <= mean <= 40.0):
        return []
    variance = sum((v - mean) ** 2 for v in lengths) / len(lengths)
    std = variance ** 0.5
    if std >= _STD_THRESHOLD:
        return []
    return [_diagnostic(
        CODE_STYLE_RHYTHM_METRONOME,
        f"句长节拍器感：{len(lengths)} 句均长 {mean:.0f} 字、标准差仅 {std:.1f}（阈值 {_STD_THRESHOLD}）。",
        body[:40],
        severity="info",
    )]


def _check_opening_cue(body: str, recent_openings: list[str] | tuple[str, ...]) -> list[dict]:
    history = [o for o in recent_openings if (o or "").strip()]
    if len(history) < _OPENING_MIN_HISTORY:
        return []
    current = _normalize(body[:_OPENING_WINDOW])
    if not current:
        return []
    similar = sum(1 for o in history if _similar(o, current))
    if similar < _OPENING_MIN_HISTORY:
        return []
    return [_diagnostic(
        CODE_STYLE_OPENING_CUE,
        f"开场趋同：本轮开头与最近 {len(history)} 层中 {similar} 层高度相似（≥2 即模板化开场）。",
        body[:_OPENING_WINDOW],
        severity="info",
    )]


def _normalize(text: str) -> str:
    return _STRIP_PUNCT_RE.sub("", text)[:_OPENING_WINDOW]


def _similar(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na == nb or SequenceMatcher(None, na, nb).ratio() >= _OPENING_SIMILARITY:
        return True
    prefix = 0
    for ca, cb in zip(na, nb):
        if ca != cb:
            break
        prefix += 1
    return prefix >= _OPENING_PREFIX


# ── 术语棘轮（跨来源）────────────────────────────────────────────────────────
# 与破折号棘轮同构（见 _check_punct_density）：都是「用过一次就越来越依赖」的正反馈。
# 差别只在载体——破折号是**单轮 token**，本项是**跨轮/跨来源的持久层文本**：模型把某个
# 说法写进数据表/世界书/前情后，每轮被原样注入 ⇒ 下一轮当作既定事实再确认一遍 ⇒ 单调累积
# 而不收敛。2026-09-23 真机实锤：「经脉」在注入块里 33 次、跨 31 个不同句子（冗余倍数 1.0×，
# 即每个位置都在**换个说法再确认一遍**，去重无效），而 assistant 正文里"看经脉""守经脉"
# **0 命中**——它从未被说出口，只活在持久层里。
_TERM_MIN_LEN = 2
_TERM_MAX_LEN = 4
_TERM_MIN_TOTAL = 6       # 跨「注入层 + 正文」合计出现次数下限
_TERM_MAX_REPORT = 8      # 单轮最多报几项（按次数降序）
_TERM_FRAGMENT_RATIO = 0.4  # 更长候选词达本词此比例 ⇒ 判本词为切词碎片（见 term_ratchet_report）

# 高频虚词/泛用词：跨来源出现属正常，不构成术语棘轮。
_TERM_STOPWORDS = frozenset((
    "已经", "没有", "自己", "一个", "不是", "这个", "那个", "什么", "时候", "这样",
    "一样", "还是", "只是", "因为", "所以", "现在", "可以", "知道", "看到", "起来",
    "下来", "上去", "过去", "出来", "东西", "地方", "一时", "一瞬", "有些", "有点",
    "同时", "仿佛", "似乎", "终于", "仍然", "依然", "以及", "其他", "其中", "两人",
    "二人", "对方", "彼此", "他们", "她们", "我们", "你们", "到底", "于是", "反而",
    "本来", "未必", "究竟", "哪怕", "除非", "无论", "不管", "既然", "甚至", "而况",
    "也没有", "没有开口", "都没有", "也没什么", "什么也没", "没有说话", "不动", "没有动",
))

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

# 边界虚字：真正的术语极少以这些字开头或结尾。用来剔掉「了一」「一下就」这种动词短语
# 切词（实测噪音主力）；副作用是「中衣」这类以「中」起头的词会被一并放弃——对**诊断用**
# 的候选清单可接受，且阈值/词表都在本模块内可调。
_TERM_EDGE_CHARS = frozenset(
    "的了着过一不也是把被就都还又很更最和与或而但则之其所以为于从到向对给让使"
    "没在要会能可吗呢吧啊呀哦嗯"
)


def _cjk_ngrams(text: str) -> list[str]:
    """抽取 2–4 字中文 n-gram（只在连续汉字串内取，不跨标点）。"""
    grams: list[str] = []
    for run in _CJK_RUN_RE.findall(text):
        for size in range(_TERM_MIN_LEN, _TERM_MAX_LEN + 1):
            grams.extend(run[i:i + size] for i in range(len(run) - size + 1))
    return grams


def term_ratchet_report(*, injected: Iterable[str] = (),
                        bodies: Iterable[str] = (),
                        exclude: Iterable[str] = (),
                        setting: Iterable[str] = (),
                        min_total: int = _TERM_MIN_TOTAL,
                        max_report: int = _TERM_MAX_REPORT) -> list[dict]:
    """跨来源「术语棘轮」检测：同一个多字词**在注入层里反复出现、且正文也在沿用**。

    与破折号棘轮同构（见 ``_check_punct_density``）：都是「用过一次就越来越依赖」的正反馈；
    差别只在载体——破折号是**单轮 token**，本项是**跨轮/跨来源的持久层文本**：模型把某个
    说法写进数据表/世界书后被每轮原样注入 ⇒ 下一轮当作既定事实再确认一遍 ⇒ 单调累积不收敛。

    判据刻意**要求两组来源都命中**（injected ≥1 且 bodies ≥1）：

    * 只躺在世界书/角色卡里的词（标题、分阶名）不是本缺陷，报出来只会刷屏
      （实测：「好感／感度／度表」是「好感度表」的切词碎片，只在注入层）；
    * 只在正文里高频的词属单轮文风问题，由既有 lint 各项负责。

    典型命中＝「经脉」（2026-09-23 实锤）：注入层 16+ 次、最近数轮正文也在用，
    而正文里「看经脉」「守经脉」**0 命中** ⇒ 被反复确认的是**持久层里的名目**，不是剧情。

    exclude 传角色名/称呼等合法高频词；**含名字的更长/更短片段一并豁免**
    （否则「妲千」「千沁」这类切词碎片仍会冒出来）。

    纯函数：0 I/O、0 LLM、不改写正文。只产出诊断 dict。
    """
    inj = [str(s) for s in injected if s and str(s).strip()]
    bod = [str(s) for s in bodies if s and str(s).strip()]
    if not inj or not bod:
        return []
    texts = inj + bod
    per_source = [Counter(_cjk_ngrams(t)) for t in texts]
    set_texts = [str(s) for s in setting if s and str(s).strip()]
    stock: Counter[str] = Counter()
    for _s in set_texts:
        stock.update(_cjk_ngrams(_s))
    total: Counter[str] = Counter()
    appear: Counter[str] = Counter()
    in_inj: Counter[str] = Counter()
    in_bod: Counter[str] = Counter()
    for i, counts in enumerate(per_source):
        total.update(counts)
        for term in counts:
            appear[term] += 1
            (in_inj if i < len(inj) else in_bod)[term] += 1
    skip = _TERM_STOPWORDS | {str(e).strip() for e in (exclude or ()) if str(e).strip()}
    pool = {
        term: n for term, n in total.items()
        if n >= min_total and in_inj[term] and in_bod[term]
        and term not in skip and not any(s in term or term in s for s in skip)
        and term[0] not in _TERM_EDGE_CHARS and term[-1] not in _TERM_EDGE_CHARS
    }
    # 「切词碎片」片区：若某个**更长**候选词的出现次数已达本词的 40%，说明本词多是作为
    # 更长词的片段出现，而非独立术语（实测：「天狐」被「天狐宫」解释、切出「狐宫」；
    # 「了一／一下」是动词短语片段）。不剔掉它们，报告会被碎片刷屏。
    fragments = {
        term for term, n in pool.items()
        if any(len(other) > len(term) and term in other and cnt >= _TERM_FRAGMENT_RATIO * n
               for other, cnt in pool.items())
    }
    candidates = [(term, n) for term, n in pool.items() if term not in fragments]
    # 次数降序、同次数先短后长；再抑制「含已收录词」的词（避免 经脉/经脉图 一族重复占位）
    candidates.sort(key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
    accepted: list[tuple[str, int]] = []
    for term, n in candidates:
        if any(term in seen or seen in term for seen, _ in accepted):
            continue
        accepted.append((term, n))
        if len(accepted) >= max_report:
            break
    # 2026-09-23 用户口径（续「对经脉执着」）：单看频次无法区分「设定支撑的高频」与
    # 「无锚点膨胀」。用户原话——「天狐宫这种多次出现还能理解（主角大半后宫在此、后期势力归男主），
    # 但这个经脉，你看设定条目都不怎么提及，就前期提了一嘴，结果一个个女主角找的借口都是经脉」。
    # ⇒ 传 setting（设定存量来源：世界书设定条目 + 角色卡静态底座）时，按「用量/存量」降序重排，
    #   并把设定里完全没有的（日常描写词，如 指腹/掌心/里侧）排到最后。缺省时顺序逐字不变。
    if set_texts:
        accepted.sort(key=lambda kv: (
            0 if stock.get(kv[0], 0) == 0 else 1,
            kv[1] / max(stock.get(kv[0], 0), 1),
        ), reverse=True)
    out: list[dict] = []
    for term, n in accepted:
        where = [i for i, counts in enumerate(per_source) if counts[term]]
        idx = where[0]
        pos = texts[idx].find(term)
        head = (f"术语棘轮：多字词「{term}」注入层 {in_inj[term]} 处、正文 {in_bod[term]} 处"
                f"（合计 {n} 次 ≥{min_total}）")
        if set_texts:
            s0 = stock.get(term, 0)
            head += (f"；设定存量 {s0} 次、用量/存量 {n / s0:.1f}" if s0
                     else "；设定里从未出现（更像模型自造的说法）")
        out.append(_diagnostic(
            CODE_STYLE_TERM_RATCHET,
            head + "——疑似被反复重新确认，"
            "请核对该说法在剧情里是否真的发生过；若只是给动作补的名目，应清掉。",
            _context(texts[idx], pos, pos + len(term)),
        ))
    return out


def style_prompt_segment(config: dict | None = None) -> str:
    """S1 生成侧预防：从同一词表编译进 roleplay system 的约束段（词表漂移由单测锁定）。

    config 为 None 或 enabled=False 时返回空串——关闭开关时 system 与现状逐字节一致。
    """
    cfg = config or {}
    if not cfg.get("enabled", True):
        return ""
    phrases = effective_phrases(cfg)
    if not phrases:
        return ""
    listed = "、".join(f"「{p}」" for p in phrases)
    return (
        "\n\n【文风要求】叙述用具体细节与动作，避免空洞概括与套路腔。"
        f"不得使用以下固定搭配：{listed}。"
        "对比句式「不是A，而是B／不是A，是B／是不是…」一轮最多出现一次，禁止连用凑洞察感；"
        "禁止同词连用排比（如「没有，没有，没有」），同一词紧邻重复最多两次；"
        "禁止把两三个字的短语单独放一段（如「左手。」「手松了。」），强调必须融进完整句子里；"
        "禁止对话回声（「知道了吗？」「我知道了。」「不，你不知道」这类一问一复述再否定）；"
        "正文禁止使用破折号「——」断句；需要插入或转折时改用逗号、句号或冒号；"
        "省略号「……」全文克制，每千字不超过两处；"
        "不用自问自答句式（难道…？不，…）；不用「首先/其次/最后」式三段表态；"
        "句长长短交错，避免每句等长。"
    )


# ── 用户态配置（对标 builtin_agents 用户覆盖模式；lint 本体保持纯函数）────────

def _config_path() -> Path:
    return DATA_DIR / "prose_style.json"


def load_config() -> dict:
    """读用户态配置 {"enabled": bool, "extra": [...], "removed": [...]}。

    缺文件/坏 JSON → 内置默认（enabled=True，零增删）。坏字段类型按缺省处理。
    """
    config = _read_config_file()
    try:
        review_every = max(0, int(config.get("review_every", 5)))
    except (TypeError, ValueError):
        review_every = 5
    return {
        "review_every": review_every,
        "enabled": bool(config.get("enabled", True)),
        "extra": [str(w) for w in (config.get("extra") or ()) if str(w).strip()]
        if isinstance(config.get("extra"), list) else [],
        "removed": [str(w) for w in (config.get("removed") or ()) if str(w).strip()]
        if isinstance(config.get("removed"), list) else [],
    }


def save_config(config: dict) -> dict:
    """落盘用户态配置，只保留 enabled/extra/removed 合法字段（未知字段丢弃）。"""
    clean = load_config() | {"enabled": bool(config.get("enabled", True))}
    try:
        clean["review_every"] = max(0, int(config.get("review_every", 5)))
    except (TypeError, ValueError):
        pass
    raw_extra = config.get("extra")
    raw_removed = config.get("removed")
    if isinstance(raw_extra, list):
        clean["extra"] = [str(w).strip() for w in raw_extra if str(w).strip()]
    if isinstance(raw_removed, list):
        clean["removed"] = [str(w).strip() for w in raw_removed if str(w).strip()]
    path = _config_path()
    json_file_store.write_json(path, clean)
    return clean


def _read_config_file() -> dict:
    # 容错面刻意用 ERRORS_WITH_OS（含 OSError）：与 palette_pref 同档，与列表型仓库不同档。
    return json_file_store.read_dict(_config_path(), errors=json_file_store.ERRORS_WITH_OS)


def effective_phrases(config: dict | None = None) -> tuple[str, ...]:
    """内置词表 + 用户增 − 用户删；单一属主的生效词表（lint 与生成侧共用）。"""
    cfg = config or {}
    extra_raw = cfg.get("extra") or ()
    extra = tuple(w for w in extra_raw if w not in EXACT_PHRASES)
    removed = frozenset(cfg.get("removed") or ())
    return tuple(w for w in (*EXACT_PHRASES, *extra) if w not in removed)
