"""分页契约（2026-10-19）：total 与 offset。

用户实锤两个现象都源于「前端拿不到总数」：
① 明明五六页资产，分页条只显示「共 2 页」；
② 只能一页页点下一页才能解锁。
"""
import pytest

from app.services import asset_store


@pytest.fixture()
def _repo():
    rid = "paging-test"
    for i in range(70):
        asset_store.upsert_asset({
            "id": f"gen-{i:03d}", "repo_id": rid, "prompt": f"p{i}",
            "description": "", "image_url": f"file:///tmp/{i}.png",
            "media_type": "image", "created_at": 1000 + i,
        })
    yield rid
    asset_store.delete_rows([f"gen-{i:03d}" for i in range(70)])


def test_count_assets_scoped(_repo):
    assert asset_store.count_assets([_repo], deleted=False) == 70


def test_offset_paging_covers_all_without_overlap(_repo):
    """offset 分页：翻完不重不漏，且能直接跳到任意页。"""
    seen: list[str] = []
    for page in range(3):
        rows = asset_store.list_assets([_repo], limit=32, offset=page * 32)
        seen.extend(r["id"] for r in rows)
    assert len(seen) == 70
    assert len(set(seen)) == 70          # 不重

    # 直达第 3 页（offset 64）＝ 最后 6 条
    last = asset_store.list_assets([_repo], limit=32, offset=64)
    assert len(last) == 6
    assert last[0]["created_at"] == 1005  # 倒序第 65 条 ⇒ 1069-64（最老的那批）
    # 与顺序翻到第 3 页结果一致
    assert [r["id"] for r in last] == seen[64:]


def test_offset_ignored_without_limit(_repo):
    """limit=0（全量）时 offset 不应生效——SQLite 里 offset 必须跟在 limit 后。"""
    assert len(asset_store.list_assets([_repo])) == 70
    assert len(asset_store.list_assets([_repo], offset=32)) == 70


def test_router_returns_total_and_offset(_repo):
    """路由层：响应带 total（前端靠它算总页数），offset 生效。"""
    from app.routers.rag import GenerationsRequest, list_generations

    r1 = list_generations(GenerationsRequest(repo_ids=[_repo], limit=32))
    assert r1["has_more"] is True and r1["total"] == 70
    assert len(r1["items"]) == 32

    r3 = list_generations(GenerationsRequest(repo_ids=[_repo], limit=32, offset=64))
    assert r3["total"] == 70 and r3["has_more"] is False
    assert len(r3["items"]) == 6
    assert not ({i["id"] for i in r1["items"]} & {i["id"] for i in r3["items"]})


def test_media_type_pushed_to_sql():
    """媒体类型下推（2026-10-19）：total 与 items 必须同口径，否则分页条出假页。"""
    from app.routers.rag import GenerationsRequest, list_generations

    rid = "paging-media"
    # 3 图 + 2 视频 + 1 音频，created_at 交错（防止只按顺序命中）
    rows = [
        ("g1", "image", 100), ("v1", "video", 101), ("g2", "image", 102),
        ("a1", "audio", 103), ("v2", "video", 104), ("g3", "image", 105),
    ]
    try:
        for i, (i_, mt, ts) in enumerate(rows):
            asset_store.upsert_asset({
                "id": i_, "repo_id": rid, "prompt": f"p{i}", "description": "",
                "image_url": f"file:///tmp/{i_}.png", "media_type": mt, "created_at": ts,
            })
        # 不限：6 条 / 1 页
        allr = list_generations(GenerationsRequest(repo_ids=[rid], limit=32))
        assert allr["total"] == 6 and len(allr["items"]) == 6
        # 只图片：3 条
        img = list_generations(GenerationsRequest(repo_ids=[rid], limit=32, media_type="image"))
        assert img["total"] == 3 and len(img["items"]) == 3
        assert {i["id"] for i in img["items"]} == {"g1", "g2", "g3"}
        # 只视频：2 条
        vid = list_generations(GenerationsRequest(repo_ids=[rid], limit=32, media_type="video"))
        assert vid["total"] == 2 and {i["id"] for i in vid["items"]} == {"v1", "v2"}
        # 音频：1 条
        aud = list_generations(GenerationsRequest(repo_ids=[rid], limit=32, media_type="audio"))
        assert aud["total"] == 1 and aud["items"][0]["id"] == "a1"
        # 非法值按「不限」处理（别把结果筛空）
        bad = list_generations(GenerationsRequest(repo_ids=[rid], limit=32, media_type="exe"))
        assert bad["total"] == 6
        # offset 与 media_type 叠加：视频 offset=1 ⇒ 只剩 1 条
        off = list_generations(GenerationsRequest(
            repo_ids=[rid], limit=32, media_type="video", offset=1))
        assert off["total"] == 2 and len(off["items"]) == 1
    finally:
        asset_store.delete_rows([i_ for i_, _, _ in rows])


def test_media_filter_changes_total_so_pager_is_honest():
    """筛「视频」后 total 变小 ⇒ 前端分页条不会再显示「共 10 页但内容只有一页」。"""
    from app.routers.rag import GenerationsRequest, list_generations
    rid = "paging-media2"
    rows = [(f"g{i}", "image", 200 + i) for i in range(70)]
    rows.append(("v1", "video", 300))
    try:
        for i_, mt, ts in rows:
            asset_store.upsert_asset({
                "id": i_, "repo_id": rid, "prompt": "p", "description": "",
                "image_url": f"file:///tmp/{i_}.png", "media_type": mt, "created_at": ts,
            })
        unfiltered = list_generations(GenerationsRequest(repo_ids=[rid], limit=32))
        assert unfiltered["total"] == 71
        video = list_generations(GenerationsRequest(repo_ids=[rid], limit=32, media_type="video"))
        assert video["total"] == 1 and video["has_more"] is False
    finally:
        asset_store.delete_rows([i_ for i_, _, _ in rows])


