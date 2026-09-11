from app.services import visual_asset_index


def test_partial_visual_model_is_never_reported_available(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_asset_index, "MODEL_DIR", tmp_path)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"partial")

    assert visual_asset_index.model_available() is False


class Collection:
    def __init__(self):
        self.payload = None

    def upsert(self, **payload):
        self.payload = payload


def test_visual_index_skips_missing_images_and_stores_generation_identity(monkeypatch, tmp_path):
    image = tmp_path / "one.png"
    image.write_bytes(b"png")
    collection = Collection()
    monkeypatch.setattr(visual_asset_index, "_collection", lambda _repo: collection)
    monkeypatch.setattr(visual_asset_index, "_encode", lambda inputs: [[0.1, 0.2] for _ in inputs])
    url = "http://127.0.0.1/local-view?path=" + str(image)

    result = visual_asset_index.index_items("repo", [
        {"id": "one", "image_url": url, "description": "红裙人物"},
        {"id": "missing", "image_url": "http://remote/image.png"},
    ])

    assert result == {"indexed": 1, "skipped": 1}
    assert collection.payload["ids"] == ["one"]
    assert collection.payload["documents"] == ["红裙人物"]
