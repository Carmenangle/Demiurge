"""MOSS 官方完整布局：模型文字与 GBC 几何的确定性合并。

这里锁纯规则（不跑模型）：官方格式折法（style/cast/lighting 进 summary.caption）、
角色卡前置、按实体名匹配、缺项回退 GBC 原描述、围栏 JSON 解析。
"""
from __future__ import annotations

import pytest

from app.services import gbc_tagger, moss_layout


def _graph() -> dict:
    return {
        "img_size": [768, 1536],
        "detail_caption": "A blonde girl in a black maid outfit with a yellow skirt.",
        "vertices": [
            {"vertex_id": "girl", "label": "entity",
             "bbox": {"left": 24 / 768, "top": 6 / 1536,
                      "right": 696 / 768, "bottom": 1530 / 1536},
             "descs": [{"text": "A blonde girl."}]},
            {"vertex_id": "thigh_1", "label": "entity",
             "bbox": {"left": 33 / 768, "top": 1116 / 1536,
                      "right": 309 / 768, "bottom": 1.0},
             "descs": [{"text": "A thigh with a strap."}]},
            {"vertex_id": "", "label": "relation",
             "bbox": {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 0.2},
             "descs": [{"text": "The skirt covers the thigh."}]},
        ],
    }


def _written() -> dict:
    return {
        "style": "An anime cel-shaded illustration with clean line art. Palette: cream, charcoal, lemon.",
        "caption": "A detailed anime illustration of a maid standing in a sunlit room.",
        "lighting": "Warm window light from the left, soft shadows falling to the right.",
        "cast": [{"name": "girl", "card": "A blonde girl with green eyes in a black maid dress."}],
        "items": [
            {"name": "girl", "caption": "She stands three-quarter view, filling most of the frame."},
            {"name": "thigh_1", "caption": "Her thigh is partly covered by the yellow skirt."},
        ],
    }


def test_to_moss_include_name_is_internal_only() -> None:
    official = gbc_tagger.to_moss(_graph(), include_name=True)
    assert [item["name"] for item in official["items"]] == ["girl", "thigh_1"]
    assert official["items"][0]["bbox"] == [-762, -360, 762, 312]
    assert "name" not in gbc_tagger.to_moss(_graph())["items"][0]


def test_skeleton_fills_unnamed_entity() -> None:
    graph = _graph()
    graph["vertices"].append(
        {"vertex_id": "", "label": "entity",
         "bbox": {"left": 0.1, "top": 0.1, "right": 0.2, "bottom": 0.2},
         "descs": [{"text": "A cup."}]})
    names = [entry["name"] for entry in moss_layout.skeleton(graph)]
    assert names == ["girl", "thigh_1", "entity_3"]


def test_select_pose_tags_normalizes_and_filters() -> None:
    tags = ["on_stomach", "Lying", "barefoot", "on stomach", "From-Behind", "on_stomach", None]
    assert moss_layout.select_pose_tags(tags) == ["on_stomach", "lying", "from_behind"]
    assert moss_layout.select_pose_tags("on_stomach") == []
    assert moss_layout.select_pose_tags(None) == []


def test_pose_hint_prefers_orientation_and_names_target_pose() -> None:
    assert (moss_layout.pose_hint(["lying", "on_stomach"])
            == "lying face-down / on her stomach（俯卧，胸腹贴地）")
    assert moss_layout.pose_hint(["on_back"]) == "lying face-up / on her back（仰卧，背贴地）"
    assert moss_layout.pose_hint(["sitting", "lying"]) == "lying（卧姿，身体水平贴地）"
    assert moss_layout.pose_hint(["sitting"]) == "sitting（坐姿）"
    assert moss_layout.pose_hint(["blonde_hair"]) == ""


def test_digest_gives_full_tag_vocabulary_before_pose_and_entities() -> None:
    """全量标签是布局模型的唯一物体词表（2026-11-07）：只喂姿态子集时，
    模型会跟着 GBC 把角认成铜管乐器、凭空写剑和标题。"""
    text = moss_layout.digest(_graph(), ["blonde_hair", "on_stomach", "lying", "looking_back"])
    assert ("WD14 标签（独立分类器；物体/服装/配饰的唯一真源，按置信度排序）："
            "blonde_hair, on_stomach, lying, looking_back") in text
    assert "姿态标签（WD14 分类器，可靠）：on_stomach, lying, looking_back" in text
    assert "姿态判定（按标签，优先于 GBC 观察）：lying face-down / on her stomach" in text
    assert "caption 与所有 items 都必须与之一致" in text
    assert text.index("WD14 标签") < text.index("姿态标签") < text.index("实体（name")


