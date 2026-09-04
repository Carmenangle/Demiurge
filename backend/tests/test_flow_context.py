# -*- coding: utf-8 -*-
"""current_flow_doc（固化链续跑 §3 设计 A）服务与接线测试。"""
from __future__ import annotations

import json

import pytest

from app import config as app_config
from app.services import capability_handlers, flow_context


@pytest.fixture(autouse=True)
def _flow_root(tmp_path, monkeypatch):
    """flow_docs 与 doc.create_repo 落盘都隔离到 tmp_path，不碰真实运行态。"""
    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    return tmp_path


def _mark(repo: str, path: str = "/repo/x/docs/a.md", **kw):
    base = {"kind": "doc_repo", "path": path, "step": "doc.create_repo"}
    base.update(kw)
    return flow_context.mark_doc(repo, **base)


def test_mark_current_returns_latest_and_keeps_order():
    _mark("t1", path="/w/docs/服装参考.md")
    _mark("t1", path="/w/docs/出图清单-角色A.md")
    cur = flow_context.current_doc("t1")
    assert cur["path"].endswith("出图清单-角色A.md")
    state = json.loads((_flow_root_impl() / "t1.json").read_text(encoding="utf-8"))
    assert len(state) == 2
    assert state[0]["path"].endswith("服装参考.md")


def _flow_root_impl():
    return flow_context._root()


def test_max_docs_trim_and_same_path_update():
    for i in range(8):
        _mark("t2", path=f"/w/docs/p{i}.md", step=f"doc.create_repo#{i}")
    state = json.loads((_flow_root_impl() / "t2.json").read_text(encoding="utf-8"))
    assert len(state) == flow_context.MAX_DOCS == 6
    assert state[-1]["path"].endswith("p7.md")
    # 同路径重复登记 = 更新不追加
    _mark("t2", path="/w/docs/p5.md", step="doc.create_repo#updated")
    state = json.loads((_flow_root_impl() / "t2.json").read_text(encoding="utf-8"))
    assert len(state) == 6
    assert sum(1 for d in state if d["path"].endswith("p5.md")) == 1
    assert state[-1]["step"] == "doc.create_repo#updated"


def test_invalid_repo_id_rejected():
    for bad in ("", "a/b", "..", "a\\b", "c:1", ".hidden"):
        with pytest.raises(ValueError):
            _mark(bad)
    # 读侧非法按无处理，不抛
    assert flow_context.current_doc("x/../y") is None
    assert flow_context.resume_hint("", "继续出图") == ""


def test_clear():
    _mark("t3")
    assert flow_context.current_doc("t3") is not None
    flow_context.clear("t3")
    assert flow_context.current_doc("t3") is None


def test_resume_hint_gating():
    _mark("t4", path="/w/docs/出图清单-角色A.md", step="doc.create_repo")
    # 延续语 + 有句柄 → 注入句柄文本
    hint = flow_context.resume_hint("t4", "接着按刚才的方案出图")
    assert "出图清单-角色A.md" in hint and "doc.create_repo" in hint
    # 非延续语 → 不注入
    assert flow_context.resume_hint("t4", "帮我画一张新的场景图") == ""
    assert flow_context.resume_hint("t4", "") == ""
    # 无句柄 → 不注入
    assert flow_context.resume_hint("nobody", "接着按刚才的方案出图") == ""


def test_handler_registers_only_with_repo_id(tmp_path):
    base = tmp_path / "work"
    base.mkdir()
    capability_handlers.create_repo_doc(
        str(base), "出图清单-角色A.md", "# 出图清单\n1 张", repo_id="t9")
    target = base / "docs" / "出图清单-角色A.md"
    assert target.is_file()
    cur = flow_context.current_doc("t9")
    assert cur is not None and cur["kind"] == "doc_repo"
    assert cur["step"] == "doc.create_repo" and cur["path"] == str(target)
    # 不带 repo_id（旧式直调）→ 不登记、不抛错
    capability_handlers.create_repo_doc(str(base), "b.md", "# B")
    assert flow_context.current_doc("t9")["path"].endswith("出图清单-角色A.md")
    # 非法 rel_path 在登记前被拒
    with pytest.raises(ValueError):
        capability_handlers.create_repo_doc(str(base), "../evil.md", "# x", repo_id="t9")
    with pytest.raises(ValueError):
        capability_handlers.create_repo_doc(str(base), "not-md.txt", "# x", repo_id="t9")
