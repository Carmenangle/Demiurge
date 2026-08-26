"""视频提示词编译 + 上交给视频模型的参数组装（V1.5/V1.6）。

参照 MiniMax H3 提示词模板规律（docs 外链 `H3-提示词模版规律.md`）：
H3 高服从字面执行，本质是「把成片逐秒、逐镜头、逐像素规定死」。
两套模板语义截然不同，分开建模，不共用：

- climax（高潮点·动作代入）：精简版。单一「动作瞬间」动态化，加代入感，
  不是整段叙事——禁止套时间分镜。
- firstlast（首尾帧·剧情影片）：完整七段式。首帧→演变→尾帧覆盖整个桥段
  起承转合，含时间分镜；首帧带「上楼层尾帧」衔接上下文（转场判断融入生成）。

本模块是**纯函数**（不调 LLM / 不调网络 / 不提交视频），用于：
1. 单测覆盖两套模板的区块完整性；
2. dry-run 把「最终要交给视频模型的参数」完整组装出来供人核对。

审计修正（docs/PLAN-VIDEO-FIRSTLAST.md）：
- R2 缺图守卫：firstlast 缺图时参考绑定/时间分镜诚实标注「无参考图，以文字为准」，
  不产出「引用不存在的图」的提示词。
- R3 元信息三件套：模型名（透传，不硬编码）+ 时长 + 画幅（size 派生）。
- R6 音频：无逐字对白时只写纯音乐结构，不写「台词=逐字」诱导幻觉。
- R7 负面约束：按分隔词拆项去重。
"""
from __future__ import annotations

import math
import re
from typing import Any

from app.services import prompt_clean

# 元信息：默认时长（秒）。preset videoDurationHint 可覆盖。
_DEFAULT_DURATION = 15

# 首尾帧时间分镜的最小三段式（起-承-合），节奏名可被调用方覆盖。
_FIRSTLAST_BEATS = ("开场定格", "主体演变", "收尾定格")


def _aspect_from_size(size: str) -> str:
    """从 size 派生画幅（1280x720 → 16:9；已含 ':' 则原样）。R3 补 H3 元信息画幅。"""
    s = (size or "").strip().lower().replace("*", "x").replace("×", "x").replace("：", ":")
    if not s:
        return ""
    if "x" not in s and ":" in s:
        return s  # 已是 16:9 这类比例
    m = re.match(r"^(\d+)\s*x\s*(\d+)$", s)
    if not m:
        return ""
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        return ""
    g = math.gcd(w, h)
    return f"{w // g}:{h // g}"


def _style_declaration(style_prefix: str, rating: str) -> str:
    """② 风格声明 = 类型 + 美学流派 + 配色（来自 preset 风格前缀，原样）。"""
    prefix = (style_prefix or "").strip()
    if not prefix:
        return ""
    # rating 只作提示，不硬塞进风格句（风格前缀已含定调）。
    return prefix


def _subject_scene(spec: dict[str, Any]) -> str:
    """④ 主体/场景：可还原的视觉细节（外貌/衣着/场景），不堆形容词。

    来源 scene_spec 的 appearance / wardrobe / locale，顺序拼接。
    """
    parts: list[str] = []
    for key in ("appearance", "wardrobe", "locale"):
        val = str(spec.get(key) or "").strip()
        if val and val not in parts:
            parts.append(val)
    return "，".join(parts)


def _negative(spec: dict[str, Any], preset_negative: str) -> str:
    """⑦ 负面约束：preset + scene_spec，按分隔词拆项保序去重（R7）。"""
    raw: list[str] = []
    for src in (preset_negative, str(spec.get("negative_prompt") or "")):
        if not src:
            continue
        for item in re.split(r"[；;/／]+", str(src)):
            item = item.strip()
            if item:
                raw.append(item)
    seen: set[str] = set()
    items: list[str] = []
    for it in raw:
        key = it.lower()
        if key not in seen:
            seen.add(key)
            items.append(it)
    return "；".join(items)


