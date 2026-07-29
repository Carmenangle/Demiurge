"""GIF ↔ 精灵图互转。业务在 services/gif_sprite，本层只做入参校验与转发。

图片走 JSON body 的 data URI（见 services/image_payload 的说明）。
"""
import io

from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel

from app.services import gif_sprite
from app.services.image_payload import PayloadError, decode_data_uri, to_data_uri

router = APIRouter()


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


class DecodeRequest(BaseModel):
    image: str = ""            # data:image/gif;base64,...


@router.post("/decode")
def decode(req: DecodeRequest) -> dict[str, object]:
    """GIF → 逐帧 PNG。前端拿去做预览、勾选剔除、Canvas 拼合。"""
    try:
        data = decode_data_uri(req.image, "GIF")
        frames, durations = gif_sprite.decode_gif(data)
    except (PayloadError, ValueError, OSError) as e:
        raise _bad(e) from e
    return {
        "frames": [to_data_uri(gif_sprite.to_png_bytes(f)) for f in frames],
        "durations": durations,
        "width": frames[0].width,
        "height": frames[0].height,
    }


class ComposeRequest(BaseModel):
    frames: list[str] = []      # 逐帧 data URI（顺序即拼合顺序）
    cols: int = 0               # <=0 表示铺成一行
    padding: int = 0
    background: str = ""        # 空 = 透明底


@router.post("/compose")
def compose(req: ComposeRequest) -> dict[str, object]:
    """帧列表 → 精灵图 PNG。"""
    if not req.frames:
        raise HTTPException(status_code=400, detail="没有帧可以拼合。")
    try:
        imgs = [_open(decode_data_uri(f, "帧")) for f in req.frames]
        sheet = gif_sprite.compose_sheet(
            imgs, req.cols, req.padding, req.background or None)
    except (PayloadError, ValueError, OSError) as e:
        raise _bad(e) from e
    return {
        "image": to_data_uri(gif_sprite.to_png_bytes(sheet)),
        "width": sheet.width,
        "height": sheet.height,
    }


class SliceRequest(BaseModel):
    image: str = ""
    cols: int = 1
    rows: int = 1
    padding: int = 0
    drop_empty: bool = True     # 关掉可保留真正的空白帧


@router.post("/slice")
def slice_sheet(req: SliceRequest) -> dict[str, object]:
    """精灵图 → 逐帧 PNG。"""
    try:
        data = decode_data_uri(req.image, "精灵图")
        frames = gif_sprite.slice_sheet(
            data, req.cols, req.rows, req.padding, req.drop_empty)
    except (PayloadError, ValueError, OSError) as e:
        raise _bad(e) from e
    return {
        "frames": [to_data_uri(gif_sprite.to_png_bytes(f)) for f in frames],
        "count": len(frames),
    }


class EncodeRequest(BaseModel):
    frames: list[str] = []
    duration_ms: int = 100
    transparent: bool = True


@router.post("/encode")
def encode(req: EncodeRequest) -> dict[str, object]:
    """帧列表 → GIF。"""
    if not req.frames:
        raise HTTPException(status_code=400, detail="没有帧可以合成。")
    try:
        imgs = [_open(decode_data_uri(f, "帧")) for f in req.frames]
        data = gif_sprite.encode_gif(imgs, req.duration_ms, req.transparent)
    except (PayloadError, ValueError, OSError) as e:
        raise _bad(e) from e
    # 回读真实帧数：连续相同帧会被合并，报输入帧数会与用户下载到的文件不符
    return {"image": to_data_uri(data, "image/gif"),
            "frames": gif_sprite.count_frames(data),
            "input_frames": len(imgs)}
