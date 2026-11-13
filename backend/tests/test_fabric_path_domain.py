"""fabric 执行期路径域闸（2026-11-05 T1）。

合同：机械计划已有的「params 里的绝对路径必须落在作品域内」同样约束自由循环——
full 不再是越域写的旁路，approval 的越域**读取**逐路径审批。

探针口径：只用 tmp_path，不执行真实 shell 命令、不碰真实租约文件
（租约文件由 conftest 的 autouse fixture 重定向到临时目录）。
"""
from __future__ import annotations

import json

import pytest

from app.services import capability_registry, capability_sandbox, fabric_loop


def _fake_chat(decisions):
    calls = {"n": 0}

    def fake(base, key, model, system, user, **kw):
        idx = calls["n"]
        calls["n"] += 1
        return json.dumps(decisions[min(idx, len(decisions) - 1)], ensure_ascii=False)
    return fake


def _run(decisions, *, access_mode, lease_id, output_dir, max_steps=4):
    return fabric_loop.run_loop(
        intent="路径域用例", configured_models={"chat"},
        access_mode=access_mode, lease_id=lease_id, output_dir=output_dir,
        chat_base="", chat_key="", chat_model="m",
        chat_fn=_fake_chat(decisions), max_steps=max_steps)


def _approval_lease(name: str) -> dict:
    return capability_sandbox.grant(
        f"fabric:{name}", [], mode=capability_sandbox.ACCESS_APPROVAL)


def _full_lease(name: str) -> dict:
    return capability_sandbox.grant(
        f"fabric:{name}", [], mode=capability_sandbox.ACCESS_FULL)


# ── 写：越域一律拒绝（两种访问标准同口径）──────────────────────────────────


