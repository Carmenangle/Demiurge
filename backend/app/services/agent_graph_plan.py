"""计划编译节点域：plan_compiler_node + 11 个 _pc_* 接缝（审批词/附件收集/编译收尾/
自由循环/委派登记/设计讨论/配方邀约）。

从 agent_graph.py 按域迁出（方案 B，2026-10-23）：搬移块 sha1 逐字节一致，
旧名经 agent_graph re-export 零破坏。向下 import agent_graph_state。"""

from __future__ import annotations

from app.services.agent_contracts import CtxLike

import logging
import re
from typing import Any

from app.config import COMFYUI_BASE_URL
from app.services import plan_compiler, run_trace, agent_context
from app.services.agent_fabric import _knowledge_catalog_text, _recipe_catalog_text, _flow_doc_hint, _live_chat, _live_trace, _run_fabric_content_loop, _task_session_continuation, _fabric_finalize, _fabric_knowledge_history, _fabric_run_args, _recipe_hard_replay, _recipe_offer_pending, _fabric_approval_word, _recipe_offer_answer
from app.services.agent_graph_state import AgentState
from app.services.agent_support import _proxy_kw

_LOG = logging.getLogger("uvicorn.error")


def _pc_configured_models(ctx: CtxLike) -> set:
    """已配置模型集合：chat 恒在，image/video/embed 按 base_url 真值判定。

    逐字自 `plan_compiler_node` 搬移（判定逻辑与字面值零改动）。
    """
    return {
        key for key, flag in (("chat", True), ("image", ctx.get("gen_base")),
                              ("video", ctx.get("vid_base")), ("embed", ctx.get("embed_base")))
        if flag
    }

