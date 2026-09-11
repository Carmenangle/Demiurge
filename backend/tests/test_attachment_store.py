"""对话附件回归测试（2026-09-01 定案 A1+B1+C1增强+D1）。

覆盖：存储层落盘/寻址/删除、文本提取（纯文本/docx/xlsx/PDF 降级/二进制）、
文件参考段落生成、REST 端点、agent 链路透传与 user_text 注入、维护清理连带。
"""
import io
import zipfile

import pytest

from app.services import attachment_store
from app.services.agent_request_context import from_payload
from app.services.agent_contracts import RunContext


# ---------- 存储层 ----------

def test_save_upload_returns_meta_and_roundtrip(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    meta = attachment_store.save_upload("thread-1", "计划.md", "text/markdown", "你好".encode("utf-8"))
    assert meta["file_id"] and len(meta["file_id"]) == 32
    assert meta["name"] == "计划.md"
    assert meta["mime"] == "text/markdown"
    assert meta["size"] == len("你好".encode("utf-8"))

    path = attachment_store.resolve(meta["file_id"])
    assert path is not None and path.exists()
    assert path.name.startswith(meta["file_id"] + "-")
    assert attachment_store.read_bytes(meta["file_id"]) == "你好".encode("utf-8")


def test_save_upload_rejects_oversize(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    monkeypatch.setattr(attachment_store, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValueError, match="文件过大"):
        attachment_store.save_upload("t", "big.bin", "", b"x" * 11)


def test_resolve_rejects_path_traversal_and_bad_ids(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    assert attachment_store.resolve("../../etc/passwd") is None
    assert attachment_store.resolve("short") is None
    assert attachment_store.resolve("A" * 32) is None  # 大写 hex 也拒绝（小写校验）


def test_delete_thread_removes_all_and_idempotent(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    attachment_store.save_upload("t1", "a.md", "", b"a")
    attachment_store.save_upload("t1", "b.txt", "", b"b")
    attachment_store.save_upload("t2", "c.md", "", b"c")

    assert attachment_store.delete_thread("t1") == 2
    assert attachment_store.delete_thread("t1") == 0  # 幂等
    assert (root / "t1").exists() is False
    assert (root / "t2").is_dir()  # 其他会话不受影响


# ---------- 文本提取 ----------

def test_extract_text_plain_utf8_and_truncation(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    meta = attachment_store.save_upload("t", "readme.txt", "text/plain", "abc你好def".encode("utf-8"))
    assert attachment_store.extract_text(meta["file_id"], meta["name"]) == "abc你好def"

    long = attachment_store.save_upload("t", "long.md", "", ("x" * 500).encode())
    text = attachment_store.extract_text(long["file_id"], long["name"], max_chars=100)
    assert len(text) == 100


def test_extract_docx_uses_word_document(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", '<?xml version="1.0"?><w:document><w:body>'
                                          "<w:p><w:r><w:t>第一段 &amp; 符号</w:t></w:r></w:p>"
                                          "</w:body></w:document>")
    meta = attachment_store.save_upload("t", "doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                        buf.getvalue())
    text = attachment_store.extract_text(meta["file_id"], meta["name"])
    assert text == "第一段 & 符号"


def test_extract_xlsx_uses_shared_strings(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst><si><t>单元格甲</t></si>'
                                            "<si><t>单元格乙</t></si></sst>")
    meta = attachment_store.save_upload("t", "book.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                        buf.getvalue())
    text = attachment_store.extract_text(meta["file_id"], meta["name"])
    assert "单元格甲" in text and "单元格乙" in text


def test_extract_bad_zip_degrades_to_none(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    meta = attachment_store.save_upload("t", "broken.docx", "", b"not a zip at all")
    assert attachment_store.extract_text(meta["file_id"], meta["name"]) is None


def test_extract_pdf_missing_lib_degrades(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    # pypdf 缺失或导入失败 → None（可选能力降级，不阻断）
    def _no_pypdf():
        raise ImportError("no pypdf")
    monkeypatch.setattr(attachment_store, "_extract_pdf", lambda path: None)
    meta = attachment_store.save_upload("t", "paper.pdf", "application/pdf", b"%PDF-1.4 fake")
    assert attachment_store.extract_text(meta["file_id"], meta["name"]) is None


def test_binary_extract_returns_none(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    meta = attachment_store.save_upload("t", "model.safetensors", "application/octet-stream", b"\x00\x01binary")
    assert attachment_store.extract_text(meta["file_id"], meta["name"]) is None


# ---------- 文件参考段落 ----------

def test_file_reference_block_text_embeds_full_text(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    meta = attachment_store.save_upload("t", "计划.md", "text/markdown", "核心计划：先做附件。".encode("utf-8"))
    block = attachment_store.file_reference_block(meta)
    assert "【文件参考：计划.md】" in block
    assert "核心计划：先做附件。" in block
    assert "【文件参考结束：计划.md】" in block


def test_file_reference_block_binary_only_meta(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    meta = attachment_store.save_upload("t", "clip.mp4", "video/mp4", b"\x00\x01")
    block = attachment_store.file_reference_block(meta)
    assert "【文件附件：clip.mp4】" in block
    assert "video/mp4" in block
    assert "clip.mp4" in block
    assert "无法直接阅读全文" in block


def test_file_reference_blocks_empty_and_mixed(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    assert attachment_store.file_reference_blocks(None) == []
    assert attachment_store.file_reference_blocks([]) == []
    txt = attachment_store.save_upload("t", "a.txt", "text/plain", "x".encode())
    bin_ = attachment_store.save_upload("t", "b.zip", "application/zip", b"\x00")
    blocks = attachment_store.file_reference_blocks([txt, bin_])
    assert len(blocks) == 2
    assert "【文件参考：a.txt】" in blocks[0]
    assert "【文件附件：b.zip】" in blocks[1]


# ---------- agent 链路透传 ----------

def test_from_payload_carries_attachments():
    metas = [{"file_id": "a" * 32, "name": "x.md", "mime": "text/markdown", "size": 10}]
    context = from_payload({"thread_id": "t", "message": "看看", "attachments": metas})
    assert context.attachments == metas
    assert context._legacy()["attachments"] == metas


def test_from_payload_missing_attachments_defaults_empty():
    context = from_payload({"thread_id": "t", "message": "看看"})
    assert context.attachments == []


def test_run_context_defaults_empty():
    ctx = RunContext(thread_id="t", message="m")
    assert ctx.attachments == []
    assert ctx._legacy()["attachments"] == []


def test_agent_state_declares_attachments():
    from app.services import agent_graph as ag
    assert "attachments" in ag.AgentState.__annotations__


def _fake_graph(captured: dict):
    """返回一个记录 init 的假图；stream 签名对齐真实调用（init, config）。"""
    class _G:
        def stream(self, init, config=None):
            captured["init"] = init
            return iter(())
    return _G()


def _stub_graph_deps(monkeypatch, captured: dict) -> None:
    """替换 stream_multi_agent 的外部依赖，保证只测注入逻辑。"""
    from app.services import agent_graph as ag
    monkeypatch.setattr(ag, "_graph", lambda: _fake_graph(captured))
    monkeypatch.setattr(ag, "_resolve_agent_cfg", lambda *a, **k: {"id": "roleplay"})
    monkeypatch.setattr(ag, "_resolve_skills", lambda *a, **k: [])
    monkeypatch.setattr(ag, "_apply_work_persona", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_handle_pending_approval", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_has_mcp", lambda *a, **k: False)
    monkeypatch.setattr(ag, "run_trace", type("_T", (), {"emit": staticmethod(lambda *a, **k: None)})())


def test_stream_multi_agent_injects_reference_blocks_into_user_text(monkeypatch, tmp_path):
    """C1 增强核心：附件参考段落进 user_text（初始 state），context.message 保持原始输入。"""
    from app.services import agent_graph as ag
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    meta = attachment_store.save_upload("t", "设定.md", "text/markdown", "世界观：蒸汽朋克。".encode("utf-8"))

    captured = {}
    _stub_graph_deps(monkeypatch, captured)
    ctx = RunContext(thread_id="t", message="分析这份设定", attachments=[meta])
    list(ag.stream_multi_agent(ctx))

    init = captured["init"]
    assert init["attachments"] == [meta]
    assert "分析这份设定" in init["user_text"]
    assert "【文件参考：设定.md】" in init["user_text"]
    assert "世界观：蒸汽朋克。" in init["user_text"]
    # 历史真源不被污染：context.message 仍是原始输入
    assert ctx.message == "分析这份设定"


def test_stream_multi_agent_without_attachments_keeps_message_clean(monkeypatch):
    from app.services import agent_graph as ag
    captured = {}
    _stub_graph_deps(monkeypatch, captured)
    ctx = RunContext(thread_id="t", message="继续")
    list(ag.stream_multi_agent(ctx))
    assert captured["init"]["user_text"] == "继续"


# ---------- 维护清理连带（B1） ----------

def test_clear_cache_deletes_thread_attachments(monkeypatch, tmp_path):
    from app.services import chat_maintenance
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    attachment_store.save_upload("repo-x", "a.txt", "text/plain", b"x")

    deleted = []
    monkeypatch.setattr(attachment_store, "delete_thread",
                        lambda tid: deleted.append(tid) or 1)
    monkeypatch.setattr(chat_maintenance.agent_runner, "is_running", lambda tid: False)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda tid: [])
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda tid, snap: None)
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda tid: None)

    chat_maintenance.clear_cache("repo-x", "")
    assert deleted == ["repo-x"]


def test_clear_deletes_thread_attachments(monkeypatch, tmp_path):
    from app.services import chat_maintenance
    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    attachment_store.save_upload("repo-y", "a.txt", "text/plain", b"x")

    monkeypatch.setattr(chat_maintenance.agent_runner, "is_running", lambda tid: False)
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda tid: None)

    assert chat_maintenance.clear("repo-y") == {"ok": True}
    assert (root / "repo-y").exists() is False


# ---------- REST 端点 ----------

def _client(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.attachments import router

    root = tmp_path / "attachments"
    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", root)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), root


def test_upload_and_download_roundtrip(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)

    r = client.post("/upload", data={"thread_id": "repo-e2e"},
                    files={"file": ("计划.md", "内容正文".encode("utf-8"), "text/markdown")})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["file_id"]) == 32
    assert body["name"] == "计划.md"
    assert body["size"] == len("内容正文".encode("utf-8"))

    d = client.get(f"/{body['file_id']}")
    assert d.status_code == 200
    assert d.content == "内容正文".encode("utf-8")
    assert "attachment" in d.headers.get("content-disposition", "")


def test_upload_rejects_empty_file(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    r = client.post("/upload", data={"thread_id": "t"},
                    files={"file": ("empty.txt", b"", "text/plain")})
    assert r.status_code == 400


def test_upload_rejects_oversize(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(attachment_store, "MAX_UPLOAD_BYTES", 4)
    r = client.post("/upload", data={"thread_id": "t"},
                    files={"file": ("big.bin", b"x" * 10, "application/octet-stream")})
    assert r.status_code == 413


def test_download_unknown_file_id_404(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    r = client.get("/" + "0" * 32)
    assert r.status_code == 404


def test_download_invalid_file_id_404(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    assert client.get("/../etc/passwd").status_code == 404
    assert client.get("/short").status_code == 404
