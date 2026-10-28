"""仓库同步（P3 ⑤，2026-10-18）：媒体类型判定、磁盘扫盘、只读体检报告。"""
from pathlib import Path

from app.services import repo_sync, repo_meta


def test_media_kind_of():
    assert repo_sync.media_kind_of(Path("a.PNG")) == "image"
    assert repo_sync.media_kind_of(Path("b.mp4")) == "video"
    assert repo_sync.media_kind_of(Path("c.MP3")) == "audio"
    assert repo_sync.media_kind_of(Path("d.txt")) is None
    assert repo_sync.media_kind_of(Path("noext")) is None


def test_disk_media_scans_three_subfolders(tmp_path):
    folder = tmp_path / "repo"
    for sub, name in [("pictures", "a.png"), ("videos", "b.mp4"), ("audios", "c.wav")]:
        d = folder / sub
        d.mkdir(parents=True)
        (d / name).write_bytes(b"x")
    (folder / "pictures" / "note.txt").write_bytes(b"x")  # 非媒体，应忽略
    got = repo_sync._disk_media(folder)
    assert {Path(p).name for p in got} == {"a.png", "b.mp4", "c.wav"}


def test_disk_media_missing_folder_returns_empty(tmp_path):
    assert repo_sync._disk_media(tmp_path / "nope") == {}


def test_repo_sync_report_unindexed_and_missing(tmp_path, monkeypatch):
    """磁盘有、索引无 → unindexed（可补录）；索引在线有、磁盘无 → missing（裂图可清）。"""
    folder = tmp_path / "repo"
    (folder / "pictures").mkdir(parents=True)
    new_file = folder / "pictures" / "new.png"
    new_file.write_bytes(b"x")
    gone = folder / "pictures" / "gone.png"  # 刻意不创建

    monkeypatch.setattr(repo_sync, "_registered_repos", lambda: {"r1": "R1"})
    monkeypatch.setattr(repo_meta, "repo_folder_path", lambda _out, _rid: folder)
    monkeypatch.setattr(
        repo_sync, "_indexed_local_paths",
        lambda _rid: ({str(gone.resolve())}, {str(gone.resolve())}),  # (live, all)
    )

    rep = repo_sync.repo_sync_report(str(tmp_path), object())
    repo = rep["repos"][0]
    assert repo["unindexed"] == [str(new_file.resolve())]
    assert repo["missing"] == [str(gone.resolve())]
    assert repo["disk"] == 1 and repo["indexed"] == 1 and repo["folder_exists"] is True
    assert rep["total_unindexed"] == 1 and rep["total_missing"] == 1
    assert rep["orphan_index_repos"] == []


def test_repo_sync_report_dead_repo(tmp_path, monkeypatch):
    """注册着但磁盘上没文件夹 → folder_exists=False。"""
    monkeypatch.setattr(repo_sync, "_registered_repos", lambda: {"r1": "R-One"})
    monkeypatch.setattr(repo_meta, "repo_folder_path", lambda _out, _rid: tmp_path / "gone")
    monkeypatch.setattr(repo_sync, "_indexed_local_paths", lambda _rid: (set(), set()))
    rep = repo_sync.repo_sync_report(str(tmp_path), object())
    assert rep["repos"][0]["folder_exists"] is False
    assert rep["repos"][0]["name"] == "R-One"


def test_purge_orphan_rows_removes_missing_keeps_existing(tmp_path, monkeypatch):
    """按主表补刀：只删「文件已缺失」的孤儿行；文件仍在的保留（防误删真源）。"""
    from app.services import asset_store

    existing = tmp_path / "ok.png"
    existing.write_bytes(b"x")
    gone = tmp_path / "gone.png"
    deleted: list = []

    class FakeConn:
        def execute(self, *_a, **_k):
            class _R:
                def fetchall(self):
                    return [
                        {"id": "keep", "image_url": f"http://127.0.0.1:8010/api/local-view?path={existing}"},
                        {"id": "drop", "image_url": f"http://127.0.0.1:8010/api/local-view?path={gone}"},
                    ]
            return _R()

        def close(self):
            pass

    monkeypatch.setattr(asset_store, "get_connection", lambda: FakeConn())
    monkeypatch.setattr(asset_store, "delete_rows", lambda ids: deleted.extend(ids) or len(ids))
    assert repo_sync._purge_orphan_rows("orphan-x") == 1
    assert deleted == ["drop"]



def test_repo_sync_fix_writes_main_table_and_list_revives(tmp_path, monkeypatch):
    """2026-10-27 真机实锤回归：补录必须落**主表**（读取域），Chroma 仅为可选增强。

    手机场景 = 嵌入 cfg 不可用（index_generation 必抛）——修复后主表仍写入，
    list_generations_page（asset_db_empty 判定走主表）即刻可见。
    """
    from app.services import asset_store, rag_store

    folder = tmp_path / "repo"
    (folder / "pictures").mkdir(parents=True)
    img = folder / "pictures" / "a.png"
    img.write_bytes(b"x")

    monkeypatch.setattr(repo_sync, "_registered_repos", lambda: {"r1": "R1"})
    monkeypatch.setattr(repo_meta, "repo_folder_path", lambda _out, _rid: folder)
    monkeypatch.setattr(repo_sync, "_indexed_local_paths", lambda _rid: (set(), set()))

    def _boom(*a, **k):  # 手机场景：嵌入不可用
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(rag_store, "index_generation", _boom)

    class _Job:
        id = "j1"
    from app.services import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "create", lambda *a, **k: _Job())
    monkeypatch.setattr(jobs_mod, "tick", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "finish", lambda *_a, **_k: None)

    result = repo_sync.repo_sync_fix(str(tmp_path), object(), do_prune=False)
    assert result["indexed"] == 1

    # 主表有行（读取域）
    assert asset_store.count_assets(["r1"]) == 1
    # doc_id = generation_doc_id 单一公式（与 index/delete 天然同 id）
    from app.services import view_urls
    expected_doc = rag_store.generation_doc_id(
        "r1", view_urls.local_view(str(img.resolve())))
    row = asset_store.list_assets(["r1"], limit=5)[0]
    assert row["id"] == expected_doc
    assert row["repo_id"] == "r1" and row["mediaType"] == "image"
    # 幂等：再修一遍不重复（_indexed_local_paths 已被上面的静态 mock 钉死，
    # 先恢复真实实现让它读主表现状）
    monkeypatch.setattr(
        repo_sync, "_indexed_local_paths",
        lambda rid: ({str(img.resolve())}, {str(img.resolve())}))
    result2 = repo_sync.repo_sync_fix(str(tmp_path), object(), do_prune=False)
    assert result2["indexed"] == 0
