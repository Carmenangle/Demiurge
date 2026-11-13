"""全局模式离线分镜模拟（2026-11-09）。

不提交 ComfyUI；默认也不调 LLM。用生产同款 `shot_plan.build_shot_plan` +
`video_prompt.build_video_request` 把一段剧情拆成镜头清单、图片/视频提示词与时长预算，
供实机（租显卡）前先核对「拆了几张图、几支视频、哪些硬切、哪些运镜」。

三种输入：
  ① 默认：内置演示剧情 + 手工锚点（完全离线，零成本）；
  ② `--chat <chat.json>` + `--judge`：从真实作品对话取一段正文，用**生产判据 LLM**
     现场判定锚点（含 shot_type / transition_desc），再走同一套编译；
  ③ `--chat ... --anchors <anchors.json>`：正文取自作品，锚点来自文件（复跑/对标用）。

可选 `--profile-llm`：图片提示词也走生产 Profile LLM（每镜一次调用）；默认用
`deterministic_fallback` 样本，只验证结构不验证文风。

用法（backend 目录）：
  .venv/Scripts/python scripts/probe_shot_plan.py --total 30
  .venv/Scripts/python scripts/probe_shot_plan.py --chat ../userdata/pictures/<作品>/<子库>/chat.json \
      --judge --batch 6 --total 30
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
    image_prompt_extract, image_prompt_profiles, illustration_prompt_store,
    llm, repo_meta, scene_card_llm, scene_illustration, scene_reference, shot_plan,
    shot_reliability, video_prompt,
)

# ── 演示剧情（判据 LLM 真实运行时由正文自动选锚点；这里手工给出等价输出）─────────
DEMO_TEXT = """\
雨夜，老面馆的玻璃门上水珠连成线。林屿收起伞，抖落肩上的雨，推门进来。

纪含章坐在靠窗的位子，抬头看他，把桌上那碗热汤往对面推了推。

苏绾从后厨端出一碟卤味，笑着放在两人中间。

三人碰杯，汤面腾起白汽，暖黄的灯把他们的影子叠在一起。

镜头一转，凌晨的巷口，雨停了。林屿把外套披在纪含章肩上，苏绾站在几步外。

三人并肩走进薄雾。"""

DEMO_ANCHORS = [
    {
        "anchor": "林屿收起伞，抖落肩上的雨，推门进来。",
        "shot_type": "move",
        "transition_desc": (
            "镜头从门外雨幕中的中景跟随林屿进门：他收伞时伞尖的水珠甩向画面左下，"
            "机位随他前移越过门框，玻璃上的雨痕在前景流过，最后停在桌边，"
            "把纪含章抬头的动作带进同一景深——全片一条连续前移，不切轴、不换镜。"
        ),
        "original_index": 0,
    },
    {
        "anchor": "纪含章坐在靠窗的位子，抬头看他，把桌上那碗热汤往对面推了推。",
        "shot_type": "cut",
        "transition_desc": "",
        "original_index": 1,
    },
    {
        "anchor": "苏绾从后厨端出一碟卤味，笑着放在两人中间。",
        "shot_type": "cut",
        "transition_desc": "",
        "original_index": 2,
    },
    {
        "anchor": "三人碰杯，汤面腾起白汽，暖黄的灯把他们的影子叠在一起。",
        "shot_type": "cut",
        "transition_desc": "",
        "original_index": 3,
    },
    {
        "anchor": "镜头一转，凌晨的巷口，雨停了。",
        "shot_type": "move",
        "transition_desc": (
            "镜头从面馆暖黄内景向外拉出，穿过玻璃水珠与门框，落到凌晨巷口；"
            "湿漉漉的地面反光逐渐占据下半画面。"
        ),
        "original_index": 4,
    },
    {
        "anchor": "三人并肩走进薄雾。",
        "shot_type": "move_single",
        "transition_desc": (
            "镜头从三人背后缓慢拉远并升高：水洼倒影先入画，薄雾一点点柔化人物轮廓，"
            "巷道尽头留白扩大——全片一条连续后拉，不切、不摇回。"
        ),
        "original_index": 5,
    },
]

BASE_SPEC = {
    "appearance": "林屿(深灰风衣+黑伞)、纪含章(米色针织开衫)、苏绾(墨绿围裙+短发)",
    "wardrobe": "雨夜日常私服",
    "locale": "老面馆内景，暖黄吊灯/玻璃水珠/木质吧台",
    "actors": ["林屿", "纪含章", "苏绾"],
    "rating": "sfw",
    "aspect_ratio": "16:9",
    "motion": 3,
    "composition": "三人中景",
    "camera": "固定机位",
}

# 模拟提取层（生产由 `_extract_video_action_plan` 一次 LLM 产出；这里手工给出等价动作流）。
DEMO_ACTIONS = {
    "林屿收起伞，抖落肩上的雨，推门进来。": [
        {"beat": "收伞", "desc": "林屿在门口收拢黑伞，伞尖的水珠甩向画面左下"},
        {"beat": "进门", "desc": "他抖落肩上的雨珠，推门跨过门槛，暖黄灯光落在他侧脸"},
        {"beat": "抬眼", "desc": "他抬眼看向窗边，脚步停在桌沿外半步"},
    ],
    "纪含章坐在靠窗的位子，抬头看他，把桌上那碗热汤往对面推了推。": [
        {"beat": "抬头", "desc": "纪含章从汤面白汽后抬起头，视线越过桌沿落在林屿脸上"},
        {"beat": "推碗", "desc": "她把手边的汤碗向对面推，碗底在木桌上滑出半寸，白汽随之倾斜"},
        {"beat": "停手", "desc": "她的指尖在碗沿停住，嘴角带一点笑，等林屿落座"},
    ],
    "苏绾从后厨端出一碟卤味，笑着放在两人中间。": [
        {"beat": "端碟", "desc": "苏绾从后厨门帘后走出，双手端着一碟卤味"},
        {"beat": "放碟", "desc": "她把碟子放在两人中间，碟底轻碰桌面"},
        {"beat": "笑", "desc": "她直起身，笑着在两人之间坐下"},
    ],
    "三人碰杯，汤面腾起白汽，暖黄的灯把他们的影子叠在一起。": [
        {"beat": "举杯", "desc": "三人同时举起手边的杯子，杯沿向桌面中央靠拢"},
        {"beat": "碰杯", "desc": "三只杯轻轻相碰，汤面腾起一股白汽"},
        {"beat": "叠影", "desc": "暖黄吊灯把三人的影子在墙上叠成一片，动作缓缓定住"},
    ],
    "镜头一转，凌晨的巷口，雨停了。": [
        {"beat": "披衣", "desc": "林屿把深灰风衣披到纪含章肩上，手指在她领口停了一下"},
        {"beat": "站定", "desc": "苏绾在几步外收拢黑伞，伞尖的水滴落在水洼里"},
        {"beat": "回望", "desc": "两人回头看向她，巷口的路灯把三道影子拉长"},
    ],
    "三人并肩走进薄雾。": [
        {"beat": "并肩", "desc": "三人并肩向巷子深处走，脚步节奏一致"},
        {"beat": "入雾", "desc": "薄雾从脚下漫上来，逐渐吞掉三人的轮廓"},
        {"beat": "留白", "desc": "巷道尽头只剩路灯光晕，人影几乎融进雾里"},
    ],
}


# ── 输入：作品正文 / 锚点 / 判据 ──────────────────────────────────────────────
def _parse_status_block(raw_text: str) -> dict[str, str]:
    """取最后一段 `<status>` 的 `[键] 值`（在场/地点/衣着…）——生产 sig 的同源快照。"""
    blocks = re.findall(r"<status>(.*?)</status>", raw_text or "", re.S)
    if not blocks:
        return {}
    out: dict[str, str] = {}
    for line in blocks[-1].splitlines():
        hit = re.match(r"\s*\[([^\]]+)\]\s*(.+)", line)
        if hit:
            out[hit.group(1).strip()] = hit.group(2).strip()
    return out


def load_chat_text(chat_path: str, message_id: str = "", chars: int = 1600) -> tuple[str, dict]:
    """从作品 chat.json 取一段可见正文（剥 think/illustration/status 等隐藏块）。"""
    raw = json.loads(Path(chat_path).read_text(encoding="utf-8"))
    messages = raw if isinstance(raw, list) else (raw.get("messages") or [])
    candidates = [m for m in messages if m.get("role") == "assistant" and m.get("text")]
    if not candidates:
        raise SystemExit(f"chat 里没有 assistant 正文：{chat_path}")
    if message_id:
        hit = next((m for m in candidates if m.get("id") == message_id), None)
        if hit is None:
            raise SystemExit(f"找不到消息 id={message_id}")
        picked = hit
    else:
        picked = max(candidates, key=lambda m: len(
            image_prompt_extract.visible_narrative_text(m.get("text") or "")))
    visible = image_prompt_extract.visible_narrative_text(picked.get("text") or "").strip()
    # 截断优先落在段落边界，避免把句子劈开
    if chars > 0 and len(visible) > chars:
        cut = visible.rfind("\n\n", 0, chars)
        visible = visible[:cut if cut > chars // 2 else chars].strip()
    meta = {
        "message_id": picked.get("id"), "chars": len(visible), "source": chat_path,
        "status": _parse_status_block(picked.get("text") or ""),
    }
    return visible, meta


def _split_top_level(value: str) -> list[str]:
    """按 `；;、` 切分，但忽略括号内的分隔符（[在场] 括号里常写「若干，端奶瓶、拉裙摆」）。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in value:
        if char in "（(":
            depth += 1
        elif char in "）)":
            depth = max(0, depth - 1)
        if char in "；;、" and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]


