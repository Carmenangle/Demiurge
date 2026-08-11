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
