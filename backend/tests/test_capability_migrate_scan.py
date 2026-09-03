"""character.migrate_scan 能力回归测试（2026-09-03，第二套固定流程机械前置）。

覆盖 st_migration.analyze_source / capability_handlers.migrate_scan_source：
- PNG（tEXt 内嵌 ST 卡）体检：kind=character_card、内嵌世界书逐条标注、顶层 first_mes；
- JSON 角色卡体检（V2 data 包装）；
- 独立世界书：dict 容器归一提示、空 keys/超长 keys、constant 越权、渲染层、
  运行时表格、注入位死字段、disable、视觉画像前缀缺失；
- preset：连接/鉴权字段检测、prompts 空检测；
- regex：缺稳定 id 检测；
- 边界：路径不存在 / 坏 JSON / 坏 PNG / 超大类 / 能力注册形态（readonly 不落盘）。
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
from app.services import st_migration


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
    """带内嵌世界书 + 正则的 ST V3 卡（first_mes 空、条目故意带各种问题）。"""
    return {
        "spec": "chara_card_v3",
        "data": {
            "name": name,
            "description": "ST 制卡经验库迁来的测试卡",
            "first_mes": "",
            "character_book": {"entries": [
                {
                    "keys": [], "content": "每轮开头输出 <status>…</status> 状态栏。",
                    "constant": True, "order": 10, "depth": 4,
                },
                {
                    "keys": ["一个超长关键词长得不像会被整串说出口"], "content": "好感 30 时触发："
                    "<if cell=\"好感\">大于 30</if>。", "disable": True,
                },
                {
                    "comment": "星港守卫", "content": "她是【外貌】银发、【穿着】制服，"
                    "常驻描写需要引用。", "keys": ["Helia", "星港"],
                },
            ]},
            "extensions": {"regex_scripts": [
                {"scriptName": "状态栏", "findRegex": r"<status>[\s\S]*?</status>"},
            ]},
        },
    }


def _write(tmp_path, name: str, data: bytes) -> str:
    target = tmp_path / name
    target.write_bytes(data)
    return str(target)


def test_migrate_scan能力注册为readonly且path必填():
    cap = cr.get("character.migrate_scan")
    assert cap is not None
    assert cap.side_effect_level == "readonly"
    assert cap.handler == "app.services.capability_handlers:migrate_scan_source"
    assert cap.params_schema["required"] == ["path"]
    assert cap.params_schema["additionalProperties"] is False


def test_png卡体检标注内嵌世界书问题(tmp_path):
    png = _png_with_text({"ccv3": _encode_card(_v3_card())})
    report = ch.migrate_scan_source(_write(tmp_path, "Helia.png", png))

    assert report["kind"] == "character_card"
    assert report["card"]["name"] == "Helia"
    assert report["card"]["first_mes_present"] is False
    assert report["regex_scripts"] == 1
    wb = report["worldbook"]
    assert wb["total_entries"] == 3
    codes = {code for entry in wb["entries"] for code in
             (issue["code"] for issue in entry["issues"])}
    # 条目 0：注入位死字段 + constant 越权 + keys 空 + 渲染层
    assert {"injection_fields", "constant_policy", "keys_empty",
            "render_layer"} <= codes
    # 条目 1：disable + keys 超长 + 运行时表格
    assert {"disable_field", "keys_too_long", "runtime_table"} <= codes
    # 条目 2：视觉画像前缀缺失
    assert "visual_anchor" in codes
    assert any(issue["code"] == "first_mes_empty"
               for issue in report["card"]["issues"])
    assert report["issue_counts"]["keys_empty"] == 1


def test_json卡V2包装体检(tmp_path):
    card = {"spec": "chara_card_v2", "data": {
        "name": "Nova", "first_mes": "你好。",
        "character_book": {"entries": [{"key": "世界", "content": "夜间宵禁。",
                                        "constant": False}]},
    }}
    path = _write(tmp_path, "Nova.json", json.dumps(card, ensure_ascii=False).encode())
    report = ch.migrate_scan_source(path)

    assert report["kind"] == "character_card"
    assert report["card"]["first_mes_present"] is True
    assert report["worldbook"]["total_entries"] == 1
    entry = report["worldbook"]["entries"][0]
    assert entry["keys"] == ["世界"]
    assert not entry["issues"]


def test_独立世界书dict容器与五类标注(tmp_path):
    book = {"entries": {
        "a1": {"keys": [], "content": "输出 <roll>d20</roll> 检定。", "constant": True,
               "insertion_order": 3, "position": 1},
        "b2": {"keys": ["夜宵", "守卫"], "content": "深夜必须宵禁。", "constant": False},
    }}
    path = _write(tmp_path, "wb.json", json.dumps(book, ensure_ascii=False).encode())
    report = ch.migrate_scan_source(path)

    assert report["kind"] == "worldbook"
    assert report["dict_container_normalized"] is True
    assert report["total_entries"] == 2
    first = report["entries"][0]
    codes = {issue["code"] for issue in first["issues"]}
    assert {"injection_fields", "constant_policy",
            "keys_empty", "render_layer"} <= codes
    second = report["entries"][1]
    assert second["keys"] == ["夜宵", "守卫"]
    assert not second["issues"]
    assert any(note.startswith("容器") for note in report["notes"])


def test_渲染层命中不高误伤普通文本(tmp_path):
    book = {"entries": [
        {"keys": ["日常"], "content": "她说「今晚的风很温柔」。", "constant": False},
        {"keys": ["战斗"], "content": "战斗结束时把 <hp> 剩余血量贴在最前。",
         "constant": False},
    ]}
    path = _write(tmp_path, "wb.json", json.dumps(book, ensure_ascii=False).encode())
    report = ch.migrate_scan_source(path)

    assert report["entries"][0]["issues"] == []
    codes = {issue["code"] for issue in report["entries"][1]["issues"]}
    assert "render_layer" in codes


def test_预设连接字段与空prompts(tmp_path):
    preset = {"prompts": [], "api_key": "sk-xxxx", "reverse_proxy": "https://p.example"}
    path = _write(tmp_path, "preset.json", json.dumps(preset).encode())
    report = ch.migrate_scan_source(path)

    assert report["kind"] == "preset"
    codes = {issue["code"] for issue in report["issues"]}
    assert {"preset_credentials", "preset_empty_prompts"} <= codes


def test_正则缺id(tmp_path):
    scripts = [{"scriptName": "状态栏", "findRegex": r"<status>.*?</status>"}]
    path = _write(tmp_path, "regex.json", json.dumps(scripts).encode())
    report = ch.migrate_scan_source(path)

    assert report["kind"] == "regex"
    assert report["regex_count"] == 1
    codes = {issue["code"] for issue in report["issues"]}
    assert "regex_missing_id" in codes


def test_路径不存在报错(tmp_path):
    with pytest.raises(ValueError, match="文件不存在"):
        ch.migrate_scan_source("D:/不存在的卡.png")


def test_坏JSON报错(tmp_path):
    path = _write(tmp_path, "bad.json", b"{not json")
    with pytest.raises(ValueError, match="JSON 非法"):
        ch.migrate_scan_source(path)


def test_坏PNG报错(tmp_path):
    path = _write(tmp_path, "broken.png", b"definitely not a png")
    with pytest.raises(ValueError, match="PNG 卡解析失败"):
        ch.migrate_scan_source(path)


def test_超大文件拒绝(tmp_path):
    path = _write(tmp_path, "big.json", b"x" * (st_migration.MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="文件过大"):
        ch.migrate_scan_source(path)


def test_常驻预算统计(tmp_path):
    book = {"entries": [
        {"keys": ["甲"], "content": "x" * 500, "constant": True, "comment": "全局机制·甲"},
        {"keys": ["乙"], "content": "y" * 300, "constant": False},
    ]}
    path = _write(tmp_path, "wb.json", json.dumps(book, ensure_ascii=False).encode())
    report = ch.migrate_scan_source(path)

    assert report["kind"] == "worldbook"  # 独立世界书形态：report 顶层即 book_section
    assert report["total_entries"] == 2
    assert report["constant_chars"] == 500
    assert any("常驻预算：500/20000" in note for note in report["report_notes"])


def test_纯函数analyze_worldbook不落盘(tmp_path, monkeypatch):
    """readonly 语义：analyze 不触碰任何目录，source 文件原样保留。"""
    book = {"entries": [{"keys": [], "content": "x", "constant": False}]}
    path = _write(tmp_path, "wb.json", json.dumps(book).encode())
    before = (tmp_path / "wb.json").read_bytes()

    report = st_migration.analyze_source(path)

    assert (tmp_path / "wb.json").read_bytes() == before
    assert report["issue_counts"]["keys_empty"] == 1
