"""L2 联机同库：远程客户端能力闸（单一属主）。

语义：`LAF_ALLOW_REMOTE=1` 放开的是「网络可达性」（request_guard 三道闸），不是
「能力面」。PC 后端带着无鉴权的进程控制 / 任意路径读写端点（`app.main` 回环门禁的
存在理由），一旦对远端可达就必须收敛能力面。判定按「客户端是否回环」二分：
**PC 本机行为逐字不变**；远端按下面的路由类表放行/收敛。

三件套合同（缺一即拒，L2 定案 2026-10-26）：
  `LAF_ALLOW_REMOTE=1` ＋ `LAF_LOCAL_TOKEN`（令牌鉴权，local_auth）＋ Tailscale Serve
  （https 传输）。**裸 `LAF_ALLOW_REMOTE`（未设令牌）＝远端一律 403**——宁可拒服务，
  也不把含密钥与进程控制的接口裸奔到局域网。

路由类表（单一真源；改这里必须同步 G/16 与 test_remote_gate）：
- DENY（远程 403）：进程控制（comfyui start/stop/restart）、PC 路径配置
  （comfyui/config）、节点管理（node-manager，含 clone+pip 安装面）、模型下载
  （models）、GGUF 导入（gguf）、visual-ci（PC 开发工具）。
- SCOPE（远程时收敛到数据根）：任意路径读写端点——`comfyui/local-view`（读，query
  参数 path，中间件可查）与 `comfyui/save-local`（写，body 的 output_dir，路由体内
  调 `ensure_write_scope`——中间件读不到 JSON body）。允许根＝`DATA_DIR` 与已配置的
  作品根（user_state 的 outputDir）。PC 本机「读任意本地路径」的设计红线
  （local_view docstring）不受影响：jail 只发生在远程分支。
- 其余端点（剧情/资产/预设/聊天/health/…）：远程可用——L2 的本意就是手机消费
  PC 数据真源。

usage_sync（写回 user_state.json 的 outputDir）注意：改路径配置走前端设置页的
user-state 端点，不属 comfyui/config，仍远程可用——但作品根变了 SCOPE 允许根随之
变化，属用户自担的配置行为。
"""
from __future__ import annotations

from pathlib import Path

from app import config
from app.services import repo_meta, request_guard

INTERLOCK_DETAIL = (
    "远端访问需要 LAF_LOCAL_TOKEN（裸 LAF_ALLOW_REMOTE 一律拒绝；"
    "三件套＝ALLOW_REMOTE + 令牌 + Tailscale Serve）"
)

_DENY_PREFIXES = (
    "/api/comfyui/start",
    "/api/comfyui/stop",
    "/api/comfyui/restart",
    "/api/comfyui/config",
    "/api/node-manager",
    "/api/models",
    "/api/gguf",
    "/api/visual-ci",
    # 离线资源同步：导出会把 PC 的模型密钥、作品与素材打进 zip，属 PC 独占能力。
    "/api/config",
)
SCOPED_READ = "/api/comfyui/local-view"
_SCOPED_WRITE = "/api/comfyui/save-local"


def is_remote(host: str) -> bool:
    return not request_guard.is_loopback_client(host)


def _allowed_roots() -> list[Path]:
    """SCOPE 允许根：DATA_DIR ＋ 已配置的作品根（未配置/读失败只剩 DATA_DIR）。"""
    roots = [Path(config.DATA_DIR)]
    try:
        state = repo_meta.load_user_state()
        raw = str(state.get("outputDir") or state.get("output_dir") or "").strip()
        if raw:
            roots.append(Path(raw))
    except Exception:  # noqa: BLE001 — user_state 缺失/坏 JSON：退 DATA_DIR，不挡判定
        pass
    return roots


def path_in_scope(raw: str) -> bool:
    """路径是否落在允许根内（resolve 后判前缀；解析失败＝不在域内）。"""
    try:
        target = Path(raw).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return any(
        target == root.resolve() or root.resolve() in target.parents
        for root in _allowed_roots()
    )


def verdict(path: str, local_view_path: str | None, *, token_enabled: bool) -> str | None:
    """远程请求的放行判定：None＝放行；str＝403 文案。

    `local_view_path` 只在 path 命中 _SCOPED_READ 时由中间件从 query 提取；
    save-local 的写路径走 `ensure_write_scope`（body 中间件读不到）。
    """
    if not token_enabled:
        return INTERLOCK_DETAIL
    if path.startswith(_DENY_PREFIXES):
        return f"端点 {path} 属 PC 独占能力，远程不可用（remote_gate）"
    if path == SCOPED_READ:
        raw = local_view_path or ""
        if raw and not path_in_scope(raw):
            return "远程仅可读取数据目录与作品根内的文件（remote_gate path scope）"
    return None


def ensure_write_scope(client_host: str, output_dir: str) -> None:
    """save-local 的远程写路径闸（路由体内调用，PC 本机 no-op）。

    抛 ValueError（路由层转 403）；令牌互锁不在此查——中间件已按全路径先行拦截。
    """
    if not is_remote(client_host):
        return
    if not path_in_scope(output_dir):
        raise ValueError(
            "远程保存仅允许写入数据目录与作品根（remote_gate path scope）")
