from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import output_path_migration

router = APIRouter()


@router.get("/")
def list_assets() -> dict[str, object]:
    return {"items": []}


class OutputPathRequest(BaseModel):
    old_dir: str
    new_dir: str


@router.post("/output-path/audit")
def audit_output_path(req: OutputPathRequest) -> dict[str, object]:
    """保存新输出路径前，检查旧路径中仍被资产库引用的文件。"""
    try:
        return output_path_migration.audit(req.old_dir, req.new_dir)
    except output_path_migration.MigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/output-path/migrate")
def migrate_output_path(req: OutputPathRequest) -> dict[str, object]:
    """迁移资产文件并同步资产索引、聊天快照和仓库封面引用。"""
    try:
        return output_path_migration.migrate(req.old_dir, req.new_dir)
    except output_path_migration.MigrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
