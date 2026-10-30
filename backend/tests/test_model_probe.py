from types import SimpleNamespace

from app.services import model_probe


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_remote_probe_只读取models不调用生成(monkeypatch):
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return _Response(payload={"data": [{"id": "chat-test"}]})

    monkeypatch.setattr(model_probe.httpx, "Client", Client)
    result = model_probe.probe_remote("chat", "https://example.test/v1", "secret", "chat-test")

    assert result["status"] == "success"
    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/models")


def test_remote_probe模型目录不存在时只报告警告(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            return _Response(status_code=404)

    monkeypatch.setattr(model_probe.httpx, "Client", Client)
    result = model_probe.probe_remote("image", "https://example.test/v1", "secret", "image-test")

    assert result["status"] == "warning"
    assert result["billable"] is False
    assert "未调用生成" in result["message"]


def test_local_embedding_probe执行最小本地推理(monkeypatch, tmp_path):
    monkeypatch.setattr(model_probe.rag_backend, "embed_query", lambda cfg, text: [0.1, 0.2, 0.3])
    (tmp_path / "modules.json").write_text("[]", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"weights")

    result = model_probe.probe_local_embedding(str(tmp_path))

    assert result["status"] == "success"
    assert "3" in result["message"]


def test_local_reranker_probe先报告缺失权重(tmp_path):
    result = model_probe.probe_local_reranker(str(tmp_path))

    assert result["status"] == "error"
    assert "缺少文件" in result["message"]


def _fake_gguf(path):
    return SimpleNamespace(
        kind="model", architecture="qwen3", parameters_b=8,
        quant="Q4_K_M", is_vision=False, has_vision_encoder=False,
    )


def _stub_gguf(monkeypatch, meta=None):
    from app.services import gguf_importer

    monkeypatch.setattr(gguf_importer, "parse_gguf", lambda path: meta or _fake_gguf(path))
    monkeypatch.setattr(gguf_importer, "fit_hardware", lambda _meta: {
        "level": "ok", "total_needed_mib": 6144, "device": {"available_mib": 8192},
    })


def test_对话本地模型接受无视觉能力的GGUF(monkeypatch, tmp_path):
    model = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    model.write_bytes(b"GGUF")
    _stub_gguf(monkeypatch)

    result = model_probe.probe_local_chat(str(model))

    assert result["status"] == "success"
    assert "可用于对话推理" in result["message"]


def test_视觉本地模型仍要求视觉能力(monkeypatch, tmp_path):
    model = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    model.write_bytes(b"GGUF")
    _stub_gguf(monkeypatch)

    result = model_probe.probe_local_vlm(str(model))

    assert result["status"] == "warning"
    assert "未检测到视觉能力" in result["message"]


def test_本地对话模型文件缺失时报对话模型(tmp_path):
    result = model_probe.probe_local_chat(str(tmp_path / "missing.gguf"))

    assert result["status"] == "error"
    assert "对话模型" in result["message"]


def test_路由把chat_local分派到对话模型检测(monkeypatch):
    from app.routers import ai_model_probe

    monkeypatch.setattr(
        model_probe, "probe_local_chat",
        lambda path: {"status": "success", "message": path, "billable": False},
    )
    result = ai_model_probe.probe_model(
        ai_model_probe.ModelProbeRequest(kind="chat-local", gguf_path="chat.gguf"),
    )

    assert result["message"] == "chat.gguf"
