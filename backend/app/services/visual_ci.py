"""
Visual CI — 插画验收层（机械 Trace + VLM 语义审计 + 可选重试）。

位置：接在 ComfyUI finalize_workflow_batch 之后，对已入库的 generation 做非阻断诊断。
功能：
  1. 机械 Trace 账本：Checkpoint、LoRA 文件、权重、Seed、尺寸、采样器 → fact ledger
  2. VLM 语义审计：逐字段检查人物、外貌、服装、动作、场景、构图、画风、噪点
  3. 图像相似度（可选）：若该角色有参考图则计算 similarity score
  4. 综合诊断：green/warn/fail + 证据链，不自动删除图片
  5. 用户选择后记录视觉偏好，或执行一次受限重试

不做的事：
  - 不单独用 VLM 判定"LoRA 已正确加载"（通用 MLLM 对人物一致性判断不稳定）
  - 不无限自动重跑（最多一次受限重试）
  - 不删除图片（只记录诊断状态）
"""
from __future__ import annotations

import json
import sqlite3
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import run_trace

# ── 数据库 ──────────────────────────────────────────────────────────────────

VISUAL_CI_DB = "visual_ci.db"


def _db_path(output_dir: str | Path, repo_id: str) -> Path:
    base = Path(output_dir) if output_dir else Path.cwd() / "data"
    return base / repo_id / VISUAL_CI_DB


def _init_db(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db), check_same_thread=False)
    con.execute("""
        CREATE TABLE IF NOT EXISTS diagnostics (
            id              TEXT PRIMARY KEY,
            generation_id   TEXT NOT NULL,
            turn_id         TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            verdict         TEXT DEFAULT '',
            mechanical      TEXT DEFAULT '{}',
            vlm_assessment  TEXT DEFAULT '{}',
            similarity      REAL   DEFAULT 0.0,
            field_ledger    TEXT DEFAULT '{}',
            retry_count     INTEGER DEFAULT 0,
            retry_of        TEXT DEFAULT '',
            evidence        TEXT DEFAULT '{}'
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_diagnostics_generation
        ON diagnostics(generation_id)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_diagnostics_status
        ON diagnostics(status)
    """)
    con.commit()
    con.close()


# ── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class FieldLedger:
    """单字段验收状态。"""
    name: str
    required: bool = False
    covered: bool = False
    evidence: str = ""
    vlm_ok: bool | None = None  # None=未检
    score: float = 0.0          # 0-1

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "required": self.required,
            "covered": self.covered,
            "evidence": self.evidence,
            "vlm_ok": self.vlm_ok,
            "score": self.score,
        }


@dataclass
class MechanicalLedger:
    """机械事实账本：ComfyUI Trace 注入的硬事实。"""
    checkpoint: str = ""
    loras: list[dict] = field(default_factory=list)
    seed: int | None = None
    width: int = 0
    height: int = 0
    sampler: str = ""
    steps: int = 0
    cfg: float = 0.0
    prompt_chars: int = 0

    def to_dict(self) -> dict:
        return {
            "checkpoint": self.checkpoint,
            "loras": self.loras,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "sampler": self.sampler,
            "steps": self.steps,
            "cfg": self.cfg,
            "prompt_chars": self.prompt_chars,
        }


@dataclass
class VLMAssessment:
    """VLM 语义评估结果。"""
    model: str = ""
    dimensions: dict[str, bool] = field(default_factory=dict)  # name → ok
    overall_ok: bool | None = None
    summary: str = ""
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "dimensions": self.dimensions,
            "overall_ok": self.overall_ok,
            "summary": self.summary,
            "raw_response": self.raw_response,
        }


