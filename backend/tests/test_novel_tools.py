"""固化02 脚本辅助层 novel_tools 单测（epub 抽取 / survey / charfacts / scan_anonymity）。

覆盖：epub spine 顺序与去标签、章节标记落盘、非 epub 拒绝；survey 章节/词频/红线；
charfacts top_n 与 anchor 两模式 + 零命中名单；scan_anonymity 主角名(三字段)/单花括号/
硬禁词/占位缺失/干净通过；与真实 handle（upsert 落盘读回）集成收尾。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services import capability_handlers as ch
from app.services import capability_registry as cr
from app.services import novel_tools as nt
from app.services import worldbook_store


# ── fixture：微型 epub（spine 乱序 + 含 script + 一个不在 spine 的文件） ──────


def _make_fake_epub(path: Path) -> Path:
    opf = (
        '<?xml version="1.0"?><package><manifest>'
        '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c3" href="ch3_extra.html" media-type="application/xhtml+xml"/>'
        "</manifest><spine>"
        '<itemref idref="c2"/><itemref idref="c1"/>'
        "</spine></package>"
    )
    files = {
        "META-INF/container.xml": "<container/>",
        "OEBPS/content.opf": opf,
        "OEBPS/ch1.xhtml": (
            "<html><body><h1>第一章 码头</h1>"
            "<p>沈栖站在码头远眺，她等的人叫陆沉。</p>"
            "<script>bad_javascript()</script>"
            "<p>陆沉说道：夜色如墨，潮声将起。</p>"
            "</body></html>"
        ),
        "OEBPS/ch2.xhtml": (
            "<html><body><h1>第二章 回声渊</h1>"
            "<p>沈栖独闯禁地，陆沉在月落前赶到。</p></body></html>"
        ),
        "OEBPS/ch3_extra.html": (
            "<html><body><p>番外：陆沉收到一封旧信。</p></body></html>"
        ),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


@pytest.fixture()
def fake_epub(tmp_path):
    return _make_fake_epub(tmp_path / "book.epub")


# ── T1 extract_epub ─────────────────────────────────────────────────────────


def test_抽取_按spine序_去script_章节标记落盘(fake_epub, tmp_path):
    out = tmp_path / "full.txt"
    res = nt.extract_epub(str(fake_epub), str(out))
    assert res["chapters"] == 3
    # spine 序：ch2 → ch1，未收录的 ch3_extra 兜底排末尾
    titles = res["titles"]
    assert "OEBPS/ch2.xhtml" in titles[0]
    assert "OEBPS/ch1.xhtml" in titles[1]
    assert "OEBPS/ch3_extra.html" in titles[2]
    assert res["path"] == str(out)
    text = out.read_text(encoding="utf-8")
    assert "===== OEBPS/ch2.xhtml =====" in text
    assert "bad_javascript" not in text  # script 已剔除
    assert "沈栖站在码头远眺" in text
    assert "番外：陆沉收到一封旧信" in text


def test_抽取_拒绝非epub与缺失文件(tmp_path):
    plain = tmp_path / "book.txt"
    plain.write_text("不是 epub", encoding="utf-8")
    with pytest.raises(ValueError, match="仅支持 .epub"):
        nt.extract_epub(str(plain), str(tmp_path / "full.txt"))
    with pytest.raises(ValueError, match="文件不存在"):
        nt.extract_epub(str(tmp_path / "nope.epub"), str(tmp_path / "full.txt"))


# ── T2 survey_fulltext ──────────────────────────────────────────────────────


def test_survey_章节词频红线(tmp_path):
    txt = tmp_path / "full.txt"
    txt.write_text(
        "===== 第1章 码头 =====\n"
        "陆沉说道：夜色如墨。沈栖站在码头远眺，等的人叫陆沉。\n"
        "陆沉说：风是从回声渊来的。\n"
        "===== 第2章 回声渊 =====\n"
        "沈栖小姐独闯禁地，陆沉在月落前赶到。她今年 16 岁。\n",
        encoding="utf-8",
    )
    res = nt.survey_fulltext(str(txt), top_names=20)
    assert res["chapter_count"] == 2
    assert res["titles"] == ["第1章 码头", "第2章 回声渊"]
    counts = dict(res["name_counts"])
    # 懒惰量词：不会把「陆沉说道」切成伪名「陆沉说」
    assert counts.get("陆沉", 0) >= 2
    assert "陆沉说" not in counts
    assert "沈栖" in counts
    assert res["redline"].get("岁", 0) >= 1
    assert res["total_chars"] > 0


def test_survey_无标记整本作单章(tmp_path):
    txt = tmp_path / "plain.txt"
    txt.write_text("没有章节标记的一段长文本，陆沉说道。", encoding="utf-8")
    res = nt.survey_fulltext(str(txt))
    assert res["chapter_count"] == 1
    assert res["titles"] == []


# ── T3 charfacts ────────────────────────────────────────────────────────────


def test_charfacts_top_n_去重_零命中报告(tmp_path):
    txt = tmp_path / "full.txt"
    para_dup = "沈栖与陆沉在码头重逢，潮声淹没告白的后半句。"
    txt.write_text(
        "===== 第1章 =====\n" + para_dup + "\n\n" + para_dup + "\n\n"
        "陆沉望着她，只说了一句「回来就好」，随即别过脸去。\n\n===== 第2章 =====\n"
        "沈栖独闯回声渊，月光把影子拉得很长。\n",
        encoding="utf-8",
    )
    out = tmp_path / "facts"
    res = nt.charfacts(str(txt), ["陆沉", "沈栖", "不存在的角色"],
                       str(out), mode="top_n", max_paras=40)
    assert res["requested"] == 3
    assert res["written"] == 2
    assert res["missing"] == ["不存在的角色"]
    lc = (out / "陆沉.txt").read_text(encoding="utf-8")
    assert lc.count("----------") >= 1  # 至少两段
    assert lc.count(para_dup) == 1      # 相邻重复段落被去重


def test_charfacts_anchor_首中末_带章节(tmp_path):
    txt = tmp_path / "full.txt"
    txt.write_text(
        "===== 第1章 =====\n开头段落，陆沉与沈栖初次相遇在码头。\n\n"
        "中间段落，陆沉在中盘抉择，沈栖守夜。\n\n===== 第2章 =====\n"
        "结尾段落，陆沉在月落前赶到禁地门口。\n",
        encoding="utf-8",
    )
    res = nt.charfacts(str(txt), ["陆沉"], str(tmp_path / "facts_a"),
                       mode="anchor")
    assert res["written"] == 1
    item = res["names"][0]
    assert item["hits"] <= 3
    body = Path(item["file"]).read_text(encoding="utf-8")
    assert "陆沉" in body
    assert "-- [" in body  # 章节锚点标注


def test_charfacts_mode非法拒绝(tmp_path):
    txt = tmp_path / "full.txt"
    txt.write_text("正文", encoding="utf-8")
    with pytest.raises(ValueError, match="mode"):
        nt.charfacts(str(txt), ["甲"], str(tmp_path / "f"), mode="random")


# ── T4 scan_anonymity ───────────────────────────────────────────────────────


def _entry(comment="角色卡·陆沉", keys=None, content=""):
    return {"comment": comment, "keys": keys or ["陆沉"], "content": content}


def test_scan_命中主角名三字段(tmp_path):
    entries = [
        _entry(content="沈栖站在码头远眺，等的人叫陆沉。"),
        _entry(comment="角色卡·沈栖", keys=["沈栖"],
               content="她与 {{user}} 命运相连。"),
    ]
    res = nt.scan_anonymity(entries, ["沈栖", "栖栖"])
    kinds = {leak["kind"] for leak in res["leaks"]}
    assert "protagonist_leak" in kinds
    assert res["passed"] is False
    fields = {leak["field"] for leak in res["leaks"] if leak["kind"] == "protagonist_leak"}
    assert {"content", "comment", "keys"} <= fields  # 三处字段全覆盖


def test_scan_单花括号与硬禁词():
    entries = [_entry(content="回复最开头输出【状态栏】，对 {user} 说…萝莉体型保留")]
    res = nt.scan_anonymity(entries, ["沈栖"])
    kinds = {leak["kind"] for leak in res["leaks"]}
    assert "single_brace_user" in kinds
    assert "hard_word" in kinds
    assert res["hard_hits"].get("萝莉", 0) >= 1
    assert res["passed"] is False


def test_scan_占位缺失是警告不阻断():
    entries = [_entry(content="陆沉 24 岁，南境望族出身，沉默果断。")]
    res = nt.scan_anonymity(entries, ["沈栖"])
    assert {leak["kind"] for leak in res["leaks"]} == {"user_placeholder_missing"}
    assert res["user_placeholder_count"] == 0
    assert res["passed"] is True  # 无主角名/硬禁词泄漏；占位缺失仅警告


def test_scan_干净条目通过():
    entries = [
        _entry(content="【人物设定】陆沉，24 岁，南境望族出身。"
                       "【场景】与 {{user}} 在码头重逢，预警只报逼近不剧透结局。"
                       "【外貌】高大剑眉。【穿着】深色军装。"),
    ]
    res = nt.scan_anonymity(entries, ["沈栖", "栖栖"])
    assert res["passed"] is True
    assert res["user_placeholder_count"] == 1
    assert res["leaks"] == []


# ── 注册 + 真实 handle 集成 ─────────────────────────────────────────────────


def test_novel_四条能力注册且handler可导入():
    ops = {cap.operation for cap in cr.all_capabilities()}
    assert {"novel.extract_epub", "novel.survey", "novel.charfacts",
            "novel.scan_anonymity"} <= ops
    assert cr.get("novel.extract_epub").category == "novel"
    assert cr.get("novel.scan_anonymity").side_effect_level == cr.SIDE_EFFECT_READONLY
    assert cr.validate_handlers() == []


def test_集成_落盘读回_扫描收尾(tmp_path):
    """extract→survey→charfacts 素材产出后：经 upsert_repo 落盘读回的条目过扫描闸门。"""
    work = tmp_path / "作品"
    work.mkdir()
    full = tmp_path / "novel_demo.full.txt"
    full.write_text(
        "===== 第1章 码头 =====\n沈栖站在码头远眺，潮声起时，她等的人叫陆沉。\n\n"
        "===== 第2章 回声渊 =====\n沈栖独闯禁地，一路无人能挡。\n",
        encoding="utf-8",
    )
    facts = nt.charfacts(str(full), ["陆沉"], str(tmp_path / "facts"),
                         mode="top_n", max_paras=10)
    assert facts["written"] == 1
    assert (tmp_path / "facts" / "陆沉.txt").is_file()

    base = str(work)
    leaky = [
        {"comment": "角色卡·陆沉", "keys": ["陆沉"], "constant": False,
         "content": "沈栖在码头等的人，24 岁年轻军官；"
                    "【外貌】高大剑眉；【好感分阶】-30 失联 / 20 重逢"},
    ]
    assert ch.upsert_repo_worldbook(base=base, repo_id="work", entries=leaky)["applied"] == 1
    snap_entries = list((worldbook_store.read_repo_snapshot(base, "work") or {}).get("entries"))
    gate = ch.novel_scan_anonymity(entries=snap_entries, protagonist_names=["沈栖"])
    assert gate["passed"] is False
    assert any(leak["kind"] == "protagonist_leak" for leak in gate["leaks"])

    clean = [
        {"comment": "角色卡·陆沉", "keys": ["陆沉"], "constant": False,
         "content": "与 {{user}} 在码头重逢的年轻军官；"
                    "【外貌】高大剑眉；【好感分阶】-30 失联 / 20 重逢"},
    ]
    assert ch.upsert_repo_worldbook(base=base, repo_id="work2", entries=clean)["applied"] == 1
    clean_entries = list(
        (worldbook_store.read_repo_snapshot(base, "work2") or {}).get("entries"))
    gate2 = ch.novel_scan_anonymity(entries=clean_entries, protagonist_names=["沈栖", "栖栖"])
    assert gate2["passed"] is True


# ── scan_anonymity 快照取数（2026-09-04）：approval 计划只给 repo_id 即可编进闸门 ──


def test_scan_anonymity_无entries时读作品世界书快照(tmp_path):
    base = str(tmp_path)
    ch.upsert_repo_worldbook(base=base, repo_id="work", entries=[
        {"comment": "角色卡·陆沉", "keys": ["陆沉"], "constant": False,
         "content": "沈栖在码头等的人；【外貌】高大剑眉"},
    ])
    # 不给 entries，给 repo_id/base → 机械读快照再扫（无 LLM 路径）
    gate = ch.novel_scan_anonymity(entries=None, protagonist_names=["沈栖"],
                                   repo_id="work", base=base)
    assert gate["passed"] is False
    assert any(leak["kind"] == "protagonist_leak" for leak in gate["leaks"])


def test_scan_anonymity_显式entries优先于快照(tmp_path):
    base = str(tmp_path)
    ch.upsert_repo_worldbook(base=base, repo_id="work", entries=[
        {"comment": "角色卡·陆沉", "keys": ["陆沉"], "content": "沈栖出现"},
    ])
    # 显式传的 entries 是干净的 → 不应读快照里的脏条目
    gate = ch.novel_scan_anonymity(
        entries=[{"comment": "角色卡·陆沉", "keys": ["陆沉"], "content": "与 {{user}} 重逢"}],
        protagonist_names=["沈栖"], repo_id="work", base=base)
    assert gate["passed"] is True


def test_scan_anonymity_无entries又无repo时拒绝(tmp_path):
    import pytest as _pytest
    with _pytest.raises(ValueError, match="世界书快照"):
        ch.novel_scan_anonymity(entries=None, protagonist_names=["沈栖"], repo_id="", base="")


def test_scan_anonymity_计划只给名单即过校验且环境注入(tmp_path):
    """approval 闸门可编排性（2026-09-04）：entries 是执行期值编不进计划，
    只给 protagonist_names 必须能过校验；submit_task 把 base/repo_id 注入。"""
    from app.services import plan_tasks, plan_validator
    from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep

    plan = GenerationPlan(
        intent="写完自查", repo_id="work",
        budgets=PlanBudgets(max_steps=2, max_gpu_tasks=0, max_llm_calls=0),
        steps=[PlanStep(id="s1", operation="novel.scan_anonymity",
                        params={"protagonist_names": ["沈栖", "栖栖"]})],
        approval_required=[],
    )
    errors = plan_validator.validate(
        plan, capabilities=cr.all_capabilities(), allowed_prefix=str(tmp_path))
    assert errors == [], errors
    sub = plan_tasks.submit_task(plan, output_dir=str(tmp_path), repo_id="work")
    assert not sub.get("deduped")
    try:
        stored = plan_tasks.get_task(sub["task_id"])
        params = stored["steps"][0]["params"]
        assert params["base"] == str(tmp_path)
        assert params["repo_id"] == "work"
    finally:
        plan_tasks.cancel_task(sub["task_id"])


# ── handler 作品域 base 泛化：work_dir 推导 _prep/ 路径（2026-09-04） ────────


def test_handler_extract_给work_dir自动落_prep_书名缺省取epub名(fake_epub, tmp_path):
    work = tmp_path / "作品"
    res = ch.novel_extract_epub(src=str(fake_epub), work_dir=str(work))
    expected = work / "_prep" / "book.full.txt"
    assert res["path"] == str(expected)
    assert expected.is_file()
    text = expected.read_text(encoding="utf-8")
    assert "===== OEBPS/ch2.xhtml =====" in text  # 内容与显式路径同构


def test_handler_extract_book_name覆盖书名(tmp_path):
    epub = _make_fake_epub(tmp_path / "源书.epub")
    work = tmp_path / "作品"
    res = ch.novel_extract_epub(src=str(epub), work_dir=str(work),
                                book_name="沈栖传")
    assert res["path"] == str(work / "_prep" / "沈栖传.full.txt")
    assert (work / "_prep" / "沈栖传.full.txt").is_file()


def test_handler_extract_显式out_txt优先于work_dir(fake_epub, tmp_path):
    work = tmp_path / "作品"
    out = tmp_path / "elsewhere" / "full.txt"
    res = ch.novel_extract_epub(src=str(fake_epub), out_txt=str(out),
                                work_dir=str(work))
    assert res["path"] == str(out)
    assert out.is_file()


def test_handler_extract_两者都不给拒绝(fake_epub):
    with pytest.raises(ValueError, match="out_txt 与 work_dir"):
        ch.novel_extract_epub(src=str(fake_epub))


def test_handler_charfacts_给work_dir自动落_prep_charfacts(tmp_path):
    txt = tmp_path / "full.txt"
    txt.write_text(
        "===== 第1章 =====\n沈栖与陆沉在码头重逢，潮声淹没告白的后半句。\n\n"
        "陆沉望着她，只说了一句「回来就好」。\n",
        encoding="utf-8",
    )
    work = tmp_path / "作品"
    res = ch.novel_charfacts(full_txt=str(txt), names=["陆沉"], work_dir=str(work))
    assert res["out_dir"] == str(work / "_prep" / "charfacts")
    assert (work / "_prep" / "charfacts" / "陆沉.txt").is_file()


def test_handler_charfacts_显式out_dir优先(tmp_path):
    txt = tmp_path / "full.txt"
    txt.write_text("沈栖与陆沉在码头重逢。\n", encoding="utf-8")
    out = tmp_path / "素材" / "facts"
    work = tmp_path / "作品"
    res = ch.novel_charfacts(full_txt=str(txt), names=["陆沉"],
                             out_dir=str(out), work_dir=str(work))
    assert res["out_dir"] == str(out)


def test_handler_charfacts_两者都不给拒绝(tmp_path):
    txt = tmp_path / "full.txt"
    txt.write_text("沈栖与陆沉在码头重逢。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="out_dir 与 work_dir"):
        ch.novel_charfacts(full_txt=str(txt), names=["陆沉"])


def test_registry_novel_schema_work_dir可选():
    extract = cr.get("novel.extract_epub")
    assert extract.params_schema["required"] == ["src"]
    assert {"out_txt", "work_dir", "book_name"} <= set(extract.params_schema["properties"])
    facts = cr.get("novel.charfacts")
    assert facts.params_schema["required"] == ["full_txt", "names"]
    assert "work_dir" in facts.params_schema["properties"]
