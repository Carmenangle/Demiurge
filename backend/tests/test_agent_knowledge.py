"""固化知识库只读服务 + /api/agents/knowledge 端点（薄直测）。

覆盖：目录缺失/空、文件名升序元数据、正文读取与截断、路径穿越/非法名拒绝、
HTTP 状态映射，以及 agent_graph 注入文本与上限的兼容回归。
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from app.config import DATA_DIR as _DATA_DIR  # noqa: F401  仅确认模块可导入
from app.routers import agents as agents_router
from app.services import agent_graph, agent_knowledge


@pytest.fixture()
def know_dir(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    root = tmp_path / agent_knowledge.KNOWLEDGE_DIR_NAME
    root.mkdir()
    return root


def _write(root, name: str, text: str, mtime: float | None = None):
    path = root / name
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# ── 服务层：list_docs ─────────────────────────────────────────────────────────


def test_list_docs_目录缺失或为空(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)  # 无 agent_knowledge 目录
    assert agent_knowledge.list_docs() == []

    root = tmp_path / agent_knowledge.KNOWLEDGE_DIR_NAME
    root.mkdir()
    assert agent_knowledge.list_docs() == []


def test_list_docs_文件名升序与元数据(know_dir):
    _write(know_dir, "b流程.md", "# B", mtime=2000)
    _write(know_dir, "A规范.md", "# A", mtime=1000)
    docs = agent_knowledge.list_docs()
    assert [d["name"] for d in docs] == ["A规范", "b流程"]  # 文件名升序（注入顺序）
    assert docs[0]["file"] == "A规范.md"
    assert docs[0]["size"] == len("# A".encode("utf-8"))
    assert docs[0]["mtime"] == 1000 * 1000
    assert docs[0]["truncated"] is False


def test_list_docs_超长标truncated(know_dir):
    _write(know_dir, "大.md", "x" * (agent_knowledge.KNOWLEDGE_PER_FILE_CHARS + 10))
    assert agent_knowledge.list_docs()[0]["truncated"] is True


def test_truncated按字符不按字节(know_dir):
    # 中文 3 字节/字符：文件字节数可远超上限，但字符数未超 → 不截断
    body = "好" * 15000  # ≈45KB 字节，但仅 15000 字符 < 20000 上限
    _write(know_dir, "中文规范.md", body)
    assert agent_knowledge.list_docs()[0]["truncated"] is False
    doc = agent_knowledge.read_doc("中文规范")
    assert doc["truncated"] is False and len(doc["content"]) == 15000


# ── 服务层：read_doc ──────────────────────────────────────────────────────────


def test_read_doc_按主名与完整名读取(know_dir):
    _write(know_dir, "规范.md", "# 标题\n正文")
    for name in ("规范", "规范.md"):
        doc = agent_knowledge.read_doc(name)
        assert doc["name"] == "规范" and doc["file"] == "规范.md"
        assert doc["content"] == "# 标题\n正文"


def test_read_doc_截断到上限(know_dir):
    body = "a" * agent_knowledge.KNOWLEDGE_PER_FILE_CHARS
    _write(know_dir, "长文档.md", body + "尾巴")
    doc = agent_knowledge.read_doc("长文档")
    assert len(doc["content"]) == agent_knowledge.KNOWLEDGE_PER_FILE_CHARS
    assert doc["content"].endswith("a")
    assert doc["truncated"] is True


@pytest.mark.parametrize("name", ["../secret", "..", "/etc/passwd", "a/b.md", "a\\b.md",
                                  "C:/x.md", "D:\\x.md", ".hidden", ""])
def test_read_doc_非法名拒绝(know_dir, name):
    with pytest.raises(ValueError):
        agent_knowledge.read_doc(name)


def test_read_doc_缺失与非md(know_dir):
    with pytest.raises(FileNotFoundError):
        agent_knowledge.read_doc("不存在")
    _write(know_dir, "note.txt", "not md")
    with pytest.raises(FileNotFoundError):
        agent_knowledge.read_doc("note.txt")


def test_read_doc_拒绝目录外同前缀(know_dir, tmp_path):
    # 知识目录外存在同名 .md 也不可读（解析后必须仍在根目录内）
    outside = tmp_path / "规范.md"
    outside.write_text("外部", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        agent_knowledge.read_doc("规范")


# ── 路由层：/api/agents/knowledge ─────────────────────────────────────────────


def test_router_list_knowledge(know_dir):
    _write(know_dir, "规范.md", "# 规范")
    got = agents_router.list_knowledge_docs()
    assert [d["name"] for d in got] == ["规范"]


def test_router_read_knowledge_ok(know_dir):
    _write(know_dir, "规范.md", "# 规范\n正文")
    doc = agents_router.read_knowledge_doc("规范")
    assert doc["content"].startswith("# 规范")


def test_router_read_knowledge_404_and_400(know_dir):
    with pytest.raises(HTTPException) as exc:
        agents_router.read_knowledge_doc("不存在")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        agents_router.read_knowledge_doc("../x")
    assert exc.value.status_code == 400


# ── 兼容回归：agent_graph 注入文本行为不变 ────────────────────────────────────


def test_knowledge_catalog_注入顺序截断与上限(know_dir):
    _write(know_dir, "A.md", "# A\n" * 1)
    _write(know_dir, "B.md", "# B\n" * 1)
    _write(know_dir, "C.md", "# C\n" * 1)
    _write(know_dir, "D.md", "# D\n" * 1)
    _write(know_dir, "E.md", "# E\n" * 1)  # 第 5 篇不入注入
    catalog = agent_graph._knowledge_catalog_text()
    assert catalog.startswith("【固化知识库】")
    for stem in ("A", "B", "C", "D"):
        assert f"【知识：{stem}】" in catalog
    assert "【知识：E】" not in catalog
    # 文件名升序 → A 在 B 前
    assert catalog.index("【知识：A】") < catalog.index("【知识：B】")


def test_knowledge_catalog_空目录为空串(know_dir):
    assert agent_graph._knowledge_catalog_text() == ""


# ── 三固化 skill 化（A1/A2）：frontmatter 技能头 + 目录注入 + load_doc ────────


def test_frontmatter_技能头解析_读与列举(know_dir):
    _write(know_dir, "固化02-规范.md",
           "---\nskill: curing-02-x\nwhenToUse: 用户给小说要求做合集卡时\n"
           "tools: [novel.survey, novel.charfacts, novel.scan_anonymity]\n"
           "---\n# 正文标题\n内容")
    meta = agent_knowledge.list_docs()[0]
    assert meta["name"] == "固化02-规范"
    assert meta["skill"] == "curing-02-x"
    assert meta["whenToUse"].startswith("用户给小说")
    assert meta["tools"] == ["novel.survey", "novel.charfacts", "novel.scan_anonymity"]
    doc = agent_knowledge.read_doc("固化02-规范")
    assert doc["content"].startswith("# 正文标题")      # 正文去头
    assert "---" not in doc["content"]
    assert doc["skill"] == "curing-02-x"
    assert doc["tools"] == ["novel.survey", "novel.charfacts", "novel.scan_anonymity"]
    # 无头普通文档：read_doc 行为不变（content=全文、无技能键）
    _write(know_dir, "普通.md", "# 普通\n正文")
    plain = agent_knowledge.read_doc("普通")
    assert plain["content"] == "# 普通\n正文"
    assert "skill" not in plain


def test_knowledge_catalog_技能只注入目录_普通知识仍常驻(know_dir):
    _write(know_dir, "固化技能.md",
           "---\nskill: curing-test\nwhenToUse: 测试触发场景\n---\n# 技能正文 甲乙丙丁")
    _write(know_dir, "普通知识.md", "# 普通正文 一二三四")
    catalog = agent_graph._knowledge_catalog_text()
    assert catalog.startswith("【固化技能库】")
    assert "curing-test" in catalog
    assert "knowledge.load_doc" in catalog
    assert "【知识：固化技能】" not in catalog      # 技能不整篇常驻
    assert "甲乙丙丁" not in catalog
    assert "【固化知识库】" in catalog
    assert "【知识：普通知识】" in catalog          # 无头文档仍全量注入
    assert "一二三四" in catalog


def test_knowledge_catalog_技能目录不被常驻上限裁掉(know_dir):
    # 5 篇无头普通知识只入 4 篇；技能目录始终全列（上限只管普通知识）
    for stem in "ABCDE":
        _write(know_dir, f"{stem}.md", f"# {stem} 内容内容")
    _write(know_dir, "技能.md",
           "---\nskill: curing-s\nwhenToUse: 触发\n---\n# 技能正文内容")
    catalog = agent_graph._knowledge_catalog_text()
    assert "curing-s" in catalog
    for stem in "ABCD":
        assert f"【知识：{stem}】" in catalog
    assert "【知识：E】" not in catalog


def test_knowledge_load_doc_handler(know_dir):
    from app.services import capability_handlers as _ch
    _write(know_dir, "固化X.md", "---\nskill: curing-x\n---\n# 正文内容")
    got = _ch.knowledge_load_doc("固化X")
    assert got["content"].startswith("# 正文内容")
    assert got["skill"] == "curing-x"
    with pytest.raises(ValueError, match="不存在"):
        _ch.knowledge_load_doc("不存在")
    with pytest.raises(ValueError, match="name"):
        _ch.knowledge_load_doc("")


# ── 三固化 skills 契约：文档 tools 引用必须真实存在于能力注册表 ──────────────


def _registered_ops() -> set[str]:
    from app.services import capability_registry as _cr
    return {cap.operation for cap in _cr.all_capabilities()}


def _assert_doc_tools_covered(doc: dict) -> None:
    """防文档-注册表漂移：skill 文档 frontmatter 里声明的 tools 必须都能在
    capability 注册表里解析（LLM 照文档调用才不会拿到不存在的能力）。"""
    assert doc.get("skill"), f"{doc['name']} 缺少 skill frontmatter"
    assert doc.get("whenToUse"), f"{doc['name']} 缺少 whenToUse 触发描述"
    ops = _registered_ops()
    for tool in doc.get("tools") or []:
        assert tool in ops, f"{doc['name']} 声明 tools 含未注册能力：{tool}"


def test_夹具_技能文档tools引用都在注册表(know_dir):
    _write(know_dir, "技能.md",
           "---\nskill: curing-demo\nwhenToUse: 演示触发\n"
           "tools: [knowledge.load_doc, novel.survey, worldbook.upsert_repo]\n"
           "---\n# 正文")
    _assert_doc_tools_covered(agent_knowledge.list_docs()[0])


def test_真实固化三份_本地DATA_DIR存在则契约校验():
    """真实固化01/02/03（backend/data/agent_knowledge/，gitignored 运行态）——
    目录不存在（CI/干净环境）时跳过，存在时逐一校验三份技能文档完整可装载。"""
    root = _DATA_DIR / agent_knowledge.KNOWLEDGE_DIR_NAME
    if not root.is_dir():
        pytest.skip("本地 DATA_DIR 无 agent_knowledge（干净环境跑单测，跳过真实固化校验）")
    docs = {d["name"]: d for d in agent_knowledge.list_docs()}
    curing = {name: d for name, d in docs.items() if d.get("skill", "").startswith("curing-")}
    assert len(curing) == 3, f"期望三份固化技能文档，实际 {len(curing)}：" \
        f"{sorted(curing)}"
    for name, doc in sorted(curing.items()):
        _assert_doc_tools_covered(doc)
        # load_doc 每份都能取到正文（frontmatter 已去头，正文以 # 或文字开头）
        body = agent_knowledge.read_doc(name)["content"]
        assert body.strip(), f"{name} load_doc 取回空正文"
        assert not body.lstrip().startswith("---"), f"{name} 正文未去 frontmatter"
    # smart 目录注入：三份固化技能都在目录行、全文不常驻
    catalog = agent_graph._knowledge_catalog_text()
    for name in ("固化01-批量生图规范", "固化02-小说转合集卡规范", "固化03-ST迁移规范"):
        assert name in catalog, f"smart 目录缺少 {name}"
        assert f"【知识：{name}】" not in catalog, f"{name} 不应整篇常驻（smart 默认）"


# ── 技能写盘 create_doc（knowledge.create_skill 的服务层，2026-09-04）──────────


def test_create_doc_落盘frontmatter并即时可见(know_dir):
    doc = agent_knowledge.create_doc(
        name="固化04-世界观书规范", skill="curing-04-worldbook",
        whenToUse="用户要求把设定/世界观材料做成体系化世界书条目时",
        tools=["knowledge.load_doc", "worldbook.upsert_repo"],
        content="# 固化流程：世界观书规范\n\n## 适用\n设定集转体系条目。")
    assert doc["name"] == "固化04-世界观书规范"
    path = know_dir / "固化04-世界观书规范.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\nskill: curing-04-worldbook\n")
    assert "tools: [knowledge.load_doc, worldbook.upsert_repo]" in text
    # list_docs/read_doc 即时可见（库随固化增长）
    listed = {d["name"]: d for d in agent_knowledge.list_docs()}
    assert listed["固化04-世界观书规范"]["skill"] == "curing-04-worldbook"
    body = agent_knowledge.read_doc("固化04-世界观书规范")["content"]
    assert body.startswith("# 固化流程") and not body.lstrip().startswith("---")


def test_create_doc_覆盖既有拒绝(know_dir):
    agent_knowledge.create_doc(
        name="同款", skill="curing-x", whenToUse="t",
        tools=["knowledge.load_doc"], content="# 一版")
    import pytest as _p
    with _p.raises(FileExistsError, match="禁止覆盖"):
        agent_knowledge.create_doc(
            name="同款", skill="curing-x2", whenToUse="t",
            tools=["knowledge.load_doc"], content="# 二版")


def test_create_doc_非法名与缺字段拒绝(know_dir):
    import pytest as _p
    for bad in ("../逃逸", "a/b", "c:\\win", "", "..", ".隐藏"):
        with _p.raises(ValueError, match="非法知识文档名"):
            agent_knowledge.create_doc(
                name=bad, skill="curing-x", whenToUse="t",
                tools=["knowledge.load_doc"], content="# x")
    with _p.raises(ValueError, match="whenToUse"):
        agent_knowledge.create_doc(
            name="缺触发", skill="curing-x", whenToUse="  ",
            tools=["knowledge.load_doc"], content="# x")
    with _p.raises(ValueError, match="tools"):
        agent_knowledge.create_doc(
            name="缺工具", skill="curing-x", whenToUse="t",
            tools=[], content="# x")


def test_skill_catalog_text_含技能不含普通知识(know_dir):
    _write(know_dir, "普通知识.md", "# 常驻说明")
    agent_knowledge.create_doc(
        name="固化A-示例", skill="curing-a",
        whenToUse="用户要给某个示例跑通固化时",
        tools=["knowledge.load_doc"], content="# 示例")
    text = agent_knowledge.skill_catalog_text()
    assert "固化A-示例" in text and "curing-a" in text
    assert "普通知识" not in text  # 无 frontmatter 的普通知识不进技能库清单
