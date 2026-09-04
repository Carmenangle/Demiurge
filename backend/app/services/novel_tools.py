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

import html
import io
import re
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
# 红线词（年龄口径预警）：命中只提示 Agent 复核，不代判
REDLINE_WORDS = ("岁", "小学", "初中", "高中", "高校", "学生", "少女", "幼女", "萝莉",
                 "未成年", "中学", "年级", "同班", "学妹", "学弟", "校服")
# 匿名硬禁词（零命中才算过）；软禁词仅统计供复核
HARD_WORDS = ("幼女", "萝莉", "小学生", "初中生", "女童", "幼童", "儿童色情")
SOFT_WORDS = ("未成年", "孩童", "儿童")
# 单花括号 {user} = f-string 陷阱（数据模块用普通字符串时会把 {{user}} 吃掉一层）
_SINGLE_BRACE_USER = re.compile(r"(?<!\{)\{user\}(?!\})")


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


def split_chapters(text: str) -> list[dict[str, str]]:
    """按「===== 名 =====」把全文切成章节；无标记时整本作单章。"""
    matches = list(CHAPTER_MARKER.finditer(text))
    if not matches:
        return [{"name": "", "text": text}]
    chapters: list[dict[str, str]] = []
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            chapters.append({"name": m.group(1).strip(), "text": body})
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
    """对分章全文做确定性清点：章节标题 / 称呼后缀候选名词频 / 红线词计数。

    top_names/max_titles 截断防工具结果爆量；红线词只计数不代判（年龄口径交 LLM）。
    """
    text = _read_text_safe(full_txt)
    chapters = split_chapters(text)
    counter: Counter[str] = Counter()
    for m in _NAME_RE.finditer(text):
        candidate = m.group(1)
        if candidate.isdigit() or len(candidate) < 2:
            continue
        counter[candidate] += 1
    redline: dict[str, int] = {}
    for word in REDLINE_WORDS:
        count = text.count(word)
        if count:
            redline[word] = count
    return {
        "source": str(full_txt),
        "total_chars": len(text),
        "chapter_count": len(chapters),
        "titles": [ch["name"] for ch in chapters[:max_titles] if ch["name"]],
        "name_counts": [[name, count] for name, count in counter.most_common(top_names)],
        "redline": redline,
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


def _pick_anchors(chapters: list[dict[str, str]], name: str,
                  window: tuple[int, int] = (180, 320)) -> list[dict[str, str]]:
    before, after = window
    hits: list[tuple[str, int, str]] = []
    for chapter in chapters:
        body = chapter["text"]
        for m in re.finditer(re.escape(name), body):
            hits.append((chapter["name"], m.start(), body))
    if not hits:
        return []
    picks = [hits[0]]
    if len(hits) >= 4:
        picks.append(hits[len(hits) // 2])
    if len(hits) >= 2:
        picks.append(hits[-1])
    out: list[dict[str, str]] = []
    seen: set[tuple[str, int]] = set()
    for chapter, idx, body in picks:
        key = (chapter, idx // 200)
        if key in seen:
            continue
        seen.add(key)
        start = max(0, idx - before)
        end = min(len(body), idx + after)
        out.append({"chapter": chapter, "snippet": body[start:end].replace("\n", " ").strip()})
    return out


def charfacts(full_txt: str, names: list[str], out_dir: str,
              mode: str = "top_n", max_paras: int = 40) -> dict[str, Any]:
    """按名单从全文切素材段，逐名落 <out_dir>/<name>.txt；返回统计与零命中名单。

    mode: top_n = 全书前 N 段完整段落（_feiji_charfacts 口径）；
          anchor = 首·中·末锚点 320 字窗口（_nv_charfacts 口径）。
    素材是中间产物，不是条目；模型只读素材文件后经 upsert_repo 写条目。
    """
    if mode not in ("top_n", "anchor"):
        raise NovelToolError(f"mode 仅支持 top_n/anchor：{mode!r}")
    text = _read_text_safe(full_txt)
    chapters = split_chapters(text)
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


# ── T4 scan_anonymity：落盘匿名/红线机械扫描 ────────────────────────────────


def _entry_fields(entry: dict[str, Any]) -> dict[str, str]:
    keys = entry.get("keys") or entry.get("key") or []
    if isinstance(keys, str):
        keys = [keys]
    return {
        "content": str(entry.get("content") or ""),
        "keys": " ".join(str(k) for k in keys if str(k).strip()),
        "comment": str(entry.get("comment") or ""),
    }


def scan_anonymity(entries: list[dict[str, Any]],
                   protagonist_names: list[str]) -> dict[str, Any]:
    """机械匿名/红线扫描（固化02 §3.6 + _feiji_audit §3 提炼）。

    - content/keys/comment 三处查主角名（含姓/名/爱称/带后缀形式，粒度由 LLM 给名单）；
    - 单花括号 {user}（f-string 陷阱）零命中；{{user}} 计数（0 = 漏写占位警告）；
    - 硬禁词零命中（阻断）；软禁词仅统计供复核。
    blocking=True 的 leak 任一命中即 passed=False（阻断交付）；占位缺失为警告不阻断。
    """
    names = [str(n).strip() for n in (protagonist_names or []) if str(n).strip()]
    leaks: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        fields = _entry_fields(entry)
        for field, value in fields.items():
            if not value:
                continue
            for name in names:
                pos = value.find(name)
                if pos == -1:
                    continue
                leaks.append({
                    "kind": "protagonist_leak", "name": name, "field": field,
                    "entry": idx, "blocking": True,
                    "snippet": value[max(0, pos - 12):pos + len(name) + 12],
                })
                break  # 每条每字段只报第一个名字一次，防刷屏
    blob_all = "\n".join(fields["content"] + "\n" + fields["keys"] + "\n" + fields["comment"]
                         for fields in (_entry_fields(e) for e in entries if isinstance(e, dict)))

    single_brace = list(_SINGLE_BRACE_USER.finditer(blob_all))
    if single_brace:
        for m in single_brace[:5]:
            leaks.append({"kind": "single_brace_user", "blocking": True,
                          "snippet": blob_all[max(0, m.start() - 12):m.end() + 12]})
    user_count = blob_all.count("{{user}}")
    if user_count == 0:
        leaks.append({"kind": "user_placeholder_missing", "blocking": False,
                      "detail": "条目中无 {{user}}，主角可能漏写占位（需人工复核是否确无主角视角）"})

    hard_hits = {w: blob_all.count(w) for w in HARD_WORDS if w in blob_all}
    for w in hard_hits:
        leaks.append({"kind": "hard_word", "word": w, "blocking": True, "count": hard_hits[w]})
    soft_stats = {w: blob_all.count(w) for w in SOFT_WORDS if w in blob_all}

    blocking = [leak for leak in leaks if leak.get("blocking")]
    return {
        "entries": len([e for e in (entries or []) if isinstance(e, dict)]),
        "protagonist_names": names,
        "user_placeholder_count": user_count,
        "leaks": leaks,
        "hard_hits": hard_hits,
        "soft_stats": soft_stats,
        "passed": not blocking,
    }
