"""离线资源同步包：一个 zip = 可移植设置 + 素材目录 + 作品仓库 + 正则 + 上网素材 + 资产账本。

场景：一台设备离线使用（不联机）时，把另一台设备的模型配置、人设、角色卡、世界书、
预设、工作流、提示词模版、作品仓库、全局正则、上网素材与资产元数据带过去，省去逐项重填。

包内结构（v3；v2 只少 `assets.json`，导入端两者都认）：
- `settings.json`：可移植设置（设备路径/网络项不打包）；
- `assets/<kind>/…`：角色卡 / 世界书 / 预设 / 工作流 / 提示词模版；
- `repos.json` ＋ `repos/<index>/…`：作品记录与文件夹内容（会话、图片随作品走）；
- `regex.json`：全局正则脚本；
- `web_materials/…`：作品库根的 `_web_materials/`（含 `.provenance.json`）；
- `assets.json`：资产元数据账本（提示词/标签/模板/模型/来源），图片落点用
  「仓库文件夹 + 夹内相对路径」两段表示，导入端按本机新路径重算 id 后写进主表。

**不随包走的东西**：设备路径与网络/代理设置（导入端保留现值）、密钥（默认省略，
需要离线直连模型时显式勾选）、缩略图派生层与回收站记录。

安全合同：
- **zip-slip 一律拒绝**：成员名必须是纯相对路径（禁绝对/盘符/`..`），且必须先落在
  临时目录、再按目标根前缀校验后才合并；包内不允许出现未知分类。
- 导入只写五类素材目录、作品根（仓库与 `_web_materials`）、全局正则文件、
  `user_state.settings` 与资产主表，不触碰 DATA_DIR 其他面。
- 默认不覆盖同名文件/同 id 正则；作品仓库始终按新身份导入（冲突换 uuid），不覆盖
  本机既有作品。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import zipfile

from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from app import config
from app.config import PROMPT_TEMPLATES_DIR
from app.services import (
    asset_library, asset_store, character_store, image_store, preset_store,
    rag_store, regex_store, repo_meta, repo_transfer, thumbnail_store,
    view_urls, worldbook_store,
)

BUNDLE_META = "__config/meta.json"
SETTINGS_MEMBER = "settings.json"
REPOS_MEMBER = "repos.json"
REGEX_MEMBER = "regex.json"
ASSETS_MEMBER = "assets.json"
FORMAT_VERSION = 3

# 设备路径：与导出端所在机器绑定，导入端必须保留自己的值。
DEVICE_KEYS = (
    "outputDir",
    "characterDir",
    "worldbookDir",
    "presetDir",
    "workflowDir",
    "comfyuiPath",
    "comfyuiPython",
    "modelsDir",
    "chatBgPath",
)
# 网络与代理：属于「当前这台机器怎么出网」，跨设备同步只会把另一台带进死角。
NETWORK_KEYS = (
    "comfyuiUrl",
    # ComfyUI 地址簿：条目 0 就是 comfyuiUrl 本身，这里是额外机台与「哪个模态用哪台」。
    # 机台地址必然与本机网络环境绑定（回环/局域网/云端实例），路由又指向那些机台 id
    # ⇒ 两者与 comfyuiUrl 同属网络项，都不进包。
    "comfyExtra",
    "comfyRoute",
    "proxyUrl",
    "proxyEnabled",
    "systemProxyUrl",
    "systemProxySource",
)
PRESERVE_KEYS = DEVICE_KEYS + NETWORK_KEYS

# 密钥字段清单（前端 stripSecrets 的后端投影；递归按字段名剥离，覆盖模型数组）。
# 只用于「默认不进包」，不进包时是**省略字段**而非清空——导入合并得以保留本机已有密钥。
SECRET_KEYS = frozenset({
    "apiKey",
    "hfToken",
    "civitaiToken",
    "smitheryKey",
    "pixivRefreshToken",
    "danbooruLogin",
    "danbooruApiKey",
})
MODEL_LIST_KEYS = ("chatModels", "imageModels", "videoModels")
MODEL_OBJ_KEYS = ("embedModel", "vlmModel")

# 素材分类：包内目录名 -> 设置里的目录键。提示词模版落在 DATA_DIR，单列。
ASSET_DIRS = (
    ("characters", "characterDir"),
    ("worldbooks", "worldbookDir"),
    ("presets", "presetDir"),
    ("workflows", "workflowDir"),
)
PROMPT_KIND = "prompt_templates"
ASSET_KINDS = tuple(kind for kind, _ in ASSET_DIRS) + (PROMPT_KIND,)
WEB_MATERIALS_KIND = "web_materials"
PROVENANCE_NAME = ".provenance.json"

_JUNK_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


class ConfigBundleError(ValueError):
    """导入/导出的用户可见错误（路由层转 4xx）。"""


def _safe_member(name: str) -> bool:
    """zip-slip 守卫：纯相对路径、无盘符、无 `..`/空段。

    与 `repo_transfer._safe_member` 同口径；两模块的包格式独立，故各留一份守卫。
    """
    if (not name or "\\" in name or name.startswith("/")
            or ":" in name.split("/")[0]):
        return False
    return not any(seg in ("", ".", "..") for seg in name.split("/"))


def _asset_roots(settings: dict) -> dict[str, Path]:
    """导出侧素材根：只打包已配置且存在的目录，未配置即空（同步本身仍可用）。"""
    roots: dict[str, Path] = {}
    for kind, key in ASSET_DIRS:
        raw = str(settings.get(key) or "").strip()
        if raw:
            roots[kind] = Path(raw).expanduser()
    roots[PROMPT_KIND] = Path(PROMPT_TEMPLATES_DIR)
    return roots


def _works_root(settings: dict) -> Path | None:
    """作品库根（settings.outputDir）：未配置返回 None；已配置返回 resolve 后的路径。"""
    raw = str(settings.get("outputDir") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return None


def _iter_files(root: Path) -> list[Path]:
    """列出可打包文件：跳过系统垃圾文件与逃出根目录的符号链接。"""
    if not root.is_dir():
        return []
    try:
        resolved_root = root.resolve()
    except OSError:
        return []
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in _JUNK_FILES:
            continue
        try:
            path.resolve().relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        files.append(path)
    return files


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _strip_secrets(node: object) -> None:
    if isinstance(node, dict):
        for key in list(node):
            if key in SECRET_KEYS:
                node.pop(key, None)
            else:
                _strip_secrets(node[key])
    elif isinstance(node, list):
        for item in node:
            _strip_secrets(item)


def portable_settings(settings: dict, *, include_secrets: bool = False) -> dict:
    """导出用设置投影：去掉设备路径/网络项；密钥默认整字段省略。"""
    out = json.loads(json.dumps(settings))
    for key in PRESERVE_KEYS:
        out.pop(key, None)
    if not include_secrets:
        _strip_secrets(out)
    return out


def _repo_export_entries(records: list, works_root: Path) -> list[tuple[dict, Path]]:
    """找出有实际文件夹的仓库（按路径去重），父作品排在子仓库之前。"""
    entries: list[tuple[dict, Path, Path]] = []
    seen: set[Path] = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("id"):
            continue
        folder = repo_meta.find_repo_folder(str(works_root), str(record["id"]))
        if folder is None:
            continue
        try:
            resolved = folder.resolve()
            resolved.relative_to(works_root)
        except (OSError, ValueError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        entries.append((record, folder, resolved))
    entries.sort(key=lambda item: (len(item[2].parts), item[2].as_posix()))
    return [(record, folder) for record, folder, _ in entries]


def _write_repos(
    zf: zipfile.ZipFile, records: list, works_root: Path | None,
) -> tuple[dict, list[dict]]:
    """把仓库文件夹写进包：父仓库跳过子仓库目录，子仓库单独成组，避免重复打包。"""
    if works_root is None:
        return {"count": 0, "files": 0, "bytes": 0}, []
    entries = _repo_export_entries(records, works_root)
    resolved_dirs = [folder.resolve() for _, folder in entries]
    items: list[dict] = []
    files_total = 0
    bytes_total = 0
    for record, folder in entries:
        resolved = folder.resolve()
        try:
            folder_rel = resolved.relative_to(works_root).as_posix()
        except ValueError:
            continue
        nested = [
            other for other in resolved_dirs
            if other != resolved and resolved in other.parents
        ]
        files: list[Path] = []
        for path in _iter_files(folder):
            try:
                resolved_file = path.resolve()
            except OSError:
                continue
            if any(
                resolved_file == sub or sub in resolved_file.parents for sub in nested
            ):
                continue
            files.append(path)
        index = len(items)
        for path in files:
            zf.write(path, f"repos/{index}/{path.relative_to(folder).as_posix()}")
            bytes_total += _size_of(path)
        files_total += len(files)
        items.append({
            "index": index,
            "record": record,
            "folder": folder_rel,
            "files": len(files),
        })
    return {"count": len(items), "files": files_total, "bytes": bytes_total}, items


def _write_web_materials(
    zf: zipfile.ZipFile, works_root: Path | None,
) -> dict[str, int]:
    """上网素材目录：作品库根的 `_web_materials/`（含 provenance）。"""
    if works_root is None:
        return {"files": 0, "bytes": 0}
    root = image_store.web_materials_dir(str(works_root))
    files = _iter_files(root)
    total = 0
    for path in files:
        zf.write(path, f"{WEB_MATERIALS_KIND}/{path.relative_to(root).as_posix()}")
        total += _size_of(path)
    return {"files": len(files), "bytes": total}


# 随包走的资产字段：`asset_store.upsert_asset` 入参形状去掉三项——
# `image_url` 与设备绑定、`thumb_url` 是本机派生层、`deleted_at` 是回收站状态。
# `id` / `repo_id` 虽在条目里另写一份，但那是**导出端**的身份，只作对账留痕；
# 导入端一律重算（见 `_import_asset_index`）。
_ASSET_CARRY_KEYS = (
    "kind", "media_type", "source", "prompt", "description", "tags",
    "template_name", "model_name", "lora_names", "created_at",
    "source_url", "source_meta", "derived_from",
)


def _asset_index_items(
    rows: list[dict], works_root: Path, folders: dict[str, str],
) -> tuple[list[dict], int]:
    """资产记录 → 包内条目（图片落点改写成「仓库文件夹 + 夹内相对路径」两段）。

    设备绝对路径不进包（与 `DEVICE_KEYS` 同一条纪律）。文件夹取**最长匹配**：
    子仓库的文件归子仓库——`_write_repos` 打包父夹时刻意跳过了子仓库目录，
    取父夹会让导入端拼出一个包里不存在的相对路径。
    """
    items: list[dict] = []
    skipped = 0
    for row in rows:
        path = thumbnail_store.local_path_of_url(str(row.get("image_url") or ""))
        if path is None:
            skipped += 1        # 远程图 / 原文件已不在本机：没有可对账的落点
            continue
        try:
            rel = path.resolve().relative_to(works_root).as_posix()
        except (OSError, ValueError):
            skipped += 1        # 作品库根之外（ComfyUI 目录、别的盘）：本包不认领
            continue
        folder = ""
        for candidate in folders:
            if rel.startswith(f"{candidate}/") and len(candidate) > len(folder):
                folder = candidate
        if not folder:
            skipped += 1        # 不属于任何导出的仓库（`_web_materials` 与孤儿记录）
            continue
        item = {key: row.get(key) for key in _ASSET_CARRY_KEYS}
        item["id"] = str(row.get("id") or "")    # 导出端 id：仅对账留痕，导入端重算
        item["repo_id"] = folders[folder]
        item["folder"] = folder
        item["rel"] = rel[len(folder) + 1:]
        items.append(item)
    return items, skipped


def _write_asset_index(
    zf: zipfile.ZipFile, works_root: Path | None, repo_items: list[dict],
) -> dict[str, int]:
    """写 `assets.json`：资产元数据账本。没有仓库文件夹时整条成员不写（旧包同形）。"""
    if works_root is None or not repo_items:
        return {"count": 0, "skipped": 0}
    folders = {
        str(item["folder"]): str(item["record"].get("id") or "") for item in repo_items
    }
    items, skipped = _asset_index_items(
        asset_library.export_asset_records(), works_root, folders)
    zf.writestr(
        ASSETS_MEMBER, json.dumps({"items": items}, ensure_ascii=False, indent=2))
    return {"count": len(items), "skipped": skipped}


def _import_asset_index(
    zf: zipfile.ZipFile, id_map: dict[str, str], folder_map: dict[str, str],
) -> dict[str, object]:
    """`assets.json` → 本机资产主表（纯 SQLite 写，零嵌入调用）。

    为什么不走 `repo_sync_fix` 补录：那条路给每张图重算嵌入（实测 57 仓库 / 880 张
    ⇒ 880 次远程嵌入调用），手机上还常常没配嵌入模型。列表页读的是主表
    （`asset_library.list_generations_page`），元数据随包走就够了。
    ⚠ 主表一旦有行，读路径就**不再回退向量库** ⇒ 手机上是「账本 + 图片」而非全量语义库，
    这是设计取舍（离线可用优先），不是缺漏。
    """
    try:
        raw = zf.read(ASSETS_MEMBER)
    except KeyError:
        return {"present": False, "imported": 0, "skipped": 0, "failed": 0}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ConfigBundleError("配置包的 assets.json 不是合法 JSON") from exc
    entries = data.get("items") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ConfigBundleError("配置包的 assets.json 缺少 items")

    output_dir = repo_meta.output_dir_from_state()
    works_root = Path(output_dir).expanduser().resolve() if output_dir else None
    imported = skipped = failed = 0
    for position, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ConfigBundleError(f"配置包的资产条目 #{position} 不是对象")
        rel = str(item.get("rel") or "")
        # 先过 zip-slip 守卫再查映射：恶意 rel 无论能不能落位都必须被拒，不能因为
        # 「这条本来就 skip 了」而悄悄放过去。
        if not _safe_member(rel):
            raise ConfigBundleError(f"配置包的资产路径不合法（zip-slip 拒绝）：{rel}")
        new_folder = folder_map.get(str(item.get("folder") or ""))
        new_repo_id = id_map.get(str(item.get("repo_id") or ""))
        if works_root is None or not new_folder or not new_repo_id:
            skipped += 1        # 对应的仓库这次没进来（或包内文件夹对不上）
            continue
        target = works_root / new_folder / rel
        try:
            target.resolve().relative_to(works_root)
        except (OSError, ValueError):
            skipped += 1
            continue
        row = {key: item.get(key) for key in _ASSET_CARRY_KEYS}
        image_url = view_urls.local_view(str(target))
        row["repo_id"] = new_repo_id
        row["image_url"] = image_url
        # id 是 sha1(repo_id|image_url)：换设备两个输入都变了，必须按新值重算，
        # 否则与本机后续写入/删除用的 id 对不上（同 id 覆盖不成、删不掉）。
        row["id"] = rag_store.generation_doc_id(new_repo_id, image_url)
        try:
            asset_store.upsert_asset(row)
            imported += 1
        except Exception:  # noqa: BLE001 [EXC-留痕] 单条写坏不阻断整包导入
            failed += 1
    return {"present": True, "imported": imported, "skipped": skipped, "failed": failed}


def export_bundle(*, include_secrets: bool = False) -> tuple[Path, str]:
    """把可移植设置与资源打成 zip（落在 DATA_DIR/transfer/），返回 (路径, 文件名)。"""
    state = repo_meta.load_user_state()
    settings = state.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    portable = portable_settings(settings, include_secrets=include_secrets)
    roots = _asset_roots(settings)
    works_root = _works_root(settings)

    out_dir = Path(config.DATA_DIR) / "transfer"
    out_dir.mkdir(parents=True, exist_ok=True)
    handle = NamedTemporaryFile(
        dir=out_dir, prefix="config-export-", suffix=".demicfg.zip", delete=False)
    zip_path = Path(handle.name)
    handle.close()

    counts: dict[str, dict[str, int]] = {}
    repos_meta: dict[str, int] = {"count": 0, "files": 0, "bytes": 0}
    web_meta: dict[str, int] = {"files": 0, "bytes": 0}
    regex_scripts = regex_store.load_scripts()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for kind, root in roots.items():
            files = _iter_files(root)
            total = 0
            for path in files:
                zf.write(path, f"assets/{kind}/{path.relative_to(root).as_posix()}")
                total += _size_of(path)
            counts[kind] = {"files": len(files), "bytes": total}

        repos_meta, repo_items = _write_repos(
            zf, list(state.get("repos") or []), works_root)
        if repo_items:
            zf.writestr(
                REPOS_MEMBER,
                json.dumps({"items": repo_items}, ensure_ascii=False, indent=2))
        web_meta = _write_web_materials(zf, works_root)
        assets_meta = _write_asset_index(zf, works_root, repo_items)
        zf.writestr(
            REGEX_MEMBER, json.dumps(regex_scripts, ensure_ascii=False, indent=2))

        meta = {
            "app": "demiurge",
            "kind": "config",
            "version": FORMAT_VERSION,
            "exported_at": time.time(),
            "source_platform": sys.platform,
            "secrets_included": include_secrets,
            "settings_keys": sorted(portable.keys()),
            "assets": counts,
            "repos": repos_meta,
            "regex": {"count": len(regex_scripts)},
            "web_materials": web_meta,
            "asset_index": assets_meta,
        }
        zf.writestr(BUNDLE_META, json.dumps(meta, ensure_ascii=False, indent=2))
        zf.writestr(
            SETTINGS_MEMBER, json.dumps(portable, ensure_ascii=False, indent=2))

    name = time.strftime("demiurge-config-%Y%m%d-%H%M.demicfg.zip")
    return zip_path, name


def inventory() -> dict[str, object]:
    """本机现有资源计数（同步面板展示用）。

    口径与各自列表接口一致：角色卡只数含 card.json 的目录，世界书/预设只数能解析的
    JSON；这样面板上的数字与用户点进资源页看到的一致。只读，不写任何东西。
    """
    state = repo_meta.load_user_state()
    raw_settings = state.get("settings")
    settings = raw_settings if isinstance(raw_settings, dict) else {}
    roots = _asset_roots(settings)
    works_root = _works_root(settings)
    characters = roots.get("characters")
    worldbooks = roots.get("worldbooks")
    presets = roots.get("presets")
    workflows = roots.get("workflows")
    web_root = (
        image_store.web_materials_dir(str(works_root)) if works_root is not None else None)
    counts: dict[str, int] = {
        "characters": len(character_store.list_cards(str(characters))) if characters else 0,
        "worldbooks": len(worldbook_store.list_books(str(worldbooks))) if worldbooks else 0,
        "presets": len(preset_store.list_presets(str(presets))) if presets else 0,
        "workflows": len(_iter_files(workflows)) if workflows else 0,
        PROMPT_KIND: len(_iter_files(roots[PROMPT_KIND])),
        "repos": sum(
            1 for item in (state.get("repos") or [])
            if isinstance(item, dict) and item.get("id")),
        "regex": len(regex_store.load_scripts()),
        # provenance 是素材来源账本，不算素材本身
        WEB_MATERIALS_KIND: (
            sum(1 for path in _iter_files(web_root) if path.name != PROVENANCE_NAME)
            if web_root is not None else 0),
    }
    return {"counts": counts}


def download_base_dir() -> Path:
    """导出落盘根：系统下载目录（Windows 中文系统的物理目录名通常仍是 Downloads）。"""
    for name in ("Downloads", "下载"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate
    return Path(config.DATA_DIR) / "exports"


def save_bundle(*, include_secrets: bool = False) -> dict:
    """导出并保存到系统下载目录（桌面壳没有下载处理器，必须后端落盘）。"""
    zip_path, filename = export_bundle(include_secrets=include_secrets)
    target_dir = download_base_dir() / "Demiurge"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    base = filename[:-4] if filename.endswith(".zip") else filename
    suffix = 2
    while target.exists():
        target = target_dir / f"{base}-{suffix}.zip"
        suffix += 1
    try:
        shutil.move(str(zip_path), str(target))
    except OSError as exc:
        cleanup_bundle_file(zip_path)
        raise ConfigBundleError(f"保存配置包失败：{exc}") from exc
    return {
        "path": str(target.resolve()),
        "filename": target.name,
        "bytes": _size_of(target),
    }


def reveal_export(raw_path: str) -> bool:
    """在资源管理器中定位刚导出的配置包；只允许定位下载目录内的文件。"""
    target = Path(raw_path).expanduser().resolve()
    root = (download_base_dir() / "Demiurge").resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConfigBundleError("只能定位「下载/Demiurge」里导出的配置包") from exc
    if not target.is_file():
        raise ConfigBundleError("配置包文件不存在（可能已被移动或删除）")
    from app.services import collection_artifacts

    return collection_artifacts.reveal_in_folder(target, select=True)


def _merge_model_list(current: object, incoming: list) -> list:
    """模型数组按 id 合并：包内省略 apiKey（未含密钥导出）时保留本机同名模型密钥。"""
    by_id: dict[str, dict] = {}
    if isinstance(current, list):
        by_id = {
            str(item.get("id")): item
            for item in current
            if isinstance(item, dict) and item.get("id")
        }
    merged: list = []
    for item in incoming:
        if isinstance(item, dict) and item.get("id") and "apiKey" not in item:
            old = by_id.get(str(item["id"]))
            if isinstance(old, dict) and old.get("apiKey"):
                item = {**item, "apiKey": old["apiKey"]}
        merged.append(item)
    return merged


def _merge_model_obj(current: object, incoming: dict) -> dict:
    if "apiKey" not in incoming and isinstance(current, dict) and current.get("apiKey"):
        return {**incoming, "apiKey": current["apiKey"]}
    return incoming


def merge_settings(current: object, incoming: dict) -> dict:
    """把包内设置合并进本机设置。

    - 未出现在包内的键保持本机原值（密钥省略导出即此语义）；
    - `PRESERVE_KEYS` 永远取本机现值（包内即使夹带也不采用）；
    - 模型数组/对象按 id 保留本机密钥（见 `_merge_model_list`）。
    """
    merged: dict = (
        json.loads(json.dumps(current)) if isinstance(current, dict) else {})
    for key, value in incoming.items():
        if key in PRESERVE_KEYS:
            continue
        if key in MODEL_LIST_KEYS and isinstance(value, list):
            merged[key] = _merge_model_list(merged.get(key), value)
        elif key in MODEL_OBJ_KEYS and isinstance(value, dict):
            merged[key] = _merge_model_obj(merged.get(key), value)
        else:
            merged[key] = value
    return merged


def _parse_members(zf: zipfile.ZipFile) -> tuple[dict, dict, list[zipfile.ZipInfo]]:
    try:
        meta = json.loads(zf.read(BUNDLE_META).decode("utf-8"))
    except KeyError as exc:
        raise ConfigBundleError(
            "不是 Demiurge 配置包（缺少 __config/meta.json）") from exc
    if (not isinstance(meta, dict) or meta.get("app") != "demiurge"
            or meta.get("kind") != "config"):
        raise ConfigBundleError("不是 Demiurge 配置包（meta 校验失败）")
    version = meta.get("version")
    if not isinstance(version, int) or not 1 <= version <= FORMAT_VERSION:
        raise ConfigBundleError("配置包版本不受支持（请更新 Demiurge 后再导入）")
    try:
        incoming = json.loads(zf.read(SETTINGS_MEMBER).decode("utf-8"))
    except KeyError as exc:
        raise ConfigBundleError("配置包缺少 settings.json") from exc
    if not isinstance(incoming, dict):
        raise ConfigBundleError("配置包的 settings.json 不是对象")

    members: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        if info.is_dir() or info.filename in (
                BUNDLE_META, SETTINGS_MEMBER, REPOS_MEMBER, REGEX_MEMBER,
                ASSETS_MEMBER):
            continue
        if not _safe_member(info.filename):
            raise ConfigBundleError(
                f"包内路径不合法（zip-slip 拒绝）：{info.filename}")
        parts = info.filename.split("/")
        is_asset = len(parts) >= 3 and parts[0] == "assets" and parts[1] in ASSET_KINDS
        is_repo = len(parts) >= 3 and parts[0] == "repos" and parts[1].isdigit()
        is_web = len(parts) >= 2 and parts[0] == WEB_MATERIALS_KIND
        if not (is_asset or is_repo or is_web):
            raise ConfigBundleError(f"配置包含未知条目：{info.filename}")
        members.append(info)
    return meta, incoming, members


def _parse_repos_index(zf: zipfile.ZipFile) -> list[dict] | None:
    """读 repos.json；缺失（v1 包）返回 None。folder 必须是包内安全相对路径。"""
    try:
        raw = zf.read(REPOS_MEMBER)
    except KeyError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ConfigBundleError("配置包的 repos.json 不是合法 JSON") from exc
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ConfigBundleError("配置包的 repos.json 缺少 items")
    result: list[dict] = []
    seen: set[str] = set()
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigBundleError("配置包的 repos.json 条目不是对象")
        record = item.get("record")
        folder = item.get("folder")
        if not isinstance(record, dict) or not record.get("id"):
            raise ConfigBundleError("配置包的仓库条目缺 repos 记录")
        if not isinstance(folder, str) or not _safe_member(folder):
            raise ConfigBundleError(f"配置包的仓库路径不合法：{folder!r}")
        if folder in seen:
            raise ConfigBundleError(f"配置包的仓库路径重复：{folder}")
        seen.add(folder)
        index = item.get("index", position)
        if not isinstance(index, int) or index < 0:
            raise ConfigBundleError("配置包的仓库分组序号不合法")
        files = item.get("files")
        if files is not None and (not isinstance(files, int) or files < 0):
            raise ConfigBundleError("配置包的仓库条目文件数不合法")
        result.append({
            "record": record, "folder": folder, "index": index, "files": files})
    return result


def _import_targets() -> dict[str, Path]:
    """导入侧素材根：本机必须已配置目录（手机首启已播种）。"""
    targets: dict[str, Path] = {}
    for kind, key in ASSET_DIRS:
        raw = repo_meta.setting_dir_from_state(key)
        if not raw:
            raise ConfigBundleError(f"本机未配置素材文件夹（{key}），无法导入")
        targets[kind] = Path(raw).expanduser()
    targets[PROMPT_KIND] = Path(PROMPT_TEMPLATES_DIR)
    return targets


def import_bundle(zip_path: Path, *, overwrite: bool = False) -> dict:
    """把配置包合并进当前设备：素材/仓库/正则/上网素材/资产账本 + 可移植设置。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            meta, incoming, members = _parse_members(zf)
            repos_index = _parse_repos_index(zf)

            targets = _import_targets()
            temp_root = Path(config.DATA_DIR) / "transfer" / f"import-{uuid4().hex}"
            temp_root.mkdir(parents=True, exist_ok=False)
            try:
                zf.extractall(temp_root, members=[m.filename for m in members])
                assets = _merge_assets(temp_root, targets, members, overwrite=overwrite)
                repos, id_map, folder_map = _import_repos(repos_index, temp_root)
                asset_index = _import_asset_index(zf, id_map, folder_map)
                web_materials = _merge_web_materials(
                    temp_root, members, overwrite=overwrite)
                regex = _merge_regex(zf, overwrite=overwrite)
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)
    except zipfile.BadZipFile as exc:
        raise ConfigBundleError("不是有效的 zip 文件") from exc

    # 仓库导入自己会写 user_state（新增 repos 记录），故设置必须在仓库之后合并，
    # 且要重新读一次状态——用导入前的快照保存会把新仓库记录整体抹掉。
    state = repo_meta.load_user_state()
    merged_settings = merge_settings(state.get("settings"), incoming)
    repo_meta.save_user_state({**state, "settings": merged_settings})
    return {
        "settings_updated": sorted(
            key for key in incoming if key not in PRESERVE_KEYS),
        "secrets_included": bool(meta.get("secrets_included")),
        "assets": assets,
        "repos": repos,
        "asset_index": asset_index,
        "regex": regex,
        "web_materials": web_materials,
        "meta": meta,
    }


