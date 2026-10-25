"""P8（2026-09-15）：`POST /api/artifacts/sync-worldbook` —— 事务下沉 service + 读设置走配置属主。

背景（第二轮架构审计 B4）：该端点在**路由内**跑完整事务 —— 手写
`json.loads(DATA_DIR / "user_state.json")` 读 `worldbookDir`（绕过属主
`repo_meta.setting_dir_from_state`）、`mkdir`、`copy2` 备份、`copy2` 覆盖、计数、错误映射，
还夹带 `json/shutil/time/DATA_DIR` 四处函数内局部导入。

本文件是**该端点的第一份路由层用例**（此前全仓 grep 零命中）。分三节：

- A. 行为特征（逐字保留）：400/404/500 文案、响应体 `{ok, dest, entries}`；
- B. 事务细节：备份命名 `<卡名>.json.bak-<int(time.time())>`、entries 计数口径、
  覆盖为**字节拷贝**、失败路径**不落盘**；
- C. 接线锁（P8 新增结构）：路由走属主 `worldbook_store.sync_card_worldbook`、
  读设置走属主 `repo_meta.setting_dir_from_state`、**反向源码锁**防回退成路由内联事务。

**硬约束**：用户可见文案 / 落盘格式 / 错误码一字不改（本文件逐字断言即是锁）。

⚠ **本文件的隔离是「两条读法都盖住」，不是常规样板**（2026-09-15 事故留档）：
首次运行本文件时 `_wire` 只 patch 了属主侧 `repo_meta._load_state`，而**当时**的实现在路由里
直读 `DATA_DIR/user_state.json` ⇒ 用例照着**真实**配置跑，把用户的真实资产库
`<userdata>/worlds/青梧与晚风.json`（123,028 字节 / 36 条）覆写成 15 字节假书，
只靠端点自带的 `.bak-<ts>` 备份才恢复回来。⇒ 任何给本端点写用例的人**必须**用 `_wire`
（它同时钉 `app.config.DATA_DIR` 与 `repo_meta._load_state`，指向同一个 tmp 状态桩，
回退到旧实现或前进到属主实现都不会碰真实盘）。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import config
from app.routers import artifacts as artifacts_router
from app.services import repo_meta

# 单卡作品：作品文件夹名 = 卡目录名（固化02 约定）⇒ 产物域去重后只有一个
_CARD = "青梧与晚风"
_REPO_ID = "r2"
_BAK_RE = re.compile(rf"^{_CARD}\.json\.bak-\d+$")
# tmp 工作区里允许出现的非产物项：卡目录 + 配置状态桩
_WORKSPACE = {_CARD, "_state"}


def _wire(monkeypatch, root: Path, *, wb_dir: str | None, repos: list[dict] | None = None) -> None:
    """把配置真源接到 tmp —— **两条读法都盖住**（属主读法 + 旧路由内联读法）。

    ⚠ 只钉 `app.config.DATA_DIR` 不够（`repo_meta` 是 `from app.config import DATA_DIR` 的
    **冻结绑定**，P3 已实测）；只钉 `repo_meta._load_state` 也不够（旧实现根本不走属主）。
    故这里 ① 写一份 tmp `user_state.json`、② 把两处 DATA_DIR 都指向它、
    ③ 再把属主 `_load_state` 也钉到同一份 payload —— 三条路径同源，无一条能漏到真实盘。
    """
    state_dir = root / "_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    settings: dict = {"outputDir": str(root)}
    if wb_dir is not None:
        settings["worldbookDir"] = wb_dir
    payload = {"repos": repos if repos is not None else [{"id": _REPO_ID, "name": _CARD}],
               "settings": settings}
    (state_dir / "user_state.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(config, "DATA_DIR", state_dir)        # 旧内联读法（函数内 import）
    monkeypatch.setattr(repo_meta, "DATA_DIR", state_dir)     # 属主模块冻结绑定
    monkeypatch.setattr(repo_meta, "_load_state", lambda: payload)


def _card_dir(root: Path, *, book: object = None, card: bool = False) -> Path:
    """建卡目录；`book` 给定时写 worldbook.json（None = 不写），`card` 给定时另写 card.json。"""
    d = root / _CARD
    d.mkdir(parents=True, exist_ok=True)
    if book is not None:
        (d / "worldbook.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    if card:
        (d / "card.json").write_text(json.dumps({"name": _CARD}, ensure_ascii=False), encoding="utf-8")
    return d


def _call(root: Path, path: Path, repo_id: str = _REPO_ID):
    return artifacts_router.artifact_sync_worldbook(
        artifacts_router.SyncWorldbookRequest(output_dir=str(root), path=str(path), repo_id=repo_id)
    )


def _body(response) -> dict:
    return json.loads(response.body)


def _extra(root: Path) -> set[str]:
    """工作区里除「卡目录 + 状态桩」之外多出来的东西（失败路径不该多出任何条目）。"""
    return {p.name for p in root.iterdir()} - _WORKSPACE


# ── A. 行为特征（逐字保留）────────────────────────────────────────────────────

def test_同步成功响应体与落盘内容(monkeypatch, tmp_path):
    """响应 `{ok, dest, entries}`；dest = <worldbookDir>/<卡名>.json，内容为**字节拷贝**。

    `dest` 是 wire 契约（前端 `api/artifacts.ts:syncWorldbook` 直接显示它）——
    `str(Path)` 形态、卡名不参与 `safe_dir` 清洗（与代码上游历史一致），都不得改。
    """
    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    card = _card_dir(tmp_path, book={"entries": {"0": {"comment": "a"}, "1": {"comment": "b"},
                                                 "2": {"comment": "c"}}})
    src = card / "worldbook.json"

    body = _body(_call(tmp_path, src))

    dst = wb_dir / f"{_CARD}.json"
    assert body == {"ok": True, "dest": str(dst), "entries": 3}
    assert dst.read_bytes() == src.read_bytes()          # 字节层面就是 copy2，不是重新序列化
    assert list(wb_dir.iterdir()) == [dst]               # 首次同步不产生备份


def test_entries为list形态同样计数(monkeypatch, tmp_path):
    """ST 世界书的 entries 两种形态都合法（dict / list），计数口径 = `len()`。"""
    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    src = _card_dir(tmp_path, book={"entries": [{"comment": "a"}, {"comment": "b"}]}) / "worldbook.json"

    assert _body(_call(tmp_path, src))["entries"] == 2


def test_旧版先备份再覆盖(monkeypatch, tmp_path):
    """重复同步：旧版原样留成 `<卡名>.json.bak-<int(time.time())>`，新内容覆盖正主。

    备份名是**用户可见落盘格式**（用户按它找回旧版），逐字保留。
    """
    wb_dir = tmp_path / "worlds"
    wb_dir.mkdir()
    old = wb_dir / f"{_CARD}.json"
    old.write_text(json.dumps({"entries": {"0": {"comment": "旧"}}}, ensure_ascii=False),
                   encoding="utf-8")
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    src = _card_dir(tmp_path, book={"entries": {"0": {"comment": "新"}}, "extra": 1}) / "worldbook.json"

    body = _body(_call(tmp_path, src))

    backups = [p for p in wb_dir.iterdir() if _BAK_RE.match(p.name)]
    assert len(backups) == 1, sorted(p.name for p in wb_dir.iterdir())
    assert json.loads(backups[0].read_text(encoding="utf-8"))["entries"]["0"]["comment"] == "旧"
    assert json.loads(old.read_text(encoding="utf-8"))["extra"] == 1     # 正主已是新内容
    assert body["dest"] == str(old)


def test_覆盖为字节拷贝不重新序列化(monkeypatch, tmp_path):
    """`copy2` 语义：源文件的**原始字节**（含缩进/键序/非 ASCII）原样落到目标。"""
    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    card = _card_dir(tmp_path, card=True)
    raw = '{"entries": {"0": {"comment": "中文·原样"}},   "spacing": [1,   2]}'
    (card / "worldbook.json").write_text(raw, encoding="utf-8")

    _call(tmp_path, card / "worldbook.json")

    assert (wb_dir / f"{_CARD}.json").read_bytes() == raw.encode("utf-8")


def test_未配置世界书目录时400且不落盘(monkeypatch, tmp_path):
    """未配置 `worldbookDir` → 400 逐字文案，且**一个文件都不建**（校验在事务之前）。"""
    _wire(monkeypatch, tmp_path, wb_dir=None)
    src = _card_dir(tmp_path, book={"entries": {}}) / "worldbook.json"

    with pytest.raises(HTTPException) as ei:
        _call(tmp_path, src)

    assert ei.value.status_code == 400
    assert ei.value.detail == "未配置世界书文件夹（worldbookDir），无法同步"
    assert _extra(tmp_path) == set()


def test_卡目录缺worldbook时404文案逐字(monkeypatch, tmp_path):
    """卡目录存在但没 worldbook.json → 404 且 detail 带**完整卡目录内绝对路径**。"""
    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    card = _card_dir(tmp_path, card=True)                 # 只有 card.json（用它过域校验）
    src = card / "worldbook.json"

    with pytest.raises(HTTPException) as ei:
        _call(tmp_path, card / "card.json")

    assert ei.value.status_code == 404
    assert ei.value.detail == f"卡目录缺少 worldbook.json：{src}"
    assert not wb_dir.exists()


def test_entries缺失时500文案逐字(monkeypatch, tmp_path):
    """世界书合法 JSON 但没有 entries → 500「同步失败：worldbook.json 缺少 entries」。"""
    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    src = _card_dir(tmp_path, book={"not_entries": 1}) / "worldbook.json"

    with pytest.raises(HTTPException) as ei:
        _call(tmp_path, src)

    assert ei.value.status_code == 500
    assert ei.value.detail == "同步失败：worldbook.json 缺少 entries"
    assert not wb_dir.exists()                            # 校验在 mkdir 之前 ⇒ 不落盘


def test_世界书不是对象时500前缀(monkeypatch, tmp_path):
    """顶层不是对象（ST 里合法 JSON 也可能写成数组）→ 走同一 500 兜底（文案前缀不变）。"""
    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    card = _card_dir(tmp_path, card=True)
    (card / "worldbook.json").write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(HTTPException) as ei:
        _call(tmp_path, card / "worldbook.json")

    assert ei.value.status_code == 500
    assert ei.value.detail.startswith("同步失败：")
    assert not wb_dir.exists()


def test_目标不可写时500前缀(monkeypatch, tmp_path):
    """落盘侧 OSError 也走同一 500 兜底（不把裸异常抛给 FastAPI 变成空体 500）。

    构造：worldbookDir 的父级被一个**普通文件**占住 ⇒ `mkdir(parents=True)` 抛 OSError。
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("not a dir", encoding="utf-8")
    _wire(monkeypatch, tmp_path, wb_dir=str(blocker / "sub"))
    src = _card_dir(tmp_path, book={"entries": {}}) / "worldbook.json"

    with pytest.raises(HTTPException) as ei:
        _call(tmp_path, src)

    assert ei.value.status_code == 500
    assert ei.value.detail.startswith("同步失败：")
    assert blocker.read_text(encoding="utf-8") == "not a dir"    # 拦截物未被破坏


