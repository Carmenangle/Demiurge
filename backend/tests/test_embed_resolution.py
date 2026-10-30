"""嵌入/对话模型解析：请求配置优先，不完整才退执行器自己的设置。

用户口径（2026-10-30）：「手机默认云端的嵌入和向量模型，如果没有可调用的，就用
电脑那一套」——回退只看配置完整性，不做健康探测；哪一侧都配不好时保持原样，
由调用方按既有语义报错/跳过。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import embed_resolution, repo_meta
from app.services.rag_backend import EmbedConfig

REMOTE_OK = EmbedConfig(
    base_url="https://embed.example.com/v1", api_key="k", embed_model="m")


@pytest.fixture()
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    return tmp_path


def _seed_embed(state_dir: Path, **embed: object) -> None:
    (state_dir / "user_state.json").write_text(json.dumps({
        "settings": {"embedModel": embed},
    }, ensure_ascii=False), encoding="utf-8")


def _seed_chat(state_dir: Path, models: list[dict], active: str = "") -> None:
    (state_dir / "user_state.json").write_text(json.dumps({
        "settings": {"chatModels": models, "activeChatModelId": active},
    }, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("base,model,key,ok", [
    ("", "m", "", False),
    ("https://embed.example.com/v1", "", "", False),
    ("https://embed.example.com/v1", "m", "", False),        # 公网缺 key＝没配完
    ("https://embed.example.com/v1", "m", "k", True),
    ("http://127.0.0.1:11434/v1", "m", "", True),            # 本机服务允许无 key
    ("http://192.168.1.9:8080/v1", "m", "", True),           # 局域网服务允许无 key
    ("localhost:11434/v1", "m", "", True),                   # 无 scheme 也能判
    ("http://lm-studio.local/v1", "m", "", True),
])
def test_端点的可用口径(base: str, model: str, key: str, ok: bool):
    assert embed_resolution.endpoint_usable(base, model, key) is ok


def test_本机嵌入要求模型目录真实存在(tmp_path: Path):
    assert embed_resolution.is_usable(EmbedConfig(mode="local")) is False
    assert embed_resolution.is_usable(
        EmbedConfig(mode="local", model_dir=str(tmp_path / "missing"))) is False
    assert embed_resolution.is_usable(
        EmbedConfig(mode="local", model_dir=str(tmp_path))) is True


def test_settings_embed映射camelCase全字段(state_dir: Path):
    _seed_embed(
        state_dir, mode="local", baseUrl="http://127.0.0.1:11434/v1", apiKey="k",
        modelName="m", modelDir=str(state_dir), rerankerDir=str(state_dir / "rr"),
        proxyUrl="http://proxy:1080",
    )
    cfg = embed_resolution.settings_embed()
    assert cfg.mode == "local"
    assert cfg.base_url.endswith("/v1") and cfg.api_key == "k" and cfg.embed_model == "m"
    assert cfg.model_dir == str(state_dir)
    assert cfg.reranker_dir == str(state_dir / "rr")
    assert cfg.proxy == "http://proxy:1080"


def test_执行器配置不完整时不回退(state_dir: Path):
    _seed_embed(state_dir, mode="remote", baseUrl="https://embed.example.com/v1",
                apiKey="", modelName="m")
    assert embed_resolution.embed_from_settings() is None


def test_resolve_请求可用则原样返回(state_dir: Path):
    _seed_embed(state_dir, baseUrl="https://pc.example.com/v1", apiKey="pc", modelName="pc-m")
    assert embed_resolution.resolve(REMOTE_OK) is REMOTE_OK


def test_resolve_请求不完整退执行器设置(state_dir: Path):
    _seed_embed(state_dir, mode="remote", baseUrl="https://pc.example.com/v1",
                apiKey="pc", modelName="pc-m")
    requested = EmbedConfig(base_url="", api_key="", embed_model="")
    cfg = embed_resolution.resolve(requested)
    assert cfg is not None
    assert cfg.base_url == "https://pc.example.com/v1" and cfg.embed_model == "pc-m"


def test_resolve_两侧都不完整返回None(state_dir: Path):
    _seed_embed(state_dir, mode="remote", baseUrl="", apiKey="", modelName="")
    assert embed_resolution.resolve(EmbedConfig()) is None
    # resolve_or 保持调用方的既有失败语义（原样返回请求配置）
    requested = EmbedConfig(base_url="", embed_model="m")
    assert embed_resolution.resolve_or(requested) is requested


def test_resolve_只配了本机嵌入目录也能回退(state_dir: Path):
    _seed_embed(state_dir, mode="local", modelDir=str(state_dir))
    cfg = embed_resolution.resolve(EmbedConfig(base_url="", embed_model="m"))
    assert cfg is not None and cfg.mode == "local" and cfg.model_dir == str(state_dir)


def test_chat_from_settings_选中的优先并可回落第一个(state_dir: Path):
    _seed_chat(state_dir, [
        {"id": "a", "baseUrl": "https://a.example.com/v1", "apiKey": "ka", "modelName": "ma"},
        {"id": "b", "baseUrl": "https://b.example.com/v1", "apiKey": "kb", "modelName": "mb"},
    ], active="b")
    assert embed_resolution.chat_from_settings() == (
        "https://b.example.com/v1", "kb", "mb")
    _seed_chat(state_dir, [
        {"id": "a", "baseUrl": "https://a.example.com/v1", "apiKey": "ka", "modelName": "ma"},
    ])
    assert embed_resolution.chat_from_settings() == (
        "https://a.example.com/v1", "ka", "ma")


def test_chat_from_settings_本地模型补本机Ollama(state_dir: Path):
    _seed_chat(state_dir, [
        {"id": "l", "source": "local", "apiKey": "", "baseUrl": "", "modelName": "",
         "ollamaName": "qwen-local"},
    ], active="l")
    assert embed_resolution.chat_from_settings() == (
        "http://localhost:11434/v1", "ollama", "qwen-local")


def test_chat_from_settings_公网缺key不可用(state_dir: Path):
    _seed_chat(state_dir, [
        {"id": "c", "baseUrl": "https://chat.example.com/v1", "apiKey": "", "modelName": "m"},
    ])
    assert embed_resolution.chat_from_settings() is None


def test_resolve_chat_请求可用优先_否则退执行器(state_dir: Path):
    _seed_chat(state_dir, [
        {"id": "pc", "baseUrl": "https://pc.example.com/v1", "apiKey": "pc",
         "modelName": "pc-m"},
    ])
    assert embed_resolution.resolve_chat(
        "https://phone.example.com/v1", "phone", "phone-m") == (
            "https://phone.example.com/v1", "phone", "phone-m")
    assert embed_resolution.resolve_chat("", "", "") == (
        "https://pc.example.com/v1", "pc", "pc-m")


def test_resolve_chat_都没有时原样返回(state_dir: Path):
    _seed_chat(state_dir, [])
    assert embed_resolution.resolve_chat("", "", "") == ("", "", "")
