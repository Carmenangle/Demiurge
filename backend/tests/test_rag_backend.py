from app.services import rag_backend


def test_reranker_path_does_not_split_embedding_cache():
    first = rag_backend.EmbedConfig(
        base_url="http://cache-test", embed_model="embedding-model", reranker_dir="D:/reranker-a",
    )
    second = rag_backend.EmbedConfig(
        base_url="http://cache-test", embed_model="embedding-model", reranker_dir="D:/reranker-b",
    )

    assert rag_backend.embedding_key(first) == rag_backend.embedding_key(second)
    assert rag_backend.embeddings(first) is rag_backend.embeddings(second)


def test_embedding_mode_explicitly_selects_adapter():
    remote = rag_backend.EmbedConfig(
        base_url="http://remote", embed_model="model", model_dir="D:/ignored",
        mode="remote",
    )
    local = rag_backend.EmbedConfig(
        base_url="http://ignored", embed_model="ignored", model_dir="D:/local",
        mode="local",
    )

    assert rag_backend.embedding_key(remote) == (
        "remote", "http://remote", "", "model",
    )
    assert rag_backend.embedding_key(local) == ("local", "D:/local")


def test_loopback_embedding_keeps_ollama_model_warm():
    local = rag_backend._RemoteEmbeddings(rag_backend.EmbedConfig(
        base_url="http://localhost:11434/v1", embed_model="qwen3-embedding",
    ))
    remote = rag_backend._RemoteEmbeddings(rag_backend.EmbedConfig(
        base_url="https://embedding.example/v1", embed_model="embedding-model",
    ))

    assert local._keep_alive is True
    assert remote._keep_alive is False
