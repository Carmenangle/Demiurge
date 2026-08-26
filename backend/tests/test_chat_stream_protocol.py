import pytest

from app.services import chat_stream_protocol as protocol


@pytest.mark.parametrize(("source", "event_type", "data"), [
    ({"trace": "主管选择 image"}, "trace", {"text": "主管选择 image"}),
    ({"delta": "回答"}, "delta", {"text": "回答"}),
    ({"replace": "最终回答"}, "replace", {"text": "最终回答"}),
    ({"route": "roleplay"}, "route", {"route": "roleplay"}),
    ({"image": "local://image", "id": "i1", "regeneration": {"prompt": "p"}},
     "image", {"url": "local://image", "id": "i1", "regeneration": {"prompt": "p"}}),
    ({"video": "local://video", "id": "v1"}, "video",
     {"url": "local://video", "id": "v1"}),
    ({"illustrate_request": {"prompt": "1girl, smile", "motion": 2, "actors": ["爱丽丝"]}, "id": "illo-req-1"},
     "illustrate_request", {"prompt": "1girl, smile", "motion": 2, "actors": ["爱丽丝"], "id": "illo-req-1"}),
    ({"illustrate_request": {"prompt": "p", "motion": 0}, "id": "r2"},
     "illustrate_request", {"prompt": "p", "motion": 0, "actors": [], "id": "r2"}),
    ({"insp": {"title": "女仆装", "content": "总结内容", "sources": []}},
     "inspiration", {"card": {"title": "女仆装", "content": "总结内容", "sources": []}}),
    ({"approval": {"id": "a1"}}, "approval", {"approval": {"id": "a1"}}),
    ({"route_choice": {"id": "r1"}}, "route_choice", {"choice": {"id": "r1"}}),
    ({"rag_status": {"state": "start", "kind": "worldbook", "count": 53}},
     "rag_status", {"state": "start", "kind": "worldbook", "count": 53}),
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


def test_插画事件保留稳定插槽id():
    event = protocol.encode_event({
        "illustrate_request": {"prompt": "p", "motion": 1, "actors": []},
        "id": "slot-1",
    })

    assert event["data"]["id"] == "slot-1"


def test_流式插画事件保留最终正文偏移():
    event = protocol.encode_event({
        "illustrate_request": {"prompt": "p", "motion": 1, "actors": [], "offset": 12},
        "id": "slot-1",
    })

    assert event["data"]["offset"] == 12


def test_插画事件保留Profile生成所需场景源():
    scene_spec = {
        "narrative": "高潮段", "draft_prompt": "close-up", "wardrobe": "红裙",
        "locale": "寝殿", "actors": ["爱丽丝"], "rating": "nsfw",
    }
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "legacy", "motion": 1, "actors": ["爱丽丝"],
            "scene_spec": scene_spec,
        },
        "id": "slot-1",
    })

    assert event["data"]["scene_spec"] == scene_spec


def test_插画事件保留回合id供最终提交Trace关联():
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "p", "motion": 0, "actors": [], "turn_id": "turn-1",
        },
        "id": "slot-1",
    })

    assert event["data"]["turn_id"] == "turn-1"


def test_插画事件透传视频协议字段_v1_5():
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "p", "motion": 3, "actors": ["Lyra"],
            "video_mode": "firstlast",
            "first_frame_desc": "雨夜门口的暖黄灯笼",
            "last_frame_desc": "三人举杯同框",
            "prev_tail_desc": "上一楼层收伞",
            "last_frame_url": "data:image/png;base64,xx",
        },
        "id": "slot-1",
    })
    data = event["data"]
    assert data["video_mode"] == "firstlast"
    assert data["first_frame_desc"] == "雨夜门口的暖黄灯笼"
    assert data["last_frame_desc"] == "三人举杯同框"
    assert data["prev_tail_desc"] == "上一楼层收伞"
    assert data["last_frame_url"] == "data:image/png;base64,xx"


def test_插画事件无视频字段时透传为空不携带_v1_5():
    event = protocol.encode_event({
        "illustrate_request": {"prompt": "p", "motion": 0, "actors": []},
        "id": "slot-1",
    })
    data = event["data"]
    assert "video_mode" not in data
    assert "first_frame_desc" not in data
    assert "last_frame_url" not in data
    assert "video_prompt" not in data
    assert "video_params" not in data


def test_插画事件透传climax视频提示词_v1_5():
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "p", "motion": 3, "actors": ["甲"],
            "video_prompt": "使用视频模型生成，15 seconds。\n\n[动作]：甲挥拳；低机位快速丝滑运镜。",
        },
        "id": "slot-1",
    })
    assert event["data"]["video_prompt"].startswith("使用视频模型生成")
    assert "[动作]" in event["data"]["video_prompt"]


def test_插画事件透传结构化视频参数_v1_5():
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "p", "motion": 3, "actors": ["甲"],
            "video_params": {
                "mode": "climax", "model": "h3-mini", "size": "1280x720",
                "endpoint": "", "images": [], "reference_binding": {}, "warnings": ["缺高潮参考图"],
            },
        },
        "id": "slot-1",
    })
    vp = event["data"]["video_params"]
    assert vp["mode"] == "climax"
    assert vp["model"] == "h3-mini"
    assert vp["warnings"] == ["缺高潮参考图"]


def test_音频事件编码台词与情感向量():
    event = protocol.encode_event({
        "audio_request": {
            "lines": [
                {"speaker": "阿尼玛", "text": "你走开。",
                 "emotion": {"angry": 0.9, "neutral": 0.1}},
                {"speaker": "李四", "text": "我不走。", "emotion": {"happy": 1}},
            ],
        },
        "id": "audio-req-1",
    })
    assert event["type"] == "audio_request"
    assert event["data"]["id"] == "audio-req-1"
    assert event["data"]["lines"][0]["speaker"] == "阿尼玛"
    assert event["data"]["lines"][0]["emotion"]["angry"] == 0.9


def test_音频事件丢弃空台词行():
    event = protocol.encode_event({
        "audio_request": {
            "lines": [{"speaker": "", "text": "x"}, {"speaker": "阿尼玛", "text": "嗨"}],
        },
        "id": "a1",
    })
    assert len(event["data"]["lines"]) == 1
    assert event["data"]["lines"][0]["speaker"] == "阿尼玛"
