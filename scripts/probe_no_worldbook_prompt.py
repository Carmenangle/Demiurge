"""零 LLM 离线渲染对照：世界书拔除 × 内容落点 × 首轮/多轮（2026-11-03）。

回答用户的问题「没有世界书、只有角色卡会是什么情况」——不猜，把各形态的
**最终提示词原文**渲染出来并排量。

五臂（变量正交）：
  A1 现状·多轮              绑卡 + 绑同名独立世界书，真实多轮（今天真机形态）
  A2 无书·多轮              同一张卡，不绑世界书
  A3 纯卡·多轮              不绑书，但把书里「角色卡·」条目正文搬回卡 description
  A4 纯卡·首轮              同 A3，但历史为空（走 opening_only 选角分支）
  A5 现状·首轮              同 A1，历史为空

不起后端、不连 LLM、不连 embed：桩掉 `worldbook._retrieve` / `schedule_index` /
`_worldbook_greenlight`（⇒ 只含 **keyword 命中 + constant 常驻** 两条轨，语义/BM25/
绿灯轨不在内；各臂同桩 ⇒ 对「书在不在」这个对照无影响）。
也不挂能动性子图（`_agency_prelude` 置 None）⇒ 不含 state 块/NPC 目标/RAG 召回。

跑法：backend/.venv/Scripts/python.exe scripts/probe_no_worldbook_prompt.py
      PROBE_CARD=卡名  PROBE_OUT=<目录>  PROBE_QUERY="本轮用户输入"
产出：PROBE_OUT（默认 %TEMP%/no-wb-probe）下 report.md + 每臂 messages.json。
      ⚠ 只写仓外，仓内 `_*` 草稿会被 release_preflight 密钥扫描误报。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

USERDATA = ROOT / "userdata"

from app.services import agent_context_assembly as assembly  # noqa: E402
from app.services import agent_graph_roleplay as agr  # noqa: E402
from app.services import character_store, run_trace, worldbook  # noqa: E402
from app.services import worldbook_store  # noqa: E402

CHARACTER_DIR = str(USERDATA / "character")
WORLDS_DIR = str(USERDATA / "worlds")

# ────────────────────────── 桩（零网络/零 LLM） ──────────────────────────
EVENTS: list[dict] = []


def _stub_trace_emit(ctx, event: str, **payload) -> None:
    EVENTS.append({"event": event, **(payload or {})})


run_trace.emit = _stub_trace_emit  # type: ignore[assignment]
worldbook._retrieve = lambda *a, **k: []  # type: ignore[assignment]
worldbook.schedule_index = lambda *a, **k: None  # type: ignore[assignment]


class _NoGreenlight:
    ran = False
    positions: list[int] = []
    error = "greenlight stubbed (零 LLM)"


assembly._worldbook_greenlight = lambda *a, **k: _NoGreenlight  # type: ignore[assignment]
agr._agency_prelude = lambda ctx, text: (None, 0, 0.0, "")  # type: ignore[assignment]
# 走内置默认文案，不吃用户态 roleplay 覆盖 ⇒ 各臂只差在「书/卡」，不掺个性化变量
agr._builtin = lambda ctx, agent, key, default: default  # type: ignore[assignment]


# ────────────────────────── 渲染一条剧情轮 ──────────────────────────
def render(ctx: dict, user_text: str) -> dict:
    """逐接缝复刻 roleplay_node 的组装顺序，产出最终 messages（不调模型）。"""
    EVENTS.clear()
    first_story_reply = agr._rp_is_first_story_reply(ctx)
    wb, wb_recall = agr._rp_prepare_worldbook(ctx, user_text)
    story_round = agr._rp_last_story_round(ctx)
    agr._rp_resolve_persona_block(ctx, user_text, first_story_reply)
    agr._rp_load_style_config(ctx)

    dynamic_parts: list[str] = []
    base, temp, dialogue = agr._rp_build_prompt_base(
        ctx, wb=wb, preset_msgs=[], preset_temp=None,
        has_hist=False, story_round=story_round)
    agr._rp_seed_dynamic_parts(ctx, wb_recall, dynamic_parts)
    base = agr._rp_append_static_blocks(ctx, base, [])
    base, dynamic_text = agr._rp_substitute_macros(ctx, base, dynamic_parts)
    system = assembly._agent_system(ctx, base)
    system = agr._rp_prefix_output_structure(system)
    tail_msgs = agr._rp_build_tail_messages(ctx, dynamic_text, [], user_text)
    messages = [{"role": "system", "content": system}, *dialogue, *tail_msgs,
                {"role": "user", "content": user_text}]

    book = assembly._repo_worldbook(ctx)
    entries = worldbook.parse_entries(book)
    return {
        "first_story_reply": first_story_reply,
        "messages": messages,
        "total_chars": sum(len(m["content"]) for m in messages),
        "message_count": len(messages),
        "sections": {
            "system_head_chars": len(system),
            "worldbook_anchor_chars": len(wb),
            "worldbook_recall_chars": len(wb_recall),
            "worldbook_dynamic_chars": len(str(ctx.get("_worldbook_dynamic_text") or "")),
            "card_persona_chars": len(str(ctx.get("persona") or "")),
            "tail_dynamic_chars": len(dynamic_text),
        },
        "diagnostics": {
            "worldbook_injected": bool(wb),
            "worldbook_entries_in_snapshot": len(entries),
            "constant_entries": sum(1 for e in entries if e.constant),
            "card_description_chars_on_disk": len(str(
                (character_store.read_card(
                    ctx.get("character_dir") or "", ctx.get("card_name") or "") or {})
                .get("description") or "")),
            "names_recognised_from_worldbook": ctx.get("_worldbook_character_names") or [],
            "cards_actually_injected": ctx.get("_selected_persona_names") or [],
            "world_rules_seen": len(ctx.get("_world_rules") or []),
            "system_has_card_block": "【角色：" in system,
            "system_has_world_block": "【世界设定" in system,
        },
    }


def base_ctx(out_dir: Path, repo_id: str, card: str, history: list[dict]) -> dict:
    return {
        "repo_id": repo_id, "thread_id": repo_id, "output_dir": str(out_dir),
        "character_dir": CHARACTER_DIR,
        "card_name": card, "card_names": [card], "opening_card_name": card,
        "user_name": "我", "user_persona": "",
        "worldbook_dir": "", "worldbook_name": "",
        "chat_model": "probe", "provider_profile": "openai_compatible",
        "history": history, "preset_name": "", "agent_cfg": None,
        "style_template": "", "skill_frags": [], "comfy_audio": "",
        "embed_base": "", "embed_key": "", "embed_model": "",
        "appearance_source": "worldbook",
    }


def opening_of(card: str) -> str:
    return str((character_store.read_card(CHARACTER_DIR, card) or {}).get("first_mes") or "").strip()


def make_pure_card_copy(out_dir: Path, card: str) -> tuple[str, int, int]:
    """把书里「角色卡·」层条目正文搬进卡 description，落成一张临时纯卡（不动真实数据）。"""
    entries = worldbook.parse_entries(worldbook_store.read_book(WORLDS_DIR, card))
    role_entries = [e for e in entries if str(e.comment or "").startswith("角色卡·")]
    body = "\n\n".join(f"{e.comment}\n{e.content}" for e in role_entries)
    dst_dir = out_dir / "character_pure" / card
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in ("card.json", "regex.json"):
        src = Path(CHARACTER_DIR) / card / name
        if src.is_file():
            shutil.copy2(src, dst_dir / name)
    target = dst_dir / "card.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    data["description"] = body
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_dir / "character_pure"), len(role_entries), len(body)


def main() -> int:
    card = os.environ.get("PROBE_CARD", "").strip()
    if not card:
        for p in sorted(Path(CHARACTER_DIR).iterdir()):
            if (p / "card.json").is_file() and Path(WORLDS_DIR, f"{p.name}.json").is_file():
                card = p.name
                break
    if not card or not worldbook_store.read_book(WORLDS_DIR, card):
        print(f"!! 找不到卡或同名世界书：{card}", file=sys.stderr)
        return 2

    query = os.environ.get("PROBE_QUERY", "他走近了些，气氛有点僵。")
    out_dir = Path(os.environ.get("PROBE_OUT") or (tempfile.gettempdir() + "/no-wb-probe"))
    out_dir.mkdir(parents=True, exist_ok=True)

    multi = [{"role": "user", "content": "（上一轮）继续。"},
             {"role": "assistant", "content": opening_of(card) or "（无开场白）"}]
    pure_dir, n_role_entries, pure_body_chars = make_pure_card_copy(out_dir, card)

    arms: dict[str, dict] = {}

    def run(name: str, *, worldbook_on: bool, character_dir: str, history: list[dict]) -> None:
        ctx = base_ctx(out_dir, f"probe{len(arms) + 1}", card, history)
        ctx["character_dir"] = character_dir
        if worldbook_on:
            ctx["worldbook_dir"] = WORLDS_DIR
            ctx["worldbook_name"] = card
        arms[name] = render(ctx, query)

    run("A1 现状·有书·多轮", worldbook_on=True, character_dir=CHARACTER_DIR, history=multi)
    run("A2 无书·多轮", worldbook_on=False, character_dir=CHARACTER_DIR, history=multi)
    run("A3 纯卡·多轮", worldbook_on=False, character_dir=pure_dir, history=multi)
    run("A4 纯卡·首轮", worldbook_on=False, character_dir=pure_dir, history=[])
    run("A5 现状·有书·首轮", worldbook_on=True, character_dir=CHARACTER_DIR, history=[])

    # ── 落盘 + 报告 ──
    lines = [
        "# 世界书拔除 × 内容落点 对照探针",
        "",
        f"- 卡：`{card}`（真实卡，未改动；纯卡副本落在 `{pure_dir}`）",
        f"- 本轮输入：`{query}`",
        "- 多轮臂的历史：开场白 1 条 AI 楼层（上下文合同只进上一轮）",
        f"- 纯卡副本内容来源：世界书「角色卡·」层 **{n_role_entries} 条 / {pure_body_chars} 字**",
        "- 桩：`_retrieve`/`schedule_index`/绿灯/能动性子图 ⇒ 只含 keyword+constant 两轨",
        "",
        "| 臂 | 总字数 | 消息数 | 书锚点 | 书召回 | 书动态 | 卡persona | 尾部动态 | 规则条 | 含卡段 | 含书段 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in arms.items():
        s, d = r["sections"], r["diagnostics"]
        lines.append(
            f"| {name} | {r['total_chars']} | {r['message_count']} "
            f"| {s['worldbook_anchor_chars']} | {s['worldbook_recall_chars']} "
            f"| {s['worldbook_dynamic_chars']} | {s['card_persona_chars']} "
            f"| {s['tail_dynamic_chars']} | {d['world_rules_seen']} "
            f"| {d['system_has_card_block']} | {d['system_has_world_block']} |")

    lines += ["", "## 各臂诊断", ""]
    for name, r in arms.items():
        d = r["diagnostics"]
        lines += [
            f"### {name}",
            f"- 首轮判定：`{r['first_story_reply']}`",
            f"- 世界书注入：`{d['worldbook_injected']}`（快照内条目 {d['worldbook_entries_in_snapshot']}，"
            f"其中 constant {d['constant_entries']}）",
            f"- 磁盘上该臂读的卡 description 字数：`{d['card_description_chars_on_disk']}`",
            f"- 从世界书正文认出的卡名：`{d['names_recognised_from_worldbook']}`",
            f"- **实际注入人设的卡**：`{d['cards_actually_injected']}`",
            "",
        ]

    for name, r in arms.items():
        slug = name.split()[0]
        (out_dir / f"{slug}-messages.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    report = out_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\n[落盘] {report}")
    for name in arms:
        print(f"[落盘] {out_dir / (name.split()[0] + '-messages.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
