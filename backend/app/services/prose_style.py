"""剧情正文去 AI 味 lint：确定性文风检查（纯函数，0 I/O、0 LLM）。

词表是**单一属主，一份两用**：本模块的 lint 用它检测，S1 生成侧预防的 system
约束段也从它编译（见 docs/PLAN-DEAIFLAVOR-ROLEPLAY.md）。两处消费禁止各自
维护一份（漂移即打回）。

检测不涂改：只产出诊断 dict（code/message/evidence/severity），不返回改写文本；
turn/id/status 由 narrative_ci._diagnostic 统一补齐。lint 前先还原防拦截拆字
（@()@ 只读还原，不落库）。

误报控制两档：EXACT_PHRASES 单轮出现即报；密度类（破折号/省略号/「不是…而是…」）
须超阈值才报。词表小而准优先，用户增删走设置（落用户态文件，待 S1）。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from app.config import DATA_DIR
from app.services.prompt_clean import restore_jailbreak

# ── 诊断码（本模块是文风诊断码的属主，narrative_ci 并流时直接引用）────────────
CODE_STYLE_BANNED_PHRASE = "style_banned_phrase"      # 套路句式/空洞大词/讨好腔
CODE_STYLE_PUNCT_DENSITY = "style_punct_density"      # 破折号/省略号密度超标
CODE_STYLE_RHYTHM_METRONOME = "style_rhythm_metronome"  # 句长节拍器感
CODE_STYLE_SELF_QA = "style_self_qa"                  # 自问自答（难道…？不，…）
CODE_STYLE_PATTERN_REPEAT = "style_pattern_repeat"    # 同一句式模板单轮重复
CODE_STYLE_OPENING_CUE = "style_opening_cue"          # 跨轮开场趋同
CODE_STYLE_LIVING_REVIEW = "style_living_review"      # S2 LLM 活人感通审综合判定

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

# 「不是A，而是B」模板：单轮 ≥2 次才报（单次在正常中文里合法）。
_NOT_BUT_RE = re.compile(r"不是[^。！？；\n]{1,18}，?而是")

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
         banned_phrases: tuple[str, ...] | None = None) -> list[dict]:
    """返回文风诊断列表；不阻断、不改写正文。每项含 code/message/evidence/severity。

    banned_phrases 为 None 时用内置词表；调用方（agent_graph）传
    ``effective_phrases(load_config())`` 即启用用户增删词表。
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
    diagnostics.extend(_check_rhythm(body))
    diagnostics.extend(_check_opening_cue(body, recent_openings))
    return diagnostics


def _diagnostic(code: str, message: str, evidence: str, severity: str = "warning") -> dict:
    return {"code": code, "message": message, "evidence": evidence, "severity": severity}


def _context(body: str, start: int, end: int, pad: int = 8) -> str:
    return body[max(0, start - pad):min(len(body), end + pad)]


def _check_punct_density(body: str) -> list[dict]:
    out: list[dict] = []
    kilo = max(len(body), 1) / 1000.0
    for mark, name in ((_DASH, "破折号"), (_ELLIPSIS, "省略号")):
        count = body.count(mark)
        if count >= 2 and count / kilo > _DENSITY_PER_KILO:
            first = body.find(mark)
            out.append(_diagnostic(
                CODE_STYLE_PUNCT_DENSITY,
                f"{name}密度超标（{count} 次 / 每千字 {count / kilo:.1f} 次，阈值 {_DENSITY_PER_KILO}）。",
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
        "破折号与省略号节制使用；不用自问自答句式；"
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def _read_config_file() -> dict:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def effective_phrases(config: dict | None = None) -> tuple[str, ...]:
    """内置词表 + 用户增 − 用户删；单一属主的生效词表（lint 与生成侧共用）。"""
    cfg = config or {}
    extra_raw = cfg.get("extra") or ()
    extra = tuple(w for w in extra_raw if w not in EXACT_PHRASES)
    removed = frozenset(cfg.get("removed") or ())
    return tuple(w for w in (*EXACT_PHRASES, *extra) if w not in removed)
