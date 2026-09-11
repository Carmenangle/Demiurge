"""Autopilot P1 单测：委派意图识别 / plan_validator 各分支 / 编译落盘闭环。"""
from __future__ import annotations

import json
from pathlib import Path

from app.services import plan_compiler, plan_validator
from app.services.capability_registry import all_capabilities
from app.services.structured_contracts import (
    GenerationPlan, PlanBudgets, PlanStep,
)

WORKS = r"D:\works\我的作品"


def _cap(operation: str):
    return next(c for c in all_capabilities() if c["operation"] == operation)


def _plan(**over) -> GenerationPlan:
    base = dict(
        intent="批量出 3 张变体图", repo_id="work",
        budgets=PlanBudgets(max_steps=4, max_gpu_tasks=4, max_llm_calls=2),
        steps=[PlanStep(id="s1", operation="workflow.list_templates")],
    )
    base.update(over)
    return GenerationPlan(**base)


# ── 委派意图识别（路由界限·零 LLM 层）────────────────────────────────────────

def test_高置信委派命中():
    assert plan_compiler.is_delegation_intent("帮我批量出 20 张变体图")
    assert plan_compiler.is_delegation_intent("整理全部世界书条目")
    assert plan_compiler.is_delegation_intent("把这三张卡都导入并建仓")
    assert plan_compiler.is_delegation_intent("帮我做个计划自动完成出图")
    assert plan_compiler.is_delegation_intent("提取文档各个套装的提示词，分批生成图片")
    assert plan_compiler.is_delegation_intent("读取文档，调用模板，逐批生成图片")


def test_单次创作与疑问不误判():
    assert not plan_compiler.is_delegation_intent("画一张图")
    assert not plan_compiler.is_delegation_intent("生成一张图")
    assert not plan_compiler.is_delegation_intent("为什么批量出图失败了？")
    assert not plan_compiler.is_delegation_intent("她提笔画了一幅像")
    assert not plan_compiler.is_delegation_intent("")


# ── 文档交付委派（允许带图附件；看图反推→生成套装文档场景）────────────────────

def test_文档交付委派命中():
    # 场景1：带参考图，看图反推外貌 + 阅读时尚文档 → 四季套装文档
    assert plan_compiler.is_doc_delegation_intent(
        "根据这张图反推角色外貌，阅读我提供的时尚穿搭文档，"
        "生成春夏秋冬各两套套装加睡衣运动服，整理生成文档")
    # 场景2：普通对话讨论角色外貌后，中途要求整理成综合文档
    assert plan_compiler.is_doc_delegation_intent(
        "把上面商讨的角色外貌结果整理成一份综合文档")
    assert plan_compiler.is_doc_delegation_intent("汇总成文档")
    assert plan_compiler.is_doc_delegation_intent("写成文档保存")
    # 合集卡/角色卡/世界书整卡交付（固化02/03 场景，2026-09-05）
    assert plan_compiler.is_doc_delegation_intent(
        "根据这本小说制作合集卡，把全局机制、系统判定机制、角色等内容都整理好")
    assert plan_compiler.is_doc_delegation_intent("创建角色卡")
    assert plan_compiler.is_doc_delegation_intent("把这张卡的内嵌世界书独立出来")
    assert plan_compiler.is_doc_delegation_intent("导入角色卡并迁移世界书")


def test_固化04上下文合并话术命中():
    """2026-09-09 补强：多源上下文合并为一个设定文档的自然说法必须进委派
    （此前仅规范原句靠「所有+整理」命中，总集/md 型说法全落空→掉 roleplay）。"""
    assert plan_compiler.is_doc_delegation_intent(
        "把当前作品世界书里所有角色条目、角色卡外貌与近期纪要，"
        "整理合并为一个设定文档，写到作品 docs/ 下")
    assert plan_compiler.is_doc_delegation_intent(
        "把这几份设定合并为一个文档，放到作品 docs/ 下。")
    assert plan_compiler.is_doc_delegation_intent(
        "整理一份作品设定总集，世界书、角色卡和近期纪要都并进去。")
    assert plan_compiler.is_doc_delegation_intent(
        "我有一堆设定材料，帮我合并成一份设定文档。")
    assert plan_compiler.is_doc_delegation_intent(
        "生成一个文档，汇总当前作品的世界书条目、角色卡和纪要。")