def _reference_binding_climax(first_frame_desc: str, actors: list[str]) -> str:
    """③ 参考绑定（climax 单图）：图片1 = 高潮动作画面，锁身份。"""
    desc = (first_frame_desc or "").strip() or "高潮动作画面"
    lines = [f"图片1={desc}（唯一参考画面，作为准确起始帧）"]
    if actors:
        lines.append(f"保持 {('、'.join(actors))} 的身份、脸部、服装、发型、造型完全一致")
    return "；".join(lines)


def _reference_binding_firstlast(first_frame_desc: str, last_frame_desc: str,
                                 actors: list[str],
                                 has_first: bool = True, has_last: bool = True) -> str:
    """③ 参考绑定（firstlast 双图）：图片1=首帧、图片2=尾帧，职责钉死。

    缺图时诚实标注「无参考图，以文字为准」，不假装有图（R2）。
    """
    f = (first_frame_desc or "").strip() or "楼层开头画面"
    l = (last_frame_desc or "").strip() or "楼层结尾画面"
    if has_first:
        lines = [f"图片1={f}（首帧/起始画面）"]
    else:
        lines = [f"首帧（无参考图，以文字为准）：{f}"]
    if has_last:
        lines.append(f"图片2={l}（尾帧/目标画面）")
    else:
        lines.append(f"尾帧（无参考图，以文字为准）：{l}")
    if actors:
        lines.append(f"保持 {('、'.join(actors))} 的身份、脸部、服装、发型、造型完全一致")
    return "；".join(lines)


def _meta(duration_hint: int, camera: str, model_name: str = "", size: str = "") -> str:
    """① 元信息：模型名（透传不硬编码）+ 时长 + 画幅 + 运镜（R3 补三件套）。"""
    dur = int(duration_hint) if duration_hint else _DEFAULT_DURATION
    parts: list[str] = []
    if model_name and str(model_name).strip():
        parts.append(str(model_name).strip())
    parts.append(f"{dur} seconds")
    aspect = _aspect_from_size(size)
    if aspect:
        parts.append(aspect)
    if camera and str(camera).strip():
        parts.append(f"镜头运动={str(camera).strip()}")
    return "，".join(parts)


def _time_segments(spec: dict[str, Any], duration_hint: int,
                   first_frame_desc: str, last_frame_desc: str,
                   prev_tail_desc: str,
                   has_first: bool = True, has_last: bool = True) -> str:
    """⑤ 时间分镜（firstlast 核心）：首帧→演变→尾帧切 3 段，每段四要素。

    - 首帧段带「上楼层尾帧」衔接上下文（转场判断融入生成，不单独判定）。
    - 缺图时首/尾帧段用文字描述开场/收尾，不引用不存在的图（R2）。
    """
    dur = int(duration_hint) if duration_hint else _DEFAULT_DURATION
    narrative = str(spec.get("narrative") or "").strip()
    camera = str(spec.get("camera") or "").strip()
    composition = str(spec.get("composition") or "").strip()
    motion = spec.get("motion") if isinstance(spec.get("motion"), (int, float)) else 0

    # 三段均分时长
    a, b = dur // 3, dur // 3
    c = dur - a - b
    bounds = [(0, a), (a, a + b), (a + b, dur)]

    # 运镜描述：camera 优先，其次 composition；缺省按 motion 给保守运镜。
    cam_hint = camera or composition
    if not cam_hint:
        cam_hint = ("低机位快速丝滑运镜" if int(motion) >= 2 else "极缓推进")

    f_open = (first_frame_desc or "楼层开头画面").strip()
    l_close = (last_frame_desc or "楼层结尾画面").strip()

    # 首帧段：有图定格首帧 / 无图文字开场；带衔接上下文。
    open_content = f"定格于首帧画面：{f_open}" if has_first else f"以文字首帧开场：{f_open}"
    if prev_tail_desc:
        open_extra = f"承接上一镜头尾帧（{prev_tail_desc}）自然延续，机位/光影/人物状态连贯，无突兀跳切"
    else:
        open_extra = "作为本段独立开场镜头"

    body_action = narrative or "主体动作按剧情自然演变"

    # 尾帧段：有图定格尾帧 / 无图文字收尾；收束为下一镜头留过渡点。
    close_content = f"收尾定格于尾帧画面：{l_close}" if has_last else f"以文字尾帧收尾：{l_close}"
    close_extra = "动作自然收束、神态定格，为下一镜头衔接留出自然过渡点"

    segments = [
        (open_content, open_extra),
        (f"{cam_hint}；{body_action}", "与节拍/重音同步" if int(motion) >= 2 else ""),
        (close_content, close_extra),
    ]

    lines: list[str] = []
    identity = "；人物身份和五官不能发生变化"
    beats = list(_FIRSTLAST_BEATS)
    for idx, ((content, extra), (start, end)) in enumerate(zip(segments, bounds)):
        tail = f"（{extra}）" if extra else ""
        lines.append(f"[{start}s–{end}s｜{beats[idx]}]：{content}{tail}{identity}")
    return "\n".join(lines)


