"""合集卡产物收集与访问校验：fabric 自由循环 done 后扫描交付物，供前端产物卡展示。

产物 = **产物域**（作品域 ∪ 本作品拥有的卡目录，见 `repo_meta.artifact_domains`）下各
目录的 card.json（主卡）与 worldbook.json（世界书快照）、域级同名文件，以及
`docs/*.md`（文档交付）与 `docs/assets/*.<图片>`（文档插图素材）。
前端拿到路径后可预览 / 下载 / 在资源管理器中定位。

安全：单机回环门禁（main._loopback_only）外的纵深防御——所有访问端点必须携带
output_dir（作品库根）与 repo_id，path 校验 resolve 后必须落在**某个域**内
（防 ../ 穿越到任意目录），且形态在 `is_allowed_artifact` 白名单内。
与 comfyui local-view 的「扩展名白名单」策略互补：这里是「目录 jail + 只读」。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ArtifactMeta",
    "artifact_kind",
    "collect_artifacts",
    "domain_owner",
    "is_allowed_artifact",
    "is_allowed_in_domains",
    "resolve_artifact",
    "artifact_display_name",
]

# 产物版本快照目录名（唯一属主，artifact_versions 反向引用；
# collect 必须排除它，否则 _versions/<序号>-<时间戳>/card.json 会被当成最新产物）。
VERSIONS_DIR = "_versions"
# 每个作品根最多收集的产物数量（防把整个素材库都列成卡片）
_MAX_ARTIFACTS = 20
# 下载/预览只允许的产物文件名（白名单：fabric 可交付形态，防把任意 json 当产物展示）
_ALLOWED_NAMES = {"card.json", "worldbook.json"}
# 文档交付（2026-09-10 链路③）：`<作品>/docs/*.md` 也要能被收集与预览——
# 固化04「整理成设定总集」的产物就是 md，此前既不在产物卡里、预览也只按纯文本渲染。
# 图片素材（doc.attach_material 落 docs/assets/）需可访问，否则文档里的图必裂。
DOCS_DIR_NAME = "docs"
DOCS_ASSETS_DIR_NAME = "assets"
DOC_SUFFIXES = {".md"}
DOC_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}
# 每个作品根最多收集的文档产物数（防把整个文档库都列成卡片）
_MAX_DOCS = 8
# 预览文本大小上限：超过只返回头部（前端提示截断），避免一次读入超大文件
_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
# 文档插图素材单文件上限（2026-09-10 C1）：`doc.attach_material` 落盘前校验，防止
# 误指巨型文件（视频/压缩包/超大原图）撑爆内存与作品目录——此前无任何上限。
# 取值对齐 generation_store 的生图产物下载上限（30MB）：正常生图产物一定能插图，
# 同时挡住明显异常的大文件。与后缀白名单同为「插图素材」的单一属主，改一处即生效。
DOC_ASSET_MAX_BYTES = 30 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactMeta:
    """产物元数据（wire 字段即前端 ArtifactMeta，保持驼峰命名）。"""

    kind: str          # card | worldbook | file
    name: str          # 人类可读名，如「角色主卡 · 玫瑰与繁花」
    path: str          # 绝对路径（前端只展示+回传，不自行拼接）
    size: int          # 字节数
    mtime: float       # 修改时间 epoch 秒

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "mtime": self.mtime,
        }


def artifact_display_name(filename: str, folder: str = "") -> str:
    """产物的人类可读名：文件名 + 归属目录（有的话）。"""
    label = {
        "card.json": "角色主卡",
        "worldbook.json": "世界书",
    }.get(filename)
    if label is None and Path(filename).suffix.lower() in DOC_SUFFIXES:
        label = f"文档 · {Path(filename).stem}"
    label = label or filename
    return f"{label} · {folder}" if folder else label


def artifact_kind(path: str | Path) -> str:
    """产物 kind（wire 字段，单一属主）：card | worldbook | doc | file。

    与 `is_allowed_artifact` 是**同一模块内、共用同一批常量**（`_ALLOWED_NAMES` /
    `DOC_SUFFIXES` / `DOC_ASSET_SUFFIXES`）的**相邻判定**，但**不等价**：本函数只看
    名字/后缀（描述形态，前端据此选渲染方式），`is_allowed_artifact` 才校验位置。
    例：`docs/sub/x.md` → kind="doc" 而白名单 False（2026-09-10 B4 措辞修正，此前误写成
    「与白名单同源判定」）。
    **所以 kind 不是授权**：任何「看一眼 kind 就放行」的写法都会把未授权路径也放出去。

    单一属主的收益：避免「收集用一套、预览用另一套」漂移（此前预览端点自己写死
    `card if name == card.json else worldbook`，md 文档会被误标成 worldbook）。
    """
    name = Path(path).name
    if name == "card.json":
        return "card"
    if name == "worldbook.json":
        return "worldbook"
    if Path(path).suffix.lower() in DOC_SUFFIXES:
        return "doc"
    return "file"


def is_allowed_artifact(target: Path, root: Path) -> bool:
    """产物访问白名单（目录 jail 之外的形态校验）。

    允许：**某域内**的 `card.json` / `worldbook.json`（任意层级）、
    `docs/*.md`（文档交付）、`docs/assets/*.<图片>`（文档插图素材）。
    其它一律拒绝——防把任意 json/文件当产物暴露给前端。
    """
    if target.name in _ALLOWED_NAMES:
        return True
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return False
    suffix = target.suffix.lower()
    if (len(parts) == 2 and parts[0] == DOCS_DIR_NAME and suffix in DOC_SUFFIXES):
        return True
    return (len(parts) == 3 and parts[0] == DOCS_DIR_NAME
            and parts[1] == DOCS_ASSETS_DIR_NAME and suffix in DOC_ASSET_SUFFIXES)


def is_allowed_in_domains(target: Path, roots: "list[Path] | tuple[Path, ...]") -> bool:
    """**域名白名单**（2026-09-10 B3/A）：路径落在任一域内且形态合法即放行。

    为什么不是单根判定：产物域是「作品域 ∪ 本作品拥有的卡目录」的集合
    （见 `repo_meta.artifact_domains`）——卡在 `<卡名>/`、docs 在 `<作品域>/docs/`，
    两者的「相对根形态」各自成立，但相对另一个域就不成立。逐域判定再取或，
    既保住「白名单按相对域形态判」的简单规则，也不需要把两个域拼成一个假根。
    """
    return any(is_allowed_artifact(target, root) for root in roots)


def domain_owner(target: Path, roots: "list[Path] | tuple[Path, ...]") -> Path | None:
    """target 归属于哪个域（第一个包含它的域）；不属于任何域返回 None。

    给路由层用：命中域即可直接拿该域做 `resolve_artifact`，错误信息（404/403）
    才与用户实际访问的位置一致，不会因为「先用另一个域试」报出误导性的越界错误。
    """
    for root in roots:
        try:
            if target.is_relative_to(Path(root).expanduser().resolve()):
                return Path(root)
        except (OSError, ValueError):
            continue
    return None


def _scan_domain(root: Path, since: float = 0.0,
                 ) -> "tuple[list[ArtifactMeta], list[ArtifactMeta]]":
    """扫单个域，返回 (core 卡/世界书, docs 文档)。域不存在/无权限 → 双空。

    与旧实现的唯一区别是**把「一个作品根」变成「一个域」**：`collect_artifacts`
    会遍历全部域后统一聚合（见 `collect_artifacts`），单域行为逐字节等价。
    """
    try:
        root = root.expanduser().resolve()
        if not root.is_dir():
            return [], []
    except OSError:
        return [], []
    core: list[ArtifactMeta] = []
    _collect_from_dir(root, core, since=since)
    # 域级直接交付物（世界书快照可能直落域根，如用户手动放置）
    for filename in _ALLOWED_NAMES:
        candidate = root / filename
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                stat = candidate.stat()
                if since > 0 and stat.st_mtime < since:
                    continue
                core.append(ArtifactMeta(
                    kind=artifact_kind(candidate),
                    name=artifact_display_name(filename, ""),
                    path=str(candidate),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                ))
        except OSError:
            continue
    docs: list[ArtifactMeta] = []
    _collect_docs(root, docs, since=since)
    return core, docs


def _domains_of(output_dir: str, repo_id: str) -> "list[Path]":
    """解析产物访问/收集域：有 repo_id → 作品域 ∪ 本作品拥有的卡目录；否则作品库根单域（低层原语）。

    **低层原语**（生产调用方 `agent_graph._collect_fabric_artifacts` 恒带 ctx.repo_id）。
    产物 HTTP 端点的「缺值返回空结果」策略在 `routers/artifacts.py._domains` 短路，
    **不**复用本函数的空分支——改这里会连带影响 fabric done 的收产区。
    """
    if repo_id:
        from app.services import repo_meta
        try:
            return repo_meta.artifact_domains(output_dir, repo_id)
        except Exception:  # noqa: BLE001 - 归属探测异常时退化为单域，不阻断产物卡
            return [Path(output_dir)] if (output_dir or "").strip() else []
    return [Path(output_dir)] if (output_dir or "").strip() else []


def _collect_from_dir(root: Path, artifacts: list[ArtifactMeta], since: float = 0.0) -> None:
    """扫描单个目录的 card.json / worldbook.json（存在才收），按目录名归组。

    since>0 时只收 mtime ≥ since 的文件（= 本次运行实际写入的产物），避免把
    上一轮/历史作品的产物当成本轮产物（2026-09-09 实锤：御仙任务显示玫瑰与繁花）。
    """
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name in ("_prep", VERSIONS_DIR):
            continue
        for filename in _ALLOWED_NAMES:
            candidate = entry / filename
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    stat = candidate.stat()
                    if since > 0 and stat.st_mtime < since:
                        continue
                    artifacts.append(ArtifactMeta(
                        kind=artifact_kind(candidate),
                        name=artifact_display_name(filename, entry.name),
                        path=str(candidate),
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                    ))
            except OSError:
                continue


def _collect_docs(root: Path, artifacts: list[ArtifactMeta], since: float = 0.0) -> None:
    """收集 `<root>/docs/*.md` 文档交付物（不递归，避免把整个文档库列成卡片）。

    作为**独立附加项**参与结果（见 collect_artifacts）：文档的父目录是 `docs/`，
    与卡目录不同组，若只走「mtime 最新目录组」聚合会被卡组挤掉——而「整理成文档」
    这类任务的主交付物恰恰就是它。since>0 时只收本次运行写入的文档。

    超过 `_MAX_DOCS` 时**按 mtime 倒序取最新若干份**（2026-09-10 A3）：`docs/` 是
    只增不减的累积目录，原实现按文件名排序取前 N，份数一多，新写的文档只要名字排在
    字母序后面就永远进不了产物卡（静默丢失）。收集语义是「最近的交付物」，最新优先
    才是正确口径；同一批写入 mtime 相同时再按文件名做稳定排序（结果可复现）。
    """
    docs_dir = root / DOCS_DIR_NAME
    if not docs_dir.is_dir():
        return
    try:
        entries = list(docs_dir.iterdir())
    except OSError:
        return
    files: list[tuple[float, Path, os.stat_result]] = []
    for candidate in entries:
        try:
            if not candidate.is_file() or candidate.suffix.lower() not in DOC_SUFFIXES:
                continue
            stat = candidate.stat()
            if stat.st_size <= 0:
                continue
            if since > 0 and stat.st_mtime < since:
                continue
        except OSError:
            continue
        files.append((stat.st_mtime, candidate, stat))
    files.sort(key=lambda item: (-item[0], item[1].name))
    for mtime, candidate, stat in files[:_MAX_DOCS]:
        artifacts.append(ArtifactMeta(
            kind=artifact_kind(candidate),
            name=artifact_display_name(candidate.name),
            path=str(candidate),
            size=stat.st_size,
            mtime=mtime,
        ))


def collect_artifacts(output_dir: str, repo_id: str = "", since: float = 0.0) -> list[dict]:
    """扫描**产物访问域**，返回**最近一次被写入的交付物目录**里的产物元数据。

    产物卡语义 = 「fabric 自由循环刚做完的这一批交付物」。fabric done 时最近被
    写入的 card.json/worldbook.json 几乎必然属于本次会话，因此按「mtime 最新的
    交付物目录」聚合即可——对 repo 身份改名（repo_name/folder_name 漂移、卡名
    目录与快照目录分叉）免疫，无需猜目录归属（2026-09-08 实锤：全根扫描会把
    历史作品的世界书也列成卡片）。

    2026-09-10 B3/A：`repo_id` 从「软提示」变成真参与域解析——域 = 作品域 ∪ 本作品
    拥有的卡目录（`_domains_of`）。给了 repo_id 时不再扫整个作品库根，历史上
    「在 A 作品的历史消息里刷出 B 作品刚产出的东西」由此关闭；且**卡目录名可以与
    作品文件夹名不同**（归属靠 `_work.json`），故域是集合而非单根。

    文档交付（2026-09-10 链路③）：`docs/*.md` 是**独立附加项**——父目录是 `docs/`，
    与卡目录不同组，若只走「最新目录组」聚合会被卡组挤掉；而「整理成设定总集」这类
    任务的主交付物恰恰只有它（无卡无世界书），因此**文档可以单独成组**：core 为空时
    也照常返回文档，不再提前 `return []`（原实现在这里会把纯文档交付整批吞掉）。

    异常（域不存在/无权限）一律返回空列表，不阻断 fabric done —— 产物卡只是展示增强。
    """
    core_items: list[ArtifactMeta] = []
    docs_items: list[ArtifactMeta] = []
    for domain in _domains_of(output_dir, repo_id):
        core, docs = _scan_domain(domain, since=since)
        core_items.extend(core)
        docs_items.extend(docs)
    picks: list[ArtifactMeta] = []
    if core_items:
        # 按父目录聚合，取 mtime 最新的目录组（同一次 fabric 写入间隔秒级，
        # 不同作品/历史作品间隔分钟级以上，取最新组不会混入旧作品）。
        newest = max(core_items, key=lambda a: a.mtime)
        newest_dir = str(Path(newest.path).parent)
        picks = [a for a in core_items if str(Path(a.path).parent) == newest_dir]
        # 最新目录里若无世界书（只有主卡），把域根下世界书副本也带上（B 态合集卡场景）
        kinds = {a.kind for a in picks}
        if "worldbook" not in kinds:
            domain_roots = {str(Path(d).expanduser().resolve()) for d in _domains_of(output_dir, repo_id)}
            wb_root = [a for a in core_items if a.kind == "worldbook"
                       and str(Path(a.path).parent).casefold() in
                       {r.casefold() for r in domain_roots}]
            picks = picks + [a for a in wb_root if a not in picks]
    # 文档交付独立附加（不参与「最新组」竞争；纯文档交付时是唯一内容）
    picks = picks + [a for a in docs_items if a not in picks]
    if not picks:
        return []
    picks.sort(key=lambda a: a.mtime, reverse=True)
    return [a.to_dict() for a in picks[:_MAX_ARTIFACTS]]


class ArtifactAccessError(ValueError):
    """产物访问被拒（路径越界/不存在/超大）。status 与 detail 供路由转 HTTPException。"""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def resolve_artifact(output_dir: str, path: str, *, allow_dir: bool = False) -> Path:
    """校验并解析产物绝对路径：
    - output_dir 必须是非空绝对路径（目录 jail 的 scope）；
    - path resolve 后必须位于 output_dir 内（禁止 ../ 逃逸）；
    - 目标必须存在；默认必须是普通文件（allow_dir=True 时目录也放行，供「打开位置」）。
    返回解析后的 Path；失败抛 ArtifactAccessError。
    """
    if not output_dir or not path:
        raise ArtifactAccessError(400, "缺少产物路径参数")
    try:
        root = Path(output_dir).expanduser().resolve()
        target = Path(path).expanduser().resolve()
    except OSError as exc:
        raise ArtifactAccessError(400, f"路径解析失败：{exc}") from exc
    if not root.is_dir():
        raise ArtifactAccessError(400, "作品根目录不存在")
    try:
        target.relative_to(root)
    except ValueError:
        raise ArtifactAccessError(403, "产物路径超出作品根目录范围") from None
    if target.is_dir():
        if allow_dir:
            return target
        raise ArtifactAccessError(400, "目标不是文件")
    if not target.is_file():
        raise ArtifactAccessError(404, "产物文件不存在")
    if not is_allowed_artifact(target, root) and not allow_dir:
        raise ArtifactAccessError(403, "该文件不在产物白名单内")
    return target


def read_preview_text(file: Path) -> tuple[str, bool]:
    """读取产物文本用于预览：返回 (text, truncated)。

    超过 _PREVIEW_MAX_BYTES 只读取头部并标记 truncated（前端提示）。二进制文件
    内容可能含乱码——由前端按 kind 判断（card/worldbook 都是 JSON）。
    """
    size = file.stat().st_size
    truncated = size > _PREVIEW_MAX_BYTES
    with file.open("r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(_PREVIEW_MAX_BYTES)
    return text, truncated


def reveal_in_folder(path: Path, *, select: bool = True) -> bool:
    """在系统资源管理器中定位文件（select=True 选中文件）或打开目录。

    仅本机 Windows（单机工具的宿主环境）；非 Windows 或失败返回 False，调用方降级。
    """
    import subprocess
    import sys

    if sys.platform != "win32":
        return False
    try:
        if select and path.is_file():
            # explorer /select 用逗号分隔参数，路径空格必须用引号包住
            subprocess.Popen(
                ["explorer", "/select,", f"{os.fspath(path)}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["explorer", os.fspath(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        return True
    except OSError:
        return False
