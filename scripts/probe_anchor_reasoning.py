"""插画锚点 · **思路审核**探针：把「判据 LLM 到底怎么想的」原样取出来给人审。

与同目录另外两个探针的分工（别混用）：
    `probe_anchor_intent.py`  = 设计期探针，自带判据副本，用来磨判据文字；
    `probe_anchor_chain.py`   = 生产链路探针，量「选点准不准」（落点距离 / 邻中 / 命中）；
    **本脚本**                = 取「判断过程」（逐句走查 + 原生思考通道），给人看、给人审。

为什么必须在**体外**做——生产判据层**结构上拿不到思路**，三处都堵死了：
    ① `llm.chat` 只返回展平后的 content（`chat_messages` 收口），推理通道根本不透出；
    ② 只读输出合同明文「不要任何解释文字」；
    ③ `parse_anchor_intent` 还会把 `<think>` 块整块删掉。
    ⇒ 用户要审「思路对不对」，只能在探针里放开，**不动生产一行**。

两档对照（同一段正文 / 同一份判据 / 同一 user 消息，只换 system 与采样参数）：
    **P 生产档**：system = `anchor_intent_system(rubric)`，think 关，生产合同
                 ⇒ 这就是**生产真正会发生的事**（含它是否守「不要解释文字」）。
    **R 推理档**：system = rubric + 本脚本的**推理合同**（先四步走查、最后给 JSON），think 开
                 ⇒ 拿到完整思考过程供审核。
    ⚠ R 档的**结论不代表生产表现**——放开 CoT 本身就会改变结果（这是常识，不是免责话术）。
       R 档只回答「判据的思路对不对」；P 档才回答「生产选得准不准」。

逐字复用的生产件（保证探针看到的与生产一致）：
    rubric   = illustration_prompt_store.effective_climax_anchor_rubric()
    system   = scene_illustration.anchor_intent_system(rubric)
    user     = agent_agency._illustration_anchor_intent 的同款拼法（在场角色 + 剧情正文）
    解析/校验 = scene_illustration.resolve_anchor_intent
    插入点   = scene_illustration.illustration_anchor_offset
    兜底档   = scene_illustration.fallback_illustration_anchor
    用例表   = probe_anchor_chain.CASES（单一属主，避免两处漂移）

用法（默认走 `backend/data/user_state.json` 的**激活对话模型**，即真机口径）：
    backend/.venv/Scripts/python.exe scripts/probe_anchor_reasoning.py
    PROBE_CASES=L3 ...                    # 只跑某几段（逗号分隔，匹配标题里的 L1/L2/L3）
    PROBE_MODE=r ...                      # 只跑推理档（省钱）；both / p / r
    PROBE_REPEAT=2 ...                    # 每段重复采样（看思路稳不稳）
    PROBE_OLLAMA=1 ...                    # 走本机 ollama 替身（⚠ 结论不可外推）
    PROBE_THINK=0 ...                     # 不尝试原生思考通道（只靠推理合同）
    PROBE_TEXT_FILE=D:/x.txt PROBE_ACTORS='甲、乙' ...
                                          # 临时塞一段自己的文本（审新样本用，不必改代码）
输出：控制台（UTF-8）+ `backend/_probe_anchor_reasoning.out`（人看）
      + `backend/_probe_anchor_reasoning.json`（结构化，供事后复盘，不必再花调用）
⚠ 密钥只从 `user_state.json` / 环境变量读，**绝不写进任何输出文件**。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "backend" / "_probe_anchor_reasoning.out"
SAMPLES_OUT = ROOT / "backend" / "_probe_anchor_reasoning.json"
USER_STATE = ROOT / "backend" / "data" / "user_state.json"
OLLAMA = "http://127.0.0.1:11434"

MODE = (os.environ.get("PROBE_MODE") or "both").strip().lower()
REPEAT = max(1, int(os.environ.get("PROBE_REPEAT", "1")))
CASE_FILTER = [x.strip() for x in (os.environ.get("PROBE_CASES") or "").split(",") if x.strip()]
WANT_PROD = MODE in ("both", "p", "prod", "production")
WANT_REASON = MODE in ("both", "r", "reason", "reasoning")
USE_OLLAMA = (os.environ.get("PROBE_OLLAMA") or "") not in ("", "0")
TRY_NATIVE_THINK = (os.environ.get("PROBE_THINK") or "1") not in ("0", "false")
PROXY = (os.environ.get("PROBE_PROXY") or "").strip()
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "600"))
# 推理档会「先写走查、最后给 JSON」，输出被截断就会整条解不出 ⇒ 默认给一个够用的上限。
# P 档刻意**不带** max_tokens：生产 `_llm.chat` 也没带，探针要逐字复现生产。
MAX_TOKENS = int(os.environ.get("PROBE_MAX_TOKENS", "4096"))
EXTRA_TEXT_FILE = (os.environ.get("PROBE_TEXT_FILE") or "").strip()
EXTRA_ACTORS = (os.environ.get("PROBE_ACTORS") or "").strip()


def load_chain_probe():
    """按路径加载同目录的生产链路探针，只取它的 CASES / para_index（用例表单属主）。"""
    path = Path(__file__).resolve().parent / "probe_anchor_chain.py"
    spec = importlib.util.spec_from_file_location("_probe_anchor_chain_src", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_endpoint() -> dict:
    """判据调用走哪个模型：环境变量 > ollama 替身 > user_state.json 的激活对话模型。"""
    base = (os.environ.get("PROBE_BASE") or "").strip()
    if base:
        return {
            "kind": "openai", "base": base.rstrip("/"),
            "key": (os.environ.get("PROBE_KEY") or "").strip(),
            "model": (os.environ.get("PROBE_MODEL") or "").strip(),
            "label": "环境变量 PROBE_BASE",
        }
    if USE_OLLAMA:
        return {
            "kind": "ollama", "base": "", "key": "",
            "model": (os.environ.get("PROBE_MODEL") or "").strip() or "qwen3.5:latest",
            "label": "本机 ollama（替身：结论只能当方向，不可外推）",
        }
    if USER_STATE.exists():
        settings = json.loads(USER_STATE.read_text(encoding="utf-8")).get("settings") or {}
        active = settings.get("activeChatModelId")
        entry = next(
            (m for m in (settings.get("chatModels") or []) if m.get("id") == active), None,
        )
        if entry and entry.get("baseUrl") and entry.get("modelName"):
            return {
                "kind": "openai", "base": str(entry["baseUrl"]).rstrip("/"),
                "key": str(entry.get("apiKey") or ""), "model": str(entry["modelName"]),
                "label": f"user_state.json 激活对话模型（{entry.get('displayName') or entry['modelName']}）",
            }
    return {
        "kind": "ollama", "base": "", "key": "", "model": "qwen3.5:latest",
        "label": "本机 ollama（user_state.json 里没找到激活对话模型，回落替身）",
    }


def _opener():
    if PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
        )
    # 生产走 inherit（系统代理），探针一律直连——与 probe_anchor_chain 同口径。
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call_model(endpoint: dict, system: str, user: str, *, think: bool,
               max_tokens: int) -> dict:
    """一次对话；返回 content / 原生思考通道 / 耗时 / 错误。密钥不回传、不落盘。"""
    started = time.monotonic()
    record = {"content": "", "native_thinking": "", "secs": 0.0, "error": ""}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        if endpoint["kind"] == "openai":
            body = {"model": endpoint["model"], "messages": messages, "temperature": 0.2}
            if max_tokens > 0:
                body["max_tokens"] = max_tokens
            payload = json.dumps(body).encode("utf-8")
            request = urllib.request.Request(
                endpoint["base"] + "/chat/completions", data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {endpoint['key']}"},
            )
            with _opener().open(request, timeout=TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            record["content"] = str(message.get("content") or "")
            record["native_thinking"] = str(
                message.get("reasoning_content")
                or message.get("reasoning")
                or choice.get("reasoning_content")
                or ""
            )
        else:
            payload = json.dumps({
                "model": endpoint["model"], "messages": messages,
                "stream": False, "think": bool(think),
                "options": {"temperature": 0.2, "num_ctx": 32768},
            }).encode("utf-8")
            request = urllib.request.Request(
                OLLAMA + "/api/chat", data=payload,
                headers={"Content-Type": "application/json"},
            )
            with _opener().open(request, timeout=TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
            message = data.get("message") or {}
            record["content"] = str(message.get("content") or "")
            record["native_thinking"] = str(message.get("thinking") or "")
    except Exception as exc:  # noqa: BLE001 探针：任何失败都要留痕，不能静默
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["secs"] = round(time.monotonic() - started, 1)
    return record


def reasoning_contract(scene_illustration) -> str:
    """推理档专用的输出要求（**只在本探针内**，生产合同属主是 ANCHOR_INTENT_CONTRACT）。

    字段名从生产的 `ANCHOR_INTENT_FIELDS` 生成 ⇒ 生产加字段时这里不会悄悄漂移。
    """
    fields = "、".join(scene_illustration.ANCHOR_INTENT_FIELDS)
    return (
        "【本次是「思路审核」模式：与生产不同的输出格式】\n"
        "请先把判断过程完整写出来，最后再给结论 JSON。\n\n"
        "第一步 · 先定性：这段剧情的主题画面是哪一种——\n"
        "      A「某个人成了什么样子」；B「这几个人处在什么关系里」。先明确回答是哪一种。\n"
        "第二步 · 逐句走查（按正文顺序）：只列真正有竞争力的 3～6 个候选，每个候选写一行，"
        "包含：原句摘录 → 那一刻画面上有什么（角色 / 姿态 / 表情 / 环境） → 判定，"
        "判定要写明是【可用】，还是【排除】并指名撞了【明确排除】/【落点判据】的哪一条。\n"
        "第三步 · 候选比较：把判为【可用】的摆在一起比可画性 / 定格态 / 主题同框 / 素材密度，"
        "说明谁胜出、为什么。\n"
        "第四步 · 结论：说明最终选中的那一句为什么胜过其他候选；"
        "若主题是 B（关系），明确说它满足同框条款在哪里。\n\n"
        "四步写完后，最后单独输出严格 JSON（字段与生产合同完全一致：" + fields + "，"
        "不要包 markdown 代码块）：\n"
        '{"anchor": "...", "composition": "...", "subjects": ["..."], "reason": "..."}\n'
        "· anchor 必须是正文里**逐字出现**的一句话（含标点、不含换行）；\n"
        "· composition ∈ 特写 / 中景 / 全身 / 双人同框 / 多人同框 / 远景；\n"
        "· subjects 是锚点那一刻画面上应出现的主要角色名。\n"
    )


def leftover_chars(raw: str) -> int:
    """JSON 之外还剩多少字符——用来判「模型有没有真的守『不要任何解释文字』」。"""
    text = (raw or "").strip()
    left, right = text.find("{"), text.rfind("}")
    if left < 0 or right < left:
        return len(text)
    return len(text[:left].strip()) + len(text[right + 1:].strip())


def locate(scene_illustration, chain, text: str, anchor: str) -> tuple[int, int, int, int]:
    offset = scene_illustration.illustration_anchor_offset(text, anchor) if anchor else None
    offset = -1 if offset is None else offset
    index, total = chain.para_index(text, offset)
    percent = round(offset / max(1, len(text)) * 100) if offset >= 0 else -1
    return offset, index, total, percent


def parse_and_locate(scene_illustration, chain, text: str, raw: str) -> dict:
    """用**生产解析器**解一次：能解出什么、落点在哪、evidence 过不过。"""
    anchor, detail = scene_illustration.resolve_anchor_intent(text, raw or "")
    _, index, total, percent = locate(scene_illustration, chain, text, anchor)
    subjects = detail.get("subjects") or []
    return {
        "anchor": anchor,
        "anchor_candidate": str(detail.get("anchor") or ""),
        "anchor_raw": str(detail.get("anchor_raw") or ""),
        "composition": str(detail.get("composition") or ""),
        "subjects": "、".join(str(item) for item in subjects),
        "reason": str(detail.get("reason") or ""),
        "pct": percent, "para_index": index, "para_total": total,
        "ok": bool(anchor),
    }


def build_cases(chain) -> list[dict]:
    """用例表来自 probe_anchor_chain.CASES；`PROBE_TEXT_FILE` 可临时追加一段自定义语料。"""
    cases = []
    for name, filename, actors, expect, expect_any, expect_anchor in chain.CASES:
        if CASE_FILTER and not any(key in name for key in CASE_FILTER):
            continue
        cases.append({
            "name": name, "file": filename, "path": chain.DL / filename, "actors": actors,
            "expect": expect, "expect_any": expect_any, "expect_anchor": expect_anchor,
        })
    if EXTRA_TEXT_FILE:
        path = Path(EXTRA_TEXT_FILE)
        if not path.is_absolute():
            path = ROOT / EXTRA_TEXT_FILE
        cases.append({
            "name": f"X0 自定义（{path.name}）", "file": path.name, "path": path,
            "actors": EXTRA_ACTORS, "expect": "（自定义样本，无预设答案）",
            "expect_any": [], "expect_anchor": "",
        })
    return [case for case in cases if case["path"].exists()]


def run_case(scene_illustration, chain, case: dict, endpoint: dict, rubric: str,
             contract: str) -> tuple[list[str], list[dict]]:
    lines: list[str] = []
    records: list[dict] = []
    text = case["path"].read_text(encoding="utf-8")
    actors = case["actors"]
    # 用户消息与生产 agent_agency._illustration_anchor_intent 同款拼法（无在场角色时省掉那一行）。
    user = "\n\n".join(part for part in (
        f"在场角色：{actors}" if actors else "",
        f"剧情正文：\n{text}",
    ) if part)

    exp_pct = locate(scene_illustration, chain, text, case["expect_anchor"])[3]
    exp_idx, exp_total = chain.para_index(
        text, locate(scene_illustration, chain, text, case["expect_anchor"])[0],
    )
    kw_anchor = scene_illustration.fallback_illustration_anchor(text)
    kw_pct = locate(scene_illustration, chain, text, kw_anchor)[3]
    kw_dist = abs(kw_pct - exp_pct) if (kw_pct >= 0 and exp_pct >= 0) else -1

    lines += [
        "=" * 96,
        f"### {case['name']}   [语料 {case['file']}  {len(text)} 字  {exp_total} 段]",
        f"    在场角色   : {actors or '(未指定)'}",
        f"    用户标注期望: {case['expect']}"
        + (f"   期望落点 {exp_pct}%（第 {exp_idx}/{exp_total} 段）" if exp_pct >= 0 else ""),
        f"    词表档基线 : anchor「{kw_anchor[:60]}」落点 {kw_pct}%"
        + (f"（距期望 {kw_dist}）" if kw_dist >= 0 else ""),
    ]

    for run in range(1, REPEAT + 1):
        if REPEAT > 1:
            lines.append(f"---- 第 {run}/{REPEAT} 次采样 ----")

        if WANT_PROD:
            call = call_model(
                endpoint, scene_illustration.anchor_intent_system(rubric),
                user, think=False, max_tokens=0,
            )
            produced = parse_and_locate(scene_illustration, chain, text, call["content"])
            leftover = leftover_chars(call["content"])
            lines += [
                "[P 生产档] 逐字复现生产调用（think 关 / 生产只读合同）——这才是生产会发生的事",
                f"    anchor      : {produced['anchor'] or '（判定作废 → 生产会退回词表档）'}",
                f"    composition : {produced['composition'] or '(空)'}"
                f"   subjects: {produced['subjects'] or '(空)'}",
                f"    reason      : {produced['reason'] or '(空)'}",
                f"    落点        : {produced['pct']}%"
                + (f"（距期望 {abs(produced['pct'] - exp_pct)}）"
                   if produced["pct"] >= 0 and exp_pct >= 0 else "")
                + f"   逐字落文: {'是' if produced['ok'] else '否'}"
                + f"   耗时 {call['secs']}s"
                + (f"   CALL ERROR {call['error'][:160]}" if call["error"] else ""),
                f"    合同遵守    : JSON 之外多输出了 {leftover} 字符"
                + ("（守住了「不要任何解释文字」）" if leftover == 0 else "（越界）"),
                f"    raw 全长 {len(call['content'])} 字"
                + (f"；原生思考通道 {len(call['native_thinking'])} 字"
                   if call["native_thinking"] else ""),
            ]
            records.append({"case": case["name"], "run": run, "mode": "P",
                            "expect_pct": exp_pct, "kw_pct": kw_pct, **produced,
                            "leftover": leftover, "secs": call["secs"],
                            "error": call["error"], "raw": call["content"]})

        if WANT_REASON:
            call = call_model(
                endpoint, rubric + "\n\n" + contract,
                user, think=TRY_NATIVE_THINK, max_tokens=MAX_TOKENS,
            )
            produced = parse_and_locate(scene_illustration, chain, text, call["content"])
            native = call["native_thinking"]
            lines += [
                "[R 推理档] 放开思考（think 开 / 探针推理合同）——**结论不代表生产表现**，只用来审思路",
                "    ⟨原生思考通道⟩ "
                + (f"{len(native)} 字" if native else "本模型未返回（该供应商没开这条通道）"),
            ]
            if native:
                lines += [f"      {line}" for line in native.splitlines()]
            lines += ["    ⟨结构化走查 + 结论（原文，未加工）⟩"]
            lines += [f"      {line}" for line in call["content"].splitlines()]
            lines += [
                f"    → 生产解析器解出的 anchor: {produced['anchor'] or '（解不出 → evidence 不过）'}"
                f"   落点 {produced['pct']}%"
                + (f"（距期望 {abs(produced['pct'] - exp_pct)}）"
                   if produced["pct"] >= 0 and exp_pct >= 0 else "")
                + f"   逐字落文: {'是' if produced['ok'] else '否'}   耗时 {call['secs']}s"
                + (f"   CALL ERROR {call['error'][:160]}" if call["error"] else ""),
            ]
            records.append({"case": case["name"], "run": run, "mode": "R",
                            "expect_pct": exp_pct, "kw_pct": kw_pct, **produced,
                            "native_thinking": native, "secs": call["secs"],
                            "error": call["error"], "raw": call["content"]})
        lines.append("")

    return lines, records


def summary_line(case: dict, records: list[dict]) -> str:
    prod = [r for r in records if r["mode"] == "P"]
    reason = [r for r in records if r["mode"] == "R"]
    parts = [case["name"]]
    for label, rows in (("P", prod), ("R", reason)):
        if not rows:
            parts.append(f"{label} 未跑")
            continue
        ok = [r for r in rows if r["ok"]]
        text = f"{label} " + (f"{ok[0]['pct']}%" if ok else f"作废 {len(rows)} 次")
        if ok and ok[0]["expect_pct"] >= 0:
            text += f"（距期望 {abs(ok[0]['pct'] - ok[0]['expect_pct'])}）"
        parts.append(text)
    if prod and reason:
        pairs = list(zip(prod, reason))
        if all(p["ok"] and q["ok"] for p, q in pairs):
            parts.append("P vs R anchor 一致" if all(p["anchor"] == q["anchor"] for p, q in pairs)
                         else "P vs R anchor **不同**")
    if prod:
        parts.append(f"P 越界 {max(r['leftover'] for r in prod)} 字")
    if reason:
        native = max(len(r.get("native_thinking") or "") for r in reason)
        parts.append(f"R 原生思考 {native} 字" if native else "R 无原生思考通道")
    return "\t".join(parts)


def main() -> None:
    from app.services import illustration_prompt_store as store
    from app.services import scene_illustration

    chain = load_chain_probe()
    endpoint = resolve_endpoint()
    contract = reasoning_contract(scene_illustration)
    rubric = store.effective_climax_anchor_rubric()
    cases = build_cases(chain)

    lines = [
        "插画锚点 · 思路审核探针（取「判据 LLM 怎么想的」；生产结构上拿不到思路，见脚本头注释）",
        f"模型     = {endpoint['model']}   （{endpoint['label']}）",
        f"端点     = {endpoint['base'] or OLLAMA}   直连={not PROXY}"
        + (f"   代理={PROXY}" if PROXY else ""),
        f"档位     = {'P 生产档 ' if WANT_PROD else ''}{'R 推理档' if WANT_REASON else ''}"
        f"   REPEAT={REPEAT}   原生思考={'尝试' if TRY_NATIVE_THINK else '关'}",
        f"判据     = illustration_prompt_store.effective_climax_anchor_rubric()  长度={len(rubric)}",
        f"用例     = {', '.join(case['name'] for case in cases) or '（无可跑用例）'}",
        "",
    ]
    all_records: list[dict] = []
    summary: list[str] = []
    for case in cases:
        case_lines, records = run_case(
            scene_illustration, chain, case, endpoint, rubric, contract,
        )
        lines += case_lines
        all_records += records
        summary.append(summary_line(case, records))

    lines += ["──────── 汇总 ────────", *summary]
    report = "\n".join(lines)
    OUT.write_text(report, encoding="utf-8")
    SAMPLES_OUT.write_text(
        json.dumps(all_records, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 控制台花屏不影响落盘
        pass
    print(report)
    print("->", OUT)
    print("->", SAMPLES_OUT)


if __name__ == "__main__":
    main()