def test_卡交付继续形态_优化类动词命中():
    """2026-09-07 治本：内容不够丰富→补写/完善/优化类指令同样进 fabric 自由循环。

    此前「先做密度检查…再补写丰富合集卡内容」被 _QUESTION_RE 的「检查」一票否决，
    掉进无 LLM 的机械计划执行器（plan_tasks partial 卡死实锤）；动词表也缺
    优化类动词。修复后以下指令全部命中委派。
    """
    # 真实翻车指令：含「检查」但语义是制作任务的继续
    assert plan_compiler.is_doc_delegation_intent(
        "基于《玫瑰与繁花》已有卡纲与已落盘条目，先做世界书密度检查找出不达标条目，"
        "再针对性地补写丰富合集卡内容（不重新通读全文）。")
    # 优化类动词 + 卡名词（语序正反都命中：同句共现，不再依赖近邻窗口）
    assert plan_compiler.is_doc_delegation_intent(
        "《玫瑰与繁花》合集卡内容不够丰富，基于已有产出补写，不要重读全文。")
    assert plan_compiler.is_doc_delegation_intent(
        "角色卡太单薄了，重点补写戴茂、黛绮丝的条目到2200字以上。")
    assert plan_compiler.is_doc_delegation_intent(
        "NSFW条目太少，按规范补到8-14条，每条400字以上。")
    assert plan_compiler.is_doc_delegation_intent("帮我把世界书里的蛇族条目细化一下")


def test_卡交付继续形态_真疑问与闲聊不误判():
    # 排查/疑问句仍不委派（为什么/怎么/？是真正的疑问信号）
    assert not plan_compiler.is_doc_delegation_intent("检查一下这个卡有什么问题")
    assert not plan_compiler.is_doc_delegation_intent("为什么剧情回复这么短")
    assert not plan_compiler.is_doc_delegation_intent("今天天气怎么样")


def test_文档交付委派不误判():
    # 无文档交付动作（纯指图）不抢图生图/反推
    assert not plan_compiler.is_doc_delegation_intent("批量处理这些图")
    assert not plan_compiler.is_doc_delegation_intent("看看这篇文档写了什么")
    # 疑问句不委派
    assert not plan_compiler.is_doc_delegation_intent("能帮我整理成文档吗？")
    # 无卡交付动作（闲聊指卡/看书）不抢普通对话
    assert not plan_compiler.is_doc_delegation_intent("这张卡真好看")
    assert not plan_compiler.is_doc_delegation_intent("看看这本世界书写了什么")
    # 2026-09-09 剧情/器件语境不误判：目标词只收文档型工件名，
    # 不收裸「设定」与剧情器物（典籍/案卷/法典）——绝不牺牲剧情默认
    assert not plan_compiler.is_doc_delegation_intent("把家族记载合并成一部典籍")
    assert not plan_compiler.is_doc_delegation_intent("把这几条规则合并成一条，写进下次剧情")
    assert not plan_compiler.is_doc_delegation_intent("帮我把书架上的案卷整理归档")
    assert not plan_compiler.is_doc_delegation_intent("")


# ── plan_validator ───────────────────────────────────────────────────────────

def test_合法计划零错误():
    assert plan_validator.validate(
        _plan(), capabilities=all_capabilities(),
        configured_models={"chat", "image"}, allowed_prefix=WORKS) == []


def test_未知能力被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="ghost.action")])
    errors = plan_validator.validate(plan, capabilities=all_capabilities())
    assert any("ghost.action" in e for e in errors)


def test_缺必填参数与多余参数被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="workflow.read_exposed_fields")])
    errors = plan_validator.validate(plan, capabilities=all_capabilities())
    assert any("template_id" in e for e in errors)
    plan2 = _plan(steps=[PlanStep(id="s1", operation="workflow.list_templates",
                                  params={"junk": 1})])
    errors2 = plan_validator.validate(plan2, capabilities=all_capabilities())
    assert any("junk" in e for e in errors2)


