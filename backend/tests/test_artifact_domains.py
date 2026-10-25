"""B3/A（2026-09-10）：产物域 = 作品域（仓库 / 小仓库文件夹）∪ 本作品拥有的卡目录。

**为什么是集合而不是单根**（这是本轮最容易做错、也最容易回归的地方）：
卡一律落 `<作品库根>/<卡名>/`（`character.upsert_repo` 的 base 注入是作品库根），而
**卡目录名可以与作品文件夹名不同**——实锤仓库「原创」的卡在 `<作品库根>/驭云录/`，
归属只能靠该卡目录的 `_work.json`（= repo_id）判定。若把域收成单个「作品域」，
这类作品的产物卡会在 fabric done 与历史消息补卡里**整批消失**，版本快照也会漏掉它。

覆盖：
1. `repo_meta.artifact_domains`：作品域优先 + 并入**归属本作品**的卡目录（不误伤别人）；
2. `/list` 按 repo_id 隔离（旧行为：`since=0` 退回全作品库根 → 在 A 作品里刷出 B 的产物）；
3. 域外卡（卡名≠作品名）仍能列出 / 预览，别人的卡仍然 403；
4. 版本：存储域 = 作品域，域外卡按 `origin_dir` 入档并**原位**回档（不搬进作品域）；
5. 落点：`doc.create_repo` / `doc.attach_material` 写作品域，不再写全局单例 `docs/`。
6. **接线锁（2026-09-15 P4）**：三个薄包装（路由读端点 / 版本快照 / 产物卡收集）**都走属主**
   `repo_meta.artifact_domains`（spy 断言返回值来自属主 + 反向锁「不得自建域」）；
   三者刻意不同的空值短路与异常兜底**分别钉住**，防被顺手「统一」。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import artifacts as artifacts_router
from app.services import (
    artifact_versions,
    capability_handlers,
    character_store,
    collection_artifacts,
    repo_meta,
)

# 1x1 PNG 魔数（`attach_material` 走魔数校验，不需要合法像素数据）
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _state(*repos: dict) -> dict:
    return {"repos": list(repos)}


# 「原创」是 Anima 下的子仓库 → 作品域 = <库根>/Anima/原创（嵌套口径）
_原创 = ({"id": "p1", "name": "Anima"}, {"id": "r1", "name": "原创", "parentId": "p1"})


def _patch_repos(monkeypatch, *repos: dict) -> None:
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(*repos))


def _patch_works_root(monkeypatch, root) -> None:
    """把「仓库文件夹」真源配成 <root> ⇒ 各端点 scope 校验按「已配置且匹配」放行。

    2026-09-15 口径变更：属主 `repo_meta.works_root_violation` 由「未配置 → 放行」改为
    「未配置 → **拒绝**」（用户定案，前端弹窗引导去设置页）。原写法 `lambda: ""` 是拿
    「未配置」当绕行 scope 校验的手法，现在必须先显式配置——与真实已配置安装一致。
    """
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: str(root))


def _patch_unconfigured_works_root(monkeypatch) -> None:
    """「仓库文件夹」未配置：所有走 `works_root_violation` 的端点都必须 **400 拒绝**。"""
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")


# ── 1. 域解析 ──────────────────────────────────────────────────────────────

def test_域收窄到作品文件夹(monkeypatch, tmp_path):
    _patch_repos(monkeypatch, *_原创)
    (tmp_path / "Anima" / "原创").mkdir(parents=True)
    assert repo_meta.artifact_domains(str(tmp_path), "r1") == [tmp_path / "Anima" / "原创"]


def test_域并入本作品拥有的卡目录且不误伤别人(monkeypatch, tmp_path):
    """卡名≠作品名（固化03 实锤）：卡在 `<库根>/驭云录/`，作品文件夹是 `<库根>/Anima/原创/`。"""
    _patch_repos(monkeypatch, *_原创)
    (tmp_path / "Anima" / "原创").mkdir(parents=True)
    (tmp_path / "Anima" / "原创" / "chat.json").write_text("[]", encoding="utf-8")
    mine = tmp_path / "驭云录"
    mine.mkdir()
    (mine / "card.json").write_text('{"name":"驭云录"}', encoding="utf-8")
    character_store.write_work_marker(str(tmp_path), "驭云录", "r1")
    other = tmp_path / "青梧与晚风"
    other.mkdir()
    (other / "card.json").write_text('{"name":"青梧与晚风"}', encoding="utf-8")

    domains = repo_meta.artifact_domains(str(tmp_path), "r1")
    assert tmp_path / "Anima" / "原创" in domains      # 作品域（docs/_versions 的家）
    assert mine in domains                            # 归属本作品的卡目录
    assert other not in domains                       # 别人的卡目录绝不进来


def test_作品文件夹名与卡名相同时域不重复(monkeypatch, tmp_path):
    """卡名=作品名（固化02 约定）：作品域本身就是卡目录，去重后只有一个域。"""
    _patch_repos(monkeypatch, {"id": "r2", "name": "青梧与晚风"})
    (tmp_path / "青梧与晚风").mkdir(parents=True)
    (tmp_path / "青梧与晚风" / "card.json").write_text('{"name":"青梧与晚风"}', encoding="utf-8")
    assert repo_meta.artifact_domains(str(tmp_path), "r2") == [tmp_path / "青梧与晚风"]


def test_repo_id为空时路由层不给任何域(monkeypatch, tmp_path):
    """2026-09-10 用户定案「缺值返回空结果」：读端点不给 repo_id 时**不再**退化成
    「作品库根」单域（那等于全库可见 = 跨作品串味），而是没有任何可放行的域。

    注意两层语义分工：`repo_meta.artifact_domains` 是**低层原语**（"给定 repo 的域集合"，
    无 repo 仍是全库，供采集原语/单测直接用）；**产品策略落在路由层 `_domains`**。
    """
    _patch_repos(monkeypatch, *_原创)
    assert repo_meta.artifact_domains(str(tmp_path), "") == [tmp_path]   # 原语不变
    assert artifacts_router._domains(str(tmp_path), "") == []            # 路由：空集
    assert repo_meta.repo_scope_path(str(tmp_path), "") is None


def test_无repo_id时列表回空且单文件拒绝_带repo_id仍正常(monkeypatch, tmp_path):
    """缺省策略的端到端行为：list 空（不显示，不报错）、单文件 403（无可放行域）；
    带上 repo_id 一切照旧。"""
    _patch_repos(monkeypatch, {"id": "a1", "name": "甲"})
    _patch_works_root(monkeypatch, tmp_path)
    (tmp_path / "甲" / "docs").mkdir(parents=True)
    md = tmp_path / "甲" / "docs" / "设定总集.md"
    md.write_text("# 甲\n", encoding="utf-8")

    # 列表：空 items（不是 403，前端只是不渲染卡）
    assert artifacts_router.artifact_list(output_dir=str(tmp_path), repo_id="") == {
        "ok": True, "items": []}
    # 单文件：没有可放行的域 → 403
    with pytest.raises(HTTPException) as ei:
        artifacts_router.artifact_preview(output_dir=str(tmp_path), path=str(md), repo_id="")
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei2:
        artifacts_router.artifact_asset(output_dir=str(tmp_path), path=str(md), repo_id="")
    assert ei2.value.status_code == 403
    # 带上 repo_id：正常拿到本作品文档
    got = artifacts_router.artifact_preview(output_dir=str(tmp_path), path=str(md), repo_id="a1")
    assert got["ok"] is True and got["kind"] == "doc"
    items = artifacts_router.artifact_list(output_dir=str(tmp_path), repo_id="a1")["items"]
    assert [Path(i["path"]).name for i in items] == ["设定总集.md"]


def test_未配置仓库文件夹时产物端点一律400拒绝(monkeypatch, tmp_path):
    """2026-09-15 用户定案「未配置 → 拒绝」：仓库文件夹没配时**不再放行**各端点自行兜底，
    而是统一 400 + `WORKS_ROOT_NOT_CONFIGURED_MSG`（前端据此弹窗并跳转「设置 → 路径与代理」）。

    改前 `works_root_violation` 在未配置时放行，而 `plans._trusted_output_dir` 拒绝 ⇒ 同一道
    校验两种结果；本条把统一后的口径钉在属主与路由两层。
    """
    _patch_repos(monkeypatch, {"id": "a1", "name": "甲"})
    _patch_unconfigured_works_root(monkeypatch)
    (tmp_path / "甲" / "docs").mkdir(parents=True)
    md = tmp_path / "甲" / "docs" / "设定总集.md"
    md.write_text("# 甲\n", encoding="utf-8")

    # 属主层：返回「未配置」专文案（与「不匹配」区分），供前端弹窗识别
    assert repo_meta.works_root_violation(str(tmp_path)) == repo_meta.WORKS_ROOT_NOT_CONFIGURED_MSG
    # 路由层：读端点同样拒绝（原来 list 会回空 items、单文件 403，现在是明确 400）
    for call in (
        lambda: artifacts_router.artifact_list(output_dir=str(tmp_path), repo_id="a1"),
        lambda: artifacts_router.artifact_preview(output_dir=str(tmp_path), path=str(md), repo_id="a1"),
    ):
        with pytest.raises(HTTPException) as ei:
            call()
        assert ei.value.status_code == 400
        assert ei.value.detail == repo_meta.WORKS_ROOT_NOT_CONFIGURED_MSG


# ── 2. 收集：按 repo_id 隔离 ───────────────────────────────────────────────


def test_list按repo_id隔离不再串味(monkeypatch, tmp_path):
    """旧缺陷：`since=0`（历史消息补卡）退回「全作品库根最新一组」——A 作品的消息里
    会刷出 B 作品刚产出的产物。现在域按 repo_id 收窄，各看各的。"""
    _patch_repos(monkeypatch, {"id": "a1", "name": "甲"}, {"id": "b1", "name": "乙"})
    a_dir = tmp_path / "甲"
    (a_dir / "docs").mkdir(parents=True)
    (a_dir / "docs" / "设定总集.md").write_text("# 甲\n", encoding="utf-8")
    b_card = tmp_path / "乙" / "card.json"
    b_card.parent.mkdir(parents=True)
    b_card.write_text('{"name":"乙"}', encoding="utf-8")
    # 让 B 的产物「更新」，旧实现会因此把 B 的卡推给 A
    future = time.time() + 10
    import os
    os.utime(b_card, (future, future))

    a_items = collection_artifacts.collect_artifacts(str(tmp_path), repo_id="a1")
    assert [Path(i["path"]).name for i in a_items] == ["设定总集.md"]
    b_items = collection_artifacts.collect_artifacts(str(tmp_path), repo_id="b1")
    assert [Path(i["path"]).name for i in b_items] == ["card.json"]


def test_域外卡按归属仍被收集并可在预览端点放行(monkeypatch, tmp_path):
    """卡名≠作品名时，卡在作品域**外**的卡目录里——集合域必须让它照常可见可预览；
    别人的卡仍然 403。"""
    _patch_repos(monkeypatch, *_原创)
    _patch_works_root(monkeypatch, tmp_path)
    (tmp_path / "Anima" / "原创").mkdir(parents=True)
    mine = tmp_path / "驭云录"
    mine.mkdir()
    (mine / "card.json").write_text('{"name":"驭云录"}', encoding="utf-8")
    character_store.write_work_marker(str(tmp_path), "驭云录", "r1")
    other = tmp_path / "青梧与晚风"
    other.mkdir()
    (other / "card.json").write_text('{"name":"青梧与晚风"}', encoding="utf-8")

    items = collection_artifacts.collect_artifacts(str(tmp_path), repo_id="r1")
    assert [Path(i["path"]) for i in items] == [mine / "card.json"]

    got = artifacts_router.artifact_preview(
        output_dir=str(tmp_path), path=str(mine / "card.json"), repo_id="r1")
    assert got["ok"] is True and got["kind"] == "card"
    assert "驭云录" in got["text"]

    with pytest.raises(HTTPException) as ei:
        artifacts_router.artifact_preview(
            output_dir=str(tmp_path), path=str(other / "card.json"), repo_id="r1")
    assert ei.value.status_code == 403
    assert "产物域" in ei.value.detail


# ── 3. 版本：存储域 = 作品域，域外卡原位回档 ────────────────────────────────

def test_版本快照落作品域且域外卡原位回档(monkeypatch, tmp_path):
    _patch_repos(monkeypatch, *_原创)
    storage = tmp_path / "Anima" / "原创"
    storage.mkdir(parents=True)
    card_dir = tmp_path / "驭云录"
    card_dir.mkdir()
    (card_dir / "card.json").write_text('{"name":"驭云录"}', encoding="utf-8")
    character_store.write_work_marker(str(tmp_path), "驭云录", "r1")

    res = artifact_versions.snapshot_current(
        str(tmp_path), trigger="done", summary="一次交付", repo_id="r1")
    assert res["saved"] is True
    meta = res["version"]
    entry = meta["files"][0]
    assert entry["rel"] == "驭云录/card.json"
    assert entry["origin_dir"] == str(card_dir)
    # 版本库落作品域，**不**落作品库根（旧行为是库根下的全局单例）
    assert (storage / "_versions" / meta["version_id"] / "驭云录" / "card.json").is_file()
    assert not (tmp_path / "_versions").exists()

    (card_dir / "card.json").write_text('{"name":"改坏了"}', encoding="utf-8")
    out = artifact_versions.restore_version(str(tmp_path), meta["version_id"], repo_id="r1")
    assert out["restored"] == [str(card_dir / "card.json")]   # 原位，不搬进作品域
    assert json.loads((card_dir / "card.json").read_text(encoding="utf-8"))["name"] == "驭云录"
    assert not (storage / "驭云录").exists()
    # 回档前的自动存档同样落作品域
    assert len(artifact_versions.list_versions(str(tmp_path), repo_id="r1")) == 2


def test_版本列表的活文件路径域内取存储域域外取origin(monkeypatch, tmp_path):
    _patch_repos(monkeypatch, *_原创)
    _patch_works_root(monkeypatch, tmp_path)
    storage = tmp_path / "Anima" / "原创"
    storage.mkdir(parents=True)
    card_dir = tmp_path / "驭云录"
    card_dir.mkdir()
    (card_dir / "card.json").write_text('{"name":"驭云录"}', encoding="utf-8")
    character_store.write_work_marker(str(tmp_path), "驭云录", "r1")
    vid = artifact_versions.snapshot_current(
        str(tmp_path), trigger="done", repo_id="r1")["version"]["version_id"]

    got = artifacts_router.artifact_versions_list(output_dir=str(tmp_path), repo_id="r1")
    entry = got["versions"][0]["files"][0]
    assert Path(entry["path"]) == card_dir / "card.json"
    # 版本副本预览走的是版本目录里的副本（B5），rel 用 meta 里的 `驭云录/card.json`
    body = artifacts_router.artifact_version_file(
        output_dir=str(tmp_path), version_id=vid, rel=entry["rel"], repo_id="r1")
    assert body["ok"] is True and body["kind"] == "card"


def test_两个作品的版本库互不可见(monkeypatch, tmp_path):
    """旧行为：版本库是作品库根下的全局单例 → A 的回档列表里能刷出 B 的版本。"""
    _patch_repos(monkeypatch, {"id": "a1", "name": "甲"}, {"id": "b1", "name": "乙"})
    for repo_id, name in (("a1", "甲"), ("b1", "乙")):
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / "card.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        artifact_versions.snapshot_current(str(tmp_path), trigger="done", repo_id=repo_id)
    assert len(artifact_versions.list_versions(str(tmp_path), repo_id="a1")) == 1
    assert len(artifact_versions.list_versions(str(tmp_path), repo_id="b1")) == 1
    assert not (tmp_path / "_versions").exists()


# ── 4. 落点：docs 不再全局单例 ────────────────────────────────────────────

def test_建文档落作品域而不是作品库根(monkeypatch, tmp_path):
    _patch_repos(monkeypatch, *_原创)
    res = capability_handlers.create_repo_doc(
        base=str(tmp_path), rel_path="设定总集.md", content="# 设定\n", repo_id="r1")
    target = Path(res["path"])
    assert target == tmp_path / "Anima" / "原创" / "docs" / "设定总集.md"
    assert target.is_file()
    assert not (tmp_path / "docs").exists()      # 全局单例不再产生


def test_无repo_id时建文档保持旧落点(monkeypatch, tmp_path):
    """兼容路径：没透传 repo_id 时落作品库根 docs/（老调用行为不变）。"""
    _patch_repos(monkeypatch, *_原创)
    res = capability_handlers.create_repo_doc(
        base=str(tmp_path), rel_path="旧文档.md", content="# x\n")
    assert Path(res["path"]) == tmp_path / "docs" / "旧文档.md"


def test_插图素材落作品域的docs_assets(monkeypatch, tmp_path):
    _patch_repos(monkeypatch, *_原创)
    (tmp_path / "Anima" / "原创").mkdir(parents=True)
    (tmp_path / "_web_materials").mkdir()
    src = tmp_path / "_web_materials" / "m1.png"
    src.write_bytes(_PNG)

    res = capability_handlers.attach_material(
        base=str(tmp_path), src=str(src), doc_rel="设定总集.md", repo_id="r1")
    assert res["rel"] == "assets/m1.png"
    assert res["markdown"] == "![m1](assets/m1.png)"
    assert (tmp_path / "Anima" / "原创" / "docs" / "assets" / "m1.png").is_file()
    assert not (tmp_path / "docs").exists()


def test_插图素材仍拒绝作品库外的文件(monkeypatch, tmp_path):
    """落点收窄不放松来源校验：src 仍必须在**作品库根**内（素材由 web.save_material 落库根）。"""
    _patch_repos(monkeypatch, *_原创)
    (tmp_path / "Anima" / "原创").mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    stray = outside / "外面.png"
    stray.write_bytes(_PNG)
    with pytest.raises(ValueError) as ei:
        capability_handlers.attach_material(
            base=str(tmp_path), src=str(stray), repo_id="r1")
    assert "作品目录内" in str(ei.value)


# ── 5. 接线锁：三个包装都必须走属主 `repo_meta.artifact_domains`（P4） ────────
#
# 背景：产物域集合是**跨作品串味**（B3/A 红线）的唯一防线，属主只有一个
# （`repo_meta.artifact_domains`），另有三处薄包装：路由读端点 / 版本快照 / 产物卡收集。
# 包装若被改回「就地自建域」（自己拼 `repo_scope_path` + `owned_card_dirs`），
# 口径会分叉且**运行期不报错**（A 作品里刷出 B 的产物、快照漏收）——故照 C5/C6 的现成手法
# 加 monkeypatch spy：断言三处都真调属主。三个包装都不在模块顶层 import 属主
# （函数内 `from app.services import repo_meta`，拿到的是同一模块对象）⇒ patch 属主一处即覆盖三处。

_OWNER_SENTINEL = [Path("/__owner_sentinel__")]


class _OwnerSpy:
    """把属主 `repo_meta.artifact_domains` 换成「记录入参 + 返回**哨兵域**」的 spy。

    返回哨兵而非真实域是关键：断言的是「包装的返回值**就是属主给的**」——
    就地自建域的实现拿不到这个哨兵，于是「改回自建域」必然变红。
    """

    def __init__(self, monkeypatch) -> None:
        self.seen: list[tuple[str, str]] = []

        def spy(output_dir: str, repo_id: str) -> list[Path]:
            self.seen.append((output_dir, repo_id))
            return list(_OWNER_SENTINEL)

        monkeypatch.setattr(repo_meta, "artifact_domains", spy)


def test_三个包装都走属主artifact_domains(monkeypatch, tmp_path):
    """P4 接线锁（正向）：三处包装的返回值必须来自属主，入参原样透传、无遗漏。"""
    spy = _OwnerSpy(monkeypatch)
    root = str(tmp_path)

    assert artifacts_router._domains(root, "r1") == _OWNER_SENTINEL          # 路由读端点
    assert artifact_versions._domains(root, "r1") == _OWNER_SENTINEL         # 版本快照
    assert collection_artifacts._domains_of(root, "r1") == _OWNER_SENTINEL   # 产物卡收集

    assert spy.seen == [(root, "r1")] * 3


def test_三个包装不得自建域绕过属主(monkeypatch, tmp_path):
    """P4 接线锁（反向）：属主之外**不得**再自己枚举卡目录拼域集合。

    收口前的写法就是「`repo_scope_path` + `owned_card_dirs`」就地拼；若有人改回去，
    这里 patch 的 `owned_card_dirs` spy 必被命中 ⇒ 本用例变红（正向用例只比对返回值，挡不住「同样调属主、
    但另外又自己拼一份」这类回退）。

    ⚠ 属主 `repo_meta.artifact_domains` **内部本来就会**调 `owned_card_dirs`（`repo_meta.py:189`），
    所以必须先把属主顶成 spy，这里的命中才只可能来自「包装自己绕过属主」。
    """
    _OwnerSpy(monkeypatch)
    touched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        character_store, "owned_card_dirs",
        lambda base, repo_id: touched.append((base, repo_id)) or [],
    )
    root = str(tmp_path)

    artifacts_router._domains(root, "r1")
    artifact_versions._domains(root, "r1")
    collection_artifacts._domains_of(root, "r1")

    assert touched == []          # 没有任何一处绕过属主自己去枚举卡目录


def test_包装的空值短路差异被分别钉住(monkeypatch, tmp_path):
    """三处包装的**空值策略刻意不同**（各自注释都有明文定案），别顺手「统一」：

    - 路由 `_domains(root, "")` → `[]`：产品策略「缺值不给任何域」（防跨作品串味，2026-09-10 定案）；
    - 版本 `_domains("", repo_id)` → `[]`：空 output_dir 直接空，不进属主；
    - 收集 `_domains_of(root, "")` → `[root]`：**低层原语**，无 repo 仍是全库（供采产区直接用）。

    三者都**不**落到属主 —— 属主对这些入参另有语义（无 repo 返回 `[作品库根]`）。
    """
    spy = _OwnerSpy(monkeypatch)
    root = str(tmp_path)

    assert artifacts_router._domains(root, "") == []
    assert artifact_versions._domains("", "r1") == []
    assert collection_artifacts._domains_of(root, "") == [Path(root)]

    assert spy.seen == []


def test_包装的异常兜底差异被分别钉住(monkeypatch, tmp_path):
    """属主抛错时的兜底**刻意不同**（兜底有无本身就是隔离强度的一部分）：

    - 版本 / 收集包装有兜底 → 退化为单域（注释明文：不该让快照或产物卡整体失败）；
    - 路由 `_domains` **无**兜底 → 异常上抛（**现状特征**：域解析失败绝不会被当成
      「只有作品库根」放行——那等于放宽隔离）。若要给它加兜底，必须同时评估这一点。
    """
    root = str(tmp_path)

    def boom(output_dir: str, repo_id: str) -> list[Path]:
        raise RuntimeError("域解析炸了")

    monkeypatch.setattr(repo_meta, "artifact_domains", boom)

    assert artifact_versions._domains(root, "r1") == [Path(root)]
    assert collection_artifacts._domains_of(root, "r1") == [Path(root)]
    with pytest.raises(RuntimeError):
        artifacts_router._domains(root, "r1")
