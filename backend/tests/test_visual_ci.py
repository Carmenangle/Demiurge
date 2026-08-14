"""Visual CI 回归测试：机械 Trace 账本 + VLM 语义审计 + 受限重试。"""


from app.services import visual_ci
from app.services.visual_ci import (
    FieldLedger,
    MechanicalLedger,
    VLMAssessment,
    VisualCIDiagnostic,
    _compute_verdict,
    _extract_json,
    _init_field_ledger,
    run_diagnostic,
)


# ── 数据类序列化 ─────────────────────────────────────────────────────────────

def test_ledger_roundtrip():
    diag = VisualCIDiagnostic(
        id="d1", generation_id="g1", turn_id="t1", status="warn", verdict="amber",
        mechanical=MechanicalLedger(
            checkpoint="model.safetensors",
            loras=[{"lora_name": "charA.safetensors", "weight": 0.8}],
            seed=42, width=1024, height=1536,
            sampler="euler", steps=28, cfg=7.0, prompt_chars=120,
        ),
        vlm=VLMAssessment(
            model="qwen2.5-vl", dimensions={"character_identity": True, "action": False},
            overall_ok=False, summary="action missing",
        ),
        similarity=0.6,
        field_ledger=[
            FieldLedger(name="character_identity", required=True, covered=True,
                        evidence="actors", vlm_ok=True, score=0.8),
        ],
        retry_count=0, retry_of="",
        evidence={"trace_source": "illustration.submitted"},
        created_at="2026-08-12T00:00:00+00:00",
    )
    d = diag.to_dict()
    assert d["mechanical"]["checkpoint"] == "model.safetensors"
    assert d["mechanical"]["loras"][0]["lora_name"] == "charA.safetensors"
    assert d["vlm"]["dimensions"]["action"] is False
    assert d["field_ledger"][0]["vlm_ok"] is True


def test_from_row_roundtrip(tmp_path):
    diag = VisualCIDiagnostic(
        id="d2", generation_id="g2", turn_id="t2", status="ok", verdict="green",
        mechanical=MechanicalLedger(checkpoint="c.safetensors", loras=[],
                                    seed=7, width=512, height=768),
        field_ledger=[FieldLedger(name="scene", required=True, covered=True, score=0.9)],
    )
    db = tmp_path / "repo" / visual_ci.VISUAL_CI_DB
    visual_ci._init_db(db)
    visual_ci._save_diagnostic(db, diag)
    loaded = visual_ci.load_diagnostic(str(tmp_path), "repo", "g2")
    assert loaded is not None
    assert loaded.status == "ok"
    assert loaded.mechanical.checkpoint == "c.safetensors"
    assert loaded.field_ledger[0].name == "scene"


# ── Field Ledger ─────────────────────────────────────────────────────────────

def test_init_field_ledger_from_scene_spec():
    diag = VisualCIDiagnostic(id="d", generation_id="g")
    _init_field_ledger(diag, {
        "draft_prompt": "a woman walking",
        "narrative": "she walks in the forest and reaches for the door",
        "actors": ["冷倾雪"],
    })
    names = {f.name for f in diag.field_ledger}
    assert names == {
        "character_identity", "appearance", "wardrobe", "action",
        "scene", "composition", "lighting", "art_style", "quality",
    }
    by_name = {f.name: f for f in diag.field_ledger}
    assert by_name["character_identity"].covered is True   # actors 非空
    assert by_name["action"].covered is True               # 命中动作词
    assert by_name["scene"].covered is True                # 命中场景词


def test_required_fields_flagged():
    diag = VisualCIDiagnostic(id="d", generation_id="g")
    _init_field_ledger(diag, {"actors": []})
    required = {f.name for f in diag.field_ledger if f.required}
    assert required == {"character_identity", "action", "scene"}


# ── Verdict 计算 ─────────────────────────────────────────────────────────────

