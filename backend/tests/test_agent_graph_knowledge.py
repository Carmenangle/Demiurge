"""固化知识库（agent_knowledge）注入回归测试（2026-09-03）。

覆盖三层：
1. `_knowledge_catalog_text()` 读取纪律：排序、空文件跳过、目录缺失 → ""、单文件 20000 字符截断、
   最多 4 个文件、拼接头部格式。
2. full 访问模式：固化流程预设清单 + 固化知识库都追加进 fabric_loop 的 history；
   done 后自动 solidify 出草稿配方并带 [[recipe:id|name]] 内联标记。
3. approval 模式：两个目录附件以「固化流程预设清单 / 智能编造知识库」为名进入编译 attachments，
   与 LoRA/模板目录同机制（不参与套装抽取回填，另见 test_plan_compiler.py）。
"""
from __future__ import annotations

from app.services import agent_graph as ag
from app.services import plan_compiler, plan_tasks
from app.services import capability_sandbox


def _make_ctx(**over) -> dict:
    base = {"chat_base": "b", "chat_key": "k", "chat_model": "m",
            "output_dir": "D:/tmp/knowledge-test"}
    base.update(over)
    return base


# ── _knowledge_catalog_text 读取纪律 ─────────────────────────────────────────

def _seed_knowledge(root, names: list[str]) -> None:
    from pathlib import Path
    kdir = Path(root) / "agent_knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (kdir / name).write_text(f"# {name} 内容", encoding="utf-8")


def test_knowledge目录缺失返回空(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert ag._knowledge_catalog_text() == ""


def test_knowledge按文件名排序拼接且跳过空文件(monkeypatch, tmp_path):
    import app.config as config
    from pathlib import Path
    kdir = Path(tmp_path) / "agent_knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / "b-第二篇.md").write_text("条目命名规范", encoding="utf-8")
    (kdir / "a-第一篇.md").write_text("", encoding="utf-8")       # 空文件跳过
    (kdir / "c-第三篇.md").write_text("   \n", encoding="utf-8")  # 纯空白跳过
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    text = ag._knowledge_catalog_text()
    assert text.startswith("【固化知识库】")
    assert "【知识：a-第一篇】" not in text      # 空文件不占位
    assert "【知识：b-第二篇】" in text and "条目命名规范" in text
    assert "【知识：c-第三篇】" not in text      # 纯空白不占位


def test_knowledge最多注入四个文件(monkeypatch, tmp_path):
    import app.config as config
    _seed_knowledge(tmp_path, [f"doc-{i}.md" for i in range(1, 7)])
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    text = ag._knowledge_catalog_text()
    for i in range(1, 5):
        assert f"【知识：doc-{i}】" in text
    for i in (5, 6):
        assert f"【知识：doc-{i}】" not in text   # 超出上限的文档不注入


def test_knowledge单文件超限截断到20000字符(monkeypatch, tmp_path):
    import app.config as config
    from pathlib import Path
    kdir = Path(tmp_path) / "agent_knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / "巨文档.md").write_text("设" * 30_000, encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    text = ag._knowledge_catalog_text()
    assert "【知识：巨文档】" in text
    # 单文件截断上限 20000 字符：正文「设」不会超过上限
    assert text.count("设") <= 20_000


# ── full 模式：清单+知识库双注入自由循环 history ─────────────────────────────

def test_full模式注入固化清单与知识库进history并自动固化(monkeypatch, tmp_path):
    captured: dict = {}
    recipe = {"id": "r_knowledge1", "name": "按规范制作合集卡"}

    class _FakeOutcome:
        status = "done"
        reply = "自由循环完成"
        steps = []  # 空轨迹即可（不做真实固化落库，用假 solidify_steps）

    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return _FakeOutcome()

    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_FULL)
    monkeypatch.setattr(plan_tasks, "solidify_steps",
                        lambda **kwargs: recipe, raising=False)
    monkeypatch.setattr("app.services.fabric_loop.run_loop", fake_run_loop)
    monkeypatch.setattr(ag, "_recipe_catalog_text",
                        lambda: "《示例配方》 id=r0（2 步）意图：导入角色卡")
    monkeypatch.setattr(ag, "_knowledge_catalog_text",
                        lambda: "【固化知识库】合集卡制作规范摘要")

    ctx = _make_ctx(output_dir=str(tmp_path), thread_id="t1", message_id="m1")
    result = ag.plan_compiler_node({"user_text": "写个文件", "trace": [], "_ctx": ctx})

    assert result["result_text"] == "自由循环完成" + "\n\n[[recipe:r_knowledge1|按规范制作合集卡]]"
    history = captured["history"]
    assert "《示例配方》" in history and "id=r0" in history
    assert "合集卡制作规范摘要" in history
    # 注入顺序：配方清单先、知识库后（recipe 在前）
    assert history.index("《示例配方》") < history.index("合集卡制作规范摘要")


# ── approval 模式：目录附件进编译 attachments ───────────────────────────────

def test_approval模式目录附件包含固化清单与知识库(monkeypatch, tmp_path):
    captured: dict = {}

    class _Outcome:
        plan = None
        errors = ["fake 校验错误"]

    def fake_compile_plan(**kwargs):
        captured.update(kwargs)
        return _Outcome()

    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_APPROVAL)
    monkeypatch.setattr(ag, "_recipe_catalog_text",
                        lambda: "《示例配方》 id=r0（2 步）意图：导入角色卡")
    monkeypatch.setattr(ag, "_knowledge_catalog_text",
                        lambda: "【固化知识库】合集卡制作规范摘要")
    monkeypatch.setattr(plan_compiler, "compile_plan", fake_compile_plan)

    ctx = _make_ctx(output_dir=str(tmp_path))
    result = ag.plan_compiler_node({"user_text": "根据规范导入角色卡", "trace": [], "_ctx": ctx})

    assert "计划编译未通过校验" in result["result_text"]  # plan=None 早退，不落盘不投递
    names = [a["name"] for a in captured["attachments"]]
    assert plan_compiler.RECIPE_CATALOG_NAME in names
    assert plan_compiler.KNOWLEDGE_CATALOG_NAME in names
    by_name = {a["name"]: a["text"] for a in captured["attachments"]}
    assert "《示例配方》" in by_name[plan_compiler.RECIPE_CATALOG_NAME]
    assert "合集卡制作规范摘要" in by_name[plan_compiler.KNOWLEDGE_CATALOG_NAME]


def test_approval模式目录为空时清单与知识库不进attachments(monkeypatch, tmp_path):
    captured: dict = {}

    class _Outcome:
        plan = None
        errors = ["fake"]

    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_APPROVAL)
    monkeypatch.setattr(ag, "_recipe_catalog_text", lambda: "")
    monkeypatch.setattr(ag, "_knowledge_catalog_text", lambda: "")
    monkeypatch.setattr(plan_compiler, "compile_plan",
                        lambda **kwargs: captured.update(kwargs) or _Outcome())

    ctx = _make_ctx(output_dir=str(tmp_path))
    ag.plan_compiler_node({"user_text": "写个文件", "trace": [], "_ctx": ctx})
    names = {a["name"] for a in captured["attachments"]}
    assert plan_compiler.RECIPE_CATALOG_NAME not in names
    assert plan_compiler.KNOWLEDGE_CATALOG_NAME not in names
