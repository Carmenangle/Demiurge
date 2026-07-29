"""节点管理路由：封装 ComfyUI-Manager，供前端「节点管理」页四个 tab。

- 已装列表/市场/队列进度：只读。
- 装/更新/卸载/开关/更新ComfyUI/重启：写操作，改环境，前端确认后调。
装/更新/卸载遵循 Manager 的「入队→start 执行→轮询 queue/status→重启」流程。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import COMFYUI_BASE_URL
from app.services import comfy_manager as _mgr
from app.services.comfyui_client import ComfyError

router = APIRouter()

_URL = COMFYUI_BASE_URL


def _guard(fn):
    try:
        return fn()
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.get("/installed")
def installed(url: str = _URL, path: str = "") -> dict:
    """本机已装节点包列表。path=ComfyUI 目录时补本地 git 信息（commit/日期）。"""
    return {"items": _guard(lambda: _mgr.list_installed(url, path))}


@router.get("/market")
def market(url: str = _URL) -> dict:
    """全部节点包（市场）。前端做搜索/分页。"""
    return {"items": _guard(lambda: _mgr.list_market(url))}


@router.get("/queue-status")
def queue_status(url: str = _URL) -> dict:
    """装/更新队列进度，前端轮询。"""
    return _guard(lambda: _mgr.queue_status(url))


class UrlReq(BaseModel):
    url: str = _URL


class PackReq(BaseModel):
    url: str = _URL
    pack: dict                      # 从 installed/market 拿到的节点包对象，原样回传
    selected_version: str = ""      # 仅 install 用，指定版本；空=默认


@router.post("/install")
def install(req: PackReq) -> dict:
    """入队安装某节点包（随后需 /start 执行）。"""
    return _guard(lambda: _mgr.enqueue_install(req.url, req.pack, req.selected_version))


@router.post("/update")
def update(req: PackReq) -> dict:
    return _guard(lambda: _mgr.enqueue_update(req.url, req.pack))


class GitUpdateReq(BaseModel):
    path: str                       # ComfyUI 目录
    pack: dict                      # 节点包对象（用 repository/id/title 定位本地目录）


@router.post("/git-update")
def git_update(req: GitUpdateReq) -> dict:
    """直连 git 更新 nightly（git-HEAD）插件：git pull --ff-only，绕开 Manager 不可靠的队列。
    立即生效（重启后加载新代码），无需入队/start。"""
    return _guard(lambda: _mgr.git_update(req.path, req.pack))


class TrackedUpdateReq(BaseModel):
    path: str                        # ComfyUI 目录
    pack: dict                       # 节点包对象（用 repository/id/title 定位本地目录）
    python_exe: str = ""             # 可选：显式指定 ComfyUI 解释器
    proxy: str = ""                  # 可选：git/pip 代理
    allow_sensitive: bool = False    # true=用户已确认可以改动共享依赖
    skip_deps: bool = False          # true=只更代码，完全不碰依赖


@router.post("/update-tracked")
def update_tracked(req: TrackedUpdateReq) -> dict:
    """git 直连更新 + 依赖预检 + 真实进度 + 结果校验（见 services/node_update）。

    与 /git-update 的区别：这个是后台任务 + 可轮询进度，且会在依赖要改动共享库时
    先停下来等用户确认，而不是直接把环境改掉。
    """
    from pathlib import Path

    from app.services import comfy_launcher, node_update

    d = _mgr.find_pack_dir(req.path, req.pack)
    if d is None:
        title = req.pack.get("title") or req.pack.get("id") or "该插件"
        raise HTTPException(status_code=404,
                            detail=f"找不到「{title}」的本地 git 目录，无法用 git 更新。")
    python_exe = req.python_exe.strip()
    if not python_exe and req.path:
        python_exe = comfy_launcher.find_python(Path(req.path)) or ""
    return node_update.start(str(d), python_exe, req.proxy,
                             req.allow_sensitive, req.skip_deps)


@router.get("/update-progress")
def update_progress() -> dict:
    """更新进度快照（含已下载字节数、速度、依赖清单），前端轮询。"""
    from app.services import node_update
    return node_update.progress()


class CoreDepsReq(BaseModel):
    path: str                        # ComfyUI 目录
    python_exe: str = ""
    proxy: str = ""


@router.post("/core-requirements")
def core_requirements(req: CoreDepsReq) -> dict:
    """核对 ComfyUI 本体依赖是否被满足（只预检不安装）。

    切版本只换代码不装依赖，会留下「新代码 + 旧依赖」的半更新态，
    典型表现是量化权重/CLIP 加载失败。切完版本应调这个提示用户。
    """
    from pathlib import Path

    from app.services import comfy_launcher, node_update

    python_exe = req.python_exe.strip()
    if not python_exe and req.path:
        python_exe = comfy_launcher.find_python(Path(req.path)) or ""
    try:
        return node_update.check_core_requirements(req.path, python_exe, req.proxy)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"依赖核对失败：{e}") from e


@router.post("/uninstall")
def uninstall(req: PackReq) -> dict:
    return _guard(lambda: _mgr.enqueue_uninstall(req.url, req.pack))


@router.post("/disable")
def disable(req: PackReq) -> dict:
    return _guard(lambda: _mgr.enqueue_disable(req.url, req.pack))


@router.post("/start")
def start(req: UrlReq) -> dict:
    """执行已入队的装/更新/卸载任务。"""
    return _guard(lambda: _mgr.start_queue(req.url))


class CheckUpdatesReq(BaseModel):
    path: str                       # ComfyUI 目录
    proxy_url: str = ""             # 设置里的代理地址（空=直连）


@router.post("/check-updates-git")
def check_updates_git(req: CheckUpdatesReq) -> dict:
    """自建检查更新：遍历 custom_nodes 直接 git fetch（带代理）比对，绕开 Manager 超时。
    返回 {updatable:{目录名:bool}, checked, failed:[...]}。"""
    return _guard(lambda: _mgr.check_updates_git(req.path, req.proxy_url))


@router.post("/update-comfyui")
def update_comfyui(req: UrlReq) -> dict:
    return _guard(lambda: _mgr.update_comfyui(req.url))


@router.post("/reboot")
def reboot(req: UrlReq) -> dict:
    """重启 ComfyUI（装/更新/卸载后生效，走 Manager）。"""
    return _guard(lambda: _mgr.reboot(req.url))


class GitUrlReq(BaseModel):
    url: str = _URL
    git_url: str                    # 要安装的插件 GitHub 链接


@router.post("/install-git")
def install_git(req: GitUrlReq) -> dict:
    """用 GitHub 链接安装插件（自动装 requirements.txt 依赖）。随后需 /start + 重启。"""
    return _guard(lambda: _mgr.install_git_url(req.url, req.git_url))


@router.get("/comfyui-versions")
def comfyui_versions(url: str = _URL) -> dict:
    """ComfyUI 可切换版本列表（nightly=开发版，vX=稳定版）。Manager 只给最近几个。"""
    return _guard(lambda: _mgr.comfyui_versions(url))


@router.get("/comfyui-git-versions")
def comfyui_git_versions(path: str = "") -> dict:
    """全量 ComfyUI 版本（直接读 git tag，对齐图1）。path=ComfyUI 目录。
    返回 {versions:[{version,date}], current}。"""
    return _guard(lambda: _mgr.git_versions(path))


class SwitchVerReq(BaseModel):
    url: str = _URL
    ver: str


@router.post("/switch-comfyui")
def switch_comfyui(req: SwitchVerReq) -> dict:
    """切换 ComfyUI 到指定版本。随后需 /start + 重启。"""
    return _guard(lambda: _mgr.switch_comfyui_version(req.url, req.ver))


class AnalyzeReq(BaseModel):
    url: str = _URL
    workflow: dict                  # 工作流 JSON（UI 或 API 格式）


@router.post("/analyze-workflow")
def analyze_workflow(req: AnalyzeReq) -> dict:
    """识别工作流缺失的节点并映射到可安装的节点包。
    返回 {missing_types, packs, unresolved}。packs 里的对象可直接传 /install。"""
    return _guard(lambda: _mgr.analyze_workflow(req.url, req.workflow))