@dataclass
class VisualCIDiagnostic:
    """完整诊断报告。"""
    id: str
    generation_id: str
    turn_id: str = ""
    status: str = "pending"          # pending | ok | warn | fail | retry
    verdict: str = ""                # green | amber | red
    mechanical: MechanicalLedger = field(default_factory=MechanicalLedger)
    vlm: VLMAssessment = field(default_factory=VLMAssessment)
    similarity: float = 0.0          # 0-1，与参考图的相似度
    field_ledger: list[FieldLedger] = field(default_factory=list)
    retry_count: int = 0
    retry_of: str = ""
    evidence: dict = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "generation_id": self.generation_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "verdict": self.verdict,
            "mechanical": self.mechanical.to_dict(),
            "vlm": self.vlm.to_dict(),
            "similarity": self.similarity,
            "field_ledger": [f.to_dict() for f in self.field_ledger],
            "retry_count": self.retry_count,
            "retry_of": self.retry_of,
            "evidence": self.evidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "VisualCIDiagnostic":
        mech = MechanicalLedger(**json.loads(row.get("mechanical", "{}")))
        vlm_raw = json.loads(row.get("vlm_assessment", "{}"))
        vlm = VLMAssessment(
            model=vlm_raw.get("model", ""),
            dimensions=vlm_raw.get("dimensions", {}),
            overall_ok=vlm_raw.get("overall_ok"),
            summary=vlm_raw.get("summary", ""),
            raw_response=vlm_raw.get("raw_response", ""),
        )
        fields = [
            FieldLedger(**f) for f in json.loads(row.get("field_ledger", "[]"))
        ]
        return cls(
            id=row["id"],
            generation_id=row["generation_id"],
            turn_id=row.get("turn_id", ""),
            status=row.get("status", "pending"),
            verdict=row.get("verdict", ""),
            mechanical=mech,
            vlm=vlm,
            similarity=row.get("similarity", 0.0),
            field_ledger=fields,
            retry_count=row.get("retry_count", 0),
            retry_of=row.get("retry_of", ""),
            evidence=json.loads(row.get("evidence", "{}")),
            created_at=row.get("created_at", ""),
        )


# ── VLM 提示词 ────────────────────────────────────────────────────────────────

_VLM_SYSTEM = (
    "You are a precise visual QA inspector for AI-generated character illustrations. "
    "Check the image against the given scene description field by field. "
    "Be strict: a field passes only if it is clearly and unambiguously present. "
    "Do NOT assume; require visible evidence. "
    "For character identity, rely on the visual description provided, not on your internal knowledge of characters."
)

_VLM_USER_TPL = (
    "Check this illustration.\n\n"
    "Scene description:\n{scene_description}\n\n"
    "Character visual description:\n{character_description}\n\n"
    "LoRA models used: {lora_list}\n\n"
    "Check ALL of the following dimensions and answer with a JSON object:\n"
    "{{\"character_identity\": true/false, \"appearance\": true/false, "
    "\"wardrobe\": true/false, \"action\": true/false, "
    "\"scene\": true/false, \"composition\": true/false, "
    "\"lighting\": true/false, \"art_style\": true/false, "
    "\"quality\": true/false, \"noise_or_artifacts\": false, "
    "\"overall_ok\": true/false, \"summary\": \"brief explanation\"}}"
)


# ── 核心服务 ─────────────────────────────────────────────────────────────────

