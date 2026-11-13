"""第 92 刀（角色底图一键定妆）特征测试：零 LLM，锁数据层口径。

用户规格（2026-11-06）原话：「角色底图在没有的情况下，但是有绑定多元数据插入的生图工作流
模版时，可以利用该工作流一键生成，生成时会**参考角色条目外貌**，使用**绑定的角色 lora 和
风格 lora**去生成」。

本文件只锁后端这一半的四条口径：
① 外貌三源定序＝世界书条目／角色卡 → 已确认外貌锚（只补未覆盖者，红线 4）→ 通用表融合，
   并把「到底哪个源给了事实」作为账本随稿交付（`appearance_origin`）；
② 定妆画面**恒第三人称、主体只有该角色**——否则预设的第一人称闸（`_first_person_narrator`
   /`_strip_narrator_from_model_view`）会把这个人从画面主体与载荷里一起剔掉，出「没有人的图」；
③ 成稿走**同一条** Profile 编译器（`image_prompt_profiles.generate_result`），不另立口径；
   外貌取不到也照常出稿（宁缺勿猜 ⇒ 不编外貌，但也不拒服务）；
④ 取景合同（第 93 刀立、第 95 刀修订）＝**全身入镜＋正面朝向镜头、双眼看向镜头＋动作从
   预设表随机取**，且这次拍的是哪条动作要一路回传（返回体 → 端点 → 面板 → Trace）。

LoRA 栈组装与提交/回收在前端（与 `illustrationWorkflowMedia` 同源），不在本文件。
"""
from __future__ import annotations

import json

import pytest

from app.routers import ai_text
from app.services import character_portrait as cp

# 中性占位名（隐私红线）：真名不得进源码与夹具。
A, B, C, D, E = "甲", "乙", "丙", "丁", "戊"


def roster_table(*rows: tuple[str, str, str, str]) -> list[dict]:
    """通用表夹具：`(姓名, 外貌特征, 穿着打扮, 性别)` 若干行。"""
    return [{
        "name": "重要角色表",
        "columns": ["姓名", "外貌特征", "穿着打扮", "性别"],
        "rows": [list(row) for row in rows],
    }]


def entry_block(name: str, text: str) -> str:
    return f"{name}：{text}"


@pytest.fixture
def isolate(monkeypatch):
    """四个事实源全部钉成「读不到」，每条用例只放开自己要断言的那一路。"""
    monkeypatch.setattr(cp.worldbook_store, "repo_visual_profiles",
                        lambda base, repo, query, **kwargs: "")
    monkeypatch.setattr(cp.worldbook_store, "repo_character_gender",
                        lambda base, repo, name: "")
    monkeypatch.setattr(cp.character_store, "repo_card_base",
                        lambda output_dir, card_name, name: "")
    monkeypatch.setattr(cp.character_store, "read_card", lambda base, name: None)
    monkeypatch.setattr(cp.table_store, "load", lambda base, repo: [])


# ── ① 外貌三源定序与来源账本 ───────────────────────────────────────────────
def test_定妆外貌_世界书条目命中记entry(isolate, monkeypatch):
    monkeypatch.setattr(cp.worldbook_store, "repo_visual_profiles",
                        lambda base, repo, query, **kwargs: entry_block(A, "【外貌】银色长发、琥珀瞳"))
    got = cp.collect_appearance("out", "repo-1", A)
    assert got["source"] == "entry"
    assert "银色长发" in got["appearance"]


def test_定妆外貌_角色卡模式读卡正文(isolate, monkeypatch):
    monkeypatch.setattr(cp.character_store, "repo_card_base",
                        lambda output_dir, card_name, name: "snap-dir")
    monkeypatch.setattr(cp.character_store, "read_card",
                        lambda base, name: {"description": "银发及腰，琥珀色眼瞳"})
    got = cp.collect_appearance("out", "repo-1", A, appearance_source="character_card")
    assert got["source"] == "entry"
    assert "银发及腰" in got["appearance"]


