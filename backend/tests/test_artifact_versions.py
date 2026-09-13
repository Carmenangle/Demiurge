"""产物版本快照测试：自动存档 / 去重 / 列表 / 回档（含预存档）/ 删除 / 路径安全。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import artifact_versions as av
from app.services import collection_artifacts


@pytest.fixture()
def sample_works(tmp_path: Path) -> Path:
    """作品根：<根>/玫瑰与繁花/card.json + worldbook.json（模拟刚落盘的合集卡）。"""
    root = tmp_path / "works"
    work = root / "玫瑰与繁花"
    work.mkdir(parents=True)
    (work / "card.json").write_text(
        json.dumps({"name": "玫瑰与繁花", "character_book": {"entries": [{"key": "机制"}]}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (work / "worldbook.json").write_text(
        json.dumps({"entries": [{"key": "机制", "comment": "世界书条目"}]},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return root


def _snapshot(root: Path, trigger: str = "done", summary: str = "") -> dict:
    return av.snapshot_current(str(root), trigger=trigger, summary=summary)


class TestSnapshot:
    def test_首次快照创建版本目录与meta(self, sample_works: Path):
        res = _snapshot(sample_works, summary="《玫瑰与繁花》合集卡已完成")
        assert res["saved"] is True
        meta = res["version"]
        assert meta["seq"] == 1
        assert meta["summary"].startswith("《玫瑰与繁花》")
        assert len(meta["files"]) == 2
        names = {f["rel"].rsplit("/", 1)[-1] for f in meta["files"]}
        assert names == {"card.json", "worldbook.json"}
        vdir = sample_works / "_versions" / meta["version_id"]
        assert vdir.is_dir()
        # 2026-09-10 起版本内按相对作品根的结构落盘（避免不同目录同名文件互相覆盖）
        assert (vdir / "玫瑰与繁花" / "card.json").is_file()
        assert (vdir / "玫瑰与繁花" / "worldbook.json").is_file()
        assert (vdir / "meta.json").is_file()

    def test_内容未变再次快照去重(self, sample_works: Path):
        _snapshot(sample_works)
        res = _snapshot(sample_works)
        assert res["saved"] is False and res["reason"] == "unchanged"
        assert len(list((sample_works / "_versions").iterdir())) == 1

    def test_内容变化后生成新版本且序号递增(self, sample_works: Path):
        _snapshot(sample_works)
        card = sample_works / "玫瑰与繁花" / "card.json"
        card.write_text(card.read_text(encoding="utf-8") + "\n// 补写内容", encoding="utf-8")
        res = _snapshot(sample_works)
        assert res["saved"] is True
        assert res["version"]["seq"] == 2
        assert len(list((sample_works / "_versions").iterdir())) == 2

    def test_无产物返回no_artifacts(self, tmp_path: Path):
        root = tmp_path / "empty"
        root.mkdir()
        assert _snapshot(root)["reason"] == "no-artifacts"
        assert _snapshot(str(tmp_path / "nope"))["reason"] == "no-artifacts"

    def test_版本目录名与meta内容可被读取(self, sample_works: Path):
        res = _snapshot(sample_works, trigger="done", summary="完成")
        versions = av.list_versions(str(sample_works))
        assert len(versions) == 1
        assert versions[0]["version_id"] == res["version"]["version_id"]
        assert versions[0]["trigger"] == "done"
        assert versions[0]["total_size"] > 0


class TestList:
    def test_空作品返回空列表(self, tmp_path: Path):
        root = tmp_path / "w"
        root.mkdir()
        assert av.list_versions(str(root)) == []
        assert av.list_versions(str(tmp_path / "nope")) == []

    def test_多个版本按新到旧排序(self, sample_works: Path):
        _snapshot(sample_works, summary="v1")
        card = sample_works / "玫瑰与繁花" / "card.json"
        card.write_text("{}", encoding="utf-8")
        _snapshot(sample_works, summary="v2")
        wb = sample_works / "玫瑰与繁花" / "worldbook.json"
        wb.write_text('{"entries":[]}', encoding="utf-8")
        _snapshot(sample_works, summary="v3")
        versions = av.list_versions(str(sample_works))
        assert [v["seq"] for v in versions] == [3, 2, 1]
        assert [v["summary"] for v in versions] == ["v3", "v2", "v1"]

    def test_损坏版本目录被跳过(self, sample_works: Path):
        _snapshot(sample_works)
        bad = sample_works / "_versions" / "bad-dir"
        bad.mkdir()
        (bad / "card.json").write_text("{}", encoding="utf-8")
        assert len(av.list_versions(str(sample_works))) == 1


class TestRestore:
    def test_回档恢复内容且先自动存档当前状态(self, sample_works: Path):
        _snapshot(sample_works, summary="v1")
        good_card = (sample_works / "玫瑰与繁花" / "card.json").read_text(encoding="utf-8")
        # 改坏当前产物（模拟一次不满意的手工修改/落盘）
        (sample_works / "玫瑰与繁花" / "card.json").write_text(
            '{"broken": true}', encoding="utf-8")
        versions = av.list_versions(str(sample_works))
        v1 = versions[-1]  # seq=1（旧版）
        res = av.restore_version(str(sample_works), v1["version_id"])
        assert len(res["restored"]) == 2
        assert (sample_works / "玫瑰与繁花" / "card.json").read_text(encoding="utf-8") == good_card
        # 回档前版本被自动存档为 pre_restore（可逆，不破坏现状）
        after = av.list_versions(str(sample_works))
        assert any(v["trigger"] == "pre_restore" for v in after)
        assert after[0]["seq"] > v1["seq"]

    def test_回档到不存在的版本抛404(self, sample_works: Path):
        with pytest.raises(av.VersionError) as ei:
            av.restore_version(str(sample_works), "999-20990101000000")
        assert ei.value.status == 404


class TestDelete:
    def test_删除版本后列表减少(self, sample_works: Path):
        _snapshot(sample_works, summary="keep")
        card = sample_works / "玫瑰与繁花" / "card.json"
        card.write_text("{}", encoding="utf-8")
        _snapshot(sample_works, summary="remove")
        versions = av.list_versions(str(sample_works))
        target = [v for v in versions if v["summary"] == "remove"][0]
        assert av.delete_version(str(sample_works), target["version_id"]) is True
        left = [v["summary"] for v in av.list_versions(str(sample_works))]
        assert left == ["keep"]
        # 删除不存在的版本
        with pytest.raises(av.VersionError) as ei:
            av.delete_version(str(sample_works), "999-20990101000000")
        assert ei.value.status == 404

    def test_非法版本号被拒(self, sample_works: Path):
        for bad in ("../x", "abc", "001-20990101", ""):
            with pytest.raises(av.VersionError):
                av.delete_version(str(sample_works), bad)


class TestResolveVersionFile:
    def test_正常解析与越界拒绝(self, sample_works: Path):
        res = _snapshot(sample_works)
        vdir = sample_works / "_versions" / res["version"]["version_id"]
        # 按 rel 解析（与 meta 同形）
        p = av.resolve_version_file(sample_works, res["version"]["version_id"], "玫瑰与繁花/card.json")
        assert p == (vdir / "玫瑰与繁花" / "card.json").resolve()
        # 兼容旧版平铺：裸文件名按 meta 的 name 兜底匹配
        p2 = av.resolve_version_file(sample_works, res["version"]["version_id"], "card.json")
        assert p2 == p
        with pytest.raises(av.VersionError):
            av.resolve_version_file(sample_works, res["version"]["version_id"], "notes.txt")
        with pytest.raises(av.VersionError) as ei:
            av.resolve_version_file(sample_works, "../evil", "card.json")
        assert ei.value.status == 400


class TestRouter:
    """新版本端点走真实路由函数（scope 放行同现有端点：monkeypatch output_dir_from_state）。"""

    def test_versions_列表补绝对路径(self, sample_works: Path, monkeypatch):
        from app.services import repo_meta as _rm
        monkeypatch.setattr(_rm, "output_dir_from_state", lambda: "")
        av.snapshot_current(str(sample_works), trigger="done", summary="完成")
        from app.routers.artifacts import artifact_versions_list
        result = artifact_versions_list(output_dir=str(sample_works), repo_id="")
        assert result["ok"] is True
        assert len(result["versions"]) == 1
        files = result["versions"][0]["files"]
        assert files and all(f["path"] for f in files)
        assert Path(files[0]["path"]).is_file()

    def test_versions_file_读的是版本副本而非当前文件(self, sample_works: Path, monkeypatch):
        """B5（2026-09-10）：版本历史「查看」必须读**版本目录里的副本**。

        旧缺陷：前端拿 `/versions` 附带的 `files[].path`（= 作品根 + rel 的**当前**路径）
        走 `/preview`，于是回档前查看旧版本看到的是**现在的**文件内容，版本历史形同摆设。
        """
        from app.services import repo_meta as _rm
        monkeypatch.setattr(_rm, "output_dir_from_state", lambda: "")
        res = av.snapshot_current(str(sample_works), trigger="done", summary="v1")
        vid = res["version"]["version_id"]
        # 快照后再改当前文件：版本副本仍应保持快照时的内容
        (sample_works / "玫瑰与繁花" / "card.json").write_text('{"cur":2}', encoding="utf-8")
        from app.routers.artifacts import artifact_version_file
        got = artifact_version_file(output_dir=str(sample_works), version_id=vid,
                                    rel="玫瑰与繁花/card.json", repo_id="")
        assert got["ok"] is True and got["kind"] == "card" and got["name"] == "card.json"
        assert json.loads(got["text"])["name"] == "玫瑰与繁花"   # ← 副本内容，不是 {"cur":2}
        assert "cur" not in got["text"]
        # meta.json 未声明的文件路径 → 404（不接受任意路径）
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            artifact_version_file(output_dir=str(sample_works), version_id=vid, rel="nope.md",
                                  repo_id="")
        assert ei.value.status_code == 404
        # 非法版本号 → 400
        with pytest.raises(HTTPException) as ei2:
            artifact_version_file(output_dir=str(sample_works), version_id="../evil",
                                  rel="玫瑰与繁花/card.json", repo_id="")
        assert ei2.value.status_code == 400

    def test_restore_与_delete_端点真实路径(self, sample_works: Path, monkeypatch):
        from app.services import repo_meta as _rm
        monkeypatch.setattr(_rm, "output_dir_from_state", lambda: "")
        res = av.snapshot_current(str(sample_works), trigger="done", summary="v1")
        vid = res["version"]["version_id"]
        from app.routers.artifacts import artifact_version_restore, artifact_version_delete
        (sample_works / "玫瑰与繁花" / "card.json").write_text('{"broken":1}', encoding="utf-8")
        from app.routers.artifacts import VersionRequest
        r1 = artifact_version_restore(VersionRequest(
            output_dir=str(sample_works), version_id=vid))
        assert r1.body is not None
        data = json.loads(r1.body)
        assert data["ok"] is True and len(data["restored"]) == 2
        r2 = artifact_version_delete(VersionRequest(
            output_dir=str(sample_works), version_id=vid))
        assert json.loads(r2.body)["ok"] is True


class TestDocArtifactsInVersions:
    """2026-09-10 链路③ 回归：产物白名单加了 docs/*.md，版本快照必须同步——
    此前复用 _ALLOWED_NAMES（只有 card/worldbook）导致「文档有卡片却无版本/不可回档」。"""

    @staticmethod
    def _docs_only(tmp_path: Path) -> Path:
        root = tmp_path / "works"
        docs = root / "docs"
        docs.mkdir(parents=True)
        (docs / "设定总集.md").write_text("# 设定总集\n\nv1\n", encoding="utf-8")
        return root

    def test_纯文档交付也能生成版本(self, tmp_path: Path):
        """关键回归：改前 docs-only 会走完复制循环但一份都收不到 → 空版本目录被删 → no-artifacts。"""
        root = self._docs_only(tmp_path)
        res = _snapshot(root, summary="整理成文档")
        assert res["saved"] is True, f"纯文档交付应能存档，实际 {res}"
        rels = [f["rel"] for f in res["version"]["files"]]
        assert rels == ["docs/设定总集.md"]
        vdir = root / "_versions" / res["version"]["version_id"]
        assert (vdir / "docs" / "设定总集.md").is_file()

    def test_文档变更产生新版本而非被误判未变(self, tmp_path: Path):
        """改前：卡未变时文档变更会被「unchanged」短路（或产生内容全同的重复卡快照）。"""
        root = self._docs_only(tmp_path)
        v1 = _snapshot(root)
        assert v1["saved"] is True
        (root / "docs" / "设定总集.md").write_text("# 设定总集\n\nv2\n", encoding="utf-8")
        v2 = _snapshot(root)
        assert v2["saved"] is True and v2["version"]["seq"] == 2

    def test_文档可回档(self, tmp_path: Path):
        root = self._docs_only(tmp_path)
        doc = root / "docs" / "设定总集.md"
        good = doc.read_text(encoding="utf-8")
        v1 = _snapshot(root)
        doc.write_text("# 被改坏\n", encoding="utf-8")
        res = av.restore_version(str(root), v1["version"]["version_id"])
        assert res["restored"] == [str(doc)]
        assert doc.read_text(encoding="utf-8") == good

    def test_卡与文档并存时都会被存档(self, tmp_path: Path):
        root = self._docs_only(tmp_path)
        work = root / "玫瑰与繁花"
        work.mkdir()
        (work / "card.json").write_text('{"name":"玫瑰与繁花"}', encoding="utf-8")
        res = _snapshot(root)
        assert res["saved"] is True
        rels = sorted(f["rel"] for f in res["version"]["files"])
        assert rels == ["docs/设定总集.md", "玫瑰与繁花/card.json"]

    def test_版本内文档可按rel解析(self, tmp_path: Path):
        root = self._docs_only(tmp_path)
        res = _snapshot(root)
        vid = res["version"]["version_id"]
        p = av.resolve_version_file(root, vid, "docs/设定总集.md")
        assert p == (root / "_versions" / vid / "docs" / "设定总集.md").resolve()

    def test_未归一的作品根也能解析版本内文件(self, tmp_path: Path):
        """2026-09-10 真实代码抽查发现的陷阱：root 未归一（8.3 短名、含 `..` 的别名路径）
        会让 `live.is_relative_to(root)` 假失败，报出误导性的 403「不在产物白名单内」。
        入口必须与 `resolve_artifact`/`list_versions` 同约定先 `resolve()`。"""
        root = self._docs_only(tmp_path)
        res = _snapshot(root)
        vid = res["version"]["version_id"]
        alias = Path(str(root)) / ".." / root.name   # 同一目录，但字面路径未归一
        assert alias != root or str(alias) != str(root)
        p = av.resolve_version_file(alias, vid, "docs/设定总集.md")
        assert p == (root / "_versions" / vid / "docs" / "设定总集.md").resolve()

    def test_旧版平铺快照仍可回档(self, tmp_path: Path):
        """兼容性回归：2026-09-10 之前的版本目录是平铺存放（版本根下直接 card.json），
        新代码必须仍能回档它们，不能让用户既有版本历史失效。"""
        root = tmp_path / "works"
        work = root / "玫瑰与繁花"
        work.mkdir(parents=True)
        (work / "card.json").write_text('{"cur":1}', encoding="utf-8")
        # 手工造一个旧式（平铺）版本目录
        vdir = root / "_versions" / "001-20260909103000"
        vdir.mkdir(parents=True)
        (vdir / "card.json").write_text('{"old":1}', encoding="utf-8")
        (vdir / "meta.json").write_text(json.dumps({
            "version_id": vdir.name, "seq": 1, "ts": "2026-09-09T10:30:00",
            "trigger": "done", "summary": "旧版平铺存档",
            "files": [{"rel": "玫瑰与繁花/card.json", "name": "card.json",
                       "size": 9, "mtime": 0}],
            "total_size": 9,
        }, ensure_ascii=False), encoding="utf-8")
        assert av.list_versions(str(root))[0]["version_id"] == vdir.name
        res = av.restore_version(str(root), vdir.name)
        assert res["restored"] == [str(work / "card.json")]
        assert (work / "card.json").read_text(encoding="utf-8") == '{"old":1}'


class TestCollectExcludesVersions:
    def test_versions目录不被当作产物(self, sample_works: Path):
        """2026-09-09 关键回归：版本快照目录含 card.json/worldbook.json，
        collect_artifacts 若扫到它会把「最新版本」当成「最新产物」。"""
        # 先造一个真实作品产物（mtime 最新），再造更晚的 _versions 伪目录
        real = sample_works / "玫瑰与繁花"
        (real / "card.json").touch()
        vdir = sample_works / "_versions" / "001-20990101000000"
        vdir.mkdir(parents=True)
        (vdir / "card.json").write_text('{"fake": true}', encoding="utf-8")
        (vdir / "worldbook.json").write_text('{"fake": true}', encoding="utf-8")
        # 版本目录 mtime 稍晚，若不被排除会成为「最新目录」
        import os as _os
        import time as _time
        now = _time.time()
        _os.utime(vdir / "card.json", (now + 1, now + 1))
        _os.utime(sample_works / "玫瑰与繁花" / "card.json", (now, now))
        items = collection_artifacts.collect_artifacts(str(sample_works))
        assert items, "真实作品产物应在"
        for item in items:
            assert "_versions" not in Path(item["path"]).parts
