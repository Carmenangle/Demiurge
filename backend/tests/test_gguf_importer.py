# -*- coding: utf-8 -*-
"""GGUF 导入器测试：解析、扫描、硬件适配、Modelfile 生成、provider 注册。"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import gguf_importer as g


# ── 构造最小 GGUF 测试文件 ──────────────────────────────────────────────────

def _write_min_gguf(path: Path, *, architecture: str = "llama",
                    size_label: str = "3.2B", file_type: int = 12,
                    kind: str = "model", extra_kv: dict | None = None) -> None:
    """写一个最小的合法 GGUF v3 文件（头部 KV，无张量）。"""
    kv: list[tuple[str, int, object]] = [
        ("general.architecture", 8, architecture),
        ("general.type", 8, kind),
        ("general.size_label", 8, size_label),
        ("general.file_type", 4, file_type),
        (f"{architecture}.block_count", 4, 32),
        (f"{architecture}.embedding_length", 4, 4096),
        (f"{architecture}.context_length", 4, 8192),
    ]
    if extra_kv:
        for k, v in extra_kv.items():
            t = 8 if isinstance(v, str) else (4 if isinstance(v, int) else 7)
            kv.append((k, t, v))

    def enc_str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    def enc_val(vtype: int, val: object) -> bytes:
        if vtype == 8:
            return enc_str(str(val))
        if vtype == 4:
            return struct.pack("<I", int(val))
        if vtype == 7:
            return struct.pack("<B", 1 if val else 0)
        raise ValueError(f"unsupported type {vtype}")

    body = b"".join(enc_str(k) + struct.pack("<I", t) + enc_val(t, v) for k, t, v in kv)
    header = b"GGUF" + struct.pack("<IQQ", 3, 0, len(kv))
    path.write_bytes(header + body)


@pytest.fixture
def gguf_file(tmp_path: Path) -> Path:
    p = tmp_path / "test-model-Q4_K_M.gguf"
    _write_min_gguf(p, architecture="qwen3vl", size_label="8.2B", file_type=12)
    return p


@pytest.fixture
def mmproj_file(tmp_path: Path) -> Path:
    p = tmp_path / "test-model-mmproj-f16.gguf"
    _write_min_gguf(
        p, architecture="clip", size_label="576M", file_type=1, kind="mmproj",
        extra_kv={"clip.has_vision_encoder": True},
    )
    return p


# ── 解析测试 ────────────────────────────────────────────────────────────────

class TestParseGguf:
    def test_parse_basic(self, gguf_file: Path):
        meta = g.parse_gguf(gguf_file)
        assert meta is not None
        assert meta.architecture == "qwen3vl"
        assert meta.quant == "q4_k_m"          # 文件名优先
        assert meta.parameters_b == 8.2
        assert meta.context_length == 8192
        assert meta.kind == "model"
        assert meta.is_vision                  # qwen3vl 架构 → 视觉

    def test_parse_mmproj(self, mmproj_file: Path):
        meta = g.parse_gguf(mmproj_file)
        assert meta is not None
        assert meta.kind == "mmproj"
        assert meta.has_vision_encoder
        assert meta.is_vision
        assert meta.quant == "f16"

    def test_parse_embedding(self, tmp_path: Path):
        p = tmp_path / "bge-m3-Q8_0.gguf"
        _write_min_gguf(p, architecture="bge-m3", size_label="0.6B", file_type=8)
        meta = g.parse_gguf(p)
        assert meta is not None
        assert meta.is_embedding
        assert not meta.is_vision

    def test_parse_non_gguf(self, tmp_path: Path):
        p = tmp_path / "fake.gguf"
        p.write_bytes(b"NOTGUF" + b"\x00" * 64)
        assert g.parse_gguf(p) is None

    def test_parse_missing_file(self, tmp_path: Path):
        assert g.parse_gguf(tmp_path / "nope.gguf") is None

    def test_quant_from_filename(self):
        assert g._quant_from_filename("model-Q6_K.gguf") == "q6_k"
        assert g._quant_from_filename("model.f16.gguf") == "f16"
        assert g._quant_from_filename("model.F16.gguf") == "f16"
        assert g._quant_from_filename("plain.gguf") == ""


# ── 扫描测试 ────────────────────────────────────────────────────────────────

class TestScan:
    def test_scan_dir(self, tmp_path: Path, gguf_file: Path, mmproj_file: Path):
        result = g.scan_gguf_dir(tmp_path)
        assert result["count"] == 2
        assert len(result["models"]) == 1
        assert len(result["mmproj"]) == 1
        assert result["models"][0]["is_vision"]

    def test_scan_missing_dir(self, tmp_path: Path):
        result = g.scan_gguf_dir(tmp_path / "missing")
        assert "error" in result

    def test_find_mmproj_for(self, tmp_path: Path, gguf_file: Path, mmproj_file: Path):
        found = g.find_mmproj_for(tmp_path, str(gguf_file))
        assert found == str(mmproj_file)


# ── 硬件适配测试 ────────────────────────────────────────────────────────────

class TestFitHardware:
    def _meta(self, params_b: float, quant: str = "q4_k_m") -> g.GgufMeta:
        return g.GgufMeta(
            path="x.gguf", filename="x.gguf", size_bytes=4 * 1024 ** 3,
            size_label=f"{params_b}B", architecture="llama", parameters_b=params_b,
            context_length=8192, quant=quant,
        )

    def test_fit_ok(self):
        meta = self._meta(3.2)
        fit = g.fit_hardware(meta, device={"available_mib": 24 * 1024})
        assert fit["level"] == "ok"
        assert any("显存充足" in s for s in fit["suggestions"])

    def test_fit_partial(self):
        meta = self._meta(14.0, quant="q4_k_m")   # 14B q4 约 9.3GB，7GB 显存不够但差距 <2x
        fit = g.fit_hardware(meta, device={"available_mib": 7 * 1024})
        assert fit["level"] == "partial_offload"
        assert any("显存不足" in s for s in fit["suggestions"])

    def test_fit_cpu_only(self):
        meta = self._meta(8.2)
        fit = g.fit_hardware(meta, device={"available_mib": 0})
        assert fit["level"] == "cpu_only"
        assert any("CPU" in s for s in fit["suggestions"])

    def test_fit_low(self):
        meta = self._meta(70.0)
        fit = g.fit_hardware(meta, device={"available_mib": 8 * 1024})
        assert fit["level"] == "low"
        assert any("严重不足" in s for s in fit["suggestions"])


# ── Modelfile 生成测试 ───────────────────────────────────────────────────────

class TestModelfile:
    def test_basic(self):
        mf = g._modelfile_content("C:\\models\\x.gguf")
        assert mf == "FROM C:\\models\\x.gguf"

    def test_with_mmproj(self):
        mf = g._modelfile_content("C:\\models\\x.gguf", "C:\\models\\mmproj.gguf")
        assert "FROM C:\\models\\x.gguf" in mf
        assert "ADAPTER C:\\models\\mmproj.gguf" in mf

    def test_suggest_name(self):
        meta = g.GgufMeta(path="", filename="x.gguf", architecture="qwen3vl",
                          parameters_b=8.2, quant="q6_k")
        assert g._suggest_model_name(meta) == "qwen3vl:8.2b-q6_k"

    def test_suggest_name_sanitize(self):
        meta = g.GgufMeta(path="", filename="x.gguf", architecture="My-Arch",
                          parameters_b=1.5, quant="Q4_K_M")
        name = g._suggest_model_name(meta)
        assert name == name.lower()
        assert all(c.isalnum() or c in "._:-" for c in name)

    def test_qwen3vl_preset_template(self):
        meta = g.GgufMeta(path="", filename="x.gguf", architecture="qwen3vl")
        preset = g._OLLAMA_PRESETS.get(meta.architecture.lower())
        assert preset is not None
        assert "<|im_start|>" in preset["template"]
        assert "<|im_end|>" in preset["stop"]


# ── Provider 注册逻辑测试（不依赖真实 DB） ──────────────────────────────────

class TestRegisterProvider:
    """直接 mock 真实 ai_provider_service 模块方法（register_provider 局部 import）。"""

    @pytest.fixture(autouse=True)
    def _isolate_db(self, tmp_path, monkeypatch):
        """把 app.db.get_connection 指向临时 sqlite，避免污染真实数据库。"""
        import sqlite3

        db_file = tmp_path / "test_app.db"
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row

        def fake_connection() -> sqlite3.Connection:
            return conn

        from app import db
        monkeypatch.setattr(db, "get_connection", fake_connection)
        yield
        conn.close()

    def test_register_creates_provider(self, monkeypatch):
        """create_provider 分支：provider 不存在 → 新建并注册。"""
        from app.services import ai_provider_service

        monkeypatch.setattr(ai_provider_service, "list_providers", lambda: [])
        created = {"id": None, "name": None}

        def fake_create(payload):
            created["id"] = "prov-1"
            created["name"] = payload.name
            return type("P", (), {"id": "prov-1", "name": payload.name})()

        monkeypatch.setattr(ai_provider_service, "create_provider", fake_create)
        result = g.register_provider(
            "qwen3vl:8b",
            base_url="http://127.0.0.1:11434/v1",
        )
        assert result["ok"]
        assert result["provider_id"] == "prov-1"

    def test_register_existing_provider_adds_model(self, monkeypatch):
        from app.services import ai_provider_service

        class Prov:
            id = "prov-1"
            name = "Ollama 本地"
            base_url = "http://127.0.0.1:11434/v1"

        monkeypatch.setattr(ai_provider_service, "list_providers", lambda: [Prov()])
        monkeypatch.setattr(ai_provider_service, "get_provider_models", lambda pid: ["gemma4:latest"])
        added = []
        monkeypatch.setattr(ai_provider_service, "add_manual_model",
                            lambda pid, name: added.append(name) or True)
        result = g.register_provider("qwen3vl:8b")
        assert result["ok"]
        assert "加入" in result["message"]
        assert result["provider_id"] == "prov-1"
        assert added == ["qwen3vl:8b"]

    def test_register_duplicate_model(self, monkeypatch):
        from app.services import ai_provider_service

        class Prov:
            id = "prov-1"
            name = "Ollama 本地"
            base_url = "http://127.0.0.1:11434/v1"

        monkeypatch.setattr(ai_provider_service, "list_providers", lambda: [Prov()])
        monkeypatch.setattr(ai_provider_service, "get_provider_models", lambda pid: ["qwen3vl:8b"])
        result = g.register_provider("qwen3vl:8b")
        assert result["ok"]
        assert "已在" in result["message"]


# ── 一键流程测试（mock ollama 命令） ─────────────────────────────────────────

class TestImportFlow:
    def test_import_success(self, monkeypatch, gguf_file: Path):
        monkeypatch.setattr(g, "_run_ollama", lambda args, timeout=900: (0, "success"))
        result = g.import_to_ollama(str(gguf_file), model_name="test:1b")
        assert result.ok
        assert result.model_name == "test:1b"
        assert "导入成功" in result.message

    def test_import_ollama_error(self, monkeypatch, gguf_file: Path):
        monkeypatch.setattr(g, "_run_ollama", lambda args, timeout=900: (1, "boom"))
        result = g.import_to_ollama(str(gguf_file), model_name="test:1b")
        assert not result.ok
        assert "boom" in result.message

    def test_import_missing_file(self):
        result = g.import_to_ollama("Z:\\nope.gguf")
        assert not result.ok
        assert "不存在" in result.message

    def test_import_auto_mmproj(self, monkeypatch, gguf_file: Path, mmproj_file: Path):
        """自动配对 mmproj：Modelfile 内容应含 ADAPTER 行。"""
        captured: dict = {}

        def fake_run(args, timeout=900):
            captured["args"] = args
            # 在 fake 里同步读 Modelfile（tempdir 退出后会被清理）
            mf_path = Path(args[-1])
            captured["content"] = mf_path.read_text(encoding="utf-8")
            return 0, "ok"

        monkeypatch.setattr(g, "_run_ollama", fake_run)
        result = g.import_to_ollama(str(gguf_file), model_name="test:1b")
        assert result.ok
        assert "ADAPTER" in captured["content"]
        assert "mmproj" in captured["content"].lower()

    def test_ollama_not_found(self, monkeypatch, gguf_file: Path):
        monkeypatch.setattr(g, "_run_ollama", lambda args, timeout=900: (1, "未找到 ollama"))
        result = g.import_to_ollama(str(gguf_file), model_name="test:1b")
        assert not result.ok
        assert "未找到 ollama" in result.message


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
