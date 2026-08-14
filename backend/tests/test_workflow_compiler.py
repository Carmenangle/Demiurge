# -*- coding: utf-8 -*-
"""Typed ComfyUI Graph Compiler 测试：显存估算、模型族检查、编译缺口、可达性终检。"""
from __future__ import annotations


from app.services import workflow_compiler as wc
from app.services import workflow_graph_rules as wgr


# ── 样本 object_info（模拟 ComfyUI 内置节点） ────────────────────────────────

def _object_info() -> dict:
    return {
        "CheckpointLoaderSimple": {
            "input": {
                "required": {
                    "ckpt_name": [[
                        "sd_xl_base_1.0.safetensors",
                        "v1-5-pruned-emaonly.safetensors",
                        "flux1-dev.safetensors",
                    ]],
                },
            },
            "output": ["MODEL", "CLIP", "VAE"],
            "output_name": ["MODEL", "CLIP", "VAE"],
            "output_node": False,
        },
        "CLIPTextEncode": {
            "input": {
                "required": {
                    "text": ["STRING", {"multiline": True}],
                    "clip": ["CLIP", {}],
                },
            },
            "output": ["CONDITIONING"],
            "output_node": False,
        },
        "EmptyLatentImage": {
            "input": {
                "required": {
                    "width": ["INT", {"default": 512, "min": 64, "max": 2048}],
                    "height": ["INT", {"default": 512, "min": 64, "max": 2048}],
                    "batch_size": ["INT", {"default": 1}],
                },
            },
            "output": ["LATENT"],
            "output_node": False,
        },
        "KSampler": {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "positive": ["CONDITIONING", {}],
                    "negative": ["CONDITIONING", {}],
                    "latent_image": ["LATENT", {}],
                    "seed": ["INT", {"default": 0}],
                    "steps": ["INT", {"default": 20}],
                    "cfg": ["FLOAT", {"default": 7.0}],
                    "sampler_name": [["euler", "dpmpp_2m"]],
                    "scheduler": [["normal", "karras"]],
                    "denoise": ["FLOAT", {"default": 1.0}],
                },
            },
            "output": ["LATENT"],
            "output_node": False,
        },
        "VAEDecode": {
            "input": {
                "required": {
                    "samples": ["LATENT", {}],
                    "vae": ["VAE", {}],
                },
            },
            "output": ["IMAGE"],
            "output_node": False,
        },
        "SaveImage": {
            "input": {
                "required": {
                    "images": ["IMAGE", {}],
                    "filename_prefix": ["STRING", {"default": "ComfyUI"}],
                },
            },
            "output": [],
            "output_node": True,
        },
        "VAELoader": {
            "input": {
                "required": {
                    "vae_name": [["vae-ft-mse-840000.safetensors", "sdxl_vae.safetensors"]],
                },
            },
            "output": ["VAE"],
            "output_node": False,
        },
        "UNETLoader": {
            "input": {
                "required": {
                    "unet_name": [["flux1-dev.safetensors", "sd3_medium.safetensors"]],
                    "weight_dtype": [["default", "fp8_e4m3fn"]],
                },
            },
            "output": ["MODEL"],
            "output_node": False,
        },
        "DualCLIPLoader": {
            "input": {
                "required": {
                    "clip_name1": [["t5xxl_fp16.safetensors", "clip_l.safetensors"]],
                    "clip_name2": [["t5xxl_fp16.safetensors", "clip_l.safetensors"]],
                    "type": [["flux", "sdxl"]],
                },
            },
            "output": ["CLIP"],
            "output_node": False,
        },
    }


def _valid_sdxl_graph() -> dict:
    """一个合法 SDXL 文生图工作流。"""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a cat", "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry", "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": 42, "steps": 20, "cfg": 7.0,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": "test"},
        },
    }


# ── 显存估算 ─────────────────────────────────────────────────────────────────


def test_estimate_vram_sdxl():
    graph = _valid_sdxl_graph()
    obj = _object_info()
    vram = wc.estimate_vram(graph, obj)
    # CheckpointLoaderSimple=7000 + VAEDecode=2000 + SaveImage=100 ≈ 9100
    assert vram >= 9000
    assert vram < 12000


def test_estimate_vram_empty():
    assert wc.estimate_vram({}, _object_info()) == 0


# ── 模型族一致性 ─────────────────────────────────────────────────────────────


def test_model_family_consistent_sdxl():
    graph = _valid_sdxl_graph()
    gaps = wc.check_model_family_consistency(graph, _object_info())
    assert gaps == []


def test_model_family_inconsistent_vae():
    """UNET 用 flux，VAE 用 sdxl → 应报模型族缺口。"""
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "t"}},
    }
    gaps = wc.check_model_family_consistency(graph, _object_info())
    assert any(g.kind == "model_family" for g in gaps)


def test_model_family_inconsistent_clip():
    """主模型 flux，CLIP 用 sdxl → 应报 CLIP 不配套。"""
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "clip_l.safetensors", "type": "sdxl"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux-vae.safetensors"}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "t"}},
    }
    gaps = wc.check_model_family_consistency(graph, _object_info())
    assert any(g.kind == "model_family" for g in gaps)


# ── 编译缺口 ─────────────────────────────────────────────────────────────────


def test_compile_missing_node_gap():
    """图中含未安装节点 → 编译应返回 model_not_found 缺口并拆掉该节点。"""
    graph = {
        "1": {"class_type": "Qwen2VLLoader", "inputs": {}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "t"}},
    }
    result = wc.compile("test", _object_info(), current_graph=graph)
    assert not result.ok
    assert any(g.kind == "model_not_found" for g in result.gaps)
    # 缺口里应列出缺失节点类型
    missing_gap = next(g for g in result.gaps if g.kind == "model_not_found")
    assert "Qwen2VLLoader" in missing_gap.missing_names


def test_compile_success():
    """合法图 → 编译成功、无缺口、可提交。"""
    graph = _valid_sdxl_graph()
    result = wc.compile("test", _object_info(), current_graph=graph)
    assert result.ok
    assert result.graph is not None
    assert result.gaps == []
    # 提交前校验
    errs = wgr.validate_graph(result.graph, _object_info())
    assert errs == []


def test_compile_type_mismatch_gap():
    """连线类型不匹配 → 返回 type_mismatch 缺口。"""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        # CLIPTextEncode 的 clip 口需要 CLIP，但连了 MODEL 输出
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "cat", "clip": ["1", 0]}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "t"}},
    }
    result = wc.compile("test", _object_info(), current_graph=graph)
    assert not result.ok
    assert any(g.kind == "type_mismatch" for g in result.gaps)


def test_compile_no_output_gap():
    """无输出节点 → 返回 graph_empty 缺口。"""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "cat", "clip": ["1", 1]}},
    }
    result = wc.compile("test", _object_info(), current_graph=graph)
    assert not result.ok
    assert any(g.kind == "graph_empty" for g in result.gaps)


# ── 便捷 API ─────────────────────────────────────────────────────────────────


def test_format_gaps_empty():
    assert "无缺口" in wc.format_gaps([])


def test_format_gaps_nonempty():
    gaps = [wc.CompilerGaps(kind="model_not_found", message="x", suggestion="y")]
    text = wc.format_gaps(gaps)
    assert "model_not_found" in text
    assert "y" in text


def test_compiler_result_ok_flag():
    ok = wc.CompilerResult(graph={"a": 1}, gaps=[])
    bad = wc.CompilerResult(graph=None, gaps=[wc.CompilerGaps("x", "m")])
    assert ok.ok is True
    assert bad.ok is False
