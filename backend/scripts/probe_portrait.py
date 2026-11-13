"""角色定妆 CLI（2026-11-12）。

把「角色样貌」按正常途径补齐：世界书条目 / 角色卡 / 通用表 → `character_portrait.collect_appearance`
→ 定妆画面合同 → 图片 Profile 编译 → 文生图模板（角色 LoRA ＋ 风格 LoRA）→ `portraits/<file>.png`。

为什么需要它：`character_reference` 是**图生图锁身份**，没有定妆底图就出不了参考图；而凌霄仙母录
三个角色（凌若冰/舞姬恋/妲千沁）都只有角色 LoRA、没有 `baseImage`。定妆链本来只在前端
（`lib/portraitBaseImage.ts`）走，服务端没有等价入口，这里补上同一个口径。

用法（backend 目录）：

```powershell
.venv/Scripts/python scripts/probe_portrait.py `
  --repo-id 09f79e6f-1ae8-4468-ba2d-8a764bbaafc9 --preset 09f79e6f-1ae8-4468-ba2d-8a764bbaafc9 `
  --characters "凌若冰,妲千沁"
```

默认**不改用户设置**（不写回 `characterLoras[name].baseImage`）；确认成图后再加 `--bind`。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import (  # noqa: E402
    character_portrait, comfyui_client, llm, repo_meta, scene_reference,
    template_store, workflow_submission,
)


def _preset(preset_id: str) -> dict:
    state = repo_meta.load_user_state()
    settings = state.get("settings") if isinstance(state, dict) else None
    media = (settings or {}).get("mediaInsert") if isinstance(settings, dict) else None
    if not isinstance(media, dict):
        raise SystemExit("读不到 user_state.settings.mediaInsert")
    preset = media.get(preset_id)
    if not isinstance(preset, dict):
        raise SystemExit(f"没有 {preset_id} 的多元数据插入预设")
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


def _bind_base_image(repo_id: str, name: str, image_path: str) -> bool:
    """把新底图写回 `settings.mediaInsert[repo_id].characterLoras[name].baseImage`。"""
    state = repo_meta.load_user_state()
    settings = state.get("settings") if isinstance(state, dict) else None
    media = (settings or {}).get("mediaInsert") if isinstance(settings, dict) else None
    preset = (media or {}).get(repo_id) if isinstance(media, dict) else None
    characters = (preset or {}).get("characterLoras") if isinstance(preset, dict) else None
    entry = (characters or {}).get(name) if isinstance(characters, dict) else None
    if not isinstance(entry, dict):
        return False
    entry["baseImage"] = image_path
    return bool(repo_meta.save_settings(settings, selection=state.get("selection")))


def main() -> int:
    parser = argparse.ArgumentParser(description="角色定妆（9:16 全身，文生图 + 角色 LoRA）")
    parser.add_argument("--repo-id", required=True, help="小仓库 id（_repo.json 的 id）")
    parser.add_argument("--preset", default="", help="mediaInsert 预设 id（默认同 repo-id）")
    parser.add_argument("--repo", default="", help="outputDir（默认取设置）")
    parser.add_argument("--characters", required=True, help="逗号分隔角色名")
    parser.add_argument("--template", default="", help="文生图模板 id（默认取预设 templateId）")
    parser.add_argument("--lora", default="", help="额外风格 LoRA（默认取预设 styleLora）")
    parser.add_argument("--lora-weight", type=float, default=0.0, help="风格 LoRA 权重")
    parser.add_argument("--profile", default="", help="图片 Profile（默认取预设 promptProfile）")
    parser.add_argument("--aspect", default=character_portrait.DEFAULT_ASPECT_RATIO)
    parser.add_argument("--long-edge", type=int, default=0, help="长边像素（默认取预设 latentLongEdge）")
    parser.add_argument("--out", default="", help="输出目录（默认 <仓库>/portraits）")
    parser.add_argument("--url", default="", help="ComfyUI 地址（默认取设置）")
    parser.add_argument("--llm", default="live", choices=["live", "mock"])
    parser.add_argument("--force", action="store_true", help="即使已有底图也重出")
    parser.add_argument("--bind", action="store_true", help="把新底图写回预设 baseImage")
    args = parser.parse_args()

    preset_id = args.preset or args.repo_id
    preset = _preset(preset_id)
    base = args.repo or repo_meta.output_dir_from_state()
    if not base:
        raise SystemExit("读不到 outputDir，请显式传 --repo")
    characters = preset.get("characterLoras") or {}
    names = [item.strip() for item in args.characters.split(",") if item.strip()]
    if not names:
        raise SystemExit("--characters 为空")

    template_id = args.template or str(preset.get("templateId") or "")
    if not template_id:
        raise SystemExit("没有文生图模板 id")
    template = template_store.get_template(template_id)
    if template is None:
        raise SystemExit(f"模板不存在：{template_id}")

    profile = args.profile or str(preset.get("promptProfile") or character_portrait.DEFAULT_PROFILE)
    long_edge = args.long_edge or int(preset.get("latentLongEdge") or 1024)
    # 9:16 = 宽∶高，长边是高；latent 与 `portraitBaseImage.ts` 同口径（1024 → 576×1024）。
    short_edge = int(long_edge * 9 / 16)
    width, height = (
        (short_edge, long_edge) if args.aspect == "9:16" else (long_edge, short_edge)
    )
    url = args.url or str((repo_meta.load_user_state().get("settings") or {}).get("comfyuiUrl") or "")
    url = url or "http://127.0.0.1:8188"
    out_dir = Path(args.out) if args.out else repo_meta.engine_repo_dir(base, args.repo_id) / "portraits"
    out_dir.mkdir(parents=True, exist_ok=True)
    generate_text = _chat_generate() if args.llm == "live" else (lambda system, user: "{}")

    results: dict[str, dict] = {}
    for name in names:
        entry = characters.get(name) or {}
        existing = str(entry.get("baseImage") or "").strip()
        if existing and Path(existing).is_file() and not args.force:
            print(f"[{name}] 已有底图，跳过：{existing}")
            results[name] = {"status": "skipped", "base_image": existing}
            continue
        loras: list[dict] = []
        actor_lora = str(entry.get("loraName") or "").strip()
        if actor_lora:
            loras.append({"name": actor_lora, "weight": float(entry.get("loraWeight") or 1.0)})
        style_lora = args.lora or str(preset.get("styleLora") or "").strip()
        style_weight = args.lora_weight or float(preset.get("styleLoraWeight") or 0.0)
        if style_lora:
            loras.append({"name": style_lora, "weight": style_weight or 0.8})
        print(f"\n[{name}] loras={[item['name'] for item in loras]}")
        try:
            compiled = character_portrait.generate_portrait(
                base, args.repo_id, name, profile=profile, aspect_ratio=args.aspect,
                generate_text=generate_text,
            )
        except Exception as exc:  # noqa: BLE001 [EXC-保留] 单角色编译失败不拖垮整组
            print(f"  编译失败：{exc}")
            results[name] = {"status": "failed", "error": str(exc)[:300]}
            continue
        prompt = str(compiled.get("prompt") or "")
        print(f"  prompt({len(prompt)} 字) 外貌来源={compiled.get('appearance_origin')} "
              f"动作={compiled.get('pose')}")
        values = scene_reference.build_template_values(
            template, prompt=prompt,
            negative=str(compiled.get("negative_prompt") or ""),
            width=width, height=height, style_loras=loras,
        )
        try:
            submitted = workflow_submission.submit_template(
                template_id, values, prompt=prompt, url=url, loras=loras,
                lora_mode="multi" if len(loras) > 1 else ("single" if loras else "none"),
            )
        except Exception as exc:  # noqa: BLE001 [EXC-保留] 提交失败照实返回
            print(f"  提交失败：{exc}")
            results[name] = {"status": "failed", "error": str(exc)[:300]}
            continue
        prompt_id = str(submitted.get("prompt_id") or "")
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            result = comfyui_client.fetch_result(url, prompt_id)
            status = str(result.get("status") or "")
            if status == "completed":
                images = result.get("images") or []
                if not images:
                    results[name] = {"status": "failed", "error": "ComfyUI 完成但没有图片"}
                    break
                stamp = time.strftime("%Y%m%d_%H%M%S")
                dest = out_dir / f"{stamp}_{name}.png"
                image = images[0]
                try:
                    comfyui_client.fetch_view_to_file(
                        url, str(image.get("filename") or ""), str(dest),
                        type=str(image.get("type") or "output"),
                        subfolder=str(image.get("subfolder") or ""),
                    )
                except Exception as exc:  # noqa: BLE001 [EXC-保留] 下载失败照实返回
                    results[name] = {"status": "failed", "error": f"下载失败：{exc}"[:300]}
                    break
                print(f"  出图：{dest}")
                bound = _bind_base_image(args.repo_id, name, str(dest)) if args.bind else False
                if args.bind:
                    print(f"  绑定 baseImage：{'成功' if bound else '失败（预设里没有该角色条目）'}")
                results[name] = {
                    "status": "generated", "base_image": str(dest), "prompt": prompt,
                    "bound": bound,
                }
                break
            if status in ("failed", "not_found"):
                results[name] = {
                    "status": "failed",
                    "error": str(result.get("error") or "ComfyUI 执行失败")[:300],
                }
                print(f"  失败：{results[name]['error']}")
                break
            time.sleep(3)
        else:
            results[name] = {"status": "failed", "error": "等待 ComfyUI 超时"}
            print("  超时")

    manifest = {"template_id": template_id, "characters": results}
    (out_dir / "portrait_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\n输出目录：{out_dir}")
    for name, item in results.items():
        print(f"  {name}: {item['status']} {item.get('base_image') or item.get('error') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
