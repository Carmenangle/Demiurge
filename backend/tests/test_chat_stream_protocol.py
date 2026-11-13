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


def test_trace事件透传执行详情():
    """2026-09-06：思考文本/工具参数/结果摘要随 trace.detail 上 wire，
    前端执行过程「思考/工具」面板点开可见。"""
    event = protocol.encode_event({"trace": "🤔 模型正在思考…", "detail": "先查候选名单，再切素材"})
    assert event["type"] == "trace"
    assert event["data"] == {"text": "🤔 模型正在思考…", "detail": "先查候选名单，再切素材"}


def test_trace事件无detail时不携带该字段():
    event = protocol.encode_event({"trace": "🧭 主管分派 → 智能编造计划"})
    assert event["data"] == {"text": "🧭 主管分派 → 智能编造计划"}
    assert "detail" not in event["data"]


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
            "transition": "reuse",
        },
        "id": "slot-1",
    })
    data = event["data"]
    assert data["video_mode"] == "firstlast"
    assert data["first_frame_desc"] == "雨夜门口的暖黄灯笼"
    assert data["last_frame_desc"] == "三人举杯同框"
    assert data["prev_tail_desc"] == "上一楼层收伞"
    assert data["last_frame_url"] == "data:image/png;base64,xx"
    # V1.5/W1：首帧复用判定（L1 原值）随线编码器透传
    assert data["transition"] == "reuse"


def test_插画事件无transition字段时不携带_v1_5_w1():
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "p", "motion": 0, "actors": [],
            "transition": "",
        },
        "id": "slot-1",
    })
    data = event["data"]
    assert "transition" not in data


def test_插画事件透传transition三态_v1_5_w2():
    # V1.5/W2：合并结果三态（reuse/regenerate/ambiguous）随线编码器透传
    for value in ("reuse", "regenerate", "ambiguous"):
        event = protocol.encode_event({
            "illustrate_request": {
                "prompt": "p", "motion": 0, "actors": [], "transition": value,
            },
            "id": "slot-1",
        })
        assert event["data"]["transition"] == value


def test_插画事件透传转场视频提示词与参数_v1_6_w3():
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "p", "motion": 0, "actors": [],
            "video_mode": "firstlast", "transition": "regenerate",
            "transition_video_prompt": "转场分镜提示词",
            "transition_video_params": {"mode": "transition", "size": "1280x720"},
        },
        "id": "slot-1",
    })
    data = event["data"]
    assert data["transition_video_prompt"] == "转场分镜提示词"
    assert data["transition_video_params"] == {"mode": "transition", "size": "1280x720"}


def test_插画事件无转场视频字段时不携带_v1_6_w3():
    event = protocol.encode_event({
        "illustrate_request": {
            "prompt": "p", "motion": 0, "actors": [],
        },
        "id": "slot-1",
    })
    assert "transition_video_prompt" not in event["data"]
    assert "transition_video_params" not in event["data"]


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


def test_思考事件编码():
    """2026-08-31 晚「思考全公开」：thinking 增量作为独立事件类型上 wire。"""
    event = protocol.encode_event({"thinking": "先推演再落笔"})
    assert event == {"protocol": "laf-chat-stream", "version": 1,
                     "type": "thinking", "data": {"text": "先推演再落笔"}}


def test_聊天室自发媒体请求编码():
    """2026-11-10 聊天室多元数据：1:1 answer 路由的自发媒体请求单独上 wire，
    前端在聊天流结束后走 generate/video 生成并插回媒体气泡。"""
    event = protocol.encode_event({"media_request": {"kind": "video", "prompt": "撩起衣角"}})
    assert event == {"protocol": "laf-chat-stream", "version": 1,
                     "type": "media_request", "data": {"kind": "video", "prompt": "撩起衣角"}}


def test_非常规装扮自拍标记编码为头像自拍():
    """[AVATAR] = 非常规装扮自拍：wire 必须带 avatar=True，前端据此自动替换聊天头像。"""
    event = protocol.encode_event({
        "media_request": {"kind": "image", "prompt": "婚纱自拍", "avatar": True},
    })
    assert event == {"protocol": "laf-chat-stream", "version": 1,
                     "type": "media_request",
                     "data": {"kind": "image", "prompt": "婚纱自拍", "avatar": True}}


def test_聊天室消息里的头像自拍标记保留avatar():
    event = protocol.encode_event({"chat_messages": [{
        "id": "m1", "speaker": "腓特烈大帝", "text": "看。",
        "media_request": {"kind": "image", "prompt": "婚纱自拍", "avatar": True},
    }]})
    assert event is not None
    assert event["data"]["messages"][0]["media_request"] == {
        "kind": "image", "prompt": "婚纱自拍", "avatar": True,
    }


def test_聊天室自发媒体请求保留角色外貌锚点():
    """角色固定外貌必须过 wire（1:1 独立事件与群聊 chat_messages 两条路都认）。"""
    appearance = "腓特烈大帝：【外貌】暗黄色长发；暗黄色眼睛；身高近两米"
    event = protocol.encode_event({"media_request": {
        "kind": "image", "prompt": "婚纱自拍", "appearance": appearance,
    }})
    assert event == {"protocol": "laf-chat-stream", "version": 1,
                     "type": "media_request",
                     "data": {"kind": "image", "prompt": "婚纱自拍",
                              "appearance": appearance}}

    messages_event = protocol.encode_event({"chat_messages": [{
        "id": "m1", "speaker": "腓特烈大帝", "text": "看。",
        "media_request": {"kind": "image", "prompt": "婚纱自拍", "appearance": appearance},
    }]})
    assert messages_event is not None
    assert messages_event["data"]["messages"][0]["media_request"] == {
        "kind": "image", "prompt": "婚纱自拍", "appearance": appearance,
    }


def test_聊天室消息透传合法时间戳并丢弃非法值():
    event = protocol.encode_event({"chat_messages": [
        {"id": "m1", "speaker": "腓特烈大帝", "text": "看。", "ts": 1762500000},
        {"id": "m2", "speaker": "腓特烈大帝", "text": "嗯。", "ts": True},
        {"id": "m3", "speaker": "腓特烈大帝", "text": "好。", "ts": "bad"},
    ]})
    assert event is not None
    messages = event["data"]["messages"]
    assert messages[0]["ts"] == 1762500000
    assert "ts" not in messages[1]
    assert "ts" not in messages[2]


def test_聊天室自发媒体请求非法时静默丢弃():
    """kind 非 image/video 或 prompt 为空 ⇒ 不产事件（不把脏请求交给前端生成）。"""
    assert protocol.encode_event({"media_request": {"kind": "audio", "prompt": "x"}}) is None
    assert protocol.encode_event({"media_request": {"kind": "image", "prompt": "  "}}) is None
    assert protocol.encode_event({"media_request": "不是映射"}) is None
