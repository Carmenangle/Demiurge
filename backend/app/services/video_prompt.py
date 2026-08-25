"""视频提示词编译 + 上交给视频模型的参数组装（V1.5/V1.6）。

参照 MiniMax H3 提示词模板规律（docs 外链 `H3-提示词模版规律.md`）：
H3 高服从字面执行，本质是「把成片逐秒、逐镜头、逐像素规定死」。
两套模板语义截然不同，分开建模，不共用：

- climax（高潮点·动作代入）：精简版。单一「动作瞬间」动态化，加代入感，
  不是整段叙事——禁止套时间分镜。
- firstlast（首尾帧·剧情影片）：完整七段式。首帧→演变→尾帧覆盖整个桥段
  起承转合，含时间分镜。

本模块是**纯函数**（不调 LLM / 不调网络 / 不提交视频），用于：
1. 单测覆盖两套模板的区块完整性；
2. dry-run 把「最终要交给视频模型的参数」完整组装出来供人核对。
"""
from __future__ import annotations

from typing import Any

# H3 元信息：默认时长（秒）。preset videoDurationHint 可覆盖。
_DEFAULT_DURATION = 15

# 首尾帧时间分镜的最小三段式（起-承-合），节奏名可被调用方覆盖。
_FIRSTLAST_BEATS = ("开场定格", "主体演变", "收尾定格")


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
    """⑦ 负面约束：preset 独立负面提示词 + scene_spec 的 negative_prompt。"""
    items = [s.strip() for s in (preset_negative, str(spec.get("negative_prompt") or ""))
             if s and s.strip()]
    return "；".join(items)


def _reference_binding_climax(first_frame_desc: str, actors: list[str]) -> str:
    """③ 参考绑定（climax 单图）：图片1 = 高潮动作画面，锁身份。"""
    desc = (first_frame_desc or "").strip() or "高潮动作画面"
    lines = [f"图片1={desc}（唯一参考画面，作为准确起始帧）"]
    if actors:
        lines.append(f"保持 {('、'.join(actors))} 的身份、脸部、服装、发型、造型完全一致")
    return "；".join(lines)


def _reference_binding_firstlast(first_frame_desc: str, last_frame_desc: str,
                                 actors: list[str]) -> str:
    """③ 参考绑定（firstlast 双图）：图片1=首帧、图片2=尾帧，职责钉死。"""
    f = (first_frame_desc or "").strip() or "楼层开头画面"
    l = (last_frame_desc or "").strip() or "楼层结尾画面"
    lines = [f"图片1={f}（首帧/起始画面）", f"图片2={l}（尾帧/目标画面）"]
    if actors:
        lines.append(f"保持 {('、'.join(actors))} 的身份、脸部、服装、发型、造型完全一致")
    return "；".join(lines)


def _meta(duration_hint: int, camera: str) -> str:
    """① 元信息：时长 + 运镜（preset videoDurationHint / videoCamera）。"""
    dur = int(duration_hint) if duration_hint else _DEFAULT_DURATION
    seg = f"{dur} seconds"
    if camera and str(camera).strip():
        seg += f"，镜头运动={str(camera).strip()}"
    return seg


