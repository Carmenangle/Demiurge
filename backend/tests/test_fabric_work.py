"""作品前序产出档案单测：扫描已有产出 → 生成【前序产出档案】注入上下文。

2026-09-07 用户定案「重发的资本」：新 run 启动时自动加载已有产出
（卡纲/素材/世界书快照/主卡），模型基于其继续，不重读全文重建。
"""

from __future__ import annotations

import json

from app.services import fabric_work


def test_无产出返回空串(tmp_path):
    assert fabric_work.build_work_brief(str(tmp_path), "r1") == ""


def test_扫描文档与素材(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    outline = docs / "卡纲-测试.md"
    outline.write_text("# 卡纲\n- 条目A", encoding="utf-8")
    prep = tmp_path / "_prep" / "charfacts"
    prep.mkdir(parents=True)
    (prep / "主角.txt").write_text("素材内容" * 100, encoding="utf-8")
    brief = fabric_work.build_work_brief(str(tmp_path), "r1")
    assert "卡纲-测试.md" in brief
    assert "主角.txt" in brief
    assert "禁止重新通读" in brief


def test_扫描世界书快照条目(tmp_path):
    prep = tmp_path / "_prep"
    prep.mkdir()
    book = {"entries": [
        {"comment": "全局机制·甲", "content": "内容" * 30},
        {"comment": "角色卡·乙", "content": "内容" * 40},
    ]}
    (tmp_path / "worldbook.json").write_text(
        json.dumps(book, ensure_ascii=False), encoding="utf-8")
    brief = fabric_work.build_work_brief(str(tmp_path), "r1")
    assert "2 条" in brief
    assert "全局机制·甲" in brief


def test_扫描B态主卡(tmp_path):
    card_dir = tmp_path / "测试卡"
    card_dir.mkdir()
    card = {
        "spec": "chara_card_v2",
        "data": {
            "name": "测试卡",
            "first_mes": "你好",
            "character_book": {"entries": [{"comment": "x"}] * 3},
        },
    }
    (card_dir / "card.json").write_text(
        json.dumps(card, ensure_ascii=False), encoding="utf-8")
    brief = fabric_work.build_work_brief(str(tmp_path), "r1")
    assert "B 态" in brief
    assert "内嵌 3 条" in brief


def test_brief超长截断(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "长文档.md").write_text("长" * 9000, encoding="utf-8")
    brief = fabric_work.build_work_brief(str(tmp_path), "r1", max_chars=300)
    assert len(brief) <= 700  # brief 本身带固定前后缀
    assert "截断" in brief