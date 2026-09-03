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


def test_文档交付委派不误判():
    # 无文档交付动作（纯指图）不抢图生图/反推
    assert not plan_compiler.is_doc_delegation_intent("批量处理这些图")
    assert not plan_compiler.is_doc_delegation_intent("看看这篇文档写了什么")
    # 疑问句不委派
    assert not plan_compiler.is_doc_delegation_intent("能帮我整理成文档吗？")
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
