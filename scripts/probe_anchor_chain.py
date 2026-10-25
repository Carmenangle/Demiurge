"""插画锚点：**生产链路**对照探针 + **多采样打分**（判据档 vs 中文词表档）。

与 `probe_anchor_intent.py` 的区别：那个是设计期探针，自带一份 rubric 副本；
本脚本**逐字复用生产代码**——
    scene_illustration.anchor_intent_system / parse_anchor_intent / resolve_anchor_intent
    scene_illustration.fallback_illustration_anchor / illustration_anchor_offset
    illustration_prompt_store.effective_climax_anchor_rubric
用于回答三个问题：
    ① 同一段正文，判据档与词表档分别把图插在哪一句后面；
    ② **换一版判据，到底是变好还是变坏**——每段重复采样 N 次，
       报「命中次数」「邻中（±N 百分点）」「与标注期望的位置距离」三类指标；
    ③ **让缺块路径也走既有仲裁机（`resolve_illustration_anchor`）是变好还是变坏**——
       生产里那台仲裁机只在 `agent_agency` L1038 `if plan:` 时才跑，缺块路径被跳过；
       本脚本对每个样本预演一遍仲裁后的落点，不改生产代码即可判定。

⚠ 「命中」用标注判定词做子串匹配，**会假阴性**：模型常选了相邻的那一句（同一帧），
   判定词却不出现。所以位置指标（邻中 / 距离）才是有意义的那个——2026-09-18 生产模型实锤。

用法（默认走本机 ollama）：
    ./.venv/Scripts/python.exe ../scripts/probe_anchor_chain.py
    PROBE_REPEAT=3 ./.venv/Scripts/python.exe ../scripts/probe_anchor_chain.py    # 每段采 3 次
    PROBE_RUBRIC_FILE=_t_rubric_v8.txt \
        ./.venv/Scripts/python.exe ../scripts/probe_anchor_chain.py              # A/B 某一版判据
    PROBE_BASE=https://... PROBE_KEY=sk-... PROBE_MODEL=deepseek-v4-flash \
        ./.venv/Scripts/python.exe ../scripts/probe_anchor_chain.py              # 生产模型复测

输出：控制台 + `backend/_probe_anchor_chain.out`（UTF-8）+ 
`backend/_probe_anchor_chain_samples.json`（逐样本结构化明细，供不花 LLM 调用复盘）。
⚠ 本机 ollama 模型只是**替身**：判据档在生产里用的是用户配置的对话模型。
  2026-09-18 生产模型（deepseek-v4-flash-0731）复跑实锤：替身与真机结论**不一致**
  （L1 替身距离 0、真机 29.9；L3 替身 40、真机 32.2 且 run 间在 19→49 间摆动）
  ⇒ 替身结论只能当**方向性**参考，**不可外推**。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "backend" / "_probe_anchor_chain.out"
# 结构化明细（供事后确定性重算：仲裁/评分/落点都可在不花一次 LLM 调用下复盘）。
SAMPLES_OUT = ROOT / "backend" / "_probe_anchor_chain_samples.json"
DL = Path(os.path.expanduser("~")) / "Downloads"

OLLAMA = "http://127.0.0.1:11434"
BASE = os.environ.get("PROBE_BASE", "")
KEY = os.environ.get("PROBE_KEY", "")
MODEL = os.environ.get("PROBE_MODEL", "qwen3.5:latest")
REPEAT = max(1, int(os.environ.get("PROBE_REPEAT", "1")))
RUBRIC_FILE = os.environ.get("PROBE_RUBRIC_FILE", "")
# 「邻中」阈值（百分点）：与人工标注落点相差在此以内即算认对了那一刻，
# 只是引了相邻的那一句。2026-09-18 生产模型实锤：L3 有样本 anchor 不含标注判定词、
# 却被判 MISS，但落点只差 3 个点、composition/subjects 全对 ⇒ 只看判定词会**假阴性**。
NEAR_PCT = max(0, int(os.environ.get("PROBE_NEAR_PCT", "10")))

# (标题, 文件名, 在场角色, 期望说明, 命中判定词, 期望锚点原文)
#
# ⚠ **「命中判定词」是验收口径，`expect_anchor` 是距离参考点——两者职能不同，别互相改**。
# 命中＝「选对了那一刻」，可有多句等价；距离＝离「最理想那一帧」多远，只留一个参考点。
#
# L1 的验收口径由用户 2026-09-19 定案：**「无论是开始还是结束的定格都可以」**
# ⇒ **预备态定格**（姿态已摆定，如「掰开一边屁股」）与**完结态定格**（如「靠在公告栏上」）
# **都算命中**，只排除**过程态**（动作进行中、姿态未定型，如「开始轻轻脱下内裤」）。
# 故 L1 给**多个判定词**；`expect_anchor` 仍保持完结态不变（那是距离的参考点）。
# （此前只给 `["靠在公告栏上"]` 单判定词 ⇒ 会把「掰开一边屁股」误判 MISS，低估 L1。）
CASES = (
    ("L1 nsfw_solo（单人 NSFW）", "nsfw_test.txt", "清夏、泰宇老师",
     "定格帧（预备态或完结态皆可）：「清夏用手掰开一边屁股。」/「浑身无力的清夏靠在公告栏上。」",
     ["靠在公告栏上", "掰开一边屁股", "肉棒抽了出来"],
     "浑身无力的清夏靠在公告栏上。"),
    ("L2 simple（正常剧情）", "simple_test.txt", "罗瑟琳、卢卡、塞蕾娜",
     "末尾表情特写：「塞蕾娜正微笑着。一副心满意足的模样。」", ["心满意足", "微笑着"],
     "塞蕾娜正微笑着。"),
    ("L3 nsfw_multi（多人 NSFW）", "nsfw_test_more Pepole.txt", "明澜、清夏、泰宇老师",
     "双人同框：「那么，我该插进哪一边呢？」", ["插进哪一边"],
     "那么，我该插进哪一边呢？"),
)


def call_model(system: str, user: str) -> tuple[str, float]:
    t0 = time.monotonic()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if BASE:
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
        with op.open(req, timeout=600) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = d["choices"][0]["message"]["content"]
    else:
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
        with op.open(req, timeout=600) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = d.get("message", {}).get("content") or ""
    return str(text), time.monotonic() - t0


def para_index(text: str, offset: int) -> tuple[int, int]:
    """返回 (段序号从1起, 段总数)；offset<0 表示没定位到。"""
    parts: list[tuple[int, int]] = []
    pos = 0
    for chunk in text.split("\n\n"):
        parts.append((pos, pos + len(chunk)))
        pos += len(chunk) + 2
    if offset < 0:
        return (-1, len(parts))
    for i, (a, b) in enumerate(parts):
        if a <= offset <= b:
            return (i + 1, len(parts))
    return (-1, len(parts))


def resolve_rubric() -> tuple[str, str]:
    """判据来源：环境变量指定的文件优先（A/B 用），否则走生产 store。"""
    if RUBRIC_FILE:
        p = Path(RUBRIC_FILE)
        if not p.is_absolute():
            p = (ROOT / "backend" / RUBRIC_FILE).resolve()
        return p.read_text(encoding="utf-8"), f"文件 {p.name}"
    from app.services import illustration_prompt_store as store

    return (
        store.effective_climax_anchor_rubric(),
        "illustration_prompt_store.effective_climax_anchor_rubric()",
    )


def main() -> None:
    from app.services import scene_illustration as si

    rubric, src = resolve_rubric()
    system = si.anchor_intent_system(rubric)
    lines = [
        f"MODEL = {MODEL}   ENDPOINT = {BASE or OLLAMA}   REPEAT = {REPEAT}"
        f"   邻中阈值 = ±{NEAR_PCT}%",
        f"判据来源 = {src}  长度={len(rubric)}",
        f"system 长度 = {len(system)}（判据 + 程序侧只读合同）",
        "",
    ]
    summary: list[str] = []
    all_samples: list[dict] = []

    for name, fname, actors, expect, expect_any, expect_anchor in CASES:
        path = DL / fname
        if not path.exists():
            lines += [f"### {name}", f"  文件缺失：{path}", ""]
            summary.append(f"{name}\t文件缺失")
            continue
        text = path.read_text(encoding="utf-8")
        user = f"在场角色：{actors}\n\n剧情正文：\n{text}"

        def locate(a: str) -> tuple[int, int, int, int]:
            off = si.illustration_anchor_offset(text, a) if a else None
            off = -1 if off is None else off
            idx, total = para_index(text, off)
            pct = round(off / max(1, len(text)) * 100) if off >= 0 else -1
            return off, idx, total, pct

        exp_off, exp_idx, exp_total, exp_pct = locate(expect_anchor)

        # —— 词表档（生产兜底，零改动；确定性，只算一次）——
        kw_anchor = si.fallback_illustration_anchor(text)
        k_off, k_idx, k_total, k_pct = locate(kw_anchor)
        kw_dist = abs(k_pct - exp_pct) if (k_pct >= 0 and exp_pct >= 0) else -1

        hits = 0
        near = 0
        arb_near = 0
        overridden = 0
        dists: list[int] = []
        arb_dists: list[int] = []
        invalid = 0
        samples: list[dict] = []
        for _ in range(REPEAT):
            record: dict = {
                "anchor": "", "composition": "", "subjects": "", "reason": "",
                "pct": -1, "secs": 0.0, "arb_pct": -1, "override": False, "error": "",
            }
            try:
                raw, secs = call_model(system, user)
            except Exception as exc:  # noqa: BLE001
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["secs"] = 0.0
                samples.append(record)
                invalid += 1
                continue
            anchor, detail = si.resolve_anchor_intent(text, raw)
            record.update(
                anchor=anchor,
                composition=str(detail.get("composition") or ""),
                subjects="/".join(detail.get("subjects") or []),
                reason=str(detail.get("reason") or ""),
                secs=secs,
            )
            if not anchor:
                invalid += 1
                samples.append(record)
                continue
            j_pct = locate(anchor)[3]
            record["pct"] = j_pct
            if any(w in anchor for w in expect_any):
                hits += 1
            if j_pct >= 0 and exp_pct >= 0:
                dists.append(abs(j_pct - exp_pct))
                if abs(j_pct - exp_pct) <= NEAR_PCT:
                    near += 1
            # 既有仲裁机（scene_illustration.resolve_illustration_anchor）：把模型选点
            # 与词表档比 `_anchor_score`，差 ≥3 就推翻。生产里它**只在同轮有计划时**才跑
            # （agent_agency L1038 `if plan:`），缺块路径（判据档真正生效处）被跳过。
            # 这里预先算出来，用于回答「让缺块路径也走仲裁是变好还是变坏」。
            arb = si.resolve_illustration_anchor(text, anchor)
            a_pct = locate(arb)[3] if arb else -1
            record["arb_pct"] = a_pct
            record["override"] = bool(arb) and a_pct != j_pct
            if record["override"]:
                overridden += 1
            if a_pct >= 0 and exp_pct >= 0:
                arb_dists.append(abs(a_pct - exp_pct))
                if abs(a_pct - exp_pct) <= NEAR_PCT:
                    arb_near += 1
            samples.append(record)

        for i, record in enumerate(samples, 1):
            all_samples.append({
                "case": name, "sample": i, "text": fname,
                "expect_pct": exp_pct, "expect_anchor": expect_anchor,
                "kw_pct": k_pct, "kw_anchor": kw_anchor,
                "model": MODEL, **record,
            })

        mean_dist = round(statistics.fmean(dists), 1) if dists else -1
        arb_mean = round(statistics.fmean(arb_dists), 1) if arb_dists else -1
        verdict = f"HIT {hits}/{REPEAT}" if hits else f"MISS 0/{REPEAT}"
        multi = 0
        lines += [
            f"### {name}   [判据档 {verdict}]"
            f"   期望在 {exp_pct}%（第 {exp_idx}/{exp_total} 段）   词表档 {k_pct}%（距 {kw_dist}）",
            f"  用户标注期望 : {expect}   在场角色 : {actors}",
        ]
        for i, s in enumerate(samples, 1):
            if not s["anchor"]:
                tail = f"  {s['secs']:.1f}s"
                if s["error"]:
                    tail += f"  CALL ERROR {s['error'][:120]}"
                lines.append(f"  #{i}  （判定作废，退回词表档）{tail}")
                continue
            dist = abs(s["pct"] - exp_pct) if (s["pct"] >= 0 and exp_pct >= 0) else -1
            if "同框" in s["composition"] or len(s["subjects"].split("/")) >= 2:
                multi += 1
            arb_note = (
                f" → 仲裁后 {s['arb_pct']}%（被推翻，改用词表档）"
                if s["override"] else f" → 仲裁后 {s['arb_pct']}%（维持）"
            )
            lines.append(
                f"  #{i}  anchor: {s['anchor']}\n"
                f"      composition: {s['composition'] or '(空)'}"
                f"   subjects: {s['subjects'] or '(空)'}\n"
                f"      落点 {s['pct']}%（距期望 {dist}）{arb_note}  {s['secs']:.1f}s\n"
                f"      reason: {s['reason']}"
            )
        lines.append(
            f"  → 判据档 命中 {hits}/{REPEAT} · 邻中±{NEAR_PCT}% {near}/{REPEAT} · "
            f"位置距离均值 {mean_dist}（词表档 {kw_dist}）· 判定作废 {invalid} 次"
            f" · 自称同框/多主体 {multi}/{REPEAT - invalid}"
        )
        lines.append(
            f"  → 过既有仲裁机 邻中 {arb_near}/{REPEAT} · 位置距离均值 {arb_mean}"
            f" · 被推翻 {overridden}/{REPEAT - invalid} 次"
        )
        lines.append("")
        summary.append(
            f"{name}\t判据 {verdict}\t命中率 {hits}/{REPEAT}\t邻中 {near}/{REPEAT}"
            f"\t位置距离 {mean_dist}\t仲裁后距离 {arb_mean}\t词表距离 {kw_dist}"
            f"\t自称同框 {multi}\t被推翻 {overridden}"
        )

    lines += ["──────── 汇总 ────────", *summary]
    report = "\n".join(lines)
    OUT.write_text(report, encoding="utf-8")
    SAMPLES_OUT.write_text(
        json.dumps(all_samples, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(report)
    print("->", OUT)
    print("->", SAMPLES_OUT)


if __name__ == "__main__":
    main()
