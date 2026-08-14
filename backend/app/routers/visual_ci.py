"""Visual CI 路由：插画验收诊断接口。

位置：接在 ComfyUI finalize 之后，对已入库 generation 做非阻断诊断。
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import visual_ci

router = APIRouter()


# ── 请求模型 ────────────────────────────────────────────────────────────────

class ChatModelReq(BaseModel):
    """与 ai_text.py 同结构的对话模型配置。"""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    proxy: str = ""


class RunDiagnosticReq(BaseModel):
    """执行一次 Visual CI 诊断。"""
    generation_id: str = Field(..., min_length=1, description="generation 记录 ID")
    turn_id: str = Field(default="", description="剧情回合 ID（可选）")
    repo_id: str = Field(..., min_length=1, description="小仓库 ID")
    output_dir: str = Field(..., min_length=1, description="仓库文件夹路径")
    generation_record: dict = Field(default_factory=dict, description="generation 记录（可选）")
    scene_spec: dict = Field(default_factory=dict, description="image_prompt_profiles scene_spec")
    reference_image_url: str = Field(default="", description="角色参考图 URL（可选）")
    chat: ChatModelReq = Field(default_factory=ChatModelReq, description="VLM 配置")


class LoadDiagnosticReq(BaseModel):
    """加载某 generation 的最新诊断报告。"""
    generation_id: str
    repo_id: str
    output_dir: str


class ListDiagnosticsReq(BaseModel):
    """列出某仓库的诊断记录。"""
    repo_id: str
    output_dir: str
    status: Literal["pending", "ok", "warn", "fail", "retry"] | None = None
    limit: int = Field(default=50, ge=1, le=200)


class RequestRetryReq(BaseModel):
    """申请受限重试。"""
    generation_id: str
    repo_id: str
    output_dir: str
    max_retries: int = Field(default=1, ge=1, le=3)


# ── 路由 ─────────────────────────────────────────────────────────────────────

@router.post("/run")
def run_diagnostic(req: RunDiagnosticReq) -> dict:
    """
    执行一次 Visual CI 诊断：
      1. 从 Trace 提取机械事实（checkpoint、LoRA、seed、尺寸等）
      2. 从 scene_spec 初始化 field_ledger
      3. 调 VLM 做字段级语义审计（可选）
      4. 计算与参考图的相似度（可选）
      5. 综合 verdict（green/amber/red）
      6. 写入 visual_ci.db 并记录 Trace 事件

    诊断失败不阻断主流程，仍返回报告（status=warn 或 fail）。
    """
    try:
        diag = visual_ci.run_diagnostic(
            generation_id=req.generation_id,
            turn_id=req.turn_id,
            repo_id=req.repo_id,
            output_dir=req.output_dir,
            generation_record=req.generation_record or None,
            scene_spec=req.scene_spec or None,
            vlm_base=req.chat.base_url,
            vlm_key=req.chat.api_key,
            vlm_model=req.chat.model,
            vlm_proxy=req.chat.proxy,
            reference_image_url=req.reference_image_url,
        )
    except Exception as exc:
        # 诊断层异常不阻断，返回 error 状态
        return {
            "error": str(exc),
            "generation_id": req.generation_id,
            "status": "error",
            "verdict": "unknown",
        }

    if diag is None:
        raise HTTPException(status_code=500, detail="诊断返回空结果")

    return diag.to_dict()


@router.post("/load")
def load_diagnostic(req: LoadDiagnosticReq) -> dict:
    """按 generation_id 加载最新诊断报告；无报告返回 404。"""
    diag = visual_ci.load_diagnostic(req.output_dir, req.repo_id, req.generation_id)
    if diag is None:
        raise HTTPException(status_code=404, detail="未找到诊断记录")
    return diag.to_dict()


@router.post("/list")
def list_diagnostics(req: ListDiagnosticsReq) -> list[dict]:
    """列出某仓库的诊断记录（可选按 status 过滤）。"""
    return [
        d.to_dict() for d in visual_ci.list_diagnostics(
            req.output_dir, req.repo_id, status=req.status or "", limit=req.limit,
        )
    ]


@router.post("/request-retry")
def request_retry(req: RequestRetryReq) -> dict:
    """
    申请受限重试。检查 retry_count，返回是否允许及原诊断的 mechanical 账本。
    """
    result = visual_ci.request_retry(
        req.output_dir, req.repo_id, req.generation_id, max_retries=req.max_retries,
    )
    if not result.get("allowed"):
        # 返回 200 但 allowed=false，由前端决定如何提示
        pass
    return result