def test_作品根不匹配时400拒绝(monkeypatch, tmp_path):
    """scope 门禁在事务之前：output_dir 不是配置的仓库文件夹 → 400（P15 定案口径）。"""
    other = tmp_path / "elsewhere"
    other.mkdir()
    _wire(monkeypatch, tmp_path, wb_dir=str(tmp_path / "worlds"))
    src = _card_dir(tmp_path, book={"entries": {}}) / "worldbook.json"

    with pytest.raises(HTTPException) as ei:
        artifacts_router.artifact_sync_worldbook(
            artifacts_router.SyncWorldbookRequest(
                output_dir=str(other), path=str(src), repo_id=_REPO_ID))

    assert ei.value.status_code == 400
    assert ei.value.detail == repo_meta.WORKS_ROOT_MSG


def test_缺repo_id时403不串味(monkeypatch, tmp_path):
    """不给 repo_id → 域集合为空（2026-09-10 定案「缺值返回空结果」）⇒ 403，不碰别作品文件。"""
    _wire(monkeypatch, tmp_path, wb_dir=str(tmp_path / "worlds"))
    src = _card_dir(tmp_path, book={"entries": {}}) / "worldbook.json"

    with pytest.raises(HTTPException) as ei:
        _call(tmp_path, src, repo_id="")

    assert ei.value.status_code == 403
    assert ei.value.detail == "产物路径超出本作品的产物域范围"


