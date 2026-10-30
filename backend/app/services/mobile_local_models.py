"""手机本机模型配置：联机同库时手机上的模型设置只落手机，绝不回写电脑。

背景：联机态整个 `/api/user-state`（含 settings）由 PC 提供；模型字段若也走那条
通道，手机上改模型就会把 PC 的模型配置改掉、且手机在断开后丢掉自己的设置。
故模型字段单独走本机端点（`/api/mobile/*` 永不被反向代理），前端联机时用本机
模型覆盖 PC 快照，只把模型发给执行请求的后端。

字段面＝模型类设置的单一清单（清单属主 `services.secret_fields.MODEL_*`），
其余 settings（ComfyUI 地址、路径、预设…）仍归电脑。
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

from app.services import mobile_remote, repo_meta
from app.services.secret_fields import (
    MODEL_LIST_KEYS,
    MODEL_OBJ_KEYS,
    keep_stored_secrets,
)

# 手机自己的模型设置：列表型 + 选中 id + 对象型（嵌入 / 视觉）
MODEL_KEYS: tuple[str, ...] = (
    *MODEL_LIST_KEYS,
    "activeChatModelId",
    "activeImageModelId",
    "activeVideoModelId",
    *MODEL_OBJ_KEYS,
)

_LIST_KEYS = frozenset(MODEL_LIST_KEYS)
_OBJ_KEYS = frozenset(MODEL_OBJ_KEYS)
_ID_KEYS = frozenset({"activeChatModelId", "activeImageModelId", "activeVideoModelId"})


class LocalModelError(ValueError):
    """请求体形状不对（路由层转 400）。"""


def _settings(state: dict) -> dict:
    settings = state.get("settings")
    return settings if isinstance(settings, dict) else {}


def _pick(settings: dict) -> dict:
    return {key: settings[key] for key in MODEL_KEYS if key in settings}


def read_models() -> dict:
    """读手机本机模型字段（缺失/损坏返回空对象，前端据此回退 PC 快照）。"""
    return _pick(_settings(repo_meta.load_user_state()))


def preserve_stored_models(incoming: object, stored: object) -> object:
    """PC 侧对应护栏：缺省的模型字段沿用盘上旧值。

    联机手机把设置回写电脑时会**摘掉**模型字段（模型只存手机、只随请求走），若电脑
    仍按整对象覆盖写，一次手机改主题就会把电脑自己的模型/选中项抹掉。缺席≠删除，
    与 `selection` 同款语义；显式带上（哪怕是空数组）仍照常覆盖。
    """
    if not isinstance(incoming, dict):
        return incoming
    base = stored if isinstance(stored, dict) else {}
    out = dict(incoming)
    for key in MODEL_KEYS:
        if key not in out and key in base:
            out[key] = base[key]
    return out


def _validate(patch: object) -> dict:
    if not isinstance(patch, dict):
        raise LocalModelError("models 必须是对象")
    out: dict = {}
    for key, value in patch.items():
        if key not in MODEL_KEYS:
            continue  # 非模型字段（ComfyUI 地址/路径/预设…）归电脑，本机不收
        if key in _LIST_KEYS and not isinstance(value, list):
            raise LocalModelError(f"{key} 必须是数组")
        if key in _OBJ_KEYS and not isinstance(value, dict):
            raise LocalModelError(f"{key} 必须是对象")
        if key in _ID_KEYS and not isinstance(value, str):
            raise LocalModelError(f"{key} 必须是字符串")
        out[key] = value
    return out


def write_models(patch: object) -> dict:
    """合并写手机本机模型字段；repos/selection/其他设置原样保留。

    空密钥不覆盖本机已有非空值（与 user-state 整对象写入同一护栏）：联机回填失败时
    前端会推一份按 stripSecrets 清空的副本，不能借此把手机上的真 key 抹掉。
    """
    incoming = _validate(patch)
    state = repo_meta.load_user_state()
    settings = dict(_settings(state))
    for key, value in incoming.items():
        settings[key] = keep_stored_secrets(value, settings.get(key), key)
    state["settings"] = settings
    repo_meta.save_user_state(state)
    return _pick(settings)


def lan_ip() -> str:
    """手机在局域网里的地址（用于把模型 URL 里的 127.0.0.1 换成电脑可达的地址）。

    取「本机到 PC 那块网卡」的源地址：UDP connect 不发包，只让内核选路由；拿不到
    PC 地址时退回公网地址选默认路由。失败返回空串（前端不提供一键替换）。
    """
    cfg = mobile_remote.load_config()
    host = ""
    port = 80
    if cfg.get("base_url"):
        parsed = urlparse(cfg["base_url"])
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    targets = [(host, port), ("8.8.8.8", 53)]
    for target_host, target_port in targets:
        if not target_host:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.5)
                sock.connect((target_host, target_port))
                ip = sock.getsockname()[0]
        except OSError:
            continue
        if ip and not ip.startswith("127."):
            return ip
    return ""
