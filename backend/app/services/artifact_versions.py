"""产物版本快照：fabric done 落盘后自动存档，支持查看历史/回档/删除。

语义（2026-09-09 用户定案；2026-09-10 链路③ 扩展文档产物、B3/A 收窄到作品域）：
- 每次智能编造「完成」都是一次存档：**作品域**/_versions/<序号>-<时间戳>/ 下
  按相对作品域的结构复制当时的最新产物组（`card.json`、`docs/设定总集.md`）+ meta.json。
  作品域 = 仓库/小仓库文件夹（`repo_meta.repo_scope_path`，2026-09-10 用户定案），
  此前是作品库根下的全局单例 `_versions/`（所有作品混放，回档列表互相串味）。
- 当前产物 = 最新落盘结果；版本历史只读，可从任意版本回档（回档前先给
  当前状态也存一档，双向可逆）、可删除中途版本。
- 产物组判定复用 collection_artifacts.collect_artifacts（与产物卡展示口径完全一致），
  白名单复用 is_allowed_artifact；内容未变化时跳过存档（去重）。
- **域外卡**（卡名≠作品名字：卡在 `<作品库根>/<卡名>/`，作品域在 `<作品库根>/<作品名>/`）：
  其 rel 记为 `<卡名>/card.json`（card.json 在任意层级都被白名单放行），并在 meta 里记
  `origin_dir` = 卡目录绝对路径，回档时**原位回写**该卡目录——版本库仍只落作品域，
  不把别人的卡吸进来，也不会把卡错写到作品域里。

安全：_versions 位于作品域内，天然受 resolve_artifact 的目录 jail 约束；
版本目录名/版本内文件路径均做白名单校验，防路径穿越。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

__all__ = [
    "VERSIONS_DIR",
    "snapshot_current",
    "list_versions",
    "restore_version",
    "delete_version",
    "resolve_version_file",
    "storage_root",
]

# 常量唯一属主在 collection_artifacts：VERSIONS_DIR（collect 排除它）、白名单判定
# is_allowed_artifact（card/worldbook + docs/*.md + docs/assets/*.<图片>）。
# 2026-09-10 链路③：此前本模块复用 _ALLOWED_NAMES（只有 card.json/worldbook.json），
# 导致文档产物既不入快照也无法回档——改为统一走 is_allowed_artifact，与产物卡收集同源。
from app.services.collection_artifacts import VERSIONS_DIR, is_allowed_artifact  # noqa: E402
# 版本目录名规范：<seq3>-<YYYYMMDDHHMMSS>，如 001-20260909103000
_DIRNAME_RE = r"^[0-9]{3}-[0-9]{14}$"
# 一次存档最多复制的文件数：8 份文档上限（collection_artifacts._MAX_DOCS）+ 主卡/世界书 + 余量。
# 注意必须 > 文档上限，否则「文档最新、排在前面」会把主卡挤出快照。
_MAX_COPY = 16
_META = "meta.json"


class VersionError(ValueError):
    """版本操作失败（版本不存在/路径越界/无产物）。status 供路由转 HTTPException。"""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def storage_root(output_dir: str, repo_id: str = "") -> Path:
    """版本库所在的**存储域**：有 repo_id → 作品域（仓库/小仓库文件夹）；否则作品库根。

    只算路径（`repo_scope_path` 不建目录）：作品域尚不存在时快照自然退化为
    {"saved": False, "reason": "no-artifacts"}，不会误写进别人的域。

    公开给路由层用：`/versions` 要给每个 `files[]` 补**活文件绝对路径**（`path`），
    域外卡走 meta 的 `origin_dir`、域内文件走 `存储域/rel`，两处都必须与快照同口径。
    """
    target = output_dir
    if repo_id:
        from app.services import repo_meta
        scoped = repo_meta.repo_scope_path(output_dir, repo_id)
        if scoped is not None:
            target = str(scoped)
    return Path(target or "").expanduser().resolve()


_storage_root = storage_root  # 内部旧名（本模块内多处调用，保留别名避免大范围改动）


def _domains(output_dir: str, repo_id: str) -> list[Path]:
    """产物访问/收集域集合（与产物卡同口径；失败退化为单域）。"""
    from app.services import collection_artifacts
    if not (output_dir or "").strip():
        return []
    try:
        return collection_artifacts._domains_of(output_dir, repo_id)
    except Exception:  # noqa: BLE001 - 域解析异常不该让快照整体失败
        return [Path(output_dir)]


def _versions_dir(root: Path) -> Path:
    return root / VERSIONS_DIR


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_of(root: Path, src: Path) -> str:
    """产物相对作品根的路径（`/` 分隔）——版本内的落盘结构与它一一对应。"""
    return str(src.relative_to(root)).replace("\\", "/")


def _version_file(version_dir: Path, rel: str, name: str = "") -> Path | None:
    """版本内某产物的实际文件：优先按 rel 结构，其次兼容 2026-09-10 之前的**平铺**存档
    （旧版本目录里文件直接放在版本根下，meta 的 rel 形如 `<作品名>/card.json`）。
    找不到返回 None。"""
    if rel:
        candidate = version_dir / rel
        if candidate.is_file():
            return candidate
    if name:
        legacy = version_dir / name
        if legacy.is_file():
            return legacy
    return None


def _parse_version_dir(name: str) -> int | None:
    import re
    if len(name) < 4 or not re.match(_DIRNAME_RE, name):
        return None
    try:
        return int(name[:3])
    except ValueError:
        return None


def _version_dirs(root: Path) -> list[Path]:
    """作品根下合法的版本目录，按 seq 升序（旧→新）。损坏/非法目录跳过。"""
    vdir = _versions_dir(root)
    if not vdir.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    try:
        entries = sorted(vdir.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        seq = _parse_version_dir(entry.name)
        if seq is not None:
            found.append((seq, entry))
    found.sort(key=lambda pair: pair[0])
    return [p for _, p in found]


def _next_seq(root: Path) -> int:
    dirs = _version_dirs(root)
    return (int(dirs[-1].name[:3]) + 1) if dirs else 1


def _meta_of(version_dir: Path) -> dict | None:
    meta = version_dir / _META
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def snapshot_current(output_dir: str, trigger: str = "", summary: str = "",
                     repo_id: str = "") -> dict:
    """把当前最新产物组存为一个版本；内容与最新版本全同则跳过。

    产物范围与产物卡完全同源：`collect_artifacts` 收到的都会被存档
    （主卡/世界书 + `docs/*.md` 文档；插图素材不在收集范围，故不参与快照）。
    版本内按相对**存储域**（作品域）的结构落盘（`card.json`、`docs/设定总集.md`），
    避免不同目录同名文件互相覆盖。

    `output_dir` 仍是**作品库根**（与 collect_artifacts 同口径），存储域由
    `repo_id` 解析（`_storage_root`）——这样调用方只需透传已有的两个值，
    不必自己算作品域，避免「算域」逻辑在多处漂移。

    返回 {"saved": True, "version": meta} 或 {"saved": False, "reason": "unchanged"}。
    无产物 / 目录异常返回 {"saved": False, "reason": "no-artifacts"}。
    绝不抛出——版本快照只是交付增强，失败不应阻断 fabric done。
    """
    try:
        from app.services import collection_artifacts
        items = collection_artifacts.collect_artifacts(output_dir, repo_id=repo_id)
        if not items:
            return {"saved": False, "reason": "no-artifacts"}
        root = _storage_root(output_dir, repo_id)
        if not root.is_dir():
            return {"saved": False, "reason": "no-artifacts"}
        domains = _domains(output_dir, repo_id)
        # 白名单过滤（按**所属域**判定）+ 相对路径（与预览/下载同一套判定，
        # 防把任意文件吸进版本目录）
        candidates: list[tuple[Path, str, str]] = []
        for item in items:
            src = Path(str(item.get("path") or ""))
            try:
                src = src.expanduser().resolve()
                if not src.is_file():
                    continue
            except OSError:
                continue
            owner = collection_artifacts.domain_owner(src, domains)
            if owner is None or not is_allowed_artifact(src, owner):
                continue
            if src.is_relative_to(root):
                candidates.append((src, _rel_of(root, src), ""))
            else:
                # 域外卡（卡名≠作品名）：版本库仍只落作品域，rel 记为 `<卡目录名>/<文件名>`
                # ——card.json/worldbook.json 在任意层级都被白名单放行；原目录记进
                # origin_dir，回档时原位回写，不把卡错搬进作品域。
                candidates.append((src, f"{src.parent.name}/{src.name}", str(src.parent)))
        if not candidates:
            return {"saved": False, "reason": "no-artifacts"}
        # 与最新版本逐文件比对（按 rel 定位 + 内容 hash；目录名漂移不影响判定）
        latest = _version_dirs(root)[-1] if _version_dirs(root) else None
        if latest is not None:
            unchanged = True
            for src, rel, _origin in candidates:
                target = _version_file(latest, rel, src.name)
                if target is None or _file_sha256(target) != _file_sha256(src):
                    unchanged = False
                    break
            if unchanged:
                return {"saved": False, "reason": "unchanged"}
        # 落盘：<存储域>/_versions/<seq3>-<ts>/<rel>
        seq = _next_seq(root)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        version_dir = _versions_dir(root) / f"{seq:03d}-{ts}"
        version_dir.mkdir(parents=True, exist_ok=True)
        files_meta: list[dict] = []
        copied = 0
        for src, rel, origin in candidates:
            if copied >= _MAX_COPY:
                break
            dest = version_dir / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            except OSError:
                continue
            entry = {
                "rel": rel, "name": src.name,
                "size": src.stat().st_size, "mtime": src.stat().st_mtime,
            }
            if origin:
                entry["origin_dir"] = origin
            files_meta.append(entry)
            copied += 1
        if not files_meta:
            shutil.rmtree(version_dir, ignore_errors=True)
            return {"saved": False, "reason": "no-artifacts"}
        meta = {
            "version_id": version_dir.name,
            "seq": seq,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "trigger": str(trigger or "")[:200],
            "summary": str(summary or "")[:500],
            "files": files_meta,
            "total_size": sum(f["size"] for f in files_meta),
        }
        try:
            (version_dir / _META).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            shutil.rmtree(version_dir, ignore_errors=True)
            return {"saved": False, "reason": "meta-write-failed"}
        return {"saved": True, "version": meta}
    except Exception:  # noqa: BLE001 - 快照失败不阻断交付流程
        return {"saved": False, "reason": "error"}


def list_versions(output_dir: str, repo_id: str = "") -> list[dict]:
    """列出全部版本（新→旧），字段即 meta.json 内容；无版本返回空列表。

    `output_dir` = 作品库根，`repo_id` → 存储域（作品域），与快照同一口径。
    """
    try:
        root = _storage_root(output_dir, repo_id)
        if not root.is_dir():
            return []
    except OSError:
        return []
    metas: list[dict] = []
    for version_dir in _version_dirs(root):
        meta = _meta_of(version_dir)
        if meta:
            metas.append(meta)
    metas.sort(key=lambda m: int(m.get("seq") or 0), reverse=True)
    return metas


def _resolve_version_dir(root: Path, version_id: str) -> Path:
    """校验 version_id 并返回其目录；非法/不存在抛 VersionError。"""
    import re
    if not version_id or not re.match(_DIRNAME_RE, str(version_id)):
        raise VersionError(400, "版本号格式非法")
    version_dir = (_versions_dir(root) / str(version_id)).resolve()
    if not version_dir.is_relative_to(_versions_dir(root).resolve()):
        raise VersionError(403, "版本路径越界")
    if not version_dir.is_dir():
        raise VersionError(404, "版本不存在")
    return version_dir


def restore_version(output_dir: str, version_id: str, repo_id: str = "") -> dict:
    """把指定版本回档为当前产物：先给当前状态预存档，再覆盖回写。

    返回 {"restored": [目标绝对路径...]}。失败抛 VersionError（含 404 版本不存在）。
    `output_dir` = 作品库根（存储域由 repo_id 解析，与快照同一口径）。
    """
    root = _storage_root(output_dir, repo_id)
    if not root.is_dir():
        raise VersionError(400, "作品根目录不存在")
    version_dir = _resolve_version_dir(root, version_id)
    meta = _meta_of(version_dir)
    if not meta:
        raise VersionError(404, "版本元数据缺失")
    files = [f for f in (meta.get("files") or []) if isinstance(f, dict)]
    if not files:
        raise VersionError(400, "版本内没有产物文件")
    # 1) 预存档当前状态（若与最近版本相同会去重跳过，不影响）
    snapshot_current(output_dir, trigger="pre_restore",
                     summary=f"回档到 {version_id} 前自动存档", repo_id=repo_id)
    # 2) 逐文件回写：目标必须在版本白名单内（rel 与 name 自洽）
    restored: list[str] = []
    for entry in files:
        rel = str(entry.get("rel") or "")
        name = str(entry.get("name") or "")
        origin = str(entry.get("origin_dir") or "")
        if not rel or not name:
            continue
        if origin:
            # 域外卡（卡名≠作品名）：按 origin_dir **原位**回写，不搬进作品域
            owner = Path(origin).expanduser().resolve()
            target = (owner / name).resolve()
            if not target.is_relative_to(owner) or not is_allowed_artifact(target, owner):
                continue
        else:
            target = (root / rel).resolve()
            if not target.is_relative_to(root):
                raise VersionError(403, "版本文件路径越界")
            # rel 与 name 必须自洽（防 meta 被改写成指向别处），且形态在产物白名单内
            if target.name != name or not is_allowed_artifact(target, root):
                continue
        src = _version_file(version_dir, rel, name)
        if src is None:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        except OSError as exc:
            raise VersionError(500, f"回档写入失败：{exc}") from exc
        restored.append(str(target))
    if not restored:
        raise VersionError(400, "没有可回档的文件")
    return {"restored": restored}


def delete_version(output_dir: str, version_id: str, repo_id: str = "") -> bool:
    """删除指定版本目录（仅版本，不动当前产物）。不存在抛 VersionError。"""
    root = _storage_root(output_dir, repo_id)
    if not root.is_dir():
        raise VersionError(400, "作品根目录不存在")
    version_dir = _resolve_version_dir(root, version_id)
    shutil.rmtree(version_dir, ignore_errors=True)
    if version_dir.exists():
        raise VersionError(500, "版本删除失败")
    return True


def resolve_version_file(output_dir: "str | Path", version_id: str, rel: str,
                         repo_id: str = "") -> Path:
    """解析版本**内**的文件（供版本内容查看/下载）：jail + 形态白名单校验。

    - `rel` 为产物相对**存储域**（作品域）的路径（如 `card.json`、`docs/设定总集.md`），
      与 meta.json 的 files[].rel 同形；域外卡为 `<卡目录名>/card.json`。
      缺省兼容旧版**平铺**存档的裸文件名（如 `card.json`）。
    - 以该版本的 meta.json 为「里面有什么」的真源：先在 files 里按 rel 匹配、再按 name 兜底，
      匹配不到即 404（不接受 meta 未声明的文件）。
    - 形态白名单按 **存储域 + rel** 判定（与产物访问同一套），不把版本目录自身当作可暴露路径。
    - `root` 先归一（2026-09-10）：Windows 上未归一的作品根（8.3 短名 `CARMEN~1`、含 `..` 的
      别名路径）会让 `live.is_relative_to(root)` 假失败、报出误导性的 403「不在产物白名单内」。
      与 `resolve_artifact`/`list_versions` 同一约定：入口一律 `resolve()`。
    """
    root = _storage_root(str(output_dir), repo_id) if repo_id else \
        Path(output_dir).expanduser().resolve()
    version_dir = _resolve_version_dir(root, version_id)
    key = str(rel or "").replace("\\", "/").strip()
    if not key:
        raise VersionError(400, "缺少版本内文件路径")
    meta = _meta_of(version_dir) or {}
    entries = [e for e in (meta.get("files") or []) if isinstance(e, dict)]
    entry = next((e for e in entries if str(e.get("rel") or "") == key), None)
    if entry is None:
        entry = next((e for e in entries if str(e.get("name") or "") == key), None)
    if entry is None:
        raise VersionError(404, "版本内没有该文件")
    src_rel = str(entry.get("rel") or "")
    name = str(entry.get("name") or "")
    if not src_rel or not name:
        raise VersionError(404, "版本元数据不完整")
    origin = str(entry.get("origin_dir") or "")
    if origin:
        owner = Path(origin).expanduser().resolve()
        live = (owner / name).resolve()
    else:
        owner = root
        live = (root / src_rel).resolve()
    if not live.is_relative_to(owner) or not is_allowed_artifact(live, owner):
        raise VersionError(403, "该文件不在产物白名单内")
    candidate = _version_file(version_dir, src_rel, name)
    if candidate is None:
        raise VersionError(404, "版本文件不存在")
    target = candidate.resolve()
    if not target.is_relative_to(version_dir.resolve()):
        raise VersionError(403, "版本文件路径越界")
    return target
