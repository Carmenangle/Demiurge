"""user-state 写侧的「模型字段缺席≠删除」：联机手机的回写不得抹掉电脑自己的模型。

联机同库时手机把设置回写电脑，但模型字段（对话/生图/视频/嵌入/视觉 + 选中 id）只存
手机本机、随请求走（见 services/mobile_local_models）。前端回写电脑时会把这几档**摘掉**，
整对象覆盖写若不补齐就会把电脑那份模型配置清空。语义与 selection 一致：缺席保留，
显式带上（哪怕是空数组）照常覆盖。
"""
import json

from app.routers import user_state
from app.services import repo_meta

MODEL_KEYS = (
    "chatModels", "activeChatModelId",
    "imageModels", "activeImageModelId",
    "videoModels", "activeVideoModelId",
    "embedModel", "vlmModel",
)


def _seed(tmp_path, monkeypatch, settings: dict) -> None:
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    user_state.set_state(user_state.UserState(repos=[], settings=settings))


def _saved(tmp_path) -> dict:
    return json.loads((tmp_path / "user_state.json").read_text(encoding="utf-8"))["settings"]


def _with_models() -> dict:
    return {
        "theme": "night",
        "chatModels": [{"id": "pc-chat", "modelName": "pc-model", "baseUrl": "https://pc.example/v1", "apiKey": "sk-pc"}],
        "activeChatModelId": "pc-chat",
        "imageModels": [{"id": "pc-img", "modelName": "pc-image", "baseUrl": "https://pc.example/v1", "apiKey": "sk-img"}],
        "activeImageModelId": "pc-img",
        "videoModels": [],
        "embedModel": {"mode": "remote", "baseUrl": "https://pc.example/v1", "modelName": "pc-embed", "apiKey": "sk-embed"},
        "vlmModel": {"mode": "remote", "baseUrl": "https://pc.example/v1", "modelName": "pc-vlm", "apiKey": "sk-vlm"},
    }


def test_absent_model_fields_keep_stored_values(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _with_models())
    phone_settings = {k: v for k, v in _with_models().items() if k not in MODEL_KEYS}
    phone_settings["theme"] = "bright"

    user_state.set_state(user_state.UserState(repos=[{"id": "phone-repo"}], settings=phone_settings))

    saved = _saved(tmp_path)
    assert saved["theme"] == "bright"  # 非模型字段照常覆盖写
    assert saved["chatModels"][0]["modelName"] == "pc-model"
    assert saved["chatModels"][0]["apiKey"] == "sk-pc"
    assert saved["activeChatModelId"] == "pc-chat"
    assert saved["imageModels"][0]["modelName"] == "pc-image"
    assert saved["activeImageModelId"] == "pc-img"
    assert saved["embedModel"]["modelName"] == "pc-embed"
    assert saved["vlmModel"]["modelName"] == "pc-vlm"


def test_explicit_model_fields_still_overwrite(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _with_models())
    next_settings = _with_models()
    next_settings["chatModels"] = []
    del next_settings["activeChatModelId"]

    user_state.set_state(user_state.UserState(repos=[], settings=next_settings))

    saved = _saved(tmp_path)
    assert saved["chatModels"] == []  # 显式空数组＝用户清空，不复活
    assert saved["activeChatModelId"] == "pc-chat"  # 缺省 id 仍按缺席保留
    assert saved["imageModels"][0]["modelName"] == "pc-image"


def test_absent_settings_keeps_models_too(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _with_models())

    user_state.set_state(user_state.UserState(repos=[{"id": "r"}], settings=None))

    saved = _saved(tmp_path)
    assert saved["chatModels"][0]["modelName"] == "pc-model"
