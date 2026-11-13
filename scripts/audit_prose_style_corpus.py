"""去 AI 味文风体检：拿**真实成稿语料**离线重放 prose_style lint（0 LLM、0 网络、只读）。

回答的是「哪几条码在打红、红得多厉害」，不是「lint 有没有 bug」——后者由单测负责。

语料 = `userdata/pictures/<作品>/…/chat.json` 里的 assistant 楼层（会话快照＝历史真源），
正文先过生产同款清洗 `story_history.prose_only`（剥 <roll>/<encounter>/<think> 等搭车块），
再**分诊**：插画回执／上游错误页／纯英文提示词不是剧情，混进来会让「开场趋同」假报九成
（实测同款 `0209.jpg\n\n(masterpiece…` 回执连排几十轮），故只让剧情轮进统计。
词表取**用户当前生效配置**（`effective_phrases(load_config())`），
所以量的是「现在这套设置在你的输出上到底判出什么」。

四个口径分开看：
  A 单轮十项（词表/密度/节拍器/自问自答/句式重复/半角逗号/星号包句/同词排比/微段落/开场趋同）——逐剧情轮重放；
  B 术语棘轮——按作品聚合，注入层＝世界书动态区尾巴，存量＝条目静态底座（生产同构近似）；
  C 存量留痕——读 narrative_ci.db 里 source=prose_style 的历史诊断，与 A/B 对照；
  D 词表体检——现有词表逐条在你的正文上命中几轮，并按「跨作品复发」给出候选新词。

产出写到 OS 临时目录（报告含作品名与正文片段，不入仓）：
  PROBE_OUT=<目录>  AUDIT_MIN_CHARS=60  PYTHONIOENCODING=utf-8 backend/.venv/Scripts/python.exe scripts/audit_prose_style_corpus.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

USERDATA = ROOT / "userdata"
WORLDS_DIR = str(USERDATA / "worlds")

from app.services import narrative_ci, prose_style, repo_meta, story_history  # noqa: E402
from app.services import worldbook, worldbook_store  # noqa: E402

MIN_CHARS = int(os.environ.get("AUDIT_MIN_CHARS", "60"))
STORY_MIN_CJK = int(os.environ.get("AUDIT_STORY_MIN_CJK", "150"))
OPENING_WINDOW = 15
OPENING_HISTORY = 3
RATCHET_BODIES = 5

# 生效配置：体检量的是「这套设置在你的输出上到底判出什么」
PROSE_CONFIG = prose_style.load_config()
PROSE_CONFIG_PHRASES = prose_style.effective_phrases(PROSE_CONFIG)

# 会话快照里**不止剧情**：插画回执、上游错误、LoRA/提交回执都占一层。把这些当正文
# 喂 lint，「开场趋同」会被 `0209.jpg\n\n(masterpiece…` 这类同款回执刷爆（实测占命中九成），
# 结论整个失真。⇒ 先分诊：只让**剧情轮**进 A/B 口径，非剧情轮单独计数供核对。
_IMAGE_RECEIPT_RE = re.compile(r"^(?:已基于\s*\d+\s*张参考图生成|已生成图片|output-\d+)")
_FILE_HEAD_RE = re.compile(r"^\s*\S{0,40}?\.(?:png|jpe?g|webp|gif)\s*$", re.IGNORECASE)
_RECEIPT_MARKS = ("提示词：", "正面提示词", "已应用 LoRA", "已提交到 ComfyUI")
_ERROR_MARKS = ("回答失败", "连接上游生成服务失败", "上游生成服务未返回成功结果")
_CJK_RE = re.compile(r"[一-鿿]")
_CJK_RE_ALL = re.compile(r"[一-鿿]{2,}")

_CODE_NAMES = {
    prose_style.CODE_STYLE_BANNED_PHRASE: "AI 味固定搭配",
    prose_style.CODE_STYLE_PUNCT_DENSITY: "破折号/省略号密度",
    prose_style.CODE_STYLE_RHYTHM_METRONOME: "句长节拍器",
    prose_style.CODE_STYLE_SELF_QA: "自问自答/对话回声",
    prose_style.CODE_STYLE_PATTERN_REPEAT: "「不是…而是…」重复",
    prose_style.CODE_STYLE_OPENING_CUE: "跨轮开场趋同",
    prose_style.CODE_STYLE_WORD_ECHO: "同词连用排比",
    prose_style.CODE_STYLE_MICRO_PARAGRAPH: "超短强调段",
    prose_style.CODE_STYLE_HALFWIDTH_COMMA: "半角逗号断句",
    prose_style.CODE_STYLE_STAR_EMPHASIS: "星号包句强调",
    prose_style.CODE_STYLE_TERM_RATCHET: "术语棘轮",
    prose_style.CODE_STYLE_LIVING_REVIEW: "LLM 活人感通审",
}


def _works(out_root: Path) -> list[dict]:
    """每个 chat.json 当一个语料单元（作品/存档各一份），只读，不创建任何东西。"""
    units: list[dict] = []
    if not out_root.is_dir():
        return units
    for work in sorted(p for p in out_root.iterdir() if p.is_dir()):
        for snap in sorted(work.rglob("chat.json")):
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            msgs = data if isinstance(data, list) else (data.get("messages") or [])
            rounds = [
                {"role": m.get("role", ""),
                 "route": str(m.get("route") or ""),
                 "kind": _classify(str(m.get("text") or m.get("content") or ""))}
                for m in msgs if isinstance(m, dict)
            ]
            for rnd, m in zip(rounds, [m for m in msgs if isinstance(m, dict)]):
                rnd["body"] = story_history.prose_only(
                    str(m.get("text") or m.get("content") or ""))
            units.append({"name": snap.parent.name if snap.parent.name != work.name
                         else work.name,
                          "work": work.name, "path": snap, "rounds": rounds})
    return units


def _classify(raw: str) -> str:
    """楼层分诊：story=剧情正文；receipt=插画/提交回执；error=上游失败；other=其余。"""
    body = story_history.prose_only(raw)
    head = body[:200]
    if not body.strip():
        return "empty"
    if any(mark in head for mark in _ERROR_MARKS):
        return "error"
    if _IMAGE_RECEIPT_RE.match(body) or _FILE_HEAD_RE.match(body.split("\n", 1)[0]) \
            or any(mark in head for mark in _RECEIPT_MARKS):
        return "receipt"
    return "story" if len(_CJK_RE.findall(body)) >= STORY_MIN_CJK else "short"


def _inject_and_exclude(unit: dict) -> tuple[list[str], list[str], list[str]]:
    """棘轮输入的**生产同构近似**：

    * injected（注入层）＝世界书各条目的「剧情进展·动态」尾巴——它才是每轮原样回灌、
      会单调累积的那一层（生产里这层是数据表上下文，此处取世界书动态区做替身）；
    * setting（设定存量）＝条目里 `DYNAMIC_MARKER` **之前**的静态底座（与生产的
      `agency_scene_state._wb_setting_sources` 同一取法），用来把「设定支撑的高频」
      与「无锚点膨胀」分开；
    * exclude＝世界书里由角色卡生成的条目名（合法高频）。

    ⚠ 把整本世界书当注入层是错的：静态设定动辄上万字，「只有/两个/名字」这类虚词必然
    越过阈值，报告会被泛词刷屏（实测 128 条里过半是泛词）。
    """
    book = worldbook_store.read_book(WORLDS_DIR, unit["work"]) or {}
    entries = book.get("entries") or []
    static: list[str] = []
    dynamic: list[str] = []
    for entry in entries:
        content = str(entry.get("content") or "")
        if worldbook.DYNAMIC_MARKER in content:
            head, tail = content.split(worldbook.DYNAMIC_MARKER, 1)
            dynamic.append(tail)
        else:
            head = content
        if head.strip():
            static.append(head)
    names: list[str] = []
    for entry in entries:
        comment = str(entry.get("comment") or "")
        if comment.startswith("角色卡·"):
            names.append(comment.split("·", 1)[1].strip())
    names.extend([unit["work"], unit["name"]])
    return dynamic or static, static, [n for n in dict.fromkeys(names) if n][:60]


def _replay_single_round(unit: dict) -> list[dict]:
    """A 口径：逐轮 lint **剧情轮**（棘轮关，交给 B 按作品聚合，避免同一诊断刷屏）。"""
    findings: list[dict] = []
    assistant = [r for r in unit["rounds"]
                 if r["role"] == "assistant" and r["kind"] == "story"]
    for idx, rnd in enumerate(assistant):
        body = rnd["body"]
        if len(body.strip()) < MIN_CHARS:
            continue
        prev = [a["body"][:OPENING_WINDOW] for a in assistant[max(0, idx - OPENING_HISTORY):idx]]
        for item in prose_style.lint(body, recent_openings=prev,
                                     banned_phrases=PROSE_CONFIG_PHRASES):
            findings.append({"unit": unit["name"], "work": unit["work"], "round": idx + 1,
                             "chars": len(body), **item})
    return findings


def _replay_ratchet(unit: dict) -> list[dict]:
    """B 口径：术语棘轮按作品跑一次（注入层 × 最近若干轮**剧情**正文）。"""
    injected, setting, exclude = _inject_and_exclude(unit)
    bodies = [r["body"] for r in unit["rounds"] if r["role"] == "assistant"
              and r["kind"] == "story" and len(r["body"]) >= MIN_CHARS]
    if not injected or not bodies:
        return []
    out = []
    for item in prose_style.term_ratchet_report(
            injected=tuple(injected), bodies=tuple(bodies[-RATCHET_BODIES:]),
            exclude=tuple(exclude), setting=tuple(setting)):
        out.append({"unit": unit["name"], "work": unit["work"], **item})
    return out


def _cliche_candidates(units: list[dict]) -> tuple[list[tuple[str, int, int]], list[tuple[str, int]]]:
    """D 口径：词表体检——(1) 现有每条词在剧情轮里的命中轮数；

    (2) **跨作品复发**的高频中文 n-gram：同一个词在 ≥3 部不同作品里、且 ≥25% 的剧情轮里
    都出现 ⇒ 它是文体套话而不是某部作品的专有名词（专有名词只在一种题材里复发）。
    这是给「词表该换成什么」用的候选，不是判据。
    """
    rounds = [(u["work"], r["body"]) for u in units for r in u["rounds"]
              if r["role"] == "assistant" and r["kind"] == "story"]
    per_phrase = [(p, sum(1 for _w, b in rounds if p in b)) for p in PROSE_CONFIG_PHRASES]
    per_phrase.sort(key=lambda kv: (-kv[1], len(kv[0])))

    in_rounds: Counter[str] = Counter()
    works_of: dict[str, set[str]] = {}
    edge = prose_style._TERM_EDGE_CHARS
    for work, body in rounds:
        grams = set()
        for run in _CJK_RE_ALL.findall(body):
            for size in (2, 3, 4):
                grams.update(run[i:i + size] for i in range(len(run) - size + 1))
        for g in grams:
            # 与模块同口径：以虚字起头的候选是**动补结构切词**（了一/成一/的那/里的），
            # 不是文体套话；不剔掉，表会被这类碎片占满（实测 30 席里 20 席是碎片）。
            if g in prose_style._TERM_STOPWORDS or g in PROSE_CONFIG_PHRASES:
                continue
            if g[0] in edge or g[-1] in edge:
                continue
            in_rounds[g] += 1
            works_of.setdefault(g, set()).add(work)
    n = max(len(rounds), 1)
    cands = [(g, c, len(works_of[g])) for g, c in in_rounds.items()
             if len(works_of[g]) >= 3 and c >= 0.25 * n]
    # 只留「不被同族更长词解释」的：A 是 B 的子串且 B 也在候选里 ⇒ 丢 A（否则「时候/的时」刷屏）
    cands = [(g, c, w) for g, c, w in cands
             if not any(g != o and g in o and ow >= cw for o, cw, ow in cands)]
    cands.sort(key=lambda kv: (-kv[1], kv[0]))
    return per_phrase, cands[:30]


def _stored_diagnostics(root: Path) -> list[dict]:
    """C 口径：读作品目录里的 narrative_ci.db（生产真跑过的留痕）。"""
    rows: list[dict] = []
    for db in sorted(root.rglob(narrative_ci.DB_NAME)):
        try:
            with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    "SELECT turn, code, severity, message, evidence, status "
                    "FROM diagnostics WHERE source='prose_style'")
                for r in cur:
                    rows.append({"unit": db.parent.name, "work": db.parent.name, **dict(r)})
        except sqlite3.Error:
            continue
    return rows


def _tally(findings: list[dict], total_rounds: int) -> list[dict]:
    by_code: dict[str, list[dict]] = {}
    for item in findings:
        by_code.setdefault(item["code"], []).append(item)
    rows = []
    for code, items in by_code.items():
        rounds = len({(i.get("work"), i.get("unit"), i.get("round")) for i in items})
        rows.append({
            "code": code, "name": _CODE_NAMES.get(code, code), "hits": len(items),
            "rounds": rounds,
            "round_rate": (rounds / total_rounds) if total_rounds else 0.0,
            "severity": items[0]["severity"],
            "sample": items[0]["message"], "evidence": items[0]["evidence"],
        })
    rows.sort(key=lambda r: (-r["hits"], r["code"]))
    return rows


def main() -> int:
    out_root = Path(repo_meta.output_dir_from_state() or (USERDATA / "pictures"))
    units = _works(out_root)
    if not units:
        print(f"!! 没找到任何会话快照：{out_root}", file=sys.stderr)
        return 2

    single = [f for u in units for f in _replay_single_round(u)]
    ratchet = [f for u in units for f in _replay_ratchet(u)]
    stored = _stored_diagnostics(out_root)

    kinds = Counter(r["kind"] for u in units for r in u["rounds"] if r["role"] == "assistant")
    per_unit = Counter(u["name"] for u in units for r in u["rounds"]
                       if r["role"] == "assistant" and r["kind"] == "story")
    story_rounds = [r for u in units for r in u["rounds"]
                    if r["role"] == "assistant" and r["kind"] == "story"
                    and len(r["body"].strip()) >= MIN_CHARS]
    total_rounds, total_chars = len(story_rounds), sum(len(r["body"]) for r in story_rounds)

    out_dir = Path(os.environ.get("PROBE_OUT") or (tempfile.gettempdir() + "/prose-style-audit"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "findings.json").write_text(json.dumps(
        {"single_round": single, "ratchet": ratchet, "stored": stored},
        ensure_ascii=False, indent=2), encoding="utf-8")

    rows = _tally(single, total_rounds)
    lines = [
        "# 去 AI 味文风体检（真实成稿离线重放）",
        "",
        f"- 语料根：`{out_root}`（只读，未改动任何文件）",
        f"- 快照 {len(units)} 份／AI 楼层分诊：{kinds.most_common()}",
        f"- **进入统计的剧情轮 {total_rounds} 轮 / {total_chars} 字**"
        f"（story＝汉字数 ≥{STORY_MIN_CJK} 且非回执/非错误页；单轮 <{MIN_CHARS} 字跳过）",
        f"- 剧情轮来自哪些存档：{per_unit.most_common()}",
        f"- 生效配置：enabled=`{PROSE_CONFIG['enabled']}`、review_every=`{PROSE_CONFIG['review_every']}`、"
        f"词表 **{len(PROSE_CONFIG_PHRASES)} 条**"
        f"（内置 {len(prose_style.EXACT_PHRASES)}＋用户加 {len(PROSE_CONFIG['extra'])}"
        f"－用户删 {len(PROSE_CONFIG['removed'])}）",
        "- 正文清洗：`story_history.prose_only`（与生产同一函数）；模型与网络**零调用**",
        "",
        "## A 单轮十项（逐轮重放）",
        "",
        "| 码 | 含义 | 命中次数 | 涉及轮数 | 轮占比 | 等级 | 样例 |",
        "|---|---|---|---|---|---|---|",
    ]
    if rows:
        for r in rows:
            lines.append(f"| `{r['code']}` | {r['name']} | {r['hits']} | {r['rounds']} | "
                         f"{r['round_rate'] * 100:.1f}% | {r['severity']} | {r['sample']} |")
    else:
        lines.append("| — | **零命中** | 0 | 0 | 0.0% | — | 单轮十项在本语料上全绿 |")

    lines += ["", "## A·逐轮明细", "",
              "| 作品/存档 | 第几轮 | 字数 | 命中的码 | 证据 |", "|---|---|---|---|---|"]
    for item in sorted(single, key=lambda i: (i["work"], i["unit"], i["round"])):
        lines.append(f"| {item['work']}／{item['unit']} | {item['round']} | {item['chars']} | "
                     f"`{item['code']}` | {item['evidence'][:40]} |")
    if not single:
        lines.append("| — | — | — | — | — |")

    lines += ["", "## B 术语棘轮（按作品聚合；注入层＝世界书「剧情进展·动态」尾巴，"
                  "存量＝条目静态底座，正文＝最近若干剧情轮）", ""]
    if ratchet:
        for item in ratchet:
            lines.append(f"- **{item['work']}**：{item['message']}\n  证据：`{item['evidence']}`")
    else:
        lines.append("- 零命中（或该作品没有同名世界书动态区可当注入层）。")

    lines += ["", "## C 生产留痕（narrative_ci.db 里 source=prose_style 的历史诊断）", ""]
    if stored:
        st = Counter(r["code"] for r in stored)
        lines += ["", "| 码 | 含义 | 留痕条数 |", "|---|---|---|"]
        for code, n in st.most_common():
            lines.append(f"| `{code}` | {_CODE_NAMES.get(code, code)} | {n} |")
        lines.append("")
        lines.append(f"- 数据库覆盖 {len({r['unit'] for r in stored})} 个存档目录；"
                     "留痕是**当时那版词表/阈值**跑出来的，与 A 的差值即「设置或判据改动的影响面」。")
    else:
        lines.append("- 没读到任何 narrative_ci.db 留痕（这些作品还没在生产里跑过带 CI 的回合）。")

    per_phrase, cands = _cliche_candidates(units)
    zero = sum(1 for _p, n in per_phrase if n == 0)
    hit_total = sum(n for _p, n in per_phrase)
    verdict = ("**这张词表对剧情文体是死表**" if hit_total == 0
               else f"仅 {len(per_phrase) - zero}/{len(per_phrase)} 条有命中")
    lines += ["", f"## D 词表体检（现有 {len(PROSE_CONFIG_PHRASES)} 条在你的正文上打不打得中）", "",
              f"- 按命中轮数排序，最高 8 条：{[p for p, _n in per_phrase[:8]]}",
              f"- 全表在 {total_rounds} 个剧情轮里**合计命中 {hit_total} 轮次**"
              f"（{zero}/{len(per_phrase)} 条一次没中）⇒ {verdict}",
              "",
              "| 跨作品复发的候选词 | 命中轮数 | 出现在几部作品 |", "|---|---|---|"]
    for g, c, w in cands:
        lines.append(f"| 「{g}」 | {c} | {w} |")
    if not cands:
        lines.append("| — | — | — |")
    lines.append("")
    lines.append("- 判据：同词在 ≥3 部**不同题材**作品里复发 ⇒ 是文体套话而非作品专名；"
                 "已剔掉模块停用词与被更长同族词解释的片段。这张表只是**候选清单**，"
                 "进词表要人工过一遍。")

    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines[:20]))
    print(f"\n[落盘] {out_dir / 'report.md'}")
    print(f"[落盘] {out_dir / 'findings.json'}")
    print(f"[计数] 单轮命中 {len(single)} 条／棘轮 {len(ratchet)} 条／留痕 {len(stored)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