def _merge_assets(
    temp_root: Path,
    targets: dict[str, Path],
    members: list[zipfile.ZipInfo],
    *,
    overwrite: bool,
) -> dict[str, dict[str, int]]:
    """把临时解包目录里的素材合并进目标根（默认跳过同名文件）。"""
    planned: dict[str, list[tuple[Path, Path]]] = {kind: [] for kind in targets}
    for info in members:
        parts = info.filename.split("/")
        if parts[0] != "assets":
            continue
        _, kind, *rest = parts
        target_root = targets.get(kind)
        if target_root is None:
            raise ConfigBundleError(f"配置包含未知素材分类：{kind}")
        source = temp_root / Path(*parts)
        dest = target_root / Path(*rest)
        resolved_target = target_root.resolve()
        try:
            dest.resolve().relative_to(resolved_target)
        except (OSError, ValueError) as exc:
            raise ConfigBundleError(
                f"包内路径逃出素材目录（zip-slip 拒绝）：{info.filename}") from exc
        planned[kind].append((source, dest))

    result: dict[str, dict[str, int]] = {}
    for kind, pairs in planned.items():
        imported = skipped = 0
        for source, dest in pairs:
            if dest.exists() and not overwrite:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            imported += 1
        result[kind] = {"imported": imported, "skipped": skipped}
    return result