def test_模型缺口被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="workflow.submit_batch",
                                 params={"template_id": "t", "variants": [{}],
                                         "prompt": "p", "url": "http://127.0.0.1:8188"})])
    errors = plan_validator.validate(plan, capabilities=all_capabilities(),
                                     configured_models={"chat"})
    assert any("image" in e and "未配置" in e for e in errors)


def test_无预算与巨型计划被拦():
    plan = _plan(budgets=PlanBudgets(max_steps=0, max_gpu_tasks=1, max_llm_calls=1))
    assert any("budgets" in e for e in plan_validator.validate(plan, capabilities=all_capabilities()))
    plan2 = _plan(budgets=PlanBudgets(max_steps=99, max_gpu_tasks=1, max_llm_calls=1))
    assert any("拆成多个小计划" in e for e in plan_validator.validate(plan2, capabilities=all_capabilities()))


def test_inputs_from环被拦():
    plan = _plan(steps=[
        PlanStep(id="a", operation="workflow.list_templates", inputs_from=["b"]),
        PlanStep(id="b", operation="workflow.list_templates", inputs_from=["a"]),
    ])
    assert any("成环" in e for e in plan_validator.validate(plan, capabilities=all_capabilities()))


def test_审批汇总不一致被拦():
    plan = _plan(approval_required=["workflow.submit_template"])
    errors = plan_validator.validate(plan, capabilities=all_capabilities(),
                                     configured_models={"chat", "image"})
    assert any("approval_required" in e for e in errors)


def test_路径越出作品域被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="workflow.submit_template",
                                 params={"template_id": "t", "values": {},
                                         "prompt": r"D:\other\evil.png",
                                         "url": "http://127.0.0.1:8188"})])
    errors = plan_validator.validate(plan, capabilities=all_capabilities(),
                                     configured_models={"chat", "image"},
                                     allowed_prefix=WORKS)
    assert any("越出作品域" in e for e in errors)


# ── 编译闭环（structured_output 假件）────────────────────────────────────────

class _FakeStructured:
    def __init__(self, payload_fn):
        self.payload_fn = payload_fn
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(self.payload_fn(self.calls))


def test_编译一次成功并落盘(tmp_path):
    payload = {
        "intent": "批量出 3 张变体图", "repo_id": "work",
        "budgets": {"max_steps": 4, "max_gpu_tasks": 4, "max_llm_calls": 2},
        "steps": [
            {"id": "s1", "operation": "workflow.list_templates"},
            {"id": "s2", "operation": "workflow.submit_batch",
             "params": {"template_id": "t", "variants": [{"steps": 20}],
                        "prompt": "p", "url": "http://127.0.0.1:8188"},
             "inputs_from": ["s1"]},
        ],
        "approval_required": ["workflow.submit_batch"],
    }
    fake = _FakeStructured(lambda _c: payload)
    outcome = plan_compiler.compile_plan(
        intent="批量出 3 张变体图", repo_id="work", output_dir=str(tmp_path),
        configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "", structured_chat_fn=fake)
    assert outcome.plan is not None, outcome.errors
    assert outcome.plan.steps[1].inputs_from == ["s1"]
    # budgets 由代码确定性归一：步数=实际步骤数，GPU=variants 数，LLM=0
    assert outcome.plan.budgets.max_steps == 2
    assert outcome.plan.budgets.max_gpu_tasks == 1
    assert outcome.plan.budgets.max_llm_calls == 0

    json_path = plan_compiler.save_plan(str(tmp_path), "work", outcome.plan)
    assert Path(json_path).is_file()
    assert Path(json_path).suffix == ".json"
    saved = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert saved["intent"] == "批量出 3 张变体图"
    md = Path(json_path.replace(".plan.json", ".plan.md"))
    assert md.is_file() and "需审批" in md.read_text(encoding="utf-8")

    card = plan_compiler.render_plan_card(outcome.plan, json_path)
    assert "workflow.submit_batch" in card and "已投递执行队列" in card


