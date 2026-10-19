"""批量视觉描述后台任务（2026-10-18）：筛选、并发写回、协作式取消、进度 tick。"""
import pytest

from app.services import jobs, vlm_batch


@pytest.fixture()
def _repo():
    """建一个临时仓库：两张图片（一张缺描述、一张已有）+ 一条非图片资产。"""
    from app.services import asset_store
    rid = "vlm-batch-test"
    asset_store.upsert_asset({
        "id": "gen-a", "repo_id": rid, "prompt": "p-a", "description": "",
        "image_url": "file:///tmp/a.png", "media_type": "image", "created_at": 1000,
    })
    asset_store.upsert_asset({
        "id": "gen-b", "repo_id": rid, "prompt": "p-b", "description": "已有描述",
        "image_url": "file:///tmp/b.png", "media_type": "image", "created_at": 1001,
    })
    asset_store.upsert_asset({
        "id": "gen-c", "repo_id": rid, "prompt": "p-c", "description": "",
        "image_url": "file:///tmp/c.mp3", "media_type": "audio", "created_at": 1002,
    })
    yield rid
    asset_store.delete_rows(["gen-a", "gen-b", "gen-c"])


def test_missing_only_picks_image_without_description(_repo):
    """只挑「图片 + 缺描述」：已有描述的、非图片的都不进队列。"""
    got = vlm_batch.missing_description_assets([_repo])
    assert [g["id"] for g in got] == ["gen-a"]


def test_file_to_datauri_missing_file_returns_empty(tmp_path):
    assert vlm_batch.file_to_datauri(tmp_path / "nope.png") == ""


def test_run_describe_missing_ticks_and_writes(_repo, monkeypatch, tmp_path):
    """正常路径：逐张写描述、进度 tick 到 total、返回 ok/failed。"""
    monkeypatch.setattr(vlm_batch, "file_to_datauri", lambda _p: "data:image/png;base64,AAAA")
    monkeypatch.setattr(vlm_batch, "describe_datauri", lambda _v, _u, _h: "一段中文描述")
    written: list[tuple] = []

    from app.services import rag_store
    monkeypatch.setattr(rag_store, "local_path_of", lambda _url: tmp_path / "a.png")
    monkeypatch.setattr(
        rag_store, "set_generation_description",
        lambda doc_id, repo_id, cfg, text: (written.append((doc_id, repo_id, text)), True)[1],
    )

    job = jobs.create("vlm_describe", "t", 0)
    res = vlm_batch.run_describe_missing(job.id, [_repo], object(), {"model": "m"})
    assert res == {"ok": 1, "failed": 0, "total": 1}
    assert written == [("gen-a", _repo, "一段中文描述")]
    snap = jobs.get(job.id)
    assert snap["status"] == "done" and snap["done"] == 1 and snap["total"] == 1


def test_run_describe_missing_single_failure_does_not_stop(_repo, monkeypatch, tmp_path):
    """单张失败只记数、不中断其余（这里只有 1 张 ⇒ 结果是 failed=1，但任务仍 done 不 failed）。"""
    monkeypatch.setattr(vlm_batch, "file_to_datauri", lambda _p: "")
    job = jobs.create("vlm_describe", "t", 0)
    res = vlm_batch.run_describe_missing(job.id, [_repo], object(), {"model": "m"})
    assert res == {"ok": 0, "failed": 1, "total": 1}
    assert jobs.get(job.id)["status"] == "done"


def test_run_describe_missing_empty_target(_repo, monkeypatch):
    """全员已有描述 ⇒ total=0、直接 done，不调模型。"""
    from app.services import asset_store
    asset_store.upsert_asset({
        "id": "gen-a", "repo_id": _repo, "prompt": "p-a", "description": "x",
        "image_url": "file:///tmp/a.png", "media_type": "image", "created_at": 1000,
    })
    job = jobs.create("vlm_describe", "t", 0)
    res = vlm_batch.run_describe_missing(job.id, [_repo], object(), {"model": "m"})
    assert res == {"ok": 0, "failed": 0, "total": 0}
    assert jobs.get(job.id)["status"] == "done"


def test_run_describe_missing_respects_cancel(_repo, monkeypatch, tmp_path):
    """取消信号：任务标 cancelled（不是 done）。"""
    monkeypatch.setattr(vlm_batch, "file_to_datauri", lambda _p: "data:image/png;base64,AAAA")
    monkeypatch.setattr(vlm_batch, "describe_datauri", lambda _v, _u, _h: "描述")
    from app.services import rag_store
    monkeypatch.setattr(rag_store, "local_path_of", lambda _url: tmp_path / "a.png")
    monkeypatch.setattr(rag_store, "set_generation_description", lambda *a, **k: True)

    job = jobs.create("vlm_describe", "t", 0)
    jobs.cancel(job.id)
    res = vlm_batch.run_describe_missing(job.id, [_repo], object(), {"model": "m"})
    assert res["total"] == 1
    assert jobs.get(job.id)["status"] == "cancelled"


def test_describe_datauri_rejects_empty():
    with pytest.raises(ValueError):
        vlm_batch.describe_datauri({"model": "m"}, "")


def test_run_describe_missing_writes_detail_split(_repo, monkeypatch, tmp_path):
    """收尾必须写「成功 N / 失败 M」——否则用户只看到 X/N，不知道还剩多少没补上。"""
    monkeypatch.setattr(vlm_batch, "file_to_datauri", lambda _p: "data:image/png;base64,AAAA")
    monkeypatch.setattr(vlm_batch, "describe_datauri", lambda _v, _u, _h: "描述")
    from app.services import rag_store
    monkeypatch.setattr(rag_store, "local_path_of", lambda _url: tmp_path / "a.png")
    monkeypatch.setattr(rag_store, "set_generation_description", lambda *a, **k: True)

    job = jobs.create("vlm_describe", "t", 0)
    vlm_batch.run_describe_missing(job.id, [_repo], object(), {"model": "m"})
    assert jobs.get(job.id)["detail"] == "成功 1 / 失败 0"


def test_run_describe_missing_empty_detail_says_none(_repo, monkeypatch):
    """空队列的 detail 要说明原因，别留空串。"""
    from app.services import asset_store
    asset_store.upsert_asset({
        "id": "gen-a", "repo_id": _repo, "prompt": "p-a", "description": "x",
        "image_url": "file:///tmp/a.png", "media_type": "image", "created_at": 1000,
    })
    job = jobs.create("vlm_describe", "t", 0)
    vlm_batch.run_describe_missing(job.id, [_repo], object(), {"model": "m"})
    assert jobs.get(job.id)["detail"] == "没有缺描述的资产"