def run_diagnostic(
    *,
    generation_id: str,
    turn_id: str,
    repo_id: str,
    output_dir: str,
    # generation_store 中可查到的 generation 记录
    generation_record: dict | None = None,
    # image_prompt_profiles 的 field_ledger（如果有的话）
    scene_spec: dict | None = None,
    # VLM 调用参数（由路由层注入）
    vlm_base: str = "",
    vlm_key: str = "",
    vlm_model: str = "",
    vlm_proxy: str = "",
    # 参考图（角色训练集或参考图路径，可选）
    reference_image_url: str = "",
) -> VisualCIDiagnostic | None:
    """
    对一条 generation 执行完整 Visual CI 诊断。

    调用链：
      1. 收集机械 Trace 证据
      2. 构建 field_ledger（从 scene_spec）
      3. 调用 VLM 做语义审计
      4. 可选：图像相似度（需要参考图）
      5. 汇总 verdict 并写入 visual_ci.db
      6. 记录 Trace 事件

    返回诊断报告；诊断失败返回 None（不阻断主流程）。
    """
    import uuid as _uuid

    diag_id = _uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    diag = VisualCIDiagnostic(
        id=diag_id,
        generation_id=generation_id,
        turn_id=turn_id,
        status="pending",
        created_at=now,
    )

    # ── 1. 机械 Trace ──────────────────────────────────────────────────────
    try:
        _probe_visual_ci_trace(diag, repo_id, generation_id)
    except Exception as exc:
        diag.evidence["trace_error"] = str(exc)

    # ── 2. Field Ledger（从 scene_spec 初始化）─────────────────────────────
    if scene_spec:
        _init_field_ledger(diag, scene_spec)
    else:
        _default_field_ledger(diag)

    # ── 3. VLM 语义审计 ──────────────────────────────────────────────────
    if vlm_base and vlm_model:
        try:
            _run_vlm_assessment(
                diag,
                generation_record=generation_record,
                vlm_base=vlm_base,
                vlm_key=vlm_key,
                vlm_model=vlm_model,
                vlm_proxy=vlm_proxy,
            )
        except Exception as exc:
            diag.evidence["vlm_error"] = str(exc)
            diag.evidence["vlm_trace"] = traceback.format_exc()

    # ── 4. 图像相似度（可选）──────────────────────────────────────────────
    if reference_image_url:
        try:
            _compute_similarity(diag, reference_image_url, generation_record, vlm_base, vlm_key, vlm_model, vlm_proxy)
        except Exception as exc:
            diag.evidence["similarity_error"] = str(exc)

    # ── 5. 综合 verdict ───────────────────────────────────────────────────
    _compute_verdict(diag)

    # ── 6. 写入数据库 ────────────────────────────────────────────────────
    try:
        db = _db_path(output_dir, repo_id)
        _init_db(db)
        _save_diagnostic(db, diag)
    except Exception as exc:
        diag.evidence["db_error"] = str(exc)

    # ── 7. Trace 事件 ────────────────────────────────────────────────────
    run_trace.emit(
        {"repo_id": repo_id, "generation_id": generation_id},
        "visual.ci",
        diag_id=diag.id,
        status=diag.status,
        verdict=diag.verdict,
        mechanical=diag.mechanical.to_dict(),
        vlm_model=diag.vlm.model,
        vlm_ok=diag.vlm.overall_ok,
        similarity=diag.similarity,
        field_count=len(diag.field_ledger),
        covered_fields=[f.name for f in diag.field_ledger if f.covered],
        failed_fields=[f.name for f in diag.field_ledger if f.required and not f.covered],
    )

    return diag


# ── Trace 读取（机械账本）───────────────────────────────────────────────────

def _probe_visual_ci_trace(diag: VisualCIDiagnostic, repo_id: str, generation_id: str) -> None:
    """从 run_trace 读取最近的 illustration.submitted 条目，提取机械事实。"""
    try:
        recent = run_trace.read_recent(repo_id, limit=50)
    except Exception:
        recent = []

    submitted = None
    for entry in reversed(recent):
        if entry.get("event") == "illustration.submitted":
            submitted = entry.get("data", {})
            break

    if submitted:
        diag.mechanical.checkpoint = submitted.get("checkpoint", "")
        # illustration.submitted 写的是 lora_name / lora_names（字符串），不是 loras（dict 列表）；
        # 字段名不匹配曾导致 mechanical.loras 恒空、no_loras_loaded 警告恒触发。按实际字段名
        # 重建 [{lora_name, weight}]；multi 模式共享 lora_weight（前端未上报各 LoRA 独立权重）。
        lora_names = submitted.get("lora_names") or []
        if not lora_names and submitted.get("lora_name"):
            lora_names = [submitted["lora_name"]]
        lora_weight = submitted.get("lora_weight") or 1.0
        diag.mechanical.loras = [
            {"lora_name": str(name), "weight": lora_weight}
            for name in lora_names
        ]
        diag.mechanical.seed = submitted.get("seed")
        diag.mechanical.width = submitted.get("width") or (submitted.get("latent") or {}).get("width", 0)
        diag.mechanical.height = submitted.get("height") or (submitted.get("latent") or {}).get("height", 0)
        diag.mechanical.sampler = submitted.get("sampler", "")
        diag.mechanical.steps = submitted.get("steps", 0)
        diag.mechanical.cfg = submitted.get("cfg", 0.0)
        diag.mechanical.prompt_chars = submitted.get("prompt_chars", 0)
        diag.evidence["trace_source"] = "illustration.submitted"
    else:
        diag.evidence["trace_source"] = "no_submitted_event_found"


