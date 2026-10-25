"""LangGraph 图状态底座：AgentState（TypedDict, total=False）。

从 agent_graph.py 拆出（方案 B，2026-10-23）：所有节点域（supervisor/nodes/plan/roleplay）
与图装配都向下 import 本模块，禁止反向依赖 agent_graph。
⚠ 新增键必须在此声明——LangGraph TypedDict schema 会静默丢弃未声明键（artifacts /
illustrate_recs 等键 2026-09-08 / 2026-10-20 踩过坑，见类内注释）。"""

from __future__ import annotations

from typing import TypedDict

from app.services.agent_contracts import RunContext



class AgentState(TypedDict, total=False):
    """图的共享状态。messages 累积对话；route 是 supervisor 判出的下一站；产出写各字段。"""
    messages: list                 # 对话消息（含用户输入、图片）
    route: str                     # supervisor 分派结果：各专家/answer/clarify
    user_text: str                 # 本轮用户文本
    images: list                   # 本轮上传图片 url
    attachments: list              # 本轮对话附件元信息 [{file_id,name,mime,size}]（已并入 user_text 参考段）
    result_text: str               # 专家产出的文本回复
    artifacts: list                # 2026-09-08 产物卡：fabric 终局下发的交付物元数据列表
                                   # （必须声明，否则 LangGraph TypedDict schema 静默丢弃该键）
    image_recs: list               # 生图产出 [{id,url}]
    video_recs: list               # 生视频产出 [{id,url}]
    # ⚠ 以下四键 2026-10-20 补齐：`finalize_turn` 会返回它们，但此前**未在 schema 声明**
    # ⇒ LangGraph TypedDict 静默丢弃（同 `artifacts` 2026-09-08 踩过的坑）。
    # 后果实锤（18:22 轮）：`illustrate_recs` 被丢 ⇒ `emit_ready` 一旦没走成即时通道，
    # 图的下发路径彻底断掉；`rag_recs` 的 `upd.get("rag_recs")` 分支也是死的。
    illustrate_recs: list          # 插画槽 [{id,prompt,anchor_offset,scene_spec,…}]
    audio_recs: list               # 对白音频 [{id,lines,turn_id}]
    rag_recs: list                 # RAG 进度事件
    _eager_result: bool            # 正文/媒体已走即时通道，完成时不要再重放一遍
    insp_cards: list               # 灵感卡
    approval: dict                 # 结构化提示词审批卡
    route_choice: dict             # Supervisor 低置信时的最小候选选择卡
    trace: list                    # 节点流转轨迹（供 SSE 透出多 agent 协作过程）
    _interrupted: bool
    _streamed_result: bool         # 节点正文已实时发送；完成时只发最终替换，不重复 delta
    # 下方是执行上下文（构图时注入，专家节点用）
    _ctx: RunContext
