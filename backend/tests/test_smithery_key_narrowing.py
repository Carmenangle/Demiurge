"""P4 第二批特征测试：Smithery Key 配置读取的异常捕获面收窄。

审计正本：docs/memory/except-exception-audit-2026-09-16.md §4 第二批。
`mcp._smithery_key` / `skills._skey` 读 `user_state.json` 的全局 Key，失败降级 `""`；
收窄后捕获面 = `(OSError, ValueError, AttributeError)`
（OSError=读盘故障；ValueError ⊃ JSONDecodeError/UnicodeDecodeError=坏 JSON/坏编码；
AttributeError=settings 非 dict）—— **其它异常（代码 bug / 环境异常）必须外抛**，不得静默吞掉。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.routers import mcp, skills

READERS = (mcp._smithery_key, skills._skey)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    # ⚠ 两函数在调用时才 `from app.config import DATA_DIR`，钉 config.DATA_DIR 即可，
    # 绝不触碰真实 user_state.json（P8 曾覆盖用户真实资产库的教训）。
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
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
