"""视频提示词编译 + 上交给视频模型的参数组装（V1.5/V1.6）。

参照 MiniMax H3 提示词模板规律（docs 外链 `H3-提示词模版规律.md`）：
H3 高服从字面执行，本质是「把成片逐秒、逐镜头、逐像素规定死」。
两套模板语义截然不同，分开建模，不共用：

- climax（高潮点·动作代入）：完整七段式（用户定稿 2026-08-28）。参考绑定声明
  「图片1中心的角色为 X」；动作段用 [时间分镜] 逐拍切段（0–Xs / X–Ys / …）。
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

import json
import math
import re
from typing import Any

from app.services import prompt_clean

# 元信息：默认时长（秒）。preset videoDurationHint 可覆盖。
_DEFAULT_DURATION = 15

# 首尾帧时间分镜的最小三段式（起-承-合），节奏名可被调用方覆盖。
_FIRSTLAST_BEATS = ("开场定格", "主体演变", "收尾定格")

# 镜头语言词汇表（单一事实来源：docs 外链《镜头运动》《镜头角度》）。
# 句式与文档「提示词：」一致，可直接交给 MiniMax H3；按 motion 强度分档，
# 开场定场、中段循环换镜、收尾收束，避免「每拍同一个运镜」的退化。
_CAMERA_ESTABLISH = {
    0: "固定镜头，相机完全静止，零运动，只记录画面内的动作",
    2: "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    3: "低角度仰拍，摄像机以快速弧线围绕主体运动",
}
_CAMERA_MIDDLE = {
    0: (
        "摄像机镜头向左移动，缓慢摇动让更多元素入画",
        "镜头焦点从前景主体切换到背景，引导注意力转移",
        "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    ),
    2: (
        "镜头绕主体旋转90度",
        "镜头横向移动，跟随主体动作",
        "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    ),
    3: (
        "手持摄像机镜头，剧烈抖动，运动模糊",
        "镜头快速推近主体后骤停",
        "摄像机以快速弧线围绕主体运动",
    ),
}
_CAMERA_SOLO = {
    0: "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    2: "镜头绕主体旋转90度",
    3: "低角度仰拍，摄像机以快速弧线围绕主体运动",
}
_CAMERA_CLOSE = {
    0: "镜头慢慢拉远",
    2: "镜头慢慢拉远",
    3: "镜头快速推近主体后骤停",
}
# 剧情句含场景切换提示词时改用转场型运镜（衔接/转场的导演思维）。
_SCENE_SHIFT_CUES = ("另一边", "来到", "走进", "走出", "片刻后", "此时", "镜头一转", "回到", "转场")
_CAMERA_SHIFT_SLOW = "遮挡揭示转场：镜头被前景遮挡后移开，揭示新场景"
_CAMERA_SHIFT_FAST = "甩镜头转场：镜头快速切换，伴有强烈的运动模糊效果"
_IDENTITY_LOCK = "人物身份和五官不能发生变化"


def _camera_band(motion: int) -> int:
    """motion 强度 → 词汇表档位（0 慢 / 2 中 / 3 快）。"""
    return 3 if motion >= 3 else (2 if motion == 2 else 0)


def _beat_camera(motion: int, idx: int, total: int, desc: str = "") -> str:
    """第 idx/total 拍的运镜：开场定场、收尾收束、中段按档位循环换镜；
    单拍用档位代表句；句子带场景切换提示时改用转场型运镜。"""
    band = _camera_band(motion)
    if total <= 1:
        return _CAMERA_SOLO[band]
    if idx == 0:
        return _CAMERA_ESTABLISH[band]
    if idx == total - 1:
        return _CAMERA_CLOSE[band]
    if any(cue in desc for cue in _SCENE_SHIFT_CUES):
        return _CAMERA_SHIFT_FAST if band >= 2 else _CAMERA_SHIFT_SLOW
    vocab = _CAMERA_MIDDLE[band]
    return vocab[(idx - 1) % len(vocab)]


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


def _subject_scene(spec: dict[str, Any]) -> str:
    """④ 主体/场景：可还原的视觉细节，禁止堆砌同义形容词。

    优先 agent 提取的简化视觉描述（video_subject_scene，已去堆砌 + 专名视觉展开）；
    否则拼接「外貌 + 衣着 + 场景」三块——外貌优先主模型英文视觉（subjects.description，
    简洁可还原），缺失回退中文 appearance。
    """
    video_scene = str(spec.get("video_subject_scene") or "").strip()
    if video_scene:
        return video_scene
    parts: list[str] = []
    # 外貌：优先英文视觉（subjects.description），缺失回退中文 appearance
    for subject in spec.get("subjects") or []:
        if isinstance(subject, dict):
            desc = str(subject.get("description") or "").strip()
            if desc and desc not in parts:
                parts.append(desc)
    if not parts:
        appearance = str(spec.get("appearance") or "").strip()
        if appearance:
            parts.append(appearance)
    # 衣着 + 场景（独立维度，始终拼接）
    for key in ("wardrobe", "locale"):
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
    # 身份/五官一致性是全片约束，只进 [负面约束] 一次；不再逐拍重申。
    if _IDENTITY_LOCK not in items:
        items.append(_IDENTITY_LOCK)
    return "；".join(items)


def _appearance_by_actor(appearance: str, actors: list[str]) -> dict[str, str]:
    """从中文外貌文本拆「角色→外貌」映射。

    支持三种格式：`名字(外貌)` 顿号/分号/逗号分隔、`名字：外貌` 多行段落头、
    单角色纯文本（无分隔且只有一个在场角色）。拆不出返回空 dict（不硬猜）。
    """
    src = (appearance or "").strip()
    if not src:
        return {}
    paren = re.findall(r"([^\s、;；，,]+?)\s*[\(（]([^\)）]+)[\)）]", src)
    if paren:
        out: dict[str, str] = {}
        for name, desc in paren:
            name, desc = name.strip(), desc.strip()
            if name and desc:
                out[name] = desc
        return out
    lines = [ln for ln in src.splitlines() if ln.strip()]
    header = re.compile(r"^\s*([^\s：:]+)\s*[：:]\s*(.+)$")
    if len(lines) >= 2 and all(header.match(ln) for ln in lines):
        mapped: dict[str, str] = {}
        for ln in lines:
            m = header.match(ln)
            if m:
                mapped[m.group(1).strip()] = m.group(2).strip()
        return mapped
    # 纯文本（无括号/冒号/换行分隔）：用 actors 名字做前缀匹配，拆出「名字 + 外貌」。
    for actor in sorted(actors, key=len, reverse=True):
        if src.startswith(actor) and len(src) > len(actor):
            rest = src[len(actor):].strip("，,、；;：: ")
            if rest:
                return {actor: rest}
    if len(actors) == 1 and not re.search(r"[：:\(（]", src):
        return {actors[0]: src}
    return {}


def _identity_gloss(spec: dict[str, Any], actors: list[str]) -> str:
    """角色身份样貌绑定串：让视频模型把「角色名」对应到具体长相。

    样貌来源：subjects(name→description 英文视觉) 优先，缺失回退 appearance
    （中文纯外貌，见 _appearance_by_actor）。都缺的角色只写名字（诚实，不硬凑）。
    当 agent 已产出简化外貌/场景（video_subject_scene）时，不再用原始中文
    appearance 兜底——否则堆砌词（丰腴肥熟/酥雌醇媚）会经参考绑定回流，违背 P4。"""
    gloss: dict[str, str] = {}
    for subject in spec.get("subjects") or []:
        if isinstance(subject, dict):
            name = str(subject.get("name") or "").strip()
            subj_desc = str(subject.get("description") or "").strip()
            if name and subj_desc:
                gloss[name] = subj_desc
    app_gloss: dict[str, str] = {}
    if not str(spec.get("video_subject_scene") or "").strip():
        app_gloss = _appearance_by_actor(str(spec.get("appearance") or ""), actors)
    parts: list[str] = []
    for actor in actors:
        desc: str | None = gloss.get(actor) or app_gloss.get(actor)
        parts.append(f"{actor}（{desc}）" if desc else actor)
    return "、".join(parts)


def _climax_frame_role(spec: dict[str, Any], first_frame_desc: str = "") -> str:
    """图片职责描述（图片↔角色绑定）：外部 first_frame_desc 优先；否则用
    「{画面角色}的高潮动作画面」，让视频模型知道这张高潮图是谁的高潮图——
    单角色、多角色都绑定，不只单角色一种情况。无在场角色才退「高潮动作画面」占位。

    视频模型拿到参考图时只看得见图，不知道图里的人对应剧情里的哪个名字；
    把角色名写进图片职责描述，等于声明「这张图就是这些角色的画面」，名字才落得实。"""
    desc = (first_frame_desc or "").strip()
    if desc and desc != "高潮动作画面":
        return desc
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        return f"{'、'.join(actors)}的高潮动作画面"
    return "高潮动作画面"


def _reference_binding_climax(first_frame_desc: str, spec: dict[str, Any]) -> str:
    """③ 参考绑定（climax 单图）：图片1中心的角色为 X，锁身份 + 角色样貌。

    不再写「画面另含未绑定角色…」提示（用户拍板 2026-08-28）：只需声明
    「图片1中心的角色为 X」即可，无名配角由视频模型按画面自行区分。
    画面级动作细节由 [时间分镜] 承载。"""
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        lines = [f"图片1中心的角色为{'、'.join(actors)}"]
        lines.append(f"保持 {_identity_gloss(spec, actors)} 的身份、脸部、服装、发型、造型完全一致")
    else:
        desc = _climax_frame_role(spec, first_frame_desc)
        lines = [f"图片1={desc}（唯一参考画面，作为起始帧）"]
    return "；".join(lines)


def _reference_binding_firstlast(first_frame_desc: str, last_frame_desc: str,
                                 spec: dict[str, Any],
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
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        lines.append(f"画面角色：{'、'.join(actors)}")
        lines.append(f"保持 {_identity_gloss(spec, actors)} 的身份、脸部、服装、发型、造型完全一致")
    return "；".join(lines)


def _reference_binding_transition(prev_tail_desc: str, first_frame_desc: str,
                                  spec: dict[str, Any],
                                  has_prev_tail: bool = True, has_first: bool = True) -> str:
    """③ 参考绑定（转场双图）：图片1=上一楼层尾帧（转场起点）、图片2=当前楼层首帧（转场终点）。

    缺图时诚实标注「无参考图，以文字为准」（R2 同套路），不假装有图。
    """
    pt = (prev_tail_desc or "").strip() or "上一楼层尾帧画面"
    ff = (first_frame_desc or "").strip() or "当前楼层首帧画面"
    if has_prev_tail:
        lines = [f"图片1={pt}（上一楼层尾帧/转场起点）"]
    else:
        lines = [f"上一楼层尾帧（无参考图，以文字为准）：{pt}"]
    if has_first:
        lines.append(f"图片2={ff}（当前楼层首帧/转场终点）")
    else:
        lines.append(f"当前楼层首帧（无参考图，以文字为准）：{ff}")
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        lines.append(f"画面角色：{'、'.join(actors)}")
        lines.append(f"保持 {_identity_gloss(spec, actors)} 的身份、脸部、服装、发型、造型完全一致")
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


def _meta_transition(duration_hint: int, camera: str,
                     model_name: str = "", size: str = "") -> str:
    """① 元信息（转场形态，坑G）：不硬控时长。

    转场时长不预设死值（用户定调）：duration_hint=0 → 不写秒数（交视频模型/模板
    默认），绝不兑底 _DEFAULT_DURATION；前端提交侧有转场时长（transitionDurationHint）
    才写具体秒数。正片（climax/firstlast）仍走 _meta 的 videoDurationHint。
    """
    parts: list[str] = []
    if model_name and str(model_name).strip():
        parts.append(str(model_name).strip())
    if int(duration_hint or 0) > 0:
        parts.append(f"{int(duration_hint)} seconds")
    else:
        parts.append("时长=视频模型默认（短桥段）")
    aspect = _aspect_from_size(size)
    if aspect:
        parts.append(aspect)
    if camera and str(camera).strip():
        parts.append(f"镜头运动={str(camera).strip()}")
    return "，".join(parts)


def _split_narrative_beats(narrative: str) -> list[str]:
    """剧情逐句切拍：。！？；、换行切句 + 引号句合并归属。

    - 引号内的分隔符不切拍（「开饭了，都过来！」是一个完整对白拍）；
    - 闭引号后紧跟正文且无句点时同样断拍（对白归属对白拍，不把闭引号
      粘到下一事件拍的开头）；
    - 去掉引号包裹片段后仍命中拒答句式的句子整句剔除——主模型拒答句
      （「我不能协助这项请求。」一类）不得流进 [时间分镜]；引号内的
      对白原样保留（「不能满足你」是正常台词，不过滤）。
    """
    text = str(narrative or "")
    punct = "。！？；" + chr(10)
    open_q = "「“『‘"
    close_q = "」”』’"
    sentences: list[str] = []
    start = 0
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in open_q:
            depth += 1
        elif ch in close_q:
            depth = max(0, depth - 1)
            nxt = text[i + 1] if i + 1 < len(text) else ""
            # 仅当闭引号后紧跟正文（字母/汉字，非逗号等停顿标点）才断拍
            if depth == 0 and nxt and re.match(r"[\w]", nxt):
                s = text[start:i + 1].strip()
                if s:
                    sentences.append(s)
                start = i + 1
        elif ch in punct and depth == 0:
            s = text[start:i + 1].strip()
            if s:
                sentences.append(s)
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    kept: list[str] = []
    for s in sentences:
        bare = re.sub(r"「[^」]*」|“[^”]*”|『[^』]*』|‘[^’]*’", "", s)
        if bare and prompt_clean.REFUSAL_RE.search(prompt_clean.restore_jailbreak(bare)):
            continue
        kept.append(s)
    return kept


def _time_segments(spec: dict[str, Any], duration_hint: int,
                   first_frame_desc: str, last_frame_desc: str,
                   prev_tail_desc: str,
                   has_first: bool = True, has_last: bool = True) -> str:
    """⑤ 时间分镜（firstlast 核心）：首帧→剧情逐句成拍→尾帧，覆盖整段桥段。

    - 首帧段带「上楼层尾帧」衔接上下文（转场判断融入生成，不单独判定）；
    - 中段把 narrative 按句子切拍：每个事件/每句对白各占一拍（引语拍标台词同步）；
    - 尾帧段收尾定格 + 拉镜头收束；缺图时诚实标注（R2）。
    - 运镜取自镜头语言词汇表，按拍位分配（定场→换镜→收束），不再全程同一「极缓推进」。
    """
    dur = int(duration_hint) if duration_hint else _DEFAULT_DURATION
    narrative = str(spec.get("narrative") or "").strip()
    motion_raw = spec.get("motion")
    motion = int(motion_raw) if isinstance(motion_raw, (int, float)) else 0
    model_cam = str(spec.get("camera") or "").strip()

    f_open = (first_frame_desc or "楼层开头画面").strip()
    l_close = (last_frame_desc or "楼层结尾画面").strip()

    # 首帧段：有图定格首帧 / 无图文字开场；带衔接上下文。
    open_content = f"定格于首帧画面：{f_open}" if has_first else f"以文字首帧开场：{f_open}"
    if prev_tail_desc:
        open_extra = f"承接上一镜头尾帧（{prev_tail_desc}）自然延续，机位/光影/人物状态连贯，无突兀跳切"
    else:
        open_extra = "作为本段独立开场镜头"

    # 中段：剧情逐句成拍（每事件/每对白一镜），引语拍标注台词同步。
    # _split_narrative_beats 负责引号句合并归属 + 拒答句剔除，不把拒答正文编进分镜。
    sentences = _split_narrative_beats(narrative)[:8]
    mid_beats: list[tuple[str, str]] = []
    for sent in sentences:
        content = sent
        if "「" in sent or "“" in sent:
            content += "；台词随口型同步"
        mid_beats.append((content, "与节拍/重音同步" if int(motion) >= 2 else ""))
    if not mid_beats:
        mid_beats = [("主体动作按剧情自然演变", "")]

    # 尾帧段：有图定格尾帧 / 无图文字收尾；收束为下一镜头留过渡点。
    close_content = f"收尾定格于尾帧画面：{l_close}" if has_last else f"以文字尾帧收尾：{l_close}"
    close_extra = "动作自然收束、神态定格，为下一镜头衔接留出自然过渡点"

    beats: list[tuple[str, str, str]] = [(open_content, open_extra, "开场定格")]
    for content, extra in mid_beats:
        beats.append((content, extra, "剧情演变"))
    beats.append((close_content, close_extra, "收尾定格"))

    lines: list[str] = []
    n = len(beats)
    for idx, (content, extra, label) in enumerate(beats):
        start = dur * idx // n
        end = dur * (idx + 1) // n
        cam = model_cam or _beat_camera(int(motion), idx, n, content)
        tail = f"（{extra}）" if extra else ""
        lines.append(f"[{start}s–{end}s｜{label}]：{cam}；{content}{tail}")
    return "\n".join(lines)


def _transition_segments(spec: dict[str, Any],
                         prev_tail_desc: str, first_frame_desc: str,
                         has_prev_tail: bool = True, has_first: bool = True) -> str:
    """⑤ 转场分镜（短桥段）：上一楼层尾帧定格 → 自然过渡 → 当前楼层首帧定格。

    短桥段不套正片时长分段（9.5 时长分档）：无时间轴，交模型按短桥段默认节奏。
    缺图时诚实标注「以文字为准」（R2 同套路），不假装有图。
    """
    pt = (prev_tail_desc or "").strip() or "上一楼层尾帧画面"
    ff = (first_frame_desc or "").strip() or "当前楼层首帧画面"
    cam_hint = str(spec.get("camera") or "").strip() or _CAMERA_SOLO[0]
    lines = [
        f"[转场起点]：{('定格于上一楼层尾帧画面：' + pt) if has_prev_tail else ('以文字重现上一楼层尾帧：' + pt + '（无参考图，以文字为准）')}",
        f"[过渡]：{cam_hint}自然过渡到当前楼层首帧，机位运动连贯、光影衔接、人物状态延续，无突兀跳切",
        f"[转场终点]：{('收束定格于当前楼层首帧画面：' + ff) if has_first else ('以文字收束到当前楼层首帧：' + ff + '（无参考图，以文字为准）')}",
    ]
    return "\n".join(lines)


def _audio_hint(
    spec: dict[str, Any], audio_lines: list | None = None, include_lines: bool = True,
) -> str:
    """⑥ 音频：音乐 / 音效 / 台词 / 同步 四要素。

    优先读 agent 同轮提取的 audio_design（音乐 + 具体音效清单 + 逐字台词 + 卡拍），
    让音频不再是「环境声」单薄占位；缺失时回退纯音乐结构（R6：无逐字对白不写
    「台词=逐字」诱导幻觉）。audio_lines 形如 [{speaker, text}, ...]（comfy_audio
    对白），在无 audio_design 时作为台词兜底。

    include_lines=False（climax 模式）：高潮定格时刻对白通常已经说完（用户定稿
    2026-08-28），动作窗口内不写台词——无论来源一律不列，[音频] 只有音乐/音效/同步。
    """
    audio_raw = spec.get("audio_design")
    design: dict[str, Any] = dict(audio_raw) if isinstance(audio_raw, dict) else {}
    music = str(design.get("music") or "").strip()
    sfx: list[str] = []
    for item in design.get("sfx") or []:
        s = str(item).strip()
        if s and s not in sfx:
            sfx.append(s)
    lines: list[str] = []
    for ln in design.get("lines") or []:
        if isinstance(ln, dict):
            text = str(ln.get("text") or "").strip()
            speaker = str(ln.get("speaker") or "").strip()
        else:
            text = str(ln).strip()
            speaker = ""
        if text:
            # 台词时点：at_s 是提取 LLM 按剧情位置推算的『什么时候说』（秒）——
            # 有就标进去，视频模型才知道这句落在哪个情节位置；缺失诚实省略。
            at = ln.get("at_s") if isinstance(ln, dict) else None
            at_txt = ""
            if isinstance(at, (int, float)) and not isinstance(at, bool):
                at_txt = f"{at:g}s｜"
            lines.append(f"{at_txt}{speaker}：{text}" if speaker else f"{at_txt}{text}")
    sync = str(design.get("sync") or "").strip()

    # 无 audio_design 时：台词从 audio_lines 参数兜底
    if not design and audio_lines:
        for ln in audio_lines:
            if isinstance(ln, dict):
                text = str(ln.get("text") or "").strip()
                speaker = str(ln.get("speaker") or "").strip()
            else:
                text = str(ln).strip()
                speaker = ""
            if text:
                lines.append(f"{speaker}：{text}" if speaker else text)

    parts = [f"音乐={music or '按本集风格铺底'}"]
    if sfx:
        parts.append(f"音效={'、'.join(sfx)}")
    else:
        parts.append("音效=环境声")
    if lines and include_lines:
        parts.append(f"台词={'；'.join(lines)}")
    parts.append(f"同步={sync or '视觉事件卡拍'}")
    return "；".join(parts)


def _climax_action_beat(spec: dict[str, Any]) -> str:
    """高潮动作延伸：优先用 action_sequence（主模型同轮输出的「定格动作 → 剧情描述的
    完整动作」时序，与图片提示词同源），缺失回退画面级要素
    （subjects/visual_facts/composition），再回退中文 narrative 原文。"""
    seq = spec.get("action_sequence")
    if isinstance(seq, list) and seq:
        beats: list[str] = []
        for item in seq:
            if not isinstance(item, dict):
                continue
            beat = str(item.get("beat") or "").strip()
            desc = str(item.get("desc") or "").strip()
            if not desc:
                continue
            beats.append(f"{beat}: {desc}" if beat else desc)
        if beats:
            return "；".join(beats)
    # 动作段只承载「动作/姿态/接触关系」，不得用 subjects.description（外貌）
    # 兜底——否则动作段会退化成整段外貌描述（P1/P5 缺陷）。外貌属于 [主体/场景]。
    parts: list[str] = []
    for fact in spec.get("visual_facts") or []:
        if isinstance(fact, dict):
            f = str(fact.get("fact") or "").strip()
            if f and f not in parts:
                parts.append(f)
    composition = str(spec.get("composition") or "").strip()
    if composition and composition not in parts:
        parts.append(composition)
    if parts:
        return ", ".join(parts)
    return str(spec.get("narrative") or "").strip() or "动作瞬间"


def _climax_fallback_beats(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """无 action_sequence 时的动作段兜底：画面级要素优先（单拍，旧行为），
    否则按句子切分 narrative 成多拍，避免「动作断链」退化成整段单拍。"""
    single = _climax_action_beat(spec)
    narrative = str(spec.get("narrative") or "").strip()
    # 命中画面级要素（visual_facts/composition）或 narrative 为空时保持单拍，不臆造分镜
    if single != narrative or not narrative:
        return [("主体动作", single)]
    sentences = _split_narrative_beats(narrative)
    if not sentences:
        # narrative 整段为拒答/空壳：回退诚实占位，不把拒答句编进 [时间分镜]
        return [("主体动作", "主体动作按剧情自然演变")]
    if len(sentences) == 1:
        return [("主体动作", sentences[0])]
    labels = ("定格起点", "主体动作", "延伸", "收尾")
    out: list[tuple[str, str]] = []
    for idx, sent in enumerate(sentences[:8]):
        label = labels[idx] if idx < len(labels) else "延伸"
        out.append((label, sent))
    return out


def _climax_camera(spec: dict[str, Any], motion: int) -> str:
    """运镜：主模型 camera 优先（直接采用，不叠词汇表）；否则用档位代表句。

    `_climax_time_segments` 会在此基础上按拍位替换为词汇表句（定场/换镜/收束），
    此处只返回单拍代表句供兼容旧调用。
    """
    camera = str(spec.get("camera") or "").strip()
    if camera:
        return camera
    return _CAMERA_SOLO[_camera_band(motion)]


def _climax_fx(motion: int) -> str:
    """纯特效：按 motion 强度给动态手法（节拍同步由 _climax_beat_sync 单独承担）。"""
    if motion >= 3:
        return "能量爆发式特效，画面错位"
    if motion >= 2:
        return "强化动作张力"
    return ""


def _climax_beat_sync(motion: int) -> str:
    """节拍同步：按 motion 强度给节奏手法。"""
    if motion >= 2:
        return "与节拍/重音同步"
    return "按自然节奏推进"


def _climax_time_segments(spec: dict[str, Any], duration_hint: int) -> str:
    """⑤ 时间分镜（climax）：把高潮动作切成 0–Xs / X–Ys / … 逐拍段，
    每段 = 节奏名 + 运镜 + 主体动作 + 特效 + 节拍同步。

    运镜按拍位取自镜头语言词汇表：首拍定场（固定/缓推），中段循环换镜，
    尾拍拉远/骤停收束；主模型 camera 存在时全程采用。身份/五官一致性
    不再逐拍重申，统一收进 [负面约束]（只出现一次）。

    优先 action_sequence（定格起点/延伸/收尾的完整动作，与图片提示词同源）；
    缺失回退单一动作瞬间（画面级要素或 narrative）。每段按总时长均分。
    """
    dur = int(duration_hint) if duration_hint else _DEFAULT_DURATION
    motion = int(spec.get("motion") or 0)
    model_cam = str(spec.get("camera") or "").strip()
    fx = _climax_fx(motion)
    sync = _climax_beat_sync(motion)

    beats: list[tuple[str, str]] = []
    seq = spec.get("action_sequence")
    if isinstance(seq, list) and seq:
        for item in seq:
            if not isinstance(item, dict):
                continue
            beat = str(item.get("beat") or "").strip()
            desc = str(item.get("desc") or "").strip()
            if desc:
                beats.append((beat or "节拍", desc))
    if not beats:
        beats = _climax_fallback_beats(spec)

    lines: list[str] = []
    n = len(beats)
    for idx, (beat, action) in enumerate(beats):
        start = dur * idx // n
        end = dur * (idx + 1) // n
        cam = model_cam or _beat_camera(motion, idx, n, action)
        parts = [p for p in (cam, action, fx, sync) if p]
        lines.append(f"{start}–{end}s｜{beat}：{'；'.join(parts)}")
    return "\n".join(lines)


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
    audio_lines: list | None = None,
    has_frame: bool = False,
) -> str:
    """高潮点·六段式（用户定稿 2026-08-28）：① 元信息 ② 参考绑定 ③ 主体/场景
    ④ 时间分镜 ⑤ 音频 ⑥ 负面约束。风格声明已停用（有参考图定调，style_prefix 仅兼容）。

    动作段用 [时间分镜] 逐拍切段（0–Xs / X–Ys / …），不再用「定格起点/延伸/收尾」
    长句。has_frame=True 时在元信息标注「使用输入图片作为准确起始帧」（I2V）。
    """
    blocks: list[str] = []
    meta = _meta(duration_hint, camera, model_name, size)
    frame_note = "；使用输入图片作为准确起始帧" if has_frame else ""
    blocks.append(f"[元信息]：{meta}{frame_note}")
    blocks.append(
        f"[参考绑定]：{_reference_binding_climax(first_frame_desc, spec)}"
    )
    subject = _subject_scene(spec)
    if subject:
        blocks.append(f"[主体/场景]：{subject}")
    blocks.append("[时间分镜]：\n" + _climax_time_segments(spec, duration_hint))
    # climax：高潮定格时刻对白通常已说完（用户定稿 2026-08-28）——动作窗口不带台词，
    # 无论来源（audio_design.lines / comfy_audio 兜底）一律不列。
    blocks.append(f"[音频]：{_audio_hint(spec, audio_lines, include_lines=False)}")
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
    """首尾帧·剧情影片：六段式（H3 骨架）。

    区块：① 元信息 ② 双图绑定 ③ 主体/场景 ④ 时间分镜 ⑤ 音频 ⑥ 负面约束。
    风格声明已停用（有参考图定调）。has_first/has_last 标识是否真的提供了首/尾帧图（R2 缺图守卫）。
    """
    blocks: list[str] = []
    blocks.append(f"[元信息]：{_meta(duration_hint, camera, model_name, size)}")
    blocks.append(
        f"[参考绑定]：{_reference_binding_firstlast(first_frame_desc, last_frame_desc, spec, has_first, has_last)}"
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


def compile_transition_video_prompt(
    spec: dict[str, Any],
    *,
    style_prefix: str = "",
    negative: str = "",
    duration_hint: int = 0,
    camera: str = "",
    model_name: str = "",
    size: str = "",
    prev_tail_desc: str = "",
    first_frame_desc: str = "",
    has_prev_tail: bool = True,
    has_first: bool = True,
) -> str:
    """转场视频·短桥段：上一楼层尾帧 → 当前楼层首帧 的自然过渡。

    六段式骨架（对齐 firstlast）：① 元信息 ② 参考绑定（图片1=上尾帧、
    图片2=当前首帧）③ 主体/场景 ④ 转场分镜 ⑤ 音频 ⑥ 负面约束。风格声明已停用（有参考图定调）。

    坑G：时长不预设死值——duration_hint=0 时不写秒数（_meta_transition 交视频模型
    默认），不兑底 _DEFAULT_DURATION；前端提交侧有转场时长才写。

    has_prev_tail/has_first 标识是否真的提供了上尾帧图/当前首帧图（R2 缺图守卫）。
    """
    blocks: list[str] = []
    blocks.append(f"[元信息]：{_meta_transition(duration_hint, camera, model_name, size)}")
    blocks.append(
        f"[参考绑定]：{_reference_binding_transition(prev_tail_desc, first_frame_desc, spec, has_prev_tail, has_first)}"
    )
    subject = _subject_scene(spec)
    if subject:
        blocks.append(f"[主体/场景]：{subject}")
    blocks.append(
        "[转场分镜]：\n"
        + _transition_segments(spec, prev_tail_desc, first_frame_desc,
                               has_prev_tail, has_first)
    )
    blocks.append(f"[音频]：{_audio_hint(spec, None)}")
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

    mode="transition"（W3 转场视频，短桥段）参数映射：
    - first_frame = 上一楼层尾帧图地址（图片1，转场起点），
      last_frame = 当前楼层首帧图地址（图片2，转场终点）；
    - first_frame_desc = 当前首帧描述（终点），prev_tail_desc = 上尾帧描述（起点）；
      last_frame_desc 在 transition 分支不使用；图地址 first_frame/last_frame 分别映射
      「图片1=上尾帧、图片2=当前首帧」；
    - 转场时长走 preset.transitionDurationHint（坑G：不预设死值，缺省交模型默认），
      正片（climax/firstlast）仍走 videoDurationHint。
    """
    preset = preset or {}
    spec = _clean_spec(spec or {})
    style_prefix = str(preset.get("stylePrefix") or preset.get("style_template") or "")
    negative = str(preset.get("negativePrompt") or "")
    duration_hint = int(preset.get("videoDurationHint") or 0)
    transition_duration_hint = int(preset.get("transitionDurationHint") or 0)
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
    elif mode == "transition":
        # W3 转场视频：图片1=上尾帧（起点），图片2=当前首帧（终点），短桥段
        has_prev_tail = bool(first_frame)
        has_first = bool(last_frame)
        prompt = compile_transition_video_prompt(
            spec, style_prefix=style_prefix, negative=negative,
            duration_hint=transition_duration_hint, camera=camera,
            model_name=model, size=size,
            prev_tail_desc=prev_tail_desc or "上一楼层尾帧画面",
            first_frame_desc=first_frame_desc or "当前楼层首帧画面",
            has_prev_tail=has_prev_tail, has_first=has_first,
        )
        images = [img for img in (first_frame, last_frame) if img]
        binding = {
            "图片1": _binding_entry(prev_tail_desc or "上一楼层尾帧画面", first_frame),
            "图片2": _binding_entry(first_frame_desc or "当前楼层首帧画面", last_frame),
        }
    else:
        # 参考绑定点：图片1只声明「图片1中心的角色为 X」（有在场角色时）；无在场角色
        # 才退回到 _climax_frame_role 的职责占位描述。画面级动作细节由 [时间分镜] 承载。
        desc = _climax_frame_role(spec, first_frame_desc)
        prompt = compile_climax_video_prompt(
            spec, style_prefix=style_prefix, negative=negative,
            duration_hint=duration_hint, camera=camera, model_name=model, size=size,
            first_frame_desc=desc, audio_lines=audio_lines, has_frame=bool(first_frame),
        )
        images = [first_frame] if first_frame else []
        actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
        role = f"图片1中心的角色为{'、'.join(actors)}" if actors else desc
        binding = {
            "图片1": _binding_entry(role, first_frame),
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
    elif mode == "transition":
        # 坑F：转场起点（上尾帧图）缺省 → 降级文字转场；终点（当前首帧图）缺省 → 文字收束
        if not first_frame:
            warnings.append("缺上尾帧图：转场降级为文字转场（以文字重现上一楼层尾帧）")
        if not last_frame:
            warnings.append("缺当前首帧图：转场终点以文字描述")
        if not first_frame and not last_frame:
            warnings.append("转场双图均缺：将退化为纯文生短桥段（无参考图）")
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
    """提示词含哪些区块（供人核对完整性）。风格声明已停用（有参考图定调）。"""
    if mode == "firstlast":
        return ["①元信息", "②参考绑定", "③主体/场景", "④时间分镜", "⑤音频", "⑥负面约束"]
    if mode == "transition":
        return ["①元信息", "②参考绑定", "③主体/场景", "④转场分镜", "⑤音频", "⑥负面约束"]
    return ["①元信息", "②参考绑定", "③主体/场景", "④时间分镜", "⑤音频", "⑥负面约束"]


def parse_video_plan(reply: str) -> dict[str, Any]:
    """解析视频提示词专用提取（动作延伸 + 简化外貌/场景）的 JSON 回复。

    容忍 markdown 代码块包裹与破甲残留；字段不合法时只丢弃对应键，不整体失败。
    产出 {action_sequence: [{beat, desc}], subject_scene: str}。

    拒答防御（对齐生图链）：正文性字段（desc/subject_scene/music/sfx/sync）命中
    拒答句式即丢弃该条——模型把「我不能协助这项请求」写进 JSON 时，不得让它
    流进 [时间分镜]/[音频] 段；台词原文不过滤（防拦截正文里「我不能满足你」这类
    正常对白必须原样保留）。整体无效（拒答/无 JSON）时返回 {}，
    调用方据此判定重试或回退纯函数兜底。
    """
    raw = (reply or "").strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    seq = data.get("action_sequence")
    if isinstance(seq, list):
        beats: list[dict[str, str]] = []
        for item in seq[:8]:
            if not isinstance(item, dict):
                continue
            beat = str(item.get("beat") or "").strip()
            desc = prompt_clean.restore_jailbreak(str(item.get("desc") or "")).strip()
            if not desc or prompt_clean.REFUSAL_RE.search(desc):
                continue
            beats.append({"beat": beat or "延伸", "desc": desc})
        if beats:
            out["action_sequence"] = beats
    subject_scene = data.get("subject_scene")
    if isinstance(subject_scene, str) and subject_scene.strip():
        scene_text = prompt_clean.restore_jailbreak(subject_scene).strip()
        if scene_text and not prompt_clean.REFUSAL_RE.search(scene_text):
            out["subject_scene"] = scene_text
    audio_design = data.get("audio_design")
    if isinstance(audio_design, dict):
        design: dict[str, Any] = {}
        music = prompt_clean.restore_jailbreak(str(audio_design.get("music") or "")).strip()
        if music and not prompt_clean.REFUSAL_RE.search(music):
            design["music"] = music
        sfx: list[str] = []
        for item in audio_design.get("sfx") or []:
            s = prompt_clean.restore_jailbreak(str(item)).strip()
            if not s or prompt_clean.REFUSAL_RE.search(s) or s in sfx:
                continue
            sfx.append(s)
        if sfx:
            design["sfx"] = sfx
        lines: list[dict[str, Any]] = []
        for ln in audio_design.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            speaker = prompt_clean.restore_jailbreak(str(ln.get("speaker") or "")).strip()
            text = prompt_clean.restore_jailbreak(str(ln.get("text") or "")).strip()
            if text:
                entry: dict[str, Any] = {"speaker": speaker, "text": text}
                # at_s：提取 LLM 按剧情位置推算的台词时点（秒）——数字/数字串才透传
                at_s = ln.get("at_s")
                if isinstance(at_s, (int, float)) and not isinstance(at_s, bool):
                    entry["at_s"] = max(0.0, float(at_s))
                elif isinstance(at_s, str):
                    try:
                        entry["at_s"] = max(0.0, float(at_s.strip()))
                    except ValueError:
                        pass
                lines.append(entry)
        if lines:
            design["lines"] = lines
        sync = prompt_clean.restore_jailbreak(str(audio_design.get("sync") or "")).strip()
        if sync and not prompt_clean.REFUSAL_RE.search(sync):
            design["sync"] = sync
        if design:
            out["audio_design"] = design
    return out
