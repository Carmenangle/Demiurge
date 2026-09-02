from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, TypedDict


_MISSING = object()


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class RunContext:
    thread_id: str
    message: str
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex, compare=False)
    images: list[str] = field(default_factory=list)
    image_mask: dict[str, str] | None = None
    chat: ModelConfig = field(default_factory=ModelConfig)
    generation: ModelConfig = field(default_factory=ModelConfig)
    video: ModelConfig = field(default_factory=ModelConfig)
    embedding: ModelConfig = field(default_factory=ModelConfig)
    size: str = "1024x1024"
    image_quality: str = "high"
    output_dir: str = ""
    repo_id: str = ""
    message_id: str = ""
    proxy_url: str = ""
    chat_proxy_url: str = ""
    gen_proxy_url: str = ""
    video_proxy_url: str = ""
    embed_proxy_url: str = ""
    route_model: str = ""
    provider_profile: str = "openai_compatible"
    style_template: str = ""
    agent_id: str = ""
    stream_output: bool = False
    approval_id: str = ""
    approval_action: str = ""
    edited_prompt: str = ""
    forced_route: str = ""
    user_message_id: str = ""
    workspace_mode: str = "story"  # story/generate/edit；edit 直达受限作品文件 Agent
    context_max_tokens: int = 20_000
    history_per_role: int = 6     # 每角色（用户/AI）读取的最近历史条数，全局上下文预算的一部分
    selfheal_attempts: int = 3    # 截断自愈次数上限（0=不自愈；设置→AI 模型可调）
    history_override: list[dict] | None = field(default=None, compare=False)  # 前端当前可见历史；显式 [] 表示已删空
    cancel_event: threading.Event = field(default_factory=threading.Event, compare=False)
    agent_cfg: dict | None = field(default=None, compare=False)
    builtin: dict = field(default_factory=dict, compare=False)  # ③ 内置 Agent 生效参数表（默认+用户覆盖）
    history: list[dict] = field(default_factory=list, compare=False)
    skill_frags: list[str] = field(default_factory=list, compare=False)
    has_mcp: bool = False
    stream_sink: Callable[[dict], None] | None = field(default=None, compare=False, repr=False)
    # 剧情扮演：关联角色卡时透传，供 roleplay 节点组装 persona 系统提示词。
    character_dir: str = ""       # 角色卡文件夹根（前端设置 characterDir）
    card_name: str = ""           # 本作品关联的角色卡名（空=非扮演，走通用对话）
    card_names: list[str] = field(default_factory=list, compare=False)  # 本作品绑定的全部角色卡
    opening_card_name: str = ""    # 兼容 card_name 的开场卡真源
    persona: str = field(default="", compare=False)  # 运行时富集：从卡组装的 persona 系统片段
    preset_dir: str = ""          # 偏置预设文件夹根（前端设置 presetDir）
    preset_name: str = ""         # 当前激活预设名（空=不用预设，走内置扮演提示）
    user_name: str = ""           # 用户人设名（填 {{user}} 宏）
    user_persona: str = ""        # 用户人设描述（填 personaDescription marker）
    persona_bound: bool = False   # 仓库显式绑定了人设：为真时 _apply_work_persona 不覆盖（用前端透传值）
    worldbook_dir: str = ""       # 独立世界书文件夹根（前端设置 worldbookDir）
    worldbook_name: str = ""      # 仓库绑定的独立世界书名（空=不绑；与卡内嵌世界书合并注入）
    illustrate: bool = False      # 剧情插画开关（开=构建 renderer 通能动性 D 阶段自动配图）
    comfy_illustrate: bool = False  # 前端已预设 ComfyUI 工作流模板：高潮点不同步 render，改发 illustrate_request 事件由前端走异步闭环
    comfy_audio: bool = False    # 前端已预设音频模板（IndexTTS）：剧情产出后发 audio_request 事件逐角色配音
    comfy_video: bool = False    # 前端已预设视频模板：开=高潮点编译 video_request（含提取 LLM 调用）；关=不调 LLM 不编译，零 token
    video_mode: str = ""        # 视频模式（""=缺省 climax / climax / firstlast），前端 preset.videoMode 透传；
                                # produce 层据此编译正片/转场 video_request（W3 前端 2 任务排队前置）
    prompt_profile: str = "anima_tags"  # 当前作品自动插画提示词模式，由主 Roleplay 同轮生成最终提示词（2026-08-31 默认切换自 krea2）
    appearance_source: str = "worldbook"  # worldbook / character_card
    character_base_images: dict = field(default_factory=dict, compare=False)  # ⑥ 角色名→底图（gpt-image 系无 LoRA，按在场角色取底图锁一致性）
    illustration_actor_names: list[str] = field(default_factory=list, compare=False)  # 自动插画可识别的已配置角色名
    style_base_image: str = ""    # ⑥ 无角色底图时的兜底风格底图（gpt-image 系）
    attachments: list[dict] = field(default_factory=list, compare=False)  # 对话附件元信息 [{file_id,name,mime,size}]；节点转「文件参考」段落进上下文
    # 运行期瞬态键槽：graph 节点把中途算出的键（scene/_regex_scripts 等）写回 ctx，供后续节点读。
    # 早期 ctx 是纯 dict 可随意塞键；改 dataclass 后固定字段外的瞬态键统一进这里，
    # 经 __setitem__/__getitem__/get 走 dict 语义。不参与相等比较（纯运行期状态）。
    extras: dict = field(default_factory=dict, compare=False)

    def input_images(self) -> list[str]:
        images = list(self.images)
        source = (self.image_mask or {}).get("image", "")
        if source and source not in images:
            images.insert(0, source)
        return images

    def _legacy(self) -> dict:
        return {
            "thread_id": self.thread_id, "turn_id": self.turn_id,
            "message": self.message,
            "repo_id": self.repo_id or self.thread_id,
            "image_mask": self.image_mask,
            "chat_base": self.chat.base_url, "chat_key": self.chat.api_key, "chat_model": self.chat.model,
            "gen_base": self.generation.base_url, "gen_key": self.generation.api_key, "gen_model": self.generation.model,
            "vid_base": self.video.base_url, "vid_key": self.video.api_key, "vid_model": self.video.model,
            "embed_base": self.embedding.base_url, "embed_key": self.embedding.api_key, "embed_model": self.embedding.model,
            "size": self.size, "image_quality": self.image_quality,
            "output_dir": self.output_dir, "proxy": self.proxy_url,
            "chat_proxy": self.chat_proxy_url, "gen_proxy": self.gen_proxy_url,
            "vid_proxy": self.video_proxy_url, "embed_proxy": self.embed_proxy_url,
            "route_model": self.route_model, "style_template": self.style_template,
            "provider_profile": self.provider_profile,
            "agent_id": self.agent_id, "message_id": self.message_id,
            "stream_output": self.stream_output, "stream_sink": self.stream_sink,
            "approval_id": self.approval_id, "approval_action": self.approval_action,
            "edited_prompt": self.edited_prompt, "forced_route": self.forced_route,
            "user_message_id": self.user_message_id, "cancel_event": self.cancel_event,
            "workspace_mode": self.workspace_mode,
            "context_max_tokens": self.context_max_tokens,
            "history_per_role": self.history_per_role,
            "selfheal_attempts": self.selfheal_attempts,
            "history_override": self.history_override,
            "agent_cfg": self.agent_cfg, "builtin": self.builtin, "history": self.history,
            "skill_frags": self.skill_frags, "has_mcp": self.has_mcp,
            "character_dir": self.character_dir, "card_name": self.card_name,
            "card_names": self.card_names, "opening_card_name": self.opening_card_name,
            "persona": self.persona,
            "preset_dir": self.preset_dir, "preset_name": self.preset_name,
            "user_name": self.user_name, "user_persona": self.user_persona,
            "persona_bound": self.persona_bound,
            "worldbook_dir": self.worldbook_dir, "worldbook_name": self.worldbook_name,
            "illustrate": self.illustrate, "comfy_illustrate": self.comfy_illustrate,
            "comfy_audio": self.comfy_audio, "comfy_video": self.comfy_video,
            "video_mode": self.video_mode,
            "prompt_profile": self.prompt_profile,
            "appearance_source": self.appearance_source,
            "character_base_images": self.character_base_images,
            "illustration_actor_names": self.illustration_actor_names,
            "style_base_image": self.style_base_image,
            "attachments": self.attachments,
        }

    def __getitem__(self, key: str):
        if key in self.extras:
            return self.extras[key]
        return self._legacy()[key]

    def __setitem__(self, key: str, value) -> None:
        # 瞬态运行期键写回 ctx（scene/_regex_scripts 等）。固定字段不经此路径写。
        self.extras[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.extras or key in self._legacy()

    def get(self, key: str, default=None):
        if key in self.extras:
            return self.extras[key]
        return self._legacy().get(key, default)

    def pop(self, key: str, default=_MISSING):
        """弹出运行期瞬态键；固定请求字段不是可变字典槽。"""
        if key in self.extras:
            return self.extras.pop(key)
        if default is _MISSING:
            raise KeyError(key)
        return default


class AgentEvent(TypedDict, total=False):
    """Agent 图内部领域事件；跨 HTTP 前必须由 chat_stream_protocol 编码。"""

    trace: str
    delta: str
    replace: str
    image: str
    video: str
    id: str
    insp: dict
    approval: dict
    route_choice: dict
    interrupted: bool
    error: str
    done: bool
    illustrate_request: dict  # 高潮点出图请求（{prompt}）；前端据本地预设模板走异步 ComfyUI 闭环