# ── C. 接线锁（P8 结构：事务属主 + 配置属主）──────────────────────────────────

def test_路由走属主service_返回值来自属主(monkeypatch, tmp_path):
    """路由**必须**把事务交给 `worldbook_store.sync_card_worldbook`，并原样回吐它的结果。

    锁法：把属主换成「记录入参 + 返回**哨兵结果**」的 spy —— 路由若自己拼 dest、自己数
    entries，就构造不出哨兵值。入参必须是**卡目录本身**（不是 path、不是作品库根）。
    """
    from app.services import worldbook_store

    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    src = _card_dir(tmp_path, book={"entries": {"0": {}}}) / "worldbook.json"

    seen: list[Path] = []
    sentinel = worldbook_store.WorldbookSyncResult(
        dest=Path("/__owner_sentinel__/x.json"), entries=4242, backup=None)

    def _spy(card_dir: Path) -> worldbook_store.WorldbookSyncResult:
        seen.append(card_dir)
        return sentinel

    monkeypatch.setattr(worldbook_store, "sync_card_worldbook", _spy)

    body = _body(_call(tmp_path, src))

    assert body == {"ok": True, "dest": str(sentinel.dest), "entries": 4242}
    assert seen == [src.parent]
    assert not wb_dir.exists()          # 路由自己不得偷偷落盘