def _import_repos(
    repos_index: list[dict] | None, temp_root: Path,
) -> tuple[dict, dict[str, str], dict[str, str]]:
    """导入仓库分组：父作品先落盘，子仓库落在父的新目录下；id 冲突换新并重映射 parentId。

    除汇总外还返回 `id_map`（旧 id → 新 id）与 `folder_map`（旧文件夹 → 新文件夹）：
    资产账本 `assets.json` 按这两张表重定位，没有第三条寻址路径。
    """
    if not repos_index:
        return {"imported": 0, "skipped": 0, "items": []}, {}, {}
    output_dir = repo_meta.output_dir_from_state()
    if not output_dir:
        raise ConfigBundleError(repo_meta.WORKS_ROOT_NOT_CONFIGURED_MSG)

    ordered = sorted(
        repos_index,
        key=lambda item: (item["folder"].count("/"), item["folder"]),
    )
    id_map: dict[str, str] = {}
    folder_map: dict[str, str] = {}
    imported: list[dict] = []
    for item in ordered:
        record = dict(item["record"])
        old_id = str(record["id"])
        old_parent = record.get("parentId")
        if old_parent:
            mapped_parent = id_map.get(str(old_parent))
            if mapped_parent:
                record["parentId"] = mapped_parent
            else:
                record.pop("parentId", None)

        parent_old = item["folder"].rsplit("/", 1)[0] if "/" in item["folder"] else ""
        parent_folder = folder_map.get(parent_old)
        source = temp_root / "repos" / str(item["index"])
        if not source.is_dir():
            # 父作品只含子仓库（或本身是空作品）时导出端没有可打包文件，zip 里
            # 不存在该分组目录；声明 files: 0 才按空目录导入，声明有文件仍判包损坏。
            if item.get("files") == 0:
                source.mkdir(parents=True, exist_ok=True)
            else:
                raise ConfigBundleError(
                    f"配置包缺少仓库文件分组：{item['folder']}")
        try:
            result = repo_transfer.import_repo_folder(
                source,
                record,
                parent_folder=Path(parent_folder) if parent_folder else None,
            )
        except repo_transfer.TransferError as exc:
            raise ConfigBundleError(str(exc)) from exc
        id_map[old_id] = str(result["repo_id"])
        folder_map[item["folder"]] = str(result["folder"])
        imported.append({
            "repo_id": result["repo_id"],
            "name": result.get("name", ""),
            "folder": result["folder"],
        })
    return {"imported": len(imported), "skipped": 0, "items": imported}, id_map, folder_map


