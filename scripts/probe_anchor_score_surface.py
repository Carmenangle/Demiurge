"""零 LLM 探针：**打分表的可作用面**（第 4 项「静态收束 −3 分」重审的定因装置）。

背景：走查文档 §2.7 曾断言「L2 期望句含『微笑』⇒ 词表档 −3 分 ⇒ 词表档选到 45% 错句」。
本探针把这条断言拆成三张可复算的表，回答一个更前置的问题：
**把「静态收束」这张惩罚表按任何口径改掉，究竟有没有一次能改变结果？**

三张表：
1. `词表档选点`——三份语料上 `fallback_illustration_anchor` 的落点（逐臂）。
2. `仲裁机裁决`——真机判据留样里每一条真机锚点原样喂进 `resolve_illustration_anchor`（逐臂）。
3. `全量穷举`——三份语料**每一段**都当 `requested_anchor` 跑一遍仲裁机（逐臂）。

另有「命中面清单」（静态表在 854 段里到底命中几段）与 L2 三句对照。

⚠ 判读纪律：
- 空翻转 ≠ 安全：`−3 → 0` 零翻转只说明该表在此面上**惰性**；`−3 → +2` 反而会大面积**倒向词表兜底段**
  （兜底段得分抬高 ⇒ 仲裁机 branch① `fallback_score >= requested_score + 3` 更易触发）⇒ **奖励化是回归**。
- 段落号不可信（空段过滤浮动）⇒ 只报百分点。

用法：
    backend/.venv/Scripts/python.exe scripts/probe_anchor_score_surface.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import scene_illustration as si

DL = Path(os.path.expanduser("~")) / "Downloads"
SAMPLES = ROOT / "backend" / "_probe_anchor_chain_samples.json"
OUT = ROOT / "backend" / "_anchor_score_surface.txt"

FILES = (
    ("L1 nsfw_solo", "nsfw_test.txt"),
    ("L2 simple", "simple_test.txt"),
    ("L3 nsfw_multi", "nsfw_test_more Pepole.txt"),
)

ORIG_SCORE = si._anchor_score
ORIG_STATIC = si._STATIC_RESOLUTION_TERMS

# 臂＝(名字, 静态表名表, 命中时的增量)。`before` 用生产现场的 −3。
ARMS = (
    ("before（−3 现行）", ORIG_STATIC, -3),
    ("A：−3 → 0", ORIG_STATIC, 0),
    ("B：−3 → +2（奖励化）", ORIG_STATIC, 2),
)


def make_scoring(static_terms, delta):
    """复刻生产 `_anchor_score`，只替换静态表的**名表**与**增量**。"""
    def scoring(paragraph: str) -> int:
        visible = si.image_prompt_extract.restore_jailbreak(paragraph)
        score = sum(
            weight for weight, words in si._FALLBACK_ANCHOR_TERMS
            if any(word in visible for word in words)
        )
        score += 5 * sum(word in visible for word in si._STATE_CHANGE_ACTION_TERMS)
        if any(word in visible for word in static_terms):
            score += delta
        if any(word in visible for word in ("前两次", "之前", "回想", "曾经")):
            score -= 4
        return score
    return scoring


def load_body(fname: str) -> str:
    with open(DL / fname, encoding="utf-8") as f:
        return f.read()


def paragraphs_of(body: str) -> list[str]:
    return [p.strip() for p in body.replace("\r\n", "\n").split("\n\n") if p.strip()]


def pct_of(body: str, needle: str) -> float:
    idx = body.find(needle)
    return round(100.0 * idx / max(1, len(body)), 1) if idx >= 0 else -1.0


def match_corpus(case: str, corpus: dict[str, str]) -> str:
    """真机留样的 `case` 带中文括注（`L1 nsfw_solo（单人 NSFW）`）⇒ 只能按前缀归桶。

    同 `probe_window_metrics.py` 的既有约定；不按前缀匹配会静默取到空串，
    让整张表 2 变成一串 `-`（本探针首版即踩）。
    """
    for tag, value in corpus.items():
        if case.startswith(tag):
            return value
    return ""


def use_arm(static_terms, delta, *, scoring=True) -> None:
    if scoring:
        si._anchor_score = make_scoring(static_terms, delta)
    else:
        si._anchor_score = ORIG_SCORE
    si._STATIC_RESOLUTION_TERMS = static_terms


def main() -> None:
    corpus = {tag: load_body(fname) for tag, fname in FILES}
    paras = {tag: paragraphs_of(body) for tag, body in corpus.items()}

    lines: list[str] = []
    add = lines.append

    add("=" * 96)
    add("打分表可作用面探针（零 LLM）——「静态收束 −3 分」重审")
    add("=" * 96)

    # ── 命中面 ──────────────────────────────────────────────────────────────
    use_arm(ORIG_STATIC, -3, scoring=False)
    hit_rows = []
    for tag, _ in FILES:
        for p in paras[tag]:
            words = [w for w in ORIG_STATIC if w in p]
            if words:
                hit_rows.append((tag, ORIG_SCORE(p), words, p))
    total = sum(len(paras[t]) for t, _ in FILES)
    add("")
    add(f"【命中面】静态表在 {total} 段里命中 {len(hit_rows)} 段")
    for tag, score, words, p in hit_rows:
        add(f"   [{tag}] score={score:>4} words={words}  {p[:60]!r}")

    # ── 表 1：词表档选点 ────────────────────────────────────────────────────
    add("")
    add("=" * 96)
    add("表 1 · 词表档选点（`fallback_illustration_anchor`）")
    add("=" * 96)
    picks = {}
    for name, terms, delta in ARMS:
        use_arm(terms, delta)
        row = {tag: si.fallback_illustration_anchor(corpus[tag]) for tag, _ in FILES}
        picks[name] = row
        add(f"   {name:<20} " + "  ".join(
            f"{tag.split()[0]}@{pct_of(corpus[tag], a)}%" for tag, a in row.items()))
    base_name = ARMS[0][0]
    for name in list(picks)[1:]:
        diff = sum(1 for tag, _ in FILES if picks[name][tag] != picks[base_name][tag])
        add(f"   {name} 与 before 落点不同的语料数 = {diff} / {len(FILES)}")

    # ── 表 2：9 份真机判据样本过仲裁机 ──────────────────────────────────────
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    add("")
    add("=" * 96)
    add("表 2 · 真机判据锚点原样喂进 `resolve_illustration_anchor`")
    add("=" * 96)
    verdicts: dict[str, list[str]] = {}
    for name, terms, delta in ARMS:
        use_arm(terms, delta)
        row = []
        for s in samples:
            judge = s["anchor"]
            body = match_corpus(s["case"], corpus)
            if not judge or not body:
                row.append("-")
                continue
            row.append(si.resolve_illustration_anchor(body, judge))
        verdicts[name] = row
        overrides = sum(
            1 for s, out in zip(samples, row)
            if s["anchor"] and out != s["anchor"]
        )
        add(f"   {name:<20} 推翻数={overrides}")
    for name in list(verdicts)[1:]:
        diff = sum(1 for a, b in zip(verdicts[name], verdicts[base_name]) if a != b)
        add(f"   {name} 与 before 裁决不同的样本数 = {diff} / {len(samples)}")
    for s, out in zip(samples, verdicts[base_name]):
        if s["anchor"]:
            add(f"     [{s['case'][:6]}·s{s['sample']}] judge={s['anchor'][:34]!r}")
            add(f"           -> {out[:44]!r}  {'OVERRIDE' if out != s['anchor'] else 'keep'}")

    # ── 表 3：全量穷举 ──────────────────────────────────────────────────────
    add("")
    add("=" * 96)
    add("表 3 · 全量穷举：每段当 `requested_anchor` × 仲裁机")
    add("=" * 96)
    use_arm(ORIG_STATIC, -3, scoring=False)
    base_sweep = {
        tag: [si.resolve_illustration_anchor(corpus[tag], p) for p in paras[tag]]
        for tag, _ in FILES
    }
    for name, terms, delta in ARMS:
        if name == base_name:
            continue
        use_arm(terms, delta)
        flips = []
        for tag, _ in FILES:
            for p, b in zip(paras[tag], base_sweep[tag]):
                a = si.resolve_illustration_anchor(corpus[tag], p)
                if a != b:
                    flips.append((tag, p, b, a))
        add(f"   {name:<20} 翻转数 = {len(flips)} / {total}")
        for tag, p, b, a in flips[:5]:
            add(f"     [{tag}] requested={p[:34]!r}")
            add(f"            before -> {b[:36]!r}")
            add(f"            after  -> {a[:36]!r}")

    # ── 分支②触发面 + L2 对照 ───────────────────────────────────────────────
    add("")
    add("=" * 96)
    add("附 · 仲裁机 branch②（`requested_is_static`）的真实触发面")
    add("=" * 96)
    use_arm(ORIG_STATIC, -3, scoring=False)
    visual = (*si._STATE_CHANGE_ACTION_TERMS, *_POSE_CONTEXT_TERMS_SAFE(),
              "高潮", "痉挛", "抽插", "插入")
    branch2 = 0
    for tag, _ in FILES:
        fb = si.fallback_illustration_anchor(corpus[tag])
        fb_action = any(w in fb for w in visual)
        add(f"   [{tag}] fallback={fb[:38]!r} has_action={fb_action}")
        if fb_action:
            branch2 += sum(
                1 for p in paras[tag]
                if any(w in p for w in ORIG_STATIC) and not any(w in p for w in visual)
            )
    add(f"   branch② 可触发段数（静态段 ∧ 兜底含动作 ∧ 静态段自身无动作）= {branch2}")

    add("")
    add("附 · L2 三句的词表得分对照")
    for s in ("她摊开书，目光却无法聚焦在文字上。", "塞蕾娜正微笑着。", "那么，我该插进哪一边呢？"):
        add(f"   score={ORIG_SCORE(s):>4}  static={[w for w in ORIG_STATIC if w in s]}  {s!r}")

    add("")
    add("附 · L2 兜底段在词表档 subjects（在场 3 人）下的取材窗口字数")
    l2_body = corpus["L2 simple"]
    l2_anchor = picks[base_name]["L2 simple"]
    win = si.illustration_scene_excerpt(
        l2_body, l2_anchor, subjects=["罗瑟琳", "卢卡", "塞蕾娜"])
    add(f"   落点 @{pct_of(l2_body, l2_anchor)}%  窗口 {len(win)} 字  {l2_anchor[:44]!r}")
    win_exp = si.illustration_scene_excerpt(
        l2_body, "塞蕾娜正微笑着。", subjects=["罗瑟琳", "卢卡", "塞蕾娜"])
    add(f"   期望句 @{pct_of(l2_body, '塞蕾娜正微笑着。')}%  窗口 {len(win_exp)} 字")

    use_arm(ORIG_STATIC, -3, scoring=False)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("\n=> ", OUT)


def _POSE_CONTEXT_TERMS_SAFE():
    """`_POSE_CONTEXT_TERMS` 属私有名表，取不到就退化成空元组（分支②只会更不易触发）。"""
    return getattr(si, "_POSE_CONTEXT_TERMS", ())


if __name__ == "__main__":
    main()
