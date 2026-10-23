"""小说增量定位（固化02 §3.9 更新版输入范围限制）：章节索引 + 新旧对比。

背景（2026-10-20 用户定案）：更新版小说做合集卡时，模型只能读「新增/被改」章节，
不得整本重读。本文件锁定机械层的确定性行为——章节绝对偏移、空白无关的章节哈希、
三档匹配（同哈希 / 同标题 / 顺序）、minor 修订默认不读、read_plan 分窗覆盖、
新出现人名候选，以及「索引缺失必须报可操作错误」这条防「静默全量重读」的红线。
"""
from __future__ import annotations

import json
import types

import pytest

from app.services import capability_handlers as ch
from app.services import capability_registry as cr
from app.services import fabric_loop
from app.services import novel_tools as nt


# ── fixtures ────────────────────────────────────────────────────────────────


def _chapter(title: str, body_seed: str, repeat: int = 40) -> str:
    return f"{title}\n" + ("%s这是第%d段正文，用来提供足够长度与段落边界。\n\n" % (body_seed, 1)) * repeat


def _book(*chapters: str) -> str:
    return "\n".join(chapters)


def _write(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


_OLD = _book(
    _chapter("第一章·码头", "甲"),
    _chapter("第二章·回声渊", "乙"),
    _chapter("第三章·归途", "丙"),
)


# ── T4 章节索引 ──────────────────────────────────────────────────────────────


def test_索引_原生分章带绝对偏移(tmp_path):
    text = _OLD
    idx = nt.build_index(text)
    assert idx["chapter_count"] == 3
    assert idx["chapters"][0]["start"] == 0
    second = idx["chapters"][1]
    assert second["start"] == text.index("第二章")
    assert second["chars"] > 0
    assert len(second["sha1"]) == 12
    assert second["para_count"] >= 1
    assert second["paras"]  # 段落哈希随索引落盘，供 diff 算相似度


def test_索引_抽取产物也有偏移(tmp_path):
    """extract_epub 产物是「===== 章节 =====」形态——旧实现 survey 不给 offset，
    导致 epub 源无法定向读；索引层必须两种形态都给出 start/chars。"""
    text = ("===== 第1章 码头 =====\n" + "甲" * 300 + "\n\n"
            + "===== 第2章 回声渊 =====\n" + "乙" * 300)
    recs = nt.chapter_records(text)
    assert [r["title"] for r in recs] == ["第1章 码头", "第2章 回声渊"]
    assert recs[0]["start"] == 0
    assert recs[1]["start"] == text.index("===== 第2章")
    # survey 同步受益：chapters 列表不再对标记产物为空
    p = _write(tmp_path, "full.txt", text)
    res = nt.survey_fulltext(str(p))
    assert res["chapters"] and res["chapters"][1]["start"] == recs[1]["start"]


def test_索引_正文段首第N章不产生伪章节(tmp_path):
    """2026-10-20 实锤：正文段落以「第1章第2段…」开头时，旧实现每段都当章节头，
    21 万字 60 章被切成上千个伪章节、chapter_count 塌成 1（索引与差异全失效）。"""
    chapters = []
    for i in (1, 2, 3):
        paras = "".join(f"第{i}章第{j}段正文，戴茂说道：这是用来模拟真实长篇的填充内容。\n\n"
                        for j in range(1, 30))
        chapters.append(f"第{i}章·标题{i}\n{paras}")
    text = "\n".join(chapters)

    idx = nt.build_index(text)
    assert idx["chapter_count"] == 3
    assert [c["title"] for c in idx["chapters"]] == ["第1章·标题1", "第2章·标题2", "第3章·标题3"]
    p = _write(tmp_path, "full.txt", text)
    assert nt.survey_fulltext(str(p))["chapter_count"] == 3


def test_索引_哈希忽略空白差异(tmp_path):
    a = nt.build_index("第一章·码头\n" + "甲" * 300)
    b = nt.build_index("第一章·码头\r\n" + ("甲" * 10 + " ") * 30)
    assert a["chapters"][0]["sha1"] == b["chapters"][0]["sha1"]


def test_索引_落盘默认路径且不回传段落哈希(tmp_path):
    p = _write(tmp_path, "novel.txt", _OLD)
    out = ch.novel_index(str(p), work_dir=str(tmp_path))
    target = tmp_path / "_prep" / "novel.index.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data["chapters"]) == 3
    assert "paras" in data["chapters"][0]
    # 工具结果是给模型看的摘要，不能带段落哈希（爆量）
    assert "paras" not in json.dumps(out, ensure_ascii=False)


def test_索引_out_json越出作品域被拒(tmp_path):
    p = _write(tmp_path, "novel.txt", _OLD)
    outside = tmp_path.parent / "outside.index.json"
    with pytest.raises(ValueError, match="作品域"):
        ch.novel_index(str(p), out_json=str(outside), work_dir=str(tmp_path))


# ── T4 新旧对比 ──────────────────────────────────────────────────────────────


def test_纯追加_新章才进read_plan(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))  # 落 _prep/novel.index.json
    new_text = _book(_OLD, _chapter("第四章·新婚", "丁"), _chapter("第五章·远行", "戊"))
    new = _write(tmp_path, "new.txt", new_text)

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"))
    assert len(res["unchanged"]) == 3
    assert len(res["added"]) == 2
    assert res["modified"] == []
    assert res["removed"] == []
    plan_chapters = {w["chapter_index"] for w in res["read_plan"]}
    assert plan_chapters == {4, 5}
    # 只该读新增部分：覆盖比例必须明显低于一半
    assert res["coverage_ratio"] < 0.5