def test_定妆外貌_条目缺失时表格兜底记table(isolate, monkeypatch):
    monkeypatch.setattr(cp.table_store, "load",
                        lambda base, repo: roster_table((B, "红色双马尾", "白色连衣裙", "女")))
    got = cp.collect_appearance("out", "repo-1", B)
    assert got["source"] == "table"
    assert "红色双马尾" in got["appearance"] and "白色连衣裙" in got["appearance"]


def test_定妆外貌_条目与表格并存记merged并融合(isolate, monkeypatch):
    monkeypatch.setattr(cp.worldbook_store, "repo_visual_profiles",
                        lambda base, repo, query, **kwargs: entry_block(C, "【外貌】银发"))
    monkeypatch.setattr(cp.table_store, "load",
                        lambda base, repo: roster_table((C, "琥珀瞳", "黑色长风衣", "")))
    got = cp.collect_appearance("out", "repo-1", C)
    assert got["source"] == "merged"
    assert "银发" in got["appearance"]
    assert "琥珀瞳" in got["appearance"] and "黑色长风衣" in got["appearance"]


def test_定妆外貌_聊天出图只取条目忽略表格冲突(isolate, monkeypatch):
    """聊天出图的固定外貌锚不能被表格当前状态（如黑色长发）覆盖条目发色。"""
    monkeypatch.setattr(cp.worldbook_store, "repo_visual_profiles",
                        lambda base, repo, query, **kwargs: entry_block(C, "【外貌】银发"))
    monkeypatch.setattr(cp.table_store, "load",
                        lambda base, repo: roster_table((C, "黑色长发", "黑色长风衣", "")))

    got = cp.collect_appearance("out", "repo-1", C, include_table=False)

    assert got["source"] == "entry"
    assert "银发" in got["appearance"]
    assert "黑色长发" not in got["appearance"]


def test_定妆外貌_全源为空记none且不抛错(isolate):
    got = cp.collect_appearance("out", "repo-1", E)
    assert got["appearance"] == "" and got["source"] == "none" and got["gender"] == ""


def test_定妆性别_世界书条目优先通用表兜底(isolate, monkeypatch):
    monkeypatch.setattr(cp.table_store, "load",
                        lambda base, repo: roster_table((D, "", "", "女")))
    monkeypatch.setattr(cp.worldbook_store, "repo_character_gender",
                        lambda base, repo, name: "")
    assert cp.collect_appearance("out", "repo-1", D)["gender"] == "女"

    monkeypatch.setattr(cp.worldbook_store, "repo_character_gender",
                        lambda base, repo, name: "男")
    assert cp.collect_appearance("out", "repo-1", D)["gender"] == "男"


def test_定妆名字为空直接报错(isolate):
    with pytest.raises(ValueError):
        cp.collect_appearance("out", "repo-1", "  ")


# ── ② 定妆画面：恒第三人称、主体只有该角色 ────────────────────────────────
def test_定妆画面_恒第三人称且画面主体只有该角色():
    scene = cp.portrait_scene(B, entry_block(B, "【外貌】红色双马尾"), "女")
    assert scene["viewpoint"] == "third_person"
    assert scene["actors"] == [B]
    assert scene["frame_subjects"] == [B]
    assert scene["subject_genders"] == {B: "女"}
    assert scene["rating"] == "nsfw"
    assert scene["aspect_ratio"] == "9:16"
    assert B in scene["narrative"]


def test_定妆画面_性别解析不出则不写subject_genders():
    scene = cp.portrait_scene(B, entry_block(B, "【外貌】红色双马尾"))
    assert "subject_genders" not in scene


def test_定妆画面_不携带剧情动作与多主体():
    """定妆＝单人全身立绘：叙事里不得出现「高潮动作」占位，也不得引入第二人。"""
    scene = cp.portrait_scene(B, "")
    assert "高潮" not in scene["narrative"]
    assert scene["wardrobe"] == "" and scene["locale"] == ""