def _audio_hint(spec: dict[str, Any], audio_lines: list | None = None) -> str:
    """⑥ 音频：无逐字对白时只写纯音乐结构，不写「台词=逐字」诱导幻觉（R6）。

    audio_lines 形如 [{speaker, text}, ...]（comfy_audio 对白），有逐字台词才列。
    """
    music = "音乐=按本集风格铺底；音效=环境声；同步=视觉事件卡拍"
    speakers: list[str] = []
    for ln in (audio_lines or []):
        if isinstance(ln, dict):
            text = str(ln.get("text") or "").strip()
            speaker = str(ln.get("speaker") or "").strip()
        else:
            text = str(ln).strip()
            speaker = ""
        if text:
            speakers.append(f"{speaker}：{text}" if speaker else text)
    if speakers:
        return f"音乐=按本集风格铺底；台词={('；'.join(speakers))}；同步=动作卡拍"
    return music


def _climax_action(spec: dict[str, Any]) -> tuple[str, str, str]:
    """高潮动作化延伸：按 motion 强度给「运镜 + 特效 + 节拍」（H3 高潮段手法）。"""
    narrative = str(spec.get("narrative") or "").strip() or "动作瞬间"
    motion = int(spec.get("motion") or 0)
    if motion >= 3:
        cam = "低机位快速丝滑运镜+高速推近骤停"
        fx = "能量爆发式特效，画面错位与鼓点精准同步"
    elif motion >= 2:
        cam = "绕主体快速运镜"
        fx = "强化动作张力，与节拍/重音同步"
    else:
        cam = "极缓推进"
        fx = "轻微运镜强化代入感"
    return narrative, cam, fx


def compile_climax_video_prompt(
    spec: dict[str, Any],
    *,
    style_prefix: str = "",
    negative: str = "",
    duration_hint: int = 0,
    camera: str = "",
    model_name: str = "",
    size: str = "",
    first_frame_desc: str = "",
) -> str:
    """高潮点·动作代入：精简版提示词（不是剧情影片，禁止时间分镜）。

    区块：① 元信息 ② 风格 ③ 单图绑定 ④ 主体/场景 ⑦ 负面约束
    + 一个「动作瞬间 + 运镜 + 特效/节拍」短句。
    """
    blocks: list[str] = []
    blocks.append(f"使用视频模型生成，{_meta(duration_hint, camera, model_name, size)}。")
    style = _style_declaration(style_prefix, str(spec.get("rating") or ""))
    if style:
        blocks.append(f"[风格]：{style}")
    blocks.append(f"[参考绑定]：{_reference_binding_climax(first_frame_desc, spec.get('actors') or [])}")
    subject = _subject_scene(spec)
    if subject:
        blocks.append(f"[主体/场景]：{subject}")
    narrative, cam, fx = _climax_action(spec)
    blocks.append(f"[动作]：{narrative}；{cam}；{fx}。")
    neg = _negative(spec, negative)
    if neg:
        blocks.append(f"[负面约束]：{neg}")
    return "\n\n".join(blocks)