# ── Field Ledger ─────────────────────────────────────────────────────────────

_REQUIRED_FIELDS = [
    "character_identity", "appearance", "wardrobe", "action",
    "scene", "composition", "lighting", "art_style", "quality",
]


def _init_field_ledger(diag: VisualCIDiagnostic, scene_spec: dict) -> None:
    """从 scene_spec 初始化 field_ledger。"""
    narrative = scene_spec.get("narrative", "")
    actors = scene_spec.get("actors", [])

    # 从 narrative 提取关键词作为证据
    for name in _REQUIRED_FIELDS:
        required = name in ("character_identity", "action", "scene")
        evidence = ""
        score = 0.0

        if name == "character_identity" and actors:
            evidence = f"Actors: {', '.join(actors)}"
            score = 1.0 if actors else 0.0
        elif name == "action":
            # 保守：动作词出现即算 covered
            action_words = ["walk", "stand", "sit", "lie", "reach", "grab",
                            "push", "pull", "turn", "look", "gaze", "hold",
                            "embrace", "kiss", "attack", "run", "jump"]
            if any(w in narrative.lower() for w in action_words):
                evidence = "Action detected in narrative"
                score = 0.7
        elif name == "scene":
            location_words = ["room", "hall", "forest", "mountain", "street",
                              "bedroom", "courtyard", "palace", "road", "path"]
            if any(w in narrative.lower() for w in location_words):
                evidence = "Scene location detected"
                score = 0.7

        diag.field_ledger.append(FieldLedger(
            name=name,
            required=required,
            covered=score >= 0.5,
            evidence=evidence,
            vlm_ok=None,
            score=score,
        ))


def _default_field_ledger(diag: VisualCIDiagnostic) -> None:
    """无 scene_spec 时的最小 ledger。"""
    for name in _REQUIRED_FIELDS:
        diag.field_ledger.append(FieldLedger(
            name=name,
            required=name in ("character_identity", "action", "scene"),
            covered=False,
            evidence="No scene_spec provided",
            score=0.0,
        ))


# ── VLM 调用 ─────────────────────────────────────────────────────────────────

