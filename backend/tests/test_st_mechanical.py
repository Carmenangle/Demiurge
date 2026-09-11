"""ST 卡机械转写（st_mechanical + migrate_mechanical handler）测试。

2026-09-09 用户定案：固化03 走机械转写（零 LLM、内容不增删、正文逐字保留），
本测试覆盖：五类规则（字段清理/宏转【】/表格转档位/keys 补裁/list）、无损验证、
以及 handler 从 PNG 卡读源→转写→落盘世界书+主卡的全链路。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.services import capability_registry, character_card, st_mechanical
from app.services.capability_handlers import migrate_mechanical
from app.services import collection_artifacts


def test_migrate_mechanical注册形态():
    """2026-09-09 根因修复：机械转写必须作为已注册能力存在，计划编译才能编排它
    （此前只存在于 agent 临时脚本里，计划里永远编不出转写步骤）。"""
    cap = capability_registry.get("character.migrate_mechanical")
    assert cap is not None
    assert cap.category == "character"
    assert cap.side_effect_level == capability_registry.SIDE_EFFECT_DURABLE
    assert cap.needs_model is None  # 零 LLM：确定性规则，与 plan 预算 max_llm_calls=0 兼容
    assert cap.handler == "app.services.capability_handlers:migrate_mechanical"
    assert cap.params_schema["required"] == ["path"]
    assert set(cap.params_schema["properties"]) == {"path", "base", "repo_id"}


def _entry(comment: str, content: str, **extra) -> dict:
    e = {"comment": comment, "content": content, "keys": [], "constant": False}
    e.update(extra)
    return e


class TestTransform:
    def test_注入位字段清理_只留五字段(self):
        src = [{
            "id": "x1", "uid": 1, "insertion_order": 3, "position": 2, "order": 1,
            "depth": 0, "atDepth": 4, "role": "system", "sortFn": 0,
            "selective": False, "display_index": 0,
            "comment": "局部机制·催情药水", "content": "正文", "keys": ["药水"],
            "constant": False, "enabled": True,
        }]
        out, stats = st_mechanical.transform_entries(src)
        assert set(out[0].keys()) == {"content", "comment", "keys", "constant", "enabled"}
        assert out[0]["content"] == "正文" and out[0]["comment"] == "局部机制·催情药水"
        assert stats["total"] == 1

    def test_渲染宏转纯文本标记(self):
        src = [_entry("系统判定机制·状态栏输出",
                      "开头输出 <status>【时间】白天</status> 与 <roll>检定</roll> 块")]
        out, _ = st_mechanical.transform_entries(src)
        assert "【状态栏】" in out[0]["content"]
        assert "【检定】" in out[0]["content"]
        assert "<status>" not in out[0]["content"] and "<roll>" not in out[0]["content"]
        assert "【时间】白天" in out[0]["content"]  # 数据照搬

    def test_好感度表格转档位文本(self):
        src = [_entry("角色卡·测试",
                      '【好感分阶】<if cell="好感度表/测试/好感度 <= -30">阶段A</if>'
                      '<if cond="cell:好感度表/测试/好感度 > -30 & cell:好感度表/测试/好感度 <= 20">阶段B</if>')]
        out, _ = st_mechanical.transform_entries(src)
        c = out[0]["content"]
        assert "【好感度 ≤ -30】阶段A" in c
        assert "【好感度 -30 ~ 20】阶段B" in c
        assert "<if" not in c and "</if>" not in c

    def test_keys空补与超6裁(self):
        src = [
            _entry("全局机制·母性溺爱与独占争宠", "正文1"),
            _entry("全局机制·世界自转与事件登门", "正文2",
                   keys=["a", "b", "c", "d", "e", "f", "g"]),
        ]
        out, stats = st_mechanical.transform_entries(src)
        assert out[0]["keys"] and 2 <= len(out[0]["keys"][0]) <= 8  # 从 comment 补
        assert len(out[1]["keys"]) == 6  # 超 6 裁 6
        assert stats["keys_filled"] == 1 and stats["keys_trimmed"] == 1

    def test_正文逐字保留_重放比对全等(self):
        src = [
            _entry("系统判定机制·骰点", "开头 <roll>块</roll> 中间 <status>栏</status> 结尾"),
            _entry("角色卡·甲", "普通正文【外貌】无标签内容"),
        ]
        out, _ = st_mechanical.transform_entries(src)
        assert st_mechanical.verify_lossless(src, out)["ok"] is True

    def test_层统计(self):
        src = [_entry("系统判定机制·A", "x"), _entry("角色卡·B", "y"), _entry("自定义·C", "z")]
        layers = st_mechanical.entry_layers(src)
        assert layers["系统判定机制"] == 1 and layers["角色卡"] == 1 and layers["其它"] == 1


class TestHandler:
    def test_migrate_mechanical_PNG卡全链路(self, tmp_path: Path):
        """PNG 卡 → 五类转写 → 世界书快照 + B 态主卡落盘 → 无损验证通过。"""
        card = {
            "name": "测试合集卡", "spec": "chara_card_v3", "spec_version": "3.0",
            "data": {
                "name": "测试合集卡", "first_mes": "开场白",
                "character_book": {"entries": [
                    _entry("系统判定机制·AI叙事核心", "开头 <status>栏</status> 核心规则",
                           keys=["叙事核心"]),
                    _entry("角色卡·主角", "【外貌】金发 正文内容"),
                    _entry("局部机制·药剂", '催情药水 <if cell="好感度表/药剂/好感度 <= -30">阶段A</if>',
                           keys=[]),
                ]},
            },
        }
        png_path = tmp_path / "test_card.png"
        png_path.write_bytes(character_card.build_png_card(card))
        base = tmp_path / "works"
        repo_id = "repo-1"
        res = migrate_mechanical(str(png_path), str(base), repo_id)
        assert res["entries"] == 3
        assert res["lossless"]["ok"] is True
        # 世界书快照落盘
        snap = base / repo_id / "worldbook.json"
        assert snap.is_file()
        book = json.loads(snap.read_text(encoding="utf-8"))
        assert len(book["entries"]) == 3
        assert "<status>" not in book["entries"][0]["content"]
        assert "【状态栏】" in book["entries"][0]["content"]
        assert "<if" not in book["entries"][2]["content"]
        # B 态主卡落盘
        card_path = base / "测试合集卡" / "card.json"
        assert card_path.is_file()
        c = json.loads(card_path.read_text(encoding="utf-8"))
        assert len(c.get("character_book", {}).get("entries") or []) == 3
        # 产物可被 collect 收集（本轮 since 过滤后仍可见）。
        # collect 按「mtime 最新的文件所在目录组」聚合；本用例两个目录毫秒内先后落盘，
        # Windows 时间戳合并/精度抖动可能让后写的 card.json 读出不比先写的
        # worldbook.json 大（2026-09-11 全量跑实锤偶发红）→ 显式设 mtime 保证
        # 最新组落在「测试合集卡」，测聚合逻辑本身、不测文件系统时间戳精度。
        os.utime(snap, (1_000_000_000.0, 1_000_000_000.0))
        os.utime(card_path, (1_000_000_100.0, 1_000_000_100.0))
        items = collection_artifacts.collect_artifacts(str(base))
        assert any("测试合集卡" in i["path"] for i in items)

    def test_migrate_mechanical_世界书JSON(self, tmp_path: Path):
        wb = tmp_path / "book.json"
        wb.write_text(json.dumps({"entries": [
            _entry("世界背景·1地理", "<encounter>登场</encounter> 内容"),
            _entry("NSFW·玩法", "【爽点】内容"),
        ]}, ensure_ascii=False), encoding="utf-8")
        base = tmp_path / "w"
        res = migrate_mechanical(str(wb), str(base), "repo-x")
        assert res["entries"] == 2 and res["lossless"]["ok"] is True
        assert res["stats"]["macro_entries"] == 1

    def test_migrate_mechanical_源不存在(self, tmp_path: Path):
        with pytest.raises(ValueError):
            migrate_mechanical(str(tmp_path / "nope.png"), str(tmp_path), "r")
