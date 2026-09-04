"""智能编造 Agent 自由循环（P6）：模型逐步决定调用哪个能力，结果回填，直到完成。

对标 DeepSeek Harness 的 agent loop：
- 一个 step = 一次模型决策 + 可选一次工具执行 + 结果回填；
- 模型看到能力清单（工具 schema），自由选择下一步调用什么；
- 审批/沙盒在工具执行前拦截（capability_sandbox 租约，approval/full 两档）；
- 不再要求"编译成固定计划后机械执行"——模型可观察结果并自行修正。

第一版边界：full 模式跑完整自由循环；approval 模式遇到 durable/expensive
且无租约时暂停返回 awaiting_approval（批准后由调用方继续）。

带图任务（如看图反推外貌→生成套装文档）：images 非空时首条 user 消息变为
多模态内容块（text + image_url），模型调用走 chat_messages 多消息通道；
structured 原生通道传输的是 JSON 字符串，载不动图片，带图时跳过。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from app.services import capability_registry, capability_sandbox, structured_output


class FabricDecision(BaseModel):
    """模型每步的决策：调用一个能力，或宣布完成。"""

    tool: str = ""              # 要调用的 operation；done=true 时可为空
    params: dict[str, Any] = field(default_factory=dict)
    done: bool = False          # 模型认为任务已完成
    reply: str = ""             # 完成时给用户的最终回复


@dataclass
class FabricOutcome:
    status: str = "done"        # done | awaiting_approval | error | step_limit
    reply: str = ""
    steps: list[dict] = field(default_factory=list)   # 每步 {tool, params, ok, result/error}
    error: str = ""
    lease_id: str = ""          # 需要批准时，批准端点要用的租约（subject）
    pending_tool: str = ""      # 等待批准的工具


def _manifest_lines(capabilities: list[dict]) -> str:
    lines = []
    for item in capabilities:
        if not item.get("available", True):
            continue
        schema = item.get("params_schema") or {}
        required = set(schema.get("required") or [])
        props = schema.get("properties", {})
        params = "、".join(
            f"{name}*" if name in required else f"{name}(可选)" for name in props)
        lines.append(
            f"- {item['operation']}：{item['description']} 参数：{params or '无'}")
    return "\n".join(lines)


_SYSTEM = (
    "你是 Demiurge 的智能编造 Agent。你可以自由调用工具完成用户目标：\n"
    "每步只输出一个 JSON 对象，格式二选一：\n"
    "1) 调用工具：{\"tool\": \"清单里的 operation\", \"params\": {具体参数}}\n"
    "2) 宣布完成：{\"done\": true, \"reply\": \"给用户的最终回复\"}\n"
    "调用工具后，你会收到工具结果；观察结果，如果失败就换方案或修复，不要重复同一个失败调用。\n"
    "所有参数必须写具体值，禁止 {{...}}、TO_BE_RESOLVED 等占位符。\n"
    "【可用工具清单】\n__MANIFEST__\n"
    "【当前输出目录】__OUTPUT_DIR__\n"
    "【用户目标】\n__INTENT__"
)


def _chat_messages(base_url: str, api_key: str, model: str,
                   messages: list[dict], **kwargs) -> str:
    """带图自由循环的默认模型通道：多消息列表直发（content 允许多模态内容块）。"""
    from app.services import llm
    return llm.chat_messages(base_url, api_key, model, messages, **kwargs)


def _dispatch(operation: str, params: dict) -> dict:
    cap = capability_registry.get(operation)
    if cap is None:
        raise ValueError(f"能力清单里没有「{operation}」")
    if not cap.handler:
        raise ValueError(f"能力「{operation}」未注册 handler")
    module_name, _, func_name = cap.handler.partition(":")
    import importlib
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    # 只透传 schema 声明的参数（薄适配纪律）
    allowed = set((cap.params_schema or {}).get("properties") or {})
    filtered = {k: v for k, v in params.items() if k in allowed}
    result = func(**filtered)
    return result if isinstance(result, dict) else {"result": result}


def run_loop(*, intent: str, history: str = "", capabilities: list[dict] | None = None,
             access_mode: str = capability_sandbox.ACCESS_APPROVAL,
             lease_id: str = "", subject: str = "",
             output_dir: str = "", repo_id: str = "",
             configured_models: set[str] | frozenset[str] = frozenset(),
             chat_base: str = "", chat_key: str = "", chat_model: str = "",
             chat_fn: Callable | None = None, structured_chat_fn: Callable | None = None,
             images: list[str] | None = None,
             chat_messages_fn: Callable | None = None,
             temperature: float = 0.2, proxy_kwargs: dict | None = None,
             max_steps: int = 24, trace: Callable | None = None) -> FabricOutcome:
    """自由循环。返回 FabricOutcome。images 非空时走多模态消息通道。"""
    caps = capabilities or capability_registry.with_availability(configured_models)
    images = [u for u in (images or []) if str(u).strip()]
    system = (_SYSTEM.replace("__MANIFEST__", _manifest_lines(caps))
              .replace("__OUTPUT_DIR__", output_dir or "（未指定）")
              .replace("__INTENT__", intent))
    if images:
        system += (f"\n【附图】本轮随消息附带 {len(images)} 张图片（在首条用户消息里），"
                   "需要看图的任务（如反推外貌特征）直接观察图片内容，不要声称看不到图。")
    messages: list[dict] = [{"role": "system", "content": system}]
    if images:
        # 多模态首条消息：图片必须以 image_url 内容块直达模型，不能进 JSON 字符串协议
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": history or "（用户目标见系统提示，请结合所附图片完成。）",
        }]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        messages.append({"role": "user", "content": content})
    elif history:
        messages.append({"role": "user", "content": history})
    outcome = FabricOutcome()
    call_kwargs: dict[str, Any] = {"temperature": temperature, "max_tokens": 16000,
                                   **(proxy_kwargs or {})}
    call_args = (chat_base, chat_key, chat_model, system)
    active_lease = lease_id

    for step_index in range(1, max_steps + 1):
        decision: FabricDecision | None = None
        try:
            if images:
                # 带图：structured 原生通道传 JSON 字符串载不动图片，直接走多消息文本通道
                sender = chat_messages_fn if callable(chat_messages_fn) else _chat_messages
                result = structured_output.validate_text(
                    sender(chat_base, chat_key, chat_model, messages, **call_kwargs),
                    FabricDecision, trace=trace,
                )
            else:
                result = structured_output.invoke(
                    FabricDecision,
                    native=(lambda u=json.dumps(messages, ensure_ascii=False):
                            structured_chat_fn(*call_args[:4], u, schema=FabricDecision, **call_kwargs))
                    if callable(structured_chat_fn) else None,
                    legacy=lambda u=json.dumps(messages, ensure_ascii=False):
                        chat_fn(*call_args[:4], u, **call_kwargs),
                    trace=trace,
                )
            decision = result.value
        except Exception as exc:  # noqa: BLE001 - 模型决策失败如实返回
            outcome.status = "error"
            outcome.error = f"第 {step_index} 步模型决策失败：{exc}"
            return outcome

        if decision.done:
            outcome.status = "done"
            outcome.reply = decision.reply.strip() or "已完成。"
            return outcome
        operation = (decision.tool or "").strip()
        if not operation:
            outcome.status = "error"
            outcome.error = f"第 {step_index} 步既没调用工具也没宣布完成"
            return outcome

        params = decision.params or {}
        step_record = {"tool": operation, "params": params, "ok": False, "result": None}
        # 审批/沙盒闸门（工具执行前拦截）
        cap = capability_registry.get(operation)
        if cap is None:
            step_record["error"] = f"能力清单里没有「{operation}」"
            outcome.steps.append(step_record)
            messages.append({"role": "assistant", "content": json.dumps(
                {"tool": operation, "params": params}, ensure_ascii=False)})
            messages.append({"role": "user", "content": json.dumps(
                {"tool_result": step_record["error"]}, ensure_ascii=False)})
            continue
        level = cap.side_effect_level
        if level in ("durable", "expensive"):
            try:
                capability_sandbox.authorize(active_lease, operation, path=output_dir)
            except PermissionError:
                outcome.status = "awaiting_approval"
                outcome.pending_tool = operation
                outcome.error = f"工具 {operation} 需要批准"
                return outcome
        # 工作区归一：shell 的 cwd 默认锁定为当前作品目录（模型不得随意指定）
        if operation == "project.run_shell" and not str(params.get("cwd") or "").strip():
            params["cwd"] = output_dir or ""
        # 配方重放的落盘域同样环境归一（handler 内还有配置真源等值校验兜底）
        if operation == "plan.instantiate_recipe" and not str(params.get("output_dir") or "").strip():
            params["output_dir"] = output_dir or ""
        # 收尾闸门取数归一：scan_anonymity 不给 entries 时自动指向本作品世界书快照
        if (operation == "novel.scan_anonymity" and not params.get("entries")
                and not str(params.get("base") or "").strip()):
            params["base"] = output_dir or ""
            params["repo_id"] = repo_id or ""
        # 固化链续跑句柄（§3 设计 A）：doc.create_repo 的落盘域与 repo_id 同样环境归一
        if operation == "doc.create_repo":
            if not str(params.get("base") or "").strip():
                params["base"] = output_dir or ""
            if not str(params.get("repo_id") or "").strip():
                params["repo_id"] = repo_id or ""
        if trace is not None:
            trace("tool.call", operation=operation, params=params)
        try:
            result_payload = _dispatch(operation, params)
            step_record["ok"] = True
            step_record["result"] = result_payload
            result_text = json.dumps(result_payload, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 - 单步失败回填给模型，让它换方案
            step_record["error"] = str(exc)
            result_text = json.dumps({"tool_error": str(exc)}, ensure_ascii=False)
        if trace is not None:
            trace("tool.result", operation=operation, ok=step_record["ok"],
                  result=step_record.get("result") or step_record.get("error"))
        outcome.steps.append(step_record)
        messages.append({"role": "assistant", "content": json.dumps(
            {"tool": operation, "params": params}, ensure_ascii=False)})
        messages.append({"role": "user", "content": json.dumps(
            {"tool_result": result_text}, ensure_ascii=False)})

    outcome.status = "step_limit"
    outcome.error = f"达到最大步数 {max_steps}，仍未宣布完成"
    return outcome