def test_full模式写作品域外被拒且文件不落盘(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "域外.txt"
    lease = _full_lease("write-outside")
    outcome = _run([
        {"tool": "file.write_text",
         "params": {"path": str(outside), "content": "不该存在"}},
        {"done": True, "reply": "已尝试"},
    ], access_mode=capability_sandbox.ACCESS_FULL, lease_id=lease["id"],
        output_dir=str(work))
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is False
    assert "越出作品域" in outcome.steps[0]["error"]
    assert not outside.exists(), "full 模式下越域写必须落不了盘"
    capability_sandbox.revoke(lease["id"])


def test_approval模式写作品域外同样被拒(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "域外.txt"
    lease = _approval_lease("approval-write-outside")
    # 即便用户批准过该能力（授权域=作品根），越域写也不给「批准」这条路
    capability_sandbox.grant_operation(lease["id"], "file.write_text", path=str(work))
    outcome = _run([
        {"tool": "file.write_text",
         "params": {"path": str(outside), "content": "不该存在"}},
        {"done": True, "reply": "已尝试"},
    ], access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id=lease["id"],
        output_dir=str(work))
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is False
    assert "越出作品域" in outcome.steps[0]["error"]
    assert not outside.exists()
    capability_sandbox.revoke(lease["id"])


@pytest.mark.parametrize("path_value", [
    lambda work, tmp: str(tmp / "work-other" / "x.txt"),      # 相似前缀目录
    lambda work, tmp: str(work.parent / ".." / "escape.txt"),  # 上跳越域
    lambda work, tmp: str(tmp / "绝对域外.txt"),
])
def test_相似前缀与上跳路径不被误放行(tmp_path, path_value):
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "work-other").mkdir()
    lease = _full_lease("tricky-paths")
    target = path_value(work, tmp_path)
    outcome = _run([
        {"tool": "file.write_text", "params": {"path": target, "content": "x"}},
        {"done": True, "reply": "已尝试"},
    ], access_mode=capability_sandbox.ACCESS_FULL, lease_id=lease["id"],
        output_dir=str(work))
    assert "越出作品域" in outcome.steps[0]["error"], target
    assert not (tmp_path / "work-other" / "x.txt").exists()
    capability_sandbox.revoke(lease["id"])


def test_域内写含上跳写法仍放行(tmp_path):
    """`work/sub/../in.txt` 解析后仍在作品域内 ⇒ 不得误拦（日常模型常这么写）。"""
    work = tmp_path / "作品"
    (work / "sub").mkdir(parents=True)
    lease = _approval_lease("inside-dotdot")
    capability_sandbox.grant_operation(lease["id"], "file.write_text", path=str(work))
    target = work / "sub" / ".." / "in.txt"
    outcome = _run([
        {"tool": "file.write_text", "params": {"path": str(target), "content": "域内"}},
        {"done": True, "reply": "写好了"},
    ], access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id=lease["id"],
        output_dir=str(work))
    assert outcome.steps[0]["ok"] is True, outcome.steps[0]
    assert (work / "in.txt").read_text(encoding="utf-8") == "域内"
    capability_sandbox.revoke(lease["id"])


def test_shell的cwd由环境归一_模型传越域值也不会落到域外(tmp_path):
    """`project.run_shell` 的 cwd 属 `ENV_INJECTED_OPS`：模型传 `/` 会被覆盖成作品根，
    因此**执行期闸门看到的是归一后的值**（闸门前置到归一之后才不误伤既有链路）。
    这里不执行任何真实命令，只锁「归一后不再有越域写路径」。
    """
    work = tmp_path / "作品"
    work.mkdir()
    model_params = {"command": "echo demiurge", "cwd": str(tmp_path.parent)}
    capability_registry.inject_env_params(
        model_params, "project.run_shell", output_dir=str(work), repo_id="r")
    assert model_params["cwd"] == str(work), "cwd 必须由环境归一（模型传值作废）"
    denied, reads = fabric_loop._path_domain_check(
        "project.run_shell", model_params, level="durable", output_dir=str(work))
    assert (denied, reads) == ([], [])


def test_shell的cwd真越域时仍按写路径拒绝(tmp_path):
    """闸门本身不认识「哪个参数会被注入」——给它越域的 cwd 必须判拒（fail-closed）。"""
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "outside_root"
    outside.mkdir()
    target = str(outside / "x")
    denied, reads = fabric_loop._path_domain_check(
        "project.run_shell", {"command": "echo x", "cwd": target},
        level="durable", output_dir=str(work))
    assert denied == [target]
    assert reads == []


# ── 读：approval 逐路径审批，full 维持完全访问──────────────────────────────


def test_approval模式越域读取先待批且零读取结果(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "域外.txt"
    outside.write_text("机密正文不该进上下文", encoding="utf-8")
    lease = _approval_lease("read-outside")
    outcome = _run([
        {"tool": "file.read_text", "params": {"path": str(outside)}},
    ], access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id=lease["id"],
        output_dir=str(work))
    assert outcome.status == "awaiting_approval"
    assert outcome.pending_tool == "file.read_text"
    assert outcome.pending_paths == [str(outside)]
    assert "机密正文" not in json.dumps(outcome.messages, ensure_ascii=False), \
        "待批前不得把域外文件内容回填进对话"
    capability_sandbox.revoke(lease["id"])


def test_approval模式越域读取按实际路径批准后返回内容(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "域外.txt"
    outside.write_text("授权后可见", encoding="utf-8")
    lease = _approval_lease("read-outside-resume")
    first = _run([
        {"tool": "file.read_text", "params": {"path": str(outside)}},
    ], access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id=lease["id"],
        output_dir=str(work))
    assert first.status == "awaiting_approval"

    # 批准 = 对该确切路径追加授权（grant 作品根不够：作品根覆盖不到域外文件）
    capability_sandbox.grant_operation(
        lease["id"], "file.read_text", path=str(outside))
    resume_messages = list(first.messages) + [{
        "role": "user",
        "content": json.dumps({"approval_granted": "用户已批准该读取"}, ensure_ascii=False)}]
    second = fabric_loop.run_loop(
        intent="路径域用例", configured_models={"chat"},
        access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id=lease["id"],
        output_dir=str(work), chat_base="", chat_key="", chat_model="m",
        chat_fn=_fake_chat([
            {"tool": "file.read_text", "params": {"path": str(outside)}},
            {"done": True, "reply": "读完了"},
        ]),
        max_steps=4, resume={"messages": resume_messages, "steps": first.steps})
    assert second.status == "done"
    assert second.steps[0]["ok"] is True
    assert "授权后可见" in json.dumps(second.steps[0]["result"], ensure_ascii=False)
    capability_sandbox.revoke(lease["id"])


def test_approval模式域内读取不新增审批停顿(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    inside = work / "note.txt"
    inside.write_text("域内正文", encoding="utf-8")
    lease = _approval_lease("read-inside")
    outcome = _run([
        {"tool": "file.read_text", "params": {"path": str(inside)}},
        {"done": True, "reply": "读完了"},
    ], access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id=lease["id"],
        output_dir=str(work))
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is True
    capability_sandbox.revoke(lease["id"])


def test_full模式越域读取维持既定完全访问(tmp_path):
    """刻意差异（不是遗漏）：full = 用户显式开启的完全访问，越域只读不逐项停。"""
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "域外.txt"
    outside.write_text("full 可见", encoding="utf-8")
    lease = _full_lease("read-outside-full")
    outcome = _run([
        {"tool": "file.read_text", "params": {"path": str(outside)}},
        {"done": True, "reply": "读完了"},
    ], access_mode=capability_sandbox.ACCESS_FULL, lease_id=lease["id"],
        output_dir=str(work))
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is True
    capability_sandbox.revoke(lease["id"])


def test_未声明作品域时不做路径闸(tmp_path):
    """output_dir 为空 = 计划未声明作品域 ⇒ 与 plan_validator 计划期词法检查同口径。"""
    lease = _approval_lease("no-domain")
    outcome = _run([
        {"tool": "file.list_dir", "params": {"path": str(tmp_path)}},
        {"done": True, "reply": "完成"},
    ], access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id=lease["id"],
        output_dir="")
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is True
    capability_sandbox.revoke(lease["id"])


# ── 分类：写着陆参数 vs 读取源参数（单一属主 = capability_registry）────────


def test_写读参数分类不混同(tmp_path):
    """import_source / migrate_mechanical 的 path 是**读取源**（外部卡文件），
    不能当写路径拒绝；写着陆目录由 base/配置真源决定。"""
    work = tmp_path / "作品"
    work.mkdir()
    source = tmp_path / "外部卡.png"
    denied, reads = fabric_loop._path_domain_check(
        "character.import_source", {"path": str(source)},
        level="durable", output_dir=str(work))
    assert denied == []
    assert reads == [str(source)]
    target = tmp_path / "x.txt"
    denied, reads = fabric_loop._path_domain_check(
        "file.write_text", {"path": str(target), "content": "x"},
        level="durable", output_dir=str(work))
    assert denied == [str(target)]
    assert reads == []


def test_读源登记表只登记真正的读取源():
    """durable/expensive 里**只有**登记过的路径参数才按「越域可读」处理，
    其余一律按写路径 fail-closed——新增「读外部文件」型能力必须显式登记，
    否则会被判越域写（红在本条，而不是静默放过）。"""
    for op, keys in capability_registry.READ_PATH_PARAMS.items():
        cap = capability_registry.get(op)
        assert cap is not None, op
        assert cap.side_effect_level in ("durable", "expensive"), op
        declared = set((cap.params_schema or {}).get("properties") or {})
        assert set(keys) <= declared, (op, keys)
    # 写侧不靠登记表兜底：未登记的 path 参数默认按写处理
    assert capability_registry.read_path_params("file.write_text") == ()
    assert capability_registry.read_path_params("nope.unknown_op") == ()
