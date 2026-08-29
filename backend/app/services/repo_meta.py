"""仓库元信息：repo_id → 仓库名，输出文件夹按仓库名命名，改名时同步迁移。

文件夹名 = 用户对小仓库的命名（保留中文），文件夹内 _repo.json 记 {id, name}。
仓库名来源：前端存到后端的 data/user_state.json（见 routers/user_state.py）。
改名时重命名文件夹 + 重写快照/RAG/封面里含旧文件夹段的图片路径（否则图断）。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from app.config import DATA_DIR
from app.services.pathnames import safe_dir, safe_seg


def _load_state() -> dict:
    """读 user_state.json（repos + settings 单一属主）。缺失/损坏返回空 dict。"""
    p = DATA_DIR / "user_state.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}


def repo_name(repo_id: str) -> str:
    """按 repo_id 查仓库名。查不到返回空串。"""
    for r in _load_state().get("repos") or []:
        if isinstance(r, dict) and r.get("id") == repo_id:
            return r.get("name") or ""
    return ""


def _repo_record(repo_id: str) -> dict | None:
    """按 repo_id 取仓库记录（含 name/parentId）。查不到返回 None。"""
    for r in _load_state().get("repos") or []:
        if isinstance(r, dict) and r.get("id") == repo_id:
            return r
    return None


def parent_folder_seg(repo_id: str) -> str:
    """子仓库的父作品文件夹段：有父且父有名 → safe_dir(父名)，否则空串。

    卡作品的对话子仓库都叫 "SAVE01"，若直接按子仓库名建文件夹会互相覆盖
    （九天神女传/SAVE01 与 神权大陆/SAVE01 撞进同一 outputDir/SAVE01/）。
    故子仓库嵌到父作品文件夹下：outputDir/<父名>/<子名>/。父名=卡名，唯一。
    """
    rec = _repo_record(repo_id)
    if not isinstance(rec, dict):
        return ""
    parent_id = rec.get("parentId")
    if not parent_id:
        return ""
    parent = _repo_record(parent_id)
    pname = (parent.get("name") or "") if isinstance(parent, dict) else ""
    return safe_dir(pname) if pname else ""


def works_root_violation(output_dir: str) -> str | None:
    """写/删类端点的作品根校验：必须等于配置的仓库文件夹根（防客户端指定任意目录）。

    未配置仓库文件夹（truth 为空）时放行——功能未启用，各服务自行兜底。
    返回 None=通过，否则返回中文错误信息供路由转 HTTPException。
    """
    truth = output_dir_from_state()
    if truth and output_dir and output_dir != truth:
        return "output_dir 必须是当前配置的仓库文件夹根路径"
    return None


def output_dir_from_state() -> str:
    """从 user_state.json 读"仓库文件夹"根路径(settings.outputDir)。未配置返回空串。

    供 chat_snapshot 自行解析会话记录落点——会话记录随图片同落 outputDir/<作品名>/，
    无需把 output_dir 透传穿过 ~20 处 caller（settings 与 repos 同在此文件，单一真源）。
    """
    settings = _load_state().get("settings")
    if isinstance(settings, dict):
        val = settings.get("outputDir")
        if isinstance(val, str):
            return val.strip()
    return ""


def setting_dir_from_state(key: str) -> str:
    """读取后端已持久化的目录设置；编辑 Agent 发布源库时禁止采用请求传入路径。"""
    settings = _load_state().get("settings")
    if isinstance(settings, dict):
        value = settings.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def folder_name(repo_id: str) -> str:
    """决定该仓库的输出文件夹段（相对 output_dir）：有仓库名用仓库名(清洗保中文)，否则回退 UUID。

    子仓库嵌到父作品文件夹下 → "<父名>/<子名>"（避免同名子仓库 SAVE01 互相覆盖）。
    父仓库/无父 → 单段。返回值可能含 "/"，Path 会正确拆成多级。
    """
    name = repo_name(repo_id)
    own = safe_dir(name) if name else safe_seg(repo_id)
    parent_seg = parent_folder_seg(repo_id)
    return f"{parent_seg}/{own}" if parent_seg else own


def repo_folder_path(output_dir: str, repo_id: str) -> Path:
    """只计算仓库输出路径，不创建目录或写 marker。清理/探测场景使用。"""
    out = Path(output_dir)
    candidate = out / folder_name(repo_id)
    if not candidate.is_dir() and _repo_record(repo_id) is None:
        marked = _find_marked_folder(out, repo_id)
        if marked is not None:
            return marked
    return _collision_safe_path(candidate, repo_id)


def _collision_safe_path(candidate: Path, repo_id: str) -> Path:
    """目标目录已由另一 UUID 占用时，返回带 UUID 后缀的隔离目录。"""
    owner = _marker_repo_id(candidate) if candidate.is_dir() else ""
    if not owner or owner == repo_id:
        return candidate
    suffix = safe_seg(repo_id, "repo", strip=False)[:8]
    isolated = candidate.with_name(f"{candidate.name} [{suffix}]")
    isolated_owner = _marker_repo_id(isolated) if isolated.is_dir() else ""
    if not isolated_owner or isolated_owner == repo_id:
        return isolated
    return candidate.with_name(f"{candidate.name} [{safe_seg(repo_id, 'repo', strip=False)}]")


def _find_marked_folder(output_dir: Path, repo_id: str) -> Path | None:
    """按不可变 UUID 找回改名前遗留的目录；目录名只用于展示，不再充当身份。"""
    if not output_dir.is_dir():
        return None
    try:
        matches = [
            marker.parent for marker in output_dir.rglob("_repo.json")
            if _marker_repo_id(marker.parent) == repo_id
        ]
    except OSError:
        return None
    return min(matches, key=lambda path: len(path.parts)) if matches else None


def migrate_legacy_folder(output_dir: str, repo_id: str) -> Path:
    """把子仓库从旧的扁平/UUID 文件夹惰性迁移到嵌套位置 <父名>/<子名>/。

    历史上子仓库对话/图片落在 outputDir/<子名>/（子名都叫 SAVE01 → 互相覆盖），
    或更早的 outputDir/<UUID>/。改为嵌套后需一次性把旧文件夹整体搬到新位置。
    幂等：新位置已存在则不动；旧位置不存在则无操作。返回目标（嵌套）路径。

    只对「有父」的子仓库生效（父仓库/无父保持原样）。歧义保护：旧扁平文件夹名
    在多个仓库间可能重名（SAVE01），仅当该文件夹的 _repo.json 标记确属本 repo_id、
    或标记缺失但仅本 repo 可能用该名时才搬——这里采取保守策略：扁平文件夹带标记且
    == 本 repo 才搬；无标记的扁平文件夹不搬（留原地，新位置缺失会触发开场白重播）。
    """
    if not output_dir:
        return repo_folder_path(output_dir, repo_id)
    out = Path(output_dir)
    new_path = repo_folder_path(output_dir, repo_id)
    parent_seg = parent_folder_seg(repo_id)
    if not parent_seg or (new_path.exists() and _marker_repo_id(new_path) in ("", repo_id)):
        return new_path  # 非子仓库，或已在新位置：无需迁移
    own = folder_name(repo_id).rsplit("/", 1)[-1]
    # 候选旧位置，按可信度排序：UUID 文件夹（唯一，最可信）> 带本 repo 标记的扁平文件夹
    candidates: list[Path] = []
    uuid_dir = out / safe_seg(repo_id)
    if uuid_dir.is_dir():
        candidates.append(uuid_dir)
    flat_dir = out / own
    if flat_dir.is_dir() and _marker_repo_id(flat_dir) == repo_id:
        candidates.append(flat_dir)
    marked = _find_marked_folder(out, repo_id)
    if marked is not None and marked not in candidates and marked != new_path:
        candidates.append(marked)
    for src in candidates:
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            src.rename(new_path)
            write_repo_marker(new_path, repo_id)
            return new_path
        except OSError:
            continue  # 搬迁失败 → 保持旧位置由调用方兜底
    return new_path


def _marker_repo_id(folder: Path) -> str:
    """读文件夹内 _repo.json 的 id。无标记/损坏返回空串。"""
    try:
        data = json.loads((folder / "_repo.json").read_text(encoding="utf-8"))
        return data.get("id") or "" if isinstance(data, dict) else ""
    except (OSError, json.JSONDecodeError):
        return ""


def repo_folder(output_dir: str, repo_id: str) -> Path:
    """返回该仓库的输出文件夹路径并建好，同时写/更新 _repo.json 标记。

    子仓库若还在旧扁平/UUID 位置，先惰性搬到嵌套位置再建（幂等）。
    """
    base = migrate_legacy_folder(output_dir, repo_id) if output_dir else repo_folder_path(output_dir, repo_id)
    base.mkdir(parents=True, exist_ok=True)
    write_repo_marker(base, repo_id)
    return base


def write_repo_marker(folder: Path, repo_id: str) -> None:
    """在输出文件夹里写/更新 _repo.json（{id, name}）。失败静默。"""
    try:
        name = repo_name(repo_id)
        if repo_id == "home" and not name:
            return
        (folder / "_repo.json").write_text(
            json.dumps({"id": repo_id, "name": name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _snapshot_path_for(repo_id: str, folder: Path | None) -> Path:
    """定位该作品的会话快照：优先仓库文件夹内(folder/chat.json，作品文件夹化后的位置)，
    否则回退旧 DATA_DIR/chat_snapshots/<id>.json。folder 一般是刚 rename 出的新文件夹。"""
    if folder is not None:
        inside = folder / "chat.json"
        if inside.is_file():
            return inside
    return DATA_DIR / "chat_snapshots" / f"{safe_seg(repo_id, strip=False)}.json"


def _rewrite_paths(repo_id: str, old_seg: str, new_seg: str, snap_folder: Path | None = None) -> dict:
    """把快照/RAG 里含 output\\<old_seg>\\ 的图片路径改成 <new_seg>。返回替换计数。"""
    from app.config import DATA_DIR as _D
    snap = 0
    sp = _snapshot_path_for(repo_id, snap_folder)
    if sp.is_file():
        t = sp.read_text(encoding="utf-8")
        snap = t.count(old_seg)
        if snap:
            sp.write_text(t.replace(old_seg, new_seg), encoding="utf-8")
    rag = 0
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(_D / "chroma"))
        c = client.get_collection(f"repo_{repo_id}")
        d = c.get(include=["metadatas"])
        up_ids, up_metas = [], []
        for i, m in enumerate(d["metadatas"]):
            iu = m.get("image_url")
            if isinstance(iu, str) and old_seg in iu:
                m = dict(m)
                m["image_url"] = iu.replace(old_seg, new_seg)
                up_ids.append(d["ids"][i])
                up_metas.append(m)
        if up_ids:
            c.update(ids=up_ids, metadatas=up_metas)
            rag = len(up_ids)
    except Exception:
        pass
    return {"snapshot": snap, "rag": rag}


def rename_folder(output_dir: str, repo_id: str, old_name: str, new_name: str) -> dict:
    """仓库改名时：重命名输出文件夹 + 重写快照/RAG 里的图片路径。

    封面在 user_state.json 里（前端改名已更新内存并回写后端），由前端负责。
    文件夹按 old_name→new_name 迁移；old 文件夹可能还是 UUID（首次从 UUID 迁移）。
    返回 {folder, snapshot, rag}。失败不抛（尽力而为）。
    """
    if not output_dir:
        return {"folder": "skip"}
    out = Path(output_dir)
    # 子仓库嵌在父作品文件夹下：改子仓库名只动末段，父段（=当前状态里父名）不变。
    parent_seg = parent_folder_seg(repo_id)
    old_own = safe_dir(old_name) if old_name else safe_seg(repo_id)
    new_own = safe_dir(new_name) if new_name else safe_seg(repo_id)
    old_dir_name = f"{parent_seg}/{old_own}" if parent_seg else old_own
    new_dir_name = f"{parent_seg}/{new_own}" if parent_seg else new_own
    # 兼容旧数据：老文件夹可能是扁平末段（未嵌套）或 UUID
    src = out / old_dir_name
    if not src.is_dir():
        src = out / old_own          # 旧扁平位置（迁移前的 outputDir/SAVE01）
    if not src.is_dir():
        src = out / safe_seg(repo_id)  # 更旧：UUID 文件夹
    dst = out / new_dir_name
    folder_status = "unchanged"
    if src.is_dir() and src.resolve() != dst.resolve():
        if dst.exists():
            folder_status = "target_exists"  # 不覆盖（前端应已禁重名）
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)  # 建父作品文件夹
            src.rename(dst)
            write_repo_marker(dst, repo_id)
            folder_status = "renamed"
    elif dst.is_dir():
        write_repo_marker(dst, repo_id)
    # 重写路径：无论文件夹是否真的 rename，只要段名变了就要改引用。
    # actual_old 用相对 output_dir 的实际旧段（含父前缀或 UUID），与落库路径一致。
    counts = {"snapshot": 0, "rag": 0}
    try:
        actual_old_seg = str(src.relative_to(out)).replace("/", "\\")
    except ValueError:
        actual_old_seg = src.name
    new_seg_rel = new_dir_name.replace("/", "\\")
    if actual_old_seg != new_seg_rel:
        old_seg = quote(f"{output_dir}\\{actual_old_seg}\\")
        new_seg = quote(f"{output_dir}\\{new_seg_rel}\\")
        # 会话快照已随文件夹 rename 移动到 dst，从新文件夹内定位再重写内部旧图路径段
        counts = _rewrite_paths(repo_id, old_seg, new_seg, snap_folder=dst)
    return {"folder": folder_status, **counts}


def delete_folder(output_dir: str, repo_id: str, name: str = "") -> dict:
    """删仓库时清理它在「仓库文件夹」里的作品文件夹（快照卡/世界书/persona/会话/图）。

    只删作品自己的文件夹，**绝不碰源库**（角色卡文件夹、世界书文件夹）——源库-作品解耦：
    源库是可复用素材，作品持自有快照。名优先按仓库名（保中文），兼容旧数据回退 UUID 文件夹。
    返回 {deleted: bool, folder}。失败不抛（尽力而为）。
    """
    import shutil
    if not (output_dir and repo_id):
        return {"deleted": False, "folder": "skip"}
    out = Path(output_dir)
    own = safe_dir(name) if name else safe_seg(repo_id)
    parent_seg = parent_folder_seg(repo_id)
    # 优先删嵌套位置 <父名>/<子名>，兼容旧数据回退扁平末段、再回退 UUID 文件夹
    target = out / f"{parent_seg}/{own}" if parent_seg else out / own
    if not target.is_dir():
        target = out / own                # 旧扁平位置
    if not target.is_dir():
        target = out / safe_seg(repo_id)   # 更旧：UUID 文件夹
    if not target.is_dir():
        return {"deleted": False, "folder": "missing"}
    shutil.rmtree(target, ignore_errors=True)
    return {"deleted": not target.exists(), "folder": target.name}