def test_digest_omits_tag_line_without_tags() -> None:
    text = moss_layout.digest(_graph())
    assert "WD14 标签" not in text
    assert "姿态标签" not in text


def test_find_violations_flags_fabricated_sword_and_title() -> None:
    """真机事故复现：GBC 把角认成铜管乐器后，模型放大出剑与 "Sword of the Sea"。"""
    written = {
        "caption": 'A silver-haired girl holding a great sword, the title "Sword of the Sea" '
                   'arcs above her.',
        "cast": [{"name": "girl", "card": "A girl with silver hair and a horn ornament."}],
        "items": [{"name": "girl", "caption": "She grips the sword with both hands."}],
    }
    problems = moss_layout.find_violations(written, ["1girl", "horns", "white_one-piece_swimsuit"])
    joined = "\n".join(problems)
    assert "手持物 “great sword”" in joined or "手持物 “sword”" in joined
    assert "画面内文字 “Sword of the Sea”" in joined
    assert "items[girl] 写了手持物" in joined


def test_find_violations_passes_tag_backed_sword_and_text() -> None:
    written = {
        "caption": 'A girl holding a sword, the sign reads "Sword of the Sea".',
        "cast": [],
        "items": [{"name": "girl", "caption": "She wields the sword."}],
    }
    assert moss_layout.find_violations(written, ["1girl", "sword", "english_text"]) == []


def test_find_violations_ignores_benign_holds_and_unquoted_words() -> None:
    """"holding her breath"/"holding a pose" 不是手持物；没有引号就没有画面文字。"""
    written = {
        "caption": "She is holding her breath while holding a pose near the sea.",
        "cast": [],
        "items": [{"name": "girl", "caption": "The Sword of the Sea rests out of frame."}],
    }
    assert moss_layout.find_violations(written, ["1girl", "white_one-piece_swimsuit"]) == []


def test_find_violations_skips_gate_without_tags() -> None:
    """老资产没存 WD14 标签：没有权威词表就不拦（保持原行为，不制造假失败）。"""
    written = {"caption": 'She holds a sword, the title "Sword of the Sea".', "items": []}
    assert moss_layout.find_violations(written, None) == []
    assert moss_layout.find_violations(written, []) == []


def test_find_violations_swimwear_family_conflict() -> None:
    tags = ["1girl", "white_one-piece_swimsuit"]
    bad = {"caption": "She wears a white bikini.", "items": []}
    problems = moss_layout.find_violations(bad, tags)
    assert "bikini（两件式）" in "\n".join(problems)
    # 报错要点名标签里的正确叫法，别把整份标签表塞进 502 detail
    assert "标签里是：white one-piece swimsuit" in problems[0]
    assert "1girl" not in problems[0]
    good = {"caption": "She wears a white one-piece swimsuit.", "items": []}
    assert moss_layout.find_violations(good, tags) == []
    # 反向：标签是两件式时不得写一体式
    rev = {"caption": "She wears a one-piece swimsuit.", "items": []}
    assert "one-piece swimsuit（一体式）" in "\n".join(
        moss_layout.find_violations(rev, ["1girl", "bikini"]))


def test_find_violations_allows_unclassified_swimsuit_naming() -> None:
    """真机误报：slingshot_swimsuit 既不在两件式也不在一件式词表 ⇒ 只是叫法不同，不判冲突。"""
    tags = ["1girl", "swimsuit", "slingshot_swimsuit"]
    written = {"caption": "She wears a white bikini.", "items": []}
    assert moss_layout.find_violations(written, tags) == []
    # 连泛称 swimsuit 都没有时也不拦：没有对立款型就没有可证的冲突
    assert moss_layout.find_violations(written, ["1girl", "thighhighs"]) == []


def test_find_violations_allows_artist_credit_block() -> None:
    """真机误报：图上真印着的署名/引流块，WD14 漏打 signature 标签也不算凭空文字。"""
    tags = ["1girl", "white_one-piece_swimsuit"]
    credit = {"caption": 'The corner reads "artist. fanbox. patreon: Some_Artist fansky: Someone".',
              "items": []}
    assert moss_layout.find_violations(credit, tags) == []
    # 署名块在前、编造标题在后：不能因为第一段是署名就整段放行
    both = {"caption": 'Signed "@some_artist", with the title "Sword of the Sea" above her.',
            "items": []}
    problems = moss_layout.find_violations(both, tags)
    assert "画面内文字 “Sword of the Sea”" in "\n".join(problems)
    assert "@some_artist" not in "\n".join(problems)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChat:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list] = []

    def invoke(self, messages: list) -> _FakeResp:
        self.calls.append(messages)
        return _FakeResp(self.replies.pop(0))


