"""P0-3 WD14 反推打标：service 单测 + 端点契约。

用 onnx 包造最小可推理模型（GlobalAveragePool→Flatten→Gemm，输出＝B＋W·输入均值），
全链真跑 onnxruntime（CPU）：锁五件事——
1. 预处理维度/值域（白底 pad 正方形 → 448 → /255 → NHWC → batch）；
2. 阈值分级过滤（general 0.35 / character 0.75）与 rating 默认剔除；
3. 按分降序；model_available 严格校验；release 卸载；
4. 端点：合并进既有 tags（不动原有）、added/total 回报；
5. 未装模型 503 引导 / 资产不存在 404 / 非本机图 400。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper
from PIL import Image

from app.routers import rag as rag_router
from app.services import rag_store, wd14_tagger

# 5 个标签：3 general(cat 0) + 1 character(cat 4) + 1 rating(cat 9)（与 _make_mini_onnx 输出维度对齐）
_TAGS = [("1girl", 0), ("solo", 0), ("long_hair", 0), ("ArtistName", 4), ("sensitive", 9)]
_N = len(_TAGS)


def _make_mini_onnx(path: Path) -> None:
    """造最小 tagger：output = B + W·mean(input)（W 每通道 0.05 只加前三个 tag）。"""
    w = np.zeros((3, _N), dtype=np.float32)
    w[0, 0] = 0.05
    w[1, 1] = 0.05
    w[2, 2] = 0.05
    b = np.array([0.9, 0.8, 0.2, 0.9, 0.9], dtype=np.float32)
    nodes = [
        # NHWC 输入：[1,448,448,3] → 对 H,W 两轴(1,2)取均值 → [1,1,1,3]
        # （GlobalAveragePool 是 NCHW 语义，会把 448 当通道 ⇒ 不能用于 NHWC）
        helper.make_node("ReduceMean", ["input"], ["gap"], axes=[1, 2], keepdims=1),
        helper.make_node("Flatten", ["gap"], ["flat"], axis=1),
        helper.make_node("Gemm", ["flat", "W", "B"], ["output"]),
    ]
    graph = helper.make_graph(
        nodes, "mini-wd14",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 448, 448, 3])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, _N])],
        [
            helper.make_tensor("W", TensorProto.FLOAT, [3, _N], w.tobytes(), raw=True),
            helper.make_tensor("B", TensorProto.FLOAT, [_N], b.tobytes(), raw=True),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 9  # onnx 1.23 默认 IR 14 超出 onnxruntime 1.28 支持（max 13）
    onnx.checker.check_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(path))


def _write_tags_csv(path: Path) -> None:
    lines = ["tag_id,name,category"]
    lines += [f"{i + 1},{name},{cat}" for i, (name, cat) in enumerate(_TAGS)]
    path.write_text("\n".join(lines), encoding="utf-8")


def _install_model(tmp_path: Path) -> Path:
    _make_mini_onnx(tmp_path / "model.onnx")
    _write_tags_csv(tmp_path / "selected_tags.csv")
    return tmp_path


def _make_image(path: Path, color: tuple[int, int, int], size=(600, 300)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


@pytest.fixture()
def model(tmp_path, monkeypatch):
    _install_model(tmp_path)
    monkeypatch.setattr(wd14_tagger, "MODEL_DIR", tmp_path)
    wd14_tagger.release()  # 防跨用例 session 复用
    yield tmp_path
    wd14_tagger.release()


# ===== service 层 =====

def test_model_available_lenient_and_load_layer_is_hard_gate(tmp_path, monkeypatch):
    """就绪判定宽容（只防空文件）；完整性由加载层硬校验（LFS 指针/残缺 → 可读错误）。"""
    monkeypatch.setattr(wd14_tagger, "MODEL_DIR", tmp_path / "empty")
    assert wd14_tagger.model_available() is False
    # 0 字节不算就绪
    (tmp_path / "zero").mkdir()
    (tmp_path / "zero" / "model.onnx").write_bytes(b"")
    (tmp_path / "zero" / "selected_tags.csv").write_bytes(b"")
    monkeypatch.setattr(wd14_tagger, "MODEL_DIR", tmp_path / "zero")
    assert wd14_tagger.model_available() is False
    # HF LFS 指针：判定层放过（大小猜不准），加载层必须报可读错误
    (tmp_path / "lfs").mkdir()
    (tmp_path / "lfs" / "model.onnx").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 400000000\n")
    _write_tags_csv(tmp_path / "lfs" / "selected_tags.csv")
    monkeypatch.setattr(wd14_tagger, "MODEL_DIR", tmp_path / "lfs")
    assert wd14_tagger.model_available() is True  # 宽容放过
    wd14_tagger.release()
    with pytest.raises(RuntimeError, match="下载不完整或损坏"):
        wd14_tagger._load_session()  # 加载层硬校验拦下
    _install_model(tmp_path / "ok")
    monkeypatch.setattr(wd14_tagger, "MODEL_DIR", tmp_path / "ok")
    assert wd14_tagger.model_available() is True


def test_preprocess_shape_and_range(tmp_path):
    src = _make_image(tmp_path / "white.png", (255, 255, 255), size=(600, 300))
    batch = wd14_tagger._preprocess(src)
    assert batch.shape == (1, 448, 448, 3)  # NHWC（wd-swinv2-tagger-v3 要求）
    assert batch.dtype == np.float32
    assert 0.0 <= float(batch.min()) and float(batch.max()) <= 1.0
    assert float(batch.mean()) > 0.99  # 白图 pad 白底 + resize，均值仍接近 1


def test_reverse_tags_thresholds_rating_and_order(model, tmp_path):
    src = _make_image(tmp_path / "white.png", (255, 255, 255))
    # 白图：1girl 0.95 / ArtistName 0.9(≥0.75) / solo 0.85 / long_hair 0.25(<0.35 剔除)
    # / sensitive 0.9（rating 默认剔除）
    assert wd14_tagger.reverse_tags(src) == ["1girl", "ArtistName", "solo"]


def test_predict_can_include_rating(model, tmp_path):
    src = _make_image(tmp_path / "white.png", (255, 255, 255))
    tags = dict(wd14_tagger.predict(src, include_rating=True))
    assert "sensitive" in tags
    assert "long_hair" not in tags  # 低于 general 阈值


def test_predict_sorted_desc(model, tmp_path):
    src = _make_image(tmp_path / "red.png", (255, 0, 0))
    scored = wd14_tagger.predict(src)
    scores = [s for _n, s in scored]
    assert scores == sorted(scores, reverse=True)


def test_release_unloads_session(model, tmp_path):
    src = _make_image(tmp_path / "white.png", (255, 255, 255))
    wd14_tagger.reverse_tags(src)  # 触发加载
    assert wd14_tagger.release() is True
    assert wd14_tagger.release() is False  # 已卸载


def test_missing_model_raises_readable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(wd14_tagger, "MODEL_DIR", tmp_path / "none")
    with pytest.raises(RuntimeError, match="WD14 模型未安装"):
        wd14_tagger._load_session()


# ===== 端点契约（直测路由函数） =====

def _gen_item(**over):
    item = {"id": "gen-1", "repo_id": "repo", "prompt": "p", "description": "",
            "image_url": "https://cdn/x.png", "tags": ["old_tag"],
            "templateName": "", "modelName": "", "loraNames": [],
            "mediaType": "image", "thumbUrl": "", "derivedFrom": [], "created_at": 1}
    item.update(over)
    return item


def test_route_merges_wd14_tags_without_touching_existing(monkeypatch, tmp_path):
    monkeypatch.setattr(wd14_tagger, "model_available", lambda: True)
    monkeypatch.setattr(wd14_tagger, "reverse_tags",
                        lambda path, limit=32: ["1girl", "solo", "old_tag"])
    local = _make_image(tmp_path / "000001.png", (10, 20, 30))
    monkeypatch.setattr(rag_store, "list_generations",
                        lambda repo_id, cfg: [_gen_item(image_url=f"local-view?path={local}")])
    monkeypatch.setattr(rag_store, "set_doc_tags", lambda *a, **k: True)
    from app.services import thumbnail_store
    monkeypatch.setattr(thumbnail_store, "local_path_of_url",
                        lambda url: local if "local-view" in url else None)

    got = rag_router.wd14_tag(rag_router.Wd14TagRequest(id="gen-1", repo_id="repo"))

    assert got["added"] == ["1girl", "solo"]        # 不动原有 old_tag
    assert got["total"] == 3                         # old_tag + 1girl + solo（去重保序）


def test_route_503_when_model_missing(monkeypatch):
    monkeypatch.setattr(wd14_tagger, "model_available", lambda: False)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        rag_router.wd14_tag(rag_router.Wd14TagRequest(id="gen-1", repo_id="repo"))
    assert exc.value.status_code == 503
    assert "wd14-tagger" in exc.value.detail


def test_route_404_when_asset_absent(monkeypatch):
    monkeypatch.setattr(wd14_tagger, "model_available", lambda: True)
    monkeypatch.setattr(rag_store, "list_generations", lambda repo_id, cfg: [])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        rag_router.wd14_tag(rag_router.Wd14TagRequest(id="nope", repo_id="repo"))
    assert exc.value.status_code == 404


def test_route_400_when_not_local_image(monkeypatch):
    monkeypatch.setattr(wd14_tagger, "model_available", lambda: True)
    monkeypatch.setattr(rag_store, "list_generations",
                        lambda repo_id, cfg: [_gen_item(image_url="https://cdn/x.png")])
    from app.services import thumbnail_store
    monkeypatch.setattr(thumbnail_store, "local_path_of_url", lambda url: None)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        rag_router.wd14_tag(rag_router.Wd14TagRequest(id="gen-1", repo_id="repo"))
    assert exc.value.status_code == 400
