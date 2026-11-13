"""角色当前状态参考图 CLI（2026-11-12）。

从真实作品对话的**最新一轮**抽出在场角色，按当前服装/状态/场景光各生成一张 9:16 参考图：

```text
chat.json 最新 assistant 正文
  ├─ <status> [在场] → 角色名单（与预设 characterLoras 求交）
  ├─ <status> [地点] → locale
  └─ 正文 → scene_card.extract_scene_card（拿 lighting）
        ↓
  character_state_card.extract_state_card（每人一张卡，原文切片 + missing/inferred）
        ↓
  image_prompt_profiles.generate_result（与一键定妆同一条编译器）
        ↓
  character_reference.generate_character_reference（图生图锁身份，输出 864×1536）
```

用法（backend 目录）：

```powershell
.venv/Scripts/python scripts/probe_character_refs.py `
  --chat ../userdata/pictures/示例作品甲/SAVE02/chat.json `
  --repo ../userdata/pictures/示例作品甲 --repo-id c5c60de2-18f1-4b88-b15a-5d4f3f5e652e `
  --llm live --limit 3
```

`--dry-run` 只打状态卡与提示词，不提交 ComfyUI（零成本核对用）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import (  # noqa: E402
    character_reference, character_state_card, image_prompt_profiles, llm,
    repo_meta, scene_card,
)

_STATUS_RE = re.compile(r"<status>(.*?)</status>", re.S)
_FIELD_RE = re.compile(r"^\[(?P<key>[^\]]+)\]\s*(?P<value>.*)$", re.M)
_PRESENT_SPLIT_RE = re.compile(r"[；;]")


def _assistant_texts(chat_path: Path) -> list[dict]:
    """倒序返回 `[{"id","turn","text"}]`：第一条是最新一轮。"""
    data = json.loads(chat_path.read_text(encoding="utf-8"))
    messages = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(messages, list):
        raise SystemExit(f"chat.json 结构不认识：{chat_path}")
    out: list[dict] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        text = str(message.get("text") or message.get("content") or "")
        if text.strip():
            out.append({
                "id": str(message.get("id") or ""),
                "turn": str(message.get("turnNo") or ""),
                "text": text,
            })
    if not out:
        raise SystemExit("chat.json 里没有 assistant 正文")
    out.reverse()
    return out


def _pick_turn(turns: list[dict], *, turn: str = "", message: str = "") -> dict:
    """选剧情片段：`--message` 前缀 > `--turn` 轮号 > 最新一轮。"""
    if message:
        hit = next((item for item in turns if item["id"].startswith(message)), None)
        if hit is None:
            raise SystemExit(f"没有 id 以 {message} 开头的 assistant 消息")
        return hit
    if turn:
        hit = next((item for item in turns if item["turn"] == str(turn)), None)
        if hit is None:
            raise SystemExit(f"没有 turnNo={turn} 的 assistant 消息")
        return hit
    return turns[0]


def _status_fields(text: str) -> dict[str, str]:
    hit = _STATUS_RE.search(text or "")
    if not hit:
        return {}
    fields: dict[str, str] = {}
    for line in hit.group(1).splitlines():
        match = _FIELD_RE.match(line.strip())
        if match:
            fields[match.group("key").strip()] = match.group("value").strip()
    return fields


def _present_names(present: str) -> list[str]:
    """`[在场]` → 角色名列表；括号里的位置/动作不是名字，`我` 不是角色。"""
    names: list[str] = []
    for clause in _PRESENT_SPLIT_RE.split(present or ""):
        part = re.split(r"[（(：:]", clause.strip())[0].strip()
        part = re.sub(r"(若干|们|等)$", "", part).strip()
        if not part or part in {"我", "玩家", "用户"}:
            continue
        if part not in names:
            names.append(part)
    return names


def _preset(repo_id: str) -> dict:
    state = repo_meta.load_user_state()
    settings = state.get("settings") if isinstance(state, dict) else None
    media = (settings or {}).get("mediaInsert") if isinstance(settings, dict) else None
    if not isinstance(media, dict):
        raise SystemExit("读不到 user_state.settings.mediaInsert")
    preset = media.get(repo_id)
    if not isinstance(preset, dict):
        raise SystemExit(f"没有 {repo_id} 的多元数据插入预设（characterLoras 来源）")
    return preset


def _chat_generate() -> object:
    state = repo_meta.load_user_state()
    settings = state.get("settings") if isinstance(state, dict) else None
    if not isinstance(settings, dict):
        raise SystemExit("读不到 user_state.settings")
    models = settings.get("chatModels") or []
    active_id = settings.get("activeChatModelId")
    picked = next((m for m in models if m.get("id") == active_id), None)
    if not picked:
        picked = next((m for m in models if m.get("apiKey")), None)
    if not picked:
        raise SystemExit("没有可用的对话模型（chatModels 里没有带 key 的条目）")
    base = str(picked.get("baseUrl") or "").strip()
    key = str(picked.get("apiKey") or "").strip()
    model = str(picked.get("modelName") or "").strip()
    if not (base and key and model):
        raise SystemExit("对话模型配置不完整（baseUrl/apiKey/modelName）")
    proxy = ""
    if settings.get("proxyEnabled"):
        source = str(settings.get("systemProxySource") or "")
        proxy = str(
            (settings.get("systemProxyUrl") if source == "system" else "")
            or settings.get("proxyUrl") or ""
        ).strip()

    def generate_text(system: str, user: str) -> str:
        return llm.chat(
            base, key, model, system, user, temperature=0.3, timeout=180.0, proxy=proxy,
        )

    return generate_text


def _merge_cards(path: Path, fresh: list[dict]) -> list[dict]:
    """按角色名合并，单独补跑一个角色时保留其余角色的状态卡。"""
    merged: dict[str, dict] = {}
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(old, list):
            for card in old:
                if isinstance(card, dict) and card.get("name"):
                    merged[str(card["name"])] = card
    except (OSError, ValueError):
        pass
    for card in fresh:
        if isinstance(card, dict) and card.get("name"):
            merged[str(card["name"])] = card
    return list(merged.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="角色当前状态参考图（9:16，图生图锁身份）")
    parser.add_argument("--chat", required=True, help="作品 chat.json")
    parser.add_argument("--repo", default="",
                        help="作品根目录 = outputDir（默认取设置；⚠ 不是作品文件夹本身）")
    parser.add_argument("--repo-id", required=True, help="小仓库 id（_repo.json 的 id）")
    parser.add_argument("--characters", default="", help="逗号分隔，覆盖自动抽取")
    parser.add_argument("--turn", default="", help="用哪一轮的剧情片段（turnNo）")
    parser.add_argument("--message", default="", help="用哪条消息的剧情片段（id 前缀）")
    parser.add_argument("--scene-key", default="current", help="输出子目录名")
    parser.add_argument("--template", required=True, help="图生图模板 id（必须有 base_image 槽）")
    parser.add_argument("--out", default="", help="输出目录（默认 <repo>/<小仓库>/character_refs/<scene_key>）")
    parser.add_argument("--lora", default="", help="风格 LoRA 文件名")
    parser.add_argument("--lora-weight", type=float, default=0.8)
    parser.add_argument("--profile", default="", help="图片 Profile（默认取预设 promptProfile）")
    parser.add_argument("--llm", default="live", choices=["live", "mock"],
                        help="live=真调对话模型编译提示词；mock=只用固定桩（只验结构）")
    parser.add_argument("--url", default="", help="ComfyUI 地址（默认取设置）")
    parser.add_argument("--limit", type=int, default=0, help="只做前 N 个角色")
    parser.add_argument("--force", action="store_true", help="忽略缓存强制重出")
    parser.add_argument("--no-turn-fallback", action="store_true",
                        help="本轮没写到的角色不回退到上一轮（默认回退并标注 turn）")
    parser.add_argument("--dry-run", action="store_true", help="只打状态卡与提示词，不提交")
    parser.add_argument("--rebuild-manifest", action="store_true",
                        help="只按 sidecar 重建 manifest/cards，不调 LLM、不提交、不重出图")
    args = parser.parse_args()

    chat_path = Path(args.chat).expanduser()
    turns = _assistant_texts(chat_path)
    picked = _pick_turn(turns, turn=args.turn, message=args.message)
    text = picked["text"]
    print(f"片段：turn={picked['turn'] or '?'} id={picked['id'][:8]} 正文 {len(text)} 字")
    fields = _status_fields(text)
    preset = _preset(args.repo_id)
    characters = preset.get("characterLoras") or {}
    # outputDir 是所有仓库的共同根（`<根>/<作品>/<子库>`）；传作品文件夹会多拼一层。
    base = args.repo or repo_meta.output_dir_from_state()
    if not base:
        raise SystemExit("读不到 outputDir，请显式传 --repo（作品根目录）")

    present = fields.get("[在场]", "") or fields.get("在场", "")
    names = [name for name in _present_names(present) if name in characters]
    if args.characters:
        names = [item.strip() for item in args.characters.split(",") if item.strip()]
    if not names:
        raise SystemExit(f"[在场] 里没有能对上预设的角色：{present!r}")
    if args.limit > 0:
        names = names[: args.limit]

    # 地点字段各家写法不同：示例作品甲用 `[地点]`，示例作品乙用 `[所在]`。
    locale = fields.get("地点", "") or fields.get("所在", "") or ""
    scene_key = args.scene_key
    scene = scene_card.extract_scene_card(text, scene_key=scene_key, locale=locale)
    lighting = scene.get("lighting") or {}
    print(f"角色 {len(names)}：{'、'.join(names)}")
    print(f"地点：{locale or '（正文未写）'} | 光：{lighting}")

    profile = str(args.profile or preset.get("promptProfile") or "anima_tags")
    style_loras = (
        [{"name": args.lora, "weight": args.lora_weight}] if args.lora else None
    )
    generate_text = _chat_generate() if args.llm == "live" else (
        lambda system, user: "{}"
    )

    out_dir = Path(args.out) if args.out else (
        repo_meta.engine_repo_dir(base, args.repo_id) / "character_refs" / scene_key
    )

    cards: list[dict] = []
    prompts: dict[str, str] = {}
    for name in names:
        base_image = str((characters.get(name) or {}).get("baseImage") or "").strip()
        card = character_state_card.extract_state_card(
            base=base, repo_id=args.repo_id, name=name, text=text,
            present=present, scene_key=scene_key, base_image=base_image,
            locale=locale, lighting=lighting, others=list(characters.keys()),
        )
        if "current_state" in card["missing"] and not args.no_turn_fallback:
            # 这一片段没写这个角色（如腓特烈在卧室）⇒ 回退到最近一段有该角色画面的正文，
            # 并在卡里标明来自哪一轮：这是**跨片**取景，审阅时必须看得见。
            for item in turns:
                if item["id"] == picked["id"] or name not in item["text"]:
                    continue
                alt = character_state_card.extract_state_card(
                    base=base, repo_id=args.repo_id, name=name, text=item["text"],
                    present=present, scene_key=scene_key, base_image=base_image,
                    locale=locale, lighting=lighting, others=list(characters.keys()),
                )
                if alt.get("state_text"):
                    alt["inferred"].append(
                        f"state_from_previous_turn:{item['turn'] or item['id'][:8]}"
                    )
                    alt["scene_key"] = scene_key
                    card = alt
                break
        card["state_hash"] = character_state_card.state_hash(card)
        cards.append(card)
        print(f"\n[{name}] hash={card['state_hash']} "
              f"missing={card['missing']} inferred={card['inferred']}")
        print(f"  服装：{card['wardrobe'] or '（未写，沿用底图）'}")
        print(f"  状态：{(card['state_text'] or '')[:160]}…")
        if args.dry_run or args.rebuild_manifest:
            continue
        # 状态没变就复用 sidecar 里已编译的提示词：既省一次 LLM 调用，也保证
        # `character_reference` 能按 prompt 命中缓存 skip，不重出已有的好图。
        # 单角色补跑曾把 manifest/cards 覆盖掉，全量重跑靠这条把记录补齐。
        if not args.force:
            sidecar = out_dir / character_reference.character_reference_filename(
                name
            ).replace(".png", ".json")
            try:
                cached = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = {}
            if isinstance(cached, dict) and cached.get("state_hash") == card["state_hash"]:
                cached_prompt = str(cached.get("prompt") or "").strip()
                if cached_prompt:
                    prompts[name] = cached_prompt
                    print(f"  复用已编译提示词（{len(cached_prompt)} 字，状态未变）")
                    continue
        scene_dict = character_state_card.build_state_scene(card)
        result = image_prompt_profiles.generate_result(profile, scene_dict, generate_text)
        prompts[name] = str(result.get("prompt") or "")
        print(f"  prompt({len(prompts[name])} 字)：{prompts[name][:220]}…")
        if result.get("validation_errors"):
            print(f"  ⚠ 校验：{result['validation_errors']}")

    if args.rebuild_manifest:
        records: dict[str, dict] = {}
        for card in cards:
            name = str(card.get("name") or "").strip()
            sidecar = out_dir / character_reference.character_reference_filename(
                name
            ).replace(".png", ".json")
            image = sidecar.with_suffix(".png")
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            records[name] = {
                "name": name,
                "scene_key": scene_key,
                "status": "generated" if image.is_file() else "failed",
                "image_path": str(image) if image.is_file() else "",
                "prompt": str(meta.get("prompt") or ""),
                "prompt_id": "",
                "state_hash": str(card.get("state_hash") or ""),
                "error": "" if image.is_file() else "缺少成图或 sidecar",
                "card": card,
            }
        (out_dir / "manifest.json").write_text(
            json.dumps({"template_id": args.template, "characters": records},
                       ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (out_dir / "cards.json").write_text(
            json.dumps(_merge_cards(out_dir / "cards.json", cards),
                       ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n已重建记录：{list(records)}（未调 LLM、未提交、未重出图）")
        return 0

    if args.dry_run:
        print("\n--dry-run：未提交 ComfyUI")
        return 0

    url = args.url or str((repo_meta.load_user_state().get("settings") or {}).get("comfyuiUrl") or "")
    manifest = character_reference.generate_character_references(
        cards, prompts=prompts, template_id=args.template,
        url=url or "http://127.0.0.1:8188", out_dir=out_dir, force=args.force,
        style_loras=style_loras,
    )
    print(f"\n输出目录：{out_dir}")
    for name, item in manifest["characters"].items():
        detail = item.get("image_path") or item.get("error") or ""
        print(f"  {name}: {item['status']} {detail}")
    (out_dir / "cards.json").write_text(
        json.dumps(_merge_cards(out_dir / "cards.json", cards), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