def spec_from_status(status: dict[str, str], base: dict) -> dict:
    """把 `<status>` 的在场/地点/衣着落成 scene_spec 样本（生产由 sig + 世界书组装）。"""
    spec = dict(base)
    present = status.get("在场") or ""
    actors: list[str] = []
    appearance: list[str] = []
    for clause in _split_top_level(present):
        clause = clause.strip()
        if not clause:
            continue
        name = re.split(r"[（(]", clause)[0].strip()
        name = re.sub(r"(若干|们|等)$", "", name).strip()
        if not name or name in ("我", "玩家", "{{user}}") or name in actors:
            continue
        actors.append(name)
        # ⛔ 括号里是状态/动作（如「入夜检查功法→睡中」），不是外貌；不能拼进 appearance
        #    并带到每一镜（2026-11-10 用户实锤）。衣着由 [衣着] 行单独承载。
        appearance.append(name)
    if actors:
        spec["actors"] = actors
        spec["appearance"] = "、".join(appearance)
    # 生产的所在键是 `[所在]`（见 agency_scene_state），`[地点]` 只作旧作品兼容。
    # 两者都没有时**必须清空**，不能沿用演示 BASE_SPEC 的「老面馆」——否则真实作品的
    # 场景事实会被假数据覆盖（2026-11-09 多文段实测）。
    locale = status.get("所在") or status.get("地点") or ""
    if locale:
        # `[所在]` 可能写成「礼堂→卧室」的转场链；图片/视频的单镜场景取**当前这一段**，
        # 与生产 `所在` 的单场景口径一致（整条链会把「卧室」混进礼堂画面）。
        spec["locale"] = re.split(r"[→>]", locale)[0].strip() or locale
    else:
        spec["locale"] = ""
    if status.get("衣着"):
        spec["wardrobe"] = status["衣着"]
    elif appearance:
        # 没有 `[衣着]` 行就不编：⛔ 不能把 `[在场]` 括号再塞进 wardrobe，
        # 否则 ③ 会输出「腓特烈（婚纱），腓特烈(婚纱)…」的重复（模拟实锤）。
        spec["wardrobe"] = ""
    return spec


def _chat_model() -> tuple[str, str, str, str]:
    """读用户设置里的当前对话模型与代理（不回显 key）。缺失 ⇒ 抛错，绝不静默换模型。"""
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
    return base, key, model, proxy


_RUNTIME_LLM = {"mode": "mock", "ollama_model": "gemma4:latest"}


def _ollama_chat_model(model_name: str) -> tuple[str, str, str, str]:
    """本机 Ollama（项目支持的本机模型链路）：0 费用，不走付费 key。"""
    return "http://127.0.0.1:11434/v1", "ollama", model_name, ""


STORYBOARD_GENERATION_PREAMBLE = (
    "【分镜编剧任务】\n"
    "你现在以本作品编剧/导演的身份工作，不是在挑一张「高潮图」。\n"
    "把下面正文按自然段逐段转成可拍分镜：每个自然段至少落到一个关键帧，"
    "或明确写进相邻关键帧的 video_beats；动作过程、对白口型、环境/时间变化、"
    "性描写段落都不得跳过。每个关键帧只出一张图；近景/硬切写进该帧 video_beats。"
    "⛔ 单个关键帧最多覆盖 4 个自然段、最多 4 拍；超过 4 段或 4 拍必须拆成多个关键帧，"
    "不能把十几段塞进一镜。\n"
    "所有镜头描述用直白可视语言：拟人/比喻转成可见的光线、位置、动作"
    "（「晨光爬上树冠」→「晨光照在树冠上」），不要照抄文学化句子；"
    "每段 beats 只写本镜时间窗口内的画面，不得把其他段落的对话/动作塞进来；"
    "anchor 必须是画面动作句，不能是纯对白。\n"
    "身体部位必须带归属：镜头角色写「镜头角色的XX」，其他角色写「角色名的XX」；"
    "不得只写「柱身/胸/手/腰」这类裸部位名。\n"
    "只输出严格 JSON（格式见最后的输出合同），不要写解释或正文续写。\n\n"
)


def _numbered_paragraphs(text: str) -> str:
    """按空行给自然段编号，供生成型分镜逐段覆盖（模型输出时能对齐段号）。"""
    import re as _re
    parts = [part.strip() for part in _re.split(r"\n\s*\n", text) if part.strip()]
    return "\n\n".join(f"[段 {index}]\n{part}" for index, part in enumerate(parts))


def _paragraph_list(text: str) -> list[str]:
    import re as _re
    return [part.strip() for part in _re.split(r"\n\s*\n", text) if part.strip()]


def _grams(value: str) -> set[str]:
    import re as _re
    clean = _re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value)
    return {clean[index:index + 2] for index in range(max(0, len(clean) - 1))}


def _anchor_corpus(anchors: list[dict]) -> str:
    parts: list[str] = []
    for anchor in anchors:
        for key in ("anchor", "composition", "framing", "camera_motion",
                    "subject_motion", "sound_cue"):
            parts.append(str(anchor.get(key) or ""))
        for beat in anchor.get("video_beats") or []:
            if not isinstance(beat, dict):
                continue
            for key in ("framing", "camera_motion", "subject_motion", "description", "sound_cue"):
                parts.append(str(beat.get(key) or ""))
    return "".join(parts)


def _paragraph_scores(text: str, anchors: list[dict]) -> list[float]:
    """逐段 2-gram 覆盖率：只算结构，不打印/阅读正文。"""
    corpus = _grams(_anchor_corpus(anchors))
    scores: list[float] = []
    for paragraph in _paragraph_list(text):
        grams = _grams(paragraph)
        scores.append(round(len(grams & corpus) / len(grams), 2) if grams else 1.0)
    return scores


def _gap_groups(scores: list[float], threshold: float = 0.35) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    for index, score in enumerate(scores):
        if score < threshold:
            current.append(index)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _infer_anchor_paragraphs(text: str, item: dict) -> dict:
    """判据漏写 `paragraphs` 时，用 anchor 逐字落段兜底（只认逐字命中）。"""
    if item.get("paragraphs"):
        return item
    anchor = str(item.get("anchor") or "").strip()
    if not anchor:
        return item
    for index, paragraph in enumerate(_paragraph_list(text)):
        if anchor in paragraph:
            item["paragraphs"] = [index]
            break
    return item


def _coverage_gap_batches(
    text: str, items: list[dict], *, size: int = 8, limit: int = 4,
) -> tuple[list[list[int]], dict]:
    """按覆盖账本找未覆盖段，切成连续批次（每批 ≤ size 段，每轮 ≤ limit 批）。"""
    from app.services import scene_illustration

    coverage = scene_illustration.paragraph_coverage(text, items)
    gaps = list(coverage.get("gaps") or [])
    if not gaps:
        return [], coverage
    batches: list[list[int]] = []
    current: list[int] = []
    for index in gaps:
        if current and (index != current[-1] + 1 or len(current) >= size):
            batches.append(current)
            current = []
        current.append(index)
    if current:
        batches.append(current)
    return batches[:limit], coverage


LOCALE_SYSTEM = "你是场记。只回答镜头所在地点（2–12 字），不要解释、不要引号。"


def _extract_shot_locale(
    text: str, anchor: str, base: str, key: str, model: str, proxy: str,
) -> str:
    """专用 locale 小调用：只问「这个镜头开始时在哪」，避免全局结尾场景污染每镜。"""
    pos = text.find(anchor)
    window = text[max(0, pos - 80):pos + len(anchor) + 220] if pos >= 0 else anchor
    user = (
        f"镜头起始句：{anchor}\n"
        f"随后画面：{window}\n"
        "只回答这个镜头开始时所在的地点（2–12 字），不要解释。"
    )
    raw = llm.chat(
        base, key, model, LOCALE_SYSTEM, user,
        temperature=0.0, timeout=30.0, proxy="",
    )
    text_value = (raw or "").strip()
    line = text_value.splitlines()[0].strip() if text_value else ""
    line = line.strip("「」“”\"'。 ")
    line = re.sub(r"^地点[:：]", "", line).strip()
    if not line or len(line) > 20 or any(
        word in line for word in ("无法", "未知", "未提及", "不知道")
    ):
        return ""
    return line


