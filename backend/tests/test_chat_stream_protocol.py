import pytest

from app.services import chat_stream_protocol as protocol


@pytest.mark.parametrize(("source", "event_type", "data"), [
    ({"trace": "主管选择 image"}, "trace", {"text": "主管选择 image"}),
    ({"delta": "回答"}, "delta", {"text": "回答"}),
    ({"image": "local://image", "id": "i1", "regeneration": {"prompt": "p"}},
     "image", {"url": "local://image", "id": "i1", "regeneration": {"prompt": "p"}}),
    ({"video": "local://video", "id": "v1"}, "video",
     {"url": "local://video", "id": "v1"}),
    ({"insp": {"query": "服装"}}, "inspiration", {"card": {"query": "服装"}}),
    ({"approval": {"id": "a1"}}, "approval", {"approval": {"id": "a1"}}),
    ({"route_choice": {"id": "r1"}}, "route_choice", {"choice": {"id": "r1"}}),
    ({"interrupted": True}, "interrupted", {}),
    ({"error": "失败"}, "error", {"message": "失败"}),
])
def test_encode_event_is_versioned_discriminated_union(source, event_type, data):
    assert protocol.encode_event(source) == {
        "protocol": "laf-chat-stream",
        "version": 1,
        "type": event_type,
        "data": data,
    }


def test_done_is_owned_by_sse_transport():
    assert protocol.encode_event({"done": True}) is None


def test_unknown_or_compound_event_is_rejected():
    with pytest.raises(ValueError, match="只能包含一种"):
        protocol.encode_event({"delta": "text", "image": "url"})
    with pytest.raises(ValueError, match="只能包含一种"):
        protocol.encode_event({"new_field": "not registered"})
