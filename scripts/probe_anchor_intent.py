"""插画锚点意图判据 · 离线验证探针。

用途：不接生产链路，用固定文本验证「判据」能不能自己选对插图锚点。
判据真源与实验结论见 docs/illustration-anchor-intent-design.md。

用法（默认走本机 ollama）：
    python scripts/probe_anchor_intent.py
    PROBE_BASE=https://... PROBE_KEY=sk-... PROBE_MODEL=deepseek-v4-flash \\
        python scripts/probe_anchor_intent.py          # 走 OpenAI 兼容端点（真机复测用）

用例：三段用户标注文本（Downloads）+ 两段留出集（不参与调参）。
输出：控制台 + backend/_probe_anchor_intent.out（UTF-8）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "_probe_anchor_intent.out"
HOLDOUT_CHAT = ROOT / "userdata" / "pictures" / "凌霄仙母录" / "SAVE01" / "chat.json"
DL = Path(os.path.expanduser("~")) / "Downloads"

OLLAMA = "http://127.0.0.1:11434"
BASE = os.environ.get("PROBE_BASE", "")
KEY = os.environ.get("PROBE_KEY", "")
MODEL = os.environ.get("PROBE_MODEL", "qwen3.5:latest")

# —— 判据真源（与 docs/illustration-anchor-intent-design.md §3 逐字一致）——
RUBRIC = """你是「插画锚点分析器」。给你一段剧情正文和在场角色，判断一件事——

如果只能为这段剧情配一张插图，图应该插在哪一句话的下方，这张图对读者的价值才最大。

【anchor 是什么意思】
anchor 表示「插图插在这一句话之后」。判断的对象是**这句话所处的那一刻，画面上有什么可看的东西**，
不是"把这句话本身画出来"。所以锚点句本身可以是一句旁白，也可以是一句叙述出来的心理活动——
只要那一刻的画面是成立的。

【这张图要解决的是"看见"，不是"知道"】
读者顺着文字读下来，来龙去脉文字已经说清了。插图唯一的价值，是把文字给不出画面感的那样东西固定下来。

【明确排除】
· 带引号的成句对白（人物在说话、宣布、承诺、询问、回答）——只有声音，没有画面；
· 此刻的内容在回顾过去、推演未来或在别处（画面不在这一刻）；
· 角色正处于剧烈动作的过程之中，姿态与形态在单帧里辨不清——文字已经把过程写得很细，画出来是重复；
· 多角色剧情里，此刻画面已被收窄到只容得下其中一个角色。

【优先选】（只按下面三类性质判断，不要按句子里的字面用词判断）
· 角色的姿态或状态已经定型的那一刻；
· 角色的表情已经落在脸上的那一刻；
· 多个关键角色之间的相对位置已经成立的那一刻。

【落点判据】
一、可画性：锚点那一刻必须能用一张静帧画清楚——有角色在场，有可见的姿态/表情/状态，有可交代的环境。

二、定格态优先：选「状态已经成立」的那一瞬，不要选「正在形成 / 正在剧烈运动中」的中间态。

三、主题同框（多角色剧情必守）：先判断这段剧情的主题画面是哪一种——
      · 「某个人成了什么样子」→ 聚焦该角色的状态或表情；
      · 「这几个人处在什么关系里」→ 锚点必须落在这些关键角色**同处一个画面、地位相当**的那一刻；
        故事继续往下走、焦点收窄到其中一人之后的位置一律不合格，
        **即使后面那一句更激烈、更露骨，也不要改成它。**

四、素材密度：水平相当时，选给出更多可用视觉信息的位置。

只输出严格 JSON，不要 markdown 代码块，不要任何解释文字，字符串一律用英文半角双引号：

{"anchor": "...", "composition": "...", "subjects": ["..."], "reason": "..."}

