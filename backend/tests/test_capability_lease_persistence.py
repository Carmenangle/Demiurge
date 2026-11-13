"""租约持久化的原子性与真实失败语义（2026-11-05 T2）。

实锤的旧缺陷：`_save_persisted` 吞 OSError 且 `revoke` 无论落盘成败都返回 True，
grant/renew 同样在「盘面根本没写成功」时返回成功——用户以为批准/撤销生效，
后端一重载就翻脸。本文件锁四条合同：
① 落盘失败一律抛出，内存不留半提交状态，盘面逐字节不变；
② 变更（复制 → 改副本 → 落盘 → 换内存）全在同一把锁内，并发不丢租约；
③ 返回成功的撤销在重载后仍然被拒；
④ 原子替换失败不遗留临时文件。
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from app.services import capability_sandbox


def _lease_file() -> os.PathLike:
    return capability_sandbox.LEASE_FILE


def _disk_leases() -> list[dict]:
    return json.loads(_lease_file().read_text(encoding="utf-8"))


def _fail_replace(monkeypatch) -> None:
    """注入「写完临时文件、发布替换时失败」——原子性合同里最危险的那一步。"""
    def _boom(*_args, **_kwargs):
        raise OSError("注入：租约盘面写入失败")
    monkeypatch.setattr(capability_sandbox.os, "replace", _boom)


def _make_lease(subject: str = "s:base") -> dict:
    return capability_sandbox.grant(
        subject, [{"operation": "file.write_text", "path": ""}],
        mode=capability_sandbox.ACCESS_APPROVAL)


def test_grant写盘失败_不返回成功且内存与盘面一致(monkeypatch):
    base = _make_lease()
    before = _lease_file().read_text(encoding="utf-8")
    _fail_replace(monkeypatch)

    with pytest.raises(OSError, match="租约盘面写入失败"):
        _make_lease("s:new")

    assert _lease_file().read_text(encoding="utf-8") == before, "失败不得动盘面"
    assert [item["id"] for item in _disk_leases()] == [base["id"]]
    assert {item["subject"] for item in capability_sandbox.active()} == {"s:base"}, \
        "落盘失败不得在内存留下半提交租约"


def test_grant_operation写盘失败_授权清单不变(monkeypatch):
    lease = _make_lease()
    before = _lease_file().read_text(encoding="utf-8")
    _fail_replace(monkeypatch)

    with pytest.raises(OSError):
        capability_sandbox.grant_operation(lease["id"], "workflow.submit_batch", path="x")

    assert _lease_file().read_text(encoding="utf-8") == before
    live = capability_sandbox.active("s:base")[0]
    assert [c["operation"] for c in live["capabilities"]] == ["file.write_text"], \
        "追加授权没落住就不能留在内存里"


def test_renew写盘失败_到期时间不变(monkeypatch):
    lease = _make_lease()
    before = _lease_file().read_text(encoding="utf-8")
    old_expires = float(lease["expires_at"])
    _fail_replace(monkeypatch)

    with pytest.raises(OSError):
        capability_sandbox.renew(lease["id"], ttl_seconds=86400, now=lambda: 9e9)

    assert _lease_file().read_text(encoding="utf-8") == before
    assert float(capability_sandbox.active("s:base")[0]["expires_at"]) == old_expires


def test_revoke写盘失败_报告失败且不谎称已撤销(monkeypatch):
    """撤销失败的后果必须是「用户知道没撤销成」，而不是「返回成功但权限复活」。"""
    lease = _make_lease()
    before = _lease_file().read_text(encoding="utf-8")
    _fail_replace(monkeypatch)

    with pytest.raises(OSError):
        capability_sandbox.revoke(lease["id"])

    assert _lease_file().read_text(encoding="utf-8") == before
    assert capability_sandbox.lease_registered(lease["id"]) is True
    # 授权仍在（与已如实报告的失败一致）：重载后也不得出现「界面说取消了、执行仍放行」
    capability_sandbox._LEASES.clear()
    capability_sandbox._load_persisted()
    capability_sandbox.authorize(lease["id"], "file.write_text")


def test_revoke成功后重载仍拒绝():
    """§2.3 验收主断言：任何返回成功的撤销，重载后必须仍然拒绝。"""
    lease = _make_lease()
    assert capability_sandbox.revoke(lease["id"]) is True

    capability_sandbox._LEASES.clear()
    capability_sandbox._load_persisted()

    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], "file.write_text")


def test_失败替换不遗留临时文件(monkeypatch):
    _make_lease()
    _fail_replace(monkeypatch)
    with pytest.raises(OSError):
        _make_lease("s:tmp")
    assert list(_lease_file().parent.glob("*.tmp")) == []
    assert list(_lease_file().parent.glob(f".{ _lease_file().name }*")) == []


def test_成功落盘不留临时文件():
    _make_lease()
    assert list(_lease_file().parent.glob("*.tmp")) == []
    assert _lease_file().is_file()


def test_盘面是合法json_逐条租约可读():
    first = _make_lease("s:a")
    second = _make_lease("s:b")
    items = _disk_leases()
    assert {item["id"] for item in items} == {first["id"], second["id"]}


def test_落盘发生在持有租约锁期间(monkeypatch):
    """并发不丢租约的**结构性**前提：序列化 + 写盘全程持锁。

    锁外落盘 = 「A 先算 payload、B 后提交、A 最后写」⇒ 盘面用旧快照覆盖掉 B 刚签的租约
    （内存却有，重启就没了）。这条断言不依赖线程调度，直接锁形态。
    """
    seen: list[bool] = []
    real_save = capability_sandbox._save_persisted

    def _probe(state=None):
        seen.append(capability_sandbox._LOCK.locked())
        return real_save(state)

    monkeypatch.setattr(capability_sandbox, "_save_persisted", _probe)
    _make_lease()
    assert seen == [True], "落盘必须在 _LOCK 内发生"


def test_并发变更不丢另一线程刚提交的租约(monkeypatch):
    """可控屏障：A 卡在「发布替换」里等 B 落盘。

    旧形态（先改内存、再在**锁外**按当时的 _LEASES 序列化写盘）：B 能插进 A 的写窗口，
    A 随后用只含自己的旧快照覆盖盘面 ⇒ B 的租约在盘上消失。
    新形态：落盘与内存替换同锁 ⇒ B 只能排在 A 之后，盘面终态含两条。
    """
    a_in_replace = threading.Event()
    b_written = threading.Event()
    state = {"b_interleaved": None, "errors": []}
    real_replace = os.replace

    def _guarded_replace(src, dst, *args, **kwargs):
        if threading.current_thread().name == "lease-A":
            # 等得到 = B 真插进了 A 的写窗口（旧形态）；等不到 = 同锁串行（新形态）
            a_in_replace.set()
            state["b_interleaved"] = b_written.wait(timeout=1.0)
            return real_replace(src, dst, *args, **kwargs)
        result = real_replace(src, dst, *args, **kwargs)
        b_written.set()
        return result

    monkeypatch.setattr(capability_sandbox.os, "replace", _guarded_replace)

    def _grant(subject: str) -> None:
        try:
            capability_sandbox.grant(
                subject, [{"operation": "file.write_text", "path": ""}],
                mode=capability_sandbox.ACCESS_APPROVAL)
        except Exception as exc:  # noqa: BLE001 - 线程内异常要带回主线程
            state["errors"].append(exc)

    thread_a = threading.Thread(target=_grant, args=("s:concurrent-a",), name="lease-A")
    thread_b = threading.Thread(target=_grant, args=("s:concurrent-b",), name="lease-B")
    thread_a.start()
    assert a_in_replace.wait(timeout=5), "A 未进入落盘窗口"
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert state["errors"] == []
    assert state["b_interleaved"] is False, "B 不得在 A 的落盘窗口里并发写盘"
    subjects = {item["subject"] for item in _disk_leases()}
    assert subjects >= {"s:concurrent-a", "s:concurrent-b"}, \
        f"并发提交不得丢租约，实际盘面只剩 {subjects}"


def test_重复授权不落盘_租约不变(monkeypatch):
    """`changed=False` 的短路：重复追加同一条授权不产生磁盘写（也就能在盘面只读时成功）。"""
    lease = _make_lease()
    before = _lease_file().read_text(encoding="utf-8")
    _fail_replace(monkeypatch)

    result = capability_sandbox.grant_operation(lease["id"], "file.write_text", path="")

    assert result["id"] == lease["id"], "已存在的授权不该被写盘失败拦死"
    assert _lease_file().read_text(encoding="utf-8") == before
