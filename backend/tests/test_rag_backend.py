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
        "remote", "http://remote", "", "model", "",
    )
    assert rag_backend.embedding_key(local) == ("local", "D:/local")


def test_remote_proxy_splits_embedding_cache_key():
    direct = rag_backend.EmbedConfig(
        base_url="http://remote", embed_model="model", mode="remote",
    )
    proxied = rag_backend.EmbedConfig(
        base_url="http://remote", embed_model="model", mode="remote",
        proxy="http://proxy",
    )

    assert rag_backend.embedding_key(direct) != rag_backend.embedding_key(proxied)


def test_loopback_embedding_keeps_ollama_model_warm():
    local = rag_backend._RemoteEmbeddings(rag_backend.EmbedConfig(
        base_url="http://localhost:11434/v1", embed_model="qwen3-embedding",
    ))
    remote = rag_backend._RemoteEmbeddings(rag_backend.EmbedConfig(
        base_url="https://embedding.example/v1", embed_model="embedding-model",
    ))

    assert local._keep_alive is True
    assert remote._keep_alive is False


def test_loopback_embedding_ignores_explicit_proxy(monkeypatch):
    client_kwargs = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [1.0]}]}

    class Client:
        def __init__(self, **kwargs):
            client_kwargs.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(rag_backend.httpx, "Client", Client)
    embedding = rag_backend._RemoteEmbeddings(rag_backend.EmbedConfig(
        base_url="http://localhost:11434/v1",
        embed_model="qwen3-embedding:latest",
        proxy="http://127.0.0.1:7897",
    ))

    assert embedding.embed_query("test") == [1.0]
    assert "proxy" not in client_kwargs


def test_remote_embedding_bounds_each_text_and_splits_large_batches(monkeypatch):
    payloads = []

    class Response:
        status_code = 200

        def __init__(self, texts):
            self.texts = texts

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [
                {"index": index, "embedding": [float(len(text))]}
                for index, text in enumerate(self.texts)
            ]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, *, headers, json):
            assert headers
            texts = list(json["input"])
            payloads.append(texts)
            return Response(texts)

    monkeypatch.setattr(rag_backend.httpx, "Client", Client)
    embedding = rag_backend._RemoteEmbeddings(rag_backend.EmbedConfig(
        base_url="http://localhost:11434/v1", embed_model="qwen3-embedding:latest",
    ))
    texts = ["x" * 10_000, *["y" * 3_000 for _ in range(9)]]

    vectors = embedding.embed_documents(texts)

    assert len(vectors) == len(texts)
    assert len(payloads) > 1
    assert all(len(text) <= 2_000 for batch in payloads for text in batch)
    assert all(len(batch) <= 1 for batch in payloads)
    assert all(sum(map(len, batch)) <= 2_000 for batch in payloads)