def test_编译期先回填variants再归一预算(tmp_path):
    # 模型只排了 submit_batch 但 variants 为空：校验前 fill_prompt_sections 应
    # 从附件文档重建变体，budgets.max_gpu_tasks 归一为文档真实段数
    doc = "\n".join([
        "## 【春·套一】测试套装",
        "QRQ, masterpiece, " + "tag, " * 200,
        "=" * 60,
        "## 【春·套二】测试套装",
        "QRQ, masterpiece, " + "tag, " * 200,
        "=" * 60,
    ])
    payload = {
        "intent": "批量出图", "repo_id": "work",
        "budgets": {"max_steps": 6, "max_gpu_tasks": 32, "max_llm_calls": 4},
        "steps": [
            {"id": "s1", "operation": "workflow.read_exposed_fields",
             "params": {"template_id": "a546d311"}},
            {"id": "s2", "operation": "workflow.submit_batch",
             "params": {"template_id": "a546d311", "url": "http://127.0.0.1:8188",
                        "variants": []}},
            {"id": "s3", "operation": "media.collect_comfy_outputs",
             "params": {"comfyui_url": "http://127.0.0.1:8188"},
             "inputs_from": ["s2.submit_result"]},
        ],
        "approval_required": ["workflow.submit_batch"],
    }
    fake = _FakeStructured(lambda _c: payload)
    outcome = plan_compiler.compile_plan(
        intent="批量出图", repo_id="work", output_dir=str(tmp_path),
        attachments=[{"name": "形象提示词-唐柚.md", "text": doc}],
        configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "", structured_chat_fn=fake)
    assert outcome.plan is not None, outcome.errors
    variants = outcome.plan.steps[1].params["variants"]
    assert len(variants) == 2
    assert outcome.plan.budgets.max_gpu_tasks == 2
    assert outcome.plan.budgets.max_steps == 3
    assert outcome.plan.budgets.max_llm_calls == 0


def test_同模板多个submit_batch自动合并():
    plan = _plan(steps=[
        PlanStep(id="s1", operation="workflow.submit_batch",
                 params={"template_id": "t", "variants": [{"name": "A"}],
                         "url": "http://127.0.0.1:8188"}),
        PlanStep(id="s2", operation="workflow.submit_batch",
                 params={"template_id": "t", "variants": [{"name": "A"}, {"name": "B"}],
                         "url": "http://127.0.0.1:8188"}),
        PlanStep(id="s3", operation="media.collect_comfy_outputs",
                 params={"comfyui_url": "http://127.0.0.1:8188"},
                 inputs_from=["s1.submit_result", "s2.submit_result"]),
    ])
    plan_compiler._merge_duplicate_submits(plan)
    assert len(plan.steps) == 2
    assert [v["name"] for v in plan.steps[0].params["variants"]] == ["A", "B"]
    assert plan.steps[1].inputs_from == ["s1.submit_result"]


def test_大文档附件全文投喂_超限才退化骨架(tmp_path):
    doc = "\n".join(["# 文档", "## 【春·套一】银灰开衫", "正文" * 3000, "## 【春·套二】雾蓝马甲", "正文" * 3000])
    view = plan_compiler._attachment_brief(doc)
    assert view == doc  # P4：全文进上下文，模型才判断得了哪些是套装
    assert "【春·套一】" in view and "【春·套二】" in view and "正文" in view
    huge = "\n".join(["## 【套一】", "正文" * 90000])
    brief = plan_compiler._attachment_brief(huge)
    assert len(brief) < len(huge)
    assert plan_compiler._attachment_brief("短文档") == "短文档"

