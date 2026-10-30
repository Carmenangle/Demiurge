"""手机端联机同库配置薄路由（逻辑单一属主 services/mobile_remote）。

只在手机独立模式（`LAF_MOBILE_REMOTE=1`）注册；PC 后端不暴露这组端点。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.services import mobile_local_models, mobile_remote, mobile_sync

router = APIRouter()


class RemoteConfigBody(BaseModel):
    base_url: str = ""
    token: str = ""
    enabled: bool = True


class SyncBody(BaseModel):
    overwrite: bool = False


class LocalModelsBody(BaseModel):
    models: dict = Field(default_factory=dict)


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


@router.get("/online-status")
async def read_online_status() -> dict:
    """联机体检聚合探针（手机「联机检测」弹窗的唯一数据源）。

    未启用联机时不发任何请求，直接返回 `active: false`；启用后依次答「电脑可达吗」
    「电脑的 ComfyUI 按它设置的地址开着吗」。本端点在 `/api/mobile` 前缀下，永不被
    反向代理 ⇒ 电脑掉线时这条仍答得出原因。
    """
    return await mobile_remote.online_status()


@router.get("/local-models")
def read_local_models() -> dict:
    """手机本机模型设置（联机态下的唯一真源）＋ 手机局域网地址。

    联机时其余用户态由电脑提供，只有模型字段留在手机；`lan_ip` 供模型页把
    `127.0.0.1` 一键换成电脑可达的地址。
    """
    return {
        "models": mobile_local_models.read_models(),
        "lan_ip": mobile_local_models.lan_ip(),
    }


@router.post("/local-models")
def write_local_models(body: LocalModelsBody) -> dict:
    """只合并写模型字段；仓库、选中项与电脑负责的其他设置原样保留。"""
    try:
        models = mobile_local_models.write_models(body.models)
    except mobile_local_models.LocalModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "models": models}


@router.get("/sync")
def read_sync() -> dict:
    """在线同步状态：面板轮询（idle/downloading/importing/done/error）。"""
    return mobile_sync.status()


@router.post("/sync")
def start_sync(body: SyncBody | None = Body(default=None)) -> dict:
    """从电脑拉一份资源包并导入本机（后台任务；进度看 GET /sync）。

    目标地址与令牌只取本机 `remote_backend.json`，请求体只有 overwrite——不接受
    前端指定源地址，避免把 PC 令牌发往任意主机。
    """
    try:
        return mobile_sync.start(overwrite=bool(body and body.overwrite))
    except mobile_sync.SyncBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except mobile_sync.SyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
