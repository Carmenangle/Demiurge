"""LoRA 触发词库：元数据解析、同步保护、编排注入。"""
import json
import sqlite3
import struct

from app.services import lora_index, lora_scan
from app.services.lora_inject import collect_triggers, inject

SCHEMA = """
create table lora_triggers (
    lora_name text primary key,
    triggers text not null default '',
    note text not null default '',
    suggested_weight real not null default 0.8,
    suggested_prompt text not null default '',
    source text not null default '',
    missing integer not null default 0,
    updated_at integer not null
)
"""


def _connection_factory(path):
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


def _prepare_db(tmp_path, monkeypatch):
    path = tmp_path / "lora.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
    monkeypatch.setattr(lora_index, "get_connection", _connection_factory(path))


def _write_safetensors(path, tag_freq=None, extra=None):
    """造一个只有头部的 safetensors：8 字节小端长度 + JSON。"""
    meta = dict(extra or {})
    if tag_freq is not None:
        meta["ss_tag_frequency"] = json.dumps(tag_freq)
    payload = json.dumps({"__metadata__": meta}).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(payload)) + payload)


def test_scan_dir_is_recursive_and_posix(tmp_path):
    _write_safetensors(tmp_path / "anime" / "a.safetensors", {"d": {"x": 1}})
    (tmp_path / "b.ckpt").write_bytes(b"x")
    (tmp_path / "note.txt").write_text("ignore me", encoding="utf-8")
    # 路径必须是正斜杠：要和 ComfyUI 的 lora_name 逐字对齐，Windows 上也一样
    assert lora_scan.scan_lora_dir(tmp_path) == ["anime/a.safetensors", "b.ckpt"]


def test_available_endpoint_lists_disk_files_and_marks_triggers(tmp_path, monkeypatch):
    from app.routers import loras as loras_router
    _prepare_db(tmp_path, monkeypatch)
    models = tmp_path / "models"
    (models / "loras" / "anime").mkdir(parents=True)
    (models / "loras" / "anime" / "a.safetensors").write_bytes(b"x")
    (models / "loras" / "b.safetensors").write_bytes(b"x")
    # 只给 a 录触发词 → has_triggers 仅 a 为真
    lora_index.save_item("anime/a.safetensors", ["trig"], "", None)

    items = loras_router.available_loras(str(models))["items"]
    by_name = {it["lora_name"]: it["has_triggers"] for it in items}
    assert set(by_name) == {"anime/a.safetensors", "b.safetensors"}  # 直接读盘，无需同步
    assert by_name["anime/a.safetensors"] is True
    assert by_name["b.safetensors"] is False
    assert {it["suggested_weight"] for it in items} == {0.8}


