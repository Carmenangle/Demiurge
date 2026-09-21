"""仓库元信息：repo_id → 仓库名，输出文件夹按仓库名命名，改名时同步迁移。

文件夹名 = 用户对小仓库的命名（保留中文），文件夹内 _repo.json 记 {id, name}。
仓库名来源：前端存到后端的 data/user_state.json（见 routers/user_state.py）。
改名时重命名文件夹 + 重写快照/RAG/封面里含旧文件夹段的图片路径（否则图断）。
"""
from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from urllib.parse import quote

from app.config import DATA_DIR
from app.services.pathnames import safe_dir, safe_seg

_LOG = logging.getLogger("uvicorn.error")


def _load_state() -> dict:
    """读 user_state.json（repos + settings 单一属主）。缺失/损坏返回空 dict。

    ⚠ `OSError` 也在吞掉之列：P8（2026-09-15）把 `sync-worldbook` 的设置读取从路由内联
    （原写法是 `except (OSError, ValueError)` 回退空串）搬到本属主上——属主**不得比它替换掉的
    代码更不耐错**，否则读盘失败（权限 / 删除竞态）会从「400 未配置」退化成裸异常 500。
    """
    p = DATA_DIR / "user_state.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, OSError):
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

    **2026-09-15 用户定案「未配置 → 拒绝」**：原先未配置时**放行**（理由「功能未启用，各服务
    自行兜底」），但这与 `plans._trusted_output_dir` 的「未配置即拒绝」口径**相反**——同一道
    校验两个结果。现统一为拒绝，并返回**独立文案**（`WORKS_ROOT_NOT_CONFIGURED_MSG`）让前端
    据此弹窗、引导用户跳转「设置 → 路径与代理」补配置。
    返回 None=通过，否则返回中文错误信息供路由转 HTTPException。
    """
    truth = output_dir_from_state()
    if not truth:
        return WORKS_ROOT_NOT_CONFIGURED_MSG
    if output_dir and not _same_path(truth, output_dir):
        return WORKS_ROOT_MSG
    return None


# 作品根校验的两条固定文案（单一属主）：前端按「未配置」这条弹窗并跳转设置页，**勿改字面值**。
WORKS_ROOT_MSG = "output_dir 必须是当前配置的仓库文件夹根路径"
WORKS_ROOT_NOT_CONFIGURED_MSG = "尚未配置「仓库文件夹」，请先在「设置 → 路径与代理」里填写后再试。"


def _same_path(a: str, b: str) -> bool:
    """路径等价比较：归一斜杠/尾分隔符；Windows 大小写不敏感（避免正反斜杠误拒）。"""
    na = os.path.normpath(a or "").rstrip(os.sep) or os.sep
    nb = os.path.normpath(b or "").rstrip(os.sep) or os.sep
    if os.name == "nt":
        return na.casefold() == nb.casefold()
    return na == nb


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
    """读取后端已持久化的目录设置；编辑 Agent 发布源库时禁止采用请求传入路径。

    目录类设置的**唯一读入口**（`worldbookDir` 走 `sync-worldbook`、
    `characterDir`/`presetDir` 走发布源库等）。2026-09-15 P8：`sync-worldbook` 原先在路由里
    手写 `json.loads(DATA_DIR/"user_state.json")` 自读一份，已改回本函数。
    """
    settings = _load_state().get("settings")
    if isinstance(settings, dict):
        value = settings.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def setting_str_from_state(key: str) -> str:
    """读取 settings 中的字符串标量设置（`smitheryKey` 等非目录项的唯一读入口）。

    2026-09-17 T6.3：`routers/mcp._smithery_key` 与 `routers/skills._skey` 原先各自
    内联自读 `user_state.json`（逐字相同的两份真源），收敛到此。settings 缺失/
    非 dict/值非字符串一律回 ""；strip 由属主统一做（key 带尾随空白传 header 会 401）。
    """
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


_ENGINE_LEGACY_PREFIX = ".engine_legacy_"

# 引擎域落盘文件名**闭集**（与 tests/test_repo_engine_paths.py 的 12 处调用点表一一对应）。
# 唯一用途：判「某个目录里有没有引擎数据」。`chat.json` / 图片 / 文档 / `_repo.json` 是
# **非引擎产物**，不能算数——2026-09-20 凌霄仙母录实锤：旧实现只凭「新目录存在」就判
# 「已有数据」，把活引擎目录整体留档 ⇒ 活跃路径从此没有 tables.json/state.json ⇒
# UI 上表现为「数据表内容全部丢失」。
_ENGINE_FILE_NAMES = frozenset({
    "state.json", "tables.json", "table_config.json", "table_progress.json",
    "worldbook.json", "worldbook_session.json",
    "chronicle.db", "temporal_facts.db", "character_beliefs.db",
    "narrative_ci.db", "visual_preferences.db", "visual_ci.db",
})


def _has_engine_files(path: Path) -> bool:
    """该目录里是否已有引擎文件。**读不动时保守返回 True**（走「不覆盖」分支，宁留档不误伤）。"""
    try:
        return any(
            child.is_file() and child.name in _ENGINE_FILE_NAMES
            for child in path.iterdir()
        )
    except OSError:
        return True


def _adopt_engine_files(source: Path, target: Path) -> list[str]:
    """把 source 下的引擎文件**复制**进 target；已存在的一律跳过（绝不覆盖）。

    只搬引擎文件，不碰 chat/图/文档；source 保持原样（它是留档的底，可回滚）。
    返回实际复制的文件名列表。复制失败不阻断——引擎数据仍在原位置，下次还会再试。
    """
    if not source.is_dir():
        return []
    import shutil

    copied: list[str] = []
    for child in sorted(source.iterdir()):
        if not child.is_file() or child.name not in _ENGINE_FILE_NAMES:
            continue
        dst = target / child.name
        if dst.exists():
            continue
        try:
            shutil.copy2(child, dst)
        except OSError as exc:  # noqa: BLE001 [EXC-留痕]
            _LOG.warning("引擎文件并入失败（%s → %s）: %s", child, dst, exc)
            continue
        copied.append(child.name)
    return copied


def _legacy_engine_dir(root: Path, seg: str, repo_id: str) -> Path | None:
    """老口径引擎目录候选，找不到返回 None（不迁移）。

    子仓库历史上出现过**两种**布局，两种都要认：
    - `<根>/<uuid>`            —— 老口径通用式；
    - `<根>/<父名>/<uuid>`     —— 子仓库嵌父作品文件夹下（凌霄仙母录实测是这种）。
    """
    direct = root / seg
    if direct.is_dir():
        return direct
    parent_seg = parent_folder_seg(repo_id)
    if parent_seg:
        nested = root / parent_seg / seg
        if nested.is_dir():
            return nested
    return None


def engine_repo_dir(output_dir: str, repo_id: str) -> Path:
    """**引擎域唯一路径属主**（2026-09-18 ③）：state/tables/chronicle/事实/认知/世界观快照等
    「引擎私有落盘」的目录解析，与 chat/图/文档/画布/卡同轨（`folder_name` 仓库名文件夹）。

    历史错轨（本刀修）：引擎族一度用 `Path(base)/safe_seg(repo_id)`——`safe_seg` 把中文换成
    `_`，于是 UUID 仓库落 `<根>/<uuid>/`，而 chat/图/文档落 `<根>/<仓库名>/`，同一作品两套
    落点。实测 `凌霄仙母录` 的 state.json 因此分裂成两份（引擎处 turn=36 / 仓库名处 turn=21）。

    口径（与 `repo_folder_path` 一致，但**只算路径、不写 marker**，读路径零副作用）：
    - 有仓库名 → `<根>/<仓库名>`；子仓库 → `<根>/<父名>/<子名>`；
    - 无仓库名（home / 无记录）→ 退回 `safe_seg(repo_id)`，与老口径逐字节一致（零迁移）；
    - 无 base → 退回 `safe_seg(repo_id)`（不拼绝对/相对混淆路径）。

    惰性迁移（老口径目录见 `_legacy_engine_dir`，两种布局都认）：
    - 新位置**不存在** → 老目录整体 rename 过来；
    - 新位置存在但**没有引擎文件**（只有 chat/图/文档占位）→ **不算「已有数据」**：
      先把老目录（及已存在的留档目录）里的引擎文件**并入**活跃路径，再把老目录留档。
      ⚠ 2026-09-20 凌霄仙母录实锤：旧实现只看「新目录存在」，于是引擎目录被留档留到
      活跃路径看不见，UI 上表现为「数据表内容全部丢失」。
    - 新位置**已有引擎文件** → 绝不覆盖，老目录挪到 `<新位置>/.engine_legacy_<旧段>/` 留档。
    幂等：新位置已有引擎文件且老目录已让位 → 无操作；搬迁失败静默回退老位置（不丢数据）。

    ⚠ 本函数**只算/搬目录，不建目录**——建目录由各模块 `mkdir(parents=True)` 自己负责，
    保持与 `repo_folder_path` 相同的「读路径无副作用」契约（写路径才建）。
    """
    seg = safe_seg(repo_id, strip=False)
    if not (output_dir or "").strip() or not repo_id:
        return Path(seg)
    root = Path(output_dir)
    new_path = root / folder_name(repo_id)
    if new_path == root / seg:  # home / 纯 ASCII 名：老新同一条，零操作
        # ⚠ 红检 M12 实测：对本分支而言此短路**行为上冗余**——`folder_name` 在无仓库名时
        # 本就返回 `safe_seg(repo_id)`，故 `new_path == legacy` 恒成立；而老目录不存在时
        # 下方 `legacy.is_dir()` 已提前返回（不触发任何 mkdir/rename）。保留作**显式意图声明**
        # 与「folder_name 对 home 语义变更」的护栏，不依赖隐式巧合。
        return new_path
    legacy = _legacy_engine_dir(root, seg, repo_id)
    if legacy is None:
        return new_path
    try:
        if not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(new_path)
            return new_path
        keeper = new_path / f"{_ENGINE_LEGACY_PREFIX}{seg}"
        if not _has_engine_files(new_path):
            # 新位置只有非引擎产物 ⇒ 引擎文件必须先并入，否则活跃路径从此没有
            # tables.json/state.json。留档目录若已存在，它比受检老目录**更新**，优先并它。
            new_path.mkdir(parents=True, exist_ok=True)
            _adopt_engine_files(keeper, new_path)
            _adopt_engine_files(legacy, new_path)
        # 老目录留档（已留过则不重复搬）
        if not keeper.exists():
            new_path.mkdir(parents=True, exist_ok=True)
            legacy.rename(keeper)
    except OSError as exc:  # noqa: BLE001 [EXC-留痕] 搬迁失败不阻断，但引擎数据仍在老位置
        _LOG.warning(
            "引擎域目录迁移失败（数据保留在原位置 %s，读写仍走该处）: %s", legacy, exc)
    return new_path


def repo_scope_path(output_dir: str, repo_id: str) -> Path | None:
    """**产物存储域**解析（2026-09-10 B3/A）：repo_id 非空时把域从「作品库根」收窄到「作品文件夹」。

    口径（2026-09-10 用户定案）：作品域 = **仓库/小仓库文件夹**——顶层 `<作品库根>/<仓库名>`，
    子仓库嵌在父作品下 `<作品库根>/<父名>/<子名>`。docs/ 与 _versions/ 都落在这里，
    是产物的「存放文件夹」。

    与写端点用的 `repo_folder` 共用同一套解析（`folder_name` → 遗留标记目录 → 同名隔离），
    但**只算路径**：GET 类产物端点不能带建目录/写 marker 的副作用，故走 `repo_folder_path`。

    红线两条：
    1. 给了 repo_id 但作品文件夹尚不存在时**仍返回该路径**，绝不回退作品库根——回退会让新作品的
       产物卡显示别人的产物（跨作品串味），那正是本函数要修的问题（读端点靠"域不存在→空结果"
       自然退化，而不是靠回退）。
    2. repo_id 为空时返回 None（本函数**不做回退**，把决定权留给调用方）。产物 HTTP 端点已改为
       「缺值返回空结果」（2026-09-10 定案，见 `routers/artifacts.py._domains`：空值直接 `[]`），
       **不再**借这条 None 回退作品库根；写端点（需 base）仍按各自口径处理。
    """
    if not (output_dir and repo_id):
        return None
    return repo_folder_path(output_dir, repo_id)


def artifact_domains(output_dir: str, repo_id: str) -> list[Path]:
    """**产物访问/收集域集合**：作品域 ∪ 本作品拥有的卡目录（去重，按顺序）。

    为什么是集合而不是单根（2026-09-10 实证）：卡一律落 `<作品库根>/<卡名>/`
    （`character.upsert_repo` 的 base 注入是作品库根），而**卡目录名可以与作品文件夹名不同**
    ——实锤仓库「原创」（子仓库：`<作品库根>/Anima/原创/`）的卡在 `<作品库根>/御仙/`，
    归属靠该目录的 `_work.json`（= repo_id）判定。若只认作品域，这类作品的产物卡会在
    fabric done 与历史补卡里**整批消失**（`_collect_fabric_artifacts` 收的正是 `<卡名>/card.json`）。
    故访问域取并集：路径落在**任一**域内即放行，jail 仍是目录级（`../` 逃逸照旧被拒）。

    repo_id 为空 → 返回 `[作品库根]`（**低层原语语义**：给定 repo 的域集合；空 repo 视为"未收窄"）。
    **注意**：这是原语，**策略**由调用方决定——产物 HTTP 端点（`routers/artifacts.py._domains`）
    2026-09-10 用户定案「缺值返回空结果」，空 repo_id **不给任何域**（列表回空、单文件 403），
    **不再**走本函数的空分支。新增调用方时若要收窄语义，请在调用方短路，勿改本原语行为。
    """
    if not (output_dir or "").strip():
        return []
    base = Path(output_dir)
    if not repo_id:
        return [base]
    scoped = repo_scope_path(output_dir, repo_id)
    domains: list[Path] = [scoped] if scoped else [base]
    try:  # 归属判定单一属主在 character_store（读 _work.json / 卡名==仓库名）
        from app.services import character_store
        for folder in character_store.owned_card_dirs(output_dir, repo_id):
            if folder not in domains:
                domains.append(folder)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 归属探测失败不阻断产物端点，但产物域缺该作品自有卡目录
        _LOG.warning("作品自有卡目录归属探测失败（产物端点继续，但产物域缺该目录）: %s", exc)
    return domains


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
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 写标记失败不阻断，但文件夹归属缺失（改名迁移会失配）
        _LOG.warning("写 _repo.json 失败（继续，但文件夹归属标记缺失，改名/占用判定会失配）: %s", exc)


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
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] RAG 路径重写失败不阻断改名，但库内旧路径残留（断图）
        _LOG.warning("改名后 RAG 图片路径重写失败（改名继续，但库内旧路径残留会断图）: %s", exc)
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
