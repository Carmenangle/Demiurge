"""固化流程：小说转合集卡规范 §3-§4 端到端演练（2026-09-04）。

最小链路：file.read_text（小说）→ doc.create_repo（卡纲）→ worldbook.upsert_repo（6 层条目）→
character.upsert_repo（主卡 first_mes 非空）→ 重读快照逐项断言 §4 验收 9 项。

不调 LLM/ComfyUI：直接用脱敏小说 fixture + 按规范手工构造的 6 层条目，模拟 LLM 转写产物。
固化02 §4 9 项验收：(1) 卡纲文档 (2) entries 容器=list (3) 6 层前缀 (4) 非常驻 keys 非空 ≤6
(5) 主角匿名 ({{user}}) (6) first_mes 非空 (7) 解析/落盘通过 (8) 角色条目含 4 锚点+好感分阶
(9) 抽样回读。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services import capability_handlers as ch
from app.services import worldbook_store


# ── fixtures ────────────────────────────────────────────────────────────────


def _setup_work(tmp_path):
    """模拟一个作品域 = tmp_path/作品 + 一份脱敏小说片段（tmp_path/小说.md）。"""
    works = tmp_path / "作品"
    works.mkdir()
    base = str(works)
    repo_id = "work"

    # 脱敏小说片段：含原主角姓名「沈栖」用于匿名断言（条目正文不得出现该名）；
    # 男主「陆沉」合法出现。
    novel = tmp_path / "novel_demo.md"
    novel.write_text(
        "# 《深海回响》节选\n\n"
        "沈栖站在码头远眺，她等的人叫陆沉，是一名 24 岁的年轻军官，出身南境望族。\n"
        "……（中间若干描写与机制铺垫）\n"
        "陆沉将军已经三个月没有归家，沈栖决定独闯敌营寻找真相。\n"
        "她穿过封锁线，踏入名为「回声渊」的禁地。\n"
        "—（节选止）\n",
        encoding="utf-8",
    )
    return {"base": base, "repo_id": repo_id, "novel": str(novel)}


def _read_entries(base, repo_id):
    snap = worldbook_store.read_repo_snapshot(base, repo_id) or {}
    return list(snap.get("entries") or [])


def _curing02_entries():
    """6 层条目样本（模拟 LLM 转写产物）；含必填锚点 + 好感分阶。

    主角匿名已按 §3.6 执行：原主角「沈栖」在转写中一律写 {{user}}（陆沉条目
    【场景·预知可改写】段即对 {{user}} 的关系定位），条目正文与 keys 不得残留原主名。
    """
    return [
        {"comment": "系统判定机制·AI 叙事核心", "constant": True,
         "keys": ["系统判定", "叙事"],
         "content": "【强制输出格式】预知式绝不写死；不剧透未来。\n"
                    "示例：回复最开头输出【状态栏】…"},
        {"comment": "全局机制·世界自转与事件登门", "constant": True,
         "keys": ["世界自转", "事件登门"],
         "content": "世界不等人。每轮自查时间/地点/出没表/事件锚点；事件有【时间】【地点】才可触发。"},
        {"comment": "全局机制·情报受限", "constant": True,
         "keys": ["情报受限"],
         "content": "获知情报须有来源，禁止上帝视角。"},
        {"comment": "世界背景·1. 势力总览", "constant": False,
         "keys": ["南境", "北疆", "王都", "回声渊"],
         "content": "南境望族 / 北疆军阀 / 王都议会 / 禁地「回声渊」速览。"},
        {"comment": "局部机制·回声渊禁地", "constant": False,
         "keys": ["回声渊", "禁地", "封锁线"],
         "content": "场所型机制：进入条件 + 内部规则。"},
        {"comment": "角色卡·陆沉", "constant": False,
         "keys": ["陆沉", "军官", "南境"],
         "content": "【人物设定】陆沉，24 岁，南境望族出身的年轻军官；沉默果断，明面冷硬、暗里克制。\n"
                    "【场景·预知可改写】与 {{user}} 的码头重逢与回声渊同路是命定交点，"
                    "预警只报逼近、不剧透过程与结局。\n"
                    "【外貌】高大剑眉，深色军装常沾海风。\n"
                    "【身材】肌肉精壮，身形挺拔。\n"
                    "【穿着】深色军官制服，袖口缀南境纹章。\n"
                    "【个体机制】【回声宿命】——与禁地「回声渊」的命定危机绑定；"
                    "好感不足则命定悲剧照走。\n"
                    "【好感分阶】-30 失联 / 20 重逢 / 55 信任 / 90 命定 / 100 独占"},
    ]


# ── §2 自由循环纪律 ──────────────────────────────────────────────────────────


def test_02_读小说_单次不超20k字符(tmp_path):
    """§2 file.read_text 分卷读（单次 ≤20k 字符）。"""
    work = _setup_work(tmp_path)
    out = ch.read_text_file(work["novel"])
    assert "陆沉" in out["text"]
    assert out["truncated"] is False


def test_02_卡纲文档落作品_docs(tmp_path):
    """§2 大工程先纲后做：doc.create_repo 把卡纲落作品 docs/，用户确认结构。"""
    work = _setup_work(tmp_path)
    out = ch.create_repo_doc(
        work["base"],
        rel_path="卡纲-深海回响.md",
        content=(
            "# 卡纲：深海回响\n\n"
            "## 章节划分\n1. 码头重逢 2. 禁地探险 3. 真相大白\n\n"
            "## 角色名单\n- 主角匿名→{{user}}\n- 陆沉（男主）\n\n"
            "## 机制取舍\n- 命运红线：-30/20/55/90/100 六档\n"
        ),
        overwrite=True,
    )
    assert (Path(work["base"]) / "docs" / "卡纲-深海回响.md").is_file()
    assert out["bytes"] > 0


# ── §3-§4 世界书快照：6 层 + 视觉画像 + 主角匿名 ─────────────────────────────


def test_02_写入6层条目_逐项断言_4验收(tmp_path):
    work = _setup_work(tmp_path)
    res = ch.upsert_repo_worldbook(
        base=work["base"], repo_id=work["repo_id"],
        entries=_curing02_entries(),
    )
    assert res["applied"] == 6

    entries = _read_entries(work["base"], work["repo_id"])
    # 验收 §4-1 entries 容器为 list
    assert isinstance(entries, list) and len(entries) == 6

    # 验收 §4-2 comment 前缀六层命名合规（保留「·」用 rpartition）
    prefixes = {(e.get("comment") or "").rpartition("·")[0] + "·"
                for e in entries if "·" in (e.get("comment") or "")}
    assert {"系统判定机制·", "全局机制·", "世界背景·", "局部机制·", "角色卡·"} <= prefixes

    # 验收 §4-3 非常驻条目 keys 非空且 ≤6；每条 key 长度 2-8
    for e in entries:
        if e.get("constant"):
            continue
        keys = e.get("keys") or e.get("key") or []
        assert 1 <= len(keys) <= 6, f"keys 越界: {e}"
        for k in keys:
            assert 2 <= len(k) <= 8, f"key 长度不合规: {k!r} in {e.get('comment')}"

    # 验收 §4-4 constant 条目仅系统判定/全局层
    for e in entries:
        if e.get("constant"):
            comment = e.get("comment") or ""
            prefix = comment.rpartition("·")[0] + "·" if "·" in comment else comment
            assert prefix in ("系统判定机制·", "全局机制·"), f"constant 越权: {e}"

    # 验收 §4-5 角色条目含 4 锚点 + 定制【好感分阶】
    roles = [e for e in entries if (e.get("comment", "").startswith("角色卡·"))]
    assert roles, "缺角色条目"
    for r in roles:
        body = r.get("content", "")
        for anchor in ("【外貌】", "【身材】", "【穿着】", "【好感分阶】"):
            assert anchor in body, f"角色 {r.get('comment')} 缺锚点 {anchor}"

    # 验收 §4-6 主角匿名：{{user}} 出现；沈栖（原作主角名）不出现；男主「陆沉」合法出现
    full = "\n".join(e.get("content", "") for e in entries)
    assert "{{user}}" in full
    assert "沈栖" not in full, "主角姓名泄漏到条目正文"
    assert "陆沉" in full

    # 验收 §4-9 抽 2-3 条重读快照核对内容与 keys（落盘一致，无漂移）
    orig = _curing02_entries()
    for i, s in enumerate(entries[:3]):
        assert s.get("content") == orig[i]["content"], f"第 {i} 条内容回读漂移"
        assert (s.get("keys") or []) == (orig[i].get("keys") or []), f"第 {i} 条 keys 回读漂移"


# ── §3.1 + §4 主卡 first_mes 非空（A 态给角色卡补开场） ─────────────────────


def test_02_主卡_upsert_first_mes_非空(tmp_path):
    work = _setup_work(tmp_path)
    card = {
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {
            "name": "陆沉",
            "description": "24 岁年轻军官，南境望族。",
            "personality": "沉默果断",
            "scenario": "王都议会与禁地之始",
            "first_mes": "——码头。雾未散，三月的潮汐裹着回声。「我回来了。」他压低声音。",
            "mes_example": "",
            "character_book": {"entries": []},
        },
    }
    ch.upsert_repo_character(base=work["base"], card=card)
    loaded = json.loads(Path(work["base"], "陆沉", "card.json").read_text(encoding="utf-8"))
    assert loaded.get("first_mes"), "first_mes 必须非空"
    assert "回来了" in loaded["first_mes"]


# ── §3.4 常驻预算（验收 §4-4 旁证） ──────────────────────────────────────────


def test_02_常驻预算_累计lt_2万字级别(tmp_path):
    """§3.3 常驻预算纪律：仅系统判定/全局层为 true 的条目累计字数控制在 2 万字符级。"""
    work = _setup_work(tmp_path)
    ch.upsert_repo_worldbook(
        base=work["base"], repo_id=work["repo_id"],
        entries=_curing02_entries(),
    )
    entries = _read_entries(work["base"], work["repo_id"])
    constant_chars = sum(len(e.get("content") or "") for e in entries if e.get("constant"))
    # 单卡预留 2 万字符上限；fixture 共 3 条常驻，应远低于 20k
    assert constant_chars > 0
    assert constant_chars <= 20000, f"常驻超出预算：{constant_chars}/20000"