# ── ②′ 取景合同：全身 ＋ 回转约 20° ＋ 随机预设动作（用户 2026-11-06 追加口径）────
def test_定妆取景_全身入镜且正面看向镜头():
    """第 95 刀修订：用户判「你不如就正常说看向镜头就行」。

    原「身体回转约 20°、隐约侧身与背身」会与动作表里的「回望」叠加，触发编译器的
    【机位由内容反推】选 `three-quarter back view`——真机成稿就是背身（2026-11-06 实锤）。
    ⚠ 反向锁：旧合同写死「取景到腰部以上」⇒ 底图只有半身，正是第 92 刀拆掉的形态。
    """
    narrative = cp.portrait_scene(B, "")["narrative"]
    for want in ("从头顶到脚底", "含鞋子", "正面朝向镜头", "看向镜头"):
        assert want in narrative
    for banned in ("回转约 20°", "侧身轮廓", "背身轮廓", "腰部以上"):
        assert banned not in narrative


def test_定妆动作_表中不得再有背向姿势():
    """反向锁：动作表是随机抽样，留一条背向姿势就等于随机把机位推成背身。"""
    assert cp._PORTRAIT_POSES
    for pose in cp._PORTRAIT_POSES:
        for banned in ("回望", "背在身后", "侧后", "背身"):
            assert banned not in pose
        assert "看向镜头" in cp.portrait_scene(B, "", pose=pose)["narrative"]


def test_定妆动作_未给时随机取自预设表():
    """「动作你就随机预设几个动作」⇒ 只从表里取，且不是一条走到底（再点一次换姿态）。"""
    picks = [cp._pick_pose() for _ in range(40)]  # noqa: SLF001 — 随机性只能靠多次抽样判
    assert all(p in cp._PORTRAIT_POSES for p in picks)
    assert len(set(picks)) > 1
    assert all(p not in cp._PORTRAIT_FRAMING for p in cp._PORTRAIT_POSES)


def test_定妆动作_显式给定则原样使用不随机():
    assert cp._pick_pose("  双手垂在身侧  ") == "双手垂在身侧"
    scene = cp.portrait_scene(B, "", pose="双手垂在身侧")
    assert "动作：双手垂在身侧。" in scene["narrative"]


def test_定妆成稿_动作只定一次并回传给面板(isolate, monkeypatch):
    """`generate_portrait` 先定条再下传：返回的 `pose` 必须与 scene 里那句**逐字相同**，
    否则面板回显「这次拍的是哪个动作」与实际提交出去的提示词对不上账。"""
    seen: dict[str, object] = {}

    def fake_generate_result(profile, scene, generate_text, **kwargs):
        seen["scene"] = scene
        return {"prompt": "1girl, solo", "negative_prompt": "", "strategy": "direct",
                "validation_errors": [], "field_ledger": {}}

    monkeypatch.setattr(cp.image_prompt_profiles, "generate_result", fake_generate_result)
    out = cp.generate_portrait("out", "repo-1", B, generate_text=lambda s, u: "x")

    assert out["pose"] in cp._PORTRAIT_POSES
    assert f"动作：{out['pose']}。" in seen["scene"]["narrative"]

    fixed = cp.generate_portrait("out", "repo-1", B, generate_text=lambda s, u: "x",
                                 pose="一手叉腰、另一手垂在身侧")
    assert fixed["pose"] == "一手叉腰、另一手垂在身侧"
    assert f"动作：{fixed['pose']}。" in seen["scene"]["narrative"]


