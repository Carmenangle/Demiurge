"""角色当前状态参考图（2026-11-12）。

输入 `character_state_card` + 该角色的**定妆底图**，用图生图模板（`LoadImage → VAEEncode`）
生成一张 9:16 的「当前状态」参考图：身份由底图锁住，服装/状态/场景光由提示词给出。

三条边界（都是真机踩出来的）：

· **必须走图生图，不能纯文生图**。纯文生图只有外貌文字，脸会漂；`base_image` 缺失时
  本模块直接 `failed`，不用「文生图 + 外貌」硬凑（那等于重新编一个人）。
· **底图必须先上传到 ComfyUI 的 input 目录**。`characterLoras[name].baseImage` 是本机绝对
  路径，`LoadImage` 只认 input 目录里的相对名——直接塞绝对路径会 `Invalid image file`
  （2026-11-12 真机 100 号节点实锤），所以这里先 `upload_image` 再填槽。
· **画幅不由本模块决定**。真机 `Krea2-平铺图生图`（`e8bbc2c9…`）的
  `ResizeImageMaskNode` 硬写 `576×1024 + crop=center` 且**不在 `exposed` 里**，
  `EmptyLatentImage` 又没接线 ⇒ 传 `latent_width/height` 是空转，输出恒 864×1536（9:16）。
  所以这里**不传尺寸**（`width=height=0`），与用户定案「角色参考图 9:16」一致。
· **缓存键含状态哈希**。同场景同状态重跑 skip；服装/状态变了（`state_hash` 变）自动重出。
  只凭「文件存在」跳过会留下旧服装的图（`scene_reference` 同一课）。

输出：`<out_dir>/<name>.png` + 同名 `.json` sidecar（prompt / base_image / state_hash）+ `manifest.json`。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.services import comfyui_client, scene_reference, template_store, workflow_submission

_LOG = logging.getLogger("uvicorn.error")

# 图生图模板的默认负面：图生图最怕「换脸/换人」，比场景图的负面更强调身份稳定。
_DEFAULT_NEGATIVE = (
    "different person, different face, different hairstyle, changed body type, "
    "extra characters, multiple people, low quality, blurry, text, watermark, "
    "cropped head, cropped feet, out of frame"
)


@dataclass
class CharacterReferenceResult:
    name: str
    scene_key: str
    status: str  # generated / skipped / failed
    image_path: str = ""
    prompt: str = ""
    prompt_id: str = ""
    state_hash: str = ""
    error: str = ""
    card: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def character_reference_filename(name: str) -> str:
    raw = str(name or "character").strip()
    safe = re.sub(r"[^\w.-]+", "_", raw, flags=re.UNICODE).strip("_")
    return f"{safe or 'character'}.png"


def _cached_meta(dest: Path) -> dict:
    sidecar = dest.with_suffix(".json")
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(dest: Path, *, name: str, template_id: str, prompt: str,
                 base_image: str, state_hash: str) -> None:
    dest.with_suffix(".json").write_text(
        json.dumps(
            {
                "name": name,
                "template_id": template_id,
                "prompt": prompt,
                "base_image": base_image,
                "state_hash": state_hash,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def generate_character_reference(
    card: dict, *, prompt: str, template_id: str, url: str, out_dir: Path,
    force: bool = False, negative: str = _DEFAULT_NEGATIVE,
    style_loras: list[dict] | None = None, timeout_s: int = 600, poll_s: int = 3,
) -> CharacterReferenceResult:
    """生成（或复用）一张角色当前状态参考图。"""
    name = str(card.get("name") or "").strip()
    scene_key = str(card.get("scene_key") or "").strip()
    identity = card.get("identity") or {}
    base_image = str(identity.get("base_image") or "").strip()
    digest = str(card.get("state_hash") or "")
    if not name:
        return CharacterReferenceResult(
            name="", scene_key=scene_key, status="failed",
            error="状态卡缺少角色名", card=card,
        )
    if not base_image:
        return CharacterReferenceResult(
            name=name, scene_key=scene_key, status="failed",
            error="缺少定妆底图（characterLoras[name].baseImage 为空），不能用纯文生图替代",
            card=card, state_hash=digest,
        )
    if not Path(base_image).is_file():
        return CharacterReferenceResult(
            name=name, scene_key=scene_key, status="failed",
            error=f"定妆底图文件不存在：{base_image}", card=card, state_hash=digest,
        )

    dest = Path(out_dir) / character_reference_filename(name)
    if dest.exists() and not force:
        meta = _cached_meta(dest)
        if meta.get("prompt") == prompt and meta.get("state_hash") == digest:
            return CharacterReferenceResult(
                name=name, scene_key=scene_key, status="skipped",
                image_path=str(dest), prompt=prompt, state_hash=digest, card=card,
            )

    template = template_store.get_template(template_id)
    if template is None:
        return CharacterReferenceResult(
            name=name, scene_key=scene_key, status="failed", prompt=prompt,
            error=f"模板不存在：{template_id}", card=card, state_hash=digest,
        )
    loras = list(style_loras or [])
    has_base_slot = any(
        isinstance(item, dict) and str(item.get("binding") or "") == "base_image"
        for item in (template.get("exposed") or [])
    )
    if not has_base_slot:
        # 模板没有 base_image 槽 ⇒ 图生图无从谈起，别提交一个纯文生图任务骗自己。
        return CharacterReferenceResult(
            name=name, scene_key=scene_key, status="failed", prompt=prompt,
            error=f"模板 {template_id} 没有 base_image 槽，无法锁身份；请选图生图模板",
            card=card, state_hash=digest,
        )
    # 底图是**本机绝对路径**，ComfyUI 的 LoadImage 只认它自己 input 目录里的名字：
    # 直接塞绝对路径会 `Invalid image file`（2026-11-12 真机 100 号节点实锤）。
    try:
        image_ref = comfyui_client.upload_image(
            url, Path(base_image).name, Path(base_image).read_bytes(),
        )
    except Exception as exc:  # noqa: BLE001 [EXC-保留] 上传失败照实返回，不提交半成品
        return CharacterReferenceResult(
            name=name, scene_key=scene_key, status="failed", prompt=prompt,
            error=f"上传定妆底图到 ComfyUI 失败：{exc}"[:300], card=card, state_hash=digest,
        )
    values = scene_reference.build_template_values(
        template, prompt=prompt, negative=negative,
        width=0, height=0, style_loras=loras, base_image=image_ref,
    )
    try:
        submitted = workflow_submission.submit_template(
            template_id, values, prompt=prompt, url=url,
            loras=loras, lora_mode="single" if loras else "none",
        )
    except Exception as exc:  # noqa: BLE001 [EXC-保留] 单角色失败不能拖垮整组
        return CharacterReferenceResult(
            name=name, scene_key=scene_key, status="failed", prompt=prompt,
            error=str(exc)[:300], card=card, state_hash=digest,
        )

    prompt_id = str(submitted.get("prompt_id") or "")
    deadline = time.monotonic() + max(30, int(timeout_s))
    while time.monotonic() < deadline:
        result = comfyui_client.fetch_result(url, prompt_id)
        status = str(result.get("status") or "")
        if status == "completed":
            images = result.get("images") or []
            if not images:
                return CharacterReferenceResult(
                    name=name, scene_key=scene_key, status="failed", prompt=prompt,
                    prompt_id=prompt_id, error="ComfyUI 完成但没有图片产物",
                    card=card, state_hash=digest,
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
                return CharacterReferenceResult(
                    name=name, scene_key=scene_key, status="failed", prompt=prompt,
                    prompt_id=prompt_id, error=f"下载角色参考图失败：{exc}"[:300],
                    card=card, state_hash=digest,
                )
            _write_cache(
                dest, name=name, template_id=template_id, prompt=prompt,
                base_image=base_image, state_hash=digest,
            )
            return CharacterReferenceResult(
                name=name, scene_key=scene_key, status="generated",
                image_path=str(dest), prompt=prompt, prompt_id=prompt_id,
                state_hash=digest, card=card,
            )
        if status in ("failed", "not_found"):
            return CharacterReferenceResult(
                name=name, scene_key=scene_key, status="failed", prompt=prompt,
                prompt_id=prompt_id,
                error=str(result.get("error") or "ComfyUI 执行失败")[:300],
                card=card, state_hash=digest,
            )
        time.sleep(max(1, int(poll_s)))
    return CharacterReferenceResult(
        name=name, scene_key=scene_key, status="failed", prompt=prompt,
        prompt_id=prompt_id, error=f"等待 ComfyUI 超时（{timeout_s}s）",
        card=card, state_hash=digest,
    )


def generate_character_references(
    cards: list[dict], *, prompts: dict[str, str], template_id: str, url: str,
    out_dir: Path, force: bool = False, negative: str = _DEFAULT_NEGATIVE,
    style_loras: list[dict] | None = None, timeout_s: int = 600,
) -> dict:
    """逐角色生成并写 `manifest.json`；单角色失败不影响其余。

    **只跑一个角色时不许抹掉别的角色**：CLI 支持 `--characters 腓特烈` 单独补一张，
    若每次都整份覆盖 manifest，补完第三张就只剩第三张的记录了。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[CharacterReferenceResult] = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        name = str(card.get("name") or "").strip()
        results.append(generate_character_reference(
            card, prompt=str(prompts.get(name) or ""), template_id=template_id,
            url=url, out_dir=out_dir, force=force, negative=negative,
            style_loras=style_loras, timeout_s=timeout_s,
        ))
    manifest_path = out_dir / "manifest.json"
    existing: dict = {}
    try:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(old, dict) and isinstance(old.get("characters"), dict):
            existing = old["characters"]
    except (OSError, ValueError):
        existing = {}
    manifest = {
        "template_id": template_id,
        "characters": {
            **existing,
            **{item.name: item.to_dict() for item in results},
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return manifest