def test_中间插入一章_后续章节不被误判修订(tmp_path):
    """插入一章会让后续章节序号整体后移；靠 sha 精确匹配后，后续 3 章必须仍是 unchanged。"""
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    insert = _chapter("第二章·插曲", "插")
    new_text = _book(_chapter("第一章·码头", "甲"), insert,
                     _chapter("第二章·回声渊", "乙"), _chapter("第三章·归途", "丙"))
    new = _write(tmp_path, "new.txt", new_text)

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"))
    assert len(res["unchanged"]) == 3
    assert len(res["added"]) == 1
    assert res["modified"] == []
    assert res["removed"] == []


def test_小改判minor_默认不读_显式包含才读(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    tweaked = _OLD.replace("这是第1段", "这是第1段的错字", 1)
    new = _write(tmp_path, "new.txt", tweaked)

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"))
    assert len(res["modified"]) == 1
    assert res["modified"][0]["level"] == "minor"
    assert res["read_plan"] == []

    res2 = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"),
                         include_minor_read=True)
    assert {w["chapter_index"] for w in res2["read_plan"]} == {1}


def test_大改判major_进read_plan(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    rewritten = _book(_chapter("第一章·码头", "全"), _chapter("第二章·回声渊", "乙"),
                      _chapter("第三章·归途", "丙"))
    new = _write(tmp_path, "new.txt", rewritten)

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"))
    major = [m for m in res["modified"] if m["level"] == "major"]
    assert len(major) == 1
    assert major[0]["index_old"] == 1
    assert {w["chapter_index"] for w in res["read_plan"]} == {1}


def test_删除章节进removed(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    new_text = _book(_chapter("第一章·码头", "甲"), _chapter("第三章·归途", "丙"))
    new = _write(tmp_path, "new.txt", new_text)

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"))
    assert len(res["removed"]) == 1
    assert "回声渊" in res["removed"][0]["title"]
    assert res["read_plan"] == []


def test_长章按max_chars分窗且覆盖整章(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    long_body = "第一1段正文。\n\n" * 8000  # 单章约 7.2 万字符，远超 20000
    new_text = _book(_OLD, "第四章·长夜\n" + long_body)
    new = _write(tmp_path, "new.txt", new_text)

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"),
                        max_chars=20000)
    wins = [w for w in res["read_plan"] if w["chapter_index"] == 4]
    assert len(wins) >= 3
    added = [a for a in res["added"] if a["index_new"] == 4][0]
    for w in wins:
        assert w["max_chars"] <= 20000
    assert wins[0]["start"] == added["start"]
    tail = wins[-1]["start"] + wins[-1]["max_chars"]
    assert tail >= added["start"] + added["chars"] - 1


def test_新章人名进候选_旧章既有名不进(tmp_path):
    old_text = "第一章·码头\n" + "戴茂说道：走吧。\n\n" * 3
    old = _write(tmp_path, "old.txt", old_text)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    new_chunk = ("\n第四章·新人\n" + "苏晚晚说道：初次见面。\n\n"
                 + "苏晚晚说道：别来无恙。\n\n苏晚晚说道：后会有期。\n")
    new = _write(tmp_path, "new.txt", old_text + new_chunk)

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"))
    names = {row[0]: (row[1], row[2]) for row in res["name_delta"]}
    assert "苏晚晚" in names and names["苏晚晚"][1] == 0
    assert "戴茂" not in names  # 只在未变章节出现 → 不算新名/更名候选


def test_旧索引缺失必须报可操作错误(tmp_path):
    new = _write(tmp_path, "new.txt", _OLD)
    with pytest.raises(ValueError, match="novel.index"):
        ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "nope.index.json"))