# ── ③ 成稿走同一条编译器 ──────────────────────────────────────────────────
def test_定妆成稿_交给Profile编译器并带回来源账本(isolate, monkeypatch):
    seen: dict[str, object] = {}

    def fake_generate_result(profile, scene, generate_text, **kwargs):
        seen.update({"profile": profile, "scene": scene, "kwargs": kwargs,
                     "generate_text": generate_text})
        return {"prompt": "1girl, solo, red twin tails", "negative_prompt": "lowres",
                "strategy": "direct", "validation_errors": [], "field_ledger": {}}

    monkeypatch.setattr(cp.image_prompt_profiles, "generate_result", fake_generate_result)
    monkeypatch.setattr(cp.worldbook_store, "repo_visual_profiles",
                        lambda base, repo, query, **kwargs: entry_block(B, "【外貌】红色双马尾"))
    text = lambda system, user: "unused"  # noqa: E731 — 只验透传，不发模型
    out = cp.generate_portrait(
        "out", "repo-1", B, profile="natural_language", appearance_source="worldbook",
        aspect_ratio="3:4", rating="sfw", generate_text=text,
        overrides={"system_override": "SYS"},
    )

    assert seen["profile"] == "natural_language"
    assert seen["generate_text"] is text
    assert seen["kwargs"] == {"system_override": "SYS"}
    assert seen["scene"]["viewpoint"] == "third_person"
    assert seen["scene"]["aspect_ratio"] == "3:4"
    assert out["prompt"] == "1girl, solo, red twin tails"
    assert out["name"] == B
    assert out["appearance_origin"] == "entry"


def test_定妆外貌为空照常出稿(isolate, monkeypatch):
    """宁缺勿猜：三源全空 ⇒ 不编外貌，但服务照常出稿（不掉错误、不 500）。"""
    monkeypatch.setattr(cp.image_prompt_profiles, "generate_result",
                        lambda profile, scene, generate_text, **kwargs: {
                            "prompt": "1girl, solo", "negative_prompt": "",
                            "strategy": "direct", "validation_errors": [], "field_ledger": {}})
    out = cp.generate_portrait("out", "repo-1", E, generate_text=lambda s, u: "x")
    assert out["appearance_origin"] == "none"
    assert out["appearance"] == ""
    assert out["prompt"] == "1girl, solo"


def test_定妆携带当前防拦截预设_无预设时逐字节不包一层(isolate, monkeypatch, tmp_path):
    """与 `/prompt/profile` 同口径：预设只在给了 preset_dir+name 时才拼，空 ⇒ system 原样。"""
    from app.services import preset_store

    seen: dict[str, str] = {}

    def fake_generate_result(profile, scene, generate_text, **kwargs):
        seen["system"] = generate_text("SYS", "USER")
        return {"prompt": "1girl, solo", "negative_prompt": "", "strategy": "direct",
                "validation_errors": [], "field_ledger": {}}

    monkeypatch.setattr(cp.image_prompt_profiles, "generate_result", fake_generate_result)

    cp.generate_portrait("out", "repo-1", B, generate_text=lambda system, _user: system)
    assert seen["system"] == "SYS"

    preset_store.save(str(tmp_path), "guard", {
        "prompts": [{"identifier": "guard", "role": "system", "content": "防拦截规则生效"}],
        "prompt_order": [{"order": [{"identifier": "guard", "enabled": True}]}],
    })
    cp.generate_portrait("out", "repo-1", B, generate_text=lambda system, _user: system,
                         preset_dir=str(tmp_path), preset_name="guard", user_name="我")
    assert "防拦截规则生效" in seen["system"]
    assert "内部生图提示词任务" in seen["system"]


# ── 路由接缝：POST /prompt/portrait ────────────────────────────────────────
def portrait_result(name: str) -> dict:
    return {"prompt": "1girl, solo, red twin tails", "negative_prompt": "lowres, blurry",
            "strategy": "direct", "validation_errors": [], "field_ledger": {},
            "name": name, "profile": "anima_tags", "appearance": "", "gender": "",
            "appearance_origin": "entry",
            "pose": cp._PORTRAIT_POSES[0]}


