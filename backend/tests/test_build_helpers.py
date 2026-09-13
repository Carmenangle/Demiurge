"""搭工作流纯 helper 测试：脱离 live ComfyUI/模型验证从路由下沉到服务层的纯逻辑。
覆盖 JSON 抽取容错、节点清单控量、控制流兜底注入、点名节点抽取、prompt 瘦身。"""
from app.services import node_candidates as nc
from app.services import workflow_builder as wb
from app.services import workflow_graph_rules


def test_extract_json_去围栏():
    assert wb._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert wb._extract_json('前言{"a": 1}后语') == {"a": 1}


def test_extract_json_无json抛错():
    import pytest
    with pytest.raises(ValueError):
        wb._extract_json("没有大括号")


def test_trim_catalog_超长截断():
    packs = [{"content": "x" * 5000}]
    out = wb._trim_catalog(packs, per_pack=100, total=12000)
    assert "已截断" in out and len(out) < 5000


def test_trim_catalog_总量封顶省略():
    packs = [{"content": "a" * 80}, {"content": "b" * 80}, {"content": "c" * 80}]
    out = wb._trim_catalog(packs, per_pack=200, total=100)
    assert "因篇幅省略" in out


def test_candidate_resolution_只补本机存在的(monkeypatch):
    oi = {"Any Switch (rgthree)": {}, "KSampler": {}}
    monkeypatch.setattr(nc.comfyui_client, "fetch_object_info", lambda _url: oi)
    monkeypatch.setattr(nc.node_index, "search", lambda *_args, **_kwargs: [
        {"node_names": ["KSampler", "ImpactSwitch"]},
    ])
    out = nc.resolve(object(), "采样", "http://comfy")
    assert "Any Switch (rgthree)" in out.names    # 存在→补
    assert "ImpactSwitch" not in out.names        # RAG 有但本机没有→跳过
    assert out.names.count("KSampler") == 1        # 去重保序


def test_关键能力节点不会被大型RAG包挤出接口表(monkeypatch):
    targets = [
        "DanbooruGalleryNode", "WD14Tagger|pysssss",
        "llama_cpp_model_loader", "llama_cpp_instruct_adv",
        "UNETLoader",
    ]
    oi = {
        **{
            f"Noise{i}": {"description": "动漫流程工具", "input": {}, "output": []}
            for i in range(30)
        },
        **{name: {"input": {}, "output": []} for name in targets},
    }
    monkeypatch.setattr(nc.comfyui_client, "fetch_object_info", lambda _url: oi)
    monkeypatch.setattr(nc.node_index, "search", lambda *_args, **_kwargs: [
        {"node_names": targets},
    ])

    candidates = nc.resolve(object(), "搭建动漫流程", "http://comfy")
    sheet = workflow_graph_rules.interface_sheet(candidates.names, oi, max_nodes=32)

    assert all(f"{name}:" in sheet for name in targets)


def test_顾问禁止把候选表缺项判断为未安装():
    assert "不能据此断言本机未安装" in wb._PLAN_SYSTEM


def test_named_nodes_in_text_抽点名节点():
    oi = {"KSampler": {}, "VAEDecode": {"display_name": "VAE 解码"}}
    hits = nc.named_nodes_in_text("请用 KSampler 采样再 VAE 解码", oi)
    assert "KSampler" in hits and "VAEDecode" in hits


def test_inventory_candidates_anima_prefers_real_loaders():
    oi = {
        "UNETLoader": {"display_name": "UNET 加载器", "category": "loaders"},
        "DualCLIPLoader": {"display_name": "DualCLIP 加载器", "category": "loaders"},
        "VAELoader": {"display_name": "VAE 加载器", "category": "loaders"},
        "CheckpointLoaderSimple": {"display_name": "Checkpoint 加载器", "category": "loaders"},
    }
    hits = nc.inventory_candidates("Anima 模型", oi)
    assert "UNETLoader" in hits
    assert "VAELoader" in hits


def test_slim_graph_保连线省widget():
    base = {"1": {"class_type": "KSampler",
                  "inputs": {"model": ["2", 0], "seed": 12345, "text": "a" * 50}}}
    slim = wb._slim_graph_for_prompt(base)
    assert slim["1"]["inputs"]["model"] == ["2", 0]   # 连线原样留
    assert slim["1"]["inputs"]["seed"] == "…"          # 标量占位
    assert slim["1"]["inputs"]["text"].endswith("…")   # 长文本截断
