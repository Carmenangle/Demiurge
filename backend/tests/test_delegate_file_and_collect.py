"""委派扩展单测：file.read_text 越域读取授权链 + media.collect_comfy_outputs 采集闭环。"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from app.services import capability_handlers, capability_registry, capability_sandbox, plan_tasks, repo_meta
from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep


SCHEMA = """
create table plan_tasks (
    id text primary key, repo_id text not null default '', output_dir text not null default '',
    intent text not null default '', plan_json text not null, content_hash text not null,
    status text not null, lease_id text not null default '', error text not null default '',
    result_json text not null default '', created_at integer not null, updated_at integer not null,
    worker_id text not null default '', lease_expires_at integer not null default 0
);
create table plan_task_steps (
    task_id text not null, seq integer not null, step_id text not null, operation text not null,
    params_json text not null default '{}', inputs_from_json text not null default '[]',
    outputs_json text not null default '{}', status text not null default 'pending',
    attempts integer not null default 0, last_error text not null default '',
    updated_at integer not null, primary key (task_id, seq)
);
"""


def _connection_factory(path):
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.executescript(SCHEMA)
    monkeypatch.setattr(plan_tasks, "get_connection", _connection_factory(path))
    progress: dict[str, dict] = {}

    class _FakeProgress:
        @staticmethod
        def load(namespace):
            return dict(progress)

        @staticmethod
        def save(namespace, tasks, limit=100):
            progress.clear()
            progress.update(tasks)

    monkeypatch.setattr(plan_tasks, "task_progress_store", _FakeProgress)
    capability_sandbox._reset_for_tests()
    works = tmp_path / "works"
    works.mkdir()
    yield {"path": path, "progress": progress, "works": works, "outside": tmp_path / "outside"}
    capability_sandbox._reset_for_tests()


def _run_once():
    task = plan_tasks._claim_next()
    if task is not None:
        plan_tasks._run_task(task, threading.Event())


# ── file.read_text ───────────────────────────────────────────────────────────

def test_read_text_handler安全边界(tmp_path):
    doc = tmp_path / "唐柚.md"
    doc.write_text("# 套装一\n提示词内容", encoding="utf-8")
    out = capability_handlers.read_text_file(str(doc))
    assert "套装一" in out["text"] and out["truncated"] is False

    binary = tmp_path / "b.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="UTF-8"):
        capability_handlers.read_text_file(str(binary))

    with pytest.raises(ValueError, match="不存在"):
        capability_handlers.read_text_file(str(tmp_path / "missing.md"))


def test_越域读取需审批_批准后执行(store, monkeypatch):
    outside = store["outside"]
    outside.mkdir()
    doc = outside / "唐柚.md"
    doc.write_text("套装提示词内容", encoding="utf-8")
    plan = GenerationPlan(
        intent="读取设计文档", repo_id="work",
        budgets=PlanBudgets(max_steps=1, max_gpu_tasks=1, max_llm_calls=1),
        steps=[PlanStep(id="s1", operation="file.read_text",
                        params={"path": str(doc)})],
        approval_required=[])
    submitted = plan_tasks.submit_task(plan, output_dir=str(store["works"]),
                                       configured_models={"chat"})
    task_id = submitted["task_id"]
    _run_once()
    task = plan_tasks.get_task(task_id)
    # 越域 readonly 读取：无租约时必须停在待审批（审批卡已明示路径）
    assert task["status"] == "awaiting_approval"
    assert "needs_approval" in task["steps"][0]["last_error"]

    # 批准 → 租约包含该读取路径 → 执行成功且内容进入步骤产出
    plan_tasks.approve_task(task_id)
    _run_once()
    task = plan_tasks.get_task(task_id)
    assert task["status"] == "done"
    assert "套装提示词内容" in task["steps"][0]["outputs"]["text"]


def test_域内读取不需要审批(store, monkeypatch):
    inside = store["works"] / "notes.md"
    inside.write_text("作品域内笔记", encoding="utf-8")
    plan = GenerationPlan(
        intent="读笔记", repo_id="work",
        budgets=PlanBudgets(max_steps=1, max_gpu_tasks=1, max_llm_calls=1),
        steps=[PlanStep(id="s1", operation="file.read_text",
                        params={"path": str(inside)})],
        approval_required=[])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["works"]),
                                     configured_models={"chat"})["task_id"]
    _run_once()
    task = plan_tasks.get_task(task_id)
    assert task["status"] == "done"
    assert "作品域内笔记" in task["steps"][0]["outputs"]["text"]


def test_计划卡明示将读取的文件():
    from app.services import plan_compiler
    plan = GenerationPlan(
        intent="读设计文档出图", repo_id="work",
        budgets=PlanBudgets(max_steps=2, max_gpu_tasks=2, max_llm_calls=2),
        steps=[
            PlanStep(id="s1", operation="file.read_text",
                     params={"path": r"D:\video\寻味电台\形象提示词-唐柚.md"}),
            PlanStep(id="s2", operation="workflow.submit_batch",
                     params={"template_id": "t", "variants": [{}], "prompt": "p",
                             "url": "http://127.0.0.1:8188"}),
        ],
        approval_required=["workflow.submit_batch"])
    card = plan_compiler.render_plan_card(plan, "x.plan.json")
    assert "将读取文件（批准即授权）" in card
    assert "形象提示词-唐柚.md" in card
    md = plan_compiler.render_plan_md(plan)
    assert "将读取文件" in md and "形象提示词-唐柚.md" in md


# ── media.collect_comfy_outputs ──────────────────────────────────────────────

def test_采集闭环_轮询取图落盘入库(store, tmp_path, monkeypatch):
    from app.services import comfyui_client

    polls = {"n": 0}

    def fake_fetch_result(url, prompt_id, filter_node_ids=None):
        polls["n"] += 1
        if polls["n"] == 1:  # 首轮还在跑
            return {"status": "running", "images": [], "videos": [], "audios": [], "texts": []}
        return {"status": "done",
                "images": [{"filename": "out.png", "subfolder": "", "type": "output"}],
                "videos": [], "audios": [], "texts": []}

    def fake_fetch_view(url, filename, type="output", subfolder="", timeout=15):
        return b"\x89PNG fake image bytes", "image/png"

    indexed: list[dict] = []

    def fake_index_generation(repo_id, cfg, prompt, tags="", image_url="", **kwargs):
        indexed.append({"repo_id": repo_id, "prompt": prompt, "tags": tags,
                        "image_url": image_url})

    monkeypatch.setattr(comfyui_client, "fetch_result", fake_fetch_result)
    monkeypatch.setattr(comfyui_client, "fetch_view", fake_fetch_view)
    import app.services.rag_store as rag_store
    monkeypatch.setattr(rag_store, "index_generation", fake_index_generation)

    works = store["works"]
    out = capability_handlers.collect_comfy_outputs(
        prompt_ids=["pid-1"], comfyui_url="http://127.0.0.1:8188",
        output_dir=str(works), repo_id="work", names=["套装一"],
        prompts=["套装一提示词"], timeout_seconds=10)
    assert out["collected"] == 1 and out["results"][0]["ok"] is True
    assert out["results"][0]["rag_indexed"] is True
    # 文件真实落在作品文件夹
    from pathlib import Path
    files = list(Path(works).rglob("*.png"))
    assert files and files[0].stat().st_size > 0
    # 资产库登记挂了套装名提示词与智能编造标签
    assert indexed[0]["prompt"] == "套装一提示词"
    assert "智能编造计划" in indexed[0]["tags"]
    assert indexed[0]["image_url"]


def test_采集闭环_submit整包回退取回每张完整提示词(store, tmp_path, monkeypatch):
    """回归（2026-09-04 唐柚 14 套画像实锤）：collect 收到的 submit_result 是 submit_batch
    整包 {"submitted":…,"results":[{prompt_id, prompt}, …]}，提示词在 results[i] 而非顶层。
    旧回退把整包当单条取 .get("prompt") → 恒空 → 消息只剩套装名、资产库也只挂套装名。
    """
    from app.services import chat_snapshot, comfyui_client

    upserted: list[dict] = []
    monkeypatch.setattr(chat_snapshot, "upsert", lambda _tid, msg: upserted.append(msg))

    def fake_fetch_result(url, prompt_id, filter_node_ids=None):
        return {"status": "done",
                "images": [{"filename": "out.png", "subfolder": "", "type": "output"}],
                "videos": [], "audios": [], "texts": []}

    def fake_fetch_view(url, filename, type="output", subfolder="", timeout=15):
        return b"\x89PNG fake image bytes", "image/png"

    indexed: list[dict] = []

    def fake_index_generation(repo_id, cfg, prompt, tags="", image_url="", **kwargs):
        indexed.append({"repo_id": repo_id, "prompt": prompt, "tags": tags,
                        "image_url": image_url})

    monkeypatch.setattr(comfyui_client, "fetch_result", fake_fetch_result)
    monkeypatch.setattr(comfyui_client, "fetch_view", fake_fetch_view)
    import app.services.rag_store as rag_store
    monkeypatch.setattr(rag_store, "index_generation", fake_index_generation)

    works = store["works"]
    full_prompt_1 = "1girl, 唐柚, 浅杏短款绗缝棉服, masterpiece, best quality"
    full_prompt_2 = "1girl, 唐柚, 米白针织开衫, masterpiece, best quality"
    out = capability_handlers.collect_comfy_outputs(
        prompt_ids=None, comfyui_url="http://127.0.0.1:8188",
        output_dir=str(works), repo_id="work",
        names=["唐柚-棉袄套一", "唐柚-棉袄套二"],
        prompts=None,
        submit_result={
            "submitted": 2, "failed": 0,
            "results": [
                {"index": 0, "ok": True, "prompt_id": "pid-1", "prompt": full_prompt_1},
                {"index": 1, "ok": True, "prompt_id": "pid-2", "prompt": full_prompt_2},
            ],
        },
        timeout_seconds=10)
    assert out["collected"] == 2
    # 消息 = 套装名 + 完整提示词（像 /w 工作流「图片+提示词」格式），而不是只有套装名
    assert len(upserted) == 2
    assert upserted[0]["text"] == f"唐柚-棉袄套一\n\n{full_prompt_1}"
    assert upserted[1]["text"] == f"唐柚-棉袄套二\n\n{full_prompt_2}"
    assert upserted[0]["image"] and upserted[0]["meta"]["kind"] == "plan_collect"
    # 资产库登记同样挂完整提示词而非套装名
    assert indexed[0]["prompt"] == full_prompt_1
    assert indexed[1]["prompt"] == full_prompt_2


def test_lora模糊解析与提交归一(monkeypatch, tmp_path):
    """LoRA 模糊匹配与 submit 归一，全部走临时触发词库，不依赖真机数据/网络。"""
    from app.services import capability_handlers as ch, comfyui_client, lora_index

    # 临时 lora_triggers 库（沿用 test_lora_triggers 的封闭模式）
    path = tmp_path / "lora.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "create table lora_triggers ("
            " lora_name text primary key, triggers text not null default '',"
            " note text not null default '', suggested_weight real not null default 0.8,"
            " suggested_prompt text not null default '', source text not null default '',"
            " missing integer not null default 0, updated_at integer not null)"
        )
        connection.execute(
            "insert into lora_triggers (lora_name, triggers, suggested_weight, source,"
            " updated_at) values (?, ?, ?, 'manual', 0)",
            ("krea2_QRQ_韩漫风.safetensors", "QRQ,韩漫风", 0.7),
        )

    def _temp_connection():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(lora_index, "get_connection", _temp_connection)
    # ComfyUI 置离线：枚举只来自临时库，真机是否在线不影响结果
    monkeypatch.setattr(comfyui_client, "fetch_object_info",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))

    # 「QRQ 风格」应命中 krea2_QRQ_韩漫风（token 交叉 + 触发词）
    hit = ch.lora_resolve("QRQ 风格")
    assert hit["matched"] and "QRQ" in hit["file"].upper()
    assert hit["suggested_weight"] == 0.7

    # 未匹配 → 保留原值不猜
    miss = ch.lora_resolve("完全不存在的lora-xyz")
    assert miss["matched"] is False and miss["candidates"]

    # submit 归一：近似名→真实文件 + 建议权重自动补 strength
    values = {"lora_name": "QRQ 风格"}
    ch._resolve_lora_in_values(values)
    assert values["lora_name"].endswith(".safetensors")
    assert values["strength_model"] == hit["suggested_weight"]


def test_plans路由output_dir必须来自配置真源():
    # F3 回归：客户端不能指定任意目录撑大路径域校验
    from fastapi import HTTPException
    from app.routers import plans
    import pytest
    with pytest.raises(HTTPException) as ei:
        plans._trusted_output_dir(r"C:\Windows")
    assert ei.value.status_code == 400
    truth = repo_meta.output_dir_from_state()
    if truth:  # 配置了仓库文件夹时应通过
        assert plans._trusted_output_dir(truth) == truth


def test_collect嵌入配置来自user_state不经模型参数():
    # F1 回归：collect schema 不含 embed_* 参数（密钥不进计划文档）；配置从 user_state 读
    cap = capability_registry.get("media.collect_comfy_outputs")
    assert "embed_base" not in (cap.params_schema.get("properties") or {})
    base, key, model = capability_handlers._embed_config_from_state()
    assert isinstance(base, str) and isinstance(key, str) and isinstance(model, str)