def test_suggested_weight_round_trip_and_sync_preserves_manual_value(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    loras = tmp_path / "loras"
    _write_safetensors(loras / "a.safetensors", {"10_x": {"trig": 10}})
    _sync(loras)
    saved = lora_index.save_item(
        "a.safetensors", ["trig"], suggested_weight=1.15,
        suggested_prompt="masterpiece, best quality",
    )
    assert saved["suggested_weight"] == 1.15
    assert saved["suggested_prompt"] == "masterpiece, best quality"
    _sync(loras)
    assert lora_index.list_items()[0]["suggested_weight"] == 1.15
    assert lora_index.list_items()[0]["suggested_prompt"] == "masterpiece, best quality"
    assert lora_index.normalize_suggested_weight(9) == 2.0


def test_available_endpoint_distinguishes_confirmed_triggerless_lora(tmp_path, monkeypatch):
    from app.routers import loras as loras_router
    _prepare_db(tmp_path, monkeypatch)
    models = tmp_path / "models"
    loras = models / "loras"
    for name in ("configured.safetensors", "universal.safetensors", "unknown.safetensors"):
        (loras / name).parent.mkdir(parents=True, exist_ok=True)
        (loras / name).write_bytes(b"x")
    lora_index.save_item("configured.safetensors", ["exact trigger"], "", None)
    # 手动保存空触发词表示用户已确认：这是无需触发词的通用 LoRA。
    lora_index.save_item("universal.safetensors", [], "", None)

    items = loras_router.available_loras(str(models))["items"]
    status = {it["lora_name"]: it["trigger_status"] for it in items}

    assert status == {
        "configured.safetensors": "configured",
        "universal.safetensors": "not_required",
        "unknown.safetensors": "unconfirmed",
    }


def test_available_endpoint_empty_when_no_dir():
    from app.routers import loras as loras_router
    assert loras_router.available_loras("")["items"] == []


def test_scan_missing_dir_returns_empty(tmp_path):
    # 用户还没设好路径时要给空结果而非抛错
    assert lora_scan.scan_lora_dir(tmp_path / "nope") == []


def test_extract_triggers_takes_high_frequency_only(tmp_path):
    f = tmp_path / "a.safetensors"
    _write_safetensors(f, {"10_x": {"mksks style": 100, "hair ribbon": 95, "hat": 30}})
    # 100 与 95 都过 90% 阈值；30 是普通 tag，不算触发词
    assert lora_scan.extract_triggers(lora_scan.read_safetensors_meta(f)) == [
        "mksks style", "hair ribbon"]


def test_generic_booru_tags_are_not_triggers(tmp_path):
    f = tmp_path / "a.safetensors"
    # 实测 iLLC0lorL1nes 的形态：真触发词和 1girl/solo 次数完全并列，
    # 光靠频率阈值分不开。注进提示词会强行改画面，必须靠停用词剔掉。
    _write_safetensors(f, {"img": {"c0lorl1nes": 45, "1girl": 45, "solo": 44}})
    assert lora_scan.extract_triggers(lora_scan.read_safetensors_meta(f)) == ["c0lorl1nes"]


def test_stopword_matches_underscore_spelling(tmp_path):
    f = tmp_path / "a.safetensors"
    # booru 标签下划线/空格两种写法混用，停用词要都认
    _write_safetensors(f, {"img": {"looking_at_viewer": 50, "mytrigger": 50}})
    assert lora_scan.extract_triggers(lora_scan.read_safetensors_meta(f)) == ["mytrigger"]


def test_all_generic_tags_yields_nothing(tmp_path):
    f = tmp_path / "a.safetensors"
    # 全是通用词时不能因为“总要给个结果”而硬塞一个出来
    _write_safetensors(f, {"img": {"1girl": 50, "solo": 50}})
    assert lora_scan.extract_triggers(lora_scan.read_safetensors_meta(f)) == []


def test_output_name_is_not_a_trigger(tmp_path):
    f = tmp_path / "a.safetensors"
    _write_safetensors(f, None, {"ss_output_name": "last"})
    # ss_output_name 只是训练输出文件名，当触发词注进提示词纯属污染
    assert lora_scan.extract_triggers(lora_scan.read_safetensors_meta(f)) == []


def test_broken_header_is_tolerated(tmp_path):
    f = tmp_path / "bad.safetensors"
    f.write_bytes(b"\x00" * 4)     # 连 8 字节长度都不够
    assert lora_scan.read_safetensors_meta(f) == {}
    assert lora_scan.detect_triggers(f) == ([], "")


def test_pickle_formats_are_not_parsed(tmp_path):
    f = tmp_path / "a.pt"
    f.write_bytes(b"junk")
    # .pt/.ckpt 是 pickle，不解，避免任意代码执行
    assert lora_scan.read_safetensors_meta(f) == {}


def test_sidecar_fallback(tmp_path):
    f = tmp_path / "c.safetensors"
    f.write_bytes(b"\x00" * 4)     # 元数据提不到
    (tmp_path / "c.civitai.info").write_text(
        json.dumps({"trainedWords": ["ganyu", "  genshin  "]}), encoding="utf-8")
    assert lora_scan.detect_triggers(f) == (["ganyu", "genshin"], "sidecar")


def test_lora_manager_metadata_json_sidecar(tmp_path):
    f = tmp_path / "e.safetensors"
    f.write_bytes(b"\x00" * 4)
    # ComfyUI-Lora-Manager 把整个 civitai 响应嵌在 civitai 键下，不放顶层
    (tmp_path / "e.metadata.json").write_text(
        json.dumps({"model_name": "e", "civitai": {"trainedWords": ["c0lorl1nes"]}}),
        encoding="utf-8")
    assert lora_scan.detect_triggers(f) == (["c0lorl1nes"], "sidecar")


def test_empty_sidecar_is_not_a_hit(tmp_path):
    f = tmp_path / "f.safetensors"
    f.write_bytes(b"\x00" * 4)
    # Lora-Manager 未抓到 civitai 数据时会留个空壳，别当成命中
    (tmp_path / "f.metadata.json").write_text(
        json.dumps({"civitai": {}, "tags": []}), encoding="utf-8")
    assert lora_scan.detect_triggers(f) == ([], "")


def test_metadata_wins_over_sidecar(tmp_path):
    f = tmp_path / "d.safetensors"
    _write_safetensors(f, {"10_x": {"from meta": 10}})
    (tmp_path / "d.civitai.info").write_text(
        json.dumps({"trainedWords": ["from sidecar"]}), encoding="utf-8")
    assert lora_scan.detect_triggers(f) == (["from meta"], "metadata")


def _sync(loras_dir, full=False):
    """直接调 _do_sync 而非 start_sync：跳过后台线程，测试才是确定性的。"""
    names = lora_scan.scan_lora_dir(loras_dir)
    lora_index._do_sync(loras_dir, names, None, full)


def test_sync_extracts_and_records_source(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    loras = tmp_path / "loras"
    _write_safetensors(loras / "a.safetensors", {"10_x": {"trig": 10}})
    _sync(loras)
    items = {i["lora_name"]: i for i in lora_index.list_items()}
    assert items["a.safetensors"]["triggers"] == ["trig"]
    assert items["a.safetensors"]["source"] == "metadata"


def test_sync_does_not_overwrite_manual(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    loras = tmp_path / "loras"
    _write_safetensors(loras / "a.safetensors", {"10_x": {"auto word": 10}})
    _sync(loras)
    lora_index.save_item("a.safetensors", ["my word"], note="校正过")
    _sync(loras)
    item = lora_index.list_items()[0]
    # 用户手工校正的内容优先于自动提取，再同步也不能被冲掉
    assert item["triggers"] == ["my word"]
    assert item["source"] == "manual"
    assert item["note"] == "校正过"


def test_full_sync_does_overwrite_manual(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    loras = tmp_path / "loras"
    _write_safetensors(loras / "a.safetensors", {"10_x": {"auto word": 10}})
    lora_index.save_item("a.safetensors", ["my word"])
    _sync(loras, full=True)     # 用户显式要求重建时才覆盖
    assert lora_index.list_items()[0]["triggers"] == ["auto word"]


def test_vanished_file_is_marked_not_deleted(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    loras = tmp_path / "loras"
    f = loras / "a.safetensors"
    _write_safetensors(f, {"10_x": {"trig": 10}})
    _sync(loras)
    f.unlink()
    _sync(loras)
    items = lora_index.list_items()
    # 不删是为了保住用户手填的内容；标 missing 让前端能提示
    assert len(items) == 1
    assert items[0]["missing"] is True


def test_triggers_map_skips_missing_and_empty(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    loras = tmp_path / "loras"
    _write_safetensors(loras / "has.safetensors", {"10_x": {"trig": 10}})
    (loras / "none.safetensors").write_bytes(b"\x00" * 4)   # 提不到词
    _sync(loras)
    (loras / "has.safetensors").unlink()
    _sync(loras)
    # 缺失的和没触发词的都不该进注入用的表
    assert lora_index.get_triggers_map() == {}


def _schema(lora_name="a.safetensors", mode=0, lora_id="3"):
    """最小可用的编排结构：LoraLoader + 正向 CLIPTextEncode + KSampler。"""
    return [
        {"id": lora_id, "type": "LoraLoader", "mode": mode,
         "widgets": [{"name": "lora_name", "value": lora_name}]},
        {"id": "5", "type": "CLIPTextEncode",
         "widgets": [{"name": "text", "value": ""}]},
        {"id": "7", "type": "KSampler",
         "inputs": [{"name": "positive", "source_node_id": "5"},
                    {"name": "negative", "source_node_id": "6"}]},
    ]


def _plan(text="1girl, smile", node_id="5"):
    return {"is_orchestration": True, "summary": "改了正向提示词",
            "ops": [{"node_id": node_id, "input": "text",
                     "action": "set_widget", "value": text}]}


def test_inject_prepends_trigger(tmp_path):
    plan = _plan()
    got = inject(plan, _schema(), "画个女孩", {"a.safetensors": ["trig word"]})
    assert got == ["trig word"]
    # 触发词必须在最前：多数 LoRA 对触发词位置敏感
    assert plan["ops"][0]["value"] == "trig word, 1girl, smile"
    # 计划要用户确认，凭空多出的词得在 summary 里交代清楚
    assert "trig word" in plan["summary"]


def test_skip_when_user_already_named_it(tmp_path):
    plan = _plan()
    # 用户自己在需求里点了名，就不该再注一遍
    assert inject(plan, _schema(), "画个女孩，用 TRIG WORD 风格",
                  {"a.safetensors": ["trig word"]}) == []
    assert plan["ops"][0]["value"] == "1girl, smile"


def test_skip_when_model_already_wrote_it(tmp_path):
    plan = _plan(text="trig word, 1girl")
    assert inject(plan, _schema(), "画个女孩", {"a.safetensors": ["trig word"]}) == []
    assert plan["ops"][0]["value"] == "trig word, 1girl"


def test_skip_bypassed_lora(tmp_path):
    plan = _plan()
    # 绕过的 LoRA 没加载，注它的词纯属污染
    assert inject(plan, _schema(mode=4), "画个女孩", {"a.safetensors": ["trig word"]}) == []
    assert plan["ops"][0]["value"] == "1girl, smile"


def test_multi_lora_dedupes_and_keeps_order(tmp_path):
    schema = _schema("a.safetensors") + [
        {"id": "4", "type": "LoraLoader",
         "widgets": [{"name": "lora_name", "value": "b.safetensors"}]},
    ]
    plan = _plan()
    got = inject(plan, schema, "x", {"a.safetensors": ["one", "shared"],
                                     "b.safetensors": ["shared", "two"]})
    assert got == ["one", "shared", "two"]


def test_no_prompt_op_means_no_injection(tmp_path):
    # 用户只说「seed 改成 5」，没提提示词口，就不该去动它
    plan = {"is_orchestration": True, "summary": "",
            "ops": [{"node_id": "7", "input": "seed",
                     "action": "set_widget", "value": 5}]}
    assert inject(plan, _schema(), "seed 改成 5", {"a.safetensors": ["trig"]}) == []


def test_uses_new_lora_when_plan_switches_it(tmp_path):
    plan = _plan()
    plan["ops"].append({"node_id": "3", "input": "lora_name",
                        "action": "set_widget", "value": "b.safetensors"})
    # 用户说「换成 b 这个 lora」时，该注 b 的触发词而不是画布上旧的 a
    got = inject(plan, _schema("a.safetensors"), "换成 b",
                 {"a.safetensors": ["old"], "b.safetensors": ["new"]})
    assert got == ["new"]


def test_custom_lora_loader_is_recognized(tmp_path):
    nodes = [{"id": "3", "type": "LoraLoaderModelOnly",
              "widgets": [{"name": "lora_name", "value": "a.safetensors"}]}]
    # 靠「类型名含 lora + 有 lora_name widget」判定，兼容各种自定义加载器
    assert collect_triggers(nodes, [], {"a.safetensors": ["t"]}) == ["t"]


def test_non_lora_node_with_lora_in_name_is_ignored(tmp_path):
    nodes = [{"id": "3", "type": "LoraTagsHelper", "widgets": [{"name": "text", "value": "x"}]}]
    # 没有 lora_name widget，不是加载器
    assert collect_triggers(nodes, [], {"a.safetensors": ["t"]}) == []
