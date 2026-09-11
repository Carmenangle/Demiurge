from pydantic import BaseModel

from app.services import structured_output


class Decision(BaseModel):
    route: str
    confidence: str = "high"


def test_parse_model_accepts_fenced_json_and_validates_schema():
    value = structured_output.parse_model(
        "说明\n```json\n{\"route\":\"answer\",\"confidence\":\"low\"}\n```",
        Decision,
    )

    assert value.route == "answer"
    assert value.confidence == "low"


def test_invoke_prefers_native_json_schema_then_falls_back_to_text():
    events: list[tuple[str, dict]] = []

    class NativeFailure:
        def invoke(self, _messages):
            raise RuntimeError("provider does not support response_format")

    class Model:
        def with_structured_output(self, _schema, **_kwargs):
            return NativeFailure()

        def invoke(self, _messages):
            return type("Reply", (), {"content": '{"route":"answer"}'})()

    result = structured_output.invoke_model(
        Model(), [("system", "route"), ("human", "hello")], Decision,
        trace=lambda event, **data: events.append((event, data)),
    )

    assert result.value.route == "answer"
    assert result.strategy == "legacy_text"
    assert events[-1][1]["status"] == "ok"
    assert events[-1][1]["strategy"] == "legacy_text"


def test_parse_model_unwraps_tool_calls_payload():
    # 模型误走工具调用模式：外层是 tool_calls，真正的 schema JSON 在 function.arguments
    raw = (
        '{"tool_calls":[{"id":"call_1","type":"function",'
        '"function":{"name":"GenerationPlan","arguments":"{\\"intent\\":\\"批量出图\\",'
        '\\"repo_id\\":\\"work\\",\\"budgets\\":{\\"max_steps\\":3,\\"max_gpu_tasks\\":14,'
        '\\"max_llm_calls\\":0},\\"steps\\":[],\\"approval_required\\":[]}"}}]}'
    )
    from app.services.structured_contracts import GenerationPlan
    plan = structured_output.parse_model(raw, GenerationPlan)
    assert plan.intent == "批量出图"
    assert plan.budgets.max_gpu_tasks == 14


def test_parse_object_rejects_non_object_root():
    try:
        structured_output.parse_object("[1,2,3]")
    except structured_output.StructuredOutputError as exc:
        assert "JSON 对象" in str(exc)
    else:
        raise AssertionError("应拒绝数组根")


def test_invoke_prefers_native_adapter_without_spending_legacy_call():
    calls: list[str] = []

    result = structured_output.invoke(
        Decision,
        native=lambda: calls.append("native") or {"route": "generate"},
        legacy=lambda: calls.append("legacy") or '{"route":"answer"}',
    )

    assert result.value.route == "generate"
    assert result.strategy == "native_json_schema"
    assert calls == ["native"]


def test_invoke_falls_back_once_when_native_adapter_is_unavailable():
    calls: list[str] = []

    def unavailable():
        calls.append("native")
        raise RuntimeError("unsupported")

    result = structured_output.invoke(
        Decision,
        native=unavailable,
        legacy=lambda: calls.append("legacy") or '{"route":"answer"}',
    )

    assert result.value.route == "answer"
    assert result.strategy == "legacy_text"
    assert calls == ["native", "legacy"]


# ── 多候选解析（2026-09-05 实锤：模型回显合同示例 + 正文被截断，首个可解码对象
#    是提示词里的示例 {"name": "套装名"...}，旧解析器拿它当根直接判死）──────────

def test_parse_model_skips_echoed_example_and_finds_real_plan():
    from app.services.structured_contracts import GenerationPlan
    real = ('{"intent": "批量出图", "repo_id": "work", '
            '"budgets": {"max_steps": 2, "max_gpu_tasks": 1, "max_llm_calls": 0}, '
            '"steps": [], "approval_required": []}')
    raw = ('输出合同示例：{"name": "套装名", "prompt_section": "标题关键词"}\n'
           "真正的计划：" + real)
    plan = structured_output.parse_model(raw, GenerationPlan)
    assert plan.intent == "批量出图"


def test_parse_model_truncated_output_reports_clear_error():
    from app.services.structured_contracts import GenerationPlan
    # 输出在 steps 中途被截断：没有任何可完整解码的计划对象
    raw = '{"intent": "出图", "steps": [{"id": "s1", "opera'
    try:
        structured_output.parse_model(raw, GenerationPlan)
    except structured_output.StructuredOutputError as exc:
        assert "未返回完整 JSON" in str(exc)
    else:
        raise AssertionError("截断输出应报错")


def test_parse_model_all_candidates_invalid_reports_first_error():
    from app.services.structured_contracts import GenerationPlan
    raw = '{"name": "套装名", "prompt_section": "标题关键词"}'
    try:
        structured_output.parse_model(raw, GenerationPlan)
    except structured_output.StructuredOutputError as exc:
        assert "结构化输出未通过 GenerationPlan 校验" in str(exc)
        assert "intent" in str(exc)
    else:
        raise AssertionError("示例对象不是合法计划，应报错")


def test_trace_includes_raw_chars_for_diagnosis():
    events: list[dict] = []
    structured_output.validate_text(
        '{"route":"answer"}', Decision,
        trace=lambda event, **data: events.append(data))
    assert events[-1]["raw_chars"] == len('{"route":"answer"}')
