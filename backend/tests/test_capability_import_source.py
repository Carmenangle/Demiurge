"""character.import_source 能力回归测试（2026-09-03）。

覆盖 import_source_card handler：
- PNG（tEXt 内嵌 ST 卡）导入角色卡源库（characterDir 运行态真源）+ 头像落盘；
- 同名冲突 FileExistsError → ValueError 提示 overwrite；overwrite=true 重导成功；
- extract_worldbook=true：内嵌世界书外拆为独立世界书（worldbookDir/<名>.json）并从卡剥离；
- JSON 卡导入（无 PNG 头像）；
- 边界：路径不存在 / 未配置 characterDir / 损坏 PNG → ValueError。
- 能力注册形态（durable + handler + path 必填）。
"""
from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest

from app.services import capability_handlers as ch
from app.services import capability_registry as cr
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


def _v3_card(name: str = "Helia") -> dict:
    """带内嵌世界书 + 正则脚本的 ST V3 卡。"""
    return {
        "spec": "chara_card_v3",
        "data": {
            "name": name,
            "description": "ST 制卡经验库迁来的测试卡",
            "first_mes": "你好，旅人。",
            "character_book": {"entries": [
                {"keys": ["世界观"], "content": "大陆通用规则：夜间宵禁。", "constant": True},
            ]},
            "extensions": {"regex_scripts": [
                {"scriptName": "状态栏", "findRegex": r"<status>[\s\S]*?</status>"},
            ]},
        },
    }


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """把 handler 读的运行态目录（characterDir/worldbookDir）指向临时目录。"""
    char_dir = tmp_path / "角色卡源库"
    wb_dir = tmp_path / "独立世界书"
    char_dir.mkdir()
    wb_dir.mkdir()
    configured = {"characterDir": str(char_dir), "worldbookDir": str(wb_dir)}
    monkeypatch.setattr(ch, "_dir_from_state", lambda key: configured.get(key) or "")
    return {"character_dir": str(char_dir), "worldbook_dir": str(wb_dir)}


def test_import_source能力注册为durable且path必填():
    cap = cr.get("character.import_source")
    assert cap is not None
    assert cap.side_effect_level == "durable"
    assert cap.handler == "app.services.capability_handlers:import_source_card"
    assert cap.params_schema["required"] == ["path"]
    assert cap.params_schema["additionalProperties"] is False


def test_png导入落盘并保存头像(dirs, tmp_path):
    png = _png_with_text({"ccv3": _encode_card(_v3_card())})
    source = tmp_path / "Helia.png"
    source.write_bytes(png)

    result = ch.import_source_card(str(source))

    assert result["name"] == "Helia"
    assert result["avatar_saved"] is True
    assert result["worldbook_extracted"] is False
    folder = cs.card_dir(dirs["character_dir"], "Helia")
    assert folder.is_dir()
    assert (folder / "card.json").is_file()
    avatar = (folder / "avatar.png").read_bytes()
    assert avatar.startswith(cc.PNG_SIGNATURE) and avatar == png
    # 内嵌世界书仍随卡保存（未外拆）
    assert (folder / "worldbook.json").is_file()


def test_同名卡冲突要求overwrite(dirs, tmp_path):
    png = _png_with_text({"chara": _encode_card(_v3_card())})
    source = tmp_path / "Helia.png"
    source.write_bytes(png)
    ch.import_source_card(str(source))

    with pytest.raises(ValueError, match="overwrite=true"):
        ch.import_source_card(str(source))

    result = ch.import_source_card(str(source), overwrite=True)
    assert result["name"] == "Helia" and result["avatar_saved"] is True


def test_extract_worldbook外拆并剥离(dirs, tmp_path):
    png = _png_with_text({"ccv3": _encode_card(_v3_card())})
    source = tmp_path / "Helia.png"
    source.write_bytes(png)

    result = ch.import_source_card(str(source), extract_worldbook=True)

    assert result["worldbook_extracted"] is True
    # 独立世界书落盘：worldbookDir/<名>.json
    wb_file = tmp_path / "独立世界书" / "Helia.json"
    assert wb_file.is_file()
    book = json.loads(wb_file.read_text(encoding="utf-8"))
    assert book["entries"][0]["keys"] == ["世界观"]
    # 卡被剥净：内嵌 worldbook.json 删除、card.json 的 character_book 置空
    folder = cs.card_dir(dirs["character_dir"], "Helia")
    assert not (folder / "worldbook.json").is_file()
    assert cs.read_card(dirs["character_dir"], "Helia")["character_book"] is None


def test_json卡导入无头像(dirs, tmp_path):
    card = {"spec": "chara_card_v2", "data": {"name": "Nova", "description": "纯 JSON 卡"}}
    source = tmp_path / "Nova.json"
    source.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    result = ch.import_source_card(str(source))

    assert result["name"] == "Nova"
    assert result["avatar_saved"] is False
    assert cs.card_exists(dirs["character_dir"], "Nova")


def test_路径不存在报错(dirs):
    with pytest.raises(ValueError, match="卡文件不存在"):
        ch.import_source_card("D:/不存在的卡.png")


def test_未配置characterDir拒绝导入(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_dir_from_state", lambda key: "")
    png = _png_with_text({"chara": _encode_card(_v3_card())})
    source = tmp_path / "Helia.png"
    source.write_bytes(png)
    with pytest.raises(ValueError, match="characterDir"):
        ch.import_source_card(str(source))


def test_损坏PNG拒绝导入(dirs, tmp_path):
    source = tmp_path / "broken.png"
    source.write_bytes(b"definitely not a png")
    with pytest.raises(ValueError, match="卡解析失败"):
        ch.import_source_card(str(source))