def test_无基准直接拒绝(tmp_path):
    new = _write(tmp_path, "new.txt", _OLD)
    with pytest.raises(ValueError, match="old_index"):
        ch.novel_diff(str(new))


def test_给旧全文也能对比(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    new = _write(tmp_path, "new.txt", _book(_OLD, _chapter("第四章·新婚", "丁")))
    res = ch.novel_diff(str(new), old_txt=str(old))
    assert len(res["unchanged"]) == 3
    assert len(res["added"]) == 1


# ── 注册面 ───────────────────────────────────────────────────────────────────


def test_新增能力已注册且在交付白名单里():
    for op in ("novel.index", "novel.diff", "worldbook.coverage", "worldbook.delete_entries"):
        cap = cr.get(op)
        assert cap is not None, f"{op} 未注册"
        assert cap.handler, f"{op} 缺 handler"
        assert op in cr.FABRIC_DOC_DELIVERY_OPS, f"{op} 不在交付能力面，模型看不见"
    assert "old_index" in (cr.get("novel.diff").params_schema or {}).get("properties", {})
    assert "source_chapters" in (cr.get("worldbook.upsert_repo").params_schema or {}).get(
        "properties", {})


# ── T5 范围落盘 + 读拦截 ─────────────────────────────────────────────────────


def test_diff带work_dir落范围文件(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    new = _write(tmp_path, "new.txt", _book(_OLD, _chapter("第四章·新婚", "丁")))

    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"),
                        work_dir=str(tmp_path))
    scope_file = tmp_path / "_prep" / "novel.scope.json"
    assert scope_file.is_file()
    assert res["scope_path"].endswith("novel.scope.json")
    scope = json.loads(scope_file.read_text(encoding="utf-8"))
    assert len(scope["unchanged_chapters"]) == 3
    assert scope["read_plan"]  # 新增章节的阅读窗口
    assert scope["new_source"] == str(new)


def test_scope只拦整段落在未变章节内的读(tmp_path):
    p = _write(tmp_path, "new.txt", "x")
    other = _write(tmp_path, "other.txt", "y")
    scope = {"new_source": str(p),
             "unchanged_chapters": [{"index": 1, "title": "第一章", "start": 0, "chars": 1000}]}
    assert nt.scope_blocks_read(scope, str(p), 0, 900) is not None
    assert nt.scope_blocks_read(scope, str(p), 200, 900) is None   # 跨出章节边界 → 放行
    assert nt.scope_blocks_read(scope, str(other), 0, 900) is None  # 别的文件 → 放行
    assert nt.scope_blocks_read(None, str(p), 0, 900) is None
    assert nt.load_scope([str(tmp_path / "nope")]) is None


def test_读拦截_未变章节给可操作文案_变更章节放行(tmp_path):
    old = _write(tmp_path, "old.txt", _OLD)
    ch.novel_index(str(old), work_dir=str(tmp_path))
    new_text = _book(_OLD, _chapter("第四章·新婚", "丁"))
    new = _write(tmp_path, "new.txt", new_text)
    res = ch.novel_diff(str(new), old_index=str(tmp_path / "_prep" / "novel.index.json"),
                        work_dir=str(tmp_path))
    scope = json.loads((tmp_path / "_prep" / "novel.scope.json").read_text(encoding="utf-8"))
    outcome = types.SimpleNamespace()

    first = scope["unchanged_chapters"][0]
    msg = fabric_loop._novel_scope_denial(outcome, str(tmp_path), "", str(new),
                                          first["start"], max(1, first["chars"] - 10))
    assert msg and "未变更章节" in msg and "read_plan" in msg

    added = res["added"][0]
    assert fabric_loop._novel_scope_denial(
        outcome, str(tmp_path), "", str(new), added["start"],
        max(1, added["chars"] - 10)) is None
    # 没有范围文件的普通任务：一律放行
    assert fabric_loop._novel_scope_denial(
        types.SimpleNamespace(), str(tmp_path / "nope"), "", str(new), 0, 500) is None