def _time_segments(spec: dict[str, Any], duration_hint: int,
                   first_frame_desc: str, last_frame_desc: str,
                   prev_tail_desc: str) -> str:
    """⑤ 时间分镜（firstlast 核心）：首帧→演变→尾帧切 3 段，每段四要素。

    - 节奏名（起-承-合三段）
    - 运镜（camera / composition 派生）
    - 主体动作（narrative 派生）
    - 特效+节拍（motion 强度派生）
    首帧描述带「上楼层尾帧」上下文（V1.5 决策②：转场判断融入生成，不单独判定）。
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
    beats = list(_FIRSTLAST_BEATS)

    # 运镜描述：camera 优先，其次 composition；缺省按 motion 给保守运镜。
    cam_hint = camera or composition
    if not cam_hint:
        cam_hint = ("低机位快速丝滑运镜" if int(motion) >= 2 else "极缓推进")

    # 三段内容：开场（首帧定格）、主体（叙事演变）、收尾（尾帧定格）。
    body_action = narrative or "主体动作按剧情自然演变"
    segments = [
        (beats[0], f"定格于首帧画面：{first_frame_desc or '楼层开头画面'}",
         f"（与上楼层尾帧衔接：{prev_tail_desc}）" if prev_tail_desc else ""),
        (beats[1], f"{cam_hint}；{body_action}",
         "与节拍/重音同步" if int(motion) >= 2 else ""),
        (beats[2], f"收尾定格于尾帧画面：{last_frame_desc or '楼层结尾画面'}",
         "口型自然、动作极小" if narrative else ""),
    ]

    lines: list[str] = []
    identity = "；人物身份和五官不能发生变化"
    for (beat, content, extra), (start, end) in zip(segments, bounds):
        line = f"[{start}s–{end}s｜{beat}]：{content}{extra}{identity}"
        lines.append(line)
    return "\n".join(lines)


def _audio_hint(spec: dict[str, Any]) -> str:
    """⑥ 音频：占位（firstlast 完整版）。对白来自 comfy_audio，此处仅列结构。"""
    actors = spec.get("actors") or []
    if not actors:
        return "音乐=按本集风格铺底；音效=环境声；同步=视觉事件卡拍"
    return f"音乐=按本集风格铺底；台词=逐字+声线（{'/'.join(actors)}）；同步=动作卡拍"


def compile_climax_video_prompt(
    spec: dict[str, Any],
    *,
    style_prefix: str = "",
    negative: str = "",
    duration_hint: int = 0,
    camera: str = "",
    first_frame_desc: str = "",
) -> str:
    """高潮点·动作代入：精简版提示词（不是剧情影片，禁止时间分镜）。

    区块：① 元信息 ② 风格 ③ 单图绑定 ④ 主体/场景 ⑦ 负面约束
    + 一个「动作瞬间 + 微动态」短句。
    """
    blocks: list[str] = []
    blocks.append(f"使用视频模型生成，{_meta(duration_hint, camera)}。")
    style = _style_declaration(style_prefix, str(spec.get("rating") or ""))
    if style:
        blocks.append(f"[风格]：{style}")
    blocks.append(f"[参考绑定]：{_reference_binding_climax(first_frame_desc, spec.get('actors') or [])}")
    subject = _subject_scene(spec)
    if subject:
        blocks.append(f"[主体/场景]：{subject}")
    narrative = str(spec.get("narrative") or "").strip() or "动作瞬间"
    motion = int(spec.get("motion") or 0)
    micro = "微运镜/特效强化动作张力，卡节拍" if motion >= 2 else "轻微运镜强化代入感"
    blocks.append(f"[动作]：{narrative}；{micro}。")
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
    first_frame_desc: str = "",
    last_frame_desc: str = "",
    prev_tail_desc: str = "",
) -> str:
    """首尾帧·剧情影片：完整七段式（H3 骨架）。

    区块：① 元信息 ② 风格 ③ 双图绑定 ④ 主体/场景 ⑤ 时间分镜 ⑥ 音频 ⑦ 负面约束。
    """
    blocks: list[str] = []
    blocks.append(f"使用视频模型生成，{_meta(duration_hint, camera)}。")
    style = _style_declaration(style_prefix, str(spec.get("rating") or ""))
    if style:
        blocks.append(f"[风格]：{style}")
    blocks.append(
        f"[参考绑定]：{_reference_binding_firstlast(first_frame_desc, last_frame_desc, spec.get('actors') or [])}"
    )
    subject = _subject_scene(spec)
    if subject:
        blocks.append(f"[主体/场景]：{subject}")
    blocks.append(
        "[时间分镜]：\n"
        + _time_segments(spec, duration_hint, first_frame_desc, last_frame_desc, prev_tail_desc)
    )
    blocks.append(f"[音频]：{_audio_hint(spec)}")
    neg = _negative(spec, negative)
    if neg:
        blocks.append(f"[负面约束]：{neg}")
    return "\n\n".join(blocks)


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
) -> dict[str, Any]:
    """组装「上交给视频模型的参数」（dry-run，不提交）。

    对齐 video_gen 的 generate / generate_with_images 签名：
    - 文生：JSON {model, prompt, size}
    - 图生：multipart image[] + {model, prompt, size}

    关键区分（两层，必须一致）：
    - 图地址（first_frame / last_frame）→ 进 images[] 数组，按顺序对应「图片1/图片2」。
    - 图职责描述（first_frame_desc / last_frame_desc）→ 进 prompt 的参考绑定文字
      （H3 要求写「图片1=首帧」这类职责，不是地址）。
    """
    preset = preset or {}
    style_prefix = str(preset.get("stylePrefix") or preset.get("style_template") or "")
    negative = str(preset.get("negativePrompt") or "")
    duration_hint = int(preset.get("videoDurationHint") or 0)
    camera = str(preset.get("videoCamera") or "")

    if mode == "firstlast":
        prompt = compile_firstlast_video_prompt(
            spec, style_prefix=style_prefix, negative=negative,
            duration_hint=duration_hint, camera=camera,
            first_frame_desc=first_frame_desc or "首帧/楼层开头画面",
            last_frame_desc=last_frame_desc or "尾帧/楼层结尾画面",
            prev_tail_desc=prev_tail_desc,
        )
        images = [img for img in (first_frame, last_frame) if img]
        binding = {
            "图片1": _binding_entry(first_frame_desc or "首帧/楼层开头画面", first_frame),
            "图片2": _binding_entry(last_frame_desc or "尾帧/楼层结尾画面", last_frame),
        }
    else:
        prompt = compile_climax_video_prompt(
            spec, style_prefix=style_prefix, negative=negative,
            duration_hint=duration_hint, camera=camera,
            first_frame_desc=first_frame_desc or "高潮动作画面",
        )
        images = [first_frame] if first_frame else []
        binding = {
            "图片1": _binding_entry(first_frame_desc or "高潮动作画面", first_frame),
        }

    base_url = str(video_config.get("base_url") or "").strip().rstrip("/")
    model = str(video_config.get("model") or video_config.get("modelName") or "")
    proxy = str(video_config.get("proxy") or "")

    return {
        "mode": mode,
        "submit": {
            "endpoint": base_url,               # 用户填的原样（代码不猜版本/单复数）
            "model": model,
            "prompt": prompt,                    # 编译出的视频提示词（含参考绑定职责文字）
            "images": images,                    # 参考图地址（climax 1 张 / firstlast 2 张）
            "size": str(video_config.get("size") or "1024x1024"),
            "proxy": proxy or None,
            "content_type": "multipart/form-data" if images else "application/json",
        },
        "reference_binding": binding,
        "prompt_sections": _section_names(mode),
    }


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
