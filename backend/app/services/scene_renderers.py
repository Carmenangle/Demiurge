"""renderer concrete 适配器：把 SceneRequest 交给既有出图管线出图（有 I/O）。

配对 `scene_illustration` 的纯逻辑——那边判「该不该出图 + prompt 是什么」，这里才真出图。
两个内置格式：
- FMT_GPT_IMAGE（云）→ `image_gen.generate`（同步直返 url / data-uri）
- FMT_COMFY（本地）→ `workflow_submission.submit_template` 提交 + `comfyui_client.fetch_result` 轮询取图

每个都是「工厂：绑定运行期 config → 返回 Renderer 闭包」——config（模型/URL/尺寸）随请求变，
不适合注册期固定。拿到 RunContext 后由子图接线把绑定好的 Renderer 注册进 `scene_illustration` 表。

依赖方向：本模块 import `scene_illustration`（借其 SceneRequest/Renderer 类型）+ 既有出图管线；
`scene_illustration` **不反向 import 本模块**（scene-illustration-purity 合同保证），故无环。
token 护栏：只返回图片地址字符串，绝不把图回灌进对话（存 url+caption 是调用方的事）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode

from app.services import comfyui_client, image_gen, workflow_submission
from app.services.scene_illustration import FMT_COMFY, FMT_GPT_IMAGE, Renderer, SceneRequest

BUILTIN_FORMATS = (FMT_GPT_IMAGE, FMT_COMFY)


@dataclass
class CloudConfig:
    """云端生图 renderer 的运行期配置（来自 RunContext.generation + 尺寸/质量）。

    character_base_images：角色名→底图（本地路径/URL/data-uri）。gpt-image 系无 LoRA，
    改用底图锁角色一致性——出图时按 SceneRequest.actors 命中取底图，未命中回退 style_base_image。
    有底图则走 generate_with_images（图生图），否则纯文生图。
    """
    base_url: str
    api_key: str
    model: str
    size: str = "1024x1024"
    quality: str = "high"
    character_base_images: dict[str, str] = field(default_factory=dict)
    style_base_image: str = ""
    proxy: str = ""


@dataclass
class ComfyConfig:
    """ComfyUI renderer 的运行期配置：走已登记的模板（模板自带 prompt 注入点）。"""
    url: str
    template_id: str
    values: dict[str, object] = field(default_factory=dict)
    poll_interval: float = 1.5
    poll_timeout: float = 120.0


def _pick_base_image(cfg: CloudConfig, actors: list[str]) -> str:
    """按在场角色挑底图：命中任一角色的配置底图即用；否则回退风格底图（可空）。"""
    for actor in actors:
        img = cfg.character_base_images.get(actor)
        if img and img.strip():
            return img.strip()
    return cfg.style_base_image.strip()


def cloud_renderer(cfg: CloudConfig) -> Renderer:
    """绑定云端配置，返回 Renderer 闭包。出图失败向上抛，由调用方转错误文本。

    有角色底图（gpt-image 锁一致性）→ 走 generate_with_images 图生图；否则纯文生图。
    """
    def render(req: SceneRequest) -> str:
        base_image = _pick_base_image(cfg, list(req.actors))
        proxy_kw = {"proxy": cfg.proxy} if cfg.proxy else {}
        if base_image:
            return image_gen.generate_with_images(
                cfg.base_url, cfg.api_key, cfg.model, req.prompt,
                images=[base_image], size=cfg.size, quality=cfg.quality, **proxy_kw)
        return image_gen.generate(
            cfg.base_url, cfg.api_key, cfg.model, req.prompt,
            size=cfg.size, quality=cfg.quality, **proxy_kw)
    return render


def _view_url(base: str, ref: dict[str, str]) -> str:
    """把 fetch_result 的图片引用（filename/subfolder/type）拼成 ComfyUI /view 直链。"""
    qs = urlencode({
        "filename": ref.get("filename", ""),
        "subfolder": ref.get("subfolder", ""),
        "type": ref.get("type", "output"),
    })
    return f"{base.rstrip('/')}/view?{qs}"


def comfy_renderer(
    cfg: ComfyConfig,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Renderer:
    """绑定 ComfyUI 配置，返回 Renderer 闭包：提交模板 → 轮询取图 → 返回 /view 直链。

    sleep/now 可注入以便单测不真等待。任务丢失（not_found）或超时抛异常。
    """
    def render(req: SceneRequest) -> str:
        res = workflow_submission.submit_template(
            cfg.template_id, cfg.values, req.prompt, cfg.url)
        prompt_id = str(res.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("ComfyUI 未返回 prompt_id，无法取图")
        deadline = now() + cfg.poll_timeout
        while now() < deadline:
            r = comfyui_client.fetch_result(cfg.url, prompt_id)
            status = r.get("status")
            if status == "completed":
                images = r.get("images") or []
                if not images:
                    raise RuntimeError("ComfyUI 工作流完成但无图片产物")
                return _view_url(cfg.url, images[0])
            if status == "not_found":
                raise RuntimeError("ComfyUI 任务丢失（可能已重启）")
            sleep(cfg.poll_interval)
        raise TimeoutError(f"ComfyUI 取图超时（prompt_id={prompt_id}）")
    return render