def _run_vlm_assessment(
    diag: VisualCIDiagnostic,
    generation_record: dict | None,
    vlm_base: str,
    vlm_key: str,
    vlm_model: str,
    vlm_proxy: str,
) -> None:
    """调 VLM 对生成图做字段级语义审计（复用 build_model + image_url 多模态调用）。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.services import llm as _llm

    # 取图片 URL
    image_url = ""
    if generation_record:
        image_url = str(generation_record.get("url") or "") or str(
            generation_record.get("display_url") or "",
        )
    if not image_url:
        diag.evidence["vlm_skip"] = "no_image_url"
        return

    # 图片统一转 base64 data URI：Ollama 的 OpenAI 兼容端点不支持 image_url，
    # 只接受 base64；云端 API 同样兼容 data URI。拉取失败则跳过 VLM。
    image_data = _to_data_uri(image_url)
    if not image_data:
        diag.evidence["vlm_skip"] = "image_fetch_failed"
        diag.evidence["vlm_skip_url"] = image_url[:120]
        return

    # 构建 scene description
    scene_desc = ""
    for f in diag.field_ledger:
        if f.covered and f.evidence:
            scene_desc += f"- {f.name}: {f.evidence}\n"

    lora_list = ", ".join(
        f"{l.get('lora_name','')}@{l.get('weight','')}"
        for l in diag.mechanical.loras
    ) or "none"

    user_text = _VLM_USER_TPL.format(
        scene_description=scene_desc or "General scene",
        character_description="See actors and appearance fields",
        lora_list=lora_list,
    )

    try:
        llm = _llm.build_model(
            vlm_base, vlm_key, vlm_model, temperature=0.2, proxy=vlm_proxy, sdk_retries=1,
        )
        content: list = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_data}},
        ]
        resp = llm.invoke([
            SystemMessage(content=_VLM_SYSTEM),
            HumanMessage(content=content),
        ])
        raw = _llm.flatten_content(resp.content).strip()
        diag.vlm.raw_response = raw

        # 解析 JSON（容忍模型输出的 markdown 代码块包裹）
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            diag.evidence["vlm_parse_error"] = raw[:300]
            return

        diag.vlm.model = vlm_model
        dims = {
            k: bool(v) for k, v in parsed.items()
            if k not in ("overall_ok", "summary")
        }
        diag.vlm.dimensions = dims
        diag.vlm.overall_ok = bool(parsed.get("overall_ok"))
        diag.vlm.summary = str(parsed.get("summary", ""))

        # 更新 field_ledger
        for f in diag.field_ledger:
            if f.name in dims:
                f.vlm_ok = dims[f.name]
                if dims[f.name]:
                    f.score = max(f.score, 0.8)
                else:
                    f.score = min(f.score, 0.3)

    except Exception as exc:
        diag.evidence["vlm_call_error"] = str(exc)


def _to_data_uri(image_url: str, max_bytes: int = 30 * 1024 * 1024) -> str:
    """把图片 URL / 本地路径 / data URI 统一转成 base64 data URI。

    - Ollama 的 OpenAI 兼容端点只接受 base64 图片，不接受 image_url
    - 云端 API（OpenAI/智谱等）同样兼容 data URI
    - 拉取失败或超限返回空字符串（由调用方决定跳过）
    """
    if not image_url:
        return ""
    if image_url.startswith("data:"):
        return image_url
    import base64
    import mimetypes

    try:
        if image_url.startswith(("http://", "https://")):
            import httpx
            from app.services.url_guard import is_local_view_url, validate_media_url
            # local-view（本机落盘产物）豁免 SSRF 校验；其余 URL 必须过校验，
            # 防止内网/metadata 地址的响应被拉回后分析（数据外泄面）。
            if not is_local_view_url(image_url):
                validate_media_url(image_url)
            # trust_env=False：避免系统代理劫持 127.0.0.1 的 local-view 取图
            resp = httpx.get(image_url, timeout=60, trust_env=False,
                             follow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = (resp.headers.get("location") or "").strip()
                target = str(httpx.URL(image_url).join(location))
                # 重定向逐跳校验：校验通过才发下一跳（与 image_proxy 同一合同）
                if not is_local_view_url(target):
                    validate_media_url(target)
                resp = httpx.get(target, timeout=60, trust_env=False,
                                 follow_redirects=False)
            resp.raise_for_status()
            data = resp.content
        else:
            # 本地文件路径
            data = Path(image_url).read_bytes()
    except Exception:
        return ""
    if not data or len(data) > max_bytes:
        return ""
    mime = mimetypes.guess_type(image_url)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _extract_json(text: str) -> Any:
    """从 VLM 输出中提取 JSON（容忍 ```json 包裹与前后缀文本）。"""
    if not text:
        return None
    import re
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped, re.I)
    candidate = fenced.group(1) if fenced else stripped
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # 尝试截取首个 { 到末尾 }
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except Exception:
            return None
    return None


# ── 图像相似度（可选）────────────────────────────────────────────────────────

def _compute_similarity(
    diag: VisualCIDiagnostic,
    reference_image_url: str,
    generation_record: dict | None,
    vlm_base: str,
    vlm_key: str,
    vlm_model: str,
    vlm_proxy: str,
) -> None:
    """
    用 VLM 比较参考图与生成图的相似度。
    策略：用同一个 VLM 让模型判断"这两张图是同一角色吗"并给 0-1 分。
    不依赖专用 embedding 模型；若后续接入专用图像相似度指标可替换此实现。
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.services import llm as _llm

    # 相似度比较依赖 VLM：未配置 VLM 时跳过（前端也不会传参考图，双保险）
    if not (vlm_base and vlm_model):
        diag.evidence["similarity_skip"] = "no_vlm_configured"
        return

    gen_url = ""
    if generation_record:
        gen_url = str(generation_record.get("url") or "") or str(
            generation_record.get("display_url") or "",
        )

    if not (reference_image_url and gen_url):
        diag.evidence["similarity_skip"] = "missing_urls"
        return

    # 两张图都转 base64 data URI（Ollama 兼容端点不支持 image_url）
    ref_data = _to_data_uri(reference_image_url)
    gen_data = _to_data_uri(gen_url)
    if not (ref_data and gen_data):
        diag.evidence["similarity_skip"] = "image_fetch_failed"
        return

    prompt = (
        "Are these two images depicting the SAME character? "
        "Judge based on: face structure, hair color/style, body proportions, distinctive features. "
        "Ignore differences in pose, clothing, lighting, and background. "
        "Return a JSON: {\"same_character\": true/false, "
        "\"similarity_score\": 0.0-1.0, \"reason\": \"...\"}"
    )

    try:
        llm = _llm.build_model(
            vlm_base, vlm_key, vlm_model, temperature=0.2, proxy=vlm_proxy, sdk_retries=1,
        )
        content: list = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": ref_data}},
            {"type": "image_url", "image_url": {"url": gen_data}},
        ]
        resp = llm.invoke([
            SystemMessage(content="You are a precise visual identity comparator."),
            HumanMessage(content=content),
        ])
        raw = _llm.flatten_content(resp.content).strip()
        parsed = _extract_json(raw)
        if isinstance(parsed, dict):
            try:
                diag.similarity = float(parsed.get("similarity_score", 0.0))
            except (TypeError, ValueError):
                diag.similarity = 0.0
            diag.evidence["similarity_reason"] = str(parsed.get("reason", ""))
        else:
            diag.similarity = 0.0
            diag.evidence["similarity_parse_error"] = raw[:200]
    except Exception as exc:
        diag.evidence["similarity_error"] = str(exc)


# ── Verdict ──────────────────────────────────────────────────────────────────

def _compute_verdict(diag: VisualCIDiagnostic) -> None:
    """综合机械账本、VLM 评估与相似度计算最终 verdict。"""
    warn_count = 0
    fail_count = 0

    # 机械账本检查
    if not diag.mechanical.checkpoint:
        warn_count += 1
        diag.evidence.setdefault("mechanical_warns", []).append("no_checkpoint_in_trace")
    if not diag.mechanical.loras and diag.evidence.get("trace_source") == "illustration.submitted":
        warn_count += 1
        diag.evidence.setdefault("mechanical_warns", []).append("no_loras_loaded")

    # VLM 检查
    for f in diag.field_ledger:
        if f.required and f.vlm_ok is False:
            fail_count += 1
            diag.evidence.setdefault("vlm_fails", []).append(f.name)
        elif f.required and f.vlm_ok is None and f.score < 0.5:
            warn_count += 1
            diag.evidence.setdefault("vlm_warns", []).append(f.name)
        # vlm_ok=True 视为通过（即使本地 evidence 提取不充分）；
        # vlm_ok=False 已在上方计 fail；vlm_ok=None 且 score>=0.5 也视为通过

    # 相似度阈值（参考图存在时）
    if diag.similarity > 0:
        if diag.similarity < 0.4:
            warn_count += 1
            diag.evidence["similarity_warn"] = f"low_similarity_{diag.similarity:.2f}"
        elif diag.similarity < 0.25:
            fail_count += 1
            diag.evidence["similarity_fail"] = f"very_low_similarity_{diag.similarity:.2f}"

    # 噪点检查：VLM 的 noise_or_artifacts 语义是“存在噪点/伪影？”，
    # false=无噪点（通过）、true=有噪点（fail）。
    noise_dims = diag.vlm.dimensions.get("noise_or_artifacts")
    if noise_dims is True:
        fail_count += 1
        diag.evidence["noise_fail"] = True

    # 综合
    if fail_count > 0:
        diag.status = "fail"
        diag.verdict = "red"
    elif warn_count > 0:
        diag.status = "warn"
        diag.verdict = "amber"
    else:
        diag.status = "ok"
        diag.verdict = "green"


# ── 数据库操作 ───────────────────────────────────────────────────────────────

def _save_diagnostic(db: Path, diag: VisualCIDiagnostic) -> None:
    con = sqlite3.connect(str(db), check_same_thread=False)
    con.execute("""
        INSERT OR REPLACE INTO diagnostics
        (id, generation_id, turn_id, created_at, status, verdict,
         mechanical, vlm_assessment, similarity, field_ledger,
         retry_count, retry_of, evidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        diag.id,
        diag.generation_id,
        diag.turn_id,
        diag.created_at,
        diag.status,
        diag.verdict,
        json.dumps(diag.mechanical.to_dict(), ensure_ascii=False),
        json.dumps(diag.vlm.to_dict(), ensure_ascii=False),
        diag.similarity,
        json.dumps([f.to_dict() for f in diag.field_ledger], ensure_ascii=False),
        diag.retry_count,
        diag.retry_of,
        json.dumps(diag.evidence, ensure_ascii=False),
    ))
    con.commit()
    con.close()


def load_diagnostic(output_dir: str, repo_id: str, generation_id: str) -> VisualCIDiagnostic | None:
    """按 generation_id 加载最新诊断报告。"""
    db = _db_path(output_dir, repo_id)
    if not db.exists():
        return None
    con = sqlite3.connect(str(db), check_same_thread=False)
    try:
        cols = [d[1] for d in con.execute("PRAGMA table_info(diagnostics)").fetchall()]
        row = con.execute("""
            SELECT * FROM diagnostics
            WHERE generation_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (generation_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return VisualCIDiagnostic.from_row(dict(zip(cols, row)))


def list_diagnostics(
    output_dir: str,
    repo_id: str,
    status: str = "",
    limit: int = 50,
) -> list[VisualCIDiagnostic]:
    """列出某仓库的诊断记录。"""
    db = _db_path(output_dir, repo_id)
    if not db.exists():
        return []
    con = sqlite3.connect(str(db), check_same_thread=False)
    try:
        cols = [d[1] for d in con.execute("PRAGMA table_info(diagnostics)").fetchall()]
        if status:
            rows = con.execute("""
                SELECT * FROM diagnostics
                WHERE status = ?
                ORDER BY created_at DESC LIMIT ?
            """, (status, limit)).fetchall()
        else:
            rows = con.execute("""
                SELECT * FROM diagnostics
                ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
    finally:
        con.close()
    return [VisualCIDiagnostic.from_row(dict(zip(cols, r))) for r in rows]


def request_retry(
    output_dir: str,
    repo_id: str,
    generation_id: str,
    max_retries: int = 1,
) -> dict:
    """
    申请受限重试。检查 retry_count，返回是否允许。
    """
    diag = load_diagnostic(output_dir, repo_id, generation_id)
    if diag is None:
        return {"allowed": False, "reason": "no_diagnostic_found"}

    if diag.retry_count >= max_retries:
        return {"allowed": False, "reason": "retry_limit_reached", "retry_count": diag.retry_count}

    db = _db_path(output_dir, repo_id)
    _init_db(db)
    con = sqlite3.connect(str(db), check_same_thread=False)
    con.execute("""
        UPDATE diagnostics
        SET retry_count = retry_count + 1, status = 'retry', retry_of = ?
        WHERE id = ?
    """, (diag.id, diag.id))
    con.commit()
    con.close()

    return {
        "allowed": True,
        "retry_count": diag.retry_count + 1,
        "original_diag_id": diag.id,
        "mechanical": diag.mechanical.to_dict(),
    }
