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


def _json_value(raw: str) -> Any:
    text = (raw or "").strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise StructuredOutputError("模型未返回完整 JSON")


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
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
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
    value = _json_value(raw)
    if not isinstance(value, dict):
        raise StructuredOutputError("模型返回的 JSON 根必须是 JSON 对象")
    unwrapped = _unwrap_tool_calls(value)
    if unwrapped is not None:
        return unwrapped
    return value


def parse_model(raw: str, schema: type[T]) -> T:
    try:
        return schema.model_validate(parse_object(raw))
    except ValidationError as exc:
        raise StructuredOutputError(f"结构化输出未通过 {schema.__name__} 校验：{exc}") from exc


def validate_text(raw: str, schema: type[T], *, trace: TraceFn | None = None) -> StructuredResult[T]:
    """校验已经由旧式文本调用得到的输出，并统一记录策略与 Schema。"""
    try:
        value = parse_model(raw, schema)
    except StructuredOutputError as exc:
        _trace(trace, schema=schema, strategy="legacy_text", status="error", error=str(exc))
        raise
    _trace(trace, schema=schema, strategy="legacy_text", status="ok")
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
    _trace(trace, schema=schema, strategy="legacy_text", status="ok", error=native_error)
    return StructuredResult(value=value, strategy="legacy_text", raw=raw)


def _trace(trace: TraceFn | None, *, schema: type[BaseModel], strategy: str,
           status: str, error: str = "") -> None:
    if trace is None:
        return
    trace(
        "structured.output",
        schema=f"{schema.__module__}.{schema.__name__}",
        schema_version="1",
        strategy=strategy,
        status=status,
        validation_error=error,
    )


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