def test_verdict_green_when_all_pass():
    diag = VisualCIDiagnostic(id="d", generation_id="g")
    diag.mechanical.checkpoint = "model.safetensors"
    diag.mechanical.loras = [{"lora_name": "charA", "weight": 1.0}]
    for name in visual_ci._REQUIRED_FIELDS:
        diag.field_ledger.append(FieldLedger(
            name=name, required=name in ("character_identity", "action", "scene"),
            covered=True, vlm_ok=True, score=0.9,
        ))
    diag.evidence["trace_source"] = "illustration.submitted"
    _compute_verdict(diag)
    assert diag.status == "ok"
    assert diag.verdict == "green"


def test_verdict_red_when_required_field_fails():
    diag = VisualCIDiagnostic(id="d", generation_id="g")
    diag.mechanical.checkpoint = "model.safetensors"
    for name in visual_ci._REQUIRED_FIELDS:
        diag.field_ledger.append(FieldLedger(
            name=name, required=name in ("character_identity", "action", "scene"),
            covered=True,
            vlm_ok=False if name == "action" else True,
            score=0.9 if name != "action" else 0.2,
        ))
    _compute_verdict(diag)
    assert diag.status == "fail"
    assert diag.verdict == "red"
    assert "action" in diag.evidence.get("vlm_fails", [])


def test_verdict_amber_when_only_warns():
    diag = VisualCIDiagnostic(id="d", generation_id="g")
    diag.mechanical.checkpoint = ""
    diag.evidence["trace_source"] = "no_submitted_event_found"
    for name in visual_ci._REQUIRED_FIELDS:
        diag.field_ledger.append(FieldLedger(
            name=name, required=name in ("character_identity", "action", "scene"),
            covered=True, vlm_ok=True, score=0.9,
        ))
    _compute_verdict(diag)
    assert diag.status == "warn"
    assert diag.verdict == "amber"


def test_verdict_noise_fail():
    diag = VisualCIDiagnostic(id="d", generation_id="g")
    diag.mechanical.checkpoint = "m.safetensors"
    for name in visual_ci._REQUIRED_FIELDS:
        diag.field_ledger.append(FieldLedger(
            name=name, required=False, covered=True, vlm_ok=True, score=0.9,
        ))
    diag.vlm.dimensions = {"noise_or_artifacts": True}  # VLM 报告有噪点 → fail
    _compute_verdict(diag)
    assert diag.status == "fail"
    assert diag.evidence.get("noise_fail") is True


def test_verdict_low_similarity_warns():
    diag = VisualCIDiagnostic(id="d", generation_id="g")
    diag.mechanical.checkpoint = "m.safetensors"
    for name in visual_ci._REQUIRED_FIELDS:
        diag.field_ledger.append(FieldLedger(
            name=name, required=False, covered=True, vlm_ok=True, score=0.9,
        ))
    diag.similarity = 0.3
    _compute_verdict(diag)
    assert diag.status == "warn"
    assert "similarity_warn" in diag.evidence


# ── JSON 提取 ────────────────────────────────────────────────────────────────

def test_extract_json_fenced_and_plain():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('{"a": 2}') == {"a": 2}
    assert _extract_json('prefix {"a": 3} suffix') == {"a": 3}
    assert _extract_json("not json") is None


# ── 完整诊断（无 VLM）────────────────────────────────────────────────────────

def test_run_diagnostic_no_vlm(tmp_path):
    diag = run_diagnostic(
        generation_id="g_novlm", turn_id="t1", repo_id="repo_x",
        output_dir=str(tmp_path),
        scene_spec={
            "draft_prompt": "a woman walking in a forest",
            "narrative": "她走进森林",
            "actors": ["冷倾雪"],
        },
        # 不配置 VLM → 跳过语义审计，只有机械账本
        vlm_base="", vlm_key="", vlm_model="",
    )
    assert diag is not None
    assert diag.generation_id == "g_novlm"
    assert diag.vlm.model == ""
    assert diag.vlm.overall_ok is None
    assert diag.status in ("ok", "warn", "fail")
    # 数据库可回读
    loaded = visual_ci.load_diagnostic(str(tmp_path), "repo_x", "g_novlm")
    assert loaded is not None
    assert loaded.id == diag.id


