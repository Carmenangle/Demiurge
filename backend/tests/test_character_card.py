"""角色卡解析/落盘测试：V1/V2/V3 归一 + PNG tEXt 读取 + 覆盖保留对话。"""
import base64
import json
import struct
import zlib

import pytest

from app.services import character_card as cc
from app.services import character_store as cs


def _png_with_text(chunks: dict[str, str]) -> bytes:
    """造一张最小 PNG：签名 + IHDR + 若干 tEXt + IEND。chunks: keyword→text。"""
    def chunk(ctype: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(
            ">I", zlib.crc32(ctype + data) & 0xFFFFFFFF
        )
    out = bytearray(cc.PNG_SIGNATURE)
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    for kw, text in chunks.items():
        out += chunk(b"tEXt", kw.encode("latin-1") + b"\x00" + text.encode("latin-1"))
    out += chunk(b"IEND", b"")
    return bytes(out)


def _encode_card(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


def test_v1_flat_card():
    card = cc.parse_card_json(json.dumps({
        "name": "Anima", "description": "d", "personality": "p",
        "scenario": "s", "first_mes": "hi", "mes_example": "e",
    }))
    assert card.name == "Anima"
    assert card.first_mes == "hi"
    assert card.spec == "chara_card_v1"
    assert not card.has_worldbook


def test_v2_wrapped_card_reads_data():
    card = cc.parse_card_json(json.dumps({
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {"name": "Bee", "description": "wrapped", "first_mes": "yo",
                 "alternate_greetings": ["a", "b"], "tags": ["x"]},
    }))
    assert card.name == "Bee"
    assert card.description == "wrapped"
    assert card.alternate_greetings == ["a", "b"]
    assert card.tags == ["x"]


def test_v2_embedded_worldbook_and_regex():
    card = cc.parse_card_json(json.dumps({
        "spec": "chara_card_v2",
        "data": {"name": "C",
                 "character_book": {"entries": [{"keys": ["k"], "content": "c"}]},
                 "extensions": {"regex_scripts": [{"scriptName": "r", "findRegex": "/x/"}]}},
    }))
    assert card.has_worldbook
    assert card.has_regex
    assert card.regex_scripts[0]["scriptName"] == "r"


def test_png_ccv3_precedence_over_chara():
    png = _png_with_text({
        "chara": _encode_card({"data": {"name": "V2name"}}),
        "ccv3": _encode_card({"spec": "chara_card_v3", "data": {"name": "V3name"}}),
    })
    card = cc.parse_card_bytes(png, "x.png")
    assert card.name == "V3name"


def test_png_falls_back_to_chara():
    png = _png_with_text({"chara": _encode_card({"data": {"name": "Only2"}})})
    assert cc.parse_card_bytes(png, "x.png").name == "Only2"


def test_missing_name_rejected():
    with pytest.raises(cc.CardParseError):
        cc.parse_card_json(json.dumps({"description": "no name"}))


def test_bad_png_rejected():
    with pytest.raises(cc.CardParseError):
        cc.read_png_card_json(b"not a png")


def test_store_roundtrip_and_overwrite_keeps_chat(tmp_path):
    base = str(tmp_path)
    card = cc.parse_card_json(json.dumps({"data": {"name": "Hero", "description": "v1"}}))
    cs.save_card(base, card)
    assert cs.card_exists(base, "Hero")
    assert not cs.has_chat(base, "Hero")

    # 写入一段对话，再覆盖导入 → 对话保留
    (cs.card_dir(base, "Hero") / cs.CHAT_FILE).write_text(
        json.dumps([{"role": "user", "text": "hi"}]), encoding="utf-8")
    assert cs.has_chat(base, "Hero")

    with pytest.raises(FileExistsError):
        cs.save_card(base, card)  # 不覆盖时拒绝

    card2 = cc.parse_card_json(json.dumps({"data": {"name": "Hero", "description": "v2"}}))
    cs.save_card(base, card2, overwrite=True)
    assert cs.read_card(base, "Hero")["description"] == "v2"
    assert cs.has_chat(base, "Hero")  # 覆盖后对话仍在


def test_store_overwrite_clears_stale_worldbook(tmp_path):
    base = str(tmp_path)
    with_book = cc.parse_card_json(json.dumps({
        "data": {"name": "W", "character_book": {"entries": [{"keys": ["k"], "content": "c"}]}}}))
    cs.save_card(base, with_book)
    assert (cs.card_dir(base, "W") / cs.WORLDBOOK_FILE).is_file()

    without = cc.parse_card_json(json.dumps({"data": {"name": "W"}}))
    cs.save_card(base, without, overwrite=True)
    assert not (cs.card_dir(base, "W") / cs.WORLDBOOK_FILE).is_file()


def test_list_cards(tmp_path):
    base = str(tmp_path)
    cs.save_card(base, cc.parse_card_json(json.dumps({"data": {"name": "A"}})))
    cs.save_card(base, cc.parse_card_json(json.dumps({"data": {"name": "B"}})))
    names = {c.name for c in cs.list_cards(base)}
    assert names == {"A", "B"}


def test_build_persona_system_includes_fields():
    card = {"name": "Lyra", "description": "精灵游侠", "personality": "冷静", "scenario": "森林边境",
            "system_prompt": "保持神秘", "first_mes": "你好，旅人。", "mes_example": "范例台词"}
    sp = cc.build_persona_system(card)
    assert "Lyra" in sp and "精灵游侠" in sp and "冷静" in sp and "森林边境" in sp
    assert "保持神秘" in sp and "范例台词" in sp
    assert "你好，旅人" not in sp  # 开场白不进 system
    assert cc.opening_message(card) == "你好，旅人。"


def test_build_persona_system_skips_empty():
    sp = cc.build_persona_system({"name": "X"})
    assert "X" in sp
    assert "【角色设定】" not in sp  # description 空则跳过
    assert cc.opening_message({"name": "X"}) == ""
