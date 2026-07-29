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


def repo_name(repo_id: str) -> str:
    """按 repo_id 查仓库名。查不到返回空串。"""
    p = DATA_DIR / "user_state.json"
    if not p.is_file():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for r in data.get("repos") or []:
            if isinstance(r, dict) and r.get("id") == repo_id:
                return r.get("name") or ""
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        pass
    return ""


def folder_name(repo_id: str) -> str:
    """决定该仓库的输出文件夹名：有仓库名用仓库名(清洗保中文)，否则回退 UUID。"""
    name = repo_name(repo_id)
    return safe_dir(name) if name else safe_seg(repo_id)


def repo_folder_path(output_dir: str, repo_id: str) -> Path:
    """只计算仓库输出路径，不创建目录或写 marker。清理/探测场景使用。"""
    return Path(output_dir) / folder_name(repo_id)


def repo_folder(output_dir: str, repo_id: str) -> Path:
    """返回该仓库的输出文件夹路径并建好，同时写/更新 _repo.json 标记。"""
    base = repo_folder_path(output_dir, repo_id)
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


def _rewrite_paths(repo_id: str, old_seg: str, new_seg: str) -> dict:
    """把快照/RAG 里含 output\\<old_seg>\\ 的图片路径改成 <new_seg>。返回替换计数。"""
    from app.config import DATA_DIR as _D
    snap = 0
    sp = _D / "chat_snapshots" / f"{safe_seg(repo_id, strip=False)}.json"
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
    old_dir_name = safe_dir(old_name) if old_name else safe_seg(repo_id)
    new_dir_name = safe_dir(new_name) if new_name else safe_seg(repo_id)
    # 兼容旧数据：老文件夹可能仍以 UUID 命名
    src = out / old_dir_name
    if not src.is_dir():
        src = out / safe_seg(repo_id)
    dst = out / new_dir_name
    folder_status = "unchanged"
    if src.is_dir() and src.resolve() != dst.resolve():
        if dst.exists():
            folder_status = "target_exists"  # 不覆盖（前端应已禁重名）
        else:
            src.rename(dst)
            write_repo_marker(dst, repo_id)
            folder_status = "renamed"
    elif dst.is_dir():
        write_repo_marker(dst, repo_id)
    # 重写路径：无论文件夹是否真的 rename，只要段名变了就要改引用
    counts = {"snapshot": 0, "rag": 0}
    actual_old = src.name  # 实际旧文件夹名（可能是 UUID）
    if actual_old != new_dir_name:
        old_seg = quote(f"{output_dir}\\{actual_old}\\")
        new_seg = quote(f"{output_dir}\\{new_dir_name}\\")
        counts = _rewrite_paths(repo_id, old_seg, new_seg)
    return {"folder": folder_status, **counts}
