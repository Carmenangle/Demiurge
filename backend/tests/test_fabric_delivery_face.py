"""内容交付自由循环能力面回归（2026-09-10）。

背景（用户定案场景）：
- 交付类任务（合集卡/角色卡/世界书/设定文档）走 fabric 自由循环，此前能力面白名单
  硬编码在 agent_graph._CARD_DELIVERY_ALLOWED（新增能力要改两处）→ 收归注册表单一属主
  capability_registry.FABRIC_DOC_DELIVERY_OPS；
- 用户诉求「工具要能在对话里自己生成、自己排错」→ 新增 FABRIC_TOOLING_OPS
  （file.write_text + project.run_shell），但**按需解锁**（2026-09-06 实锤：模型用
  run_shell 读小说、load 固化01 编排生图，无条件放开会重演跑偏）；
- 判定文本与执行文本解耦：delivery_intent = 本轮原文拼历史（_fabric_judge），
  解决「先对话积累上下文，再说一句『整理成设定总集』」时能力面/前序产出档案落空。
"""
from __future__ import annotations

from app.services import agent_graph, capability_registry as cr, plan_compiler


def test_交付能力面白名单每个操作都真实注册():
    """防漂移：白名单里的操作必须存在于注册表（否则模型永远看不到 = 静默失效）。"""
    registered = {c.operation for c in cr.all_capabilities()}
    unknown = (set(cr.FABRIC_DOC_DELIVERY_OPS) | set(cr.FABRIC_TOOLING_OPS)) - registered
    assert unknown == set(), f"白名单含未注册能力：{sorted(unknown)}"


def test_自建工具通道与常规交付面互不重叠():
    """write_text/run_shell 只在按需通道里——常规交付面保持「摸不到 shell」的红线语义。"""
    assert cr.FABRIC_TOOLING_OPS & cr.FABRIC_DOC_DELIVERY_OPS == frozenset()
    assert "project.run_shell" not in cr.FABRIC_DOC_DELIVERY_OPS
    assert "file.write_text" not in cr.FABRIC_DOC_DELIVERY_OPS


def test_白名单单一属主agent_graph不再持有本地清单():
    assert not hasattr(agent_graph, "_CARD_DELIVERY_ALLOWED")


def test_交付能力面过滤与按需解锁():
    doc_intent = "把世界书角色条目、角色卡外貌和近期纪要整理合并为一个设定文档，写到 docs/设定总集.md"
    tooling_intent = (
        "把世界书条目整理成设定总集写到 docs/设定总集.md；"
        "顺便写个脚本把素材图按角色归一下，跑一下看报错自己排错")

    ops = {c["operation"] for c in agent_graph._fabric_capabilities(doc_intent, {"chat"})}
    assert "doc.create_repo" in ops and "knowledge.load_doc" in ops
    # 固化04 的纪读取数口必须在交付面里：迁移白名单到注册表时曾漏掉它（真实链路冒烟实锤：
    # 模型只能 file.read_text 直读 chronicle.db 被拒 → 产物「近期纪要」退化成「（无近期纪要）」）。
    assert "narrative.read_chronicle" in ops
    # 防跑偏红线：常规交付任务摸不到 shell / 写脚本
    assert "project.run_shell" not in ops and "file.write_text" not in ops
    assert "workflow.submit_batch" not in ops

    ops_tool = {c["operation"]
                for c in agent_graph._fabric_capabilities(tooling_intent, {"chat"})}
    assert "project.run_shell" in ops_tool and "file.write_text" in ops_tool
    assert "doc.create_repo" in ops_tool  # 交付能力不因解锁工具而丢失


def test_非交付意图保持全量能力面():
    ops = {c["operation"]
           for c in agent_graph._fabric_capabilities("按这套穿搭批量出 8 张图", {"chat"})}
    assert "workflow.submit_batch" in ops  # 批量生图仍见生图能力


def test_自建工具意图判定():
    # 命中：显式要求写脚本/跑命令/排错/自动化
    for text in ("写个脚本把素材图下载下来",
                 "跑一下这个命令看看哪里报错，自己排错",
                 "做个自动化小工具处理图片",
                 "自己生成一个脚本处理这批文件"):
        assert plan_compiler.wants_tooling_intent(text), text
    # 不命中：常规交付、疑问句、裸「脚本」措辞不当（剧本/脚本两义）
    for text in ("把这几份设定合并为一个文档",
                 "把小说脚本整理成设定总集",
                 "为什么要跑命令",
                 ""):
        assert not plan_compiler.wants_tooling_intent(text), text


def test_判定文本决定能力面而非执行文本():
    """短指令「继续补写」+ 历史含交付意图：能力面按判定文本收窄（而不是全量）。
    反向验证 delivery_intent 解耦——intent 仍可用短文本，能力面不落空。"""
    calls: dict = {}
    real = agent_graph._fabric_capabilities

    def spy(intent_text: str, configured: set[str]):
        calls["intent_text"] = intent_text
        return real(intent_text, configured)

    agent_graph._fabric_capabilities = spy  # type: ignore[assignment]
    try:
        args = agent_graph._fabric_run_args(
            {"chat_base": "http://x", "chat_key": "k", "chat_model": "m"},
            intent="继续补写", history="",
            access_mode="approval", lease_id="l", output_dir="/tmp/none",
            repo_id="r", configured={"chat"}, images=[],
            delivery_intent="把世界书条目整理合并为一个设定文档",
        )
    finally:
        agent_graph._fabric_capabilities = real  # type: ignore[assignment]
    assert calls["intent_text"] == "把世界书条目整理合并为一个设定文档"
    assert args["intent"] == "继续补写"
    ops = {c["operation"] for c in args["capabilities"]}
    assert "doc.create_repo" in ops
