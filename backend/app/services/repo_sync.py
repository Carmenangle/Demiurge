"""仓库同步（2026-10-18，P3 建议顺序⑤）：对账「注册表 / 磁盘 / 索引」三方。

为什么需要：资产有**三个**可能对不上的真源——
  ① 注册表 `user_state.repos`（前端 localStorage + 后端镜像）知道有哪些仓库；
  ② 磁盘 `outputDir/<仓库>/{pictures,videos,audios}/` 里的真实媒体文件（文件夹内
     `_repo.json` 记 {id,name}，改名也能识别）；
  ③ 索引 SQLite `assets` + Chroma 记录每条资产。
用户手动往仓库文件夹丢/删文件、资源管理器改名、ComfyUI 直写目录——②与①③会漂移。

安全口径（用户定案：不能误删/误索引真源）：
  - **先只读体检** `repo_sync_report`：扫三方、报漂移，不改任何数据；
  - **再按需修复** `repo_sync_fix`：只做两件保守的事——
      补录：磁盘有、索引无的媒体 → 建索引（按扩展名判 image/video/audio）；
      清理：索引有、磁盘无的裂图 + 孤儿仓库 → prune（复用既有逻辑）；
  - ⛔ 绝不反向删磁盘文件、绝不覆盖已有索引记录。
"""
import json
import logging
from pathlib import Path

from app.services import asset_store, repo_meta, rag_store

_log = logging.getLogger("uvicorn.error")

