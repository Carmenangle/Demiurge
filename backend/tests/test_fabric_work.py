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
    from app.services import character_store

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
    # 2026-09-14 治本：主卡扫描改走 owned_card_dirs（单一属主），故卡目录须带归属标记
    # （或卡目录名 == 仓库名）；无标记的散卡不再被当成「本作品前序产出」。
    character_store.write_work_marker(str(tmp_path), "测试卡", "r1")
    brief = fabric_work.build_work_brief(str(tmp_path), "r1")
    assert "B 态" in brief
    assert "内嵌 3 条" in brief


def test_别的作品的卡不进前序档案(tmp_path):
    """2026-09-14 治本回归：作品库根下别的作品的卡不得进【本作品前序产出档案】。

    实盘：`userdata/pictures/` 根下并列 旧巷当铺 / 驭云录 / 青梧与晚风 三张卡，
    只有归属本作品的才该出现——旧实现对库根做 `*/card.json` 全扫，会把别的作品
    的卡当成「本作品此前落盘的产出」写进 system 上下文（误导模型）。
    """
    from app.services import character_store

    alien = tmp_path / "别人的卡"
    alien.mkdir()
    (alien / "card.json").write_text(json.dumps({
        "spec": "chara_card_v2",
        "data": {"name": "别人的卡", "first_mes": "x",
                 "character_book": {"entries": [{"comment": "别人的条目"}] * 5}},
    }, ensure_ascii=False), encoding="utf-8")
    character_store.write_work_marker(str(tmp_path), "别人的卡", "other-repo")

    mine = tmp_path / "驭云录"
    mine.mkdir()
    (mine / "card.json").write_text(json.dumps({
        "spec": "chara_card_v2",
        "data": {"name": "驭云录", "first_mes": "y",
                 "character_book": {"entries": [{"comment": "我的条目"}] * 2}},
    }, ensure_ascii=False), encoding="utf-8")
    character_store.write_work_marker(str(tmp_path), "驭云录", "r1")

    brief = fabric_work.build_work_brief(str(tmp_path), "r1")
    assert "驭云录" in brief
    assert "别人的卡" not in brief
    assert "别人的条目" not in brief


def test_brief超长截断(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "长文档.md").write_text("长" * 9000, encoding="utf-8")
    brief = fabric_work.build_work_brief(str(tmp_path), "r1", max_chars=300)
    assert len(brief) <= 700  # brief 本身带固定前后缀
    assert "截断" in brief


def test_charfacts库根跨作品扫描_已知有意豁免锁():
    """T6.2（2026-09-17 用户拍板永久豁免，二轮审计 #23 同判）：`_scan_charfacts`
    root=库根时会跨作品列出别的作品的素材段——**有意保留**，理由见
    `fabric_work._scan_charfacts` docstring（charfacts 按作品命名可区分、库根级
    素材对冷启动有价值、收紧会让老仓库失忆）。本锁固化豁免注释存在：
    若有人移除 docstring 中的「已知有意」声明或改掉函数行为，此处即红，
    提示先回读 ARCHITECTURE「明确不做的」对应条目，别静默改语义。"""
    from pathlib import Path as _P
    src = _P(fabric_work.__file__).read_text(encoding="utf-8")
    assert "已知有意（2026-09-17 用户拍板 T6.2 永久豁免）" in src, (
        "_scan_charfacts 的豁免声明被移除——先回读 ARCHITECTURE「明确不做的」"
        "对应条目，确认是否真要推翻用户拍板")
    # 行为面现状锁：库根 _prep/charfacts 仍被扫描（改收紧 = 推翻豁免，须走用户决策）
    import inspect
    body = inspect.getsource(fabric_work._scan_charfacts)
    assert 'cf = root / "_prep" / "charfacts"' in body