def _merge_web_materials(
    temp_root: Path,
    members: list[zipfile.ZipInfo],
    *,
    overwrite: bool,
) -> dict[str, int]:
    """合并 `_web_materials/`：默认跳过同名，provenance 只跟随真正写入本机的文件。"""
    relevant = [
        info for info in members if info.filename.startswith(f"{WEB_MATERIALS_KIND}/")
    ]
    if not relevant:
        return {"imported": 0, "skipped": 0}
    output_dir = repo_meta.output_dir_from_state()
    if not output_dir:
        raise ConfigBundleError(repo_meta.WORKS_ROOT_NOT_CONFIGURED_MSG)
    target_root = image_store.web_materials_dir(output_dir)
    target_resolved = target_root.resolve()

    source_root = temp_root / WEB_MATERIALS_KIND
    incoming_prov: dict = {}
    try:
        raw = json.loads(
            (source_root / PROVENANCE_NAME).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            incoming_prov = raw
    except (OSError, ValueError):
        pass

    imported = skipped = 0
    written: set[str] = set()
    for info in relevant:
        parts = info.filename.split("/")
        rel = Path(*parts[1:])
        if rel.as_posix() == PROVENANCE_NAME:
            continue
        source = temp_root / Path(*parts)
        dest = target_root / rel
        try:
            dest.resolve().relative_to(target_resolved)
        except (OSError, ValueError) as exc:
            raise ConfigBundleError(
                f"包内路径逃出上网素材目录（zip-slip 拒绝）：{info.filename}") from exc
        if dest.exists() and not overwrite:
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        written.add(rel.as_posix())
        imported += 1

    if written:
        current = image_store.load_web_material_provenance(output_dir)
        changed = False
        for name, value in incoming_prov.items():
            if not isinstance(value, dict):
                continue
            if name in written and (name not in current or overwrite):
                current[name] = value
                changed = True
        if changed:
            image_store.save_web_material_provenance(output_dir, current)
    return {"imported": imported, "skipped": skipped}


def _merge_regex(zf: zipfile.ZipFile, *, overwrite: bool) -> dict[str, int]:
    """按 id（无 id 用 scriptName）合并全局正则：默认跳过同名，overwrite 才替换。"""
    try:
        raw = zf.read(REGEX_MEMBER)
    except KeyError:
        return {"imported": 0, "skipped": 0}
    try:
        incoming = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ConfigBundleError("配置包的 regex.json 不是合法 JSON") from exc
    if not isinstance(incoming, list):
        raise ConfigBundleError("配置包的 regex.json 不是数组")

    current = regex_store.load_scripts()

    def key_of(script: dict) -> str:
        if script.get("id"):
            return f"id:{script['id']}"
        name = script.get("scriptName")
        return f"name:{name}" if name else ""

    index: dict[str, int] = {}
    for position, script in enumerate(current):
        if not isinstance(script, dict):
            continue
        key = key_of(script)
        if key and key not in index:
            index[key] = position

    imported = skipped = 0
    for script in incoming:
        if not isinstance(script, dict):
            skipped += 1
            continue
        key = key_of(script)
        if not key:
            skipped += 1
            continue
        matched = index.get(key)
        if matched is None:
            current.append(script)
            index[key] = len(current) - 1
            imported += 1
        elif overwrite:
            current[matched] = script
            imported += 1
        else:
            skipped += 1
    if imported:
        regex_store.save_scripts(current)
    return {"imported": imported, "skipped": skipped}


def cleanup_bundle_file(path: Path) -> None:
    """FileResponse 背景任务：下载完成后删临时 zip。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
