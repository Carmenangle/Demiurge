"""图像智能体端点：SSE 流式生成 + 后台运行状态 + 打断。
生成跑在后台线程（agent_runner），与 HTTP 连接解耦。
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Literal

from app.services.ai_common import EmbedModelReq
from app.generated.wire_contracts import AGENT_INVOCATION_WIRE_FIELDS
from app.services.sse import sse_response

router = APIRouter()


class ImageMaskRequest(BaseModel):
    image: str
    mask: str


class ImageAgentRequest(EmbedModelReq):
    thread_id: str = "home"            # 对话线 = 仓库 id
    message: str = ""                  # 本轮用户输入
    images: list[str] = []             # 随文图片（data URI 或 URL）
    image_mask: ImageMaskRequest | None = None  # 原图与独立 Alpha 蒙版
    gen_base_url: str = ""             # 生图模型（imageModels）
    gen_api_key: str = ""
    gen_model: str = ""
    video_base_url: str = ""           # 视频模型（videoModels）
    video_api_key: str = ""
    video_model: str = ""
    size: str = "1024x1024"            # 生图尺寸（前端比例+分辨率档算好的 宽x高）
    image_quality: Literal["auto", "low", "medium", "high"] = "high"
    output_dir: str = ""               # 输出图片路径（后端落盘留存云图）
    repo_id: str = ""                  # 留存/入库归属仓库（空则用 thread_id）
    message_id: str = ""               # 前端 botId：最终文本按此 id 落盘去重
    proxy_url: str = ""                # 联网搜索代理（search_inspiration 工具用）
    search_provider: str = ""          # 联网搜索源：空=自动回落链(bing-cn→ddg)；显式指定则不回落
    chat_proxy_url: str = ""           # 当前对话模型代理（空=直连）
    gen_proxy_url: str = ""            # 当前生图模型代理（空=直连）
    video_proxy_url: str = ""          # 当前视频模型代理（空=直连）
    style: str = ""                    # 用户手动选的提示词风格 sd/gpt/banana/""(自动)
    style_template: str = ""           # 自定义风格存档的整段内容（非空时优先于 style）
    agent_id: str = ""                 # 多 Agent：选中的 Agent 预设 id（空=内置默认行为）
    stream_output: bool = False         # 智能体正文是否通过 SSE 增量输出
    approval_id: str = ""              # 历史提示词审批卡 id
    approval_action: str = ""          # submit / change / cancel
    edited_prompt: str = ""            # change 时用户在卡片内修改后的提示词
    forced_route: str = ""              # 主管选择卡点击后的显式路由
    user_message_id: str = ""            # 选择卡关联的原用户消息 id
    context_max_tokens: int = Field(default=20_000, ge=0)  # 0=无上限（历史全量不裁剪），去掉 le 上限
    context_count: int = Field(default=6, ge=1, le=50)  # 已废弃（2026-09-11 token 化）：兼容保留，后端不再消费
    compact_trigger_pct: int = Field(default=80, ge=0, le=100)  # 历史占用达有效预算该百分比即自动压缩（0=关闭）
    selfheal_attempts: int = Field(default=3, ge=0, le=5)  # 截断自愈次数（0=不自愈）
    # 聊天室行为上限（设置→聊天室）：缺省值与旧硬编码一致。
    chat_guard_max_chars: int = Field(default=900, ge=0, le=5000)
    chat_default_max_tokens: int = Field(default=0, ge=0, le=131_072)  # 0=跟随全局；>0=聊天独立上限
    chat_followup_max: int = Field(default=3, ge=0, le=10)
    chat_reopen_max: int = Field(default=2, ge=0, le=10)
    chat_initiative_idle_minutes: int = Field(default=120, ge=0, le=10_080)
    chat_initiative_cooldown_minutes: int = Field(default=360, ge=0, le=10_080)
    chat_initiative_daily_max: int = Field(default=6, ge=0, le=100)
    chat_cadence: Literal["quiet", "normal", "frequent"] = "normal"
    chat_active_window_minutes: int = Field(default=15, ge=0, le=1_440)
    chat_max_bot_turns: int = Field(default=5, ge=1, le=50)
    chat_min_affinity: int = Field(default=30, ge=-100, le=100)
    chat_quiet_start: str = "00:30"
    chat_quiet_end: str = "08:30"
    chat_group_activity: Literal["quiet", "normal", "lively"] = "normal"
    # 2026-09-04 设置整合：智能体参数预设（温度/采样/输入输出上限），从 settings 注入。
    # 权威源 = settings（前端每轮 payload），preset 顶层不再管理这些字段。
    preset_sampling: dict = Field(default_factory=dict)
    provider_profile: Literal["openai_compatible", "claude_compatible"] = "openai_compatible"


# 单 agent 生成入口（POST /ai/image-agent → agent_runner.run_stream）已下线。
# 其 ReAct 大脑降级为多 Agent 的 tool_agent 专家节点（承接 MCP/工具串联），自由文本一律走 /multi-agent。
# 下方 /image-agent/running 与 /image-agent/cancel 保留：后台化的共用机制，多 Agent 同用同一 thread 计数/取消信号。


class MultiAgentRequest(ImageAgentRequest):
    workspace_mode: Literal["story", "generate", "edit"] = "story"
    interaction_mode: Literal["story", "chat", "group_chat"] = "story"  # 聊天/群聊显式模式（默认 story 零改变）
    chat_members: list[str] = []  # 聊天室成员（角色卡名，顺序即群聊轮转顺序）；空=按绑定卡推断
    route_model: str = ""   # supervisor 判分派用的（快）模型，空则用主对话模型
    character_dir: str = ""  # 角色卡文件夹根（前端设置 characterDir），供剧情扮演读卡
    card_name: str = ""      # 本作品关联的角色卡名（空=非扮演）
    reaction_kind: str = ""  # 聊天室：刚发完照片/换头像后的反应（image|avatar）
    reaction_prompt: str = ""  # 刚发的照片画面提示词（给反应提供上下文）
    card_names: list[str] = []  # 本作品绑定的全部角色卡
    opening_card_name: str = "" # 新会话开场卡；card_name 为兼容别名
    preset_dir: str = ""     # 偏置预设文件夹根（前端设置 presetDir）
    preset_name: str = ""    # 当前激活预设名（空=不用预设）
    user_name: str = ""      # 用户人设名（填 {{user}} 宏）
    user_persona: str = ""   # 用户人设描述（填 personaDescription marker）
    persona_bound: bool = False  # 仓库显式绑定了人设：为真时不被作品快照 persona.json 覆盖
    worldbook_dir: str = ""  # 独立世界书文件夹根（前端设置 worldbookDir）
    worldbook_name: str = "" # 仓库绑定的独立世界书名（空=不绑独立书；与卡内嵌世界书合并）
    illustrate: bool = False  # 剧情插画开关（开=能动性 D 阶段自动配图）
    comfy_illustrate: bool = False  # 前端已预设 ComfyUI 工作流模板：高潮点改发 illustrate_request 事件走异步闭环
    comfy_audio: bool = False  # 前端已预设音频模板（IndexTTS）：剧情产出后发 audio_request 事件逐角色配音
    comfy_video: bool = False  # 前端已预设视频模板：开=高潮点编译 video_request；关=零 token
    video_mode: Literal["", "climax", "firstlast", "shots"] = ""  # 视频模式（缺省 climax / shots=全局模式分镜序列）
    prompt_profile: str = "anima_tags"
    # 2026-10-25 提示词模版：prompt_profile 为 custom:<id> 时同时报送其内置协议族
    # （模版被删时后端按该 kind 降级内置，不抛错）；空=按默认 anima_tags。
    prompt_profile_kind: str = ""
    # 视频提示词模版 id（builtin:<kind> / custom:<hex> / 空=内置默认骨架）
    video_prompt_template_id: str = ""
    # 视频工作流模版 id（空=未知）：后端据此派生尾帧/参考图槽与默认时长
    video_template_id: str = ""
    # 全局模式分镜序列的总时长预算（秒，0=按模板/正文估算）
    video_duration_hint: int = Field(default=0, ge=0)
    appearance_source: Literal["worldbook", "character_card"] = "worldbook"
    illustration_viewpoint: Literal["", "first_person", "third_person"] = ""  # 画面人称手选（「多元数据插入」面板按钮组；空=按锚点段自动判定）
    illustration_batch_count: int = Field(default=0, ge=0)  # 高潮点模式批量生图数目（0/缺省=1 张；面板最小 1、无上限）
    character_base_images: dict[str, str] = {}  # ⑥ 角色名→底图（gpt-image 系按在场角色取底图锁一致性）
    illustration_actor_names: list[str] = []  # 自动插画可从正文机械识别的已配置角色名
    style_base_image: str = ""  # ⑥ 无角色底图时的兜底风格底图（gpt-image 系）
    history: list[dict] | None = None  # 前端当前可见历史；显式 [] 禁止回退旧 checkpoint
    attachments: list[dict] = []  # 对话附件元信息 [{file_id,name,mime,size}]；agent 链路转「文件参考」段落
    story_turn: int = Field(default=0, ge=0)  # M1：前端持久化剧情回合号（assistant 楼层绑定）；0=未提供走后端派生


class TraceReplayRequest(BaseModel):
    repo_id: str
    turn_id: str = ""
    limit: int = Field(default=200, ge=1, le=200)


_missing_wire_fields = AGENT_INVOCATION_WIRE_FIELDS.difference(MultiAgentRequest.model_fields)
if _missing_wire_fields:
    raise RuntimeError(f"MultiAgentRequest 缺少共享 wire 字段：{sorted(_missing_wire_fields)}")


@router.post("/multi-agent")
def multi_agent(req: MultiAgentRequest) -> StreamingResponse:
    """Supervisor 多 Agent（LangGraph）：默认普通对话，明确执行时分派图片/视频/工具专家。SSE 流式，
    透出节点流转({trace})供前端展示协作过程。生成同样跑在 agent_runner 后台线程里。"""
    from app.services import agent_runner
    from app.services.agent_request_context import from_payload

    if not req.message.strip() and not req.images and not req.image_mask:
        raise HTTPException(status_code=400, detail="内容为空")

    context = from_payload(req.model_dump())
    try:
        q = agent_runner.run_multi_stream(context)
    except agent_runner.RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return sse_response(lambda: agent_runner.drain(q))


@router.post("/chat/initiative")
def chat_initiative(req: MultiAgentRequest) -> dict[str, object]:
    """角色主动找上门（2026-11-09）：打开私聊时前端按需调用。

    判定与生成都在服务层：有剧情沉淀 + 已认识且好感度够 + 聊天闲置够久 + 冷却已过。
    不产生用户消息；生成结果已由 chat_room 落盘，前端只负责合并显示。不可发起/失败
    时返回空 messages，前端静默跳过。
    """
    from app.services import agent_context, chat_room, thread_admission
    from app.services.agent_request_context import from_payload

    if req.interaction_mode != "chat":
        return {"messages": [], "skip": "not_chat"}
    thread_id = (req.thread_id or req.repo_id or "").strip()
    if thread_id and thread_admission.is_active(thread_id):
        return {"messages": [], "skip": "busy"}
    context = from_payload(req.model_dump())
    context["interaction_mode"] = "chat"
    budget = agent_context.resolve_history_budget(
        context.context_max_tokens, context.preset_sampling)
    context.history = agent_context.recent_history(
        context.thread_id, max_tokens=budget, per_role=0,
        history_override=context.history_override,
    )
    try:
        messages = chat_room.proactive_turn(context)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 主动消息失败不打扰用户
        return {"messages": [], "skip": f"error:{exc}"}
    return {"messages": messages, "skip": "" if messages else "not_eligible"}


@router.post("/chat/roster")
def chat_roster(req: MultiAgentRequest) -> dict[str, object]:
    """角色名册（2026-11-10）：合集卡从世界书条目识别角色，供聊天头像条、
    「管理角色」面板与多元数据插入自动补全共用。

    只读：不调模型、不写会话。`members` 是当前生效的角色名；`candidates` 是全部
    启用条目（含未识别项），供前端手动勾选补漏。
    """
    from app.services import chat_room, chat_snapshot
    from app.services.agent_request_context import from_payload

    context = from_payload(req.model_dump())
    context["interaction_mode"] = req.interaction_mode
    try:
        roster = chat_room.chat_roster(context)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 名册读取失败返回空，前端回退绑定卡
        return {"source": "cards", "members": [], "entries": [], "candidates": [],
                "fallback_card": "", "member_threads": {}, "member_activity": {},
                "error": str(exc)}
    entries = [
        {key: value for key, value in item.items() if key != "content"}
        for item in (roster.get("entries") or [])
        if isinstance(item, dict)
    ]
    repo_id = str(context.get("repo_id") or "")
    source_by_name = {
        str(item.get("name") or ""): str(item.get("source_name") or "")
        for item in roster.get("entries") or []
        if isinstance(item, dict)
    }
    member_threads: dict[str, str] = {}
    member_activity: dict[str, float] = {}
    for name in roster.get("members") or []:
        thread_id = chat_room.thread_id_for_member(
            repo_id, str(name), source_by_name.get(str(name), ""),
        )
        member_threads[str(name)] = thread_id
        try:
            member_activity[str(name)] = float(chat_snapshot.last_write_at(thread_id))
        except Exception:  # noqa: BLE001 [EXC-保留] 没聊过的角色按 0 排序
            member_activity[str(name)] = 0.0
    return {
        "source": roster.get("source") or "cards",
        "members": roster.get("members") or [],
        "entries": entries,
        "candidates": roster.get("candidates") or [],
        "fallback_card": roster.get("fallback_card") or "",
        "member_threads": member_threads,
        "member_activity": member_activity,
    }


@router.post("/chat/initiative/scan")
def chat_initiative_scan(req: MultiAgentRequest) -> dict[str, object]:
    """扫描角色名册并给够格的角色写主动开场（2026-11-10）。

    每位角色写进自己的私聊线程；单次最多 2 人。前端只把当前角色的消息上屏，
    其他角色只记未读红点，从而让「未打开角色也有新消息」可验证。
    """
    from app.services import agent_context, chat_room, thread_admission
    from app.services.agent_request_context import from_payload

    if req.interaction_mode != "chat":
        return {"results": []}
    thread_id = (req.thread_id or req.repo_id or "").strip()
    if thread_id and thread_admission.is_active(thread_id):
        return {"results": []}
    context = from_payload(req.model_dump())
    context["interaction_mode"] = "chat"
    budget = agent_context.resolve_history_budget(
        context.context_max_tokens, context.preset_sampling)
    context.history = agent_context.recent_history(
        context.thread_id, max_tokens=budget, per_role=0,
        history_override=context.history_override,
    )
    try:
        results = chat_room.scan_initiatives(context, limit=2)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 扫描失败不打扰用户
        return {"results": [], "skip": f"error:{exc}"}
    return {"results": results}


@router.post("/chat/followup")
def chat_followup(req: MultiAgentRequest) -> dict[str, object]:
    """长时间不回复时的追问 / 会话窗口结束后的重开（2026-11-10）。

    前端私聊打开期间按固定间隔轮询；后端按 `chat_followup` 的节奏与上限决定是否
    真正生成。不在窗口/未到时间/达到上限时返回空 messages 与 skip 原因，前端静默。
    """
    from app.services import agent_context, chat_followup as followup_state, chat_room, thread_admission
    from app.services.agent_request_context import from_payload

    if req.interaction_mode != "chat":
        return {"messages": [], "thread_id": "", "speaker": "", "kind": "", "skip": "not_chat"}
    thread_id = (req.thread_id or req.repo_id or "").strip()
    if thread_id and thread_admission.is_active(thread_id):
        return {"messages": [], "thread_id": thread_id, "speaker": "", "kind": "", "skip": "busy"}
    # 老会话（本功能上线前已有私聊）第一次打开：把最后一条角色消息当作「刚说完」，
    # 让追问/重开节奏从此刻起算，避免旧聊天永远停在过去。
    if thread_id and not followup_state.is_tracked(thread_id):
        try:
            from app.services import chat_snapshot
            last_items = chat_snapshot.load(thread_id)
            if last_items and str(last_items[-1].get("role") or "") == "assistant":
                followup_state.note_bot(
                    thread_id, bubbles=1,
                    max_followups=req.chat_followup_max,
                    max_reopens=req.chat_reopen_max,
                    cadence=req.chat_cadence,
                )
        except Exception as exc:  # noqa: BLE001 [EXC-留痕] bootstrap 失败不阻断后续判定
            return {"messages": [], "thread_id": thread_id, "speaker": "", "kind": "",
                    "skip": f"bootstrap_error:{exc}"}
    kind = followup_state.due(
        thread_id,
        max_followups=req.chat_followup_max,
        max_reopens=req.chat_reopen_max,
        max_bot_turns=req.chat_max_bot_turns,
        active_window_minutes=req.chat_active_window_minutes,
        quiet_start=req.chat_quiet_start,
        quiet_end=req.chat_quiet_end,
    )
    if not kind:
        return {"messages": [], "thread_id": thread_id, "speaker": "", "kind": "", "skip": "not_due"}
    context = from_payload(req.model_dump())
    context["interaction_mode"] = "chat"
    budget = agent_context.resolve_history_budget(
        context.context_max_tokens, context.preset_sampling)
    context.history = agent_context.recent_history(
        context.thread_id, max_tokens=budget, per_role=0,
        history_override=context.history_override,
    )
    members = chat_room.resolve_members(context, mode="chat")
    speaker = (members[0] if members else "").strip()
    if not speaker:
        return {"messages": [], "thread_id": thread_id, "speaker": "", "kind": "", "skip": "no_speaker"}
    try:
        messages = chat_room.followup_turn(
            context, speaker=speaker, thread_id=thread_id, kind=kind,
        )
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 追问失败不打扰用户
        return {"messages": [], "thread_id": thread_id, "speaker": speaker,
                "kind": "", "skip": f"error:{exc}"}
    return {
        "messages": messages,
        "thread_id": thread_id,
        "speaker": speaker,
        "kind": kind if messages else "",
        "skip": "" if messages else "not_eligible",
    }


@router.post("/chat/reaction")
def chat_reaction(req: MultiAgentRequest) -> dict[str, object]:
    """角色刚发完照片/换完头像后补一句反应（2026-11-10 用户口径）。

    不产生用户消息：生成一条角色消息写进当前私聊线程，前端合并上屏。
    """
    from app.services import agent_context, chat_room, thread_admission
    from app.services.agent_request_context import from_payload

    if req.interaction_mode != "chat":
        return {"messages": [], "thread_id": "", "speaker": "", "skip": "not_chat"}
    thread_id = (req.thread_id or req.repo_id or "").strip()
    if not thread_id:
        return {"messages": [], "thread_id": "", "speaker": "", "skip": "no_thread"}
    if thread_admission.is_active(thread_id):
        return {"messages": [], "thread_id": thread_id, "speaker": "", "skip": "busy"}
    context = from_payload(req.model_dump())
    context["interaction_mode"] = "chat"
    budget = agent_context.resolve_history_budget(
        context.context_max_tokens, context.preset_sampling)
    context.history = agent_context.recent_history(
        context.thread_id, max_tokens=budget, per_role=0,
        history_override=context.history_override,
    )
    members = chat_room.resolve_members(context, mode="chat")
    speaker = (members[0] if members else "").strip()
    if not speaker:
        return {"messages": [], "thread_id": thread_id, "speaker": "", "skip": "no_speaker"}
    try:
        messages = chat_room.reaction_turn(
            context, speaker=speaker, thread_id=thread_id,
            kind=req.reaction_kind, prompt=req.reaction_prompt,
        )
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 反应失败只少一句，不打扰聊天
        return {"messages": [], "thread_id": thread_id, "speaker": speaker,
                "skip": f"error:{exc}"}
    return {
        "messages": messages, "thread_id": thread_id, "speaker": speaker,
        "skip": "" if messages else "not_eligible",
    }


class ChatReadRequest(BaseModel):
    """私聊已读上报（2026-11-10）：前端可见且滚到底时调用。"""
    thread_id: str = ""
    last_ts: float = 0.0


@router.get("/chat/read")
def get_chat_read(thread_id: str = "") -> dict[str, object]:
    """读取私聊已读状态（前端渲染「已读/未读」圆点用）。"""
    from app.services import chat_read_state
    return {"state": chat_read_state.get(thread_id)}


@router.post("/chat/read")
def chat_read(req: ChatReadRequest) -> dict[str, object]:
    from app.services import chat_read_state
    return {"ok": True, "state": chat_read_state.mark_read(req.thread_id, req.last_ts)}


class MemorySealRequest(BaseModel):
    """M1 记忆级联封口（2026-09-06 用户定案）：删除消息/重新生成后调用。"""
    output_dir: str
    repo_id: str = ""       # 空 → thread_id
    thread_id: str = ""
    card_name: str = ""     # 兼容单卡入参
    card_names: list[str] = []  # 仓库绑定的全部卡（逐卡回退纪要抽取进度，审计 #3）
    turns: list[int] = []   # 被删除/重生成覆盖的剧情回合号（assistant 楼层绑定的 turnNo）
    mode: Literal["delete", "regenerate"] = "delete"  # 删除=回合永久封口；重生成=条目隐藏、回合开放


@router.post("/memory/seal")
def memory_seal(req: MemorySealRequest) -> dict[str, object]:
    """按回合级联封口派生记忆（纪要/时序事实/角色状态）：注入侧等效清除、
    数据保留可审计（封口非物理删）；表格/RAG/世界书暂无回合归属，保持手动清理。"""
    from app.services import memory_rollback
    repo_id = (req.repo_id or req.thread_id or "").strip()
    if not (req.output_dir.strip() and repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    if not req.turns:
        raise HTTPException(status_code=400, detail="缺少封口回合 turns")
    # M1 审计 #5 竞态守卫：该会话仍有生成任务在跑时封口，旧任务收尾的维护写入会把
    # 已封数据写回（残留复活）。拒绝并让前端稍后重试——删除操作本身不受影响。
    from app.services import thread_admission
    if thread_admission.is_active((req.thread_id or repo_id).strip()):
        raise HTTPException(status_code=409,
                            detail="该会话有生成任务运行中，暂不能封口记忆；请等任务结束后重试删除/重新生成")

    return memory_rollback.seal_turns(
        req.output_dir.strip(), repo_id,
        card_name=req.card_name.strip(), card_names=req.card_names, turns=req.turns,
        mode=req.mode)


@router.get("/agent/trace")
def agent_trace(repo_id: str, turn_id: str = "", limit: int = 20) -> dict[str, object]:
    """实时过程可视化：前端轮询当前作品最近 trace 事件（思考/工具/重试/节点流转）。"""
    from app.services import run_trace
    if not repo_id.strip():
        raise HTTPException(status_code=400, detail="repo_id 不能为空")
    events = run_trace.read_recent(repo_id.strip(), turn_id=turn_id.strip(),
                                   limit=max(1, min(limit, 100)))
    return {"events": events}


@router.post("/trace/replay")
def replay_trace(req: TraceReplayRequest) -> dict[str, object]:
    """离线校验既有 Trace；不调用模型，不写会话、资产或数据库。"""
    from app.services import trace_replay

    if not req.repo_id.strip():
        raise HTTPException(status_code=400, detail="repo_id 不能为空")
    return trace_replay.replay_recent(
        req.repo_id.strip(), turn_id=req.turn_id.strip(), limit=req.limit,
    )


class RegenerateImageRequest(BaseModel):
    thread_id: str
    repo_id: str
    prompt: str
    images: list[str] = []
    image_mask: ImageMaskRequest | None = None
    gen_base_url: str
    gen_api_key: str
    gen_model: str
    gen_proxy_url: str = ""
    size: str = "1024x1024"
    image_quality: Literal["auto", "low", "medium", "high"] = "high"
    output_dir: str = ""
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "embedding-3"
    embed_proxy_url: str = ""


@router.post("/regenerate-image")
def regenerate_image(req: RegenerateImageRequest) -> dict[str, object]:
    """按结果消息保存的不可变参数直接重放，不经过 Supervisor 或提示词改写。"""
    from app.services import generation_store, image_gen

    regeneration = {
        "kind": "ai-image", "prompt": req.prompt, "images": list(req.images),
        **({"imageMask": req.image_mask.model_dump()} if req.image_mask else {}),
        "size": req.size, "quality": req.image_quality,
        "model": {"baseUrl": req.gen_base_url, "modelName": req.gen_model},
    }
    try:
        if req.images or req.image_mask:
            images = list(req.images)
            if req.image_mask and req.image_mask.image not in images:
                images.insert(0, req.image_mask.image)
            kwargs: dict[str, Any] = {"size": req.size, "quality": req.image_quality}
            if req.image_mask:
                kwargs["mask"] = req.image_mask.mask
            if req.gen_proxy_url:
                kwargs["proxy"] = req.gen_proxy_url
            url = image_gen.generate_with_images(
                req.gen_base_url, req.gen_api_key, req.gen_model,
                req.prompt, images, **kwargs,
            )
        else:
            proxy_kw: dict[str, Any] = {"proxy": req.gen_proxy_url} if req.gen_proxy_url else {}
            url = image_gen.generate(
                req.gen_base_url, req.gen_api_key, req.gen_model,
                req.prompt, size=req.size, quality=req.image_quality, **proxy_kw,
            )
        persist_kw: dict[str, Any] = {}
        if req.embed_proxy_url:
            persist_kw["embed_proxy"] = req.embed_proxy_url
        if req.gen_proxy_url:
            persist_kw["gen_proxy"] = req.gen_proxy_url
        from app.services import repo_meta as _rm
        if (err := _rm.works_root_violation(req.output_dir)):
            raise HTTPException(status_code=400, detail=err)
        rec = generation_store.persist_image(
            req.thread_id, req.repo_id, req.prompt, url, req.output_dir,
            req.embed_base_url, req.embed_api_key, req.embed_model,
            regeneration, **persist_kw,
        )
        return {"ok": True, **rec}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"重新生图失败：{exc}") from exc


@router.get("/image-agent/running")
def image_agent_running(thread_id: str = "home") -> dict[str, object]:
    """该 thread 是否有后台生成任务在跑。前端切回/刷新时据此轮询快照等落盘。"""
    from app.services import agent_runner
    return {"running": agent_runner.is_running(thread_id)}


@router.get("/image-agent/running-threads")
def image_agent_running_threads() -> dict[str, object]:
    """当前有后台生成任务在跑的所有 thread（仓库 id），供后台活动面板列出。"""
    from app.services import thread_admission
    return {"threads": thread_admission.active_threads()}


class ChatQueueEnqueueRequest(MultiAgentRequest):
    pass


@router.post("/chat-queue/enqueue")
def chat_queue_enqueue(req: ChatQueueEnqueueRequest) -> dict[str, object]:
    """把一条忙时排队消息落后端队列；worker 在前一条结束后串行认领执行（刷新/重开仍继续）。"""
    from app.services import chat_agent_queue
    payload = req.model_dump()
    if req.image_mask:
        payload["image_mask"] = req.image_mask.model_dump()
    task = chat_agent_queue.enqueue(payload)
    return {"task": task}


@router.get("/chat-queue")
def chat_queue_list(thread_id: str = "") -> dict[str, object]:
    """列出某仓库（或全部）的排队消息，供前端持久化队列条与后台面板显示。"""
    from app.services import chat_agent_queue
    return {"tasks": chat_agent_queue.list_tasks(thread_id)}


class ChatQueueCancelRequest(BaseModel):
    task_id: str


@router.post("/chat-queue/cancel")
def chat_queue_cancel(req: ChatQueueCancelRequest) -> dict[str, object]:
    """取消一条尚未发出的排队消息。"""
    from app.services import chat_agent_queue
    task = chat_agent_queue.cancel(req.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="排队消息不存在")
    return {"task": task}


class CancelRequest(BaseModel):
    thread_id: str = "home"


class IllustrationFailureRequest(BaseModel):
    thread_id: str
    repo_id: str = ""
    message_id: str
    slot_id: str
    stage: str
    error: str
    prompt_id: str = ""
    comfyui_url: str = ""


class IllustrationSubmissionRequest(BaseModel):
    thread_id: str
    repo_id: str = ""
    turn_id: str = ""
    message_id: str
    slot_id: str
    template_id: str
    prompt_id: str
    prompt: str
    prompt_profile: str = ""
    lora_name: str = ""
    lora_weight: float | None = None
    lora_mode: str = "single"
    lora_names: list[str] = Field(default_factory=list)
    latent_width: int | None = Field(default=None, ge=1, le=16384)
    latent_height: int | None = Field(default=None, ge=1, le=16384)
    value_keys: list[str] = Field(default_factory=list)
    source: Literal["automatic", "manual"] = "automatic"


class AudioSubmissionRequest(BaseModel):
    thread_id: str
    repo_id: str = ""
    turn_id: str = ""
    message_id: str
    slot_id: str
    speaker: str
    text: str
    voice_ref: str = ""          # 参考音轨本地路径（排查音色来源）
    template_id: str
    prompt_id: str
    emotion: dict[str, float] = Field(default_factory=dict)  # 8 维情感向量
    value_keys: list[str] = Field(default_factory=list)
    source: Literal["automatic", "manual"] = "automatic"


class IllustrationClaimRequest(BaseModel):
    thread_id: str
    message_id: str
    slot_id: str


class IllustrationStageRequest(BaseModel):
    """前端插画提交链的**阶段标记**（2026-10-19）。

    动机：`illustration.claim` 到 `illustration.failed` 之间是**观测黑洞**——
    实锤一次卡了 7 分 47 秒，后端 trace 一条事件都没有，只能靠时间线倒推。
    这里让前端在 claim / 编译 / 上传 / 提交 四个节点各报一条，纯观测、不改行为。
    """
    thread_id: str = ""
    repo_id: str = ""
    turn_id: str = ""
    message_id: str = ""
    slot_id: str = ""
    stage: str                      # claim | compile | upload | submit
    status: str = "start"           # start | ok | error
    detail: str = ""
    elapsed_ms: int | None = None


class EnsureAudioSlotRequest(BaseModel):
    thread_id: str
    message_id: str
    slot_id: str
    speaker: str = ""
    seq: int | None = None
    total: int | None = None


@router.post("/image-agent/ensure-audio-slot")
def ensure_audio_slot(req: EnsureAudioSlotRequest) -> dict[str, bool]:
    """音频对白槽补写快照（追加式，保留已有图片/视频槽）。前端提交配音前调用，
    保证 finalize 时 resolve_media_slot 能找到槽位并原位回填音频 URL。"""
    from app.services import generation_store
    generation_store.persist_audio_slot(
        thread_id=req.thread_id, message_id=req.message_id, slot_id=req.slot_id,
        speaker=req.speaker or None, seq=req.seq, total=req.total,
    )
    return {"ok": True}


@router.post("/image-agent/illustration-claim")
def illustration_claim(req: IllustrationClaimRequest) -> dict[str, bool]:
    """ComfyUI 提交前认领权威插画槽；重复事件不得产生第二个任务。"""
    from app.services import generation_store
    claimed = generation_store.claim_illustration_submission(
        thread_id=req.thread_id, message_id=req.message_id, slot_id=req.slot_id,
    )
    return {"ok": True, "claimed": claimed}


@router.post("/image-agent/illustration-stage")
def illustration_stage(req: IllustrationStageRequest) -> dict[str, bool]:
    """前端插画提交链的阶段标记；追踪失败绝不影响生图（best-effort）。"""
    from app.services import run_trace
    try:
        run_trace.emit(
            {
                "thread_id": req.thread_id,
                "repo_id": req.repo_id or req.thread_id,
                "turn_id": req.turn_id,
            },
            "illustration.stage",
            message_id=req.message_id,
            slot_id=req.slot_id,
            stage=req.stage,
            status=req.status,
            detail=req.detail[:200],
            elapsed_ms=req.elapsed_ms,
        )
    except Exception:  # noqa: BLE001 [EXC-保留] 遥测 best-effort，绝不阻断提交
        pass
    return {"ok": True}


@router.post("/image-agent/illustration-submission")
def illustration_submission(req: IllustrationSubmissionRequest) -> dict[str, bool]:
    """记录前端最终提交给 ComfyUI 的实际参数；追踪失败不影响生图。"""
    from app.services import generation_store, run_trace
    slot_bound = generation_store.persist_illustration_submission(
        thread_id=req.thread_id,
        message_id=req.message_id,
        slot_id=req.slot_id,
        prompt_id=req.prompt_id,
    )
    ctx = {
        "thread_id": req.thread_id,
        "repo_id": req.repo_id or req.thread_id,
        "turn_id": req.turn_id,
    }
    run_trace.emit(
        ctx,
        "illustration.submitted",
        message_id=req.message_id,
        slot_id=req.slot_id,
        template_id=req.template_id,
        prompt_id=req.prompt_id,
        prompt=req.prompt,
        prompt_chars=len(req.prompt),
        prompt_profile=req.prompt_profile,
        lora_name=req.lora_name,
        lora_weight=req.lora_weight,
        lora_mode=req.lora_mode,
        lora_names=req.lora_names,
        latent={"width": req.latent_width, "height": req.latent_height},
        value_keys=req.value_keys,
        source=req.source,
        slot_bound=slot_bound,
    )
    return {"ok": True}


@router.post("/image-agent/audio-submission")
def audio_submission(req: AudioSubmissionRequest) -> dict[str, bool]:
    """记录前端最终提交给 ComfyUI 的音频配音参数（台词/音色/情感），追踪失败不影响配音。"""
    from app.services import run_trace
    ctx = {
        "thread_id": req.thread_id,
        "repo_id": req.repo_id or req.thread_id,
        "turn_id": req.turn_id,
    }
    run_trace.emit(
        ctx,
        "audio.submitted",
        message_id=req.message_id,
        slot_id=req.slot_id,
        speaker=req.speaker,
        text=req.text,
        text_chars=len(req.text),
        voice_ref=req.voice_ref,
        template_id=req.template_id,
        prompt_id=req.prompt_id,
        emotion=req.emotion,
        value_keys=req.value_keys,
        source=req.source,
    )
    return {"ok": True}


@router.post("/image-agent/illustration-failure")
def illustration_failure(req: IllustrationFailureRequest) -> dict[str, object]:
    """记录自动插画失败并移除持久化槽，不向对话正文追加错误。

    有 prompt_id 时同时取消 ComfyUI 侧任务（/interrupt + /queue delete），
    防止僵尸/已停任务继续霸占队列导致后续任务排队死锁。
    """
    from app.services import generation_store
    removed = generation_store.persist_illustration_failure(
        thread_id=req.thread_id,
        repo_id=req.repo_id or req.thread_id,
        message_id=req.message_id,
        slot_id=req.slot_id,
        stage=req.stage,
        error=req.error,
        prompt_id=req.prompt_id,
    )
    cancelled: dict[str, bool] = {"deleted": False, "interrupted": False}
    if req.prompt_id:
        from app.services import comfyui_client
        from app.config import COMFYUI_BASE_URL
        comfy_url = req.comfyui_url or COMFYUI_BASE_URL
        try:
            cancelled = comfyui_client.interrupt(comfy_url, req.prompt_id)
        except Exception:  # [EXC-保留] 取消清理 best-effort：interrupt 失败不改变「已移除」语义
            pass
    return {"ok": True, "removed": removed, "cancelled": cancelled}


@router.post("/image-agent/cancel")
def image_agent_cancel(req: CancelRequest) -> dict[str, object]:
    """打断该 thread 的后台生成：协作式取消，半成品文本落盘并补进记忆供下一轮续写=合并。"""
    from app.services import agent_runner
    running = agent_runner.cancel(req.thread_id)
    return {"ok": True, "running": running}