# 各类型媒体扩展名（同步扫盘 + 判 media_type 用）。
MEDIA_EXTS = {
    "image": (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"),
    "video": (".mp4", ".webm", ".mov", ".mkv", ".avi"),
    "audio": (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus"),
}
_MEDIA_SUBFOLDERS = ("pictures", "videos", "audios")


def media_kind_of(path: Path) -> str | None:
    suf = path.suffix.lower()
    for kind, exts in MEDIA_EXTS.items():
        if suf in exts:
            return kind
    return None


def _disk_media(folder: Path) -> dict[str, Path]:
    """仓库文件夹下的媒体文件 {abs_path: Path}（扫 pictures/videos/audios 三层）。"""
    out: dict[str, Path] = {}
    if not folder.is_dir():
        return out
    for sub in _MEDIA_SUBFOLDERS:
        d = folder / sub
        if not d.is_dir():
            continue
        try:
            for f in d.iterdir():
                if f.is_file() and media_kind_of(f):
                    out[str(f.resolve())] = f
        except OSError as exc:  # noqa: BLE001 [EXC-留痕] 单层读失败不阻断其余
            _log.warning("扫盘失败 %s: %s", d, exc)
    return out


def _registered_repos() -> dict[str, str]:
    """{repo_id: name}，从 user_state.repos。读失败返回空 dict（保守：无注册表就不动）。"""
    try:
        from app.config import DATA_DIR

        p = DATA_DIR / "user_state.json"
        if not p.is_file():
            return {}
        st = json.loads(p.read_text(encoding="utf-8"))
        return {
            str(r["id"]): str(r.get("name") or "")
            for r in (st.get("repos") or []) if r.get("id")
        }
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 读注册表失败不炸，返空
        _log.warning("读注册仓库失败: %s", exc)
        return {}


def _indexed_local_paths(repo_id: str) -> tuple[set[str], set[str]]:
    """某仓库索引里的本地媒体文件绝对路径，返回 (live, all)：
      - all ：含回收站软删行（deleted_at 非空）——用于判「是否见过此文件」，
              **软删过的不再补录**（尊重回收站，不把用户删掉的又索引回来）；
      - live：仅未软删行——用于判「裂图」（只清理真的坏了的在线记录）。
    """
    conn = asset_store.get_connection()
    try:
        rows = conn.execute(
            "select image_url, deleted_at from assets where repo_id=? and kind='generation'",
            (repo_id,),
        ).fetchall()
    finally:
        conn.close()
    live: set[str] = set()
    allp: set[str] = set()
    for r in rows:
        p = rag_store.local_path_of(r["image_url"] or "")
        if p is None:
            continue
        resolved = str(p.resolve())
        allp.add(resolved)
        if r["deleted_at"] is None:
            live.add(resolved)
    return live, allp


def repo_sync_report(output_dir: str, cfg) -> dict:
    """只读体检：对账三方，报告漂移，**不改任何数据**。

    返回：
      repos: 每个注册仓库 {repo_id,name,folder_exists,disk,indexed,unindexed[],missing[]}
        - unindexed：磁盘有、索引无（可补录）
        - missing  ：索引有、磁盘无（裂图，可清理）
      orphan_index_repos：索引有记录但不在注册表的 repo_id（可清理，复用孤儿判定）
      total_unindexed / total_missing：汇总
    """
    registered = _registered_repos()
    per_repo = []
    total_unindexed = 0
    total_missing = 0
    for repo_id, name in registered.items():
        folder = repo_meta.repo_folder_path(output_dir, repo_id)
        disk = _disk_media(folder)
        live_indexed, all_indexed = _indexed_local_paths(repo_id)
        # 补录候选＝磁盘有、但从没进过索引（含软删行也不补录，尊重回收站）
        unindexed = sorted(set(disk) - all_indexed)
        # 裂图候选＝在线索引有、磁盘没了（软删行进回收站，不算裂图）
        missing = sorted(live_indexed - set(disk))
        total_unindexed += len(unindexed)
        total_missing += len(missing)
        per_repo.append({
            "repo_id": repo_id,
            "name": name or repo_id,
            "folder_exists": folder.is_dir(),
            "disk": len(disk),
            "indexed": len(live_indexed),
            "unindexed": unindexed,
            "missing": missing,
        })
    # 孤儿索引仓库（**在线**记录、不在注册表、也非合成 repo；软删行进回收站不掺和）
    conn = asset_store.get_connection()
    try:
        all_rows = conn.execute(
            "select distinct repo_id from assets where deleted_at is null"
        ).fetchall()
    finally:
        conn.close()
    orphan = sorted(
        {str(r["repo_id"]) for r in all_rows if r["repo_id"]}
        - set(registered) - rag_store._RESERVED_REPO_IDS
    )
    return {
        "repos": per_repo,
        "orphan_index_repos": orphan,
        "total_unindexed": total_unindexed,
        "total_missing": total_missing,
    }


def _purge_orphan_rows(repo_id: str) -> int:
    """按**主表**清孤儿仓库里「文件已缺失」的在线行（硬删）。

    为什么需要补刀：`prune_orphan_repos` 只遍历 Chroma 文档；若某行**主表有、Chroma 文档没了**
    （部分删除/写失败留下的残留），prune 扫不到它，孤儿就会在体检里一直挂着清不掉。
    这里按 detects 的同一来源（主表）再清一遍，保证「报告能看见的孤儿，修复就能清掉」。
    """
    conn = asset_store.get_connection()
    try:
        rows = conn.execute(
            "select id, image_url from assets where repo_id=? and deleted_at is null",
            (repo_id,),
        ).fetchall()
    finally:
        conn.close()
    ids = []
    for r in rows:
        p = rag_store.local_path_of(r["image_url"] or "")
        if p is not None and not p.is_file():
            ids.append(r["id"])
    if not ids:
        return 0
    asset_store.delete_rows(ids)
    return len(ids)


def repo_sync_fix(output_dir: str, cfg, *, do_index: bool = True, do_prune: bool = True) -> dict:
    """按体检结果修复（保守）：补录未入库文件 + 清理裂图/孤儿。返回 {indexed, pruned}。

    ⛔ 不删磁盘文件、不覆盖已有索引；单个文件失败只留痕、不阻断其余。
    """
    from app.services import view_urls

    report = repo_sync_report(output_dir, cfg)
    indexed = 0
    pruned = 0
    if do_index:
        for repo in report["repos"]:
            for path in repo["unindexed"]:
                p = Path(path)
                kind = media_kind_of(p) or "image"
                try:
                    rag_store.index_generation(
                        repo["repo_id"], cfg, prompt="",
                        image_url=view_urls.local_view(str(p)), media_type=kind,
                    )
                    indexed += 1
                except Exception as exc:  # noqa: BLE001 [EXC-留痕] 单文件失败不阻断
                    _log.warning("补录失败 %s: %s", path, exc)
    if do_prune:
        for repo in report["repos"]:
            if repo["missing"]:
                try:
                    pruned += rag_store.prune_missing_generations(repo["repo_id"], cfg)
                except Exception as exc:  # noqa: BLE001 [EXC-留痕]
                    _log.warning("清理裂图失败 repo=%s: %s", repo["repo_id"], exc)
        if report["orphan_index_repos"]:
            try:
                pruned += rag_store.prune_orphan_repos(cfg)["removed"]
            except Exception as exc:  # noqa: BLE001 [EXC-留痕]
                _log.warning("孤儿仓库清理失败: %s", exc)
            # 补刀：prune 只走 Chroma，主表有行/Chroma 文档没了的残留清不掉，按主表再清一遍
            for rid in report["orphan_index_repos"]:
                try:
                    pruned += _purge_orphan_rows(rid)
                except Exception as exc:  # noqa: BLE001 [EXC-留痕]
                    _log.warning("孤儿主表补刀失败 repo=%s: %s", rid, exc)
    return {"indexed": indexed, "pruned": pruned}
