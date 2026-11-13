"""本地导入媒体的元信息提取（PNG/EXIF/视频容器 → 提示词与生成参数）。

单一属主：所有「从文件读生成参数」的解析规则集中在此。路由与入库服务只调
`extract_image_metadata` / `extract_video_metadata`，不得各写一份。

覆盖三类来源（按优先级）：
1. ComfyUI：PNG 文本块的 `prompt`（API 图）——CLIPTextEncode 文本、KSampler 参数、
   UNETLoader/CheckpointLoader 模型、LoraLoader、EmptyLatentImage 尺寸；
2. A1111/Forge：PNG `parameters` 或 JPEG EXIF UserComment 的 `Negative prompt: … Steps: …`；
3. 纯文本/exif 描述以及视频容器标签（comment/description 等，best-effort）。

解析只读不写：返回 dict 由入库服务决定落哪些字段；任何字段缺失一律空值，不抛异常
（导入一张无元数据的图仍应成功）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"}

_LORA_TAG = re.compile(r"<lora:([^:>]+)(?::([^>]*))?>", re.IGNORECASE)
_SAMPLE_CLASSES = ("KSampler", "SamplerCustom")


def media_kind(suffix: str) -> str:
    """扩展名 → `image` / `video` / 空串（不支持）。"""
    value = (suffix or "").strip().lower()
    if value and not value.startswith("."):
        value = "." + value
    if value in IMAGE_SUFFIXES:
        return "image"
    if value in VIDEO_SUFFIXES:
        return "video"
    return ""


def _to_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _normalize_params(raw: dict[str, str]) -> dict[str, Any]:
    """A1111 参数行 → 归一键（只留可展示的生成参数，未知键丢弃）。"""
    out: dict[str, Any] = {}
    steps = _to_int(raw.get("Steps"))
    if steps is not None:
        out["steps"] = steps
    sampler = (raw.get("Sampler") or "").strip()
    if sampler:
        out["sampler"] = sampler
    scheduler = (raw.get("Schedule type") or raw.get("Scheduler") or "").strip()
    if scheduler:
        out["scheduler"] = scheduler
    cfg = _to_float(raw.get("CFG scale"))
    if cfg is not None:
        out["cfg"] = cfg
    seed = _to_int(raw.get("Seed"))
    if seed is not None:
        out["seed"] = seed
    size = (raw.get("Size") or "").strip()
    match = re.match(r"(\d+)\s*[x×]\s*(\d+)", size)
    if match:
        out["width"] = int(match.group(1))
        out["height"] = int(match.group(2))
    model = (raw.get("Model") or "").strip()
    if model:
        out["model"] = model
    model_hash = (raw.get("Model hash") or "").strip()
    if model_hash:
        out["modelHash"] = model_hash
    denoise = _to_float(raw.get("Denoising strength"))
    if denoise is not None:
        out["denoise"] = denoise
    vae = (raw.get("VAE") or "").strip()
    if vae:
        out["vae"] = vae
    clip_skip = _to_int(raw.get("Clip skip"))
    if clip_skip is not None:
        out["clipSkip"] = clip_skip
    return out


def parse_a1111_parameters(text: str) -> dict[str, Any]:
    """A1111/Forge 的 `parameters` 文本 → {prompt, negativePrompt, params}。"""
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return {}
    lines = raw.split("\n")
    params_at = -1
    params_line = ""
    for index in range(len(lines) - 1, -1, -1):
        if re.match(r"^Steps:\s*\d", lines[index].strip()):
            params_at = index
            params_line = lines[index].strip()
            break
    head = "\n".join(lines[:params_at]).strip() if params_at >= 0 else raw
    negative = ""
    marker = re.search(r"(?:^|\n)\s*Negative prompt:\s*", head, re.IGNORECASE)
    if marker:
        negative = head[marker.end():].strip()
        prompt = head[:marker.start()].strip()
    else:
        prompt = head
    raw_params: dict[str, str] = {}
    for chunk in re.split(r",\s*(?=[A-Za-z][A-Za-z0-9 _/.\-]*:)", params_line):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        raw_params[key.strip()] = value.strip()
    params = _normalize_params(raw_params)
    loras = [name.strip() for name, _weight in _LORA_TAG.findall(prompt) if name.strip()]
    if loras:
        params["loraNames"] = loras
    return {"source": "a1111", "prompt": prompt, "negativePrompt": negative, "params": params}


def _node_text(nodes: dict[str, dict], value: Any, depth: int = 0) -> str:
    """沿连线找 CLIPTextEncode 文本（只跟 conditioning/text 类输入，避免误入 CLIPLoader）。"""
    if depth > 8:
        return ""
    if isinstance(value, str):
        return value
    if not (isinstance(value, list) and len(value) == 2):
        return ""
    node = nodes.get(str(value[0]))
    if not node:
        return ""
    inputs = node.get("inputs") or {}
    for key in ("text", "string", "value"):
        candidate = inputs.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    for key in ("conditioning", "text", "string"):
        candidate = inputs.get(key)
        if isinstance(candidate, list):
            found = _node_text(nodes, candidate, depth + 1)
            if found.strip():
                return found
    return ""


def _resolve_scalar(nodes: dict[str, dict], value: Any, depth: int = 0) -> Any:
    """把「值或指向值节点的连线」解成标量（Seed (rgthree) 这类值节点）。"""
    if depth > 6:
        return None
    if isinstance(value, (int, float, str)):
        return value
    if not (isinstance(value, list) and len(value) == 2):
        return None
    node = nodes.get(str(value[0])) or {}
    inputs = node.get("inputs") or {}
    for key in ("seed", "noise_seed", "value", "int", "float", "number", "text", "string"):
        candidate = inputs.get(key)
        if isinstance(candidate, (int, float, str)):
            return candidate
        if isinstance(candidate, list):
            resolved = _resolve_scalar(nodes, candidate, depth + 1)
            if resolved is not None:
                return resolved
    return None


def _node_size(nodes: dict[str, dict]) -> tuple[int | None, int | None]:
    for node in nodes.values():
        class_type = str(node.get("class_type") or "")
        if "Empty" not in class_type or "Latent" not in class_type:
            continue
        inputs = node.get("inputs") or {}
        width = _to_int(_resolve_scalar(nodes, inputs.get("width")))
        height = _to_int(_resolve_scalar(nodes, inputs.get("height")))
        if width and height:
            return width, height
    return None, None


def parse_comfyui_graph(payload: str | dict) -> dict[str, Any]:
    """ComfyUI PNG `prompt`（API 图 JSON）→ {prompt, negativePrompt, params}。"""
    try:
        graph = json.loads(payload) if isinstance(payload, str) else payload
    except (TypeError, ValueError):
        return {}
    if not isinstance(graph, dict):
        return {}
    nodes = {str(key): value for key, value in graph.items() if isinstance(value, dict)}
    if not nodes:
        return {}

    samplers = [
        (node_id, node) for node_id, node in nodes.items()
        if any(str(node.get("class_type") or "").startswith(prefix) for prefix in _SAMPLE_CLASSES)
    ]
    params: dict[str, Any] = {}
    positive = ""
    negative = ""
    if samplers:
        # 多采样器（两级放大）取第一个：它就是首发生成参数，后续是高清化重采样。
        _, sampler = samplers[0]
        inputs = sampler.get("inputs") or {}
        positive = _node_text(nodes, inputs.get("positive")).strip()
        negative = _node_text(nodes, inputs.get("negative")).strip()
        for key, norm in (("steps", "steps"), ("cfg", "cfg"), ("sampler_name", "sampler"),
                          ("scheduler", "scheduler"), ("denoise", "denoise")):
            value = _resolve_scalar(nodes, inputs.get(key))
            if value not in (None, ""):
                if norm in ("steps",):
                    parsed = _to_int(value)
                elif norm in ("cfg", "denoise"):
                    parsed = _to_float(value)
                else:
                    parsed = str(value).strip()
                if parsed not in (None, ""):
                    params[norm] = parsed
        seed = _to_int(_resolve_scalar(nodes, inputs.get("noise_seed", inputs.get("seed"))))
        if seed is not None:
            params["seed"] = seed
    else:
        # 无采样器（某些工作流只存编码节点）：按标题提示分正负，取第一个当正面。
        texts = [(node_id, node) for node_id, node in nodes.items()
                 if "CLIPTextEncode" in str(node.get("class_type") or "")]
        for _, node in texts:
            text = str((node.get("inputs") or {}).get("text") or "").strip()
            if not text:
                continue
            title = str((node.get("_meta") or {}).get("title") or "")
            if re.search(r"negative|负面|反向", title, re.IGNORECASE):
                negative = negative or text
            elif not positive:
                positive = text

    model = ""
    loras: list[str] = []
    for node in nodes.values():
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") or {}
        if not model and "CheckpointLoader" in class_type and inputs.get("ckpt_name"):
            model = str(inputs["ckpt_name"])
        if not model and "UNETLoader" in class_type and inputs.get("unet_name"):
            model = str(inputs["unet_name"])
        if "LoraLoader" in class_type and inputs.get("lora_name"):
            name = str(inputs["lora_name"]).strip()
            if name and name not in loras:
                loras.append(name)
    if model:
        params["model"] = model
    if loras:
        params["loraNames"] = loras
    width, height = _node_size(nodes)
    if width and height:
        params["width"] = width
        params["height"] = height
    return {"source": "comfyui", "prompt": positive, "negativePrompt": negative, "params": params}


def parse_metadata_text(text: str) -> dict[str, Any]:
    """任意元信息文本 → 解析结果；识别不出格式时原样当提示词。"""
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("{") and '"class_type"' in raw:
        parsed = parse_comfyui_graph(raw)
        if parsed:
            return parsed
    if "Steps:" in raw or "Negative prompt:" in raw:
        return parse_a1111_parameters(raw)
    return {"source": "text", "prompt": raw, "negativePrompt": "", "params": {}}


def _decode_exif_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for prefix, encoding in ((b"ASCII\x00\x00\x00", "ascii"), (b"UNICODE\x00", "utf-16")):
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        else:
            encoding = "utf-8"
        try:
            return value.decode(encoding, "replace").strip("\x00").strip()
        except LookupError:
            return value.decode("utf-8", "replace").strip("\x00").strip()
    return str(value).strip()


def _exif_comment(exif: Any) -> str:
    if not exif:
        return ""
    # 37510=UserComment, 270=ImageDescription, 40092=XPComment
    for tag in (37510, 270, 40092):
        try:
            text = _decode_exif_text(exif.get(tag))
        except Exception:  # noqa: BLE001 - EXIF 结构因相机/软件而异，读不到就当没有
            text = ""
        if text:
            return text
    return ""


def extract_image_metadata(path: str | Path) -> dict[str, Any]:
    """读图片文件 → {kind, format, width, height, source, prompt, negativePrompt, params}。"""
    from PIL import Image  # 局部导入：只有导入链需要 Pillow

    target = Path(path)
    result: dict[str, Any] = {
        "kind": "image", "format": target.suffix.lstrip(".").upper() or "IMAGE",
        "width": 0, "height": 0, "source": "", "prompt": "", "negativePrompt": "", "params": {},
    }
    texts: dict[str, str] = {}
    exif_text = ""
    try:
        with Image.open(target) as image:
            result["width"], result["height"] = image.size
            result["format"] = (image.format or result["format"]).upper()
            info = dict(image.info or {})
            for wanted in ("parameters", "prompt", "workflow", "comment", "description", "software"):
                for actual in (wanted, wanted.capitalize(), wanted.upper()):
                    value = info.get(actual)
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", "replace")
                    if isinstance(value, str) and value.strip():
                        texts[wanted] = value
                        break
            if result["format"] in ("JPEG", "JPG", "WEBP", "TIFF", "MPO"):
                try:
                    exif_text = _exif_comment(image.getexif())
                except Exception:  # noqa: BLE001 - 损坏 EXIF 不阻断导入
                    exif_text = ""
    except Exception:  # noqa: BLE001 - 图片损坏时仍允许入库（无元信息）
        return result

    raw_text = texts.get("prompt") or texts.get("parameters") or ""
    parsed: dict[str, Any] = {}
    if raw_text:
        parsed = parse_metadata_text(raw_text)
    elif exif_text:
        parsed = parse_metadata_text(exif_text)
    elif texts.get("comment") or texts.get("description"):
        parsed = parse_metadata_text(texts.get("comment") or texts.get("description") or "")
    elif texts.get("workflow"):
        # UI 版工作流图（nodes/links）暂不反解：标记来源，提示词留空。
        parsed = {"source": "comfyui-workflow"}

    if parsed:
        result["source"] = str(parsed.get("source") or "")
        result["prompt"] = str(parsed.get("prompt") or "")
        result["negativePrompt"] = str(parsed.get("negativePrompt") or "")
        params = parsed.get("params")
        result["params"] = dict(params) if isinstance(params, dict) else {}
    # 采样器给的 latent 尺寸与实际成品尺寸可能不同（高清化）⇒ 以文件实际像素为准。
    if result["width"] and result["height"]:
        result["params"]["width"] = result["width"]
        result["params"]["height"] = result["height"]
    return result


def _find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        from app.services.audio_merge import find_ffmpeg

        return find_ffmpeg()
    except Exception:  # noqa: BLE001 - 没有 ffmpeg 时视频元信息降级为空
        return None


def extract_video_metadata(path: str | Path) -> dict[str, Any]:
    """读视频容器 → {kind, format, width, height, duration, source, prompt, params}。

    容器标签的 comment/description 若承载 A1111/ComfyUI 文本，按文本规则解析；
    否则把它们当普通提示词（很多工具把 prompt 写在 comment 里）。
    """
    target = Path(path)
    result: dict[str, Any] = {
        "kind": "video", "format": target.suffix.lstrip(".").upper() or "VIDEO",
        "width": 0, "height": 0, "duration": 0.0,
        "source": "", "prompt": "", "negativePrompt": "", "params": {},
    }
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return result
    try:
        proc = subprocess.run(  # noqa: S603 - 本机固定二进制 + 路径参数，无 shell
            [ffmpeg, "-hide_banner", "-i", str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
        )
    except (OSError, subprocess.SubprocessError):
        return result
    stderr = proc.stderr or ""
    duration = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", stderr)
    if duration:
        result["duration"] = round(
            int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3)), 2)
    video_stream = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", stderr)
    if video_stream:
        result["width"] = int(video_stream.group(1))
        result["height"] = int(video_stream.group(2))
    fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
    tags: dict[str, str] = {}
    in_metadata = False
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped == "Metadata:":
            in_metadata = True
            continue
        if in_metadata and (stripped.startswith("Duration:") or stripped.startswith("Stream")):
            in_metadata = False
        if not in_metadata or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip().lower()
        if key in ("encoder", "comment", "description", "title", "artist", "parameters", "prompt"):
            tags[key] = value.strip()

    params: dict[str, Any] = {"durationSeconds": result["duration"]}
    if result["width"] and result["height"]:
        params["width"] = result["width"]
        params["height"] = result["height"]
    if fps:
        params["fps"] = _to_float(fps.group(1))
    if tags.get("encoder"):
        params["encoder"] = tags["encoder"]
    carried = tags.get("parameters") or tags.get("prompt") or tags.get("comment") or tags.get("description") or ""
    if carried:
        parsed = parse_metadata_text(carried)
        if parsed:
            result["source"] = str(parsed.get("source") or "")
            result["prompt"] = str(parsed.get("prompt") or "")
            result["negativePrompt"] = str(parsed.get("negativePrompt") or "")
            extra = parsed.get("params")
            if isinstance(extra, dict):
                params.update(extra)
    if not result["source"] and tags:
        result["source"] = "container"
    if tags.get("title"):
        params["title"] = tags["title"]
    result["params"] = params
    return result