def test_读世界书目录走repo_meta属主(monkeypatch, tmp_path):
    """`worldbookDir` 必须经 `repo_meta.setting_dir_from_state` 读（P8 核心修复点之一）。

    这是本项真正的「读设置单一属主」锁：P8 前路由自读 `DATA_DIR/user_state.json`
    ⇒ 属主函数**一次都不会被调用**（spy 零命中、本用例红），而且用例会照着真实配置
    把用户资产库当目标写。spy 同时断言 key 就是 `worldbookDir`（不是别名字段）。
    """
    wb_dir = tmp_path / "worlds"
    _wire(monkeypatch, tmp_path, wb_dir=str(wb_dir))
    src = _card_dir(tmp_path, book={"entries": {}}) / "worldbook.json"

    seen: list[str] = []
    real = repo_meta.setting_dir_from_state

    def _spy(key: str) -> str:
        seen.append(key)
        return real(key)

    monkeypatch.setattr(repo_meta, "setting_dir_from_state", _spy)

    body = _body(_call(tmp_path, src))

    assert seen == ["worldbookDir"]
    assert body["dest"] == str(wb_dir / f"{_CARD}.json")


def _code_only(path: Path) -> str:
    """源码剥掉 **docstring** 后剩下的文本（注释保留）。

    形态锁必须**只认代码**：本项目文档惯例是把「改前是什么样」写进 docstring
    （P8 的服务 docstring 就写着「原先直读 `DATA_DIR/"user_state.json"`」），
    裸子串断言会把这段正当说明误判成违规 —— 第一次跑就是因此变红。
    只剥 docstring、**保留其它字符串字面量**：要抓的违规本身正是
    `"user_state.json"` 这样的字面量，剥掉全部字符串等于把锁变成空断言。
    """
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    dropped: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        head = body[0]
        if (isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant)
                and isinstance(head.value.value, str)):
            dropped.update(range(head.lineno, (head.end_lineno or head.lineno) + 1))
    return "\n".join(text for idx, text in enumerate(lines, start=1) if idx not in dropped)


def test_路由与属主都不得自读user_state或自行拷贝():
    """**反向源码锁**：防「回退成路由内联事务」。

    P8 的收益全在这条上 —— 只要路由或属主再出现「自读 user_state.json / 自己 copy2」，
    设置与事务就又有了第二个属主；而**单测很容易漏掉它**（本次事故正是如此：行为在两条
    实现下一模一样，但旧实现会把真实资产库当目标写）。形态锁比行为锁更早报警。
    """
    from app.services import worldbook_store

    router_code = _code_only(Path(artifacts_router.__file__))
    store_code = _code_only(Path(worldbook_store.__file__))

    assert "user_state.json" not in router_code       # 设置读取不留在路由
    assert "json.loads" not in router_code            # 世界书解析不留在路由
    assert "shutil" not in router_code                # 备份/覆盖不留在路由
    assert "setting_dir_from_state" in store_code     # 属主经配置属主读设置
    assert "user_state.json" not in store_code        # 属主也不自读 state 文件


def test_空卡目录名400文案逐字(monkeypatch, tmp_path):
    """服务层防御分支：拿不到卡目录名 → 400 逐字文案。

    路由侧实际不可达（`_resolve_in` 已 `resolve()` 归一，`.name` 不会为空），但接缝是公开
    API，契约得直接钉住 —— 否则这行「防御」随时会被当成死代码删掉。
    """
    from app.services import worldbook_store

    _wire(monkeypatch, tmp_path, wb_dir=str(tmp_path / "worlds"))

    with pytest.raises(worldbook_store.WorldbookSyncError) as ei:
        worldbook_store.sync_card_worldbook(Path(tmp_path.anchor))   # 盘根：name 为空

    assert (ei.value.status, ei.value.detail) == (400, "无法从产物路径解析卡目录名")
