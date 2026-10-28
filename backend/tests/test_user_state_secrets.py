"""user-state 写侧密钥护栏：空密钥不覆盖盘上非空值。

背景（2026-10-28 真机事故）：`POST /api/user-state` 是整对象覆盖写，而客户端
localStorage 副本被 `stripSecrets` 清空了密钥；某次启动回填失败后 `hydrated`
一置真就把空副本整份推回到后端，把盘上真 key 抹成空串——实测 8 个对话模型 +
embed + 各 token 全空，之后每轮生成都死在 401，工作台一路收不到插画请求。

判据：① 空/缺省不清空；② 非空照常覆盖（换 key 生效）；
③ 新 id 不继承别人的 key；④ 删掉的模型条目不复活。
注入接缝同 test_user_state_selection：钉 `repo_meta.DATA_DIR`，绝不碰真实存档。
"""
import json

from app.routers import user_state
from app.services import repo_meta


def _seed(tmp_path, monkeypatch, settings: dict) -> None:
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    user_state.set_state(user_state.UserState(repos=[], settings=settings))


def _saved(tmp_path) -> dict:
    return json.loads((tmp_path / "user_state.json").read_text(encoding="utf-8"))["settings"]


def _full_settings() -> dict:
    return {
        "theme": "night",
        "chatModels": [
            {"id": "chat-1", "modelName": "deepseek-flash", "baseUrl": "https://api.deepseek.com", "apiKey": "sk-chat"},
        ],
        "imageModels": [
            {"id": "img-1", "modelName": "gpt-image-2", "baseUrl": "https://ai.example.com/v1", "apiKey": "sk-img"},
        ],
        "videoModels": [
            {"id": "vid-1", "modelName": "video-x", "baseUrl": "https://v.example.com/v1", "apiKey": "sk-vid"},
        ],
        "embedModel": {"modelName": "qwen3", "baseUrl": "http://localhost:11434/v1", "apiKey": "sk-embed"},
        "vlmModel": {"modelName": "qwen-vl", "baseUrl": "https://vlm.example.com/v1", "apiKey": "sk-vlm"},
        "hfToken": "hf-token",
        "civitaiToken": "civitai-token",
        "smitheryKey": "smithery-key",
        "pixivRefreshToken": "pixiv-token",
        "danbooruLogin": "user",
        "danbooruApiKey": "dbb-key",
    }


def _keyless_copy(settings: dict) -> dict:
    """模拟前端 stripSecrets 后的副本：模型数组与 token 全空，其余字段照旧。"""
    out = json.loads(json.dumps(settings))
    for name in ("chatModels", "imageModels", "videoModels"):
        for model in out.get(name) or []:
            model["apiKey"] = ""
    for name in ("embedModel", "vlmModel"):
        out[name]["apiKey"] = ""
    for name in ("hfToken", "civitaiToken", "smitheryKey", "pixivRefreshToken", "danbooruLogin", "danbooruApiKey"):
        out[name] = ""
    return out


def test_keyless_copy_does_not_wipe_stored_secrets(tmp_path, monkeypatch):
    stored = _full_settings()
    _seed(tmp_path, monkeypatch, stored)

    user_state.set_state(user_state.UserState(repos=[], settings=_keyless_copy(stored)))

    saved = _saved(tmp_path)
    assert saved["theme"] == "night"  # 非密钥字段照常被覆盖写
    assert saved["chatModels"][0]["apiKey"] == "sk-chat"
    assert saved["imageModels"][0]["apiKey"] == "sk-img"
    assert saved["videoModels"][0]["apiKey"] == "sk-vid"
    assert saved["embedModel"]["apiKey"] == "sk-embed"
    assert saved["vlmModel"]["apiKey"] == "sk-vlm"
    assert saved["hfToken"] == "hf-token"
    assert saved["civitaiToken"] == "civitai-token"
    assert saved["smitheryKey"] == "smithery-key"
    assert saved["pixivRefreshToken"] == "pixiv-token"
    assert saved["danbooruLogin"] == "user"
    assert saved["danbooruApiKey"] == "dbb-key"


def test_nonempty_secret_still_overwrites(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _full_settings())
    next_settings = _keyless_copy(_full_settings())
    next_settings["chatModels"][0]["apiKey"] = "sk-chat-v2"
    next_settings["hfToken"] = "hf-v2"

    user_state.set_state(user_state.UserState(repos=[], settings=next_settings))

    saved = _saved(tmp_path)
    assert saved["chatModels"][0]["apiKey"] == "sk-chat-v2"
    assert saved["hfToken"] == "hf-v2"
    assert saved["imageModels"][0]["apiKey"] == "sk-img"  # 未提供的仍保留


def test_new_model_id_does_not_inherit_key(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _full_settings())
    next_settings = _keyless_copy(_full_settings())
    next_settings["chatModels"].append(
        {"id": "chat-2", "modelName": "glm-5.3", "baseUrl": "https://t.example.com/v1", "apiKey": ""}
    )

    user_state.set_state(user_state.UserState(repos=[], settings=next_settings))

    saved = _saved(tmp_path)
    assert [m["id"] for m in saved["chatModels"]] == ["chat-1", "chat-2"]
    assert saved["chatModels"][1]["apiKey"] == ""


def test_removed_model_is_not_resurrected(tmp_path, monkeypatch):
    stored = _full_settings()
    stored["chatModels"].append(
        {"id": "chat-2", "modelName": "glm-5.3", "baseUrl": "https://t.example.com/v1", "apiKey": "sk-glm"}
    )
    _seed(tmp_path, monkeypatch, stored)
    next_settings = _keyless_copy(stored)
    next_settings["chatModels"] = next_settings["chatModels"][:1]  # 用户在设置里删掉第二条

    user_state.set_state(user_state.UserState(repos=[], settings=next_settings))

    saved = _saved(tmp_path)
    assert [m["id"] for m in saved["chatModels"]] == ["chat-1"]
    assert saved["chatModels"][0]["apiKey"] == "sk-chat"


def test_null_api_key_counts_as_empty(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _full_settings())
    next_settings = _keyless_copy(_full_settings())
    next_settings["embedModel"]["apiKey"] = None

    user_state.set_state(user_state.UserState(repos=[], settings=next_settings))

    assert _saved(tmp_path)["embedModel"]["apiKey"] == "sk-embed"


def test_absent_settings_keeps_stored_settings(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _full_settings())

    user_state.set_state(user_state.UserState(repos=[{"id": "repo-1"}], settings=None))

    state = user_state.get_state()
    assert state.repos == [{"id": "repo-1"}]
    assert state.settings["chatModels"][0]["apiKey"] == "sk-chat"

def test_absent_secret_field_keeps_stored_key(tmp_path, monkeypatch):
    """旧 APK / 旧前端的 Settings 没有这个字段：缺席不是删除（整对象语义下的例外）。"""
    _seed(tmp_path, monkeypatch, _full_settings())
    next_settings = _keyless_copy(_full_settings())
    for model in next_settings["chatModels"]:
        del model["apiKey"]
    del next_settings["hfToken"]
    del next_settings["danbooruApiKey"]

    user_state.set_state(user_state.UserState(repos=[], settings=next_settings))

    saved = _saved(tmp_path)
    assert saved["chatModels"][0]["apiKey"] == "sk-chat"
    assert saved["hfToken"] == "hf-token"
    assert saved["danbooruApiKey"] == "dbb-key"
