"""零 LLM 探针：取材窗口指标（旧行为 vs A′），补齐 `probe_anchor_chain.py` 的测量盲区。

背景（`docs/illustration-window-handoff-2026-09-18.md` §4.4）：`probe_anchor_chain.py` 只量
「**锚点落点位置**」，量不到真正关心的「**窗口长度 / 窗口内角色数 / 覆盖用户点名画面与否**」。
本探针把这两个指标补上，且在 **A′ 落地后**可直接回答「旧行为 vs 现在」差多少。

数据源：
- 语料 `~/Downloads/{nsfw_test.txt, simple_test.txt, nsfw_test_more Pepole.txt}`
  （真源口径在 `scripts/probe_anchor_chain.py` 的 `CASES`）；
- 真机判据留样 `backend/_probe_anchor_chain_samples.json`
  （不花一次 LLM 调用即可复算——这是本探针「零 LLM」的关键）。

输出 `backend/_window_metrics.txt`：每个锚点的窗口字数 / 是否收口在锚点 / 关键画面词覆盖。
**刻意不使用段号**：切段口径会随空段与占位串过滤浮动（历史踩坑），只报字数与百分比位置。

用法：
    backend/.venv/Scripts/python.exe scripts/probe_window_metrics.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import scene_illustration as si  # noqa: E402

DL = Path(os.path.expanduser("~")) / "Downloads"
SAMPLES = ROOT / "backend" / "_probe_anchor_chain_samples.json"
OUT = ROOT / "backend" / "_window_metrics.txt"

# (标题, 文件名, 在场角色, 期望锚点, 关键画面词[顺序固定，输出 +/- 覆盖串])
CASES = (
    ("L1 nsfw_solo", "nsfw_test.txt", ["宥夏", "泰宇老师"],
     "浑身无力的宥夏靠在公告栏上。", ["掰开", "翘着屁股", "靠在公告栏上"]),
    ("L2 simple", "simple_test.txt", ["罗瑟琳", "卢卡", "塞蕾娜"],
     "塞蕾娜正微笑着。", ["微笑着", "心满意足"]),
    ("L3 nsfw_multi", "nsfw_test_more Pepole.txt", ["世熙", "宥夏", "泰宇老师"],
     "那么，我该插进哪一边呢？", ["掰开", "臀", "并排", "献上", "等待", "插进哪一边"]),
)


def load_real_samples() -> dict:
    """把真机留样按 `case` 归桶；样本的 case 键带中文括注，调用方按前缀匹配。"""
    if not SAMPLES.exists():
        return {}
    buckets: dict = {}
    stack = [json.loads(SAMPLES.read_text(encoding="utf-8"))]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            label = str(node.get("case") or node.get("label") or "")
            if label and "anchor" in node:
                buckets.setdefault(label, []).append(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return buckets


def split_subjects(raw) -> list:
    """样本里 subjects 是 `/` 连接的字符串；兼容列表形态。"""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [
        part.strip()
        for part in str(raw or "").replace("、", "/").split("/")
        if part.strip()
    ]


def coverage(text: str, words: list) -> str:
    return "".join("+" if word in text else "-" for word in words)


def main() -> int:
    real = load_real_samples()
    lines: list = []
    for title, fname, actors, expect_anchor, keywords in CASES:
        path = DL / fname
        lines.append("")
        lines.append(f"### {title}  语料={fname}  在场={'、'.join(actors)}")
        lines.append(f"    关键画面词（顺序固定）：{' | '.join(keywords)}")
        if not path.exists():
            lines.append(f"    语料缺失：{path}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines.append(f"    语料 {len(text)} 字")

        bucket: list = []
        for label, items in real.items():
            if label.startswith(title):
                bucket.extend(items)
        lines.append(f"    真机判据样本 {len(bucket)} 条")

        rows = [("期望锚点", expect_anchor, split_subjects(actors))]
        for sample in bucket:
            anchor = str(sample.get("anchor") or "").strip()
            if not anchor:
                lines.append("    真机样本 anchor 为空（判据判无效那一轮）⇒ 跳过")
                continue
            rows.append((
                f"真机判据({sample.get('composition') or '?'})",
                anchor,
                split_subjects(sample.get("subjects")) or split_subjects(actors),
            ))

        for tag, anchor, subjects in rows:
            if anchor not in text:
                lines.append(f"    [{tag}] 锚点不在语料中（逐字校验不过）：{anchor[:40]}")
                continue
            old = si.illustration_scene_excerpt(text, anchor)
            new = si.illustration_scene_excerpt(text, anchor, subjects=subjects)
            pos = text.index(anchor) / max(1, len(text)) * 100
            lines.append(f"    [{tag}] 锚点@{pos:5.1f}%  S={'/'.join(subjects)}")
            lines.append(f"        锚点：{anchor}")
            lines.append(
                f"        旧：{len(old):5d} 字  {coverage(old, keywords)}"
                f"（{'收口在锚点' if old.endswith(anchor) else '未收口'}）"
            )
            lines.append(
                f"        A′：{len(new):5d} 字  {coverage(new, keywords)}"
                f"（{'收口在锚点' if new.endswith(anchor) else '未收口'}）"
            )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
