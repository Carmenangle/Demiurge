"""图片分辨率缩放。业务在 services/image_resize，本层只做入参校验与转发。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import image_resize
from app.services.image_payload import PayloadError, decode_data_uri, to_data_uri

router = APIRouter()


class ProbeRequest(BaseModel):
    image: str = ""


@router.post("/probe")
def probe(req: ProbeRequest) -> dict[str, object]:
    """只回原图尺寸，供前端显示与预填目标尺寸。"""
    try:
        w, h = image_resize.probe(decode_data_uri(req.image, "图片"))
    except (PayloadError, ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"width": w, "height": h}


class ResizeRequest(BaseModel):
    image: str = ""
    target_w: int = 0
    target_h: int = 0
    scale: float = 0.0          # >0 时优先于 target_w/h
    keep_aspect: bool = True
    filter_name: str = "lanczos"
    sharpen: int = 0            # 0=不锐化（实测 >30% 反而降保真，见 service 注释）
    format: str = "png"         # png/jpeg/webp
    quality: int = 92           # 仅 jpeg/webp


@router.post("/resize")
def resize(req: ResizeRequest) -> dict[str, object]:
    """缩放图片。png 无损；jpeg/webp 按 quality 有损压缩。"""
    if req.target_w <= 0 and req.target_h <= 0 and req.scale <= 0:
        raise HTTPException(status_code=400, detail="请给出目标尺寸或缩放比例。")
    try:
        data = decode_data_uri(req.image, "图片")
        out, w, h, mime = image_resize.resize(
            data, req.target_w, req.target_h, req.scale, req.keep_aspect,
            req.filter_name, req.sharpen, req.format, req.quality)
    except (PayloadError, ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "image": to_data_uri(out, mime),
        "width": w,
        "height": h,
        "bytes": len(out),
    }
