"""GBC 场景图打标：MOSS 映射、配置生成、缺件判定、场景图存取。

真跑 `gbc.exe` 的路径不在单测里（外部引擎 + torch 权重）：这里锁**纯规则**——
坐标变换（交付形 vs 草稿形）、label 分档、缺件点名、子进程配置形状。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import db as dbmod
from app.services import gbc_store, gbc_tagger
from app.services.gbc_tagger import GbcRuntime


def _graph() -> dict:
    return {
        "img_size": [768, 1536],
        "detail_caption": "A blonde girl in a black maid outfit with a yellow skirt.",
        "vertices": [
            {"vertex_id": "", "label": "image",
             "bbox": {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0, "confidence": None},
             "descs": [{"text": "whole image"}]},
            {"vertex_id": "girl", "label": "entity",
             "bbox": {"left": 24 / 768, "top": 6 / 1536,
                      "right": 696 / 768, "bottom": 1530 / 1536, "confidence": 0.788},
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


def test_to_moss_delivered_centers_bbox_y_first() -> None:
    moss = gbc_tagger.to_moss(_graph())
    assert moss["summary"]["canvas"] == "single"
    assert moss["summary"]["caption"] == _graph()["detail_caption"]
    assert moss["bbox_coord_space"] == "abs_centralized"
    assert moss["bbox_format"] == "yxyx"
    assert [item["label"] for item in moss["items"]] == ["character", "object"]
    # [top-h/2, left-w/2, bottom-h/2, right-w/2]
    assert moss["items"][0]["bbox"] == [-762, -360, 762, 312]
    assert moss["items"][1]["bbox"] == [348, -351, 768, -75]


def test_to_moss_draft_is_abs_xyxy_with_canvas() -> None:
    draft = gbc_tagger.to_moss(_graph(), draft=True)
    assert draft["width"] == 768 and draft["height"] == 1536
    assert draft["bbox_coord_space"] == "abs"
    assert draft["bbox_format"] == "xyxy"
    assert draft["items"][0]["name"] == "girl"
    assert draft["items"][0]["bbox"] == [24, 6, 696, 1530]
    assert draft["items"][1]["bbox"] == [33, 1116, 309, 1536]


def test_to_moss_skips_entity_without_bbox() -> None:
    graph = _graph()
    graph["vertices"].append(
        {"vertex_id": "skirt", "label": "entity", "bbox": {}, "descs": [{"text": "x"}]})
    moss = gbc_tagger.to_moss(graph)
    assert [item["caption"] for item in moss["items"]] == ["A blonde girl.", "A thigh with a strap."]


def test_outline_lists_entities_and_relations() -> None:
    out = gbc_tagger.outline(_graph())
    assert [e["name"] for e in out["entities"]] == ["girl", "thigh_1"]
    assert out["relations"] == ["The skirt covers the thigh."]
    assert out["moss_items"] == 2


def test_missing_parts_names_each_piece(tmp_path: Path) -> None:
    assert gbc_tagger.missing_parts(GbcRuntime(exe="", sam3_dir="", embed_dir="")) == [
        "gbc.exe", "SAM-3 权重目录", "EmbeddingGemma 权重目录"]

    sam3 = tmp_path / "sam3"
    sam3.mkdir()
    (sam3 / "model.safetensors").write_bytes(b"x")
    exe = tmp_path / "gbc.exe"
    exe.write_bytes(b"")
    rt = GbcRuntime(exe=str(exe), sam3_dir=str(sam3), embed_dir=str(tmp_path / "missing"))
    assert gbc_tagger.missing_parts(rt) == ["EmbeddingGemma 权重目录"]


def test_build_config_keeps_key_out_of_file(tmp_path: Path) -> None:
    rt = GbcRuntime(exe="gbc.exe", sam3_dir=str(tmp_path / "sam3"),
                    embed_dir=str(tmp_path / "embed"), device="cuda", max_top_level=8)
    text = gbc_tagger.build_config(
        work_dir=tmp_path, job_path=tmp_path / "job.jsonl", sink_path=tmp_path / "out.jsonl",
        rt=rt, vlm_base_url="http://127.0.0.1:11434/v1/", vlm_model="qwen3vl:8b",
        has_api_key=True,
    )
    namespace: dict = {}
    exec(compile(text, "config.py", "exec"), namespace)  # noqa: S102 - 生成物必须可执行
    assert namespace["engines"]["detect"]["model"] == str(tmp_path / "sam3")
    assert namespace["engines"]["detect"]["devices"] == ["cuda:0"]
    assert namespace["engines"]["vlm"]["endpoints"][0]["base_url"] == "http://127.0.0.1:11434/v1"
    assert namespace["engines"]["vlm"]["endpoints"][0]["api_key_env"] == gbc_tagger.API_KEY_ENV
    assert namespace["controls"]["root"] == "tags"
    assert namespace["controls"]["max_top_level"] == 8
    assert namespace["source"]["path"] == str(tmp_path / "job.jsonl")
    # 本机 VLM（Ollama/vLLM）保留 schema 文法，且不覆盖 GBC 自己的输出上限
    assert "structured" not in namespace["engines"]["vlm"]
    assert "sampling" not in namespace["engines"]["vlm"]

    plain = gbc_tagger.build_config(
        work_dir=tmp_path, job_path=tmp_path / "job.jsonl", sink_path=tmp_path / "out.jsonl",
        rt=rt, vlm_base_url="http://127.0.0.1:11434/v1", vlm_model="m", has_api_key=False,
    )
    assert "__key_env" not in plain and gbc_tagger.API_KEY_ENV not in plain


def test_cloud_vlm_asks_json_in_prompt_and_leaves_room_for_reasoning(tmp_path: Path) -> None:
    """云端 VLM 两条实测约束（2026-11-07，DeepSeek）：
    ① `response_format` json_schema strict 直接 HTTP 400 ⇒ 配置必须走 structured=prompt；
    ② 推理模型一次用掉 457/512 个输出 tokens 思考，实体 JSON 被截断后静默丢顶点
    ⇒ 用 sampling 抬高 max_tokens。本机端点不受影响（保住 grammar）。
    """
    rt = GbcRuntime(exe="gbc.exe", sam3_dir=str(tmp_path / "sam3"),
                    embed_dir=str(tmp_path / "embed"), device="cuda")
    text = gbc_tagger.build_config(
        work_dir=tmp_path, job_path=tmp_path / "job.jsonl", sink_path=tmp_path / "out.jsonl",
        rt=rt, vlm_base_url="https://api.deepseek.com/", vlm_model="deepseek-flash",
        has_api_key=True,
    )
    namespace: dict = {}
    exec(compile(text, "config.py", "exec"), namespace)  # noqa: S102
    vlm = namespace["engines"]["vlm"]
    assert vlm["structured"] == "prompt"
    assert vlm["sampling"]["max_tokens"] >= 4096
    assert vlm["endpoints"][0]["base_url"] == "https://api.deepseek.com"


def test_detect_device_gets_index(tmp_path: Path) -> None:
    """裸 `cuda` 会让 GBC 的 `torch-cuda-all` 线程启动即死 ⇒ 永久挂起、GPU 0 %。

    真机现场（2026-11-06 23:19 单）：`togeari/engines/torch/runtime.py:297`
    `torch.cuda.set_device("cuda")` ⇒ `ValueError: Expected a torch.device with a
    specified index or an integer, but got:cuda`，线程死掉后任务留在 ready 队列，
    单元 inflight=1 / batches=0，进程一直挂到后端 1800 s 超时。
    """
    for raw, want in (("cuda", "cuda:0"), ("CUDA", "cuda:0"), ("cuda:1", "cuda:1"),
                      ("cpu", "cpu"), ("", "cpu")):
        rt = GbcRuntime(exe="gbc.exe", sam3_dir="", embed_dir="", device=raw)
        text = gbc_tagger.build_config(
            work_dir=tmp_path, job_path=tmp_path / "job.jsonl",
            sink_path=tmp_path / "out.jsonl", rt=rt,
            vlm_base_url="", vlm_model="", has_api_key=False,
        )
        namespace: dict = {}
        exec(compile(text, "config.py", "exec"), namespace)  # noqa: S102
        assert namespace["engines"]["detect"]["devices"] == [want], raw


def test_build_job_rounds_scores() -> None:
    job = gbc_tagger.build_job(key="a1", image_path=Path("D:/img.png"), caption="  A girl. ",
                               tags={"girl": 0.99831, "skirt": 0.66834})
    assert job["captions"]["detail"] == "A girl."
    assert job["tags"]["general"] == {"girl": 0.9983, "skirt": 0.6683}
    assert job["image"] == str(Path("D:/img.png"))


#: 2026-11-07 真机素材的 WD14 全量标签（生成图照画了剑与标题的那张）。
_ANIMA_TAGS = [
    "1girl", "breasts", "horns", "solo", "braid", "mole_under_mouth", "swimsuit",
    "one-piece_swimsuit", "mole", "twin_braids", "dark-skinned_female",
    "hair_over_one_eye", "white_one-piece_swimsuit", "dark_skin", "large_breasts",
    "smile", "long_hair", "leaning_forward", "cleavage", "looking_at_viewer",
    "thighlet", "outdoors", "official_alternate_costume", "blonde_hair",
    "asymmetrical_bangs", "horn_ornament", "jewelry", "very_long_hair",
    "long_bangs", "yellow_eyes", "sky", "bracelet",
]


def test_caption_from_tags_keeps_only_tag_backed_objects() -> None:
    """种子描述 = 标签里的物体清单：部位/表情/姿态/场景/元信息都不得变成实体。"""
    caption = gbc_tagger.caption_from_tags(_ANIMA_TAGS)
    assert caption == ("Objects in the image: girl, horns, white one-piece swimsuit, "
                       "thighlet, horn ornament, jewelry, bracelet.")
    for noise in ("breasts", "braid", "mole", "smile", "long hair",
                  "leaning", "cleavage", "blonde", "sky", "official", "eye"):
        assert noise not in caption


def test_caption_from_tags_normalizes_counts_phrases_and_plurals() -> None:
    caption = gbc_tagger.caption_from_tags([
        "2girls", "1boy", "white_one-piece_swimsuit", "one-piece_swimsuit",
        "swimsuit", "twin_braids", "alice_(genshin_impact)", "holding_sword",
        "shirt", "collared_shirt",
    ])
    # 2girls ⇒ girls、1boy ⇒ boy；部位复数（braids）不落实体；括号限定词剥掉
    assert caption.startswith("Objects in the image: girls, boy, white one-piece swimsuit, alice, ")
    # 后缀短语只留最具体的（swimsuit ⊂ white...；shirt ⊂ collared shirt）
    assert caption.count("one-piece swimsuit") == 1 and caption.count("swimsuit") == 1
    assert "collared shirt" in caption and "shirt," not in caption
    assert "twin braids" not in caption


def test_caption_from_tags_empty_without_objects() -> None:
    assert gbc_tagger.caption_from_tags(["solo", "outdoors", "sky", "smile"]) == ""
    assert gbc_tagger.caption_from_tags([]) == ""
    assert gbc_tagger.caption_from_tags(None) == ""


def test_gbc_tag_asset_builds_caption_from_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """前端不再传整图描述：caption 在服务端按 WD14 标签生成，且按同一门槛过滤。"""
    from app.services import asset_library, tagger_registry, thumbnail_store
    from app.services.rag_backend import EmbedConfig

    spec = tagger_registry.TaggerSpec("wd14", "WD14", Path("models"))
    monkeypatch.setattr(tagger_registry, "resolve", lambda _id: spec)
    monkeypatch.setattr("app.services.wd14_tagger.model_available", lambda _dir: True)
    monkeypatch.setattr(tagger_registry, "predict", lambda *_a, **_k: [
        ("1girl", 0.98), ("white_one-piece_swimsuit", 0.81), ("sword", 0.2),
    ])
    monkeypatch.setattr(asset_library.rag_store, "list_generations",
                        lambda *_a, **_k: [{"id": "a1", "image_url": "/pictures/a.png"}])
    monkeypatch.setattr(thumbnail_store, "local_path_of_url", lambda _url: Path("a.png"))
    seen: dict = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {"img_size": [100, 100], "detail_caption": "x", "_elapsed_s": 1.0,
                "vertices": [{"vertex_id": "girl", "label": "entity",
                              "bbox": {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9},
                              "descs": [{"text": "a girl"}]}]}

    monkeypatch.setattr(gbc_tagger, "run_one", fake_run)
    saved: dict = {}
    monkeypatch.setattr("app.services.gbc_store.save",
                        lambda _id, _repo, **kw: saved.update(kw))
    monkeypatch.setattr(asset_library.rag_store, "add_tag_snapshot",
                        lambda *_a, **_k: {"id": "s1", "tags": ["1girl"]})

    out = asset_library.gbc_tag_asset(
        "a1", "home", runtime=GbcRuntime(exe="gbc.exe", sam3_dir="s", embed_dir="e"),
        vlm_base_url="http://v", vlm_model="m", vlm_api_key="", cfg=EmbedConfig())

    assert seen["caption"] == "Objects in the image: girl, white one-piece swimsuit."
    assert seen["tags"] == {"1girl": 0.98, "white_one-piece_swimsuit": 0.81, "sword": 0.2}
    assert saved["engine"]["caption"] == seen["caption"]
    assert out["total"] == 1


def test_gbc_tag_asset_stops_when_no_object_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import asset_library, tagger_registry, thumbnail_store
    from app.services.rag_backend import EmbedConfig

    spec = tagger_registry.TaggerSpec("wd14", "WD14", Path("models"))
    monkeypatch.setattr(tagger_registry, "resolve", lambda _id: spec)
    monkeypatch.setattr("app.services.wd14_tagger.model_available", lambda _dir: True)
    monkeypatch.setattr(tagger_registry, "predict",
                        lambda *_a, **_k: [("solo", 0.9), ("looking_at_viewer", 0.8)])
    monkeypatch.setattr(asset_library.rag_store, "list_generations",
                        lambda *_a, **_k: [{"id": "a1", "image_url": "/pictures/a.png"}])
    monkeypatch.setattr(thumbnail_store, "local_path_of_url", lambda _url: Path("a.png"))
    monkeypatch.setattr(gbc_tagger, "run_one",
                        lambda **_kw: pytest.fail("没有物体标签时不得启动 GBC"))

    with pytest.raises(ValueError, match="没有识别出可用于 GBC 的物体标签"):
        asset_library.gbc_tag_asset(
            "a1", "home", runtime=GbcRuntime(exe="gbc.exe", sam3_dir="s", embed_dir="e"),
            vlm_base_url="http://v", vlm_model="m", vlm_api_key="",
            cfg=EmbedConfig())


def test_gbc_route_no_longer_accepts_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import rag as rag_router
    from app.services import asset_library

    seen: dict = {}

    def fake(asset_id: str, repo_id: str, **kwargs):
        seen.update({"id": asset_id, "repo": repo_id, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(asset_library, "gbc_tag_asset", fake)
    out = rag_router.gbc_tag(rag_router.GbcTagRequest(id="a1", gbc_exe="C:/gbc.exe"))
    assert out == {"ok": True}
    assert seen["id"] == "a1" and "caption" not in seen
    assert "caption" not in rag_router.GbcTagRequest.model_fields


def test_run_one_rejects_incomplete_environment_before_spawning(tmp_path: Path) -> None:
    rt = GbcRuntime(exe=str(tmp_path / "nope.exe"), sam3_dir="", embed_dir="")
    with pytest.raises(gbc_tagger.GbcUnavailable):
        gbc_tagger.run_one(image_path=tmp_path / "a.png", caption="A girl.", tags={},
                           rt=rt, vlm_base_url="http://127.0.0.1:11434/v1", vlm_model="m")


class _StubResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class _StubOpener:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, bytes | None]] = []

    def open(self, target: object, timeout: int = 0) -> _StubResponse:
        url = target.full_url if hasattr(target, "full_url") else str(target)
        self.calls.append((url, getattr(target, "data", None)))
        return _StubResponse(self.payload)


def test_ollama_root_strips_openai_suffix() -> None:
    assert gbc_tagger._ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert gbc_tagger._ollama_root("http://127.0.0.1:11434/api/") == "http://127.0.0.1:11434"
    assert gbc_tagger._ollama_root("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_unload_local_vlm_releases_resident_model() -> None:
    opener = _StubOpener({"models": [{"name": "qwen3vl:8b", "size_vram": 5_479_008_829}]})
    assert gbc_tagger._unload_local_vlm("http://127.0.0.1:11434/v1", "qwen3vl:8b",
                                        opener=opener)
    assert [call[0] for call in opener.calls] == [
        "http://127.0.0.1:11434/api/ps", "http://127.0.0.1:11434/api/generate",
    ]
    assert json.loads((opener.calls[1][1] or b"{}").decode("utf-8")) == {
        "model": "qwen3vl:8b", "keep_alive": 0,
    }


def test_unload_local_vlm_keeps_cpu_only_model() -> None:
    opener = _StubOpener({"models": [{"name": "qwen3vl:8b", "size_vram": 0}]})
    assert not gbc_tagger._unload_local_vlm("http://localhost:11434/v1", "qwen3vl:8b",
                                            opener=opener)
    assert len(opener.calls) == 1


def test_unload_local_vlm_skips_remote_endpoint() -> None:
    opener = _StubOpener({"models": [{"name": "qwen3vl:8b", "size_vram": 1}]})
    assert not gbc_tagger._unload_local_vlm("https://api.example.com/v1", "qwen3vl:8b",
                                            opener=opener)
    assert opener.calls == []


def test_unload_local_vlm_tolerates_foreign_payload() -> None:
    opener = _StubOpener([{"name": "qwen3vl:8b"}])
    assert not gbc_tagger._unload_local_vlm("http://127.0.0.1:11434/v1", "qwen3vl:8b",
                                            opener=opener)
    assert len(opener.calls) == 1


def test_failure_summary_surfaces_placement_root_cause() -> None:
    output = "\n".join([
        "Traceback (most recent call last):",
        "ValueError: placement does not fit:",
        "  cuda:0: needs 5.00 GiB, 3.08 GiB free (less 5% headroom): "
        "detector weights and activations 5.00 GiB",
        "ValueError: engine hosts failed to start:",
        "  local: engine host 'local' exited (1)",
    ])
    text = gbc_tagger._failure_summary(output, 1)
    assert "ValueError: placement does not fit" in text
    assert "needs 5.00 GiB, 3.08 GiB free" in text
    assert "raise " not in text and "File \"" not in text
    assert "显存不足" in text and "cpu" in text


def test_failure_summary_falls_back_to_last_lines() -> None:
    text = gbc_tagger._failure_summary("warm up\nboom: bad config\ncleanup", 2)
    assert "boom: bad config" in text and "显存不足" not in text
    assert gbc_tagger._failure_summary("", 2) == "exit=2"


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "app.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod._wal_ready = False
    dbmod.init_db()
    return path


def test_gbc_store_roundtrip(isolated_db: Path) -> None:
    graph = _graph()
    moss = gbc_tagger.to_moss(graph)
    layout = {"layout": {"summary": {"canvas": "single", "caption": "x"}, "items": []},
              "parts": {"style": "x"}}
    gbc_store.save("a1", "home", graph=graph, moss=moss,
                   moss_draft=gbc_tagger.to_moss(graph, draft=True),
                   engine={"vlm_model": "m", "device": "cuda"}, moss_layout=layout)
    saved = gbc_store.get("a1")
    assert saved is not None
    assert saved["graph"]["img_size"] == [768, 1536]
    assert saved["moss"]["items"][0]["bbox"] == [-762, -360, 762, 312]
    assert saved["engine"]["device"] == "cuda"
    assert saved["moss_layout"]["parts"]["style"] == "x"

    # 重跑 GBC 不带 moss_layout ⇒ 旧官方布局跟着图一起作废
    gbc_store.save("a1", "home", graph=graph, moss=moss, moss_draft={}, engine={})
    assert len(gbc_store.get("a1")["moss"]["items"]) == 2   # 同 id 覆盖，不叠加
    assert gbc_store.get("a1")["moss_layout"] == {}

    assert gbc_store.save_layout("a1", layout) is True
    assert gbc_store.get("a1")["moss_layout"]["layout"]["summary"]["caption"] == "x"
    assert gbc_store.save_layout("missing", layout) is False

    gbc_store.drop("a1")
    assert gbc_store.get("a1") is None


def test_gbc_graph_response_drops_unused_layout_payloads(isolated_db: Path) -> None:
    """详情面板只消费场景图摘要与官方布局；MOSS 交付形/草稿形/归一化 Layout
    不再交付（2026-11-07 用户定案），响应里不许再漏出来。"""
    from app.routers import rag as rag_router

    graph = _graph()
    gbc_store.save("a1", "home", graph=graph, moss=gbc_tagger.to_moss(graph),
                   moss_draft=gbc_tagger.to_moss(graph, draft=True), engine={})
    out = rag_router.gbc_graph(rag_router.GbcGraphRequest(id="a1"))
    assert out["ok"] is True
    assert [e["name"] for e in out["outline"]["entities"]] == ["girl", "thigh_1"]
    assert "layout" not in out and "moss" not in out and "moss_draft" not in out
    assert out["moss_layout"] == {}


def test_asset_gbc_table_created(isolated_db: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(isolated_db)
    try:
        names = {r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'")}
    finally:
        conn.close()
    assert "asset_gbc" in names


def test_asset_gbc_migration_adds_moss_layout(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """老库（没有 moss_layout_json）升级时要补列，而不是让详情面板 500。"""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """create table asset_gbc (
               asset_id text primary key,
               repo_id text not null default '',
               graph_json text not null default '{}',
               moss_json text not null default '{}',
               moss_draft_json text not null default '{}',
               engine_json text not null default '{}',
               created_at integer not null default 0)""")
    conn.execute("insert into asset_gbc (asset_id) values ('a1')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod._wal_ready = False
    dbmod.init_db()
    conn = sqlite3.connect(path)
    try:
        columns = {r[1] for r in conn.execute("pragma table_info(asset_gbc)")}
        old = conn.execute("select moss_layout_json from asset_gbc where asset_id='a1'").fetchone()
    finally:
        conn.close()
    assert "moss_layout_json" in columns
    assert old == ("{}",)


def test_no_proxy_sanitized_for_gbc_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """机器级 NO_PROXY 含 `[::1]` 会让 GBC 的 api 引擎在建 httpx 客户端时直接死。

    2026-11-06 真机根因：httpx 0.28.1 的 `get_environment_proxies` 只认裸 IPv6，
    `[::1]` 退化成非法图案 `all://*[::1]` ⇒ `InvalidURL: Invalid port: ':1]'`
    ⇒ GBC 报 `lane 'vlm' unit 'api-0': start() failed: I/O loop did not start`。
    """
    import httpx

    dirty = "localhost,127.0.0.0/8,[::1],::1,bad[entry]"
    monkeypatch.setenv("NO_PROXY", dirty)
    monkeypatch.setenv("no_proxy", dirty)
    env = gbc_tagger._subprocess_env("")
    tokens = env["NO_PROXY"].split(",")
    assert "::1" in tokens            # 括号解包，语义保留
    assert "[::1]" not in tokens      # 脏项已清
    assert "bad" not in tokens        # 畸形项丢弃
    assert "127.0.0.1" in tokens and "localhost" in tokens

    # 真失败路径：脏环境建客户端必抛；清洗后的环境可建。
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:7890")
    with pytest.raises(httpx.InvalidURL):
        httpx.Client(trust_env=True)
    monkeypatch.setenv("NO_PROXY", env["NO_PROXY"])
    monkeypatch.setenv("no_proxy", env["NO_PROXY"])
    client = httpx.Client(trust_env=True)
    client.close()


def test_subprocess_env_carries_vlm_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """云端视觉模型要走产品解析出的代理；直连时必须清掉继承来的系统代理。

    GBC 子进程只调 VLM，父进程若带 `HTTP_PROXY` 而前端选的是「直连」，不清掉就等于
    把用户选项静默推翻；反过来选了代理却不透传，就会出现「描述走代理、GBC 直连失败」。
    """
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:7890")

    env = gbc_tagger._subprocess_env("")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert name not in env, name

    env = gbc_tagger._subprocess_env("sk-test", "http://127.0.0.1:7897")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert env[name] == "http://127.0.0.1:7897", name
    assert env[gbc_tagger.API_KEY_ENV] == "sk-test"
    assert "127.0.0.1" in env["NO_PROXY"].split(",")  # 本机回环仍直连


def _fault_event(message: str = "HTTP 500 from POST https://api.deepseek.com/chat/completions",
                 *, node: str = "chat", kind: str = "fault.taken") -> str:
    return json.dumps({"kind": kind, "error_node": node, "error_message": message},
                      ensure_ascii=False)


def test_vlm_faults_reads_chat_http_errors(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "events.jsonl").write_text(
        _fault_event() + "\n" + _fault_event("HTTP 500 from POST https://api.deepseek.com/chat/completions")
        + "\n" + _fault_event("detect exploded", node="detect") + "\n",
        encoding="utf-8",
    )
    faults = gbc_tagger._vlm_faults(tmp_path)
    assert faults == ["HTTP 500 from POST https://api.deepseek.com/chat/completions"]


def test_vlm_faults_falls_back_to_lane_counts(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "summary.json").write_text(json.dumps({
        "lanes": {"vlm": {"submitted": 2, "batches": 0}},
        "events": {"fault.taken": 2},
    }), encoding="utf-8")
    assert gbc_tagger._vlm_faults(tmp_path) == [
        "视觉模型调用未产出结果（提交 2 次、失败 2 次）"]


def test_empty_graph_error_only_when_vlm_faulted(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "events.jsonl").write_text(_fault_event() + "\n", encoding="utf-8")
    empty = {"vertices": [{"label": "image"}]}

    msg = gbc_tagger._empty_graph_error(empty, tmp_path, cloud=True)
    assert msg is not None
    assert "HTTP 500" in msg and "本地视觉模型" in msg and "未保存空图" in msg
    # 本地端点不给「云端拒绝」的建议
    local = gbc_tagger._empty_graph_error(empty, tmp_path, cloud=False)
    assert local is not None and "检查视觉模型是否可用" in local

    # 有实体 ⇒ 不拦（部分成功仍交付）
    assert gbc_tagger._empty_graph_error(
        {"vertices": [{"label": "image"}, {"label": "entity"}]}, tmp_path, cloud=True) is None
    # 没有 fault ⇒ 保持原行为（0 实体是描述/标签问题，不是 VLM 故障）
    clean = tmp_path / "clean"
    (clean / "report").mkdir(parents=True)
    assert gbc_tagger._empty_graph_error(empty, clean, cloud=True) is None