def test_编译两次仍非法如实返回错误(tmp_path):
    bad = {"intent": "x", "steps": [{"id": "s1", "operation": "ghost.action"}]}
    fake = _FakeStructured(lambda _c: bad)
    outcome = plan_compiler.compile_plan(
        intent="x", output_dir=str(tmp_path), configured_models={"chat"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "",
        structured_chat_fn=fake)
    assert outcome.plan is None
    assert outcome.errors and any("ghost.action" in e for e in outcome.errors)
    assert fake.calls == 2  # 带校验错误重试一次


# ── 编译重试反馈闭环（2026-09-05 实锤）───────────────────────────────────────
# 真实失败链：模型直写 14 套完整 prompt → 输出超长被截断 → 解析器拿到提示词里
# 回显的示例对象判「intent missing」→ 解析错误没回灌重试消息 → 第二次模型收到
# 空反馈盲目收敛成只读计划 → 撞死「意图要求出图但无 submit/collect」。

def _合法submit计划payload() -> dict:
    return {
        "intent": "读取文档套装提示词，用模板批量出图", "repo_id": "work",
        "budgets": {"max_steps": 3, "max_gpu_tasks": 2, "max_llm_calls": 0},
        "steps": [
            {"id": "s1", "operation": "workflow.list_templates"},
            {"id": "s2", "operation": "workflow.submit_batch",
             "params": {"template_id": "t", "variants": [{"name": "A"}, {"name": "B"}],
                        "prompt": "p", "url": "http://127.0.0.1:8188"},
             "inputs_from": ["s1"]},
            {"id": "s3", "operation": "media.collect_comfy_outputs",
             "params": {"comfyui_url": "http://127.0.0.1:8188"},
             "inputs_from": ["s2.submit_result"]},
        ],
        "approval_required": ["workflow.submit_batch", "media.collect_comfy_outputs"],
    }


def test_解析失败错误回灌重试消息(tmp_path):
    calls: list[str] = []

    def chat_fn(_b, _k, _m, _s, user, **_kw):
        calls.append(user)
        if len(calls) == 1:
            # 第一次：示例回显 + 计划被截断（复刻真实 trace 的 attempt1）
            return ('输出示例 {"name": "套装名", "prompt_section": "标题关键词"}\n'
                    '计划：{"intent": "出图", "steps": [{"id": "s1", "opera')
        return json.dumps(_合法submit计划payload(), ensure_ascii=False)

    outcome = plan_compiler.compile_plan(
        intent="读取文档套装提示词批量出图", output_dir=str(tmp_path),
        configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="", chat_fn=chat_fn)
    assert outcome.plan is not None, outcome.errors
    assert len(calls) == 2
    # 重试消息必须带真实错误与可行动指引，不能是空反馈
    assert "结构化输出未通过" in calls[1]
    assert "prompt_section" in calls[1]
    assert any(step.operation == "workflow.submit_batch" for step in outcome.plan.steps)


def test_附件在场时系统提示强制段落引用(tmp_path):
    seen: dict = {}

    class _RecordingStructured:
        def __call__(self, *args, **kwargs):
            seen["system"] = args[3]
            return kwargs["schema"].model_validate(_合法submit计划payload())

    doc = "## 【春·套一】测试套装\nQRQ, masterpiece, " + "tag, " * 200
    outcome = plan_compiler.compile_plan(
        intent="读取文档套装提示词批量出图", output_dir=str(tmp_path),
        attachments=[{"name": "形象提示词-柏言.md", "text": doc}],
        configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "",
        structured_chat_fn=_RecordingStructured())
    assert outcome.plan is not None, outcome.errors
    assert "禁止直写完整 prompt" in seen["system"]
    assert "prompt_section" in seen["system"]

    # 只有目录附件（LoRA/模板清单）时不触发：没有用户文档可引用
    seen.clear()
    plan_compiler.compile_plan(
        intent="批量出图", output_dir=str(tmp_path),
        attachments=[{"name": plan_compiler.LORA_CATALOG_NAME, "text": "目录"}],
        configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "",
        structured_chat_fn=_RecordingStructured())
    assert "禁止直写完整 prompt" not in seen["system"]


# ── 批量延续（方案 A，2026-09-05 实锤）───────────────────────────────────────
# 续轮编译上下文没有原文档附件，模型凭记忆重写提示词=幻觉（实锤编出
# 「套装01-日常校园」伪套装且校验拦不住）。收口：延续意图命中时注入上一批计划
# 真实变体，模型只写 name+改动字段，prompt 由编译期按 name 机械回填。

def test_批量延续意图判定():
    assert plan_compiler.is_batch_continuation("将latent替换为1080x720，再次生成一批图")
    assert plan_compiler.is_batch_continuation("重新生成全部套装，这次用横版")
    assert plan_compiler.is_batch_continuation("再来一批，其他不变")
    # 2026-09-05 用户实锤：「重新分批生成」不连续，「上轮」指代——都要命中
    assert plan_compiler.is_batch_continuation("上轮是生图比例生成错误,宽度应该是720,高度应该是1080,重新分批生成结果")
    assert plan_compiler.is_batch_continuation("上次那批重新出")
    assert not plan_compiler.is_batch_continuation("画一张图")
    assert not plan_compiler.is_batch_continuation("批量出图")  # 首编无重复词
    assert not plan_compiler.is_batch_continuation("")


def test_prev_batch_for_兜底窗口(tmp_path):
    import time
    plans = tmp_path / "plans"
    plans.mkdir()
    batch_plan = {
        "intent": "批量出图",
        "steps": [{"id": "s2", "operation": "workflow.submit_batch",
                   "params": {"template_id": "t1",
                              "variants": [{"name": "套一", "prompt": "p1"}]}}]}
    path = plans / "20260905-090000-batch.plan.json"
    path.write_text(json.dumps(batch_plan, ensure_ascii=False), encoding="utf-8")
    fresh = time.time() - 3600
    import os
    os.utime(path, (fresh, fresh))

    prev = plan_compiler.prev_batch_for(str(tmp_path), "批量出 14 张套装图")
    assert prev is not None and prev["mtime"] == fresh  # 无延续语：批量出图委派+24h 内兜底
    # 超窗（25h）：不注入
    stale = time.time() - 25 * 3600
    os.utime(path, (stale, stale))
    assert plan_compiler.prev_batch_for(str(tmp_path), "批量出 14 张套装图") is None
    # 超窗但延续语命中：不限时效仍注入
    os.utime(path, (stale, stale))
    got = plan_compiler.prev_batch_for(str(tmp_path), "重新生成那一批图")
    assert got is not None
    # 非出图意图（文档整理）：不注入
    os.utime(path, (fresh, fresh))
    assert plan_compiler.prev_batch_for(str(tmp_path), "把全部纪要整理合并为一个文档") is None
    assert plan_compiler.prev_batch_for(str(tmp_path / "x"), "重新生成一批") is None


def test_最近批量计划提取_跳过无变体计划(tmp_path):
    import time
    plans = tmp_path / "plans"
    plans.mkdir()
    doc_plan = {"intent": "整理文档", "steps": [{"id": "s1", "operation": "doc.create_repo"}]}
    batch_plan = {
        "intent": "批量出图",
        "steps": [{"id": "s2", "operation": "workflow.submit_batch",
                   "params": {"template_id": "t1", "lora_name": "a.safetensors",
                              "variants": [{"name": "套一", "prompt": "p1"},
                                           {"name": "套二", "prompt": "p2"}]}}]}
    (plans / "20260905-100000-doc.plan.json").write_text(
        json.dumps(doc_plan, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.01)
    (plans / "20260905-090000-batch.plan.json").write_text(
        json.dumps(batch_plan, ensure_ascii=False), encoding="utf-8")
    prev = plan_compiler.latest_submit_plan(str(tmp_path))
    assert prev is not None
    assert prev["template_id"] == "t1"
    assert [v["name"] for v in prev["variants"]] == ["套一", "套二"]
    assert plan_compiler.latest_submit_plan(str(tmp_path / "nonexist")) is None


def test_延续回填按name匹配(tmp_path):
    prev = {"variants": [{"name": "套一", "prompt": "prompt-A",
                          "values": {"latent_width": 720, "latent_height": 1080,
                                     "lora_weight": 0.9}},
                         {"name": "套二", "prompt": "prompt-B",
                          "values": {"latent_width": 720, "latent_height": 1080}}]}
    plan = _plan(steps=[
        PlanStep(id="s1", operation="workflow.submit_batch",
                 params={"template_id": "t", "url": "http://127.0.0.1:8188",
                         "variants": [{"name": "套一", "latent_width": 1080},
                                      {"name": "套二"}]})])
    filled = plan_compiler.fill_prev_batch_prompts(plan, prev)
    assert filled == 2
    variants = plan.steps[0].params["variants"]
    # prompt 逐字回填；模型显式写的键优先，未写的从上一批继承
    assert variants[0]["prompt"] == "prompt-A" and variants[0]["latent_width"] == 1080
    assert variants[1]["prompt"] == "prompt-B"
    assert variants[1]["values"] == {"latent_width": 720, "latent_height": 1080}
    assert "latent_width" not in variants[1]  # 模型没写顶层键就不凭空造
    # 已有 prompt 的变体不被覆盖
    plan2 = _plan(steps=[PlanStep(id="s1", operation="workflow.submit_batch",
                                  params={"variants": [{"name": "套一", "prompt": "自写"}]})])
    assert plan_compiler.fill_prev_batch_prompts(plan2, prev) == 0
    assert plan2.steps[0].params["variants"][0]["prompt"] == "自写"


def test_延续编译端到端_附件注入且prompt回填(tmp_path):
    prev = {"template_id": "t1", "lora_name": "a.safetensors",
            "variants": [{"name": "套一", "prompt": "prompt-A"},
                         {"name": "套二", "prompt": "prompt-B"}]}
    payload = {
        "intent": "将latent替换为1080x720再次生成一批", "repo_id": "work",
        "budgets": {"max_steps": 2, "max_gpu_tasks": 2, "max_llm_calls": 0},
        "steps": [
            {"id": "s1", "operation": "workflow.submit_batch",
             "params": {"template_id": "t1", "lora_name": "a.safetensors",
                        "url": "http://127.0.0.1:8188",
                        "variants": [{"name": "套一", "latent_width": 1080,
                                      "latent_height": 720},
                                     {"name": "套二", "latent_width": 1080,
                                      "latent_height": 720}]}},
            {"id": "s2", "operation": "media.collect_comfy_outputs",
             "params": {"comfyui_url": "http://127.0.0.1:8188"},
             "inputs_from": ["s1.submit_result"]},
        ],
        "approval_required": ["workflow.submit_batch", "media.collect_comfy_outputs"],
    }
    fake = _FakeStructured(lambda _c: payload)
    outcome = plan_compiler.compile_plan(
        intent="将latent替换为1080x720，再次生成一批图", output_dir=str(tmp_path),
        configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "",
        structured_chat_fn=fake, prev_batch=prev)
    assert outcome.plan is not None, outcome.errors
    variants = outcome.plan.steps[0].params["variants"]
    assert [v["prompt"] for v in variants] == ["prompt-A", "prompt-B"]
    assert variants[0]["latent_width"] == 1080 and variants[0]["latent_height"] == 720


def test_延续附件与系统提示():
    prev = {"template_id": "t1", "lora_name": "a.safetensors",
            "variants": [{"name": "套一", "prompt": "p"}]}
    att = plan_compiler.prev_batch_attachment(prev)
    assert att["name"] == plan_compiler.PREV_BATCH_ATTACHMENT_NAME
    assert att["name"] in plan_compiler.CATALOG_ATTACHMENT_NAMES  # 不触发段落引用强制
    assert "逐字复制" in att["text"] and "套一" in att["text"]

# ── 段落回填卫生：正文行中套名引用不是标题 / 目录附件不是提示词源 ─────────────
# 真实失败（2026-09-02）：唐柚文档「使用说明」第 4 条含【运动·套一/套二】行中引用，
# 旧判定「任何含【】的行都是标题」把该行当成段落开头，吞掉第 5-9 条并延伸进拼接的
# LoRA/模板目录附件（文件名含 masterpiece 通过质量过滤）→ 伪第 15 变体；目录里
# 「禁止写 TO_BE_RESOLVED」字样又触发占位符校验被拒，整批任务无法投递。

def _套装文档() -> str:
    def 套(名: str) -> str:
        return "\n".join([
            f"## 【{名}】测试套装",
            "QRQ, masterpiece, " + "tag, " * 200,
            "=" * 60,
        ])
    return "\n".join([
        套("春·套一"), 套("春·套二"),
        "## 使用说明",
        "1. 每个代码块是一套完整 prompt，整块复制即可。",
        "4. **鞋履**：签名鞋全 14 套统一；【运动·套一/套二】配同款白低帮运动鞋。",
        "5. **袜子纪律**：裙装套分两类，长裤套一律及踝短袜。",
    ])


def test_extract_all_sections_正文行中套名引用不算标题():
    joined = _套装文档() + "\n" + (
        "共 2 个：\n- anima-base-1-masterpiece-v51.safetensors（触发词:masterpiece/very aesthetic，建议权重:0.8）\n"
        "用户提到近似名称时优先用上面的真实文件名。\n" + "目录说明填充。 " * 120
    )  # 模拟旧 join 行为：目录附件紧跟文档，伪段落延伸进去后被 masterpiece 过滤放行
    sections = plan_compiler.extract_all_sections(joined)
    assert [n for n, _ in sections] == ["【春·套一】测试套装", "【春·套二】测试套装"]

def test_fill_prompt_sections_目录附件不参与抽取与回填():
    plan = _plan(steps=[PlanStep(
        id="s3", operation="workflow.submit_batch",
        params={"template_id": "a546d311", "url": "http://127.0.0.1:8188",
                "variants": ["【春·套一】测试套装", "【春·套二】测试套装"]})],
        approval_required=["workflow.submit_batch"])
    filled = plan_compiler.fill_prompt_sections(plan, [
        {"name": "形象提示词-唐柚.md", "text": _套装文档()},
        {"name": plan_compiler.LORA_CATALOG_NAME,
         "text": "共 1 个：\n- anima-masterpiece.safetensors（触发词:masterpiece）\n"
                 "禁止写 TO_BE_RESOLVED、{{...}} 或任何占位符。"},
        {"name": plan_compiler.TEMPLATE_CATALOG_NAME,
         "text": "共 1 个：\n- a546d311 Krea2-高清文生图优化流\n禁止写 TO_BE_RESOLVED。"},
        {"name": plan_compiler.RECIPE_CATALOG_NAME,
         "text": "【固化流程预设】《套装文档流程》 id=r1（3 步）意图：看图反推外貌。"
                 "\n禁止写 TO_BE_RESOLVED 占位符。"},
        {"name": plan_compiler.KNOWLEDGE_CATALOG_NAME,
         "text": "【固化知识库】条目命名/constant 判定规范。\n"
                 "【春·套一】在正文说明文字中出现不等于套装标题。"},
    ])
    variants = plan.steps[0].params["variants"]
    assert len(variants) == 2  # 恰好文档真实段落数，无伪变体（目录附件不参与抽取回填）
    assert filled == 2
    for v in variants:
        assert "TO_BE_RESOLVED" not in v["prompt"]
        assert "鞋履" not in v["prompt"] and "袜子纪律" not in v["prompt"]
        assert "固化流程预设" not in v["prompt"] and "固化知识库" not in v["prompt"]
    # 回填后的计划必须过校验闸门（此前正是被 variants[14] 占位符误报拦截）
    errors = plan_validator.validate(
        plan, capabilities=all_capabilities(), configured_models={"chat", "image"})
    assert not errors, errors

def test_fill_prompt_sections_模型直写数量不符时以标题抽取重建():
    # P4 保真：模型直写了 prompt，但变体数 != 文档真实套装数 → 代码以抽取结果为准重建
    plan = _plan(steps=[PlanStep(
        id="s3", operation="workflow.submit_batch",
        params={"template_id": "a546d311", "url": "http://127.0.0.1:8188",
                "variants": [{"name": "春·套一", "prompt": "模型只写了一套"}]})],
        approval_required=["workflow.submit_batch"])
    filled = plan_compiler.fill_prompt_sections(plan, [
        {"name": "形象提示词-唐柚.md", "text": _套装文档()},
    ])
    variants = plan.steps[0].params["variants"]
    assert len(variants) == 2  # 文档真实段数
    assert filled == 2
    assert all("模型只写了一套" not in v["prompt"] for v in variants)
