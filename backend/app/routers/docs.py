"""只读文档接口：新人引导「独立文档页」的取数口。业务在 services/doc_library。"""
from fastapi import APIRouter, HTTPException, Query

from app.services import doc_library

router = APIRouter()


@router.get("/doc")
def get_doc(
    path: str = Query("", description="相对仓库根的 .md 路径，如 docs/guide/workflow-template-import.md"),
) -> dict[str, object]:
    """读一篇 docs/ 下的 Markdown，返回 {path, title, content}。

    路径越界 / 非 .md / 不存在一律 400（属调用方传错，不是服务端故障）。
    """
    try:
        return doc_library.read_doc(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
