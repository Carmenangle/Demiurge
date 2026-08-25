"""M1.4 灵感卡资产库测试：保存/列表/详情/删图留文本/删除（删 JSON 留图片）。"""

import pytest

from app.services import inspiration_store


def test_save_and_list_card(tmp_path):
    out = str(tmp_path)
    card = inspiration_store.save_inspiration_card(
        out, title="女仆装", content="蕾丝、黑白配色、A 字裙摆",
        sources=[{"title": "某服装站", "url": "https://a.example/page"}],
    )
    assert card["id"].startswith("insp-")
    assert card["images"] == []

    listed = inspiration_store.list_inspiration_cards(out)
    assert len(listed) == 1
    assert listed[0]["title"] == "女仆装"
    assert listed[0]["content"] == "蕾丝、黑白配色、A 字裙摆"
    assert listed[0]["cover_url"] == ""  # 无图 → 纯文本封面

    detail = inspiration_store.get_inspiration_card(out, card["id"])
    assert detail["sources"][0]["url"] == "https://a.example/page"


def test_save_empty_card_rejected(tmp_path):
    with pytest.raises(Exception):
        inspiration_store.save_inspiration_card(str(tmp_path), title="", content="")


def test_save_card_idempotent_override(tmp_path):
    out = str(tmp_path)
    cid = "insp-abc123"
    inspiration_store.save_inspiration_card(out, card_id=cid, title="一", content="A")
    inspiration_store.save_inspiration_card(out, card_id=cid, title="二", content="B")
    cards = inspiration_store.list_inspiration_cards(out)
    assert len(cards) == 1
    assert cards[0]["title"] == "二"


def test_card_images_local_ref_preserved(tmp_path):
    """本地 local-view 引用直接记录，不触发下载。"""
    out = str(tmp_path)
    card = inspiration_store.save_inspiration_card(
        out, title="t", content="c",
        images=[{
            "full_url": "/api/comfyui/local-view?path=C%3A%5Cimg.png",
            "source_url": "https://src.example/page",
            "title": "参考图",
        }],
    )
    assert card["images"][0]["url"].startswith("/api/comfyui/local-view")
    assert card["images"][0]["source_url"] == "https://src.example/page"


def test_remote_image_download_uses_web_material(monkeypatch, tmp_path):
    """远程图走受控下载：未登记候选的 URL 被拒。"""
    from app.services import image_store, web_material_candidates

    _PNG = bytes.fromhex("89504e470d0a1a0a" + "00" * 16)
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    monkeypatch.setenv("WEB_MATERIAL_ALLOWED_DOMAINS", "img.example")
    web_material_candidates._CANDIDATES.clear()
    web_material_candidates.register_candidates([{"full_url": "https://img.example/1.png"}])

    out = str(tmp_path)
    card = inspiration_store.save_inspiration_card(
        out, title="t", content="c",
        images=[{"full_url": "https://img.example/1.png", "source_url": "https://img.example/page"}],
    )
    assert len(card["images"]) == 1
    assert "/local-view?" in card["images"][0]["url"]
    # 图片落盘 _web_materials/，卡 JSON 在 inspiration/
    assert len(list(image_store.web_materials_dir(out).glob("*.png"))) == 1
    assert (tmp_path / "_web_materials" / "inspiration" / f"{card['id']}.json").is_file()
    web_material_candidates._CANDIDATES.clear()


def test_remote_image_not_candidate_rejected(monkeypatch, tmp_path):
    """未登记的远程图：保存整卡失败（不落半成品卡）。"""
    from app.services import image_store, web_material_candidates

    _PNG = bytes.fromhex("89504e470d0a1a0a" + "00" * 16)
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    web_material_candidates._CANDIDATES.clear()
    with pytest.raises(Exception) as ei:
        inspiration_store.save_inspiration_card(
            str(tmp_path), title="t", content="c",
            images=[{"full_url": "https://evil.example/x.png"}],
        )
    assert "候选" in str(ei.value)
    assert not list((tmp_path / "_web_materials" / "inspiration").glob("*.json"))
    web_material_candidates._CANDIDATES.clear()


def test_update_remove_image_keeps_text_and_file(tmp_path):
    """删图只留文本：JSON 里 images 清空，图片文件保留（可作独立素材）。"""
    out = str(tmp_path)
    card = inspiration_store.save_inspiration_card(
        out, title="t", content="c",
        images=[{
            "full_url": "/api/comfyui/local-view?path=C%3A%5Cimg.png",
            "source_url": "https://src.example/page",
        }],
    )
    url = card["images"][0]["url"]
    updated = inspiration_store.update_inspiration_card(
        out, card_id=card["id"], remove_image_urls=[url],
    )
    assert updated["images"] == []
    assert updated["title"] == "t"
    assert updated["content"] == "c"

    # 文本编辑
    updated2 = inspiration_store.update_inspiration_card(
        out, card_id=card["id"], title="新标题", content="新内容",
    )
    assert updated2["title"] == "新标题"
    assert updated2["content"] == "新内容"


def test_delete_card_keeps_image_file(tmp_path):
    out = str(tmp_path)
    card = inspiration_store.save_inspiration_card(
        out, title="t", content="c",
        images=[{
            "full_url": "/api/comfyui/local-view?path=C%3A%5Cimg.png",
            "source_url": "https://src.example/page",
        }],
    )
    assert inspiration_store.delete_inspiration_card(out, card["id"])
    assert inspiration_store.list_inspiration_cards(out) == []
    # 图片 JSON 引用删除（只删卡 JSON）
    assert not (tmp_path / "_web_materials" / "inspiration" / f"{card['id']}.json").exists()


def test_bad_card_id_rejected(tmp_path):
    with pytest.raises(Exception):
        inspiration_store.get_inspiration_card(str(tmp_path), "../etc/passwd")
    assert not inspiration_store.delete_inspiration_card(str(tmp_path), "../../evil")
