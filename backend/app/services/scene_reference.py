"""空场景环境图生成（2026-11-12）。

输入 `scene_card`，用文生图模板生成一张**无人物**的场景建立图，按 `scene_key`
缓存到 `scene_refs/<scene_key>.png`。本模块只做「卡 → 提示词 → 提交 → 下载」，
不参与分镜编排；分镜后续把这张图当 img2img 的 base_image。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.services import comfyui_client, scene_card, template_store, workflow_submission

_LOG = logging.getLogger("uvicorn.error")
_DEFAULT_NEGATIVE = (
    "NSFW, nude, low quality, blurry, text, watermark, people, characters, "
    "close-up, cropped room, doorway framing, door in foreground"
)


@dataclass
class SceneReferenceResult:
    scene_key: str
    status: str  # generated / skipped / failed
    image_path: str = ""
    prompt: str = ""
    prompt_id: str = ""
    error: str = ""
    card: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def scene_reference_filename(scene_key: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(scene_key or "scene")).strip("_")
    return f"{safe or 'scene'}.png"


def _cached_prompt(dest: Path) -> str:
    """读取同名 sidecar 里的提示词；旧图没有 sidecar 时返回空串（视为未知）。"""
    sidecar = dest.with_suffix(".json")
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("prompt") or "") if isinstance(data, dict) else ""


def _write_cache(dest: Path, *, scene_key: str, template_id: str, prompt: str) -> None:
    dest.with_suffix(".json").write_text(
        json.dumps(
            {"scene_key": scene_key, "template_id": template_id, "prompt": prompt},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def build_template_values(
    template: dict, *, prompt: str, negative: str, width: int, height: int,
    style_loras: list[dict], base_image: str = "",
) -> dict:
    """按模板 exposed 填值：prompt/负向/尺寸/底图/LoRA，其余字段用模板默认值补齐。

    2026-11-12：`base_image` 分支与尺寸分支抽出来共用——角色参考图走图生图模板，
    与本模块共用同一个填写器（避免两处各写一份 binding 白名单而漂移）。
    `width/height` 传 0 表示**不覆盖**模板自带尺寸（图生图模板的 Resize 不在 exposed，
    传画幅也改不动，见 `character_reference` 模块头）。
    """
    lora_name = str((style_loras[0] or {}).get("name") or "") if style_loras else ""
    lora_weight = (style_loras[0] or {}).get("weight") if style_loras else None
    values: dict[str, object] = {}
    for item in template.get("exposed") or []:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "")
        field_name = str(item.get("field") or "")
        binding = str(item.get("binding") or "")
        if not node_id or not field_name:
            continue
        key = f"{node_id}.{field_name}"
        if binding == "prompt":
            values[key] = prompt
        elif binding == "negative_prompt":
            values[key] = negative
        elif binding == "latent_width" and int(width or 0) > 0:
            values[key] = int(width)
        elif binding == "latent_height" and int(height or 0) > 0:
            values[key] = int(height)
        elif binding == "base_image" and str(base_image or "").strip():
            values[key] = str(base_image).strip()
        elif binding == "lora_name" and lora_name:
            values[key] = lora_name
        elif binding == "lora_weight" and lora_weight is not None:
            values[key] = lora_weight
        elif item.get("default") not in (None, ""):
            values[key] = item.get("default")
    return values


# 旧私有名保留：既有测试与调用方按这个名引用，行为完全一致。
_template_values = build_template_values


def generate_scene_reference(
    card: dict, *, template_id: str, url: str, out_dir: Path,
    force: bool = False, width: int = 1024, height: int = 576,
    negative: str = _DEFAULT_NEGATIVE, style_loras: list[dict] | None = None,
    style: str = "", timeout_s: int = 600, poll_s: int = 3,
) -> SceneReferenceResult:
    """生成（或复用）一张空场景环境图。"""
    scene_key = str(card.get("scene_key") or "scene").strip()
    prompt = scene_card.build_scene_prompt(card, style=style)
    dest = Path(out_dir) / scene_reference_filename(scene_key)
    # 场景卡规则/提示词变了要重出，不能只凭「文件存在」跳过（会留下旧构图）。
    if dest.exists() and not force and _cached_prompt(dest) == prompt:
        return SceneReferenceResult(
            scene_key=scene_key, status="skipped", image_path=str(dest),
            prompt=prompt, card=card,
        )
    template = template_store.get_template(template_id)
    if template is None:
        return SceneReferenceResult(
            scene_key=scene_key, status="failed", prompt=prompt,
            error=f"模板不存在：{template_id}", card=card,
        )
    loras = list(style_loras or [])
    values = build_template_values(
        template, prompt=prompt, negative=negative,
        width=width, height=height, style_loras=loras,
    )
    try:
        submitted = workflow_submission.submit_template(
            template_id, values, prompt=prompt, url=url,
            loras=loras, lora_mode="single" if loras else "none",
        )
    except Exception as exc:  # noqa: BLE001 [EXC-保留] 单场景失败不能拖垮整组
        return SceneReferenceResult(
            scene_key=scene_key, status="failed", prompt=prompt,
            error=str(exc)[:300], card=card,
        )
    prompt_id = str(submitted.get("prompt_id") or "")
    deadline = time.monotonic() + max(30, int(timeout_s))
    while time.monotonic() < deadline:
        result = comfyui_client.fetch_result(url, prompt_id)
        status = str(result.get("status") or "")
        if status == "completed":
            images = result.get("images") or []
            if not images:
                return SceneReferenceResult(
                    scene_key=scene_key, status="failed", prompt=prompt,
                    prompt_id=prompt_id, error="ComfyUI 完成但没有图片产物", card=card,
                )
            image = images[0]
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                comfyui_client.fetch_view_to_file(
                    url, str(image.get("filename") or ""), str(dest),
                    type=str(image.get("type") or "output"),
                    subfolder=str(image.get("subfolder") or ""),
                )
            except Exception as exc:  # noqa: BLE001 [EXC-保留] 下载失败照实返回
                return SceneReferenceResult(
                    scene_key=scene_key, status="failed", prompt=prompt,
                    prompt_id=prompt_id, error=f"下载场景图失败：{exc}"[:300], card=card,
                )
            _write_cache(dest, scene_key=scene_key, template_id=template_id, prompt=prompt)
            return SceneReferenceResult(
                scene_key=scene_key, status="generated", image_path=str(dest),
                prompt=prompt, prompt_id=prompt_id, card=card,
            )
        if status in ("failed", "not_found"):
            return SceneReferenceResult(
                scene_key=scene_key, status="failed", prompt=prompt,
                prompt_id=prompt_id,
                error=str(result.get("error") or "ComfyUI 执行失败")[:300],
                card=card,
            )
        time.sleep(max(1, int(poll_s)))
    return SceneReferenceResult(
        scene_key=scene_key, status="failed", prompt=prompt,
        prompt_id=prompt_id, error=f"等待 ComfyUI 超时（{timeout_s}s）", card=card,
    )


def generate_scene_references(
    cards: dict[str, dict], *, template_id: str, url: str, out_dir: Path,
    force: bool = False, width: int = 1024, height: int = 576,
    negative: str = _DEFAULT_NEGATIVE, style_loras: list[dict] | None = None,
    style: str = "", timeout_s: int = 600,
) -> dict:
    """逐场景生成环境图并写 manifest.json；已存在的图默认跳过。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[SceneReferenceResult] = []
    for key, raw_card in (cards or {}).items():
        if not isinstance(raw_card, dict):
            continue
        card = dict(raw_card)
        card.setdefault("scene_key", str(key))
        results.append(generate_scene_reference(
            card, template_id=template_id, url=url, out_dir=out_dir,
            force=force, width=width, height=height, negative=negative,
            style_loras=style_loras, style=style, timeout_s=timeout_s,
        ))
    manifest = {
        "template_id": template_id,
        "scenes": {item.scene_key: item.to_dict() for item in results},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return manifest