def _pc_approval_word(text: str) -> str | None:
    """审批词直通车判定：整条消息恰为审批/取消词之一时返回归一化小写词，否则 None。

    逐字自 `plan_compiler_node` 搬移（含 `flags=re.IGNORECASE` 与 `group(1).lower()`）。
    """
    m = re.match(r"^\s*(批准|同意|执行|确认|approve|ok|好|取消|cancel|撤销)\s*$",
                 text, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower()

def _pc_judge_text(intent_text: str, history) -> str:
    """判定用文本：本轮意图 + 历史用户消息（**逐条前插**）。

    短指令（「继续补写」）零 LLM 判定会丢语义，故不命中 doc 委派时把历史用户消息
    前插再判一次。⚠ 是逐条前插（后出现的用户消息更靠前），不是追加——这是改造前
    既有语义，不得"顺手修正"。

    逐字自 `plan_compiler_node` 搬移（判定逻辑与字面值零改动）。
    """
    judge = intent_text
    if not plan_compiler.is_doc_delegation_intent(judge):
        for h in (history or []):
            if isinstance(h, dict) and h.get("role") == "user":
                judge = str(h.get("content") or "") + "\n" + judge
        judge = judge.strip()
    return judge

def _pc_collect_attachments(ctx: CtxLike, text: str, attachments: list[dict]) -> None:
    """编译期预读：把编译上下文附件**原地追加**进 `attachments`。

    覆盖：消息/历史里显式写出的本地文本路径（容量封顶 3 条）、正文里的【文件参考】块
    全文（并按 name 解析真实落盘路径）、LoRA 目录、模板目录（含暴露字段清单）、固化
    流程清单、固化知识库。各子块自带 try/except 降级，单块不可用不影响其余块。

    ⚠ **原地改 `attachments` 而非返回新列表**：改造前它就是 `plan_compiler_node` 的局部
    变量，异常路径下「已收集到的部分附件」仍会被下游 `compile_plan` 使用——返回新列表
    会让调用方在异常时拿到空列表，属行为差异。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    _scan_text = text
    for _hitem in (ctx.get("history") or []):
        if isinstance(_hitem, dict) and _hitem.get("role") == "user":
            _scan_text += "\n" + str(_hitem.get("content") or "")
    for raw_path in set(re.findall(
            r"[A-Za-z]:\\[^\s<>|？?」』]*\.(?:md|txt|json|csv|log|ya?ml|xml|html)",
            _scan_text)):
        try:
            read = plan_compiler.read_user_file(raw_path)
        except Exception as exc:  # noqa: BLE001 - 预读失败如实留痕，不阻断编译
            run_trace.emit(ctx, "plan.attachments", status="error",
                           path=raw_path, error=str(exc))
            continue
        attachments.append({"name": raw_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
                            "text": read["text"]})
        run_trace.emit(ctx, "plan.attachments", status="ok", path=raw_path,
                       chars=len(read["text"]))
    if len(attachments) > 3:
        attachments[:] = attachments[:3]
    # 用户把文档作为附件发送时，正文里带【文件参考：name】…【文件参考结束：name】块；
    # 这些全文也必须进 attachments，fill_prompt_sections 才能机械回填 prompt_section 引用
    # （路径预读只覆盖「消息里写绝对路径」的情况）。
    # 同时按 name 匹配 ctx.attachments 的 file_id 取真实落盘路径（attachment_store.resolve），
    # 让模型在 plan 步骤里写真实路径而非编造（2026-09-05 实锤：编出 /Users/momo/... 越域路径）。
    _attach_by_name = {str(a.get("name") or ""): str(a.get("file_id") or "")
                       for a in (ctx.get("attachments") or []) if a.get("file_id")}
    for fm in re.finditer(r"【文件参考：([^】]+)】[^\n]*\n(.*?)\n【文件参考结束：\1】", text, re.S):
        _name = fm.group(1).strip()
        _item = {"name": _name, "text": fm.group(2)}
        _fid = _attach_by_name.get(_name)
        if _fid:
            try:
                from app.services import attachment_store as _attach_store
                _p = _attach_store.resolve(_fid)
                if _p is not None:
                    _item["path"] = str(_p)
                    run_trace.emit(ctx, "plan.attachment_path", name=_name, path=str(_p))
            except Exception as exc:  # noqa: BLE001 [EXC-留痕] 路径解析失败仍可编译，但附件无本地路径
                _LOG.warning("附件路径解析失败（仍可编译，但该附件无本地路径）: %s", exc)
        attachments.append(_item)
    try:
        from app.services import capability_handlers as _ch
        catalog = _ch.lora_list()
        if catalog.get("loras"):
            lines = "\n".join(
                f"- {item['file']}" + (f"（触发词:{'/'.join(item['triggers'])}，建议权重:{item['suggested_weight']}）"
                                       if item["triggers"] else "")
                for item in catalog["loras"])
            attachments.append({"name": plan_compiler.LORA_CATALOG_NAME, "text":
                                f"共 {catalog['count']} 个：\n{lines}\n"
                                "用户提到近似名称时优先用上面的真实文件名；"
                                "宽泛指向（如「用 krea2 的」）存在多个候选时，"
                                "在计划卡里列出候选让用户选择，禁止替用户猜。"})
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 目录不可用时跳过，但编译上下文缺 LoRA 清单
        _LOG.warning("LoRA 目录读取失败（编译上下文缺 LoRA 清单，近似名无法对号）: %s", exc)
    try:
        from app.services import template_store
        templates = template_store.list_templates()
        if templates:
            lines = "\n".join(
                f"- {t.get('id')} {t.get('name') or ''}".rstrip()
                for t in templates)
            # 通用机制：把每个模板的暴露字段清单注入编译上下文，模型按 field
            # 名自由往 variants 写值（width/height/seed/任意节点参数），执行层按
            # binding 注入——换任何模板都适用，不硬编码特定参数名。
            field_lines = []
            for t in templates:
                exposed = t.get("exposed") or []
                if not isinstance(exposed, list):
                    continue
                brief = "、".join(
                    f"{f.get('field')}" + (f"/{f.get('binding')}" if f.get('binding') else "")
                    for f in exposed[:24]
                    if isinstance(f, dict) and f.get('field'))
                if brief:
                    field_lines.append(f"- {t.get('id')} {t.get('name') or ''} 暴露字段：{brief}")
            catalog_text = f"共 {len(templates)} 个：\n{lines}\n"
            if field_lines:
                catalog_text += ("暴露字段（variants 里按 field 名写值；binding 标注语义落点——"
                                 "如 width 的 binding 是 latent_width 即潜在尺寸节点；用户提到 "
                                 "latent/像素尺寸时，把数值写进对应语义字段，未暴露的节点"
                                 "（放大链路等）不在可写范围也不应去猜）：\n") \
                                + "\n".join(field_lines) + "\n"
            catalog_text += "用户指定模板时优先用上面的真实 id（或完整 name）；" \
                            "禁止写 TO_BE_RESOLVED、{{...}} 或任何占位符。"
            attachments.append({"name": plan_compiler.TEMPLATE_CATALOG_NAME,
                                "text": catalog_text})
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 模板库不可用时跳过，但编译上下文缺模板清单
        _LOG.warning("模板库读取失败（编译上下文缺模板清单）: %s", exc)
    recipe_catalog = _recipe_catalog_text()
    if recipe_catalog:
        # 固化流程清单进编译上下文：意图高度一致时编译器可编排 plan.instantiate_recipe 整条重放
        attachments.append({"name": plan_compiler.RECIPE_CATALOG_NAME,
                            "text": recipe_catalog})
    knowledge = _knowledge_catalog_text()
    if knowledge:
        # 固化知识库进编译上下文：流程规范/映射表对智能编造永远在线
        attachments.append({"name": plan_compiler.KNOWLEDGE_CATALOG_NAME,
                            "text": knowledge})

def _pc_prev_batch_and_flow_hint(ctx: CtxLike, text: str, compile_text: str,
                                 output_dir: str, attachments: list[dict]) -> dict | None:
    """批量延续注入：命中延续意图时把上一批计划变体与上次流程文档句柄**原地追加**。

    返回 `prev_batch`（未命中为 None）。同样原地改 `attachments`（理由见
    `_pc_collect_attachments`）。逐字自 `plan_compiler_node` 搬移。
    """
    prev_batch: dict | None = None
    try:
        prev_batch = plan_compiler.prev_batch_for(output_dir, compile_text)
        if prev_batch:
            attachments.append(plan_compiler.prev_batch_attachment(prev_batch))
            run_trace.emit(ctx, "plan.prev_batch", status="ok",
                           variants=len(prev_batch.get("variants") or []),
                           path=prev_batch.get("plan_path"))
    except Exception as exc:  # noqa: BLE001 - 延续注入失败不阻断编译
        run_trace.emit(ctx, "plan.prev_batch", status="error", error=str(exc))
        prev_batch = None
    flow_hint = _flow_doc_hint(ctx, text)
    if flow_hint:
        # current_flow_doc（固化链 §3 设计 A）：延续语命中时把上次流程文档句柄
        # 送进编译上下文，模型先读文档再编排，避免「接着做」断指代
        attachments.append({"name": "doc.flow_resume", "text": flow_hint})
    return prev_batch

def _pc_compile_and_finalize(ctx: CtxLike, state: AgentState, *, text: str,
                             intent_text: str, judge_text: str, trace: list,
                             output_dir: str, configured: set,
                             attachments: list[dict], compile_text: str,
                             prev_batch: dict | None) -> dict:
    """编译机械计划 → 校验兜底 → 回填段落引用 → 落盘 → 投递执行队列 → 渲染计划卡。

    ⚠ 与改造前逐字同形，含一处「既有形态」不得顺手修：投递 `submit_task` 抛
    `ValueError` 时只改 `queue_note`，`submitted` 仍**未绑定** ⇒ 末行
    `submitted.get(...)` 会抛 NameError（改造前既有行为，本步不修、不改语义）。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    try:
        outcome = plan_compiler.compile_plan(
            intent=compile_text, history=agent_context.history_text(ctx),
            attachments=attachments,
            repo_id=str(ctx.get("repo_id") or ctx.get("thread_id") or ""),
            comfyui_url=COMFYUI_BASE_URL,
            output_dir=output_dir, configured_models=configured,
            chat_base=ctx["chat_base"], chat_key=ctx["chat_key"],
            chat_model=ctx.get("route_model") or ctx["chat_model"],
            chat_fn=_live_chat(ctx),
            structured_chat_fn=ctx.get("structured_chat_fn"),
            proxy_kwargs=_proxy_kw(ctx),
            prev_batch=prev_batch,
            trace=_live_trace(ctx, "plan_compiler"),
        )
    except Exception as exc:  # noqa: BLE001 - 编译异常如实回复，不编造计划
        run_trace.emit(ctx, "agent.error", agent="plan_compiler", error=str(exc))
        return {"result_text": f"计划编译失败：{exc}", "trace": trace}
    if outcome.plan is None:
        if any("没有任何 submit/collect" in e for e in outcome.errors):
            # 2026-09-11 智能兜底：讨论/咨询型请求（「跟我讨论各服装的生图提示词」）
            # 会被规模词+生成词误带进机械计划——讨论本就不该有 submit 步骤，
            # 重试只会死循环撞同一堵墙。不再报错死路，降级自由循环（断点审批），
            # 让 LLM 直接读文档与用户讨论；真要出图时循环内照样能编排 submit。
            run_trace.emit(ctx, "plan.compile", status="fallback_fabric",
                           errors="; ".join(outcome.errors)[:300])
            trace = trace + ["🧭 意图与计划形态不符（疑似讨论型请求）→ 降级自由循环"]
            return _run_fabric_content_loop(ctx, state, intent_text=intent_text,
                                            judge_text=judge_text, text=text,
                                            trace=trace, output_dir=output_dir,
                                            configured=configured)
        return {"result_text": "计划编译未通过校验：\n- " + "\n- ".join(outcome.errors),
                "trace": trace}
    # 段落引用回填：模型为省输出上限会把变体写成 prompt_section 引用，
    # 编译期按预读附件机械抽取完整提示词（执行器无 LLM，必须在此回填）。
    try:
        filled = plan_compiler.fill_prompt_sections(outcome.plan, attachments)
        if filled:
            run_trace.emit(ctx, "plan.sections_filled", count=filled)
    except Exception as exc:  # noqa: BLE001 - 回填失败如实留痕
        run_trace.emit(ctx, "plan.sections_filled", status="error", error=str(exc))
    json_path = plan_compiler.save_plan(output_dir, outcome.plan.repo_id, outcome.plan)
    run_trace.emit(ctx, "plan.validated", status="ok", steps=len(outcome.plan.steps),
                   path=json_path)
    # P2：自动投递执行队列；durable/expensive 步骤由 P3 审批闸门拦到 awaiting_approval
    queue_note = ""
    try:
        from app.services import plan_tasks
        submitted = plan_tasks.submit_task(
            outcome.plan, output_dir=output_dir,
            repo_id=str(ctx.get("repo_id") or ctx.get("thread_id") or ""),
            configured_models=configured)
        queue_note = ("已投递执行队列" if not submitted["deduped"]
                      else f"与已有任务重复，复用 {submitted['task_id'][:8]}")
    except ValueError as exc:
        queue_note = f"投递被拒：{exc}"
    card = plan_compiler.render_plan_card(outcome.plan, json_path)
    approval_marker = f"\n\n[[plan:{submitted['task_id']}]]" if submitted.get("task_id") else ""
    return {"result_text": card + "\n" + queue_note + approval_marker,
            "trace": trace + ["📋 计划已落盘并投递执行队列（durable/expensive 需审批）"]}

def _pc_design_discussion_reply(ctx: CtxLike, state: AgentState, *, intent_text: str,
                                judge_text: str, text: str, trace: list,
                                output_dir: str, configured: set) -> dict | None:
    """设计讨论型：登记任务会话后降级自由循环（不编机械计划）。

    命中条件 = `is_design_discussion_intent(judge_text)` 或
    `_task_session_continuation(ctx, intent_text)`；未命中返回 None 交给下游分派。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    if not (plan_compiler.is_design_discussion_intent(judge_text)
            or _task_session_continuation(ctx, intent_text)):
        return None
    # 设计讨论型（2026-09-11）：讨论没有 submit/产出步骤，编机械计划必然撞
    # 「意图与计划不一致」；正道是自由循环——LLM 读文档直接讨论，要产出时
    # 循环内照样能编排。按合同模板注入灵感卡/历史附件/提示词规范。
    # 上下文合同 P1：任务会话登记——首链存原始任务句与材料，接续轮判定
    # 优先查登记（词表只兜底），登记缺失全部 fail-open 退回现行判定。
    trace = trace + ["💬 设计讨论 → 自由循环（不编机械计划）"]
    try:
        from app.services import task_session_store
        task_session_store.save({
            "task_type": "design_discussion",
            "intent": intent_text,
            "thread_id": str(ctx.get("thread_id") or ""),
            "repo_id": str(ctx.get("repo_id") or ""),
            "output_dir": output_dir,
            "materials": [
                {"file_id": str(_a.get("file_id") or ""),
                 "name": str(_a.get("name") or "")}
                for _a in (ctx.get("attachments") or [])
                if isinstance(_a, dict) and _a.get("file_id")
            ],
        })
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 登记失败不阻断讨论，但任务会话缺本轮登记
        _LOG.warning("设计讨论任务登记失败（讨论继续，但任务会话缺该登记）: %s", exc)
    return _run_fabric_content_loop(ctx, state, intent_text=intent_text,
                                    judge_text=judge_text, text=text,
                                    trace=trace, output_dir=output_dir,
                                    configured=configured,
                                    task_type="design_discussion")

def _pc_register_delegation(ctx: CtxLike, *, intent_text: str, judge_text: str,
                            output_dir: str) -> None:
    """上下文合同 P2b：委派型任务登记（materials 并集 / 首链意图保留）。

    一线程一活跃任务，后一个任务自然覆盖前一个（设计讨论接续判定随之失效，词表粘性兜底）。
    路由不消费委派登记（委派意图本身判定可靠），仅作任务卡 ①目标/②材料/⑤产物的登记源；
    登记失败不阻断。逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    if not (plan_compiler.is_delegation_intent(intent_text)
            or plan_compiler.is_doc_delegation_intent(judge_text)):
        return
    try:
        from app.services import task_session_store
        task_session_store.save({
            "task_type": "delegation",
            "intent": intent_text,
            "thread_id": str(ctx.get("thread_id") or ""),
            "repo_id": str(ctx.get("repo_id") or ""),
            "output_dir": output_dir,
            "materials": [
                {"file_id": str(_a.get("file_id") or ""),
                 "name": str(_a.get("name") or "")}
                for _a in (ctx.get("attachments") or [])
                if isinstance(_a, dict) and _a.get("file_id")
            ],
        })
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 登记失败不阻断委派，但任务会话缺该登记
        _LOG.warning("委派任务登记失败（委派继续，但任务会话缺该登记）: %s", exc)

def _pc_run_full_loop(ctx: CtxLike, state: AgentState, *, text: str, judge_text: str,
                      output_dir: str, configured: set, trace: list) -> dict | None:
    """full 访问模式：跑自由循环（ReAct），step_limit/error 中断后自动续跑（上限 2 次）。

    仅在 `_agent_access_mode() == ACCESS_FULL` 时生效，否则返回 None 交给下游分派。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    from app.services import capability_sandbox, fabric_loop, plan_tasks
    if plan_tasks._agent_access_mode() != capability_sandbox.ACCESS_FULL:
        return None
    from app.services import fabric_checkpoint
    lease = capability_sandbox.grant(
        subject=f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}",
        capabilities=[], ttl_seconds=86400, approved_by="full_mode",
        mode=capability_sandbox.ACCESS_FULL)
    history_text = _fabric_knowledge_history(ctx, text)
    cp_id = f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}"
    thread_id = str(ctx.get("thread_id") or "")
    repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "")

    def _cp_fn_full(step: int, msgs: list, steps: list, status: str) -> None:
        fabric_checkpoint.save({
            "id": cp_id, "status": status,
            "intent": text, "history": history_text,
            "delivery_intent": judge_text,
            "messages": msgs, "steps": steps,
            "lease_id": lease["id"], "pending_tool": "",
            "output_dir": output_dir, "repo_id": repo_id, "thread_id": thread_id,
        })

    _resume_full: dict | None = None
    _cpf = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
    if _cpf is not None:
        _resume_full = {"messages": list(_cpf.get("messages") or []),
                        "steps": list(_cpf.get("steps") or [])}
    # 2026-09-09 harness 自动化：step_limit/error 中断后自动续跑（上限 2 次），
    # 用户发一次指令即可跑完（条目数闸门在 fabric_loop 内保证未达标不 done）。
    outcome: Any = None
    _auto = 0
    while True:
        outcome = fabric_loop.run_loop(**_fabric_run_args(
            ctx, intent=text, history=history_text,
            access_mode=capability_sandbox.ACCESS_FULL,
            lease_id=lease["id"], output_dir=output_dir,
            repo_id=repo_id,
            configured=configured, images=list(state.get("images") or []),
            resume=_resume_full, checkpoint_fn=_cp_fn_full,
            delivery_intent=judge_text,
        ))
        if outcome.status in ("step_limit", "error") and _auto < 2:
            _cp2 = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
            if _cp2 is None:
                break
            _auto += 1
            _resume_full = {"messages": list(_cp2.get("messages") or []),
                            "steps": list(_cp2.get("steps") or [])}
            run_trace.emit(ctx, "fabric.auto_resume", round=_auto,
                           status=outcome.status, steps=len(outcome.steps))
            continue
        break
    if outcome.status == "done":
        fabric_checkpoint.delete(cp_id)
    return _fabric_finalize(outcome, text, trace, ctx, output_dir)

def _pc_ask_recipe_offer(ctx: CtxLike, *, output_dir: str, trace: list, intent_text: str,
                         judge_text: str, recovered) -> dict | None:
    """事前固化询问：值得固化的交付类任务、且已保留流程清单里没有同类时，先问一句要不要固化。

    ⚠ 只在「新任务」上问（答复轮已被 `recovered` 消费掉）；清单里已有同类时不问，
    改判能否硬重放。返回节点结果 dict；不问或不必问则返回 None。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    if recovered is not None:
        return None
    from app.services import recipe_match, recipe_offer_store
    if recipe_match.is_solidifiable(judge_text):
        _offer_hit, _offer_score = recipe_match.similar_recipe(judge_text)
        if _offer_hit is not None:
            # 清单里已有同类 → 不问，改判能不能硬重放。
            _replay_reply = _recipe_hard_replay(
                _offer_hit, _offer_score, ctx, output_dir, trace)
            if _replay_reply is not None:
                return _replay_reply
        else:
            _offer_fp = recipe_match.fingerprint(judge_text)
            _prev_offer = _recipe_offer_pending(ctx, output_dir)
            # 同一意图只打扰一次：用户没理会、又原样重发同一条指令时直接放行去跑。
            if not (_prev_offer and _prev_offer.get("fingerprint") == _offer_fp):
                recipe_offer_store.save({
                    "id": f"offer:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}",
                    "fingerprint": _offer_fp,
                    "intent": intent_text,
                    "delivery_intent": judge_text,
                    "output_dir": output_dir,
                    "repo_id": str(ctx.get("repo_id") or ""),
                    "thread_id": str(ctx.get("thread_id") or ""),
                })
                run_trace.emit(ctx, "recipe.offer", status="asked", fingerprint=_offer_fp)
                return {
                    "result_text": (
                        "这次任务在已固化的流程清单里没有同类。要不要为它固化成一条"
                        "可复用流程？以后同类请求可以整条重放、省 token。\n\n"
                        "- 回复「要」→ 现在照常执行，跑完把这次的步骤固化成流程；\n"
                        "- 回复「直接跑」→ 照常执行、不固化（默认）。"),
                    "trace": trace + ["🧩 询问是否固化流程"]}
    return None

def _pc_approval_word_reply(ctx: CtxLike, text: str, output_dir: str, trace: list) -> dict | None:
    """审批词直通车：消息恰为审批/取消词时直接审批或取消最近的 awaiting_approval 计划。

    返回节点结果 dict；不是审批词则返回 None（调用方继续后续分派）。
    ⚠ 无待审批计划时先探 approval 自由循环 checkpoint（2026-09-06 断点续跑），
    再回「当前没有待审批的计划。」
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    _word = _pc_approval_word(text)
    if _word:
        from app.services import plan_tasks as _pt
        _pending = [t for t in _pt.list_tasks(output_dir=output_dir, limit=10)
                    if t["status"] == "awaiting_approval"]
        if not _pending:
            # 无待审批计划时，看 approval 自由循环 checkpoint（2026-09-06 断点续跑）
            _fabric = _fabric_approval_word(_word, ctx, output_dir, trace)
            if _fabric is not None:
                return _fabric
            return {"result_text": "当前没有待审批的计划。", "trace": trace}
        _task = _pending[0]
        if _word in ("取消", "cancel", "撤销"):
            _pt.cancel_task(_task["id"])
            return {"result_text": f"已取消计划：{_task['intent'][:120]}", "trace": trace}
        _pt.approve_task(_task["id"])
        return {"result_text": f"已批准计划：{_task['intent'][:120]}\n执行器继续执行，"
                               f"进度见后台活动面板。", "trace": trace}
    return None

def plan_compiler_node(state: AgentState) -> dict:
    """智能编造节点：full 模式跑自由循环（ReAct）；approval 模式内容生成型任务
    （卡/书/文档交付，固化02/03）走自由循环断点审批，批量生图等确定性任务走计划编译。"""
    ctx = state["_ctx"]
    text = state.get("user_text", "")
    trace = state.get("trace", []) + ["📋 智能编造计划编译中…"]
    run_trace.emit(ctx, "agent.started", agent="plan_compiler")
    output_dir = str(ctx.get("output_dir") or "").strip()
    if not output_dir:
        return {"result_text": "请先选择作品（计划文档需要落盘到作品文件夹）。", "trace": trace}
    configured = _pc_configured_models(ctx)
    # 审批词直通车：对话里发「批准/同意/执行/取消」不再编译新计划，
    # 直接审批/取消最近一个 awaiting_approval 计划（修复 2026-09-02 实锤：
    # 用户发「批准」被当成新任务编译，上下文断档）。
    _approval_reply = _pc_approval_word_reply(ctx, text, output_dir, trace)
    if _approval_reply is not None:
        return _approval_reply
    # 事前固化询问的答复（2026-09-10）：回「要」→ 照常跑并固化；回「直接跑」→ 照常跑
    # 但不固化。两条分支都必须用**询问时存下的原始任务文本**继续——本轮消息只是这两个
    # 词，拿它当 intent 会把真正的任务整个丢掉（supervisor 已零 LLM 强制路由到这里）。
    _offer_recovered = _recipe_offer_answer(text, ctx, output_dir)
    if _offer_recovered is not None:
        trace = trace + ["🧩 已按固化询问答复继续"]
        if _offer_recovered["answer"] == "skip":
            # 用户已明确不要为这次固化流程 → 抑制 done 时的草稿卡。否则跑完照样弹
            # 「保留/不保留」，那次询问就成了纯噪声（问与不问结果一样）。
            ctx["_suppress_recipe"] = True
        else:
            ctx.pop("_suppress_recipe", None)
    # 访问模式分派：
    # - full：自由循环（ReAct），模型逐步决定调用哪个能力，观察结果后自行修正；
    # - approval 内容生成型任务（卡/书/文档交付，固化02/03 场景）：走自由循环断点审批——
    #   机械计划执行器不调 LLM，承载不了「读素材→LLM 写条目」的内容生成
    #   （2026-09-06 实锤占位条目/生图误注入），自由循环 + 逐能力审批才是正解；
    # - approval 其余（批量生图等确定性任务）：继续走下方计划编译+机械执行。
    #
    # 判定用文本（full/approval 共用，2026-09-07 治本 + 2026-09-10 扩展）：
    # 短指令（「继续补写」「进行补写内容」）零 LLM 判定丢语义——supervisor 有完整历史
    # 能判对 plan，本节点只看本轮短句却漏判 → 掉进机械 compile_plan（实锤：1 步
    # check_density 计划 partial 卡死）。修法：本轮不命中时拼历史用户消息再判一次。
    # 2026-09-10：该判定文本还作为 **delivery_intent** 决定能力面与前序产出档案
    # （不改 intent）——「先对话积累上下文，再说一句『整理成设定总集』」时，若用本轮
    # 原文判定，白名单与前序产出扫描都会落空。
    intent_text = str(ctx.get("message") or text or "").strip()
    if _offer_recovered is not None and _offer_recovered["intent"]:
        # 答复轮：本轮原文只是「要 / 直接跑」，真正的任务文本在询问记录里。
        intent_text = _offer_recovered["intent"]
        text = intent_text
    _fabric_judge = _pc_judge_text(intent_text, ctx.get("history"))
    # ── 事前固化询问（2026-09-10）：值得固化且清单无同类时先问一句（详见接缝 docstring）
    _offer_reply = _pc_ask_recipe_offer(
        ctx, output_dir=output_dir, trace=trace, intent_text=intent_text,
        judge_text=_fabric_judge, recovered=_offer_recovered)
    if _offer_reply is not None:
        return _offer_reply
    _full_reply = _pc_run_full_loop(ctx, state, text=text, judge_text=_fabric_judge,
                                    output_dir=output_dir, configured=configured,
                                    trace=trace)
    if _full_reply is not None:
        return _full_reply
    _pc_register_delegation(ctx, intent_text=intent_text, judge_text=_fabric_judge,
                            output_dir=output_dir)
    if plan_compiler.is_doc_delegation_intent(_fabric_judge):
        # approval 内容生成型：自由循环断点审批（读文件/写条目/落盘逐个确认）
        return _run_fabric_content_loop(ctx, state, intent_text=intent_text,
                                        judge_text=_fabric_judge, text=text,
                                        trace=trace, output_dir=output_dir,
                                        configured=configured)
    _discussion_reply = _pc_design_discussion_reply(
        ctx, state, intent_text=intent_text, judge_text=_fabric_judge, text=text,
        trace=trace, output_dir=output_dir, configured=configured)
    if _discussion_reply is not None:
        return _discussion_reply

    # 编译期预读：本轮消息 + 历史用户消息里显式写出的本地文本文件（仅用户明示的
    # 路径，容量封顶）。历史用户消息兜底「根据上一轮的批量生成进行修改」这类不带
    # 路径的指代——预读必须在编译期完成，执行器无 LLM，file.read_text 读出的内容
    # 填不进 variants（2026-09-03 实锤：模型编了「套装1..10」占位）。
    attachments: list[dict] = []
    prev_batch: dict | None = None
    try:
        _pc_collect_attachments(ctx, text, attachments)
        # 批量延续（方案 A，2026-09-05 实锤）：续轮编译上下文没有原文档（附件只随
        # 当条消息注入、历史只带可见文本），模型凭记忆重写提示词=幻觉。命中延续
        # 意图时把上一批计划的真实变体注入编译上下文，prompt 由编译期按 name 回填。
        # 判定必须用剥离【文件参考】块后的指令文本（2026-09-05 实锤：100k 字小说
        # 全文会误命中批量词+动作词，把生图上一批注入合集卡任务）。
        compile_text = re.sub(r"【文件参考：.*?【文件参考结束：[^】]*】", "", text, flags=re.S).strip()
        prev_batch = _pc_prev_batch_and_flow_hint(
            ctx, text, compile_text, output_dir, attachments)
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "plan.attachments", status="error", error=str(exc))
    # compile_text 已在上方 prev_batch 判定处提前计算（剥离【文件参考】块）；
    # 附件正文已进 attachments，避免大文档全文在 user intent 里再送一遍拖慢编译。
    return _pc_compile_and_finalize(
        ctx, state, text=text, intent_text=intent_text, judge_text=_fabric_judge,
        trace=trace, output_dir=output_dir, configured=configured,
        attachments=attachments, compile_text=compile_text, prev_batch=prev_batch)
