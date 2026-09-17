"""P4 第二批特征测试：Smithery Key 配置读取的异常捕获面收窄。

审计正本：docs/memory/except-exception-audit-2026-09-16.md §4 第二批。
`mcp._smithery_key` / `skills._skey` 读 `user_state.json` 的全局 Key，失败降级 `""`。
2026-09-17 T6.3：两壳读法收敛到属主 `repo_meta.setting_str_from_state`
（`_load_state` 吞 OSError/JSONDecodeError/UnicodeDecodeError/TypeError，
其它异常外抛——契约不变）；patch 接缝随属主改钉 `repo_meta.DATA_DIR`
（monkeypatch 判据=谁在调用期查这个名字）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.routers import mcp, skills
from app.services import repo_meta

READERS = (mcp._smithery_key, skills._skey)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    # ⚠ 属主 `_load_state` 调用期查 `repo_meta.DATA_DIR`（模块级绑定）——
    # 绝不触碰真实 user_state.json（P8 曾覆盖用户真实资产库的教训）。
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize("reader", READERS, ids=lambda r: r.__module__)
def test_valid_key(reader, data_dir):
    (data_dir / "user_state.json").write_text(
        '{"settings": {"smitheryKey": "abc"}}', encoding="utf-8")
    assert reader() == "abc"


@pytest.mark.parametrize("reader", READERS, ids=lambda r: r.__module__)
@pytest.mark.parametrize("content", [
    '{"settings": {}}',                        # 缺 key → ""
    '{}',                                      # 缺 settings → ""
    '{"settings": "not-a-dict"}',              # settings 非 dict → AttributeError 捕获 → ""
    '{oops',                                   # 坏 JSON → JSONDecodeError 捕获 → ""
])
def test_degradation_returns_empty(reader, data_dir, content):
    (data_dir / "user_state.json").write_text(content, encoding="utf-8")
    assert reader() == ""


@pytest.mark.parametrize("reader", READERS, ids=lambda r: r.__module__)
def test_missing_file_returns_empty(reader, data_dir):
    assert reader() == ""


@pytest.mark.parametrize("reader", READERS, ids=lambda r: r.__module__)
def test_unexpected_error_propagates(reader, data_dir, monkeypatch):
    # 收窄的核心契约：非（读盘/解析/形状）类异常必须外抛，不得吞。
    (data_dir / "user_state.json").write_text('{"settings": {}}', encoding="utf-8")

    def _boom(self, *a, **k):
        raise RuntimeError("非解析类故障")

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(RuntimeError):
        reader()


# ===== T6.3 反向源码锁（2026-09-17）：smithery 读法收敛后防回退内联 =====
# P8 先例：行为网抓不到属主漂移，只有源码形态锁抓得住。

def _src(rel: str) -> str:
    from app.services import repo_meta as _rm
    root = Path(_rm.__file__).resolve().parents[2]
    return (root / rel).read_text(encoding="utf-8")


def test_mcp_skills_无内联自读():
    """内联自读特征 = `json.loads` 或 `DATA_DIR /`（收敛前两处各有）；壳 docstring
    提到 user_state.json 属合法叙述，不锁字面串。"""
    for rel in ("app/routers/mcp.py", "app/routers/skills.py"):
        code = _src(rel)
        assert "json.loads" not in code, f"{rel} 回退内联解析（T6.3 已收敛到属主）"
        assert "DATA_DIR /" not in code, f"{rel} 回退内联读 user_state 文件"
        assert "setting_str_from_state" in code, f"{rel} 未经属主读法"


def test_capability_handlers_无目录自读残留():
    """`_dir_from_state` 已删（T6.3）；characterDir/worldbookDir 只走
    `repo_meta.setting_dir_from_state`。全文件 `user_state.json` 仅允许
    `_embed_config_from_state` 一处（读嵌套 embedModel 对象，语义独立）。
    词边界 regex：`\\b_dir_from_state\\b` 不会误中 `setting_dir_from_state`
    （内部 'g'→'_' 均为 word 字符，无边界的判据依据）。"""
    import re
    src = _src("app/services/capability_handlers.py")
    assert re.search(r"\b_dir_from_state\b", src) is None, "已删除的目录自读不得复活"
    assert src.count("user_state.json") == 1, (
        "capability_handlers 只允许 _embed_config_from_state 一处自读（embedModel 嵌套对象）")