def _mock_judge_raw(path: str) -> tuple[str, str, str, str, str, dict]:
    """离线 mock：不发网络请求。用已有 anchors JSON 模拟「502 重试 + 截断续写」。"""
    fixture = Path(path) if path else Path(r"..\_tmp\review_lingxiao_locale.json")
    if not fixture.exists():
        raise SystemExit(f"mock 锚点文件不存在：{fixture}（用 --mock-anchors 指定）")
    data = json.loads(fixture.read_text(encoding="utf-8"))
    anchors = data.get("anchors") or []
    if not anchors:
        raise SystemExit("mock 锚点为空：请指定含 anchors 的 JSON")
    payload = json.dumps({"anchors": anchors}, ensure_ascii=False)
    state = {"calls": 0, "retries": 0, "continuations": 0}

    def fake_call() -> str:
        state["calls"] += 1
        if state["calls"] == 1:
            # 第一次模拟上游 502，验证退避重试。
            raise RuntimeError("502 upstream timeout（mock）")
        # 第二次返回半截 JSON，验证截断续写。
        return payload[:max(1, len(payload) // 2)]

    def continue_fn(partial: str) -> str:
        state["continuations"] += 1
        return payload[len(partial):]

    raw = shot_reliability.call_with_retry(
        fake_call, attempts=3, backoff=(0.0, 0.0, 0.0),
        on_retry=lambda _n, _exc: state.__setitem__("retries", state["retries"] + 1),
    )
    raw = shot_reliability.complete_json(raw, continue_fn, max_attempts=3)
    return raw, "mock", "", "", "", state


def judge_anchors(
    text: str, *, batch: int, actors: list[str], proxy_override: str = "",
    viewpoint: str = "", preset_dir: str = "", preset_name: str = "",
    mode: str = "generation", llm_mode: str = "mock", mock_anchors_path: str = "",
    ollama_model: str = "gemma4:latest",
) -> tuple[list[dict], dict]:
    """跑判据 LLM（生成型/分析型）或离线 mock，返回锚点与诊断。"""
    mock_state: dict = {}
    if llm_mode == "mock":
        # 默认离线：不发任何网络请求。用已有 anchors JSON 模拟一次 502 + 一次截断续写。
        raw, model, base, key, proxy, mock_state = _mock_judge_raw(mock_anchors_path)
        preset_name = "mock"
        mode_effective = "generation"
        system = ""
        viewpoint_hint = ""
        gap_enabled = False
        locale_enabled = False
    else:
        rubric = illustration_prompt_store.effective_climax_anchor_rubric()
        if not rubric:
            raise SystemExit("判据 rubric 为空")
        if llm_mode == "ollama":
            base, key, model, proxy = _ollama_chat_model(ollama_model)
        else:
            base, key, model, proxy = _chat_model()
        if proxy_override:
            proxy = proxy_override
        system = scene_illustration.anchor_intent_system(rubric, storyboard=True)
        if mode == "generation":
            # 生成型：先编剧任务，再用户判据与输出合同；不是「选一张图」的分析口吻。
            system = STORYBOARD_GENERATION_PREAMBLE + system
        # A 方案（用户 2026-11-10 定案）：判据也带对话预设的防拦截上下文，
        # 与正常图片提示词链（`system_with_preset`）同口径；预设缺失时保持原 system。
        if preset_dir.strip() and preset_name.strip():
            system = image_prompt_profiles.system_with_preset(
                system,
                {"actors": actors, "appearance": "", "narrative": text[:2000]},
                preset_dir=preset_dir, preset_name=preset_name,
                task_label="内部分镜判据任务", head_only=False,
            )
        # 人称提示与生产同源（第一人称才注入；未指定时 user 消息不加该段）。
        from app.services import agency_illustration_actors
        viewpoint_hint = agency_illustration_actors._anchor_intent_viewpoint_hint(
            {"illustration_viewpoint": viewpoint},
        )
        user = "\n\n".join(part for part in (
            viewpoint_hint,
            f"在场角色：{'、'.join(actors)}" if actors else "",
            f"剧情正文（按自然段编号）：\n{_numbered_paragraphs(text)}" if mode == "generation"
            else f"剧情正文：\n{text}",
        ) if part)
        raw = llm.chat(
            base, key, model, system, user,
            temperature=0.2,
            timeout=300.0 if llm_mode == "ollama" else (180.0 if mode == "generation" else 90.0),
            proxy=proxy,
        )
        mode_effective = mode
        gap_enabled = mode == "generation"
        locale_enabled = True
    items = scene_illustration.dedupe_anchor_intents(
        scene_illustration.parse_anchor_intents(raw or ""), text,
    )
    items = [dict(item, original_index=index) for index, item in enumerate(items)]
    if mode_effective != "generation":
        # ⚠ 生成型不能再按 score>0 过滤：非高潮段（环境/过程/性描写）本来就低分，
        # 过滤掉就等于跳过整段——这正是「逐段覆盖」漏段的主因。
        items = [item for item in items if int(item.get("score") or 0) > 0]

    gap_passes = 0
    raw_total = len(raw or "")
    if gap_enabled:
        # 覆盖账本审计：判据漏写的段按 `paragraphs` 找出来，只对缺口段再生成并合并。
        for item in items:
            _infer_anchor_paragraphs(text, item)
        paragraphs = _paragraph_list(text)
        for _ in range(6):
            batches, _coverage = _coverage_gap_batches(text, items)
            if not batches:
                break
            added = 0
            for group in batches:
                gap_text = "\n\n".join(
                    f"[段 {index}]\n{paragraphs[index]}" for index in group
                )
                gap_user = "\n\n".join(part for part in (
                    viewpoint_hint,
                    f"只补以下自然段（段号 {group[0]}–{group[-1]}）的分镜："
                    "每段至少一个关键帧或写进相邻关键帧的 video_beats；"
                    "每个关键帧必须写 `paragraphs`（只列你覆盖的段号）；"
                    "不要重复已有锚点；只输出 anchors JSON。",
                    gap_text,
                ) if part)
                raw_gap = llm.chat(
                    base, key, model, system, gap_user,
                    temperature=0.2,
                    timeout=300.0 if llm_mode == "ollama" else 180.0,
                    proxy=proxy,
                )
                raw_total += len(raw_gap or "")
                new_items = scene_illustration.dedupe_anchor_intents(
                    scene_illustration.parse_anchor_intents(raw_gap or ""), text,
                )
                for item in new_items:
                    _infer_anchor_paragraphs(text, item)
                if new_items:
                    items = scene_illustration.dedupe_anchor_intents(items + new_items, text)
                    added += len(new_items)
            gap_passes += 1
            if added == 0:
                break
        items.sort(key=lambda item: (
            text.find(str(item.get("anchor") or ""))
            if text.find(str(item.get("anchor") or "")) >= 0 else len(text)
        ))
        items = [dict(item, original_index=index) for index, item in enumerate(items)]

    # 全文覆盖：generation 模式不设 12 镜上限（0=不抽样）；显式 batch>0 才封顶。
    items = scene_illustration.coverage_anchor_intents(
        items, batch if batch > 0 else 0,
    )
    coverage = scene_illustration.paragraph_coverage(text, items)
    # 专用 locale 小调用：判据常把 locale 留空，逐镜只问地点（不读正文、不打印）。
    locale_calls = 0
    if mode_effective == "generation" and locale_enabled:
        for item in items:
            if str(item.get("locale") or "").strip():
                continue
            try:
                shot_locale = _extract_shot_locale(
                    text, str(item.get("anchor") or ""), base, key, model, proxy,
                )
            except Exception:  # noqa: BLE001 — 地点识别失败不得打断分镜
                shot_locale = ""
            locale_calls += 1
            if shot_locale:
                item["locale"] = shot_locale
    diag = {
        "model": model,
        "preset": preset_name,
        "mode": "mock" if llm_mode == "mock" else mode,
        "mock_calls": int(mock_state.get("calls") or 0),
        "mock_retries": int(mock_state.get("retries") or 0),
        "mock_continuations": int(mock_state.get("continuations") or 0),
        "cost_cny": 0 if llm_mode == "mock" else None,
        "raw_chars": raw_total,
        "raw_head": (raw or "")[:300],
        "anchor_count": len(items),
        "gap_passes": gap_passes,
        "locale_calls": locale_calls,
        # 覆盖账本（段号级）；旧 2-gram 分数另存，便于对照。
        "coverage": coverage,
        "paragraph_scores": _paragraph_scores(text, items),
        "scores": [int(item.get("score") or 0) for item in items],
    }
    return items, diag


def _scoped_appearance(base_spec: dict, names: list[str]) -> str:
    """从基础外貌串按角色名拆出本镜外貌；拆不出就空（不把别人的卡面塞进来）。

    基础串有两种格式：`示例角色乙：…\\n示例角色甲：…`（逐角色段落）与 `黑桃、苹果、你`
    （只是名单）。后者不是外貌，必须先识别掉；其余交给 `_appearance_by_actor`
    按名字拆（`腓特烈大帝` 也能归到配置名 `腓特烈`）。
    """
    appearance = str(base_spec.get("appearance") or "")
    roster = {str(name).strip() for name in (base_spec.get("actors") or [])}
    listed = [part.strip() for part in re.split(r"[、,，]", appearance) if part.strip()]
    if listed and all(part in roster for part in listed):
        return ""
    mapped = video_prompt._appearance_by_actor(appearance, names) if names else {}
    return "\n".join(f"{name}：{mapped[name]}" for name in names if mapped.get(name))


def _spec_for(
    item: shot_plan.ShotPlanItem, anchor: dict, narrative: str, base_spec: dict,
    wardrobe_by_actor: dict[str, str] | None = None,
) -> dict:
    """按锚点的 subjects 收窄在场角色（生产 `subjects_override` 同口径）。"""
    scene_anchor = str(item.scene_anchor or anchor.get("scene_anchor") or "").strip()
    spec = {
        **base_spec,
        "narrative": narrative,
        "shot_duration_s": item.duration_s,
        "shot_narrative": narrative,
        "action_sequence": DEMO_ACTIONS.get(item.start_anchor, []),
        "shot_framing": item.framing,
        "shot_camera_motion": item.camera_motion,
        "shot_subject_motion": item.subject_motion,
        "shot_sound_cue": item.sound_cue,
        # 图片相对本镜的位置（start/mid/end）：中段/尾帧必须在视频提示词里交代清楚。
        "shot_image_timing": item.image_timing,
        # 图片对应的**具体秒数**（用户 2026-11-10：必须交代图片在本镜第几秒）。
        "image_time_s": item.image_time_s,
        # 每镜地点：优先判据给的 locale，空则回落全局（用户实锤：全局会把结尾卧房带进剑场）。
        "locale": item.locale or base_spec.get("locale") or "",
        # 关键帧内部时间轴：近景/硬切不再单独出图。
        "shot_video_beats": item.video_beats,
        # 全自动连续锚点：同场景空间地标 + 同角色服装（由 shot_plan 从正文/状态块派生）。
        "scene_anchor": scene_anchor,
    }
    known = set(base_spec.get("actors") or [])
    subjects = [str(name).strip() for name in (anchor.get("subjects") or [])]
    scoped = [name for name in subjects if name in known]
    if item.shot_type == "establish":
        # 环境空镜默认不选主要角色/外貌；但锚点/上下文已解析出**具体角色**时
        # （如「她把剑搁在门后」→ 示例角色甲），必须保留该角色的条目外貌，否则图片
        # 只剩「A woman」，用不上银发/狐耳/眼罩等设定（用户 2026-11-11 实锤）。
        spec.update({
            "establish": True,
            "actors": scoped,
            "appearance": _scoped_appearance(base_spec, scoped),
            "wardrobe": str(base_spec.get("wardrobe") or "") if scoped else "",
            "composition": item.framing or "远景/环境空镜",
        })
        if item.camera_motion:
            spec["camera"] = item.camera_motion
        spec["wardrobe_anchor"] = ""
        return spec
    if subjects:
        # 锚点点名谁，本镜主体就是谁：未进配置的角色（红发的女人/孩子们/蛮啾）保留原名，
        # ⛔ 不能回退成整本角色配置——否则画面会被替换成配置名单里的主角团，锚点人物消失
        # （2026-11-11 审稿实锤：短开场镜2 锚点是「红发的女人」，提示词却写黑桃/苹果/你）。
        spec["actors"] = subjects
        spec["appearance"] = _scoped_appearance(base_spec, scoped)
        if not scoped:
            # 没有条目外貌可继承：交给正文/状态事实，别把配置名单的卡面塞给锚点人物。
            spec["wardrobe"] = ""
    if item.framing:
        spec["composition"] = item.framing
    if item.camera_motion:
        spec["camera"] = item.camera_motion
    # 第一人称：已知主人公＝镜头，不能出现在任何主体列表里（2026-11-10 用户实锤：
    # 「主人公的肩入画」）。只按姓名剔除，不动正文；第三人称不生效。
    if str(base_spec.get("viewpoint") or "") == "first_person":
        narrator = str(base_spec.get("narrator") or "").strip()
        if narrator:
            for key in ("actors", "subjects", "frame_subjects"):
                value = spec.get(key)
                if isinstance(value, list):
                    spec[key] = [name for name in value if name != narrator]
    spec["wardrobe_anchor"] = shot_plan.wardrobe_anchor_for(
        [str(name) for name in (spec.get("actors") or []) if str(name).strip()],
        wardrobe_by_actor,
    )
    return spec


def _window(text: str, item: shot_plan.ShotPlanItem, nxt: shot_plan.ShotPlanItem | None) -> str:
    """该镜在正文里的窗口文本（图片/视频共用同一段叙事）。"""
    start = item.start_offset
    if start is None:
        return item.start_anchor
    end = item.end_offset if item.shot_type == "move" else (
        nxt.start_offset if nxt is not None and nxt.start_offset is not None else len(text)
    )
    return (text[start:end] if end is not None else text[start:]).strip()


def _llm_generate_text(proxy_override: str = "") -> callable:
    if _RUNTIME_LLM["mode"] == "ollama":
        base, key, model, proxy = _ollama_chat_model(str(_RUNTIME_LLM["ollama_model"]))
        timeout = 300.0
    else:
        base, key, model, proxy = _chat_model()
        timeout = 120.0
    if proxy_override:
        proxy = proxy_override

    def generate_text(system: str, user: str) -> str:
        return llm.chat(
            base, key, model, system, user, temperature=0.4, timeout=timeout, proxy=proxy,
        )

    return generate_text


# 图片 Profile 只该看到「图片时刻」这一拍：beats / 主体运动 / 触发声属于视频，
# 传进图片模型会把整段 15s 的动作都画进首帧（2026-11-10 镜0 实锤）。
_IMAGE_SPEC_EXCLUDE = (
    "shot_video_beats", "shot_subject_motion", "shot_sound_cue",
    "shot_framing", "shot_camera_motion", "shot_narrative", "role_refs",
)


def _image_prompt_spec(spec: dict) -> dict:
    """与生产 `_wb_comfy_scene_spec` 同口径：图片只拿这一刻的叙事与画面事实。"""
    return {key: value for key, value in spec.items() if key not in _IMAGE_SPEC_EXCLUDE}


_ANIMA_SENTENCE_STARTS = (
    "the ", "a ", "an ", "she ", "he ", "her ", "his ", "in the ", "this ",
    "it ", "they ", "as ", "at ", "with ", "after ", "before ", "from ",
    "on ", "while ", "when ", "there ", "we ", "since ", "because ",
    "although ", "if ", "just ",
)
# 句子连接/介词/副词开头 ⇒ 是散文碎片，不是 tag。
_ANIMA_TAG_STOP_STARTS = (
    "and ", "or ", "but ", "that ", "which ", "who ", "of ", "to ", "for ",
    "by ", "into ", "over ", "under ", "is ", "are ", "was ", "were ",
    "gently ", "slowly ", "softly ", "slightly ", "almost ",
)
# 图片是单帧结果：过程 tag 不应留在首行（姿势 tag 如 standing/sitting 保留）。
_ANIMA_PROGRESSIVE_TAGS = frozenset({
    "walking", "entering", "turning", "reaching", "running", "moving",
    "approaching", "leaving", "standing up", "sitting down", "getting up",
})
# 模型偶尔把「负面提示词」整段写进正向 tags（真机复审：luoluo 镜4 首行混入
# cartoon/painting/blurry/out of focus…）。这些词在正向 tags 里只会压制画面。
_ANIMA_NEGATIVE_TAGS = frozenset({
    "cartoon", "drawing", "painting", "blurry", "out of focus", "lowres",
    "worst quality", "bad hands", "bad anatomy", "cropped at the bottom edge",
    "foreground objects blocking view", "deformed", "extra limbs", "mutated",
    "out of frame", "cropped", "low quality", "sketch", "watermark", "signature",
    "multiple people", "multiple characters", "looking at viewer", "narrator visible",
})


def _strip_prompt_scaffolding(text: str) -> str:
    """去掉模型稿的 markdown 小标题/负面提示词段（只删结构残渣，不改画面内容）。

    负面段按**行**切：模型有时把 `Negative Prompt:` 写在画面描述之前的同一行，
    整段切会把后面的正文一起吞掉（真机复审：凌霄镜1 只剩一行 tag，守卫无法重建）。
    """
    kept: list[str] = []
    for line in text.splitlines():
        kept.append(re.split(r"(?i)\bnegative prompt\s*[:：]", line, maxsplit=1)[0])
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\*{2,}\[[^\]]*\]\*{2,}", " ", cleaned)
    cleaned = re.sub(r"\[[A-Z][^\]\n]{0,40}\]", " ", cleaned)
    cleaned = re.sub(r"\*{2,}", "", cleaned)
    cleaned = re.sub(r"\(\s*\)", " ", cleaned)
    return cleaned


def _anima_format_guard(prompt: str, rating: str = "sfw") -> tuple[str, bool]:
    """把模型稿强制回 anima 两行结构（只修格式，不改内容）。

    本机 gemma4 常把散文混进第一行、带 `****`/`**Prompt**`/空括号，甚至把同一段
    重复两遍（`kept_content` 会原样保留）。这里只做结构归一：
    第一行＝质量词 + 短 tag（过滤句子碎片），第二行＝最长的一段英文画面描述。
    返回 `(提示词, 是否修过)`；修不了就原样返回。
    """
    original = (prompt or "").strip()
    text = _strip_prompt_scaffolding(re.sub(r"\s+([.?!])", r"\1", original))
    if not text.strip():
        return prompt, False
    has_artifact = bool(re.search(r"\*{2,}|\(\s*\)|^\s*(?:Prompt|###|Scene)\b", original, re.M))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = lines[0] if lines else ""
    mixed_first = any(len(segment.split()) > 5 for segment in first.split(","))
    if not has_artifact and len(lines) >= 2 and not mixed_first:
        return text, text != original
    prose = max(
        (line for line in lines if re.search(r"[.?!]", line)),
        key=len, default="",
    )
    prose = prose.split("**Prompt**")[0]
    # 模型常在文段末尾再用反引号罗列一遍 tags（真机复审：凌霄镜1）；截到第一个反引号，
    # 避免「文段机械复述 tags」污染成图模型。
    prose = re.split(r"\s*`[^`\n]+`", prose)[0]
    prose = re.sub(r"\*{2,}", "", prose)
    prose = re.sub(r"\(\s*\)", "", prose)
    prose = re.sub(r"\s+", " ", prose).strip(" ,")
    if not prose or not re.search(r"[A-Za-z]{3}", prose):
        return prompt, False
    candidates: list[str] = []
    for line in lines:
        for segment in re.split(r",|\n", line):
            tag = segment.strip().strip("*_.`- ")
            tag = re.sub(r"\(\s*\)", "", tag).strip()
            if not tag or not tag.isascii() or len(tag) > 48 or len(tag.split()) > 5:
                continue
            low = tag.lower()
            if (re.search(r"[.!?;:]", tag) or low.startswith(_ANIMA_SENTENCE_STARTS)
                    or low.startswith(_ANIMA_TAG_STOP_STARTS)):
                continue
            if any(marker in low for marker in (
                "prompt", "****", "###", "scene block", "self-correction",
                "scene description", "style & tone", "check",
            )):
                continue
            # 模型把结构标签写进首行：`[Scene Description]`、`(Self-Correction...)` 一律不留在 tag。
            if re.search(r"[\[\]{}()]", tag):
                continue
            if low in _ANIMA_PROGRESSIVE_TAGS:
                continue
            if low in _ANIMA_NEGATIVE_TAGS:
                continue
            candidates.append(tag)
    tags = [
        tag.strip() for tag in image_prompt_profiles.anima_quality_tags(rating).split(",")
        if tag.strip()
    ]
    for tag in candidates:
        if tag.lower() not in {item.lower() for item in tags}:
            tags.append(tag)
    if len(tags) < 6:
        return prompt, False
    repaired = f"{', '.join(tags)}\n{prose}"
    # 已经就是规范两行 ⇒ 不算修过，避免把原稿标成修复稿。
    changed = repaired.strip() != text.strip()
    return repaired, changed


def _resolve_generic_subjects(
    subjects: list[str] | tuple[str, ...] | None,
    text: str,
    configured: list[str],
    aliases: dict[str, list[str]] | None,
) -> list[str]:
    """把「她 / the primary adult character」这类泛称解析成配置角色。

    用户 2026-11-10：正文只写「她」时，仍应能按上下文认出示例角色甲并使用其角色条目外貌。
    规则很保守：只有**当前主体全是泛称/为空**且上下文只命中**唯一**一个配置角色时才替换；
    多角色命中或没有命中 ⇒ 原样返回，绝不猜。
    """
    names = [str(name).strip() for name in (subjects or []) if str(name).strip()]
    generic = {
        "她", "他", "你", "我", "她们", "他们", "女人", "男人", "女孩", "男孩",
        "the primary adult character", "primary adult character", "主角", "主人公",
    }
    if names and any(name not in generic for name in names):
        return names
    hits: list[str] = []
    for name in configured:
        candidates = [name, *(aliases or {}).get(name, [])]
        if any(candidate and candidate in (text or "") for candidate in candidates):
            hits.append(name)
    return [hits[0]] if len(hits) == 1 else names


def simulate(
    text: str, anchors: list[dict], total_s: int, caps_duration: int,
    *, base_spec: dict, profile: str = "anima_tags", profile_llm: bool = False,
    proxy_override: str = "", image_prompts: dict[int, str] | None = None,
    character_base_images: dict[str, str] | None = None,
    character_configured: list[str] | None = None,
    character_image_mode: str = "text2img",
    video_character_refs: bool = True,
    character_aliases: dict[str, list[str]] | None = None,
    video_subject_scenes: dict[int, str] | None = None,
    present: str = "",
) -> dict:
    main_actors = list(character_configured or base_spec.get("actors") or [])
    configured_actors = [str(name) for name in main_actors if str(name).strip()]
    anchors = shot_plan.prepare_storyboard_anchors(anchors, text, main_actors)
    plan = shot_plan.build_shot_plan(
        anchors, text, sequence_id="sim", total_s=total_s, caps_duration=caps_duration,
        main_actors=main_actors, present=present,
    )
    caps = {"duration": caps_duration} if caps_duration > 0 else None
    video_config = {
        "base_url": "http://127.0.0.1:8188", "model": "MiniMax H3", "size": "1280x720",
    }
    generate_text = _llm_generate_text(proxy_override) if profile_llm else None
    shots: list[dict] = []
    for index, item in enumerate(plan.items):
        nxt = plan.items[index + 1] if index + 1 < len(plan.items) else None
        video_narrative = _window(text, item, nxt)
        anchor = anchors[index] if index < len(anchors) else {}
        # 与生产/高潮点同口径：图片提示词吃**锚点所在段落摘录**（含段落附近信息），
        # 不在这里另造「单句时刻」输入；镜头差异由锚点/景别/主体与图片位置承担。
        try:
            # 分镜按**锚点段落**取素材：传 subjects 会让摘录回扫到该角色首次出现的
            # 场景起点，同段多镜/后续镜全部撞到第一段（用户 2026-11-10 实锤：镜3 重合）。
            image_narrative = scene_illustration.illustration_scene_excerpt(
                text, item.start_anchor, expand_context=False,
            ) or item.start_anchor
        except Exception:  # noqa: BLE001 纯兜底：摘录失败就用锚点句本身
            image_narrative = item.start_anchor
        # 段落全文保留，只追加「本帧落点」；与生产 build_shot_requests 同口径。
        image_narrative = shot_plan.with_frame_focus(
            image_narrative, item.start_anchor, item.image_time_s,
        )
        # 正文只写「她 / the primary adult character」时，按上下文解析到唯一配置角色；
        # 解析结果同时用于图片 spec（角色条目外貌）与视频出场名单。
        cast_scan_text = "\n".join([video_narrative, image_narrative, item.start_anchor])
        resolved_subjects = _resolve_generic_subjects(
            anchor.get("subjects"), cast_scan_text, configured_actors, character_aliases,
        )
        spec = _spec_for(
            item, {**anchor, "subjects": resolved_subjects}, image_narrative, base_spec,
            plan.wardrobe_by_actor,
        )
        prompt_warnings: list[str] = []
        prompt_spec = _image_prompt_spec(spec)
        # 出场角色 ↔ 视频角色参考图（图片2 起步）：只要角色在**视频画面里出场**
        # （提示词 actors / 锚点点名 / 帧主体 / 本镜正文点名）且配了底图，就按出场顺序
        # 作为图片2、图片3…；环境镜 actors 被清空但锚点点名了角色时也照样绑定。
        shot_actors = [str(name) for name in (spec.get("actors") or []) if str(name).strip()]
        shot_subjects = [
            str(name) for name in resolved_subjects if str(name).strip()
        ]
        frame_subjects = [
            str(name) for name in (spec.get("frame_subjects") or spec.get("subjects") or [])
            if str(name).strip() in configured_actors
        ]
        # 与生产同一条纯函数：点名扫描只认锚点句 + 本镜时间分镜；整段 narrative
        # 会把相邻段落里才出场的角色提前绑成图片2（示例作品甲镜0 实锤）。
        video_cast = shot_plan.shot_cast_names(
            shot_actors, shot_subjects, frame_subjects, configured_actors,
            anchor_text=item.start_anchor, beats=item.video_beats,
        )
        # 出场名单以**锚点点名**为准（没有 subjects 才退回视频出场名单）：
        # 锚点写的是孩子们/女管事时，提示词里却带着配置主角 ⇒ 由 bleed 单独报出。
        display_characters = list(dict.fromkeys(
            shot_subjects if shot_subjects else video_cast
        ))
        # 视频角色参考图与**图片生成模式无关**：文生图只是图片侧不送底图，
        # 视频侧仍靠角色底图锁外貌（用户 2026-11-10 口径）。
        use_character_images = bool(video_character_refs)
        base_images = (
            dict(character_base_images or {}) if use_character_images else {}
        )
        # 缺底图的角色**不占图片编号**：视频只上传真有底图的角色，若把缺底图的也预排
        # 成图片2，后面有底图的角色就会写成图片3，与真实请求错位（示例作品甲镜0 实锤：
        # 蛮啾无底图却占图片2、腓特烈变成图片3，而视频提示词只声明图片2=腓特烈）。
        character_refs, missing_character_refs = shot_plan.bind_character_images(
            video_cast, base_images, include_missing=False,
        )
        if not use_character_images:
            missing_character_refs = []
        # 视频只声明**真会上传**的角色底图（有底图的角色）；缺底图的走
        # `missing_character_refs` 供审阅标「未配置」，既不占编号也不写进视频提示词。
        video_role_refs = [
            str(ref.get("name") or "") for ref in character_refs if ref.get("has_base_image")
        ]
        if video_role_refs:
            spec["role_refs"] = video_role_refs
        bound_names = {
            str(ref.get("name") or "") for ref in character_refs if ref.get("has_base_image")
        }
        generic_subjects = {"你", "我", "他", "她", "他们", "她们", "大家", "众人"}
        character_audit = {
            "configured": configured_actors,
            "image_mode": character_image_mode,
            "video_character_refs": use_character_images,
            "base_images_used": bool(use_character_images and base_images),
            "shot_subjects": shot_subjects,
            "raw_subjects": [
                str(name) for name in (anchor.get("subjects") or []) if str(name).strip()
            ],
            "resolved_subjects": shot_subjects,
            "prompt_actors": shot_actors,
            "video_cast": video_cast,
            # 锚点里点名、但不在多元数据插入角色配置里的（群演/描述性别名）。
            "unknown_subjects": [
                name for name in shot_subjects
                if name not in configured_actors and name not in generic_subjects
            ],
            # 锚点已经点名了别人，提示词却仍带着配置里的其他角色 ⇒ 角色串场。
            "prompt_actors_not_in_subjects": [
                name for name in shot_actors
                if shot_subjects and name not in shot_subjects
            ],
            # 出场但没绑上底图（环境镜/未进配置）：名单里要出现，但视频不写图片N。
            "unbound_characters": [
                name for name in list(dict.fromkeys([*display_characters, *video_cast]))
                if name not in bound_names
            ],
        }
        prompt_diag: dict = {}
        reuse_prompt = (image_prompts or {}).get(item.shot_index)
        if reuse_prompt:
            # 位置/视频提示词重算时沿用上次图片成稿，不重复烧 LLM。
            # 仍须过一遍当前字段账本：角色条目/规则表更新后，旧稿缺的外观事实由
            # `complete_field_coverage` 补上（与生产 `generate` 同一条链），否则复用稿
            # 会永远停在旧规则（2026-11-11 凌霄镜2/4/9/10 缺银发/狐耳/眼罩实锤）。
            image_prompt, _coverage = image_prompt_profiles.complete_field_coverage(
                profile, reuse_prompt, prompt_spec,
            )
            image_source = "reuse（沿用上次成稿 · 字段账本补齐）"
            image_strategy = "reuse"
        elif generate_text is not None:
            try:
                # 图片提示词一律走生产 Profile（用户模板/两行 schema/字段账本），
                # 不另造旁路；本机模型没通过时由 Profile 自己降级并记录 strategy。
                image_prompt = image_prompt_profiles.generate(
                    profile, prompt_spec, generate_text,
                    diagnostics=prompt_diag, allow_repair=False,
                )
                image_source = f"LLM Profile（{profile}）"
                image_strategy = str(prompt_diag.get("strategy") or "")
                image_prompt, format_repaired = _anima_format_guard(
                    image_prompt, str(prompt_spec.get("rating") or "sfw"),
                )
                if format_repaired:
                    image_strategy = "format_repair"
            except Exception as exc:  # noqa: BLE001 模拟可降级：失败退回确定性样本并明说
                image_prompt = image_prompt_profiles.deterministic_fallback(profile, prompt_spec)
                image_source = "deterministic_fallback（Profile LLM 失败）"
                image_strategy = "error"
                prompt_warnings.append(f"图片提示词 LLM 失败：{type(exc).__name__}: {exc}")
        else:
            image_prompt = image_prompt_profiles.deterministic_fallback(profile, prompt_spec)
            image_source = "deterministic_fallback（结构样本，非生产成稿）"
            image_strategy = "deterministic"

        scene_override = str((video_subject_scenes or {}).get(item.shot_index) or "").strip()
        if item.shot_type == "establish":
            spec["shot_camera"] = item.transition_desc
            if scene_override:
                # ③ 只写「角色名（当前可见状态）+ 场景」；外貌由图片1承载。
                spec["video_subject_scene"] = scene_override
            video = video_prompt.build_video_request(
                mode=("shot_establish_move" if item.transition_desc else "shot_establish"),
                spec=spec, video_config=video_config, pending_frame=True, caps=caps,
            )
        elif item.shot_type == "cut":
            if scene_override:
                spec["video_subject_scene"] = scene_override
            video = video_prompt.build_video_request(
                mode="shot_cut", spec=spec, video_config=video_config,
                pending_frame=True, caps=caps,
            )
        elif item.shot_type == "move_single":
            spec["shot_camera"] = item.transition_desc
            if scene_override:
                spec["video_subject_scene"] = scene_override
            video = video_prompt.build_video_request(
                mode="shot_move_single", spec=spec, video_config=video_config,
                pending_frame=True, caps=caps,
            )
        else:
            # move：视频挂在下一锚点；④ 演两锚点之间的正文，运镜路径只进 ①/camera
            #（与生产 build_shot_requests 同口径；⛔ 不把路径当叙事逐拍重复）。
            move_spec = {
                **spec,
                "narrative": video_narrative,
                "shot_camera": item.transition_desc,
                "shot_duration_s": item.duration_s,
            }
            if scene_override:
                move_spec["video_subject_scene"] = scene_override
            video = video_prompt.build_video_request(
                mode="shot_move", spec=move_spec, video_config=video_config,
                first_frame_desc=item.start_anchor,
                last_frame_desc=item.end_anchor,
                pending_frame=True, caps=caps,
            )

        beat_text = "\n".join(
            f"{beat.get('subject_motion') or ''}；{beat.get('description') or ''}"
            for beat in (item.video_beats or []) if isinstance(beat, dict)
        )
        ownership_errors = shot_plan.ownership_errors(
            beat_text,
            cast=display_characters,
            require_named=shot_plan.nsfw_requires_named(
                beat_text, str(spec.get("rating") or ""),
            ),
        )
        if ownership_errors:
            prompt_warnings.extend(ownership_errors)

        shots.append({
            "shot_index": item.shot_index,
            "shot_type": item.shot_type,
            "video_mode": item.video_mode,
            "duration_s": item.duration_s,
            "image_key": item.image_key,
            "end_image_key": item.end_image_key,
            "start_anchor": item.start_anchor,
            "end_anchor": item.end_anchor,
            "transition_desc": item.transition_desc,
            "framing": item.framing,
            "camera_motion": item.camera_motion,
            "subject_motion": item.subject_motion,
            "sound_cue": item.sound_cue,
            "image_timing": item.image_timing,
            "image_time_s": item.image_time_s,
            "image_narrative": image_narrative,
            "locale": item.locale,
            # 全自动连续锚点：审阅稿要能直接看到这一镜拿了哪个空间/服装锚点。
            "scene_key": item.scene_key,
            "scene_anchor": item.scene_anchor,
            "wardrobe_anchor": str(spec.get("wardrobe_anchor") or ""),
            "characters": display_characters,
            "character_refs": character_refs,
            "missing_character_refs": missing_character_refs,
            "character_audit": character_audit,
            "ownership_errors": ownership_errors,
            "paragraphs": list(item.paragraphs),
            "video_beats": item.video_beats,
            "video_subject_scene": str(spec.get("video_subject_scene") or ""),
            "video_slot": (
                f"下一锚点 {item.shot_index + 1}:move" if item.shot_type == "move"
                else f"{item.shot_index}:video"
            ),
            "image_prompt": image_prompt,
            "image_source": image_source,
            "image_strategy": image_strategy,
            "video_prompt": video["submit"]["prompt"],
            "video_seconds": video["submit"]["duration"],
            "video_images": video["submit"]["images"],
            "warnings": [*plan.warnings, *prompt_warnings, *video["warnings"]],
        })
    return {
        "sequence_id": plan.sequence_id,
        "total_shots": plan.total_shots,
        "total_duration_s": plan.total_duration_s,
        "warnings": plan.warnings,
        "shots": shots,
        "anchors": anchors,
        # 场景卡：空场景环境图的上游；每个 scene_key 一张，含建筑/陈设/方位/光线/机位。
        "scene_cards": plan.scene_cards,
        # 审阅稿要按段贴出「这一镜的正文出处」，随 JSON 一起带走。
        "story": text,
    }


def render_markdown(result: dict, text: str, meta: dict) -> str:
    lines = [
        "# 全局模式离线分镜模拟",
        "",
        "> 不提交 ComfyUI；分镜与视频提示词来自生产同款纯函数。",
        f"> 正文来源：{meta.get('source_label') or '内置演示剧情'}"
        f"（{meta.get('chars')} 字{', message=' + str(meta['message_id']) if meta.get('message_id') else ''}）",
        f"> 锚点来源：{meta.get('anchor_source') or '内置演示锚点'}",
    ]
    judge = meta.get("judge")
    if judge:
        lines += [
            f"> 判据模型：{judge.get('model')}；方式：{judge.get('mode') or 'analysis'}；"
            f"预设：{judge.get('preset') or '无'}；"
            f"原始输出 {judge['raw_chars']} 字；"
            f"解析锚点 {judge['anchor_count']} 条；score={judge['scores']}",
        ]
    lines += [
        "",
        f"- 镜头数：**{result['total_shots']}**，总时长：**{result['total_duration_s']}s**",
        f"- 图片数：**{result['total_shots']}** 张（每锚点一张）",
        f"- 视频数：**{result['total_shots']}** 支（每锚点出发一支；move 由下一锚点承载）",
        f"- 类型分布：move **{sum(1 for s in result['shots'] if s['shot_type'] == 'move')}** / "
        f"move_single **{sum(1 for s in result['shots'] if s['shot_type'] == 'move_single')}** / "
        f"cut **{sum(1 for s in result['shots'] if s['shot_type'] == 'cut')}**",
        f"- 画面人称：**{meta.get('viewpoint') or '自动/未指定'}**",
    ]
    spec = meta.get("spec") or {}
    if spec:
        lines.append(
            f"- 画面事实：在场 **{'、'.join(spec.get('actors') or [])}**；"
            f"地点 **{spec.get('locale') or '—'}**",
        )
    lines += [
        "",
        "## 分镜总览",
        "",
        "| 镜 | 类型 | 时长 | 地点 | 景别/机位 | 视频槽 | 视频模式 | 起始锚点 | 终点锚点 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for shot in result["shots"]:
        lines.append(
            f"| {shot['shot_index']} | {shot['shot_type']} | {shot['duration_s']}s | "
            f"{shot.get('locale') or '—'} | "
            f"{shot['framing'] or '—'} / {shot['camera_motion'] or '—'} | "
            f"{shot['video_slot']} | {shot['video_mode']} | "
            f"{shot['start_anchor'][:24]} | {shot['end_anchor'][:24] or '—'} |"
        )
    if result["warnings"]:
        lines += ["", "## 降级/时长 warnings", ""]
        lines += [f"- {warning}" for warning in result["warnings"]]

    if judge:
        lines += ["", "## 判据原始输出（首段）", "", "```text", judge["raw_head"], "```"]

    lines += ["", "## 逐镜详情", ""]
    timing_labels = {"start": "首帧", "mid": "中段", "end": "尾帧", "at": "非首帧（该段起点）"}
    for shot in result["shots"]:
        timing = str(shot.get("image_timing") or "start")
        lines += [
            f"### 镜 {shot['shot_index']} · {shot['shot_type']} · {shot['duration_s']}s",
            "",
            f"- 起始锚点：`{shot['start_anchor']}`",
            f"- 图片位置：**第 {shot.get('image_time_s') or 0} 秒**"
            f"（{timing_labels.get(timing, timing)}）",
            f"- 画面时刻（中文原文）：{shot.get('image_narrative')}"
            if shot.get("image_narrative") else "",
            f"- 地点：`{shot.get('locale')}`" if shot.get("locale") else "",
            f"- 终点锚点：`{shot['end_anchor']}`" if shot["end_anchor"] else "",
            f"- 运镜描述：{shot['transition_desc']}" if shot["transition_desc"] else "",
            f"- 景别/角度：{shot['framing']}" if shot["framing"] else "",
            f"- 机位运动：{shot['camera_motion']}" if shot["camera_motion"] else "",
            f"- 主体运动：{shot['subject_motion']}" if shot["subject_motion"] else "",
            f"- 触发声/事件：{shot['sound_cue']}" if shot["sound_cue"] else "",
            f"- 视频落槽：`{shot['video_slot']}`",
        ]
        beats = shot.get("video_beats") or []
        if beats:
            lines += ["", "**关键帧内部视频时间轴（段间硬切，不再单独生图）**", ""]
            cursor = 0
            for index, beat in enumerate(beats):
                duration = int(beat.get("duration_s") or 2)
                start, end = cursor, cursor + duration
                cursor = end
                parts = []
                if index > 0 and beat.get("cut"):
                    parts.append("硬切")
                for key in ("framing", "camera_motion", "subject_motion", "description"):
                    value = str(beat.get(key) or "").strip()
                    if value:
                        parts.append(value)
                sound = str(beat.get("sound_cue") or "").strip()
                if sound:
                    parts.append(f"声音：{sound}")
                lines.append(f"- {start}–{end}s｜" + "；".join(parts))
        lines += [
            "",
            f"**图片提示词（{shot['image_source']}）**",
            "",
            "```text",
            shot["image_prompt"],
            "```",
            "",
            f"**视频提示词（{shot['video_mode']}，{shot['video_seconds']}s）**",
            "",
            "```text",
            shot["video_prompt"],
            "```",
            "",
        ]
    lines += ["## 剧情正文", "", "```text", text, "```", ""]
    return "\n".join(line for line in lines if line is not None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=0,
                        help="整楼视频总时长预算（秒；0=按镜头数/正文自动估算，不设 30s 上限）")
    parser.add_argument("--caps-duration", type=int, default=0,
                        help="模板固定时长（>0 时每镜按该值，总时长＝镜数×值）")
    parser.add_argument("--chat", default="", help="作品 chat.json 路径（取一段 assistant 正文）")
    parser.add_argument("--message", default="", help="指定消息 id（缺省取最长可见正文）")
    parser.add_argument("--chars", type=int, default=0,
                        help="正文截断字数（0=不截断，默认；长楼按全文分镜）")
    parser.add_argument("--judge", action="store_true", help="用生产判据 LLM 现场判定锚点")
    parser.add_argument("--batch", type=int, default=0,
                        help="覆盖式分镜硬上限（0=默认 12；不再是按分数取前 N）")
    parser.add_argument("--anchors", default="", help="锚点 JSON 文件（与 --chat 配套）")
    parser.add_argument("--spec", default="", help="scene_spec JSON 覆盖（appearance/locale/actors 等）")
    parser.add_argument("--viewpoint", default="", choices=["", "first_person", "third_person"],
                        help="画面人称（默认自动判定；第一人称＝镜头是叙事者眼睛，叙事者不入画）")
    parser.add_argument("--protagonist", default="",
                        help="主人公姓名（第一人称下视作镜头，从 subjects/actors 中剔除）")
    parser.add_argument("--preset-dir", default="", help="防拦截预设目录（默认取设置 presetDir）")
    parser.add_argument("--preset-name", default="", help="防拦截预设名（默认取设置 activePresetName）")
    parser.add_argument("--no-preset", action="store_true",
                        help="不套对话预设（本机小上下文模型对照测试用）")
    parser.add_argument("--judge-mode", default="generation", choices=["generation", "analysis"],
                        help="分镜生成方式：generation=编剧逐段分镜（默认）；analysis=旧判据选点")
    parser.add_argument("--llm", default="mock", choices=["mock", "ollama", "live"],
                        help="mock=离线模拟（默认，0 费用）；ollama=本机模型（0 费用）；"
                             "live=设置里的付费 key（会计费）")
    parser.add_argument("--ollama-model", default="gemma4:latest",
                        help="--llm ollama 时使用的本机模型名（ollama list 可见）")
    parser.add_argument("--mock-anchors", default="",
                        help="mock 用的 anchors JSON（默认 ..\\_tmp\\review_lingxiao_locale.json）")
    parser.add_argument("--profile", default="anima_tags", help="图片提示词 Profile")
    parser.add_argument("--profile-llm", action="store_true", help="图片提示词走生产 Profile LLM")
    parser.add_argument("--dump-raw", default="", help="把判据原始输出写到此文件")
    parser.add_argument("--proxy", default="", help="覆盖 LLM 代理（缺省按设置里的代理）")
    parser.add_argument("--out", default=str(BACKEND.parent / "_tmp" / "shot_plan_simulation.md"))
    parser.add_argument("--json-out", default=str(BACKEND.parent / "_tmp" / "shot_plan_simulation.json"))
    parser.add_argument("--scene-refs", action="store_true",
                        help="为每个场景提交 ComfyUI 文生图，产出「空场景环境图」")
    parser.add_argument("--scene-llm", action="store_true",
                        help="用对话模型从正文补全场景内容/结构/排列（需 --llm live 或 ollama）")
    parser.add_argument("--scene-template", default="",
                        help="空场景图用的文生图模板 id（--scene-refs 必填）")
    parser.add_argument("--scene-url", default="",
                        help="ComfyUI 地址（默认取设置 comfyuiUrl）")
    parser.add_argument("--scene-out", default="",
                        help="场景图输出目录（默认 _tmp/scene_refs/<作品目录>）")
    parser.add_argument("--scene-lora", default="", help="场景图风格 LoRA 文件名（可选）")
    parser.add_argument("--scene-lora-weight", type=float, default=0.8)
    parser.add_argument("--scene-width", type=int, default=1024,
                        help="场景图宽（默认 1024；与 --scene-height 组成 16:9）")
    parser.add_argument("--scene-height", type=int, default=576,
                        help="场景图高（默认 576）")
    parser.add_argument("--scene-style", default="",
                        help="追加到场景提示词的风格/质量串（可选）")
    parser.add_argument("--scene-force", action="store_true", help="已有场景图也重新生成")
    args = parser.parse_args()

    meta: dict = {"source_label": "", "chars": len(DEMO_TEXT), "anchor_source": "", "judge": None}
    present = ""

    if args.chat:
        text, chat_meta = load_chat_text(args.chat, args.message, args.chars)
        meta.update(chat_meta)
        meta["source_label"] = args.chat
        # `<status>` 是生产 sig 的同源快照：先落真实在场/地点/衣着，再允许 --spec 覆盖。
        base_spec = spec_from_status(chat_meta.get("status") or {}, BASE_SPEC)
        # `[在场]` 原文交给 shot_plan 自动派生 wardrobe_by_actor（不手工写锚点）。
        present = str((chat_meta.get("status") or {}).get("在场") or "")
    else:
        text = DEMO_TEXT
        base_spec = dict(BASE_SPEC)
    if args.spec:
        base_spec.update(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    # 画面人称：手选优先；未指定时用生产同一个自动判定接缝（viewpoint_scope）。
    viewpoint = args.viewpoint
    if viewpoint not in ("first_person", "third_person"):
        try:
            viewpoint = scene_illustration.viewpoint_scope(
                text[:600], list(base_spec.get("actors") or []),
            ).mode or ""
        except Exception:  # noqa: BLE001 — 模拟脚本可降级：判不出人称就不写 POV
            viewpoint = ""
    if viewpoint:
        base_spec["viewpoint"] = viewpoint
    if args.protagonist.strip():
        # 第一人称：已知主人公一律是镜头，不能出现在 subjects/actors 里。
        base_spec["narrator"] = args.protagonist.strip()
    meta["viewpoint"] = viewpoint
    meta["spec"] = base_spec

    # A 方案：判据也走对话预设的防拦截上下文（与正常图片链同口径）。
    settings = (repo_meta.load_user_state() or {}).get("settings") or {}
    preset_dir = args.preset_dir or str(settings.get("presetDir") or "")
    preset_name = args.preset_name or str(settings.get("activePresetName") or "")
    if args.no_preset:
        preset_dir = ""
        preset_name = ""

    _RUNTIME_LLM["mode"] = args.llm
    _RUNTIME_LLM["ollama_model"] = args.ollama_model
    if args.llm == "ollama":
        print(f"本机 Ollama：{args.ollama_model}（0 费用，不走设置里的 key）", flush=True)
    elif args.llm == "live":
        print("⚠ live 模式会使用设置里的 key 计费；默认 mock/ollama 不发付费请求。", flush=True)
    if args.judge:
        anchors, judge = judge_anchors(
            text, batch=args.batch, actors=list(base_spec["actors"]),
            proxy_override=args.proxy, viewpoint=viewpoint,
            preset_dir=preset_dir, preset_name=preset_name, mode=args.judge_mode,
            llm_mode=args.llm, mock_anchors_path=args.mock_anchors,
            ollama_model=args.ollama_model,
        )
        meta["judge"] = judge
        meta["anchor_source"] = f"生产判据 LLM（batch={args.batch}）"
        if args.dump_raw:
            Path(args.dump_raw).write_text(judge["raw_head"], encoding="utf-8")
        if not anchors:
            print("判据没有解析出任何锚点（拒答/格式错/0 分）：无法模拟 shots，请换正文或改用 --anchors")
            return 2
    elif args.anchors:
        loaded = json.loads(Path(args.anchors).read_text(encoding="utf-8"))
        # 兼容两种输入：裸 anchors 数组，或完整报告 JSON（取 `anchors` 字段）。
        anchors = loaded.get("anchors") if isinstance(loaded, dict) else loaded
        anchors = anchors or []
        meta["anchor_source"] = args.anchors
    else:
        if args.chat:
            raise SystemExit("--chat 需要配 --judge 或 --anchors：内置演示锚点对不上真实正文")
        anchors = DEMO_ANCHORS
        meta["anchor_source"] = "内置演示锚点"

    result = simulate(
        text, anchors, args.total, args.caps_duration,
        base_spec=base_spec, profile=args.profile, profile_llm=args.profile_llm,
        proxy_override=args.proxy, present=present,
    )
    if args.scene_llm:
        if args.llm == "mock":
            raise SystemExit("--scene-llm 需要 --llm live 或 --llm ollama（mock 不调模型）")
        generate_text = _llm_generate_text(args.proxy)
        print(f"场景卡 LLM 补全：{len(result['scene_cards'])} 个场景", flush=True)
        result["scene_cards"] = scene_card_llm.enrich_scene_cards(
            result["scene_cards"], generate_text=generate_text,
        )
        for key, card in result["scene_cards"].items():
            print(
                f"  {key}: {card.get('extractor')} | "
                f"arch={len(card.get('architecture') or [])} "
                f"props={len(card.get('fixed_props') or [])} "
                f"relations={len(card.get('relations') or [])}"
            )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(result, text, meta), encoding="utf-8")
    json_out = Path(args.json_out)
    json_out.write_text(json.dumps({**result, "meta": meta}, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    scene_manifest = None
    if args.scene_refs:
        if not args.scene_template:
            raise SystemExit("--scene-refs 需要 --scene-template（文生图模板 id）")
        if not result["scene_cards"]:
            raise SystemExit("本次分镜没有 scene_cards（正文没有可提取的场景窗口）")
        url = (args.scene_url or str(settings.get("comfyuiUrl") or "")
               or "http://127.0.0.1:8188")
        if args.scene_out:
            scene_out = Path(args.scene_out)
        else:
            label = (
                f"{Path(args.chat).parent.parent.name}_{Path(args.chat).parent.name}"
                if args.chat else "demo"
            )
            scene_out = BACKEND.parent / "_tmp" / "scene_refs" / label
        style_loras = (
            [{"name": args.scene_lora, "weight": args.scene_lora_weight}]
            if args.scene_lora else None
        )
        print(f"\n空场景环境图：{len(result['scene_cards'])} 个场景 -> {scene_out}", flush=True)
        scene_manifest = scene_reference.generate_scene_references(
            result["scene_cards"], template_id=args.scene_template, url=url,
            out_dir=scene_out, force=args.scene_force, style_loras=style_loras,
            style=args.scene_style, width=args.scene_width, height=args.scene_height,
        )
        for key, item in scene_manifest["scenes"].items():
            detail = item.get("image_path") or item.get("error") or ""
            print(f"  {key}: {item['status']} {detail}")

    print(f"镜头 {result['total_shots']} / 总时长 {result['total_duration_s']}s")
    for shot in result["shots"]:
        print(
            f"  {shot['shot_index']}. {shot['shot_type']:11s} {shot['duration_s']:2d}s "
            f"-> {shot['video_slot']:14s} {shot['video_mode']}"
        )
    if result["warnings"]:
        print("warnings:")
        for warning in result["warnings"]:
            print(f"  - {warning}")
    print(f"\n报告：{out}")
    print(f"JSON：{json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