def test_run_diagnostic_missing_trace_marks_amber(tmp_path):
    diag = run_diagnostic(
        generation_id="g_notrace", turn_id="t9", repo_id="repo_y",
        output_dir=str(tmp_path),
        scene_spec={"actors": ["虞妙玥"]},
    )
    assert diag is not None
    assert diag.evidence.get("trace_source") == "no_submitted_event_found"


def test_retry_limit_enforced(tmp_path):
    visual_ci.run_diagnostic(
        generation_id="g_retry", turn_id="t1", repo_id="repo_z",
        output_dir=str(tmp_path),
        scene_spec={"actors": ["x"]},
    )
    first = visual_ci.request_retry(str(tmp_path), "repo_z", "g_retry", max_retries=1)
    assert first["allowed"] is True
    assert first["retry_count"] == 1
    second = visual_ci.request_retry(str(tmp_path), "repo_z", "g_retry", max_retries=1)
    assert second["allowed"] is False
    assert second["reason"] == "retry_limit_reached"


def test_retry_unknown_generation(tmp_path):
    result = visual_ci.request_retry(str(tmp_path), "repo_z", "ghost")
    assert result["allowed"] is False
    assert result["reason"] == "no_diagnostic_found"


# ── 路由冒烟 ─────────────────────────────────────────────────────────────────

def test_router_smoke(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.visual_ci import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/run", json={
        "generation_id": "g_api", "turn_id": "t1", "repo_id": "r_api",
        "output_dir": str(tmp_path),
        "scene_spec": {"draft_prompt": "a woman", "narrative": "walk", "actors": ["a"]},
        "chat": {"base_url": "", "api_key": "", "model": ""},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] in ("green", "amber", "red")

    r2 = client.post("/list", json={"repo_id": "r_api", "output_dir": str(tmp_path)})
    assert r2.status_code == 200
    assert len(r2.json()) >= 1

    r3 = client.post("/load", json={
        "generation_id": "g_api", "repo_id": "r_api", "output_dir": str(tmp_path),
    })
    assert r3.status_code == 200
    assert r3.json()["generation_id"] == "g_api"

    r4 = client.post("/load", json={
        "generation_id": "nope", "repo_id": "r_api", "output_dir": str(tmp_path),
    })
    assert r4.status_code == 404


def test_router_rejects_empty_generation_id(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.visual_ci import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post("/run", json={
        "generation_id": "",   # Pydantic Field(..., min_length=1) 时拒绝
        "turn_id": "",
        "repo_id": "r",
        "output_dir": str(tmp_path),
    })
    # 空 generation_id 或 repo_id 是 bad request
    assert r.status_code >= 400


# ── 图片转 base64 data URI（Ollama 兼容端点只接受 base64）───────────────────

def test_to_data_uri_data_uri_passthrough():
    """已是 data URI 时原样返回。"""
    from app.services.visual_ci import _to_data_uri
    uri = "data:image/png;base64,AAAA"
    assert _to_data_uri(uri) == uri


def test_to_data_uri_empty():
    from app.services.visual_ci import _to_data_uri
    assert _to_data_uri("") == ""
    assert _to_data_uri(None) == ""


def test_to_data_uri_local_file(tmp_path):
    """本地文件路径 → data URI，带正确 mime。"""
    from app.services.visual_ci import _to_data_uri
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    uri = _to_data_uri(str(p))
    assert uri.startswith("data:image/png;base64,")
    import base64
    raw = base64.b64decode(uri.split(",", 1)[1])
    assert raw.startswith(b"\x89PNG")


def test_to_data_uri_missing_file():
    """文件不存在返回空串（调用方跳过 VLM）。"""
    from app.services.visual_ci import _to_data_uri
    assert _to_data_uri(r"D:\no\such\file.png") == ""
