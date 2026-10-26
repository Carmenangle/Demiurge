"""手机端联机同库配置薄路由（逻辑单一属主 services/mobile_remote）。

只在手机独立模式（`LAF_MOBILE_REMOTE=1`）注册；PC 后端不暴露这组端点。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import mobile_remote

router = APIRouter()


class RemoteConfigBody(BaseModel):
    base_url: str = ""
    token: str = ""
    enabled: bool = True


@router.get("/remote")
def read_remote() -> dict:
    """当前联机配置（令牌只报 token_set，不回传本体）。"""
    return mobile_remote.public_config()


@router.post("/remote")
async def write_remote(body: RemoteConfigBody) -> dict:
    """保存并探测。探测失败也保存（用户可先填好、下次再连），由 probe 字段如实报告。"""
    try:
        saved = mobile_remote.save_config(
            base_url=body.base_url, token=body.token, enabled=body.enabled)
    except mobile_remote.RemoteConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = mobile_remote.public_config(saved)
    probe = None
    if cfg["active"]:
        probe = await mobile_remote.probe(
            base_url=saved["base_url"], token=saved["token"])
    return {"config": cfg, "probe": probe}


@router.post("/remote/test")
async def test_remote() -> dict:
    """用已保存的配置探一次 PC（设置面板的「测试连接」）。"""
    cfg = mobile_remote.load_config()
    if not mobile_remote.is_active(cfg):
        raise HTTPException(status_code=400, detail="尚未启用联机同库（先保存电脑地址与令牌）")
    return await mobile_remote.probe(base_url=cfg["base_url"], token=cfg["token"])
