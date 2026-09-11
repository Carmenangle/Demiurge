"""调色盘提取 + 当前色彩约束的读写。业务在 services/palette 与 services/palette_pref。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import palette, palette_pref
from app.services.image_payload import PayloadError, decode_data_uri, to_data_uri

router = APIRouter()


class ExtractRequest(BaseModel):
    image: str = ""
    max_colors: int = 16
    bit_depth: str = "rgb888"    # rgb888/565/555/444/332
    method: str = "mediancut"    # mediancut/octree


@router.post("/extract")
def extract(req: ExtractRequest) -> dict[str, object]:
    """图片 → hex 色值列表（按占比降序）+ 量化预览图。"""
    try:
        data = decode_data_uri(req.image, "图片")
        colors, preview = palette.extract(
            data, req.max_colors, req.bit_depth, req.method)
    except (PayloadError, ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "colors": colors,
        "preview": to_data_uri(palette.to_png_bytes(preview)),
        "count": len(colors),
    }


class ConstraintRequest(BaseModel):
    repo_id: str = ""            # 小仓库 id；"home" 表示临时会话
    colors: list[str] = []       # 空列表 = 清除约束
    name: str = ""


@router.put("/constraint")
def set_constraint(req: ConstraintRequest) -> dict[str, object]:
    """设为「当前色彩约束」，后续 AI 编排/生图自动带上这组颜色。"""
    if not req.repo_id.strip():
        raise HTTPException(status_code=400, detail="repo_id 为空")
    return palette_pref.save(req.repo_id.strip(), req.colors, req.name)


@router.get("/constraint")
def get_constraint(repo_id: str = "") -> dict[str, object]:
    """读当前色彩约束，供前端回显。"""
    if not repo_id.strip():
        raise HTTPException(status_code=400, detail="repo_id 为空")
    return palette_pref.load(repo_id.strip())