- anchor：原文中逐字出现的一句话（含标点，不含换行），必须能在正文里原样找到
- composition：最合适的取景方式，从这几个里选：特写 / 中景 / 全身 / 双人同框 / 多人同框 / 远景
- subjects：锚点那一刻画面上应出现的主要角色名
- reason：一句话说明为什么选这里（不超过 40 字）
"""

_QUOTES = "「」『』“”‘’\"'（）()【】[]"


def norm_match(s: str) -> str:
    """逐字命中判定：忽略空白与各类引号（模型常把 “” 改写成 「」）。"""
    return re.sub(r"[\s" + re.escape(_QUOTES) + r"]+", "", s or "")


def restore(text: str) -> str:
    """破甲写法 @词@(词)@ → 自然文（生产同款）。"""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.services import image_prompt_extract  # noqa: PLC0415

        return image_prompt_extract.restore_jailbreak(text)
    except Exception:
        return re.sub(r"@([^@()]{1,12})@\([^)]{0,12}\)@?", r"\1", text)


def holdout_scene() -> str:
    """留出集：真实长剧情的一个 <content> 段（结构不同于三段标注）。"""
    if not HOLDOUT_CHAT.exists():
        return ""
    data = json.loads(HOLDOUT_CHAT.read_text(encoding="utf-8"))
    best = ""
    for msg in data:
        if msg.get("role") != "assistant":
            continue
        m = re.search(r"<content\b[^>]*>([\s\S]*?)</content>", msg.get("text") or "", re.I)
        if m and len(m.group(1).strip()) > len(best):
            best = m.group(1).strip()
    return best


def multi_text() -> str:
    """多人标注文本：优先取 Downloads，缺失则跳过该用例。"""
    for name in ("nsfw_test_more Pepole.txt", "nsfw_test_more People.txt"):
        p = DL / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def build_cases() -> list[dict]:
    multi = multi_text()
    cases = [
        {
            "name": "L1 nsfw_solo(标注)",
            "actors": "宥夏、泰宇老师",
            "text": (DL / "nsfw_test.txt").read_text(encoding="utf-8"),
            "expect": "事后结果图：「浑身无力的宥夏靠在公告栏上。」",
            "expect_any": ["靠在公告栏上"],
        },
        {
            "name": "L2 simple(标注)",
            "actors": "罗瑟琳、卢卡、塞蕾娜",
            "text": (DL / "simple_test.txt").read_text(encoding="utf-8"),
            "expect": "末尾表情特写：「塞蕾娜正微笑着。一副心满意足的模样。」",
            "expect_any": ["心满意足", "微笑着"],
        },
    ]
    if multi:
        cases.append({
            "name": "L3 nsfw_multi(标注)",
            "actors": "世熙、宥夏、泰宇老师",
            "text": multi,
            "expect": "双人同框：「那么，我该插进哪一边呢？」",
            "expect_any": ["插进哪一边"],
        })
        cases.append({
            "name": "H1 留出·车内对白",
            "actors": "宥夏母亲、宥夏父亲",
            "text": multi[multi.find("***") + 3:].strip() if "***" in multi else "",
            "expect": "（留出：不预设答案，看是否符合判据——局面定型的一刻 / 表情）",
            "expect_any": [],
        })
    if holdout_scene():
        cases.append({
            "name": "H2 留出·真实长剧情",
            "actors": "舞姬恋、凌渊",
            "text": holdout_scene(),
            "expect": "（留出：按判据，单人场景应取事后状态，而非动作过程）",
            "expect_any": [],
        })
    return cases


def call_model(system: str, user: str) -> tuple[str, float]:
    t0 = time.monotonic()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if BASE:  # OpenAI 兼容端点
        body = json.dumps({
            "model": MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
        }).encode("utf-8")
        req = urllib.request.Request(
            BASE.rstrip("/") + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {KEY}"},
        )
        with op.open(req, timeout=1200) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = d["choices"][0]["message"]["content"]
    else:  # 本机 ollama
        body = json.dumps({
            "model": MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False, "think": False,
            "options": {"temperature": 0.2, "num_ctx": 32768},
        }).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA + "/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        with op.open(req, timeout=1200) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = d.get("message", {}).get("content") or ""
    return str(text), time.monotonic() - t0


def parse_json(raw: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # 容错：模型常用全角引号 “” 当 JSON 分隔符 ⇒ 整包不可解析，退回逐字段抓取。
    out: dict = {}
    am = re.search(r'"anchor"\s*:\s*"([\s\S]*?)"\s*[,}]', raw)
    if am:
        out["anchor"] = am.group(1)
    for field in ("composition", "reason"):
        fm = re.search(r'"%s"\s*:\s*"([^"]*)"' % field, raw)
        if fm:
            out[field] = fm.group(1)
    return out


def main() -> None:
    lines: list[str] = [f"MODEL = {MODEL}   ENDPOINT = {BASE or OLLAMA}", ""]
    for case in build_cases():
        if not case["text"]:
            continue
        text = restore(case["text"])
        user = f"在场角色：{case['actors']}\n\n正文：\n{text}"
        try:
            raw, secs = call_model(RUBRIC, user)
        except Exception as exc:  # noqa: BLE001
            lines += [f"### {case['name']}", f"  CALL ERROR: {type(exc).__name__}: {exc}", ""]
            continue
        data = parse_json(raw)
        anchor = str(data.get("anchor") or "")
        n_anchor, n_text = norm_match(anchor), norm_match(text)
        hit = bool(n_anchor) and n_anchor in n_text
        offset = n_text.find(n_anchor) if hit else -1
        pct = (offset / len(n_text) * 100) if hit and n_text else -1
        verdict = ""
        if case["expect_any"]:
            verdict = "HIT" if any(w in anchor for w in case["expect_any"]) else "MISS"
        lines += [
            f"### {case['name']}  [{verdict}]  {secs:.1f}s",
            f"  期望       : {case['expect']}",
            f"  anchor     : {anchor}",
            f"  composition: {data.get('composition')}   subjects: {data.get('subjects')}",
            f"  reason     : {data.get('reason')}",
            f"  逐字命中   : {hit}  offset={offset} ({pct:.0f}% 处)  文长={len(n_text)}",
            "",
        ]
        if not data:
            lines += ["  RAW(前 300):", "  " + raw[:300].replace("\n", " | "), ""]
    report = "\n".join(lines)
    OUT.write_text(report, encoding="utf-8")
    print(report)
    print("->", OUT)


if __name__ == "__main__":
    main()
