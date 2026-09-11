"""固化02 脚本辅助层 novel_tools 单测（epub 抽取 / survey / charfacts）。

覆盖：epub spine 顺序与去标签、章节标记落盘、非 epub 拒绝；survey 章节/词频；
charfacts top_n 与 anchor 两模式 + 零命中名单；与真实 handle（upsert 落盘读回）集成收尾。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services import capability_handlers as ch
from app.services import capability_registry as cr
from app.services import novel_tools as nt


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
    assert res["total_chars"] > 0





def test_survey_候选名清洗_虚词碎片丢弃(tmp_path):
    """2026-09-06 实锤修复：《玫瑰与繁花》name_counts 里「我知/或者/怎么/不知/
    她知/你知/你在/要知/谁知」全是「X+道/说」句式碎片，必须丢弃。"""
    txt = tmp_path / "noise.txt"
    txt.write_text(
        "我知道这件事。或者说换个说法。怎么说呢。她不知去向。你知我在。"
        "你在哪里。要知如此。谁知后来。也不知过了多久。他不知真相。"
        "她也不知该如何。黛绮丝公主说：安苏娜小姐来了。",
        encoding="utf-8",
    )
    res = nt.survey_fulltext(str(txt), top_names=20)
    counts = dict(res["name_counts"])
    for noise in ("我知", "或者", "怎么", "不知", "她知", "你知", "你在", "要知",
                  "谁知", "也不知", "他不知", "她也不知", "知道", "不知道"):
        assert noise not in counts, f"{noise} 不应出现在候选名单"
    assert "黛绮丝" in counts  # 真实名保留
    assert "安苏娜" in counts


def test_survey_候选名清洗_动词尾裁剪归并(tmp_path):
    """「绮丝笑着说/戴茂笑着道」的 4 字候选被裁剪归并回真名「绮丝/戴茂」。"""
    txt = tmp_path / "clip.txt"
    txt.write_text(
        "绮丝笑着说：今晚月色真好。戴茂笑着道：确实。绮丝看了看远方。"
        "戴茂站在船头。绮丝回过身来。戴茂点了点头。",
        encoding="utf-8",
    )
    res = nt.survey_fulltext(str(txt), top_names=20)
    counts = dict(res["name_counts"])
    assert "绮丝" in counts
    assert "戴茂" in counts
    assert "绮丝笑着" not in counts
    assert "戴茂笑着" not in counts
    assert "绮丝笑" not in counts





def test_survey_候选名清洗_二级虚词前缀与短语(tmp_path):
    """2026-09-06 二级实锤：「是黛绮丝/对赫瑞拉/贵的公爵/茂忍不住/不敢相信/
    笑眯眯/准确/我的」等虚词前缀与动词短语残留。"""
    txt = tmp_path / "noise2.txt"
    txt.write_text(
        "是黛绮丝说道：别怕。对赫瑞拉说：来吧。贵的公爵说道：退下。"
        "茂忍不住说：等等。戴茂不敢相信道：什么？笑眯眯地开口。准确地说。"
        "我的天。戴茂疑惑道：怎么。戴茂说道：走吧。",
        encoding="utf-8",
    )
    res = nt.survey_fulltext(str(txt), top_names=30)
    counts = dict(res["name_counts"])
    for noise in ("是黛绮丝", "对赫瑞拉", "贵的公爵", "茂忍不住", "戴茂疑惑",
                  "不敢相信", "笑眯眯", "准确", "我的", "高贵"):
        assert noise not in counts, f"{noise} 不应出现在候选名单"
    # 前缀裁剪归并回真名
    assert "黛绮丝" in counts
    assert "赫瑞拉" in counts
    assert "公爵" in counts
    assert "戴茂" in counts



def test_survey_原生章节识别_标题与offset(tmp_path):
    """2026-09-07 用户定案：原生「第X章」标题识别——之前只认 extract ===== 标记导致
    chapter_count=1 实锤；现在识别「第X章·标题」，返回标题/字数/offset 供定向阅读。"""
    txt = tmp_path / "native.md"
    txt.write_text(
        ("第一章·真正的金手指\n" + "戴茂获得金手指。" * 50 + "\n"
         + "第二章·同行\n" + "安苏娜同行。" * 50 + "\n"
         + "第三章·人类的残忍\n" + "没人相信戴茂。" * 50),
        encoding="utf-8",
    )
    res = nt.survey_fulltext(str(txt), top_names=5)
    assert res["chapter_count"] >= 3
    chs = res["chapters"]
    assert chs[0]["title"].startswith("第一章")
    assert chs[0]["start"] == 0
    assert chs[1]["start"] > chs[0]["start"]  # offset 递增
    assert "第一章" in res["titles"][0]

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


def test_charfacts_章节范围裁剪防剧透(tmp_path):
    """2026-09-06：chapter_start/end 限定素材范围——未来章节的角色经历不得切进素材。"""
    txt = tmp_path / "full.txt"
    txt.write_text(
        "===== 第1章 =====\n沈栖与陆沉在码头重逢，潮声淹没告白的后半句。\n\n"
        "===== 第2章 =====\n陆沉望着她，只说了一句「回来就好」。\n\n"
        "===== 第3章 =====\n沈栖独闯回声渊，月光把影子拉得很长——她后来牺牲了。\n",
        encoding="utf-8",
    )
    # 只切第 1-2 章:第 3 章的未来剧情不进素材
    res = nt.charfacts(str(txt), ["沈栖"], str(tmp_path / "facts_c"),
                       mode="top_n", chapter_start=1, chapter_end=2)
    assert res["written"] == 1
    body = Path(res["names"][0]["file"]).read_text(encoding="utf-8")
    assert "码头重逢" in body
    assert "牺牲" not in body, "未来章节内容不应切进素材（防剧透）"
    # 非法范围拒绝
    with pytest.raises(ValueError, match="章节范围"):
        nt.charfacts(str(txt), ["沈栖"], str(tmp_path / "f2"),
                     chapter_start=5, chapter_end=9)


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



def test_check_entry_density_角色机制世界NSFW密度门禁():
    """2026-09-07：密度检查对齐 ST——角色≥2200、机制≥800、世界背景≥700、NSFW≥400。

    2026-09-07 治本（审计实锤假通过）：
    - event 判定原用「编号开头 ^数字 + 点号」，真实命名是「世界背景·1纳维茨帝国总纲」——
      旧口径从未统计到 event → 世界密度形同虚设，已改为「世界背景·」前缀；
    - NSFW 维度整个缺失（ST 玩法 400-650），已补上。
    """
    entries = [
        {"comment": "角色卡·陆沉", "content": "密" * 2300},  # ≥2200
        {"comment": "角色卡·沈栖", "content": "短" * 100},  # <2200 不达标
        {"comment": "系统判定机制·骰点", "content": "密" * 850},  # ≥800
        {"comment": "全局机制·世界自转", "content": "短" * 50},  # <800 不达标
        {"comment": "世界背景·1纳维茨帝国总纲", "content": "密" * 750},  # ≥700 真实前缀
        {"comment": "世界背景·8月狼族", "content": "短" * 30},  # <700 不达标
        {"comment": "NSFW·半神采补双修", "content": "密" * 450},  # ≥400
        {"comment": "NSFW·催情药水玩法", "content": "短" * 60},  # <400 不达标
    ]
    res = nt.check_entry_density(entries)
    assert res["total"] == 8 and res["checked"] == 8
    assert res["passed"] is False
    below = {b["comment"]: b["chars"] for b in res["below"]}
    assert "角色卡·沈栖" in below
    assert "全局机制·世界自转" in below
    assert "世界背景·8月狼族" in below
    assert "NSFW·催情药水玩法" in below
    assert "角色卡·陆沉" not in below
    assert "NSFW·半神采补双修" not in below
    # 全达标通过
    ok_entries = [
        {"comment": "角色卡·陆沉", "content": "密" * 2300},
        {"comment": "系统判定机制·骰点", "content": "密" * 850},
        {"comment": "世界背景·1纳维茨帝国总纲", "content": "密" * 750},
        {"comment": "NSFW·半神采补双修", "content": "密" * 450},
    ]
    assert nt.check_entry_density(ok_entries)["passed"] is True


def test_check_entry_density_数字编号前缀纳入event分类_默认阈值600():
    """2026-09-09 用户定案：
    - 编号条目默认下限 600（原 700）；
    - 「1. 地理·」「6. 大事件·」数字前缀与「世界背景·」同为编号层——此前数字前缀漏检。
    """
    entries = [
        {"comment": "1. 地理·巷底粉门", "content": "密" * 650},   # ≥600 达标
        {"comment": "6. 大事件·命定时间线", "content": "短" * 300},  # <600 不达标
        {"comment": "世界背景·总纲", "content": "密" * 610},       # ≥600 达标
        {"comment": "18. 妈妈娼馆·角色速览表", "content": "短" * 100},  # <600 不达标
    ]
    res = nt.check_entry_density(entries)
    assert res["event"] == 4, "数字前缀 + 世界背景· 都应计入编号层"
    assert res["passed"] is False
    below = {b["comment"]: b["chars"] for b in res["below"]}
    assert "1. 地理·巷底粉门" not in below
    assert "世界背景·总纲" not in below
    assert "6. 大事件·命定时间线" in below
    assert "18. 妈妈娼馆·角色速览表" in below
    # 阈值确认:610 ≥600 不 below,600 整也不 below
    exact = [{"comment": "6. 大事件·边界", "content": "密" * 600}]
    assert nt.check_entry_density(exact)["passed"] is True


def test_worldbook_check_density_handler_读快照(tmp_path):
    """2026-09-06：handler 缺省读作品世界书快照再查。"""
    from app.services import capability_handlers as ch

    work = tmp_path / "作品"
    work.mkdir()
    ch.upsert_repo_worldbook(base=str(work), repo_id="work", entries=[
        {"comment": "角色卡·陆沉", "keys": ["陆沉"], "constant": False,
         "content": "密" * 2300},
        {"comment": "系统判定机制·骰点", "keys": ["骰点"], "constant": True,
         "content": "密" * 850},
    ])
    res = ch.worldbook_check_density(repo_id="work", base=str(work))
    assert res["total"] == 2 and res["passed"] is True


def test_upsert_repo_worldbook_跨批同名只更新不新增(tmp_path):
    """2026-09-07 治本：同名条目跨批 upsert 只更新，不新增重复。

    实锤：旧 A 态条目 keys=[史莱姆,体质] 与新补写 keys=[史莱姆,莉露姆,魔物] 用
    严格交集漏配 → 同名条目被重复添加（审计 8 组重复）；comment 归一 + 子串匹配
    后同 comment 只更新。keys 完全不同的同名条目也靠 comment 归一兜住。
    """
    work = tmp_path / "作品"
    work.mkdir()
    # 第一批：写两条
    ch.upsert_repo_worldbook(base=str(work), repo_id="w", entries=[
        {"comment": "局部机制·史莱姆体质", "keys": ["史莱姆", "体质"], "content": "旧版" * 100},
        {"comment": "角色卡·陆沉", "keys": ["陆沉"], "content": "陆沉内容" * 200},
    ])
    # 第二批：同名史莱姆（keys 换了）+ 新条目 —— 史莱姆应更新，不新增
    out = ch.upsert_repo_worldbook(base=str(work), repo_id="w", entries=[
        {"comment": "局部机制·史莱姆体质", "keys": ["史莱姆", "莉露姆", "魔物"],
         "content": "新版" * 120},
        {"comment": "角色卡·沈栖", "keys": ["沈栖"], "content": "沈栖内容" * 200},
    ])
    assert out["applied"] == 2  # 史莱姆更新 + 沈栖新增
    from app.services import worldbook_store
    book = worldbook_store.read_repo_snapshot(str(work), "w")
    entries = book.get("entries") or []
    comments = [str(e.get("comment") or "") for e in entries]
    assert comments.count("局部机制·史莱姆体质") == 1, "同名条目跨批不得重复"
    assert comments.count("角色卡·陆沉") == 1
    assert comments.count("角色卡·沈栖") == 1
    slime = next(e for e in entries if str(e.get("comment") or "") == "局部机制·史莱姆体质")
    assert slime["content"].startswith("新版")  # 被第二批覆盖


def test_strip_meta_instructions_剥离AI渲染元指导保留设定():
    """2026-09-07 治本（用户审计实锤「AI渲染是什么」）：模型写 NSFW/机制条目时把
    【写法】【AI渲染】【情境示例】等元指导混进正文——是 AI 渲染指令不是世界观设定。
    落盘前剥离元指导段，保留【爽点】等设定段；角色卡设定段原样保留。
    """
    sample = (
        "【爽点】蛇后拉米娅以蛇尾盘绕的独特方式与戴茂相好。\n"
        "【写法】AI渲染拉米娅以蛇尾卷缠的氛围，动作强调「盘、绕、缚、吸」。\n"
        "【边界】本条目仅限成年玩家体验。\n"
        "【情境示例】月下蛇族营地，拉米娅卸下铠甲。\n"
        "【补充】角色卡·拉米娅、世界背景·7蛇族领地。"
    )
    warnings: list[str] = []
    cleaned = ch._strip_meta_instructions(sample, warnings)
    assert "AI渲染" not in cleaned
    assert "【写法】" not in cleaned
    assert "【情境示例】" not in cleaned
    assert "【爽点】" in cleaned  # 设定段保留
    assert "【边界】" in cleaned
    assert "【补充】" in cleaned
    assert warnings, "清洗应记录 warning"

    # 角色卡设定段不受影响
    role = "【人物设定】黛绮丝，半神之身。\n【性格】冷静睿智。"
    assert ch._strip_meta_instructions(role, []) == role


def test_upsert_同名历史重复合并为一条(tmp_path):
    """2026-09-07 治本：历史遗留同名多版本（旧 A 态短版+新补写长版）并存，
    upsert 命中后删除其余同名旧条目——写入路径去重，不依赖事后清理。"""
    from app.services import worldbook_store
    work = tmp_path / "作品"
    work.mkdir()
    # 先建快照，再手动塞两条同名（模拟历史遗留）
    ch.upsert_repo_worldbook(base=str(work), repo_id="w", entries=[
        {"comment": "角色卡·测试", "keys": ["测试"], "content": "初版" * 40}])
    book = worldbook_store.read_repo_snapshot(str(work), "w")
    book["entries"].append({
        "content": "旧版B" * 70, "comment": "角色卡·测试",
        "keys": ["测试", "甲"], "constant": False, "enabled": True})
    worldbook_store.save_repo_snapshot(str(work), "w", book)
    before = [e for e in book["entries"]
              if str(e.get("comment") or "") == "角色卡·测试"]
    assert len(before) == 2, "前置：两条同名已在快照"

    ch.upsert_repo_worldbook(base=str(work), repo_id="w", entries=[
        {"comment": "角色卡·测试", "keys": ["测试"], "content": "新版完整" * 100}])

    after_book = worldbook_store.read_repo_snapshot(str(work), "w")
    same = [e for e in after_book["entries"]
            if str(e.get("comment") or "") == "角色卡·测试"]
    assert len(same) == 1, "同名历史重复应合并为一条"
    assert same[0]["content"].startswith("新版完整")


def test_upsert_覆盖保护_短版不能覆盖长版(tmp_path):
    """2026-09-07 用户定案「按原文关键词扩写，防偏差」：短版重写覆盖长版是退化
    （22:08 实锤：戴茂 1945→949）。已有同名条目且新内容更短（<目标字数）→ 拒绝写入；
    无同名的新骨架允许先落盘（随后补密度）。"""
    work = tmp_path / "作品"
    work.mkdir()
    # 先写入一个达标长版
    ch.upsert_repo_worldbook(base=str(work), repo_id="w", entries=[
        {"comment": "角色卡·戴茂", "keys": ["戴茂"],
         "content": "【人物设定】戴茂穿越者。" + "细节" * 1200},  # 3600+ 字达标
    ])
    # 短版想覆盖 → 拒绝
    out = ch.upsert_repo_worldbook(base=str(work), repo_id="w", entries=[
        {"comment": "角色卡·戴茂", "keys": ["戴茂"], "content": "短版" * 300},  # 600 字 < 2200
    ])
    assert out["applied"] == 0, "短版覆盖长版必须被拒绝"
    assert any("覆盖保护" in str(w) for w in out["warnings"]), "应回填覆盖保护提示"
    from app.services import worldbook_store
    book = worldbook_store.read_repo_snapshot(str(work), "w")
    da = next(e for e in book["entries"] if str(e.get("comment") or "") == "角色卡·戴茂")
    assert "细节" in da["content"], "长版内容应保持不变"
