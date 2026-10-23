"""小说预处理机械工具（固化02 脚本辅助层）：epub 抽取 / 全书清点 / 素材切段 / 匿名扫描。

设计（docs/memory/curing02-novel-tools-revision-draft-2026-09-04.md）：
长篇小说 1M+ 字无法整本进 LLM 上下文，机械层只做「分卷、统计、切素材、扫描」，
内容判断全部留给 LLM 转写（对齐固化03 §3.4 机械/人工边界口径）。本模块是这套
机械层的唯一属主——四个确定性函数，全部纯 stdlib、可单测；薄适配在
capability_handlers，注册在 capability_registry（novel.*）。

来源：D:\\tool\\SillyTavern\\novel 脚本管线（_extract_epub / _feiji_analyze /
_feiji_charfacts / _nv_charfacts / _feiji_audit §3）只读提炼；ST 组装/PNG/正则/
双存储部分一律丢弃（固化03 口径）。本模块不读配置、不依赖 routers。
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import re
import time
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MAX_EPUB_BYTES = 80 * 1024 * 1024  # 80MB：误指 PNG/图片原图/超大杂档直接拒绝
MAX_TEXT_BYTES = 120 * 1024 * 1024  # 清点/切段输入文本上限（源文件预检）

# 抽取时全文章节标记（extract 落盘格式，survey/charfacts 依赖同一标记语法）
CHAPTER_MARKER = re.compile(r"^===== (.+?) =====\s*$", re.M)

# 称呼后缀（ST 词频经验提炼：说/道/同学/老师/学姐/学长/君/酱/桑/大人/小姐/夫人/
# 母亲/父亲/儿子/女儿/队长/会长/公主/女王/魔王/陛下/少主/圣上）
_NAME_TERMS = (r"说|道|同学|老师|学姐|学长|君|酱|桑|大人|小姐|夫人|母亲|父亲"
               r"|儿子|女儿|队长|会长|公主|女王|魔王|陛下|少主|圣上")
_NAME_RE = re.compile(
    r"[（(]?([一-龥]{2,4}?|[A-Za-z]{2,12}?)[）)]?[，。、！？\s]*(?:" + _NAME_TERMS + r")"
)

# 2026-09-06 实锤修复：survey name_counts 噪音（用户核对《玫瑰与繁花》名单）——
# 「我知/或者/怎么/不知/她知/你知/你在/要知/谁知/也不知/他不知」全是「X+道/说」
# 句式碎片（我知道→我知+道、或者说→或者+说）；「绮丝笑着/戴茂笑着」是「名+动词着」
# 被 4 字非贪婪吞入（绮丝笑着说→候选=绮丝笑着+后缀说）。
# 三层清洗：裁剪动词尾 → 整词停用 → 虚词尾丢弃。
_NAME_CLIP_TAILS = frozenset("着了过的地得笑看问说想听走来去到见")
_NAME_DROP_TAILS = frozenset("知道是在也不就都还要能会可这那你我他她它们个些上中前边里时候然后于如若虽因为所以但而或与和者怎")
_NAME_PREFIX_CLIP = frozenset(
    "的是对在也不就都还要能会可这那你我他她它们个些中上里边时候然后于如若虽"
    "因为所以但而或与和怎么及更最太很贵")  # 候选前缀虚词/形容词（是黛绮丝→黛绮丝）
# 动词/形容词短语子串：命中即非人名（茂忍不住/不敢相信/戴茂疑惑/笑眯眯/我的）
_NAME_PHRASE_SUBSTR = ("忍不住", "不敢", "疑惑", "好奇", "脑袋", "激动",
                       "笑眯眯", "准确", "我的", "高贵", "怒斥", "抱怨",
                       "没有", "不用", "自己", "她的", "他的", "看着",
                       "好意思", "敢相信", "的阴")
_NAME_STOPWORDS = frozenset({
    "我知", "或者", "怎么", "不知", "她知", "你知", "你在", "要知", "谁知",
    "也不知", "他不知", "她也不知", "知道", "不知道", "可是", "但是", "只是",
    "于是", "然后", "虽然", "其实", "真的", "有点", "有些", "一时", "一定",
    "一下", "已经", "还是", "甚至", "恐怕", "大概", "也许", "或许", "难道",
    "到底", "什么", "为什么", "如何", "好像", "似乎", "仿佛", "几乎", "渐渐",
    "慢慢", "终于", "突然", "忽然", "连忙", "赶紧", "急忙", "立刻", "马上",
    "顿时", "随即", "接着", "跟着", "随后", "后来", "原来", "本来", "当然",
    "自然", "果然", "居然", "竟然", "显然", "明明", "只有", "只要", "无论",
    "不管", "如果", "要是", "即使", "哪怕", "不过", "然而", "因此", "所以",
    "因为", "由于", "为了", "对于", "关于", "根据", "按照", "通过", "经过",
    "随着", "作为", "成为", "就是", "而是", "不是", "也是", "更是", "还要",
    "还想", "还有", "会有", "可能", "可以", "能够", "应该", "应当", "必须",
    "需要", "开始", "继续", "起来", "出来", "下去", "过来", "过去", "回来",
    "回去", "进来", "进去", "上去", "下来", "看见", "发现", "觉得", "明白",
    "理解", "清楚", "相信", "感觉", "感到", "想到", "想起", "记得", "忘记",
    "听到", "听说", "看到", "望见", "遇见", "碰到", "遇到", "接受", "得到",
})


def _clean_name_candidate(candidate: str) -> str | None:
    """清洗 survey 候选名：裁剪动词尾、丢弃虚词碎片。返回 None=噪音。

    - 裁剪：「绮丝笑着说」候选「绮丝笑着」→ 裁「着」→「绮丝笑」→ 裁「笑」→「绮丝」；
    - 整词停用：我知/或者/怎么/不知…直接丢弃；
    - 虚词尾丢弃：尾字是 知/道/是/在/也/不/就/都/还/要/能/会/可/这/那/你/我/他/她/
      们/个/些/上/下/中/前/后/边/里/时/候/然/后/于/如/若/虽/因/为/所/以/但/而/或/
      与/和/者/怎/么 之一 → 丢弃（真实人名不以这些字结尾）。
    """
    name = candidate
    # 前缀虚词裁剪：是黛绮丝 → 黛绮丝；贵的公爵 → 公爵
    while len(name) > 2 and name[0] in _NAME_PREFIX_CLIP:
        name = name[1:]
    # 动词尾裁剪：绮丝笑着说 → 绮丝
    while len(name) > 2 and name[-1] in _NAME_CLIP_TAILS:
        name = name[:-1]
    if len(name) < 2 or name in _NAME_STOPWORDS:
        return None
    if any(part in name for part in _NAME_PHRASE_SUBSTR):
        return None
    # 含「不」非首字：动词短语（茂忍不住/不敢相信），真名几乎不以「不」居中出现
    if "不" in name[1:]:
        return None
    if name[-1] in _NAME_DROP_TAILS:
        return None
    return name

class NovelToolError(ValueError):
    pass


# ── T1 epub → 分章纯文本 ────────────────────────────────────────────────────


class _HtmlText(HTMLParser):
    """去 script/style、块级标签换行的极简 HTML→文本提取。"""

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.skip = False
        self._block = ("p", "div", "br", "h1", "h2", "h3", "h4", "li", "tr")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = False
        elif tag in self._block:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)

    def render(self) -> str:
        text = "".join(self.out)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()


def _read_archive_bytes(src: str) -> bytes:
    raw = Path(src)
    if not raw.is_file():
        raise NovelToolError(f"文件不存在：{src}")
    if not src.lower().endswith(".epub"):
        raise NovelToolError(f"仅支持 .epub：{src}")
    size = raw.stat().st_size
    if size > MAX_EPUB_BYTES:
        raise NovelToolError(f"文件过大（{size}>{MAX_EPUB_BYTES} 字节），请确认是小说 epub 而非原图/杂档")
    return raw.read_bytes()


def _spine_order(opf_text: str, opf_name: str) -> list[str]:
    """解析 OPF：manifest id→href + spine itemref→idref，返回按 spine 序的条目路径。

    相对 href 一律以 OPF 所在目录为基准解析（用归档首项算基准是错的：容器文件
    可能排在 content.opf 前面，2026-09-04 实测翻车）。
    """
    base_dir = Path(opf_name).parent
    manifest = dict(re.findall(r'<item[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf_text))
    refs = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf_text)
    ordered: list[str] = []
    for ref in refs:
        href = manifest.get(ref)
        if not href:
            continue
        ordered.append(str((base_dir / href).as_posix()))
    return ordered


def extract_epub(src: str, out_txt: str | None = None) -> dict[str, Any]:
    """抽取 epub 全文为分章文本；out_txt 给定时按「===== <名> =====」标记落盘。

    返回 {source, chapters, chars, titles(前 20), path?}；章序以 OPF spine 为准，
    spine 未收录的 xhtml 按文件名兜底排在末尾（与 ST 脚本同语义）。
    """
    data = _read_archive_bytes(src)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in archive.namelist() if not n.endswith("/")]
    except zipfile.BadZipFile as exc:
        raise NovelToolError(f"epub 解包失败（非 zip）：{exc}") from exc

    opf_candidates = [n for n in names if n.lower().endswith(".opf")]
    if not opf_candidates:
        raise NovelToolError("epub 内未找到 OPF 文件，无法确定章节顺序")
    opf_text = archive.read(opf_candidates[0]).decode("utf-8", "ignore")
    ordered = _spine_order(opf_text, opf_candidates[0])

    html_names = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]
    in_spine = set(ordered)
    seq = [n for n in ordered if n in html_names]
    seq += sorted(n for n in html_names if n not in in_spine)

    chapters: list[dict[str, str]] = []
    for name in seq:
        try:
            raw = archive.read(name)
        except KeyError:
            continue
        parser = _HtmlText()
        parser.feed(raw.decode("utf-8", "ignore"))
        text = parser.render()
        if text:
            chapters.append({"name": name, "text": text})
    if not chapters:
        raise NovelToolError("epub 抽取后无正文章节")

    path: str | None = None
    if out_txt:
        target = Path(out_txt)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n\n".join(f"===== {ch['name']} =====\n{ch['text']}" for ch in chapters),
            encoding="utf-8",
        )
        path = str(target)
    return {
        "source": src,
        "chapters": len(chapters),
        "chars": sum(len(ch["text"]) for ch in chapters),
        "titles": [ch["name"] for ch in chapters[:20]],
        "path": path,
    }


# ── 分章文本解析（extract 落盘与人工 txt 都吃同一标记语法） ─────────────────





# 2026-09-07 用户定案：原生小说章节标题识别（「第X章·标题」/「【书名】（第X章）标题」）——
# 之前 survey 只认 extract 的 ===== 标记，人工 txt 的原生「第X章」被漏掉(chapter_count=1 实锤)。
_NATIVE_CHAPTER_RE = re.compile(
    r"(?P<title>第[一二三四五六七八九十百千0-9０-９]+章[·`\.\s—\-]*[^\n【】]{0,30})"
    r"|(?P<wrap>【[^】]{2,20}】\s*[（(]第[一二三四五六七八九十百千0-9０-９]+章[）)][^\n]{0,30})",
    re.M,
)


def _native_heading_matches(text: str) -> list[Any]:
    """原生「第X章」**标题行**匹配（行级 plausibility 过滤）。

    背景（2026-10-20 实锤）：正文段落若以「第1章第2段正文…」开头，旧实现会把每一段
    都当章节头 → 21 万字的《60 章》小说切出上千个伪章节，真章节全被「<200 字残留头」
    规则跳过，chapter_count 塌成 1（索引/差异全失效）。标题行判据（机械、可解释）：
    行首位置 + 整行 ≤40 字 + 行内无句末标点（。！？；）。
    非常规排版兜底：过滤后不足 2 条而原始匹配 ≥5 条（标题与正文同行），退回旧口径，
    避免把整本塌成单章。
    """
    raw = [m for m in _NATIVE_CHAPTER_RE.finditer(text) if m.group(0).strip()]
    picked: list[Any] = []
    for m in raw:
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end].strip()
        if not line or len(line) > 40:
            continue
        if any(ch in line for ch in "。！？；"):
            continue
        picked.append(m)
    if len(picked) < 2 and len(raw) >= 5:
        return raw
    return picked


def split_chapters_native(text: str, min_title: int = 6) -> list[dict[str, Any]]:
    """按原生「第X章·标题」切分章节，返回 [{name, start, chars}]（含字符偏移，供定向 read_text）。

    去重：纯「第N章 作者/日期」残留头（标题过短且与下一个标记紧邻 <500 字符）跳过。
    无任何章节标记 → 整本作单章。
    """
    matches = _native_heading_matches(text)
    if not matches:
        return [{"name": "", "start": 0, "chars": len(text)}]
    chapters: list[dict[str, Any]] = []
    for idx, m in enumerate(matches):
        title = (m.group("title") or m.group("wrap") or "").strip()
        start = m.start()
        nxt = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body_len = nxt - start
        # 残留头跳过：本段 <200 字且不是最后一段（纯头部标记行，如「【书名】（第二章）作者」）；真实章节至少上千字不会误杀
        if body_len < 200 and idx + 1 < len(matches):
            continue
        body = text[start:nxt].strip()
        if not body:
            continue
        chapters.append({"name": title, "start": start, "chars": nxt - start})
    if not chapters:
        return [{"name": "", "start": 0, "chars": len(text)}]
    return chapters


def split_chapters(text: str) -> list[dict[str, Any]]:
    """按「===== 名 =====」把全文切成章节；无标记时整本作单章。

    2026-10-20：带 `start`/`chars` 绝对偏移（与 split_chapters_native 同形）——
    此前标记形态不给 offset，epub 抽取产物只能靠标题猜位置，无法定向读。
    """
    matches = list(CHAPTER_MARKER.finditer(text))
    if not matches:
        return [{"name": "", "text": text, "start": 0, "chars": len(text)}]
    chapters: list[dict[str, Any]] = []
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            chapters.append({"name": m.group(1).strip(), "text": body,
                             "start": m.start(), "chars": end - m.start()})
    return chapters


def _read_text_safe(path: str) -> str:
    raw = Path(path)
    if not raw.is_file():
        raise NovelToolError(f"文件不存在：{path}")
    if raw.stat().st_size > MAX_TEXT_BYTES:
        raise NovelToolError(f"文本过大（>{MAX_TEXT_BYTES} 字节）")
    return raw.read_text(encoding="utf-8", errors="replace")


# ── T2 survey：章节清点 + 候选名/红线词统计 ─────────────────────────────────


def survey_fulltext(full_txt: str, top_names: int = 60, max_titles: int = 40) -> dict[str, Any]:
    """对分章全文做确定性清点：章节标题 / 称呼后缀候选名词频。

    top_names/max_titles 截断防工具结果爆量。
    """
    text = _read_text_safe(full_txt)
    # 2026-09-07：优先原生「第X章」识别（人工 txt，带 start/chars 供定向 read_text）；extract 的 ===== 标记兜底
    native = split_chapters_native(text)
    if CHAPTER_MARKER.search(text):
        chapters = split_chapters(text)
    else:
        chapters = native
    counter: Counter[str] = Counter()
    for m in _NAME_RE.finditer(text):
        candidate = _clean_name_candidate(m.group(1))
        if not candidate:
            continue
        counter[candidate] += 1
    return {
        "source": str(full_txt),
        "total_chars": len(text),
        "chapter_count": len(chapters),
        "titles": [ch["name"] for ch in chapters[:max_titles] if ch["name"]],
        # 2026-10-20：两种形态都带 start/chars（split_chapters 已补偏移），
        # 不再按「首章有没有 start」分叉——否则 epub 抽取产物恒返回空 chapters。
        "chapters": [
            {"index": i + 1, "title": ch["name"], "chars": int(ch.get("chars") or 0),
             "start": int(ch.get("start") or 0)}
            for i, ch in enumerate(chapters[:max_titles])
        ],
        "name_counts": [[name, count] for name, count in counter.most_common(top_names)],
    }


def check_entry_density(entries: list[dict[str, Any]],
                        min_role_chars: int = 1800,  # 2026-09-07 用户定案：角色底线 1800（目标 2200）
                        min_mech_chars: int = 800,
                        min_event_chars: int = 600,  # 2026-09-09 用户定案：编号底线 600
                        min_nsfw_chars: int = 400) -> dict[str, Any]:
    """固化02 §4 密度检查（2026-09-07 对齐 ST 合集卡标准）：角色条目 ≥1800 字（目标 2200，底线 1800）、
    机制条目（系统判定/全局/局部）≥800 字、世界背景/大事件 ≥700 字、NSFW ≥400 字。

    ST 时代单卡合集 240KB 规模的关键就是密度：角色 2200-2800、机制 800-1400、
    世界/事件 700-1100、玩法 400-650（旧 400/200/200 太低 → 2KB 骨架卡实锤）。

    2026-09-07 治本（审计实锤，假通过根因）：
    - event 判定原用「编号开头 ^\\d+\\s*\\.」，但真实条目命名是
      「世界背景·1纳维茨帝国总纲」——前缀是「世界背景·」，编号在 · 之后，
      旧口径从未统计到任何 event → 世界背景密度形同虚设；
    - NSFW 维度整个缺失（ST 玩法 400-650 无检查）；
    - 现在按真实命名前缀分类：角色卡·/系统判定机制·全局机制·局部机制·/
      世界背景·/NSFW·，四类全覆盖。

    返回不达标清单（comment/chars/min/kind），passed=False 时模型据此补写。
    只统计不代写——密度不足提示 LLM 补写（与 §2.5 机械/LLM 分工一致）。
    """
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    role = [e for e in entries if (e.get("comment") or "").startswith("角色卡·")]
    mech = [e for e in entries if (e.get("comment") or "").startswith(
        ("系统判定机制·", "全局机制·", "局部机制·"))]
    event = [e for e in entries if (e.get("comment") or "").startswith("世界背景·")
             or bool(re.match(r"^\d+\.\s*", (e.get("comment") or "")))]
    nsfw = [e for e in entries if (e.get("comment") or "").startswith("NSFW·")]
    below: list[dict[str, Any]] = []
    for e in role:
        c = e.get("content") or ""
        if len(c) < min_role_chars:
            below.append({"comment": e.get("comment"), "chars": len(c),
                          "min": min_role_chars, "kind": "角色"})
    for e in mech:
        c = e.get("content") or ""
        if len(c) < min_mech_chars:
            below.append({"comment": e.get("comment"), "chars": len(c),
                          "min": min_mech_chars, "kind": "机制"})
    for e in event:
        c = e.get("content") or ""
        if len(c) < min_event_chars:
            below.append({"comment": e.get("comment"), "chars": len(c),
                          "min": min_event_chars, "kind": "背景/事件"})
    for e in nsfw:
        c = e.get("content") or ""
        if len(c) < min_nsfw_chars:
            below.append({"comment": e.get("comment"), "chars": len(c),
                          "min": min_nsfw_chars, "kind": "NSFW"})
    return {
        "total": len(entries),
        "checked": len(role) + len(mech) + len(event) + len(nsfw),
        "role": len(role), "mech": len(mech),
        "event": len(event), "nsfw": len(nsfw),
        "below": below,
        "passed": not below,
    }


# ── T3 charfacts：按名单切素材段 ─────────────────────────────────────────────


def _safe_segment(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("._")
    return cleaned or "name"


def _paragraphs(text: str, min_len: int = 20) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > min_len]


def _all_paragraphs(text: str, min_len: int = 20) -> list[str]:
    """整本文本切段，按章 body 切避免「===== 章节标记 =====」粘进首段。"""
    paragraphs: list[str] = []
    for chapter in split_chapters(text):
        paragraphs.extend(_paragraphs(chapter["text"], min_len))
    return paragraphs


def _pick_top_n(text: str, name: str, max_paras: int) -> list[str]:
    paras = _all_paragraphs(text)
    seen: set[str] = set()
    picked: list[str] = []
    for p in paras:
        if name not in p:
            continue
        if p in seen:
            continue
        seen.add(p)
        picked.append(p)
        if len(picked) >= max_paras:
            break
    return picked


def _pick_anchors(chapters: list[dict[str, Any]], name: str,
                  window: tuple[int, int] = (180, 320)) -> list[dict[str, str]]:
    before, after = window
    hits: list[tuple[str, int, str]] = []
    for ch in chapters:
        body = str(ch.get("text") or "")
        for m in re.finditer(re.escape(name), body):
            hits.append((str(ch.get("name") or ""), m.start(), body))
    if not hits:
        return []
    picks = [hits[0]]
    if len(hits) >= 4:
        picks.append(hits[len(hits) // 2])
    if len(hits) >= 2:
        picks.append(hits[-1])
    out: list[dict[str, str]] = []
    seen: set[tuple[str, int]] = set()
    for chapter_name, idx, body in picks:
        key = (chapter_name, idx // 200)
        if key in seen:
            continue
        seen.add(key)
        start = max(0, idx - before)
        end = min(len(body), idx + after)
        out.append({"chapter": chapter_name,
                    "snippet": body[start:end].replace("\n", " ").strip()})
    return out


def charfacts(full_txt: str, names: list[str], out_dir: str,
              mode: str = "top_n", max_paras: int = 40,
              chapter_start: int = 0, chapter_end: int = 0) -> dict[str, Any]:
    """按名单从全文切素材段，逐名落 <out_dir>/<name>.txt；返回统计与零命中名单。

    mode: top_n = 全书前 N 段完整段落（_feiji_charfacts 口径）；
          anchor = 首·中·末锚点 320 字窗口（_nv_charfacts 口径）。
    chapter_start/chapter_end（2026-09-06）：按章节索引范围（1 起）裁剪后再切素材——
    角色经历只取「剧情已推进到的章节」，禁止把未读/未来章节内容切进素材（防剧透）。
    0 = 不限（保留原行为）。
    素材是中间产物，不是条目；模型只读素材文件后经 upsert_repo 写条目。
    """
    if mode not in ("top_n", "anchor"):
        raise NovelToolError(f"mode 仅支持 top_n/anchor：{mode!r}")
    text = _read_text_safe(full_txt)
    chapters = split_chapters(text)
    # 章节范围裁剪（防剧透）：start/end 为章节序号（1 起），0 表示不限
    _cs = int(chapter_start or 0)
    _ce = int(chapter_end or 0)
    if _cs or _ce:
        _start = max(0, _cs - 1)
        _end = min(len(chapters), _ce if _ce else len(chapters))
        if _start < 0 or _end > len(chapters) or _start >= _end or _start >= len(chapters):
            raise NovelToolError(
                f"章节范围无效：start={chapter_start} end={chapter_end}（全文共 {len(chapters)} 章）")
        text = "\n\n".join(ch["text"] for ch in chapters[_start:_end])
        chapters = chapters[_start:_end]
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in names:
        if mode == "top_n":
            picked = _pick_top_n(text, name, max_paras)
            parts = picked
        else:
            anchors = _pick_anchors(chapters, name)
            parts = [f"-- [{a['chapter']}]\n{a['snippet']}" for a in anchors]
        if not parts:
            missing.append(name)
            continue
        body = "\n----------\n".join(parts)
        target = root / f"{_safe_segment(name)}.txt"
        target.write_text(body, encoding="utf-8")
        items.append({"name": name, "hits": len(parts), "chars": len(body),
                      "file": str(target)})
    return {
        "out_dir": str(root),
        "mode": mode,
        "requested": len(names),
        "written": len(items),
        "names": items,
        "missing": missing,
        "total_chars": sum(item["chars"] for item in items),
    }


# ── T4 增量定位：章节索引 + 新旧对比（2026-10-20 用户定案）──────────────────
#
# 目的：更新版小说（同一作品的新版本）做合集卡时，模型必须**只读新增/被改章节**，
# 不得整本重读（24 万字 ≈ 20 万 token 级，重读一遍就是白烧）。
# 机械层只做确定性定位：章节绝对偏移、空白无关的章节/段落哈希、三档匹配
# （同哈希 → 同标题 → 顺序）、阅读区间（read_plan，按 max_chars 分窗）、
# 新出现人名候选（更名/新角色初判）。
# 语义判断（哪些条目要改、怎么改、要不要合并）仍归自由循环——机械不裁决语义。
#
# 与固化02 §3.9 的关系：§3.9 定义「更新语义」（修订/新增/废弃/更名映射），
# 本节提供它缺的「输入范围界定」；索引落 <作品>/_prep/novel.index.json，
# 供下一次更新复用（跨 run 台账的第一步）。

INDEX_VERSION = 1
_MAX_INDEX_PARAS = 4000      # 单章段落哈希上限（防索引膨胀；相似度用上限内集合）
_MAX_LIST_OUT = 300          # 工具返回的清单条数上限（防工具结果爆量）
_DIFF_MIN_SIMILARITY = 0.5   # 顺序配对时判「同一章的修订」而非「删+增」的相似度下限

_TITLE_ORDINAL_RE = re.compile(
    r"^第\s*[0-9０-９零一二三四五六七八九十百千万两]{1,8}\s*[章节回卷部篇]\s*[·:：、.\-—\s]*")
_TITLE_NOISE_RE = re.compile(
    r"[\s\u3000·、，。．,:：;；()（）\[\]【】《》<>\"'“”‘’!?！？/\\\-—_]+")


def _norm_title(title: str) -> str:
    """标题归一：去掉「第X章」序号与标点空白，供跨版本同名对齐（重编号不影响）。"""
    cleaned = _TITLE_ORDINAL_RE.sub("", (title or "").strip())
    return _TITLE_NOISE_RE.sub("", cleaned).lower()


def _norm_body(text: str) -> str:
    """正文归一：抹平空白（换行/缩进/空格）——排版重排不应被当成内容变更。"""
    return re.sub(r"\s+", "", text or "")


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def chapter_records(text: str) -> list[dict[str, Any]]:
    """全文切章节，返回 [{index,title,start,chars,body}]；start/chars 是全文绝对偏移。

    三种形态：`===== 章节 =====`（extract 产物）优先 → 原生「第X章·标题」→ 整本单章。
    偏移是定向 read_text 的唯一依据（索引 / 差异 / 材料切段共用同一套）。
    """
    marks = list(CHAPTER_MARKER.finditer(text))
    if marks:
        out: list[dict[str, Any]] = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = text[m.end():end].strip()
            if not body:
                continue
            out.append({"index": len(out) + 1, "title": m.group(1).strip(),
                        "start": m.start(), "chars": end - m.start(), "body": body})
        if out:
            return out
    native = _native_heading_matches(text)
    if native:
        rows: list[dict[str, Any]] = []
        for i, m in enumerate(native):
            start = m.start()
            end = native[i + 1].start() if i + 1 < len(native) else len(text)
            # 残留头跳过（纯「第N章 作者/日期」行）：与 split_chapters_native 同口径
            if end - start < 200 and i + 1 < len(native):
                continue
            body = text[start:end].strip()
            if not body:
                continue
            rows.append({"index": len(rows) + 1,
                         "title": (m.group("title") or m.group("wrap") or "").strip(),
                         "start": start, "chars": end - start, "body": body})
        if rows:
            return rows
    return [{"index": 1, "title": "", "start": 0, "chars": len(text), "body": text.strip()}]


def _index_from_records(records: list[dict[str, Any]], total_chars: int) -> dict[str, Any]:
    chapters: list[dict[str, Any]] = []
    for rec in records:
        digests = [_sha12(_norm_body(p)) for p in _paragraphs(rec["body"])]
        chapters.append({
            "index": rec["index"],
            "title": rec["title"],
            "title_norm": _norm_title(rec["title"]),
            "start": rec["start"],
            "chars": rec["chars"],
            "sha1": _sha12(_norm_body(rec["body"])),
            "para_count": len(digests),
            "paras": digests[:_MAX_INDEX_PARAS],
        })
    return {"version": INDEX_VERSION, "total_chars": total_chars,
            "chapter_count": len(chapters), "chapters": chapters}


def build_index(text: str) -> dict[str, Any]:
    """构建章节索引（纯函数）：每章 sha1 + 段落哈希集合，供后续跨版本对比。"""
    return _index_from_records(chapter_records(text), len(text))


def write_index(full_txt: str, out_json: str) -> dict[str, Any]:
    """构建索引并落盘；返回**摘要**（不含段落哈希——工具结果不能爆量）。"""
    text = _read_text_safe(full_txt)
    index = build_index(text)
    target = Path(out_json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return {
        "source": str(full_txt),
        "index_path": str(target),
        "total_chars": index["total_chars"],
        "chapter_count": index["chapter_count"],
        "chapters": [{"index": c["index"], "title": c["title"], "start": c["start"],
                      "chars": c["chars"]} for c in index["chapters"][:_MAX_LIST_OUT]],
        "note": "索引已落盘（含每章 sha1 与段落哈希，不回传）。更新版对比用 novel.diff。",
    }


def read_index(path: str) -> dict[str, Any]:
    """读已落盘索引；缺失/损坏一律抛可操作错误（禁止静默退化成「全量重读」）。"""
    target = Path(path)
    if not target.is_file():
        raise NovelToolError(
            f"章节索引不存在：{path}。更新版对比必须先对旧版全文跑 novel.index 生成索引；"
            "没有旧索引就没有输入范围依据，不要整本重读旧文代替。")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NovelToolError(f"章节索引不可读（{exc}）：{path}") from exc
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, list) or not chapters:
        raise NovelToolError(f"章节索引没有章节数据：{path}（重建索引后再对比）")
    return data


def _similarity(old_paras: list[str], new_paras: list[str]) -> float:
    """段落哈希的多重集包含度：交集计数 / 较大侧总数。空集给 0（判重写，宁可多读）。

    用多重集而非 set：章节里重复出现的段落（叠句/仪式段）被折叠成一条时，
    改一段会让 set 相似度直接掉到 0——把 minor 修订误判成整章重写。
    """
    a = Counter(old_paras or [])
    b = Counter(new_paras or [])
    total = max(sum(a.values()), sum(b.values()))
    if not total:
        return 0.0
    return sum((a & b).values()) / total


def _name_counter(text: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for m in _NAME_RE.finditer(text or ""):
        candidate = _clean_name_candidate(m.group(1))
        if candidate:
            counter[candidate] += 1
    return counter


def _name_delta(changed_text: str, baseline_text: str,
                min_hits: int = 2, limit: int = 40) -> list[list[Any]]:
    """变更章节里新出现的人名（旧章节零出现）——新角色 / 更名候选的机械初判。

    只做候选提示：判定是否同一人（戴茂→黛茂）仍归模型语义判断（固化02 §3.9）。
    """
    changed = _name_counter(changed_text)
    baseline = _name_counter(baseline_text)
    out: list[list[Any]] = []
    for name, hits in changed.most_common():
        if hits < min_hits or baseline.get(name):
            continue
        out.append([name, hits, 0])
        if len(out) >= limit:
            break
    return out


def _read_plan(chapters: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """把待读章节展开成 read_text 窗口：[{chapter_index,title,start,max_chars}]。"""
    step = max(1000, int(max_chars or 20000))
    plan: list[dict[str, Any]] = []
    for ch in chapters:
        start = int(ch.get("start") or 0)
        end = start + max(0, int(ch.get("chars") or 0))
        pos = start
        while pos < end:
            plan.append({"chapter_index": int(ch.get("index") or 0),
                         "title": str(ch.get("title") or ""),
                         "start": pos, "max_chars": min(step, end - pos)})
            pos += step
    return plan


def diff_novel(*, new_txt: str, old_index: str | None = None, old_txt: str | None = None,
               max_chars: int = 20000, include_minor_read: bool = False,
               minor_ratio: float = 0.03, work_dir: str | None = None) -> dict[str, Any]:
    """新旧版本机械对比：unchanged / modified(minor|major) / added / removed + read_plan。

    匹配三档（确定性，与章节序号无关）：
    1. 同 sha1 → unchanged（插入章节导致的整体后移不会误判修订）；
    2. 剩余里归一标题唯一 1:1 → 同一章，按段落相似度判 minor/major；
    3. 仍剩余按顺序配对，相似度 ≥ _DIFF_MIN_SIMILARITY 判修订，否则 old=removed / new=added。

    read_plan 只含 added + modified(major)（minor 差异 < minor_ratio 默认不读，
    显式 include_minor_read=True 才纳入）。输出不含段落哈希。

    work_dir（2026-10-20，固化02 §3.9 第四步）：给了就把「本轮范围」落成
    `<work_dir>/_prep/novel.scope.json`（未变更章节区间 + read_plan），
    fabric 读拦截据此拒读未变更章节——把「只读新章」从提示词纪律变成机制。
    """
    idx_path = str(old_index or "").strip()
    old_path = str(old_txt or "").strip()
    if not idx_path and not old_path:
        raise NovelToolError(
            "缺少旧版基准：给 old_index（推荐，先对旧版跑 novel.index）或 old_txt（旧版全文）；"
            "没有基准无法界定输入范围。")
    if idx_path and Path(idx_path).is_file():
        old_data = read_index(idx_path)
    elif idx_path:
        raise NovelToolError(f"章节索引不存在：{idx_path}（先对旧版跑 novel.index，或改传 old_txt）")
    else:
        old_data = build_index(_read_text_safe(old_path))

    new_text = _read_text_safe(str(new_txt))
    records = chapter_records(new_text)
    new_index = _index_from_records(records, len(new_text))
    old_chapters = [c for c in old_data.get("chapters") or [] if isinstance(c, dict)]
    new_chapters = new_index["chapters"]

    unchanged: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    used_new: set[int] = set()
    matched_old: set[int] = set()

    # 1) 同 sha1
    buckets: dict[str, list[dict[str, Any]]] = {}
    for c in new_chapters:
        buckets.setdefault(str(c.get("sha1") or ""), []).append(c)
    for o in old_chapters:
        bucket = buckets.get(str(o.get("sha1") or ""))
        if not bucket:
            continue
        n = bucket.pop(0)
        used_new.add(int(n["index"]))
        matched_old.add(int(o["index"]))
        unchanged.append({"index_old": int(o["index"]), "index_new": int(n["index"]),
                          "title_old": str(o.get("title") or ""),
                          "title_new": str(n.get("title") or ""),
                          "chars": int(n.get("chars") or 0)})

    def _modify(o: dict[str, Any], n: dict[str, Any]) -> dict[str, Any]:
        ratio = round(1.0 - _similarity(list(o.get("paras") or []),
                                       list(n.get("paras") or [])), 4)
        item = {"index_old": int(o["index"]), "index_new": int(n["index"]),
                "title_old": str(o.get("title") or ""),
                "title_new": str(n.get("title") or ""),
                "start": int(n.get("start") or 0),
                "chars": int(n.get("chars") or 0),
                "change_ratio": ratio,
                "level": "minor" if ratio <= minor_ratio else "major"}
        modified.append(item)
        return item

    def _add(n: dict[str, Any]) -> None:
        added.append({"index_new": int(n["index"]), "title": str(n.get("title") or ""),
                      "start": int(n.get("start") or 0),
                      "chars": int(n.get("chars") or 0)})

    # 2) 归一标题唯一 1:1
    def _group(chapters: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for c in chapters:
            key = str(c.get("title_norm") or "")
            if key:
                out.setdefault(key, []).append(c)
        return out

    rest_old = [o for o in old_chapters if int(o["index"]) not in matched_old]
    rest_new = [n for n in new_chapters if int(n["index"]) not in used_new]
    old_by_title = _group(rest_old)
    new_by_title = _group(rest_new)
    for key, olds in old_by_title.items():
        news = new_by_title.get(key) or []
        if len(olds) != 1 or len(news) != 1:
            continue
        o, n = olds[0], news[0]
        _modify(o, n)
        matched_old.add(int(o["index"]))
        used_new.add(int(n["index"]))

    # 3) 剩余按顺序配对
    rest_old = sorted((o for o in old_chapters if int(o["index"]) not in matched_old),
                      key=lambda c: int(c["index"]))
    rest_new = sorted((n for n in new_chapters if int(n["index"]) not in used_new),
                      key=lambda c: int(c["index"]))
    for i in range(min(len(rest_old), len(rest_new))):
        o, n = rest_old[i], rest_new[i]
        sim = _similarity(list(o.get("paras") or []), list(n.get("paras") or []))
        if sim >= _DIFF_MIN_SIMILARITY:
            _modify(o, n)
            matched_old.add(int(o["index"]))
            used_new.add(int(n["index"]))
        else:
            removed.append({"index_old": int(o["index"]),
                            "title": str(o.get("title") or ""),
                            "chars": int(o.get("chars") or 0)})
            _add(n)
            matched_old.add(int(o["index"]))
            used_new.add(int(n["index"]))
    for o in rest_old:
        if int(o["index"]) not in matched_old:
            removed.append({"index_old": int(o["index"]),
                            "title": str(o.get("title") or ""),
                            "chars": int(o.get("chars") or 0)})
    for n in rest_new:
        if int(n["index"]) not in used_new:
            _add(n)

    # ── 阅读范围：只覆盖 added + modified(major)（+ 显式纳入的 minor）──
    to_read = [n for n in new_chapters
               if int(n["index"]) in {a["index_new"] for a in added}]
    major_idx = {m["index_new"] for m in modified if m["level"] == "major"}
    minor_idx = {m["index_new"] for m in modified if m["level"] == "minor"}
    to_read += [n for n in new_chapters if int(n["index"]) in major_idx]
    if include_minor_read:
        to_read += [n for n in new_chapters if int(n["index"]) in minor_idx]
    to_read.sort(key=lambda c: int(c["index"]))
    plan = _read_plan(to_read, int(max_chars or 20000))

    total_chars = new_index["total_chars"]
    changed_chars = sum(int(c.get("chars") or 0) for c in to_read)
    body_by_idx = {int(r["index"]): r["body"] for r in records}
    changed_text = "\n".join(body_by_idx.get(int(c["index"]), "") for c in to_read)
    baseline_text = "\n".join(r["body"] for r in records
                              if int(r["index"]) not in {int(c["index"]) for c in to_read})

    result: dict[str, Any] = {
        "old_source": idx_path or old_path,
        "new_source": str(new_txt),
        "old_chapter_count": len(old_chapters),
        "new_chapter_count": len(new_chapters),
        "total_chars": total_chars,
        "changed_chars": changed_chars,
        "coverage_ratio": round(changed_chars / total_chars, 4) if total_chars else 0.0,
        "unchanged": unchanged[:_MAX_LIST_OUT],
        "modified": modified[:_MAX_LIST_OUT],
        "added": added[:_MAX_LIST_OUT],
        "removed": removed[:_MAX_LIST_OUT],
        "read_plan": plan,
        "name_delta": _name_delta(changed_text, baseline_text),
        "hint": ("只读 read_plan 里的区间（file.read_text 按 start/max_chars）；unchanged 章节"
                 "对应的条目不要重写，minor 修订默认视为无需改条目。条目级影响面由覆盖台账"
                 "反查（worldbook.coverage + 变更章节号），不要为找线索整本重读。"),
    }
    wd = str(work_dir or "").strip()
    if wd:
        # 范围落盘：fabric 读拦截的基线（未变更章节整段读取会被拒）
        result["scope_path"] = str(write_scope(wd, result, new_chapters))
    return result


# ── T5 增量范围落盘 + 读拦截（2026-10-20，固化02 §3.9 第四步）─────────────
#
# 提示词纪律在长循环里必然衰减（模型第 20 步就开始重读旧章）。治本是把「本轮允许
# 读哪些区间」落成文件，由 fabric_loop 的 file.read_text 分支机械拦截。
# 范围文件与索引/台账同目录：<作品>/_prep/novel.scope.json。

SCOPE_VERSION = 1
SCOPE_FILE = "novel.scope.json"


def scope_path(work_dir: str) -> Path:
    return Path(str(work_dir)) / "_prep" / SCOPE_FILE


def write_scope(work_dir: str, result: dict[str, Any],
                new_chapters: list[dict[str, Any]]) -> Path:
    """把 diff 结果落成范围文件（只存读拦截必需的最小集合，不存条目内容）。"""
    unchanged_idx = {int(u["index_new"]) for u in result.get("unchanged") or []}
    payload = {
        "version": SCOPE_VERSION,
        "generated_at": time.time(),
        "new_source": result.get("new_source") or "",
        "old_source": result.get("old_source") or "",
        "unchanged_chapters": [
            {"index": int(c.get("index") or 0), "title": str(c.get("title") or ""),
             "start": int(c.get("start") or 0), "chars": int(c.get("chars") or 0)}
            for c in new_chapters if int(c.get("index") or 0) in unchanged_idx
        ],
        "read_plan": result.get("read_plan") or [],
        "counts": {
            "unchanged": len(result.get("unchanged") or []),
            "modified": len(result.get("modified") or []),
            "added": len(result.get("added") or []),
            "removed": len(result.get("removed") or []),
        },
        "coverage_ratio": result.get("coverage_ratio") or 0.0,
    }
    p = scope_path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_scope(dirs: list[str]) -> dict[str, Any] | None:
    """在候选目录里找最近一份范围文件（`<dir>/_prep/novel.scope.json`）。

    读不到/坏了返回 None（范围文件是增效信息，不能阻断普通读取）。
    """
    for d in dirs or []:
        if not str(d or "").strip():
            continue
        p = scope_path(str(d))
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("unchanged_chapters") is not None:
            data["_path"] = str(p)
            return data
    return None


def scope_blocks_read(scope: dict[str, Any] | None, target_path: str,
                      offset: int, max_chars: int) -> dict[str, Any] | None:
    """请求区间**完整落在**某个未变更章节内 → 返回该章节（供拦截文案），否则 None。

    只拦「整段都在未变章节里」的读：跨章/跨到变更章的读一律放行——宁可少拦，
    不可把合法读取拦死（模型确需上下文时要能通过「读大窗口」跳出）。
    """
    if not isinstance(scope, dict):
        return None
    new_src = str(scope.get("new_source") or "")
    if not new_src or not str(target_path or "").strip():
        return None
    try:
        if Path(new_src).resolve() != Path(str(target_path)).resolve():
            return None
    except OSError:
        if (new_src.replace("\\", "/").lower()
                != str(target_path).replace("\\", "/").lower()):
            return None
    start = max(0, int(offset or 0))
    end = start + max(1, int(max_chars or 20000))
    for ch in scope.get("unchanged_chapters") or []:
        if not isinstance(ch, dict):
            continue
        ch_start = int(ch.get("start") or 0)
        ch_end = ch_start + int(ch.get("chars") or 0)
        if ch_start <= start and end <= ch_end:
            return ch
    return None
