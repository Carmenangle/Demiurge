"""可插拔打标注册表：内置清单 / 回落默认 / 丢模型清单 / 分派参数。"""
from __future__ import annotations

import json
from pathlib import Path

from app.services import tagger_registry as reg


def test_list_builtin_contains_wd_family():
    ids = {t["id"] for t in reg.list_taggers()}
    assert {"wd-swinv2-v3", "wd-eva02-large-v3", "wd-vit-large-v3", "wd-convnext-v3"} <= ids


def test_resolve_falls_back_to_default():
    assert reg.resolve("").id == reg.DEFAULT_ID
    assert reg.resolve("nope").id == reg.DEFAULT_ID
    assert reg.resolve("wd-eva02-large-v3").id == "wd-eva02-large-v3"


def test_dropin_manifest_is_picked_up(tmp_path, monkeypatch):
    """丢一个目录 + model.onnx + selected_tags.csv + tagger.json ⇒ 出现在列表且可解析参数。"""
    tdir = tmp_path / "taggers"
    d = tdir / "my-tagger"
    d.mkdir(parents=True)
    (d / "model.onnx").write_bytes(b"x" * 100)
    (d / "selected_tags.csv").write_bytes(b"tag_id,name,category\n1,a,0\n")
    (d / "tagger.json").write_text(
        json.dumps({"id": "my-tagger", "name": "My", "thresholds": {"0": 0.4}}), encoding="utf-8")
    monkeypatch.setattr(reg, "TAGGERS_DIR", tdir)

    items = {t["id"]: t for t in reg.list_taggers()}
    assert "my-tagger" in items
    assert items["my-tagger"]["available"] is True
    assert items["my-tagger"]["source"] == "dropin"
    assert reg.resolve("my-tagger").thresholds[0] == 0.4


def test_predict_dispatches_spec_params(monkeypatch):
    calls: dict = {}

    def fake_predict(image_path, **kw):  # noqa: ARG001
        calls.update(kw)
        return [("a", 0.9)]

    monkeypatch.setattr(reg.wd14_tagger, "predict", fake_predict)
    out = reg.predict("wd-eva02-large-v3", Path("x.png"), limit=5)
    spec = reg.resolve("wd-eva02-large-v3")
    assert calls["model_dir"] == spec.dir
    assert calls["input_size"] == spec.input_size
    assert out == [("a", 0.9)]
