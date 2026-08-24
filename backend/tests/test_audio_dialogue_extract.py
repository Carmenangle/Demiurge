from app.services import audio_dialogue_extract as ade


def test_normalize_emotion_all_zero_falls_back_to_neutral():
    assert ade.normalize_emotion({"happy": 0, "angry": 0}) == {
        "happy": 0.0, "angry": 0.0, "sad": 0.0, "fear": 0.0,
        "hate": 0.0, "low": 0.0, "surprise": 0.0, "neutral": 1.0,
    }


def test_normalize_emotion_invalid_input_falls_back_to_neutral():
    assert ade.normalize_emotion(None) == {
        "happy": 0.0, "angry": 0.0, "sad": 0.0, "fear": 0.0,
        "hate": 0.0, "low": 0.0, "surprise": 0.0, "neutral": 1.0,
    }


def test_normalize_emotion_clamps_and_fills_missing():
    out = ade.normalize_emotion({"angry": 2.5, "neutral": 0.2})
    assert out["angry"] == 1.0
    assert out["neutral"] == 0.2
    assert out["sad"] == 0.0


def test_extract_audio_dialogue_parses_lines():
    reply = (
        '<content>她低声说：“你走开。”</content>\n'
        '<audio>{"lines":[{"speaker":"阿尼玛","text":"你走开。",'
        '"emotion":{"angry":0.9,"sad":0.1}}]}</audio>'
    )
    clean, plan = ade.extract_audio_dialogue(reply)
    assert '<audio>' not in clean
    assert len(plan["lines"]) == 1
    assert plan["lines"][0]["speaker"] == "阿尼玛"
    assert plan["lines"][0]["text"] == "你走开。"
    assert plan["lines"][0]["emotion"]["angry"] == 0.9


def test_extract_audio_dialogue_empty_when_no_block():
    clean, plan = ade.extract_audio_dialogue("没有 audio 块")
    assert clean == "没有 audio 块"
    assert plan == {}


def test_extract_audio_dialogue_empty_lines_when_bad_json():
    clean, plan = ade.extract_audio_dialogue("正文 <audio>不是json</audio>")
    assert plan == {}


def test_extract_audio_dialogue_skips_empty_speaker_or_text():
    reply = (
        '<audio>{"lines":[{"speaker":"","text":"x"},'
        '{"speaker":"阿尼玛","text":""},'
        '{"speaker":"李四","text":"你好","emotion":{"happy":1}}]}</audio>'
    )
    _, plan = ade.extract_audio_dialogue(reply)
    assert len(plan["lines"]) == 1
    assert plan["lines"][0]["speaker"] == "李四"


def test_build_fallback_dialogue_extracts_known_speaker():
    story = "阿尼玛：你来了。\n李四：我来了。\n（他缓缓坐下）"
    lines = ade.build_fallback_dialogue(story, ["阿尼玛", "李四"])
    assert [ln["speaker"] for ln in lines] == ["阿尼玛", "李四"]
    assert lines[0]["emotion"]["neutral"] == 1.0


def test_build_fallback_dialogue_filters_unknown_speaker():
    story = "陌生人：你好。\n阿尼玛：欢迎。"
    lines = ade.build_fallback_dialogue(story, ["阿尼玛"])
    assert [ln["speaker"] for ln in lines] == ["阿尼玛"]
