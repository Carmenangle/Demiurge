import pytest

from app.services import capability_sandbox, procedure_skills, scenario_lab


def test_capability_is_scoped_and_revocable(tmp_path):
    capability_sandbox._reset_for_tests()
    lease = capability_sandbox.grant(
        "procedure:test", [{"operation": "scenario.snapshot", "path": str(tmp_path)}],
    )
    assert capability_sandbox.authorize(
        lease["id"], "scenario.snapshot", path=str(tmp_path / "repo"),
    )["subject"] == "procedure:test"
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], "workflow.submit", path=str(tmp_path))
    capability_sandbox.revoke(lease["id"])
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], "scenario.snapshot", path=str(tmp_path))


def test_full租约放行任意操作且可撤销(tmp_path):
    capability_sandbox._reset_for_tests()
    lease = capability_sandbox.grant(
        "plan:full", [], ttl_seconds=600, approved_by="full_mode",
        mode=capability_sandbox.ACCESS_FULL)
    assert capability_sandbox.authorize(
        lease["id"], "file.write", path=str(tmp_path / "anywhere"),
    )["mode"] == capability_sandbox.ACCESS_FULL
    assert capability_sandbox.authorize(
        lease["id"], "workflow.submit_batch", path="D:\\elsewhere",
    )["subject"] == "plan:full"
    capability_sandbox.revoke(lease["id"])
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], "file.write", path=str(tmp_path))


def test_approval租约空capabilities拒绝而full放行():
    capability_sandbox._reset_for_tests()
    with pytest.raises(ValueError):
        capability_sandbox.grant("plan:empty", [])
    lease = capability_sandbox.grant(
        "plan:full", [], mode=capability_sandbox.ACCESS_FULL)
    assert lease["capabilities"] == [{"operation": "*", "path": "", "domain": "", "tool": ""}]
    capability_sandbox.authorize(lease["id"], "any.op")


def test_failure_summary从trace统计失败模式(monkeypatch):
    monkeypatch.setattr(procedure_skills.run_trace, "read_recent", lambda *_a, **_k: [
        {"event": "plan.step_failed", "data": {"operation": "test.batch", "error": "上游 502"}},
        {"event": "plan.step_failed", "data": {"operation": "test.batch", "error": "上游 502"}},
        {"event": "plan.step_blocked", "data": {"operation": "file.write_text", "reason": "write_loop"}},
    ])
    summary = procedure_skills.failure_summary("repo", turn_id="t1")
    assert summary["total"] == 3
    assert summary["failures"]["test.batch"]["count"] == 2
    assert summary["failures"]["test.batch"]["reasons"] == ["上游 502"]
    assert summary["failures"]["file.write_text"]["reasons"] == ["write_loop"]


def test_procedure_requires_review_adapter_and_capability(monkeypatch, tmp_path):
    capability_sandbox._reset_for_tests()
    monkeypatch.setattr(procedure_skills, "STORE", tmp_path / "procedures.json")
    monkeypatch.setattr(procedure_skills.run_trace, "read_recent", lambda *_args, **_kwargs: [
        {"event": "scenario.snapshot", "data": {"repo_id": "repo"}},
    ])
    monkeypatch.setattr(scenario_lab, "create_snapshot", lambda *args, **kwargs: {
        "snapshot_id": "snap", "repo_id": args[1],
    })
    item = procedure_skills.propose("repo", "turn", "保存现场")
    assert not procedure_skills.dry_run(item["id"], {"output_dir": str(tmp_path)})["executable"]
    procedure_skills.review(item["id"], item["steps"], approved=True)
    lease = capability_sandbox.grant(
        f"procedure:{item['id']}",
        [{"operation": "scenario.snapshot", "path": str(tmp_path)}],
    )
    result = procedure_skills.execute(item["id"], lease["id"], {"output_dir": str(tmp_path)})
    assert result["results"][0]["result"]["snapshot_id"] == "snap"