def test_定妆端点_把仓库作用主与档位交给服务(monkeypatch, isolate):
    seen: dict[str, object] = {}

    def fake_generate_portrait(base, repo_id, name, **kwargs):
        seen.update({"base": base, "repo_id": repo_id, "name": name, "kwargs": kwargs})
        return portrait_result(name)

    monkeypatch.setattr(cp, "generate_portrait", fake_generate_portrait)
    out = ai_text.gen_portrait_prompt(ai_text.PortraitPromptRequest(
        output_dir="D:/works/仓库甲", repo_id="repo-1", name=B,
        profile="natural_language", appearance_source="character_card",
        aspect_ratio="3:4", rating="sfw", pose="双手在身前轻轻交握、指尖自然相扣",
        base_url="b", api_key="k", model="m", proxy="chat-proxy",
    ))

    assert (seen["base"], seen["repo_id"], seen["name"]) == ("D:/works/仓库甲", "repo-1", B)
    assert seen["kwargs"]["profile"] == "natural_language"
    assert seen["kwargs"]["appearance_source"] == "character_card"
    assert seen["kwargs"]["aspect_ratio"] == "3:4" and seen["kwargs"]["rating"] == "sfw"
    assert seen["kwargs"]["pose"] == "双手在身前轻轻交握、指尖自然相扣"
    assert out["appearance_origin"] == "entry" and out["profile"] == "anima_tags"
    assert out["pose"] == cp._PORTRAIT_POSES[0]


def test_定妆端点_自定义生图模版走同一套override(monkeypatch, isolate):
    seen: dict[str, object] = {}
    monkeypatch.setattr(ai_text, "_resolve_image_template",
                        lambda profile: ("anima_tags", {"id": "tpl-9"}, "custom:tpl-9"))
    monkeypatch.setattr(ai_text, "_image_template_overrides",
                        lambda kind, record: {"system_override": "SYS"})

    def fake_generate_portrait(base, repo_id, name, **kwargs):
        seen.update(kwargs)
        return portrait_result(name)

    monkeypatch.setattr(cp, "generate_portrait", fake_generate_portrait)
    ai_text.gen_portrait_prompt(ai_text.PortraitPromptRequest(
        output_dir="out", repo_id="repo-1", name=B, profile="custom:tpl-9",
        base_url="b", api_key="k", model="m",
    ))

    assert seen["profile"] == "anima_tags"          # 全链只认内置 kind
    assert seen["overrides"] == {"system_override": "SYS"}
    assert callable(seen["generate_text"])


def test_定妆端点_名字为空报400(monkeypatch, isolate):
    with pytest.raises(Exception) as exc:
        ai_text.gen_portrait_prompt(ai_text.PortraitPromptRequest(
            output_dir="out", repo_id="repo-1", name="  ",
            base_url="b", api_key="k", model="m",
        ))
    assert getattr(exc.value, "status_code", None) == 400


def test_定妆端点_成稿trace带策略与来源而不带正文(monkeypatch, isolate):
    from app.services import run_trace

    events: list[dict] = []
    monkeypatch.setattr(run_trace, "emit",
                        lambda ctx, event, **fields: events.append(
                            {"event": event, "ctx": ctx, **fields}))
    monkeypatch.setattr(cp.worldbook_store, "repo_visual_profiles",
                        lambda base, repo, query, **kwargs: entry_block(B, "【外貌】红色双马尾"))
    monkeypatch.setattr(cp.image_prompt_profiles, "generate_result",
                        lambda profile, scene, generate_text, **kwargs: {
                            "prompt": "1girl, solo", "negative_prompt": "lowres",
                            "strategy": "repaired", "validation_errors": ["人数词不符"],
                            "field_ledger": {}})

    ai_text.gen_portrait_prompt(ai_text.PortraitPromptRequest(
        output_dir="out", repo_id="repo-1", name=B, base_url="b", api_key="k", model="m",
    ))

    event = events[0]
    assert event["event"] == "illustration.portrait"
    assert event["ctx"]["repo_id"] == "repo-1" and event["name"] == B
    assert event["strategy"] == "repaired" and event["appearance_origin"] == "entry"
    # 随机动作的归因量：记的是预设表里的固定措辞（可枚举），不是外貌正文。
    assert event["pose"] in cp._PORTRAIT_POSES
    assert event["prompt_chars"] == len("1girl, solo")
    assert "红色双马尾" not in json.dumps(event, ensure_ascii=False)