def compile_firstlast_video_prompt(
    spec: dict[str, Any],
    *,
    style_prefix: str = "",
    negative: str = "",
    duration_hint: int = 0,
    camera: str = "",
    model_name: str = "",
    size: str = "",
    first_frame_desc: str = "",
    last_frame_desc: str = "",
    prev_tail_desc: str = "",
    has_first: bool = True,
    has_last: bool = True,
    audio_lines: list | None = None,
) -> str:
    """首尾帧·剧情影片：完整七段式（H3 骨架）。

    区块：① 元信息 ② 风格 ③ 双图绑定 ④ 主体/场景 ⑤ 时间分镜 ⑥ 音频 ⑦ 负面约束。
    has_first/has_last 标识是否真的提供了首/尾帧图（R2 缺图守卫）。
    """
    blocks: list[str] = []
    blocks.append(f"使用视频模型生成，{_meta(duration_hint, camera, model_name, size)}。")
    style = _style_declaration(style_prefix, str(spec.get("rating") or ""))
    if style:
        blocks.append(f"[风格]：{style}")
    blocks.append(
        f"[参考绑定]：{_reference_binding_firstlast(first_frame_desc, last_frame_desc, spec.get('actors') or [], has_first, has_last)}"
    )
    subject = _subject_scene(spec)
    if subject:
        blocks.append(f"[主体/场景]：{subject}")
    blocks.append(
        "[时间分镜]：\n"
        + _time_segments(spec, duration_hint, first_frame_desc, last_frame_desc,
                         prev_tail_desc, has_first, has_last)
    )
    blocks.append(f"[音频]：{_audio_hint(spec, audio_lines)}")
    neg = _negative(spec, negative)
    if neg:
        blocks.append(f"[负面约束]：{neg}")
    return "\n\n".join(blocks)


def _clean_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """对 scene_spec 文本字段做破甲标记还原（复用共享清洗规则 prompt_clean）。

    图像生成的防拦截是两层：① restore_jailbreak 还原 @()@ 破甲标记（纯函数，本层）；
    ② _apply_regex(IMAGE_PROMPT) 用户正则清洗（接线层，需 ctx，不在纯函数边界内）。

    本模块只做第①层兜底，统一走 prompt_clean.clean_spec_text_fields，
    避免 @(x)@ 残留进视频提示词。
    """
    return prompt_clean.clean_spec_text_fields(spec)