def _wire_fake_chat(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> _FakeChat:
    chat = _FakeChat(replies)
    monkeypatch.setattr(moss_layout.vlm_batch, "file_to_datauri",
                        lambda _path: "data:image/png;base64,AAAA")
    monkeypatch.setattr(moss_layout._llm, "build_model", lambda *_a, **_k: chat)
    monkeypatch.setattr(moss_layout._llm, "flatten_content", lambda content: content)
    return chat


def _json(written: dict) -> str:
    import json

    return json.dumps(written)


def test_write_retries_once_when_output_conflicts_with_tags(
        monkeypatch: pytest.MonkeyPatch) -> None:
    bad = {"style": "Anime cel shading.", "caption": "She holds a sword.", "cast": [],
           "items": [{"name": "girl", "caption": "A girl."}]}
    good = {"style": "Anime cel shading.", "caption": "She stands by the sea.", "cast": [],
            "items": [{"name": "girl", "caption": "A girl."}]}
    chat = _wire_fake_chat(monkeypatch, [_json(bad), _json(good)])
    out = moss_layout.write(_graph(), "a.png", base_url="http://v", api_key="k",
                            model="m", tags=["1girl"])
    assert out["caption"] == good["caption"]
    assert out["items"] == good["items"]
    assert len(chat.calls) == 2
    correction = chat.calls[1][1].content[-1]["text"]
    assert "手持物" in correction and "上一次输出" in correction


def test_write_returns_immediately_when_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    good = {"style": "Anime cel shading.", "caption": "She stands by the sea.", "cast": [],
            "items": [{"name": "girl", "caption": "A girl."}]}
    chat = _wire_fake_chat(monkeypatch, [_json(good)])
    out = moss_layout.write(_graph(), "a.png", base_url="http://v", api_key="k",
                            model="m", tags=["1girl"])
    assert out["caption"] == good["caption"]
    assert len(chat.calls) == 1


def test_write_fails_when_violation_survives_retry(
        monkeypatch: pytest.MonkeyPatch) -> None:
    bad = {"style": "Anime cel shading.", "caption": "She holds a sword.", "cast": [],
           "items": [{"name": "girl", "caption": "A girl."}]}
    _wire_fake_chat(monkeypatch, [_json(bad), _json(bad)])
    with pytest.raises(moss_layout.MossLayoutFailed, match="纠正重试后仍存在"):
        moss_layout.write(_graph(), "a.png", base_url="http://v", api_key="k",
                          model="m", tags=["1girl"])


def test_compose_folds_style_cast_lighting_into_summary_caption() -> None:
    out = moss_layout.compose(_graph(), _written())
    layout = out["layout"]
    caption = layout["summary"]["caption"]
    assert layout["summary"]["canvas"] == "single"
    assert caption.startswith("An anime cel-shaded illustration")
    assert "A detailed anime illustration of a maid standing" in caption
    assert "Characters: A blonde girl with green eyes in a black maid dress." in caption
    assert caption.endswith(
        "Lighting: Warm window light from the left, soft shadows falling to the right.")
    assert layout["bbox_coord_space"] == "abs_centralized"
    assert layout["bbox_format"] == "yxyx"
    assert "name" not in layout["items"][0]


def test_compose_prepends_card_to_character_and_keeps_object() -> None:
    out = moss_layout.compose(_graph(), _written())
    girl, thigh = out["layout"]["items"]
    assert girl["label"] == "character"
    assert girl["caption"].startswith("A blonde girl with green eyes in a black maid dress.")
    assert girl["caption"].endswith("She stands three-quarter view, filling most of the frame.")
    assert thigh["label"] == "object"
    assert thigh["caption"] == "Her thigh is partly covered by the yellow skirt."
    assert out["parts"]["cast"] == [
        {"name": "girl", "card": "A blonde girl with green eyes in a black maid dress."}]


def test_compose_matches_by_name_and_falls_back_to_gbc_text() -> None:
    written = _written()
    written["items"] = [
        {"name": "Girl", "caption": "The girl faces the window."},
        {"name": "skirt", "caption": "Not a GBC entity."},
    ]
    out = moss_layout.compose(_graph(), written)
    girl, thigh = out["layout"]["items"]
    assert girl["caption"].endswith("The girl faces the window.")
    # 没提到 GBC 实体 ⇒ 退回它的原描述，不丢项
    assert thigh["caption"] == "A thigh with a strap."
    assert [entry["name"] for entry in out["parts"]["items"]] == ["girl", "thigh_1"]


def test_character_fallback_aligns_stale_sitting_with_pose_tags() -> None:
    graph = _graph()
    graph["vertices"][0]["descs"] = [
        {"text": "She is shown from behind, sitting with her legs spread."}]
    written = _written()
    written["items"] = [{"name": "thigh_1", "caption": "Her thigh is partly covered."}]
    out = moss_layout.compose(graph, written, tags=["on_stomach", "lying", "looking_back"])
    caption = out["layout"]["items"][0]["caption"]
    assert "lying face-down on her stomach" in caption
    assert "sitting" not in caption.lower()


def test_character_fallback_keeps_stale_pose_without_tags() -> None:
    graph = _graph()
    graph["vertices"][0]["descs"] = [{"text": "She is seated on the sand."}]
    written = _written()
    written["items"] = [{"name": "thigh_1", "caption": "Her thigh."}]
    out = moss_layout.compose(graph, written)
    assert "seated" in out["layout"]["items"][0]["caption"].lower()


def test_align_character_pose_handles_verbs_and_case() -> None:
    assert (moss_layout.align_character_pose("She sits on the sand.", ["on_stomach"])
            == "She lies face-down on her stomach on the sand.")
    assert (moss_layout.align_character_pose("Sitting on the sand.", ["on_stomach"])
            == "Lying face-down on her stomach on the sand.")
    assert (moss_layout.align_character_pose("She is seated.", ["on_back"])
            == "She is lying face-up on her back.")


def test_compose_dedupes_cast_line_and_skips_duplicate_card() -> None:
    written = _written()
    written["cast"].append({"name": "girl", "card": "A blonde girl with green eyes in a black maid dress."})
    out = moss_layout.compose(_graph(), written)
    caption = out["layout"]["summary"]["caption"]
    assert caption.count("Characters:") == 1
    # 角色卡在 summary 只出现一次（Characters 行），角色项里再前置一次
    both = caption + " " + out["layout"]["items"][0]["caption"]
    assert both.count("A blonde girl with green eyes") == 2


def test_parse_written_strips_fence_and_prose() -> None:
    raw = """好的，结果如下：
```json
{"style":"Anime style. Palette: blue.","caption":"A detailed anime illustration.",
 "lighting":"Soft daylight.","cast":[],"items":[{"name":"girl","caption":"She stands."}]}
```"""
    out = moss_layout.parse_written(raw)
    assert out["style"] == "Anime style. Palette: blue."
    assert out["cast"] == []
    assert out["items"] == [{"name": "girl", "caption": "She stands."}]


@pytest.mark.parametrize("raw", [
    "",
    "没有 JSON",
    '{"style":"x","caption":"y","items":[]}',
])
def test_parse_written_rejects_unusable(raw: str) -> None:
    with pytest.raises(moss_layout.MossLayoutFailed):
        moss_layout.parse_written(raw)


def test_route_forwards_wire_and_maps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from app.routers import rag as rag_router
    from app.services import asset_library

    seen: dict = {}

    def fake(asset_id: str, **kwargs):
        seen.update({"id": asset_id, **kwargs})
        return {"ok": True, "layout": {}, "parts": {}, "elapsed_s": 1.2}

    monkeypatch.setattr(asset_library, "moss_layout_asset", fake)
    out = rag_router.moss_layout(rag_router.MossLayoutRequest(
        id="a1", vlm_base_url="http://v1", vlm_model="vlm", vlm_api_key="k",
        vlm_proxy="http://p", hint="电影感"))
    assert out["ok"] is True
    assert seen == {"id": "a1", "vlm_base_url": "http://v1", "vlm_model": "vlm",
                    "vlm_api_key": "k", "vlm_proxy": "http://p", "hint": "电影感"}

    for exc, status in ((moss_layout.MossLayoutUnavailable("x"), 503),
                        (moss_layout.MossLayoutFailed("x"), 502),
                        (asset_library.AssetMissing("x"), 404),
                        (ValueError("x"), 400)):
        def boom(_id: str, *, _exc=exc, **kwargs):
            raise _exc

        monkeypatch.setattr(asset_library, "moss_layout_asset", boom)
        with pytest.raises(HTTPException) as info:
            rag_router.moss_layout(rag_router.MossLayoutRequest(id="a1"))
        assert info.value.status_code == status


def test_asset_guard_requires_gbc_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import asset_library, gbc_store

    monkeypatch.setattr(gbc_store, "get", lambda _id: None)
    with pytest.raises(ValueError, match="还没有 GBC 场景图"):
        asset_library.moss_layout_asset(
            "a1", vlm_base_url="http://v", vlm_model="m", vlm_api_key="k")


def test_asset_guard_rejects_graph_without_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import asset_library, gbc_store

    monkeypatch.setattr(gbc_store, "get", lambda _id: {
        "graph": {"img_size": [100, 100],
                  "vertices": [{"label": "image", "bbox": {"left": 0.0, "top": 0.0,
                                                          "right": 1.0, "bottom": 1.0}}]},
    })
    with pytest.raises(ValueError, match="没有实体"):
        asset_library.moss_layout_asset(
            "a1", vlm_base_url="http://v", vlm_model="m", vlm_api_key="k")


def test_asset_requires_local_image_and_vlm(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from app.services import asset_library, asset_store, gbc_store, thumbnail_store

    monkeypatch.setattr(gbc_store, "get", lambda _id: {"graph": _graph()})
    monkeypatch.setattr(asset_store, "get_row", lambda _id: {"image_url": "/pictures/a.png"})

    monkeypatch.setattr(thumbnail_store, "local_path_of_url", lambda _url: None)
    with pytest.raises(ValueError, match="本机留存图片"):
        asset_library.moss_layout_asset(
            "a1", vlm_base_url="http://v", vlm_model="m", vlm_api_key="k")

    monkeypatch.setattr(thumbnail_store, "local_path_of_url", lambda _url: Path("a.png"))
    with pytest.raises(moss_layout.MossLayoutUnavailable):
        asset_library.moss_layout_asset(
            "a1", vlm_base_url="", vlm_model="", vlm_api_key="k")


def test_asset_composes_and_persists_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path
    from typing import cast

    from app.services import asset_library, asset_store, gbc_store, thumbnail_store

    monkeypatch.setattr(gbc_store, "get", lambda _id: {"graph": _graph()})
    monkeypatch.setattr(asset_store, "get_row", lambda _id: {"image_url": "/pictures/a.png"})
    monkeypatch.setattr(thumbnail_store, "local_path_of_url", lambda _url: Path("a.png"))
    monkeypatch.setattr(moss_layout, "write", lambda *_a, **_k: _written())
    persisted: dict = {}

    def save_layout(asset_id: str, payload: dict) -> bool:
        persisted.update({"id": asset_id, **payload})
        return True

    monkeypatch.setattr(gbc_store, "save_layout", save_layout)
    out = asset_library.moss_layout_asset(
        "a1", vlm_base_url="http://v", vlm_model="deepseek-flash",
        vlm_api_key="k", hint="电影感")

    assert out["ok"] is True
    assert out["model"] == "deepseek-flash"
    layout = cast(dict, out["layout"])
    assert [item["label"] for item in layout["items"]] == ["character", "object"]
    assert persisted["id"] == "a1"
    assert persisted["layout"] == out["layout"]
    assert persisted["parts"]["cast"][0]["name"] == "girl"


def test_asset_passes_engine_tags_and_falls_back_to_row_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from app.services import asset_library, asset_store, gbc_store, thumbnail_store

    seen: list[object] = []
    monkeypatch.setattr(thumbnail_store, "local_path_of_url", lambda _url: Path("a.png"))
    monkeypatch.setattr(moss_layout, "write",
                        lambda *_a, **kwargs: seen.append(kwargs.get("tags")) or _written())
    monkeypatch.setattr(gbc_store, "save_layout", lambda *_a, **_k: True)

    monkeypatch.setattr(gbc_store, "get", lambda _id: {
        "graph": _graph(), "engine": {"tags": ["on_stomach", "lying"]}})
    monkeypatch.setattr(asset_store, "get_row", lambda _id: {
        "image_url": "/pictures/a.png", "tags_json": '["sitting"]'})
    asset_library.moss_layout_asset("a1", vlm_base_url="http://v", vlm_model="m", vlm_api_key="k")
    assert seen[-1] == ["on_stomach", "lying"]

    monkeypatch.setattr(gbc_store, "get", lambda _id: {"graph": _graph(), "engine": {}})
    monkeypatch.setattr(asset_store, "get_row", lambda _id: {
        "image_url": "/pictures/a.png", "tags_json": '["on_stomach", "barefoot"]'})
    asset_library.moss_layout_asset("a1", vlm_base_url="http://v", vlm_model="m", vlm_api_key="k")
    assert seen[-1] == ["on_stomach", "barefoot"]
