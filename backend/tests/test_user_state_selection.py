"""user-state.selection：把「上次选中的仓库/作品」记到后端，跨 WebView 数据目录恢复。

两条判据：① 带 selection 的整体写读得回来；② 旧请求（请求体没有 selection 字段）
覆盖写不得抹掉已记住的选择——启动早期的 repos/settings 合并上传就是这种请求。
读侧已收口 repo_meta（2026-10-24 审计中期项）：注入接缝随属主改钉
`repo_meta.DATA_DIR`（与 test_user_state_ownership 同款，绝不触碰真实 user_state.json）。
"""
import json

from app.routers import user_state
from app.services import repo_meta


def test_selection_roundtrip_and_preserved_on_legacy_write(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    selection = {"repoId": "repo-1", "workId": "work-1", "savedAt": 1760000000000}

    user_state.set_state(user_state.UserState(repos=[], settings={}, selection=selection))

    assert user_state.get_state().selection == selection
    saved = json.loads((tmp_path / "user_state.json").read_text(encoding="utf-8"))
    assert saved["selection"] == selection

    # 不带 selection 的覆盖写（旧前端 / 启动早期合并上传）⇒ 保留盘上值
    user_state.set_state(user_state.UserState(
        repos=[{"id": "repo-1"}], settings={"theme": "liquid-glass"},
    ))
    state = user_state.get_state()
    assert state.selection == selection
    assert state.repos == [{"id": "repo-1"}]
    assert state.settings == {"theme": "liquid-glass"}

    # 显式传 null 才是清空
    user_state.set_state(user_state.UserState(repos=[], settings={}, selection=None))
    assert user_state.get_state().selection is None
