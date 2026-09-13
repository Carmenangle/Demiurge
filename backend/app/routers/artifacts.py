"""合集卡产物访问端点：预览 / 下载 / 在资源管理器中定位（Claude 式产物卡的后端支撑）。

- GET  /api/artifacts/preview?output_dir=&path=   读取产物文本预览（超大文件截断头部）
- GET  /api/artifacts/download?output_dir=&path=   流式下载原文件（浏览器另存为）
- POST /api/artifacts/open-folder                 在系统资源管理器中定位文件/打开目录

安全（纵深防御，回环门禁已挡住远端）：output_dir 是请求方声明的**作品库根**（scope），
repo_id 决定收窄到哪个**作品域**；path resolve 后必须落在**某个域**内（`_resolve_in`）。
产物域集合 = 作品域 ∪ 本作品拥有的卡目录（见 `repo_meta.artifact_domains`）——
卡一律落 `<作品库根>/<卡名>/`，而卡目录名可以与作品文件夹名不同（实锤仓库「原创」的卡在
`<作品库根>/御仙/`），故写端点仍用「作品库根 + repo_id」，读端点用「域集合」。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.services.collection_artifacts import (
    ArtifactAccessError,
    DOC_ASSET_SUFFIXES,
    artifact_kind,
    domain_owner,
    read_preview_text,
    resolve_artifact,
    reveal_in_folder,
)
from app.services import repo_meta

router = APIRouter()


def _validated_root(output_dir: str) -> str:
    """校验并归一**作品库根**（产物 scope 的第一层门禁）。

    `works_root_violation`：output_dir 必须等于配置的仓库文件夹根（未配置时放行）——
    防客户端把任意目录当 scope；与各写端点一致。
    """
    err = repo_meta.works_root_violation(output_dir)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not output_dir.strip():
        raise HTTPException(status_code=400, detail="缺少作品根目录 output_dir")
    return output_dir.strip()


def _domains(root: str, repo_id: str = "") -> list[Path]:
    """产物访问域集合：有 repo_id → 作品域 ∪ 本作品拥有的卡目录；**无 repo_id → 空集**。

    2026-09-10 用户定案「缺值返回空结果」：不给 repo_id 时**不再**退化成「作品库根」单域——
    那等于全库可见（A 作品的历史消息能读到 B 作品的产物），正是 B3/A 要修的跨作品串味。
    空集下没有可放行的域：单文件端点（preview/download/asset/open-folder/sync-worldbook）
    一律 403；列表端点另在 `artifact_list` 早返回空 items（"不显示"而不是"报错"）。
    取舍：拿不到 repo 上下文的历史调用宁可**不显示**产物，也不串味。

    白名单（`is_allowed_artifact`）按「相对某个域的形态」判定，域一换，`card.json` 就不再
    可跨作品读取，`docs/*.md` 也从「全库共享的恰一层」变成「本作品 docs/」——**收窄域即
    完成隔离，白名单规则本身不用改**（2026-09-10 用户定案口径：作品域 = 仓库/小仓库文件夹）。
    """
    if not repo_id:
        return []
    return repo_meta.artifact_domains(root, repo_id)


def _resolve(root: str, path: str, *, allow_dir: bool = False):
    try:
        return resolve_artifact(root, path, allow_dir=allow_dir)
    except ArtifactAccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _resolve_in(domains: list[Path], path: str, *, allow_dir: bool = False):
    """在候选域集合里解析产物路径：命中哪个域就用**那个域**做 jail 与白名单校验。

    先定位归属域再解析（而不是「逐个域试」）：错误信息（404 文件不存在 / 403 形态不在
    白名单）才与用户实际访问的位置一致，不会因为先拿另一个域去试而报出误导性的越界错误。
    """
    try:
        target = Path(path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"路径解析失败：{exc}") from exc
    owner = domain_owner(target, domains)
    if owner is None:
        raise HTTPException(status_code=403, detail="产物路径超出本作品的产物域范围")
    return _resolve(str(owner), path, allow_dir=allow_dir)


@router.get("/list")
def artifact_list(
    output_dir: str = Query(..., description="仓库文件夹根（产物 scope）"),
    repo_id: str = Query("", description="当前作品 id：给了就只列**本作品**的产物（2026-09-10 B3/A）"),
) -> dict:
    """列出当前作品的交付产物（历史消息补卡用）：前端对已落盘的智能编造结果消息
    惰性拉取，展示「当前作品目录里最新的产物」。

    2026-09-10 B3/A：`repo_id` 从「软提示」变成真参与域解析——域收窄到作品域（∪ 本作品
    拥有的卡目录，卡名可与作品名不同）后，补卡不再看到**别作品**的卡/文档（此前 `since=0`
    会退回「全作品库最新 mtime 组」+「全库最新 8 份 docs」，在 A 作品的历史消息里能刷出
    B 作品刚产出的东西）。

    2026-09-10 用户定案「缺值返回空结果」：**不给 repo_id 时直接回空 items**，不再退化成
    「作品库根」单域（那仍是全库可见）。前端对老消息就不再渲染产物卡——不串味优先。
    """
    root = _validated_root(output_dir)
    if not repo_id:
        return {"ok": True, "items": []}
    from app.services.collection_artifacts import collect_artifacts
    return {"ok": True, "items": collect_artifacts(root, repo_id=repo_id)}


@router.get("/preview")
def artifact_preview(
    output_dir: str = Query(..., description="仓库文件夹根（产物 scope）"),
    path: str = Query(..., description="产物绝对路径"),
    repo_id: str = Query("", description="当前作品 id（收窄到作品目录）"),
) -> dict:
    """读取产物文本用于预览；超大文件截断并标记 truncated，前端提示另存查看。

    kind 走 `collection_artifacts.artifact_kind` 单一属主（card/worldbook/doc/file）：
    md 文档会返回 `doc`，前端据此走 Markdown 渲染（而非纯文本 `<pre>`）。
    """
    file = _resolve_in(_domains(_validated_root(output_dir), repo_id), path)
    try:
        text, truncated = read_preview_text(file)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读取产物失败：{exc}") from exc
    return {
        "ok": True,
        "name": file.name,
        "size": file.stat().st_size,
        "kind": artifact_kind(file),
        "text": text,
        "truncated": truncated,
    }


@router.get("/download")
def artifact_download(
    output_dir: str = Query(..., description="仓库文件夹根（产物 scope）"),
    path: str = Query(..., description="产物绝对路径"),
    repo_id: str = Query("", description="当前作品 id（收窄到作品目录）"),
) -> FileResponse:
    """流式下载原文件（Content-Disposition: attachment）。"""
    file = _resolve_in(_domains(_validated_root(output_dir), repo_id), path)
    return FileResponse(
        file,
        media_type="application/octet-stream",
        filename=file.name,
    )


@router.get("/asset")
def artifact_asset(
    output_dir: str = Query(..., description="仓库文件夹根（产物 scope）"),
    path: str = Query(..., description="作品域内素材绝对路径（docs/assets/ 下的图片）"),
    repo_id: str = Query("", description="当前作品 id（收窄到作品目录）"),
) -> FileResponse:
    """**内联**读取作品域内的图片素材（文档插图渲染用，2026-09-10 链路③）。

    与 `/download` 的区别：不带 `Content-Disposition: attachment`、按扩展名回正确
    media_type——否则浏览器不把 `<img src>` 当图片渲染（下载端点只适合「另存为」）。
    白名单与预览/下载同一套（`is_allowed_artifact`），仍受产物域目录 jail 约束。

    **只服务插图素材**（2026-09-10 C4）：本端点按语义就是给 `<img src>` 用的，此前却
    对任何白名单产物都内联返回（card.json 也能拿到 `application/json` 内联响应）。
    后缀收口到 `DOC_ASSET_SUFFIXES` 单一属主——正文/其他产物走 `/preview` 或 `/download`。
    """
    import mimetypes

    file = _resolve_in(_domains(_validated_root(output_dir), repo_id), path)
    if file.suffix.lower() not in DOC_ASSET_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"/asset 只服务文档插图素材（图片），不支持 {file.suffix or '无扩展名'}："
                   f"{file.name}。文本预览请用 /preview，下载请用 /download。")
    media_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    return FileResponse(file, media_type=media_type)


@router.get("/versions/file")
def artifact_version_file(
    output_dir: str = Query(..., description="仓库文件夹根（产物 scope）"),
    version_id: str = Query(..., description="版本目录名，如 001-20260909103000"),
    rel: str = Query(..., description="版本内文件相对作品根的路径，同 meta.files[].rel"),
    repo_id: str = Query("", description="当前作品 id（收窄到作品目录）"),
) -> dict:
    """读取**版本副本**的文本用于预览（2026-09-10 B5）。

    为什么不能复用 `/preview`：`/versions` 每条 `files[]` 附带的 `path` 是
    **当前活文件**路径，回档前查看旧版本会显示**现在的**文件内容（当前文件被删还会
    404）——版本历史就成了摆设。本端点以版本目录内的副本为真源
    （`artifact_versions.resolve_version_file`：meta.files 白名单 + jail），
    返回形态与 `/preview` 一致（含 kind，前端据此走 Markdown/JSON 渲染）。

    2026-09-10 B3/A：`output_dir` 仍是**作品库根**，`repo_id` 由 `resolve_version_file`
    自己解析存储域（作品域）——与快照同一口径，路由不再各算一遍域。
    """
    root = _validated_root(output_dir)
    from app.services.artifact_versions import resolve_version_file
    try:
        file = resolve_version_file(root, version_id, rel, repo_id=repo_id)
    except Exception as exc:  # noqa: BLE001 - 统一转 HTTP 错误（含 VersionError 档位）
        raise _version_errors(exc) from exc
    try:
        text, truncated = read_preview_text(file)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读取版本文件失败：{exc}") from exc
    return {
        "ok": True,
        "version_id": version_id,
        "rel": rel,
        "name": file.name,
        "size": file.stat().st_size,
        "kind": artifact_kind(file),
        "text": text,
        "truncated": truncated,
    }


class VersionRequest(BaseModel):
    output_dir: str = ""
    version_id: str = ""  # 版本目录名，如 001-20260909103000
    repo_id: str = ""     # 当前作品 id：与 GET 端点同口径收窄到作品目录（B3/A）


def _version_errors(exc: Exception) -> HTTPException:
    from app.services.artifact_versions import VersionError
    if isinstance(exc, VersionError):
        return HTTPException(status_code=exc.status, detail=exc.detail)
    return HTTPException(status_code=500, detail=f"版本操作失败：{exc}")


@router.get("/versions")
def artifact_versions_list(
    output_dir: str = Query(..., description="仓库文件夹根（产物 scope）"),
    repo_id: str = Query("", description="当前作品 id（收窄到作品目录）"),
) -> dict:
    """列出产物版本历史（新→旧）。每个版本可预览/回档/删除。

    files 里给每个条目补**活文件**绝对 path（wire 供前端预览/下载直接回传），
    与产物卡的 ArtifactMeta.path 用法一致：域内文件 = `存储域/rel`，域外卡
    （卡名≠作品名，meta 带 `origin_dir`）= `origin_dir/name`。
    B3/A 后版本库随作品域走（此前是作品库根下的全局单例，A 作品的回档列表里
    能刷出 B 作品的版本）。
    """
    root = _validated_root(output_dir)
    from app.services.artifact_versions import list_versions, storage_root as _storage_root
    versions = list_versions(root, repo_id=repo_id)
    storage = _storage_root(root, repo_id)
    for version in versions:
        for entry in version.get("files") or []:
            rel = str(entry.get("rel") or "")
            origin = str(entry.get("origin_dir") or "")
            name = str(entry.get("name") or "")
            if origin and name:
                entry["path"] = str(Path(origin) / name)
            else:
                entry["path"] = str(storage / rel) if rel else ""
    return {"ok": True, "versions": versions}


@router.post("/versions/restore")
def artifact_version_restore(req: VersionRequest) -> JSONResponse:
    """回档到指定版本：先给当前状态自动存档（pre_restore），再把版本文件覆盖回写。"""
    root = _validated_root(req.output_dir)
    from app.services.artifact_versions import restore_version
    try:
        result = restore_version(root, req.version_id, repo_id=req.repo_id)
    except Exception as exc:  # noqa: BLE001 - 统一转 HTTP 错误
        raise _version_errors(exc) from exc
    return JSONResponse({"ok": True, **result})


@router.post("/versions/delete")
def artifact_version_delete(req: VersionRequest) -> JSONResponse:
    """删除指定版本（仅版本目录，不动当前产物）。"""
    root = _validated_root(req.output_dir)
    from app.services.artifact_versions import delete_version
    try:
        delete_version(root, req.version_id, repo_id=req.repo_id)
    except Exception as exc:  # noqa: BLE001 - 统一转 HTTP 错误
        raise _version_errors(exc) from exc
    return JSONResponse({"ok": True})


class SyncWorldbookRequest(BaseModel):
    output_dir: str = ""
    path: str = ""  # 产物文件绝对路径（card.json / worldbook.json），据此解析卡目录名
    repo_id: str = ""  # 当前作品 id（收窄到作品目录，B3/A）


@router.post("/sync-worldbook")
def artifact_sync_worldbook(req: SyncWorldbookRequest) -> JSONResponse:
    """把作品内卡目录的世界书同步到独立世界书（资产库 worlds/<卡名>.json）。

    手动同步（2026-09-09 用户定案：不自动）：产物完成消息下方的「同步到资产库」按钮
    调用本端点——读卡目录的 worldbook.json，覆盖写 worldbookDir/<卡名>.json
    （旧版先备份为 <卡名>.json.bak-<时间戳>）。仅同步世界书，不动主卡。

    2026-09-10 B3/A：卡目录由**所属域**解析（作品域或本作品拥有的卡目录——
    卡名可与作品名不同），`target` 与 `target.parent/worldbook.json` 都在同一卡目录内。
    """
    root = _validated_root(req.output_dir)
    target = _resolve_in(_domains(root, req.repo_id), req.path, allow_dir=True)
    from pathlib import Path
    from app.config import DATA_DIR
    try:
        import json as _json0
        _st = _json0.loads((DATA_DIR / "user_state.json").read_text(encoding="utf-8"))
        wb_dir = str((_st.get("settings") or {}).get("worldbookDir") or "").strip()
    except (OSError, ValueError):
        wb_dir = ""
    if not wb_dir:
        raise HTTPException(status_code=400, detail="未配置世界书文件夹（worldbookDir），无法同步")
    card_name = str(target.parent.name or "").strip()
    if not card_name or card_name in (".", ".."):
        raise HTTPException(status_code=400, detail="无法从产物路径解析卡目录名")
    src = target.parent / "worldbook.json"
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"卡目录缺少 worldbook.json：{src}")
    try:
        import json as _json
        import shutil as _shutil
        import time as _time
        book = _json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(book.get("entries"), (list, dict)):
            raise ValueError("worldbook.json 缺少 entries")
        dst = Path(wb_dir) / f"{card_name}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file():
            _shutil.copy2(dst, dst.with_name(f"{card_name}.json.bak-{int(_time.time())}"))
        _shutil.copy2(str(src), str(dst))
        count = len(book.get("entries") or [])
        return JSONResponse({"ok": True, "dest": str(dst), "entries": count})
    except Exception as exc:  # noqa: BLE001 - 统一转 HTTP 错误
        raise HTTPException(status_code=500, detail=f"同步失败：{exc}") from exc


class OpenFolderRequest(BaseModel):
    output_dir: str = ""
    path: str = ""       # 要定位的文件；传目录则直接打开该目录
    select: bool = True  # 定位文件时是否选中（默认 true）
    repo_id: str = ""    # 当前作品 id（收窄到作品目录，B3/A）


@router.post("/open-folder")
def artifact_open_folder(req: OpenFolderRequest) -> JSONResponse:
    """在系统资源管理器中定位产物文件（或打开目录）。

    仅本机单机功能：后端与桌面同机，explorer 直接弹宿主资源管理器。
    失败（非 Windows / 路径消失）返回 ok=false，前端提示降级。
    """
    root = _validated_root(req.output_dir)
    if not req.path.strip():
        raise HTTPException(status_code=400, detail="缺少产物路径 path")
    target = _resolve_in(_domains(root, req.repo_id), req.path, allow_dir=True)
    opened = reveal_in_folder(target, select=req.select)
    return JSONResponse({"ok": opened, "path": str(target)})

