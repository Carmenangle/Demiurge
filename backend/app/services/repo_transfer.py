"""作品包导入/导出（M5 · 换机迁移）：一个 zip = 一个作品。

作品在磁盘上自包含（`outputDir/<作品名>/`：会话记录随图片同落，`_repo.json` 是身份
标记，目录名只用于展示），外加 user_state.json 里的一条 repos 记录（名称/绑卡等）。
导出包＝文件夹内容 + `__transfer/meta.json`（repos 记录与格式版本）；导入＝解包到
作品根、身份证冲突时换新 id、登记 repos 记录。

安全合同：
- **zip-slip 一律拒绝**：成员名必须是纯相对路径（禁绝对/盘符/`..`），解包目标不得
  逃出作品根。
- 包格式自证：缺 `__transfer/meta.json` 或 `app != "demiurge"` 即拒（防拿任意 zip
  当作品包灌进作品根）。
- 导入只写作品根与 user_state（save_user_state 原子写）；不触碰 DATA_DIR 其他面。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import zipfile

from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from app import config
from app.services import repo_meta

TRANSFER_META = "__transfer/meta.json"
FORMAT_VERSION = 1
_BAD_SEG = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class TransferError(ValueError):
    """导入/导出的用户可见错误（路由层转 4xx）。"""


def _output_dir() -> str:
    output_dir = repo_meta.output_dir_from_state()
    if not output_dir:
        raise TransferError(repo_meta.WORKS_ROOT_NOT_CONFIGURED_MSG)
    return output_dir


def _repo_record(repo_id: str) -> dict:
    for record in repo_meta.load_user_state().get("repos") or []:
        if isinstance(record, dict) and record.get("id") == repo_id:
            return dict(record)
    raise TransferError(f"找不到作品 {repo_id!r}（repos 记录缺失）")


def export_repo(repo_id: str) -> tuple[Path, str]:
    """把作品打成 zip（落在 DATA_DIR/transfer/），返回 (zip 路径, 建议文件名)。"""
    output_dir = _output_dir()
    record = _repo_record(repo_id)
    folder = repo_meta.find_repo_folder(output_dir, repo_id)
    if folder is None:
        raise TransferError(f"作品 {repo_id!r} 没有输出文件夹，无物可导出")

    out_dir = config.DATA_DIR / "transfer"
    out_dir.mkdir(parents=True, exist_ok=True)
    handle = NamedTemporaryFile(
        dir=out_dir, prefix="export-", suffix=".demirepo.zip", delete=False)
    zip_path = Path(handle.name)
    handle.close()
    meta = {
        "app": "demiurge",
        "version": FORMAT_VERSION,
        "exported_at": time.time(),
        "repo": record,
    }
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(TRANSFER_META, json.dumps(meta, ensure_ascii=False, indent=2))
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder).as_posix())
    name = _BAD_SEG.sub("_", str(record.get("name") or repo_id)).strip("_") or repo_id
    return zip_path, f"{name}.demirepo.zip"


def _safe_member(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or ":" in name.split("/")[0]:
        return False
    return not any(seg in ("", ".", "..") for seg in name.split("/"))


def _claim_repo_folder(
    output_dir: str, record: dict, *, parent_folder: Path | None = None,
) -> tuple[str, Path]:
    """为新导入的作品分配身份与新目录（不落盘）。

    冲突换 uuid；展示名不覆盖既有目录（追加 -2/-3）；`parent_folder` 非空表示子仓库，
    新目录落在父作品文件夹下。
    """
    state = repo_meta.load_user_state()
    existing_ids = {
        str(r.get("id")) for r in state.get("repos") or [] if isinstance(r, dict)
    }
    new_id = str(record["id"])
    if new_id in existing_ids or repo_meta.find_repo_folder(output_dir, new_id):
        new_id = uuid4().hex

    display = _BAD_SEG.sub("_", str(record.get("name") or new_id)).strip("_") or new_id
    base = parent_folder if parent_folder is not None else Path(output_dir)
    dest = base / display
    suffix = 2
    while dest.exists():
        dest = base / f"{display}-{suffix}"
        suffix += 1
    return new_id, dest


def _register_repo(new_record: dict, dest: Path) -> None:
    """登记 repos 记录并写身份标记（先登记再写标记：标记要按 id 反查名称）。"""
    state = repo_meta.load_user_state()
    repos = [r for r in (state.get("repos") or []) if isinstance(r, dict)]
    repos.append(new_record)
    repo_meta.save_user_state({**state, "repos": repos})
    repo_meta.write_repo_marker(dest, str(new_record["id"]))


def import_repo(zip_path: Path) -> dict:
    """把作品包导入作品根：换新 id（冲突时）、登记 repos 记录。返回导入结果。"""
    output_dir = _output_dir()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                meta = json.loads(zf.read(TRANSFER_META).decode("utf-8"))
            except KeyError as exc:
                raise TransferError("不是 Demiurge 作品包（缺少 __transfer/meta.json）") from exc
            if not isinstance(meta, dict) or meta.get("app") != "demiurge":
                raise TransferError("不是 Demiurge 作品包（meta 校验失败）")
            record = meta.get("repo")
            if not isinstance(record, dict) or not record.get("id"):
                raise TransferError("作品包缺 repos 记录")

            members = [info for info in zf.infolist()
                       if not info.is_dir() and info.filename != TRANSFER_META]
            for info in members:
                if not _safe_member(info.filename):
                    raise TransferError(f"包内路径不合法（zip-slip 拒绝）：{info.filename}")

            new_id, dest = _claim_repo_folder(output_dir, record)
            dest.mkdir(parents=True, exist_ok=False)
            zf.extractall(dest, members=[m.filename for m in members])

    except zipfile.BadZipFile as exc:
        raise TransferError("不是有效的 zip 文件") from exc

    new_record = {**record, "id": new_id}
    _register_repo(new_record, dest)
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    return {"repo_id": new_id, "folder": str(dest), "name": new_record.get("name", "")}


def import_repo_folder(
    source_dir: Path, record: dict, *, parent_folder: Path | None = None,
) -> dict:
    """把已解包的一棵作品目录导入作品根（离线资源包里的仓库分组复用）。

    与 `import_repo` 共用 id/目录冲突规则；`parent_folder` 非空时落在该父作品文件夹
    下（子仓库），否则落在作品根。复制失败时清掉半成品目录，不留空壳。
    """
    if not isinstance(record, dict) or not record.get("id"):
        raise TransferError("作品包缺 repos 记录")
    output_dir = _output_dir()
    new_id, dest = _claim_repo_folder(output_dir, record, parent_folder=parent_folder)
    try:
        dest.mkdir(parents=True, exist_ok=False)
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            target = dest / path.relative_to(source_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    except OSError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    new_record = {**record, "id": new_id}
    _register_repo(new_record, dest)
    return {"repo_id": new_id, "folder": str(dest), "name": new_record.get("name", "")}


def cleanup_transfer_file(path: Path) -> None:
    """FileResponse 背景任务：下载完成后删临时 zip。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
