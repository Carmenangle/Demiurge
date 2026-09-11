"""统一结构化输出 Runtime：原生 JSON Schema 优先，文本解析与 Pydantic 校验兜底。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
TraceFn = Callable[..., None]
InvokeFn = Callable[[], Any]


class StructuredOutputError(ValueError):
    """模型输出不是目标 JSON Schema。"""


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    value: T
    strategy: str
    raw: str = ""


def _json_candidates(raw: str):
    """按出现顺序产出文本里所有可完整解码的 JSON 值。

    正常输出首个即根对象；模型回显合同示例、输出被截断或夹带说明文字时，
    可解析的目标对象可能不在第一个位置——逐候选交给 schema 校验兜底。
    """
    text = (raw or "").strip()
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        if text[index] not in "[{":
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
            yield value
            index += end
        except json.JSONDecodeError:
            index += 1


def _unwrap_tool_calls(value: Any) -> dict[str, Any] | None:
    """模型误走工具调用时，输出是 {"tool_calls":[{...,"function":{"arguments": {...}}}]}。

    把最外层 tool_calls 解开：优先取第一个含 intent 键的 arguments，否则取第一个
    可解析的 arguments dict。不是工具调用包装返回 None。
    """
    if not isinstance(value, dict) or "tool_calls" not in value:
        return None
    candidates: list[dict[str, Any]] = []
    for call in value.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        if not isinstance(fn, dict):
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                continue
        if isinstance(args, dict):
            candidates.append(args)
    for candidate in candidates:
        if "intent" in candidate:
            return candidate
    return candidates[0] if candidates else None


def parse_object(raw: str) -> dict[str, Any]:
    for value in _json_candidates(raw):
        if not isinstance(value, dict):
            raise StructuredOutputError("模型返回的 JSON 根必须是 JSON 对象")
        unwrapped = _unwrap_tool_calls(value)
        if unwrapped is not None:
            return unwrapped
        return value
    raise StructuredOutputError("模型未返回完整 JSON（输出可能被截断）")


def parse_model(raw: str, schema: type[T]) -> T:
    first_error: ValidationError | None = None
    for value in _json_candidates(raw):
        if isinstance(value, dict):
            unwrapped = _unwrap_tool_calls(value)
            if unwrapped is not None:
                value = unwrapped
        try:
            return schema.model_validate(value)
        except ValidationError as exc:
            if first_error is None:
                first_error = exc  # 首个候选通常是模型真正的根对象，报错以它为准
    if first_error is None:
        raise StructuredOutputError("模型未返回完整 JSON（输出可能被截断）")
    raise StructuredOutputError(
        f"结构化输出未通过 {schema.__name__} 校验：{first_error}") from first_error


def validate_text(raw: str, schema: type[T], *, trace: TraceFn | None = None) -> StructuredResult[T]:
    """校验已经由旧式文本调用得到的输出，并统一记录策略与 Schema。"""
    try:
        value = parse_model(raw, schema)
    except StructuredOutputError as exc:
        _trace(trace, schema=schema, strategy="legacy_text", status="error",
               error=str(exc), raw_chars=len(raw or ""))
        raise
    _trace(trace, schema=schema, strategy="legacy_text", status="ok", raw_chars=len(raw or ""))
    return StructuredResult(value=value, strategy="legacy_text", raw=raw)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")


def invoke(
    schema: type[T],
    *,
    legacy: InvokeFn,
    native: InvokeFn | None = None,
    trace: TraceFn | None = None,
) -> StructuredResult[T]:
    """统一结构化调用接缝。

    Provider Adapter 可提供原生约束调用；不支持时只回退一次旧文本调用，避免
    业务 Module 自行实现 JSON 截取、校验、重试与 Trace。
    """
    native_error = ""
    if native is not None:
        try:
            parsed = native()
            unwrapped = _unwrap_tool_calls(parsed) if isinstance(parsed, dict) else None
            if unwrapped is not None:
                parsed = unwrapped
            value = parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
            _trace(trace, schema=schema, strategy="native_json_schema", status="ok")
            return StructuredResult(value=value, strategy="native_json_schema")
        except Exception as exc:  # noqa: BLE001 - 能力协商失败必须回退
            native_error = str(exc)

    try:
        raw = _response_text(legacy())
        value = parse_model(raw, schema)
    except Exception as exc:  # noqa: BLE001 - 对外统一结构化错误类型
        error = f"native={native_error}; legacy={exc}" if native_error else str(exc)
        _trace(trace, schema=schema, strategy="legacy_text", status="error", error=error)
        raise StructuredOutputError(error) from exc
    _trace(trace, schema=schema, strategy="legacy_text", status="ok",
           error=native_error, raw_chars=len(raw))
    return StructuredResult(value=value, strategy="legacy_text", raw=raw)


def _trace(trace: TraceFn | None, *, schema: type[BaseModel], strategy: str,
           status: str, error: str = "", **extra: Any) -> None:
    if trace is None:
        return
    payload = dict(
        schema=f"{schema.__module__}.{schema.__name__}",
        schema_version="1",
        strategy=strategy,
        status=status,
        validation_error=error,
    )
    payload.update({key: value for key, value in extra.items() if value is not None})
    trace("structured.output", **payload)


def invoke_model(model: Any, messages: Any, schema: type[T],
                 *, trace: TraceFn | None = None) -> StructuredResult[T]:
    """调用 LangChain 模型；原生 JSON Schema 不可用时自动回退旧文本 Adapter。"""
    def native_call() -> Any:
        adapter = model.with_structured_output(schema, method="json_schema")
        return adapter.invoke(messages)

    return invoke(
        schema,
        native=native_call,
        legacy=lambda: model.invoke(messages),
        trace=trace,
    )
