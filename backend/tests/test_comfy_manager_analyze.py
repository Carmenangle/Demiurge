"""analyze_workflow：以 object_info 为唯一已安装判据的回归测试。"""
from app.services import comfy_manager


def _patch(monkeypatch, *, installed_types, market, mappings=None):
    from app.services import comfyui_client as cc
    monkeypatch.setattr(cc, "fetch_object_info", lambda url: {t: {} for t in installed_types})
    monkeypatch.setattr(comfy_manager, "list_market", lambda url: market)
    monkeypatch.setattr(comfy_manager, "_get", lambda url, path: mappings or {})


def test_installed_node_not_reported_missing(monkeypatch):
    # 节点已在 object_info 中：即使包 id 与 market 命名不一致也不算缺失
    wf = {"nodes": [{"type": "AnimaSampler", "properties": {"cnr_id": "comfyui-anima"}}]}
    _patch(monkeypatch, installed_types={"AnimaSampler"}, market=[])
    result = comfy_manager.analyze_workflow("http://x", wf)
    assert result["missing_packs"] == []
    assert result["packs"] == []
    assert result["unresolved"] == []


def test_no_cnr_id_does_not_create_none_pack(monkeypatch):
    # 老工作流无 cnr_id：不得产生 "None" 假包
    wf = {"nodes": [{"type": "SomeNode", "properties": {}}]}
    _patch(monkeypatch, installed_types=set(), market=[], mappings={})
    result = comfy_manager.analyze_workflow("http://x", wf)
    assert "None" not in result["missing_packs"]
    assert result["unresolved"] == ["SomeNode"]


def test_missing_node_resolved_to_pack_with_repo(monkeypatch):
    wf = {"nodes": [{"type": "FooNode", "properties": {"cnr_id": "foo-pack"}}]}
    market = [{"id": "foo-pack", "title": "Foo", "state": "not-installed",
               "repository": "https://github.com/a/foo"}]
    _patch(monkeypatch, installed_types=set(), market=market)
    result = comfy_manager.analyze_workflow("http://x", wf)
    assert result["missing_packs"] == ["foo-pack"]
    assert len(result["packs"]) == 1
    assert result["packs"][0]["repository"] == "https://github.com/a/foo"


def test_missing_pack_without_repo_goes_unresolved(monkeypatch):
    # 解析到包 id 但 market 无有效仓库地址：不进可安装列表，进 unresolved
    wf = {"nodes": [{"type": "BarNode", "properties": {"cnr_id": "bar-pack"}}]}
    _patch(monkeypatch, installed_types=set(), market=[])
    result = comfy_manager.analyze_workflow("http://x", wf)
    assert result["packs"] == []
    assert "bar-pack" in result["unresolved"]


def test_skip_virtual_nodes(monkeypatch):
    wf = {"nodes": [
        {"type": "Note", "properties": {}},
        {"type": "Reroute", "properties": {}},
        {"type": "PrimitiveNode", "properties": {}},
    ]}
    _patch(monkeypatch, installed_types=set(), market=[])
    result = comfy_manager.analyze_workflow("http://x", wf)
    assert result["missing_packs"] == []
    assert result["unresolved"] == []
