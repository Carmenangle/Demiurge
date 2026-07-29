from pathlib import Path

from app.services import rag_backend


def test_embed_config_only_uses_user_configured_reranker_path(
    monkeypatch, tmp_path: Path
):
    bundled = tmp_path / "bundled"
    monkeypatch.setenv("LAF_BUNDLED_RERANKER_DIR", str(bundled))

    assert rag_backend.EmbedConfig().reranker_dir == ""
    assert rag_backend.EmbedConfig(reranker_dir="D:/custom").reranker_dir == "D:/custom"