def build_video_request(
    *,
    mode: str,
    spec: dict[str, Any],
    video_config: dict[str, Any],
    preset: dict[str, Any] | None = None,
    first_frame: str = "",
    last_frame: str = "",
    first_frame_desc: str = "",
    last_frame_desc: str = "",
    prev_tail_desc: str = "",
    audio_lines: list | None = None,
) -> dict[str, Any]:
    """组装「上交给视频模型的参数」（dry-run，不提交）。

    对齐 video_gen 的 generate / generate_with_images 签名：
    - 文生：JSON {model, prompt, size}
    - 图生：multipart image[] + {model, prompt, size}

    关键区分（两层，必须一致）：
    - 图地址（first_frame / last_frame）→ 进 images[] 数组，按顺序对应「图片1/图片2」。
    - 图职责描述（first_frame_desc / last_frame_desc）→ 进 prompt 的参考绑定文字
      （H3 要求写「图片1=首帧」这类职责，不是地址）。
    - 缺图时产出 warnings + 诚实降级措辞（R2）。
    """
    preset = preset or {}
    spec = _clean_spec(spec or {})
    style_prefix = str(preset.get("stylePrefix") or preset.get("style_template") or "")
    negative = str(preset.get("negativePrompt") or "")
    duration_hint = int(preset.get("videoDurationHint") or 0)
    camera = str(preset.get("videoCamera") or "")

    base_url = str(video_config.get("base_url") or "").strip().rstrip("/")
    model = str(video_config.get("model") or video_config.get("modelName") or "")
    proxy = str(video_config.get("proxy") or "")
    # 视频默认 16:9（R9：不再沿用图片的 1024x1024）
    size = str(video_config.get("size") or "1280x720")

    if mode == "firstlast":
        has_first = bool(first_frame)
        has_last = bool(last_frame)
        prompt = compile_firstlast_video_prompt(
            spec, style_prefix=style_prefix, negative=negative,
            duration_hint=duration_hint, camera=camera, model_name=model, size=size,
            first_frame_desc=first_frame_desc or "首帧/楼层开头画面",
            last_frame_desc=last_frame_desc or "尾帧/楼层结尾画面",
            prev_tail_desc=prev_tail_desc,
            has_first=has_first, has_last=has_last, audio_lines=audio_lines,
        )
        images = [img for img in (first_frame, last_frame) if img]
        binding = {
            "图片1": _binding_entry(first_frame_desc or "首帧/楼层开头画面", first_frame),
            "图片2": _binding_entry(last_frame_desc or "尾帧/楼层结尾画面", last_frame),
        }
    else:
        prompt = compile_climax_video_prompt(
            spec, style_prefix=style_prefix, negative=negative,
            duration_hint=duration_hint, camera=camera, model_name=model, size=size,
            first_frame_desc=first_frame_desc or "高潮动作画面",
        )
        images = [first_frame] if first_frame else []
        binding = {
            "图片1": _binding_entry(first_frame_desc or "高潮动作画面", first_frame),
        }

    warnings = _missing_frame_warnings(mode, first_frame, last_frame)

    return {
        "mode": mode,
        "submit": {
            "endpoint": base_url,               # 用户填的原样（代码不猜版本/单复数）
            "model": model,
            "prompt": prompt,                    # 编译出的视频提示词（含参考绑定职责文字）
            "images": images,                    # 参考图地址（climax 1 张 / firstlast 2 张）
            "size": size,
            "proxy": proxy or None,
            "content_type": "multipart/form-data" if images else "application/json",
        },
        "reference_binding": binding,
        "prompt_sections": _section_names(mode),
        "warnings": warnings,
    }


def _missing_frame_warnings(mode: str, first_frame: str, last_frame: str) -> list[str]:
    """缺图守卫（R2）：把缺失帧的后果显式列给调用方/人核对。"""
    warnings: list[str] = []
    if mode == "firstlast":
        if not first_frame:
            warnings.append("缺首帧图：首帧将以文字描述开场（退化为文生/待上游补图）")
        if not last_frame:
            warnings.append("缺尾帧图：尾帧将以文字描述收尾")
        if not first_frame and not last_frame:
            warnings.append("首尾帧图均缺：将退化为纯文生视频（无参考图）")
    else:
        if not first_frame:
            warnings.append("缺高潮参考图：将以文字描述生成动作画面")
    return warnings


def _binding_entry(role: str, source: str) -> str:
    """参考绑定条目：职责 → 图地址（供人核对两层是否对应）。"""
    if source:
        return f"{role} → {source}"
    return f"{role} → （未提供图地址，将退化为文生或由上游补图）"


def _section_names(mode: str) -> list[str]:
    """提示词含哪些区块（供人核对完整性）。"""
    if mode == "firstlast":
        return ["①元信息", "②风格", "③参考绑定", "④主体/场景", "⑤时间分镜", "⑥音频", "⑦负面约束"]
    return ["①元信息", "②风格", "③参考绑定", "④主体/场景", "动作瞬间", "⑦负面约束"]
