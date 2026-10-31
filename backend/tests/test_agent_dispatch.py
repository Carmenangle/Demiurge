"""Supervisor 分派 Interface 测试：模型拥有语义判断，代码只校验能力条件。"""
import inspect
import json
import random
import threading

import pytest

from app.services import agency, agent_context, agent_graph as ag, worldbook, worldbook_store
from app.services import agent_graph_nodes as agn  # 专家节点域（2026-10-23 方案 B 再拆）
from app.services import agent_graph_roleplay as agr  # Roleplay 回合域（2026-10-23 方案 B 再拆）
from app.services import agent_context_assembly as aga  # A 簇新属主（方案 B 拆分）
from app.services import agent_agency as aag  # B 簇新属主（方案 B 第二步之 B）
from app.services import agency_illustration_actors as aa  # 插画主体域（2026-10-23 方案 B 再拆）
from app.services import agency_scene_comfy as asc  # 插画 comfy 编译域（2026-10-23 方案 B 再拆）
from app.services import agency_maintenance as am  # 维护簇新属主（2026-10-23 方案 B 再拆）
from app.services import roleplay_agency as ra
from app.services.agent_contracts import RunContext


def _ctx(**over) -> dict:
    base = {"chat_base": "b", "chat_key": "k", "chat_model": "m"}
    base.update(over)
    return base


def test_agent_state保留流式完成标记():
    assert "_streamed_result" in ag.AgentState.__annotations__


def test_过滤外貌已知名单之外的角色段不混入():
    # P3 回归：appearance 里出现 illustration_actor_names（known）之外的角色段时，
    # 旧实现只按 known 白名单匹配段落头，导致 unknown 角色段被当成选中角色的续行保留。
    # 修复后通用段落头识别，只保留 actors 里的角色段。
    src = "苏映晚：【外貌】墨发，暗红美眸。\n陆莹纱：【外貌】红绸束发，娇俏。"
    out = ag._filter_illustration_appearance(src, ["苏映晚"], ["苏映晚"])
    assert "苏映晚" in out
    assert "陆莹纱" not in out
    assert "红绸束发" not in out


def test_正文额度只保留思考预留不再为同轮成稿追加预算():
    # 上下文合同·同轮成稿剥离：内联 profile_prompt 义务撤下后，
    # 输出预算不再为同轮成稿追加预留；正文额度 = 显式上限 + 思考/状态预留 4000。
    ctx = _ctx(
        comfy_illustrate=True,
        prompt_profile="krea2",
        builtin={"roleplay": {"maxTokens": 4000}},
    )

    assert ag._roleplay_sampling(ctx)["max_tokens"] == 8000
    assert ag._roleplay_sampling(_ctx(
        comfy_illustrate=False,
        builtin={"roleplay": {"maxTokens": 4000}},
    ))["max_tokens"] == 8000
    # 2026-08-29 验收改约：未配置 max_tokens 时必须给 ≥16000 的充裕显式上限
    # （含思考/状态预算，防正文 0 字截断；具体数值允许调优）。
    assert ag._roleplay_sampling(_ctx(comfy_illustrate=True))["max_tokens"] >= 16000


def test_预设正文额度优先并只在外追加思考预留():
    ctx = _ctx(
        comfy_illustrate=True,
        prompt_profile="anima_tags",
        builtin={"roleplay": {"maxTokens": 4000}},
        preset_sampling={"max_tokens": 600000},
    )

    assert ag._roleplay_sampling(ctx)["max_tokens"] == 604000


def test_正文额度按模型输出上限收敛min语义():
    """2026-09-04 实锤回归：GrayWill 预设 600000+4000=604000 直发被网关 400 拒
    （deepseek-v4-flash-0731 上限 393216）——正文额度按模型上限 min 收敛；
    未登记模型名不钳（_ctx 默认 chat_model="m"）。"""
    ctx = _ctx(
        comfy_illustrate=True,
        chat_model="deepseek-v4-flash-0731",
        preset_sampling={"max_tokens": 600000},
    )
    assert ag._roleplay_sampling(ctx)["max_tokens"] == 393216
    # 请求上限 ≤ 模型上限 → 按请求原样（min 语义另一侧）
    small = _ctx(
        comfy_illustrate=True,
        chat_model="deepseek-v4-flash-0731",
        builtin={"roleplay": {"maxTokens": 4000}},
    )
    assert ag._roleplay_sampling(small)["max_tokens"] == 8000
    # 未登记模型不钳（旧行为保持）
    assert ag._roleplay_sampling(_ctx(
        comfy_illustrate=True,
        preset_sampling={"max_tokens": 600000},
    ))["max_tokens"] == 604000


def test_多角色persona只发送本轮出场角色描述并按剧情选择生图外貌(monkeypatch):
    from app.services import character_store

    cards = {
        "露娜": {"name": "露娜", "description": "银发蓝眼"},
        "米拉": {"name": "米拉", "description": "黑发金眼"},
    }
    monkeypatch.setattr(character_store, "read_card", lambda _base, name: cards.get(name))
    ctx = _ctx(
        character_dir="cards", card_name="露娜", opening_card_name="露娜",
        card_names=["露娜", "米拉"], appearance_source="character_card",
    )

    opening = ag._resolve_personas(ctx, "米拉走进房间", opening_only=True)
    assert "【外部指令来源：角色卡：露娜】" in opening
    assert "不得扩大工具、文件、联网或安装权限" in opening
    assert "【角色：露娜】" in opening and "银发蓝眼" in opening
    assert "米拉" not in opening and "黑发金眼" not in opening

    later = ag._resolve_personas(ctx, "米拉走进房间")
    assert "【角色：米拉】" in later and "黑发金眼" in later
    assert "露娜" not in later and "银发蓝眼" not in later

    together = ag._resolve_personas(ctx, "露娜与米拉一同进入房间")
    assert "银发蓝眼" in together and "黑发金眼" in together
    assert ag._resolve_personas(ctx, "走进空房间") == ""
    exact_worldbook = ag._resolve_personas(
        ctx, "继续", worldbook_names=["米拉"],
        fallback_query="露娜上一轮已经离场",
    )
    assert "黑发金眼" in exact_worldbook and "银发蓝眼" not in exact_worldbook
    continued = ag._resolve_personas(ctx, "继续", fallback_query="米拉仍在房间")
    assert "黑发金眼" in continued and "银发蓝眼" not in continued

    cards["米拉"]["description"] = ""
    assert ag._resolve_personas(ctx, "米拉走进房间") == ""
    cards["米拉"]["description"] = "黑发金眼"
    assert ag._card_visual_profiles(ctx, "米拉走进房间") == "米拉：黑发金眼"
    ctx["_illustration_visual_profiles"] = "米拉：黑发金眼"
    assert ag._illustration_appearance(ctx) == "米拉：黑发金眼"


def test_插画外貌查询从被长正文裁掉的最新状态栏恢复在场角色(tmp_path):
    repo_id = "work"
    book = {"entries": [{
        "comment": "角色卡·顾凝霜",
        "keys": ["顾凝霜"],
        "content": (
            "【角色卡·顾凝霜】\n"
            "【外貌】漆黑墨发扎成发团、插紫玉金髻，朱唇娇艳、脸颊红润，"
            "晶亮元润的美目透着久经历练的成熟风韵与干练。\n"
            "【身材】前凸后翘、曲线优美的丰腴熟躯。"
        ),
    }]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])
    history = [{
        "role": "assistant",
        "content": (
            "<status>\n[所在] 破木栏旁\n[在场] 顾凝霜\n</status>\n"
            "<content>" + ("随后剧情继续推进。" * 400) + "</content>"
        ),
    }]
    ctx = _ctx(output_dir=str(tmp_path), repo_id=repo_id, history=history)

    query = ag._illustration_visual_query(ctx, "继续")
    profiles = worldbook_store.repo_visual_profiles(str(tmp_path), repo_id, query)

    assert "顾凝霜" not in agent_context.history_text(ctx)[-2000:]
    assert "顾凝霜" in query
    assert "朱唇娇艳" in profiles
    assert "晶亮元润的美目" in profiles


@pytest.mark.parametrize(
    ("history", "expected", "excluded"),
    [
        ([{"role": "assistant", "content": "露娜的开场白"}], "银发蓝眼", "黑发金眼"),
        ([{"role": "user", "content": "上一轮"}, {"role": "assistant", "content": "露娜离场"}],
         "黑发金眼", "银发蓝眼"),
    ],
)
def test_roleplay在世界书解析后按首轮或出场角色选择描述(
    monkeypatch, history, expected, excluded,
):
    from app.services import character_store

    cards = {
        "露娜": {"name": "露娜", "description": "银发蓝眼"},
        "米拉": {"name": "米拉", "description": "黑发金眼"},
    }
    captured = {}
    monkeypatch.setattr(character_store, "read_card", lambda _base, name: cards.get(name))
    def resolve_worldbook(ctx, query):
        ctx["_worldbook_character_names"] = ["米拉"]
        return "【米拉】已进入房间"

    monkeypatch.setattr(agr, "_resolve_worldbook", resolve_worldbook)

    def resolve_preset(ctx, wb, **kwargs):
        captured["persona"] = ctx.get("persona") or ""
        return [], None, False, [], []

    monkeypatch.setattr(agr, "_resolve_preset", resolve_preset)
    monkeypatch.setattr(ag._llm, "chat_messages_stream", lambda *args, **kwargs: "剧情正文")
    ctx = _card_ctx(
        opening_card_name="露娜", card_names=["露娜", "米拉"],
        history=history, persona="不应沿用的全量角色卡",
    )

    ag.roleplay_node({"user_text": "继续", "images": [], "_ctx": ctx})

    assert expected in captured["persona"]
    assert excluded not in captured["persona"]


def test_预设角色描述marker使用本轮已筛选内容(monkeypatch):
    from app.services import preset_store

    captured = {}
    monkeypatch.setattr(preset_store, "read_preset", lambda *_args: {"prompts": [{}]})
    monkeypatch.setattr(
        preset_store, "assemble_messages",
        lambda preset, markers, history: captured.update(markers) or [],
    )
    monkeypatch.setattr(preset_store, "has_history_marker", lambda _preset: False)
    monkeypatch.setattr(preset_store, "select_chains", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(
        preset_store, "sampling_params",
        lambda _preset: {"temperature": 0.8, "max_tokens": 600000},
    )
    ctx = _card_ctx(
        preset_dir="presets", preset_name="active", persona="【角色：米拉】\n黑发金眼",
        _selected_persona_names=["米拉"],
        _selected_persona_personality="冷静果断",
        _selected_persona_scenario="雨夜客栈",
        _selected_persona_examples="米拉：别出声。",
    )

    ag._resolve_preset(ctx, "", turn=2)

    assert captured["char_name"] == "米拉"
    assert ctx["_preset_sampling"]["max_tokens"] == 600000
    assert captured["char_description"] == "【角色：米拉】\n黑发金眼"
    assert captured["char_personality"] == "冷静果断"
    assert captured["scenario"] == "雨夜客栈"
    assert captured["dialogue_examples"] == "米拉：别出声。"


def test_本轮筛选角色卡分别保留预设字段(monkeypatch):
    from app.services import character_store

    monkeypatch.setattr(character_store, "read_card", lambda _base, _name: {
        "description": "黑发金眼",
        "personality": "冷静果断",
        "scenario": "雨夜客栈",
        "mes_example": "米拉：别出声。",
    })
    ctx = _card_ctx(card_names=["米拉"], opening_card_name="米拉")

    persona = ag._resolve_personas(ctx, "米拉推开门")

    assert "黑发金眼" in persona
    assert "冷静果断" in ctx["_selected_persona_personality"]
    assert "雨夜客栈" in ctx["_selected_persona_scenario"]
    assert "米拉：别出声。" in ctx["_selected_persona_examples"]


def test_主剧情世界书选择范围贯通到curator读写与trace(monkeypatch, tmp_path):
    repo_id = "work"
    book = {"entries": [
        {"content": "顾凝霜角色条目", "keys": ["顾凝霜"]},
        {"content": "未参与本轮的机制条目"},
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])
    monkeypatch.setattr(aga, "_repo_worldbook", lambda ctx: book)
    monkeypatch.setattr(worldbook, "ensure_indexed", lambda *args, **kwargs: False)
    monkeypatch.setattr(worldbook, "_retrieve", lambda *args, **kwargs: [])
    events = []
    monkeypatch.setattr(
        ag.run_trace, "emit", lambda ctx, event, **data: events.append((event, data)),
    )
    ctx = _ctx(output_dir=str(tmp_path), repo_id=repo_id, embed_model="embed")

    injected = ag._resolve_worldbook(ctx, "顾凝霜醒来")
    context_fn = ag._curator_worldbook_context_fn(ctx, repo_id)
    writer = ag._curator_worldbook_fn(ctx, repo_id)

    assert "顾凝霜角色条目" in injected
    assert ctx["_selected_worldbook_indices"] == [0]
    curator_context = context_fn("任意正文")
    assert '"index":0' in curator_context
    assert '"index":1' not in curator_context
    assert writer([
        {"op": "worldbook_update", "index": 1, "text": "越权", "evidence": "正文"},
        {"op": "worldbook_add", "text": "新事实"},
    ]) == 1
    scope = next(data for event, data in events if event == "worldbook.update_scope")
    assert scope == {"allowed_indices": [0], "rejected_indices": [1]}


def test_世界书模糊召回不激活角色卡且当前精确别名优先(monkeypatch):
    book = {"entries": [
        {"content": "帝国通用规则：夜间实行宵禁。", "constant": True},
        {"content": "露娜负责王城路线与贵族礼仪。", "keys": ["露娜"]},
        {"content": "米拉负责边境诊疗与药材鉴定。", "keys": ["米拉", "医生"]},
    ]}
    monkeypatch.setattr(aga, "_repo_worldbook", lambda ctx: book)
    monkeypatch.setattr(worldbook, "schedule_index", lambda *args, **kwargs: False)
    monkeypatch.setattr(worldbook, "_retrieve", lambda *args, **kwargs: [])
    ctx = _ctx(
        repo_id="work", card_name="露娜", opening_card_name="露娜",
        card_names=["露娜", "米拉"],
        history=[{"role": "assistant", "content": "露娜已经离场。"}],
    )

    injected = ag._resolve_worldbook(ctx, "请医生检查药材")

    assert "米拉负责边境诊疗" in injected
    assert ctx["_keyword_worldbook_indices"] == [2]
    assert ctx["_worldbook_character_names"] == ["米拉"]


def test_角色描述历史回退排除已离场角色(monkeypatch):
    from app.services import character_store

    cards = {
        "露娜": {"name": "露娜", "description": "银发蓝眼的向导"},
        "米拉": {"name": "米拉", "description": "黑发金眼的医师"},
    }
    monkeypatch.setattr(character_store, "read_card", lambda _base, name: cards.get(name))
    ctx = _ctx(
        character_dir="cards", card_name="露娜", opening_card_name="露娜",
        card_names=["露娜", "米拉"],
    )

    persona = ag._resolve_personas(
        ctx, "继续", fallback_query="米拉已经离开诊室，露娜仍留在这里。",
    )

    assert "银发蓝眼的向导" in persona
    assert "黑发金眼的医师" not in persona


def test_角色描述选择不受绑定顺序影响(monkeypatch):
    from app.services import character_store

    cards = {
        "露娜": {"description": "露娜描述"},
        "米拉": {"description": "米拉描述"},
        "诺雅": {"description": "诺雅描述"},
    }
    monkeypatch.setattr(character_store, "read_card", lambda _base, name: cards.get(name))
    for order in (["露娜", "米拉", "诺雅"], ["诺雅", "露娜", "米拉"], ["米拉", "诺雅", "露娜"]):
        ctx = _ctx(
            character_dir="cards", card_name="露娜", opening_card_name="露娜",
            card_names=order,
        )
        persona = ag._resolve_personas(ctx, "请医生检查", worldbook_names=["米拉"])
        assert "米拉描述" in persona
        assert "露娜描述" not in persona and "诺雅描述" not in persona


@pytest.mark.parametrize(
    "context",
    ["米拉没有离开诊室。", "米拉离开后又回到诊室。", "米拉仍在诊室整理药材。"],
)
def test_角色历史回退保留否定离场与重新入场(context):
    assert ag._active_fallback_names(["露娜", "米拉"], context) == ["米拉"]


def test_角色历史回退只读取最近一条AI剧情():
    ctx = _ctx(history=[
        {"role": "assistant", "content": "米拉正在诊室。"},
        {"role": "user", "content": "转场"},
        {"role": "assistant", "content": "露娜正在王城门口等待。"},
    ])
    assert ag._recent_character_context(ctx) == "露娜正在王城门口等待。"


def test_包含关系角色名使用最长非重叠匹配(monkeypatch):
    from app.services import character_store

    cards = {
        "莉亚": {"description": "短名角色"},
        "卡蜜莉亚": {"description": "完整名称角色"},
    }
    monkeypatch.setattr(character_store, "read_card", lambda _base, name: cards.get(name))
    ctx = _ctx(
        character_dir="cards", card_name="卡蜜莉亚", opening_card_name="卡蜜莉亚",
        card_names=["莉亚", "卡蜜莉亚"],
    )

    only_long = ag._resolve_personas(ctx, "卡蜜莉亚走进房间")
    both = ag._resolve_personas(ctx, "卡蜜莉亚让莉亚留在房间")

    assert "完整名称角色" in only_long and "短名角色" not in only_long
    assert "完整名称角色" in both and "短名角色" in both


def test_首次世界书索引立即发送非阻塞状态事件(monkeypatch):
    book = {"entries": [{"content": "卡蜜莉亚角色条目", "keys": ["卡蜜莉亚"]}]}
    emitted = []
    traces = []
    monkeypatch.setattr(aga, "_repo_worldbook", lambda ctx: book)
    monkeypatch.setattr(
        worldbook,
        "schedule_index",
        lambda repo_id, entries, cfg, on_initial=None, on_backfill=None: bool(
            on_initial and on_initial(len(entries))),
    )
    monkeypatch.setattr(
        worldbook,
        "assemble_selection_stable",
        lambda *args, **kwargs: worldbook.Selection("【世界设定】", [0]),
    )
    monkeypatch.setattr(
        ag.run_trace, "emit", lambda ctx, event, **data: traces.append((event, data)),
    )
    ctx = _ctx(
        repo_id="new-work", embed_model="embed", stream_sink=emitted.append,
    )

    assert ag._resolve_worldbook(ctx, "卡蜜莉亚") == "【世界设定】"
    assert emitted == [{
        "rag_status": {"state": "start", "kind": "worldbook", "count": 1},
    }]
    assert traces[-1] == (
        "worldbook.index", {"status": "started", "initial": True, "count": 1},
    )


def test_世界书激活窗口不扫描已离场的旧历史角色():
    ctx = _ctx(history=[
        {"role": "user", "content": "很久以前顾凝霜出现"},
        {"role": "assistant", "content": "顾凝霜随后离场"},
        {"role": "user", "content": "现在只和苏映晚说话"},
        {"role": "assistant", "content": "苏映晚正在配药"},
    ])

    scan = ag._worldbook_scan_text(ctx, "继续观察苏映晚")

    assert "苏映晚" in scan
    assert "顾凝霜" not in scan


def _decision(route: str, confidence: str = "high", alternatives=None) -> str:
    return json.dumps({
        "route": route,
        "confidence": confidence,
        "alternatives": alternatives or [],
    })


def _dispatch(text, *, images=None, ctx=None) -> dict:
    state = {"user_text": text, "images": images or [], "_ctx": ctx or _ctx()}
    return ag.supervisor_node(state)


def _route(text, *, images=None, ctx=None) -> str:
    return _dispatch(text, images=images, ctx=ctx)["route"]


def test_编辑模式普通请求固定直达编辑Agent且不调用主管模型():
    def should_not_call(*_args, **_kwargs):
        raise AssertionError("编辑模式不应调用 Supervisor 模型")

    # 普通排错请求（无委派意图）：编辑模式仍固定直达受限编辑 Agent，零 LLM
    assert _route("修复 scripts/a.js", ctx=_ctx(
        workspace_mode="edit", chat_fn=should_not_call, card_name="角色卡",
        character_dir="cards",
    )) == "edit"


def test_编辑模式制作类强命令放行智能编造():
    """老项目遗留边界修复（2026-09-05）：编辑模式原强制直达只读编辑 Agent，
    导致「制作合集卡/角色卡/世界书」被拦截。制作类强命令现优先走 plan。"""
    def should_not_call(*_args, **_kwargs):
        raise AssertionError("编辑模式制作类请求不应直达编辑 Agent")

    assert _route("根据这本小说制作合集卡，把全局机制、角色等内容整理好",
                  ctx=_ctx(workspace_mode="edit", chat_fn=should_not_call)) == "plan"
    assert _route("创建角色卡", ctx=_ctx(
        workspace_mode="edit", chat_fn=should_not_call)) == "plan"
    assert _route("把这张卡的内嵌世界书独立出来", ctx=_ctx(
        workspace_mode="edit", chat_fn=should_not_call)) == "plan"


def test_附件长文本不污染委派强命令判定():
    """2026-09-05 实锤：stream_multi_agent 把附件参考（可达 100k 字小说全文）追加进
    user_text，正文必含疑问词（为什么/？/问题…）触发 _QUESTION_RE 一票否决，
    导致「制作合集卡」强命令被 Supervisor LLM 误判为 edit。
    修复：强命令层用 ctx.message（剥离附件的原始指令）判定意图。"""
    instruction = "根据这本小说制作合集卡，把全局机制、系统判定机制、角色等内容整理好"
    novel = "【文件参考：小说.txt】\n" + ("第一章 真正的金手指「蠢蛋，你在干嘛？不想活了」为什么总是这样？问题到底在哪？\n" * 500)
    user_text = instruction + "\n\n" + novel  # 与 stream_multi_agent 的追加形态一致

    assert _route(user_text, ctx=_ctx(
        workspace_mode="story", message=instruction)) == "plan"
    assert _route(user_text, ctx=_ctx(
        workspace_mode="edit", message=instruction)) == "plan"
    # 无附件/无 ctx.message 时回退 text，普通对话不受影响
    assert _route("你好", ctx=_ctx()) == "answer"


def test_编辑节点把当前输入和附件交给受限Agent(monkeypatch):
    captured = {}

    def fake_run(ctx, text, images, trace):
        captured.update(ctx=ctx, text=text, images=images, trace=trace)
        return {"result_text": "done", "trace": trace}

    monkeypatch.setattr(agn.edit_agent, "run", fake_run)  # 消费方 edit_node 已迁 agent_graph_nodes（2026-10-23）
    ctx = _ctx(repo_id="work", workspace_mode="edit")
    result = ag.edit_node({
        "_ctx": ctx, "user_text": "修复 scripts/a.js", "images": ["error.png"], "trace": [],
    })

    assert result["result_text"] == "done"
    assert captured["ctx"] is ctx
    assert captured["text"] == "修复 scripts/a.js"
    assert captured["images"] == ["error.png"]


def test_每个普通轮次都由主管模型判断并收到上下文与附件状态():
    captured = {}

    def decide(*args, **kwargs):
        captured["system"] = args[3]
        captured["user"] = args[4]
        return _decision("answer")

    assert _route(
        "继续处理刚才的内容",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=decide,
            history=[{"role": "assistant", "content": "刚才确定了界面设计。"}],
        ),
    ) == "answer"
    assert "【本轮可用路由】" in captured["system"]
    assert "附件数量：1" in captured["user"]
    assert "刚才确定了界面设计" in captured["user"]


def test_主管追踪包含模型消息与最终路由(monkeypatch):
    events = []
    monkeypatch.setattr(ag.run_trace, "emit",
                        lambda ctx, event, **data: events.append((event, data)))

    assert _route("推进剧情", ctx=_ctx(
        chat_fn=lambda *args, **kwargs: json.dumps({
            "route": "answer", "confidence": "high", "alternatives": [], "scene": "dialogue",
        }),
    )) == "answer"

    request = next(data for event, data in events if event == "model.request")
    decision = next(data for event, data in events if event == "agent.completed")
    assert request["agent"] == "supervisor"
    assert request["messages"][-1]["role"] == "user"
    assert "推进剧情" in request["messages"][-1]["content"]
    assert decision["route"] == "answer" and decision["scene"] == "dialogue"


def test_主管显式原生结构化Adapter成功时不调用旧文本模型():
    calls: list[str] = []

    def native(*_args, schema, **_kwargs):
        calls.append(schema.__name__)
        return {"route": "answer", "confidence": "high", "scene": "dialogue"}

    def legacy(*_args, **_kwargs):
        raise AssertionError("原生结构化成功后不应再花一次旧文本请求")

    assert _route("普通问题", ctx=_ctx(
        structured_chat_fn=native, chat_fn=legacy,
    )) == "answer"
    assert calls == ["SupervisorDecision"]


def test_审查已有提示词的问题由主管模型分派为对话():
    captured = {}

    def decide(*args, **kwargs):
        captured["system"] = args[3]
        return _decision("answer")

    result = _route(
        "生成效果不满意，你再看看之前的提示词有什么问题，为什么输入框和拖动手柄都被吞了",
        images=["result.png"],
        ctx=_ctx(chat_fn=decide),
    )

    assert result == "answer"
    assert "审查已有提示词" in captured["system"]
    assert "根据图片产出新的提示词" in captured["system"]


def test_相同模糊文本可由主管结合语境作出不同分派():
    text = "参考这张图继续处理"
    assert _route(
        text, images=["reference.png"],
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("answer")),
    ) == "answer"
    assert _route(
        text, images=["reference.png"],
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("img2img")),
    ) == "img2img"


@pytest.mark.parametrize(("route", "images"), [
    ("answer", []),
    ("generate", []),
    ("img2img", ["reference.png"]),
    ("analyze", ["reference.png"]),
    ("video", []),
    ("inspire", []),
])
def test_主管高置信分派不再被关键词规则覆盖(route, images):
    assert _route(
        "同一段自然语言可以有不同理解",
        images=images,
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision(route)),
    ) == route


def test_主管只看到本轮结构上可用的路由():
    captured = {}

    def decide(*args, **kwargs):
        captured["available"] = args[3].split("【本轮可用路由】", 1)[1]
        return _decision("answer")

    _route(
        "处理附件",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=decide,
            agent_cfg={"tools": {"image_to_image": False, "analyze_image": True}},
        ),
    )

    assert "- answer：" in captured["available"]
    assert "- analyze：" in captured["available"]
    assert "- generate：" not in captured["available"]
    assert "- img2img：" not in captured["available"]
    assert "- tool_agent：" not in captured["available"]


def test_主管低置信时选择卡严格使用模型候选():
    result = _dispatch(
        "参考这张图处理一下",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision(
                "answer", "low", ["img2img", "analyze", "video"],
            ),
            message_id="bot-1",
            user_message_id="user-1",
        ),
    )

    assert result["route"] == "clarify"
    assert result["route_choice"]["messageId"] == "bot-1"
    assert result["route_choice"]["userMessageId"] == "user-1"
    assert [item["route"] for item in result["route_choice"]["options"]] == [
        "answer", "img2img", "analyze",
    ]


def test_选择卡过滤关闭工具与非法候选():
    result = _dispatch(
        "处理附件",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision(
                "answer", "low", ["analyze", "unknown", "img2img"],
            ),
            agent_cfg={"tools": {"image_to_image": True, "analyze_image": False}},
        ),
    )

    assert [item["route"] for item in result["route_choice"]["options"]] == [
        "answer", "img2img",
    ]


def test_低置信候选过滤后不足两个则安全回退对话():
    result = _dispatch(
        "处理附件",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision(
                "answer", "low", ["generate", "unknown"],
            ),
        ),
    )

    assert result["route"] == "answer"
    assert "route_choice" not in result


def test_模型不能选择缺少必要附件的路由():
    assert _route(
        "处理一下",
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("img2img")),
    ) == "answer"
    assert _route(
        "整理提示词",
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("analyze")),
    ) == "answer"


def test_模型不能越过Agent工具开关():
    assert _route(
        "画只猫",
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision("generate"),
            agent_cfg={"tools": {"generate_image": False}},
        ),
    ) == "answer"


def test_无MCP时工具专家不可用():
    assert _route(
        "查资料",
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("tool_agent")),
    ) == "answer"


def test_有MCP时工具专家可用():
    assert _route(
        "查资料",
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision("tool_agent"),
            has_mcp=True,
        ),
    ) == "tool_agent"


def test_用户选择可跳过主管模型但不能绕过能力条件():
    def boom(*args, **kwargs):
        raise AssertionError("显式选择不应再次调用主管模型")

    assert _route(
        "继续处理",
        images=["reference.png"],
        ctx=_ctx(forced_route="img2img", chat_fn=boom),
    ) == "img2img"
    assert _route(
        "继续处理",
        ctx=_ctx(forced_route="img2img", chat_fn=boom),
    ) == "answer"
    assert _route(
        "继续处理",
        ctx=_ctx(forced_route="unknown", chat_fn=boom),
    ) == "answer"


def test_角色卡纯文本直达roleplay且不调用主管模型():
    def boom(*args, **kwargs):
        raise AssertionError("角色卡纯文本不应重复调用主管模型")

    ctx = _ctx(
        character_dir="cards", card_name="Lyra", chat_fn=boom,
    )
    result = _dispatch("两人开始激烈争吵", ctx=ctx)

    assert result["route"] == "roleplay"
    assert ctx["scene"] == "conflict"


def test_主管模型不复用联网搜索代理():
    captured = {}

    def decide(*args, **kwargs):
        captured.update(kwargs)
        return _decision("answer")

    assert _route("普通问题", ctx=_ctx(chat_fn=decide, proxy="http://127.0.0.1:7897")) == "answer"
    assert "proxy" not in captured


def test_主管模型使用当前对话模型代理():
    captured = {}

    def decide(*args, **kwargs):
        captured.update(kwargs)
        return _decision("answer")

    assert _route("普通问题", ctx=_ctx(chat_fn=decide, chat_proxy="http://chat-proxy")) == "answer"
    assert captured["proxy"] == "http://chat-proxy"


def test_模型异常或畸形输出安全回退对话():
    def boom(*args, **kwargs):
        raise RuntimeError("上游失败")

    assert _route("模糊请求", ctx=_ctx(chat_fn=boom)) == "answer"
    assert _route("模糊请求", ctx=_ctx(
        chat_fn=lambda *args, **kwargs: "not-json",
    )) == "answer"


# ── 多轮上下文窗口与独立执行提示词 ──

def test_最近历史显式传每角色条数时各保留六条():
    """per_role 仍供剧情等内部通道显式使用；默认已废弃（2026-09-11 token 化）。"""
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(20)
    ]

    history = agent_context.recent_history("thread", per_role=6, history_override=source)

    assert len(history) == 12
    assert history[0]["content"] == "消息8"
    assert history[-1]["content"] == "消息19"
    assert sum(item["role"] == "user" for item in history) == 6
    assert sum(item["role"] == "assistant" for item in history) == 6


def test_最近历史默认不限条数():
    """2026-09-11 上下文 token 化：条数约束废弃，短消息全部入上下文（token 预算内）。"""
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(20)
    ]

    history = agent_context.recent_history("thread", history_override=source)

    assert len(history) == 20
    assert history[0]["content"] == "消息0"


def test_上下文按角色分别截取而非简单取最后十二条():
    history = [
        *[{"role": "user", "content": f"用户{i}"} for i in range(10)],
        *[{"role": "assistant", "content": f"助手{i}"} for i in range(8)],
    ]
    selected = agent_context.recent_history("thread", per_role=6, history_override=history)

    assert [item["content"] for item in selected if item["role"] == "user"] == [
        "用户4", "用户5", "用户6", "用户7", "用户8", "用户9",
    ]
    assert [item["content"] for item in selected if item["role"] == "assistant"] == [
        "助手2", "助手3", "助手4", "助手5", "助手6", "助手7",
    ]


def test_最近历史受默认两万token预算限制():
    # 2026-09-04 L2 保头保尾窗口：头部 2 条整条保留（前缀锚），中段让位给最近消息并标注压缩。
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ]

    history = agent_context.recent_history("thread", history_override=source)

    # 12 条各 3000 token → 预算 2 万放不下：保头 2 + 最近 4 条整条，中段 6 条折叠标注
    assert len(history) == 6
    assert all(len(item["content"]) == 3000 for item in history[:2])   # 头部原文
    assert history[-1]["content"] == "设" * 3000                        # 最新消息未丢
    assert all(len(item["content"]) >= 3000 for item in history[-4:])  # 最近整条（首条带标注）
    assert "已按预算压缩" in history[2]["content"]                       # 中段折叠标注
    assert sum(agent_context.estimate_tokens(item["content"]) for item in history) <= 20_000


def test_最近历史接受自定义token预算():
    # L2 语义：预算极小放不下任何整条时，只裁剪保留「最近一条」，中段全部折叠标注。
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ]

    history = agent_context.recent_history("thread", max_tokens=9_000, history_override=source)

    assert len(history) == 3                     # 保头 2 + 最近 1（裁剪）
    assert all(len(item["content"]) == 3000 for item in history[:2])
    assert history[-1]["content"].startswith("…（中间 9 条历史已按预算压缩")
    assert len(history[-1]["content"]) < 3000
    assert sum(agent_context.estimate_tokens(item["content"]) for item in history) <= 9_000


def test_最近历史token上限为0则不裁剪():
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ]

    history = agent_context.recent_history("thread", max_tokens=0, history_override=source)

    # 0=无上限：per_role 各取 6 条共 12 条，且内容全量不截断。
    assert len(history) == 12
    assert all(len(item["content"]) == 3000 for item in history)


def test_最近历史接受自定义每角色条数():
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(20)
    ]

    history = agent_context.recent_history("thread", per_role=3, history_override=source)

    assert len(history) == 6
    assert sum(item["role"] == "user" for item in history) == 3
    assert sum(item["role"] == "assistant" for item in history) == 3
    assert history[-1]["content"] == "消息19"


def test_历史预算取设置与模型窗口的较小值():
    """有效预算 = min(设置上限, 模型输入窗口 − 输出预留)（2026-09-11 token 化）。"""
    resolve = agent_context.resolve_history_budget
    assert resolve(20_000, {"openai_max_context": 32_000, "openai_max_tokens": 8_000}) == 20_000
    assert resolve(20_000, {"openai_max_context": 24_000, "openai_max_tokens": 8_000}) == 16_000
    assert resolve(20_000, {"openai_max_context": 8_000, "openai_max_tokens": 8_000}) == 20_000  # 窗口被预留吃光 → 不钳
    # 设置无限 → 只受模型窗口约束；模型未配 → 设置原样；两者皆无 → 0（不裁剪）
    assert resolve(0, {"openai_max_context": 32_000, "openai_max_tokens": 8_000}) == 24_000
    assert resolve(20_000, {}) == 20_000
    assert resolve(0, {}) == 0
    # 非法字段防御
    assert resolve(20_000, {"openai_max_context": -1}) == 20_000
    assert resolve(20_000, {"openai_max_context": "32k"}) == 20_000


def test_自动压缩达到触发百分比时旧段变摘要():
    """auto-compact（/compact 对齐）：旧段 LLM 摘要 + 近期原文保留。"""
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"设定{i}" + "细" * 800}
        for i in range(8)
    ]
    seen = {}

    def fake_summarize(old):
        seen["count"] = len(old)
        return "已确认：主角银粉渐变发色；待办：讨论场景2配色"

    history = agent_context.recent_history(
        "thread", max_tokens=4_000, history_override=source,
        compact_trigger_pct=50, summarize_fn=fake_summarize)

    assert seen["count"] >= 1
    assert history[0]["role"] == "assistant"
    assert "历史压缩摘要" in history[0]["content"]
    assert "银粉渐变发色" in history[0]["content"]
    assert history[-1]["content"].startswith("设定7")           # 近期原文保留
    assert sum(agent_context.estimate_tokens(i["content"]) for i in history) <= 4_000


def test_压缩失败退回机械裁剪():
    """fail-open：LLM 摘要返回 None → 与原保头保尾路径同款结果，绝不丢历史。"""
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ]

    history = agent_context.recent_history(
        "thread", max_tokens=9_000, history_override=source,
        compact_trigger_pct=50, summarize_fn=lambda old: None)

    assert len(history) == 3
    assert history[-1]["content"].startswith("…（中间 9 条历史已按预算压缩")


def test_压缩未达触发百分比不触发():
    source = [{"role": "user", "content": "短消息"}]

    def boom(old):
        raise AssertionError("未达触发阈值不应调用压缩")

    history = agent_context.recent_history(
        "thread", max_tokens=4_000, history_override=source,
        compact_trigger_pct=50, summarize_fn=boom)

    assert history == source


def test_agent上下文优先使用前端显式历史且不上传已删除消息(monkeypatch):
    from app.services import chat_memory

    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: [
        {"role": "assistant", "content": "已被前端删除的拒绝历史"},
    ])
    visible = [
        {"role": "user", "content": "保留的用户消息"},
        {"role": "assistant", "content": "保留的正常回复"},
    ]

    history = agent_context.recent_history("thread", history_override=visible)

    assert history == visible
    assert all("已被前端删除" not in item["content"] for item in history)


def test_agent上下文快照存在时不回退到旧checkpoint(monkeypatch):
    from app.services import chat_memory

    monkeypatch.setattr(agent_context.chat_snapshot, "load_prompt_history", lambda _thread: [])
    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: [
        {"role": "assistant", "content": "旧checkpoint拒绝历史"},
    ])

    assert agent_context.recent_history("thread") == []


def test_agent上下文仅在快照不存在时回退checkpoint(monkeypatch):
    from app.services import chat_memory

    fallback = [{"role": "assistant", "content": "checkpoint历史"}]
    monkeypatch.setattr(agent_context.chat_snapshot, "load_prompt_history", lambda _thread: None)
    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: fallback)

    assert agent_context.recent_history("thread") == fallback


def test_依赖上文的生图请求会整理成独立提示词():
    calls = []
    ctx = _ctx(
        history=[
            {"role": "user", "content": "角色是金发绿瞳的文学系大小姐。"},
            {"role": "assistant", "content": "已经确定服装使用鼠尾草绿长裙。"},
        ],
        chat_fn=lambda *args, **kwargs: calls.append((args, kwargs)) or "金发绿瞳文学系大小姐，鼠尾草绿长裙，生成全身图",
    )

    prompt = agent_context.standalone_execution_prompt(ctx, "按刚才的设定生成全身图，其他不变")

    assert prompt.startswith("金发绿瞳文学系大小姐")
    assert calls


def test_完整执行提示词不额外调用上下文整理():
    def boom(*args, **kwargs):
        raise AssertionError("完整提示词不应额外调用模型")

    text = "生成一张金发绿瞳成年女性的全身角色设定图，鼠尾草绿长裙，白色背景"
    assert agent_context.standalone_execution_prompt(_ctx(history=[], chat_fn=boom), text) == text


# ── 剧情扮演路由：关联角色卡的作品，通用对话并入 roleplay ──

def _card_ctx(**over) -> dict:
    return _ctx(character_dir="D:/cards", card_name="Lyra", **over)


def test_有卡作品的普通对话并入剧情扮演():
    # 主管判 answer，但作品关联了角色卡 → 强制并入 roleplay，保持人设
    assert _route("你好", ctx=_card_ctx(chat_fn=lambda *a, **k: _decision("answer"))) == "roleplay"


def test_无卡作品保持通用对话():
    assert _route("你好", ctx=_ctx(chat_fn=lambda *a, **k: _decision("answer"))) == "answer"


def test_有卡作品仍可分派生图():
    # 明确生图意图用零 LLM 强规则分派，不被 roleplay 吞掉
    assert _route("画一张她的立绘", ctx=_card_ctx(
        gen_base="b", gen_key="k", gen_model="m",
        chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用主管")))) == "generate"


def test_角色卡强执行规则不把出图问题误当生图命令():
    assert _route(
        "出图效果为什么不好",
        ctx=_card_ctx(gen_base="b", gen_key="k", gen_model="m",
                      chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用主管"))),
    ) == "roleplay"


def test_roleplay_不可用当无卡():
    assert not ag._route_available("roleplay", False, _ctx())
    assert ag._route_available("roleplay", False, _card_ctx())


def _decision_scene(route: str, scene: str) -> str:
    return json.dumps({"route": route, "confidence": "high", "alternatives": [], "scene": scene})


def test_场景分类写回ctx():
    # supervisor 那次调用产出的 scene 规整后写入 ctx，供 roleplay_node 选链/配图
    ctx = _card_ctx(chat_fn=lambda *a, **k: _decision_scene("roleplay", "情色"))
    state = {"user_text": "她凑近了", "images": ["reference.png"], "_ctx": ctx}
    ag.supervisor_node(state)
    assert ctx["scene"] == "nsfw"  # 中文别名归一


def test_场景缺失不写ctx():
    ctx = _card_ctx(chat_fn=lambda *a, **k: _decision("roleplay"))
    state = {"user_text": "你好", "images": ["reference.png"], "_ctx": ctx}
    ag.supervisor_node(state)
    assert "scene" not in ctx  # 无 scene 字段 → 不写，保持缺省


def test_roleplay状态块里的user宏未设人设时回退我(monkeypatch):
    # 状态块/快照常含字段名「对{{user}}态度」，它在旧 substitute 点之后才拼进 base。
    # 用户没填人设名时也不能让字面 {{user}} 漏进 system → 被模型照抄进正文。收口替换须兜到「我」。
    captured = {}

    # deps 非 None 才会走 st_block + state_instruction 拼接分支
    class _Deps:
        pass
    monkeypatch.setattr(agr, "_agency_prelude",
                        lambda ctx, text: (_Deps(), 1, 0.0, "\n\n【当前状态】\nLyra·对{{user}}态度: 好奇"))
    monkeypatch.setattr(agr, "_agency_propose", lambda ctx, deps, aff, wb, text="": ("", False))
    monkeypatch.setattr(agr, "_agency_writeback",
                        lambda ctx, deps, reply, turn, aff, lost: (reply, [], {}, {}))
    monkeypatch.setattr(agr, "_resolve_preset",
                        lambda ctx, wb, **k: ([], None, False, [], []))
    monkeypatch.setattr(agr, "_resolve_worldbook", lambda ctx, q: "")
    # 记忆检索走假件（本测只验宏替换，不碰 RAG）
    monkeypatch.setattr(ra, "recall_chronicle", lambda *a, **k: "")

    def fake_chat_stream(base, key, model, messages, on_delta, *, temperature, **kw):
        # A 方案（2026-09-04）：非流式也走 chat_messages_stream（本地聚合可取消）
        captured["system"] = messages[0]["content"]
        captured["tail"] = "\n".join(
            m["content"] for m in messages if m["role"] == "system"
        )
        captured["kwargs"] = kw
        return "好的"

    monkeypatch.setattr(ag._llm, "chat_messages_stream", fake_chat_stream)
    ctx = _card_ctx(user_name="")  # 关键：没设人设名
    ag.roleplay_node({"user_text": "你好", "images": [], "_ctx": ctx})
    # P4-roleplay（2026-09-12）：状态块（每轮变化）后置到尾部 system 消息，静态头不再夹带它。
    # 宏替换对**两段**都要生效：任一段都不得残留字面 {{user}}。
    assert "{{user}}" not in captured["system"]      # 静态头无字面残留
    assert "{{user}}" not in captured["tail"]        # 尾部动态块同样无字面残留
    assert "对我态度" in captured["tail"]             # 回退「我」（状态块在尾部）
    assert "对我态度" not in captured["system"]       # 静态头不得夹带每轮变化的块
    assert "proxy" not in captured["kwargs"]          # 联网代理不注入聊天模型


def test_roleplay把机械召回候选与预设上下文合并后只调一次主模型(monkeypatch, tmp_path):
    calls = []
    events = []
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应独立调用 Recall LLM")),
        rng=random.Random(0), state_base=str(tmp_path), curator_gate=1.0,
        index_fn=lambda text, title: None,
    )
    monkeypatch.setattr(agr, "_agency_prelude", lambda ctx, text: (deps, 1, 10.0, ""))
    monkeypatch.setattr(
        agr, "_agency_propose",
        lambda ctx, current, affinity, wb, text="": (calls.append("world") or ("", False)),
    )
    monkeypatch.setattr(agr, "_resolve_worldbook", lambda ctx, query: "世界书候选")
    monkeypatch.setattr(
        agr, "_resolve_preset",
        lambda ctx, wb, **kw: ([{"role": "system", "content": "GrayWill预设"}], None, False, [], []),
    )
    monkeypatch.setattr(agr, "_rag_recall_text", lambda *a, **k: calls.append("rag") or "作品记忆候选")
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: events.append((event, data)),
    )
    monkeypatch.setattr(
        ra, "recall_chronicle",
        lambda *a, **k: calls.append("recall_candidates") or "往事纪要候选\n作品记忆候选",
    )

    def roleplay_once(*args, **kwargs):
        messages = args[3]
        system = messages[0]["content"]
        tail_text = "\n".join(
            m["content"] for m in messages if m["role"] == "system"
        )
        # P4-roleplay（2026-09-12）：静态头（预设/骰点规则/状态更新规则）留 messages[0]；
        # 每轮变化的召回/状态/表格块后置到尾部 system → 断言改看全部 system 文本。
        assert "GrayWill预设" in system
        assert "往事纪要候选" in tail_text
        assert "作品记忆候选" in tail_text
        assert "往事纪要候选" not in system     # 静态头不得夹带每轮变化的召回块
        # 上下文合同·同轮成稿剥离：正文轮不再下发内联插画 JSON 义务与 near_generation_contract
        assert "<illustration>" not in system
        assert "<illustration>" not in tail_text
        assert "【本轮插画执行合同】" not in system
        assert "【本轮插画执行合同】" not in tail_text
        assert "<表格更新>" not in system
        assert "<表格更新>" not in tail_text
        tail_systems = [m for m in messages if m["role"] == "system"][1:]
        assert all("本轮插画执行合同" not in m["content"] for m in tail_systems)
        assert messages[-1] == {"role": "user", "content": "继续剧情"}
        calls.append("roleplay")
        return "<content>剧情正文</content>"

    monkeypatch.setattr(
        ag._llm, "chat_messages_stream",
        roleplay_once,
    )
    monkeypatch.setattr(
        am, "_table_maintenance",  # 消费方 _agency_maintenance 已迁 agency_maintenance（2026-10-23）
        lambda ctx, deps, repo_id, reply, turn: calls.append("table"), raising=False,
    )
    monkeypatch.setattr(ra, "maybe_illustrate", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: calls.append("chronicle"))
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: calls.append("curator") or 0)

    out = ag.roleplay_node({
        "user_text": "继续剧情", "images": [],
        "_ctx": _card_ctx(output_dir=str(tmp_path), repo_id="work", comfy_illustrate=True),
    })

    assert out["result_text"] == "<content>剧情正文</content>"
    ag.join_maintenance_threads()  # 维护已转后台线程：等在途维护结束后再断言顺序
    assert calls == ["world", "rag", "recall_candidates", "roleplay", "table", "chronicle", "curator"]
    assert not any(event == "model.request" and data.get("agent") == "recall" for event, data in events)


def test_roleplay动态块后置_静态头跨轮逐字节稳定(monkeypatch, tmp_path):
    """P4-roleplay（2026-09-12）：每轮变化的块（召回/状态/表格/directive）落在**尾部**
    system 消息，messages[0] 静态头跨轮逐字节稳定 —— 这是跨轮前缀缓存能命中的前提。

    红线：内容一字不少（只在尾部出现），静态头不得夹带任何动态块。
    """
    captured: list[list[dict]] = []
    recalls = iter(["往事纪要甲", "往事纪要乙"])
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
        curator_gate=1.0, index_fn=lambda text, title: None,
    )
    monkeypatch.setattr(agr, "_agency_prelude", lambda ctx, text: (deps, 1, 0.0, ""))
    monkeypatch.setattr(agr, "_agency_propose", lambda ctx, d, aff, wb, text="": ("", False))
    monkeypatch.setattr(
        agr, "_agency_writeback",
        lambda ctx, d, reply, turn, aff, lost: (reply, [], {}, {}),
    )
    monkeypatch.setattr(agr, "_resolve_preset", lambda ctx, wb, **k: ([], None, False, [], []))
    monkeypatch.setattr(agr, "_resolve_worldbook", lambda ctx, q: "世界书条目")
    monkeypatch.setattr(agr, "_rag_recall_text", lambda *a, **k: "")
    monkeypatch.setattr(agr, "_table_recall_text", lambda *a, **k: "")
    monkeypatch.setattr(ra, "recall_chronicle", lambda *a, **k: next(recalls, ""))
    monkeypatch.setattr(am, "_table_maintenance", lambda *a, **k: None, raising=False)  # 消费方已迁 agency_maintenance
    monkeypatch.setattr(ra, "maybe_illustrate", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)

    def fake_chat_stream(base, key, model, messages, on_delta, *, temperature, **kw):
        captured.append(messages)
        # 桩回复必须是**段落收束**的完整正文：剧情完整度闸（2026-09-21）对「末尾不是句末
        # 标点」的正文会再续写一轮，旧桩 `<content>正文</content>` 会让本用例的
        # 「每轮一次模型调用」前提失效（引用意图＝静态头稳定，不是闸本身）。
        return "<content>正文。</content>"

    monkeypatch.setattr(ag._llm, "chat_messages_stream", fake_chat_stream)
    ctx = _card_ctx(output_dir=str(tmp_path), repo_id="work")
    ag.roleplay_node({"user_text": "继续剧情", "images": [], "_ctx": ctx})
    ag.roleplay_node({"user_text": "再继续剧情", "images": [], "_ctx": ctx})
    ag.join_maintenance_threads()

    assert len(captured) == 2
    head1 = captured[0][0]["content"]
    head2 = captured[1][0]["content"]
    assert head1 == head2                        # 静态头跨轮逐字节稳定
    assert "往事纪要甲" not in head1              # 静态头不得夹带每轮变化的召回块
    assert "世界书条目" in head1                  # 世界书（会话内稳定）留在静态头，可贡献前缀
    tail1 = "\n".join(m["content"] for m in captured[0] if m["role"] == "system")
    tail2 = "\n".join(m["content"] for m in captured[1] if m["role"] == "system")
    assert "往事纪要甲" in tail1                  # 内容一字不少，只是移到尾部
    assert "往事纪要乙" in tail2
    assert captured[0][-1] == {"role": "user", "content": "继续剧情"}


def test_输出纪律尾注包含实测浪费源逐条禁令(monkeypatch, tmp_path):
    """2026-09-12 定稿：逐条禁令已按用户裁定移除（冗余根因在预设思考框架，另行治理）；
    尾注必须保留结构三步与思考预算原则（≤正文一半）。
    2026-09-13 泄漏修订：实现细节（前端折叠/1000-8000 字数区间）混入模型可见文本，
    被模型照抄进正文（神恩大陆 msg[4] 实证）→ 断言不得再出现，且必须带元话语硬禁令。"""
    calls = []
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(agr, "_agency_prelude", lambda ctx, text: (deps, 1, 10.0, ""))
    monkeypatch.setattr(agr, "_agency_propose", lambda ctx, cur, aff, wb, text="": ("", False))
    monkeypatch.setattr(agr, "_resolve_worldbook", lambda ctx, query: "")
    monkeypatch.setattr(
        agr, "_resolve_preset",
        lambda ctx, wb, **kw: ([{"role": "system", "content": "预设"}], None, False, [], []),
    )
    monkeypatch.setattr(agr, "_rag_recall_text", lambda *a, **k: "")
    monkeypatch.setattr(agr, "_table_recall_text", lambda *a, **k: "")
    monkeypatch.setattr(ra, "recall_chronicle", lambda *a, **k: "")
    monkeypatch.setattr(ag.run_trace, "emit", lambda ctx, event, **data: None)

    def roleplay_once(*args, **kwargs):
        calls.append(args[3])
        return "<content>正文</content>"

    monkeypatch.setattr(ag._llm, "chat_messages_stream", roleplay_once)
    monkeypatch.setattr(ra, "maybe_illustrate", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)

    out = ag.roleplay_node({
        "user_text": "继续", "images": [],
        "_ctx": _card_ctx(output_dir=str(tmp_path), repo_id="work"),
    })
    assert "扮演失败" not in (out.get("result_text") or ""), out.get("result_text")

    # chat_messages 亦被表格维护等节点复用；取带输出纪律的那次（=roleplay 主调用）
    roleplay_calls = [m for m in calls if any(
        isinstance(x, dict) and "输出纪律" in str(x.get("content")) for x in m)]
    assert roleplay_calls, f"roleplay 主调用未被捕获；共 {len(calls)} 次调用"
    tails = [m["content"] for m in roleplay_calls[0]
             if isinstance(m, dict) and m.get("role") == "system" and "输出纪律" in m["content"]]
    assert tails, "输出纪律尾注缺失"
    text = tails[0]
    for marker in ("思考预算原则", "不超过正文的一半"):
        assert marker in text, f"输出纪律缺少思考预算条款：{marker}"
    # 2026-09-13 泄漏回归锁：实现细节不进模型可见文本
    for banned in ("1000-8000", "当前预设要求", "前端折叠", "推演内思考过程"):
        assert banned not in text, f"输出纪律泄漏实现细节：{banned}"
    # 元话语硬禁令必须在场
    for marker in ("硬禁令", "质量检查", "系统提示"):
        assert marker in text, f"输出纪律缺少元话语硬禁令：{marker}"


def test_世界agent收到本轮输入与召回后的npc条目(monkeypatch, tmp_path):
    captured = {}
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )

    def consult(current, **kwargs):
        captured.update(kwargs)
        return [agency.Verdict(
            "卡蜜莉亚", agency.OUTCOME_ACCEPT, agency.DEGREE_PARTIAL, 20, 60, "普通成功",
            intent="留下幽影徽章", goal="维持对主角的长期掌控",
        )]

    monkeypatch.setattr(ra, "consult_world", consult)
    ctx = _ctx(
        chat_base="base", chat_key="key", chat_model="model",
        persona="合集空壳卡", history=[{"role": "assistant", "content": "[在场] 卡蜜莉亚"}],
    )

    directive, _ = ag._agency_propose(
        ctx, deps, None, "【角色卡·卡蜜莉亚】掌控欲极强", "委婉拒绝收养",
    )

    assert "【角色卡·卡蜜莉亚】掌控欲极强" in captured["core"]
    assert "委婉拒绝收养" in captured["scene"]
    assert captured["affinity"] is None
    assert "留下幽影徽章" in directive
    assert ctx["_agency_goal_deltas"] == [{
        "field": "叙事/卡蜜莉亚·当前目标", "op": "set",
        "value": "维持对主角的长期掌控",
        "evidence": "World Agent依据在场角色core与本轮场景推导",
    }]


def test_comfy高潮提取失败时把中文正文作为Profile场景源而非旧prompt(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    assert not hasattr(ag, "_extract_image_prompt")
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)

    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="nsfw",
            comfy_illustrate=True, persona="银发、蓝眼", history=[], proxy="",
        ),
        deps,
        "白露谷站在窗边，回头看向镜头。",
        turn=3,
        affinity=10.0,
        lost=False,
    )

    assert clean == "白露谷站在窗边，回头看向镜头。"
    assert images == []
    assert request["prompt"] == ""
    assert request["scene_spec"]["profile"] == "anima_tags"  # 2026-08-31 默认协议切 Anima
    assert request["scene_spec"]["profile_prompt"].isascii()
    assert "白露谷站在窗边，回头看向镜头" in request["scene_spec"]["narrative"]
    assert request["scene_spec"]["draft_prompt"].isascii()
    assert "白露谷" not in request["scene_spec"]["draft_prompt"]
    assert trace[-1][0] == "illustration.request"
    assert trace[-1][1]["status"] == "emitted"


def test_profile_llm_fallback_carries_guard_preset(monkeypatch, tmp_path):
    """同轮成稿被清空时，图像 Profile 的 LLM 兜底必须携带当前防拦截预设。

    这是「防拦截生效」的关键：旧实现直接掉本地模板、完全没有 LLM 参与，
    预设自然无从谈起。这里验证 system 已叠加 guard，且失败仍能回退非空模板。
    """
    from app.services import preset_store

    preset = {
        "prompts": [{"identifier": "guard", "role": "system", "content": "防拦截规则生效"}],
        "prompt_order": [{"order": [{"identifier": "guard", "enabled": True}]}],
    }
    preset_store.save(str(tmp_path), "guard", preset)
    captured = {}

    def fake_stream(base, key, model, messages, on_delta, **_kw):
        captured["system"] = str(messages[0]["content"])
        captured["user"] = str(messages[1]["content"])
        # 返回空→校验失败→内部走 deterministic，仍证明 guard 已随 system 透传
        return ""

    monkeypatch.setattr(ag._llm, "chat_messages_stream", fake_stream)
    scene = {
        "profile": "krea2",
        "narrative": "她走进房间。",
        "actors": ["她"],
        "rating": "nsfw",
    }
    ctx = _ctx(
        chat_base="b", chat_key="k", chat_model="m",
        preset_dir=str(tmp_path), preset_name="guard",
    )
    out, strategy = ag._profile_llm_fallback(ctx, scene)

    assert "防拦截规则生效" in captured["system"]
    assert out  # 校验失败后仍回退本地模板，非空
    assert strategy.startswith("llm_retargeted")


def test_profile_llm_fallback_error_carries_reason(monkeypatch):
    """插桩回归（2026-09-18）：兜底链抛异常必须把类型与消息写进 trace。

    此前 error 事件只带 status（全量 trace 里 700+ 条无原因），无法区分「超时/连接失败」
    与「上游 4xx 审核」——这正是「NSFW 生图提示词为什么总掉词表兜底」的观测缺口。
    """
    from app.services import run_trace

    def boom(*_args, **_kwargs):
        raise RuntimeError("上游返回 400：content filter")

    monkeypatch.setattr(aga._llm, "chat_messages_stream", boom)
    trace: list = []
    monkeypatch.setattr(
        run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    out, strategy = aga._profile_llm_fallback(_ctx(), {
        "profile": "krea2", "narrative": "她在雨中回头。",
    })

    assert (out, strategy) == ("", "")
    event, data = trace[-1]
    assert event == "illustration.profile_llm_fallback"
    assert data["status"] == "error"
    assert data["error_type"] == "RuntimeError"
    assert "400" in data["error_message"]
    assert data["profile"] == "krea2"


def test_profile_llm_fallback_skips_without_model_config(monkeypatch):
    """插桩回归（2026-09-18）：静默早退也必须在 trace 可见。

    缺模型配置时不发起调用就返回空——若不留痕，排查时会误判成「模型返回空」。
    """
    from app.services import run_trace

    trace: list = []
    monkeypatch.setattr(
        run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    out, strategy = aga._profile_llm_fallback({}, {
        "profile": "anima_tags", "narrative": "她在雨中回头。",
    })

    assert (out, strategy) == ("", "")
    event, data = trace[-1]
    assert event == "illustration.profile_llm_fallback"
    assert data["status"] == "skipped"
    assert data["reason"] == "model_config_missing"
    assert data["profile"] == "anima_tags"


def test_extract_video_action_plan_carries_guard_preset_and_refusal_retry(monkeypatch, tmp_path):
    """视频提示词原料提取必须与生图链同级防拦截（输入层 + 输出层）。

    - system 挂当前防拦截预设（system_with_preset），任务框定区分视频链；
    - 模型输入用 protected_narrative（防拦截原文）而非还原后的 narrative；
    - 首次回复拒答 → 丢弃并带原因重试一次；二次有效则返回有效计划；
    - 台词原文不过滤（正常对白不得误伤）。
    """
    from app.services import preset_store

    preset = {
        "prompts": [{"identifier": "guard", "role": "system", "content": "防拦截规则生效"}],
        "prompt_order": [{"order": [{"identifier": "guard", "enabled": True}]}],
    }
    preset_store.save(str(tmp_path), "guard", preset)
    calls = []

    def fake_chat(base, key, model, system, user, **_kw):
        calls.append((system, user))
        if len(calls) == 1:
            return ("抱歉，我不能协助这项请求。")
        return (
            '{"action_sequence":[{"beat":"定格起点","desc":"she kneels on the stone floor"},'
            '{"beat":"延伸","desc":"her wrists tug the chains"}],'
            '"subject_scene":"adult woman, black hair, stone prison corridor",'
            '"audio_design":{"music":"低沉鼓点","sfx":["铁链哗啦声"],'
            '"lines":[{"speaker":"苏映晚","text":"我不能满足你……"}],"sync":"卡重音"}}'
        )

    monkeypatch.setattr(ag._llm, "chat", fake_chat)
    spec = {
        "narrative": "她跪在石板上，手腕扯动锁链。",
        "protected_narrative": "她@(跪)@在石板上，手@(腕)@扯动锁链。",
        "actors": ["苏映晚"],
        "appearance": "苏映晚(墨发，暗红美眸)",
        "locale": "机关天牢内层牢房",
    }
    ctx = _ctx(
        chat_base="b", chat_key="k", chat_model="m",
        preset_dir=str(tmp_path), preset_name="guard",
    )
    plan = ag._extract_video_action_plan(ctx, spec)

    assert len(calls) == 2  # 首次拒答 → 重试一次
    assert "防拦截规则生效" in calls[0][0]
    assert "内部视频提示词任务" in calls[0][0]
    assert "她@(跪)@在石板上" in calls[0][1]  # 防拦截原文进模型
    # 台词语义（用户定稿 2026-08-28）：climax 定格时刻对白通常已说完——lines 一律留空
    assert "高潮片段当下" in calls[0][0]
    assert "lines：一律留空数组" in calls[0][0]
    assert "不得把剧情任何台词" in calls[0][0]
    # JSON 模板仍含 at_s 字段（firstlast 分支按剧情位置推算时点用）
    assert "at_s" in calls[0][0]
    assert plan["action_sequence"][0]["desc"] == "she kneels on the stone floor"
    assert plan["audio_design"]["sfx"] == ["铁链哗啦声"]
    # 台词原文不过滤（防误伤正常对白）
    assert plan["audio_design"]["lines"] == [
        {"speaker": "苏映晚", "text": "我不能满足你……"},
    ]


def test_extract_video_action_plan_duration_rule_only_in_climax(monkeypatch):
    """高潮时长由剧情动作量判定（2026-10-31）：只有 climax 协议要求 duration_s；
    首尾帧覆盖整段楼层，时长仍走模板/预设，协议里不得出现该字段。"""
    calls: list[str] = []

    def fake_chat(base, key, model, system, user, **_kw):
        calls.append(system)
        return '{"action_sequence":[{"beat":"起点","desc":"she kneels"}],"duration_s":7}'

    monkeypatch.setattr(asc._llm, "chat", fake_chat)
    ctx = _ctx()
    spec = {"narrative": "她跪在石板上。"}

    plan = asc._extract_video_action_plan(ctx, spec, video_mode="climax")
    assert plan["duration_s"] == 7
    assert '"duration_s":6' in calls[0]
    assert "duration_s：按 action_sequence 的动作量" in calls[0]

    asc._extract_video_action_plan(ctx, spec, video_mode="firstlast")
    assert "duration_s" not in calls[1]


def test_wb_frames_and_video_climax_falls_back_to_5s_when_extraction_fails(monkeypatch):
    """提取失败也必须发 5 秒，不能回落到模板旧默认 12 秒（2026-10-31 用户定案）。"""
    monkeypatch.setattr(asc, "_extract_video_action_plan", lambda *a, **k: {})
    monkeypatch.setattr(asc, "_video_skeleton_for", lambda ctx, mode: None)
    req = {"prompt": "p", "video_config": {}}
    out = asc._wb_frames_and_video(
        {"comfy_video": True, "video_mode": "climax"}, req,
        {"narrative": "她跪在石板上。"}, "", "", 2,
    )
    assert req["video_request"]["submit"]["duration"] == 5
    assert "5 seconds" in out


def test_extract_video_action_plan_firstlast_lists_all_dialogue(monkeypatch, tmp_path):
    """firstlast 模式：首尾帧影片从头到尾覆盖剧情——提取协议要求列出全部对白并标 at_s。"""
    from app.services import preset_store

    preset = {
        "prompts": [{"identifier": "guard", "role": "system", "content": "防拦截规则生效"}],
        "prompt_order": [{"order": [{"identifier": "guard", "enabled": True}]}],
    }
    preset_store.save(str(tmp_path), "guard", preset)
    calls = []

    def fake_chat(base, key, model, system, user, **_kw):
        calls.append((system, user))
        return (
            '{"action_sequence":[{"beat":"开场","desc":"she kneels on the stone floor"}],'
            '"audio_design":{"music":"低沉鼓点","sfx":["铁链哗啦声"],'
            '"lines":[{"speaker":"苏映晚","text":"放开我。","at_s":2}],'
            '"sync":"卡重音"}}'
        )

    monkeypatch.setattr(ag._llm, "chat", fake_chat)
    spec = {
        "narrative": "她跪在石板上，手腕扯动锁链。",
        "protected_narrative": "她@(跪)@在石板上，手@(腕)@扯动锁链。",
        "actors": ["苏映晚"],
    }
    ctx = _ctx(
        chat_base="b", chat_key="k", chat_model="m",
        preset_dir=str(tmp_path), preset_name="guard",
    )
    plan = ag._extract_video_action_plan(ctx, spec, video_mode="firstlast")

    # firstlast 分支：全部对白 + at_s 按剧情位置推算
    assert "从头到尾所有角色亲口说出的台词" in calls[0][0]
    assert "按剧情位置推算" in calls[0][0]
    assert "lines：一律留空数组" not in calls[0][0]
    assert plan["audio_design"]["lines"] == [
        {"speaker": "苏映晚", "text": "放开我。", "at_s": 2.0},
    ]


def test_extract_video_action_plan_重试仍拒答则回退空(monkeypatch, tmp_path):
    """两次都拒答 → 返回 {}，调用方回退纯函数兜底，不把拒答文本漏进提示词。"""
    def fake_chat(*_a, **_kw):
        return "I cannot help with this request."

    monkeypatch.setattr(ag._llm, "chat", fake_chat)
    ctx = _ctx(chat_base="b", chat_key="k", chat_model="m")
    plan = ag._extract_video_action_plan(
        ctx, {"narrative": "她跪在石板上。", "actors": ["苏映晚"]},
    )
    assert plan == {}


def test_writeback剥离transition块并透传判定_v1_5_w1(monkeypatch, tmp_path):
    """V1.5/W1：<transition> 块从正文剥离，判定值（L1 原值）随 illustrate_req 透传。"""
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: "")
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<content>雨夜，面馆门口挂起暖黄的灯笼，水珠顺着门帘滴落。\n\n"
        "纪含章推门而入，庄听澜已经坐定，朝她招手。</content>\n"
        "<transition>reuse</transition>"
    )
    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="climax",
            comfy_illustrate=True, history=[],
        ),
        deps, reply, turn=3, affinity=10.0, lost=False,
    )

    # <transition> 块已剥离，正文（含 content 内的可见叙述）保留
    assert "<transition>" not in clean
    assert "面馆" in clean
    # V1.5/W1：判定值随出图请求透传（有值才带）
    assert request.get("transition") == "reuse"


def test_writeback无transition块时回退ambiguous_v1_5_w1(monkeypatch, tmp_path):
    """V1.5/W2：主模型漏输出 <transition> 块 + 无历史 → 合并结果 ambiguous（L0 兕底，不抛错）。"""
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: "")
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="climax",
            comfy_illustrate=True, history=[],
        ),
        deps,
        "<content>雨夜面馆门口，灯笼摇晃。</content>",
        turn=3, affinity=10.0, lost=False,
    )
    assert "面馆" in clean
    # W2：合并结果三态透传——无历史（empty_input→L0 ambiguous）+ 无 L1 → ambiguous
    assert request.get("transition") == "ambiguous"


def test_writeback首帧复用合并_l0胜出忽略l1_v1_5_w2(monkeypatch, tmp_path):
    """V1.5/W2：L0 确定 reuse（上一楼尾帧与当前首段共享地点词）→ 忽略 <transition> regenerate。"""
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: "")
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<content>面馆里的暖光依旧，庄听澜抿了口汤。</content>\n"
        "<transition>regenerate</transition>"
    )
    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="climax",
            comfy_illustrate=True,
            history=[{"role": "assistant", "content": "三人围坐面馆，举杯同框。"}],
        ),
        deps, reply, turn=4, affinity=10.0, lost=False,
    )
    # L0 共享「面馆」→ reuse 胜出（L1 regenerate 被忽略）
    assert request.get("transition") == "reuse"


def test_writeback首帧复用合并_第一楼消费l1_v1_5_w2(monkeypatch, tmp_path):
    """V1.5/W2：第一楼（无上一楼尾帧）→ L0 ambiguous → 消费 <transition> regenerate。"""
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: "")
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<content>面馆里的暖光依旧，庄听澜抿了口汤。</content>\n"
        "<transition>regenerate</transition>"
    )
    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="climax",
            comfy_illustrate=True, history=[],
        ),
        deps, reply, turn=4, affinity=10.0, lost=False,
    )
    # 无历史 → prev_tail 空 → L0 empty_input ambiguous → 合并消费 L1 regenerate
    assert request.get("transition") == "regenerate"


def test_画面主体是用户时不得从状态栏借用在场角色LoRA(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: "[在场] 苏映晚")
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit", lambda ctx, event, **data: trace.append((event, data)),
    )
    reply = (
        '<content>机关天牢入口的门铁木厚实。\n\n'
        '他把宗主令贴上门侧机括，门锁开始转动。</content>\n'
        '<illustration>{"anchor":"他把宗主令贴上门侧机括，门锁开始转动。",'
        '"camera":"medium shot","composition":"hand and lock in focus",'
        '"subjects":[{"name":"你","description":"adult man in dark robes pressing a jade token against the lock","weight":1.5}],'
        '"prompt":"iron-wood prison door, jade token, mechanical lock",'
        '"profile_prompt":"An adult man in dark robes presses a jade token against the mechanical lock of an iron-wood prison door in a medium shot with directional corridor light and clear material detail.",'
        '"motion":1}</illustration>'
    )
    ctx = _ctx(
        repo_id="work", thread_id="work", card_name="作品名", scene="climax",
        comfy_illustrate=True, appearance_source="worldbook",
        illustration_actor_names=["苏映晚"], prompt_profile="krea2",
        history=[],
    )
    ctx["_illustration_visual_profiles"] = "苏映晚：【外貌】墨发，暗红美眸。"

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps, reply, turn=8, affinity=10.0, lost=False,
        user_text="去打开牢门",
    )

    assert request, trace
    assert request["actors"] == []
    assert request["scene_spec"]["appearance"] == ""


def test_comfy首个用户回合即使已有开场白也兜底生成插画(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")

    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="卡蜜莉亚",
            scene="conflict", comfy_illustrate=True, comfy_video=True,
            persona="乌黑长发，猩红眼眸，成熟丰腴的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        "卡蜜莉亚站在孤儿院门前，猩红眼眸安静地注视着他。",
        turn=2,
        affinity=10.0,
        lost=False,
        user_text="委婉拒绝收养。",
    )

    assert clean.startswith("卡蜜莉亚")
    assert images == []
    assert request["actors"] == ["卡蜜莉亚"]
    assert request["scene_spec"]["rating"] == "sfw"
    assert request["anchor"] == "卡蜜莉亚站在孤儿院门前，猩红眼眸安静地注视着他。"
    # V1.5 默认开放：produce 时即 dry-run 组装视频参数，trace 记录视频提示词全文
    expected_video_prompt = request["video_request"]["submit"]["prompt"]
    assert trace[-1] == (
        "illustration.request",
        {
                "status": "emitted", "reason": "first_story_reply",
                "scene": "conflict", "inferred_scene": "conflict",
                "actor_count": 1, "actors": ["卡蜜莉亚"],
                "actor_candidates": [], "status_actors": [],
                "plan_retargeted": False,
                "prompt_chars": len(request["prompt"]),
                "video_prompt_chars": len(expected_video_prompt),
                "video_prompt": expected_video_prompt,
                # 前置观测（2026-09-22）：人称判定与画面主体收敛结果入 trace。
                # 锚点句「…注视着他。」含第三人称代词 ⇒ `third_person`、source＝`anchor`；
                # 该场景走词表档（无判据）⇒ 画面主体名集合为空（只有判据档才填）。
                "viewpoint": "third_person",
                "viewpoint_detail": {
                    "viewpoint": "third_person", "auto": "third_person",
                    "source": "anchor", "conflict": False,
                },
                "frame_subjects": [],
                # 2026-09-22 追加观测：叙事者姓名/来路（本场景无 output_dir/人设 ⇒ 空）。
                "narrator": "", "narrator_source": "",
                # 2026-10-19 画幅可观测：锚点句含「站在」⇒ 关键词档判 2:3（竖构图全身）；
                # 本场景无插画计划（first_story_reply）⇒ source 必为 keyword_infer。
                "aspect_ratio": "2:3", "aspect_ratio_source": "keyword_infer",
            },
        )


def test_comfy_video关闭时不编译视频且不调提取LLM(monkeypatch, tmp_path):
    """三模态独立开关（对齐图/音链）：comfy_video=False → 零 LLM 提取、零 video_request、零 video trace。

    视频链此前寄生在图链上（illustrate_req 有就编译+调 _extract_video_action_plan），
    每个高潮回合干烧一次聊天模型 token。关视频开关必须彻底关闭整条链。
    """
    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    video_calls = []
    monkeypatch.setattr(
        asc, "_extract_video_action_plan",  # 消费方 _agency_writeback 已迁 agent_agency（方案 B 之 B）
        lambda *a, **k: video_calls.append(1) or {},
        raising=False,
    )
    llm_calls = []
    monkeypatch.setattr(
        ag._llm, "chat",
        lambda *a, **k: (llm_calls.append(1), '{"action_sequence":[{"beat":"定格起点","desc":"x"}]}')[1],
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))

    clean, _images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="卡蜜莉亚",
            scene="conflict", comfy_illustrate=True, comfy_video=False,
            persona="乌黑长发，猩红眼眸，成熟丰腴的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        "卡蜜莉亚站在孤儿院门前，猩红眼眸安静地注视着他。",
        turn=2, affinity=10.0, lost=False, user_text="委婉拒绝收养。",
    )
    # 上下文合同·同轮成稿剥离后：视频提取链零调用；Profile 由独立链编译（_llm.chat 允许被其调用）
    assert video_calls == []
    assert not request.get("video_request")  # 不编译 video_request
    assert request.get("actors") == ["卡蜜莉亚"]  # 图链正常：出图请求照发
    req_trace = next(d for ev, d in trace if ev == "illustration.request")
    assert req_trace["video_prompt_chars"] == 0 and req_trace["video_prompt"] == ""


def test_comfy_video开启时编译视频并调提取LLM(monkeypatch, tmp_path):
    """开关开启 = 现行为不变：_extract_video_action_plan 调用 + video_request 编译。"""
    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    llm_calls = []

    def fake_chat(*_a, **_k):
        llm_calls.append(1)
        return (
            '{"action_sequence":[{"beat":"定格起点","desc":"she stands before the gate"},'
            '{"beat":"延伸","desc":"her crimson eyes narrow"}],'
            '"subject_scene":"tall woman, black hair, orphanage gate",'
            '"audio_design":{"music":"低沉","sfx":["风声"],"lines":[],"sync":""}}'
        )

    monkeypatch.setattr(ag._llm, "chat", fake_chat)
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))

    _clean, _images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="卡蜜莉亚",
            scene="conflict", comfy_illustrate=True, comfy_video=True,
            persona="乌黑长发，猩红眼眸，成熟丰腴的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        "卡蜜莉亚站在孤儿院门前，猩红眼眸安静地注视着他。",
        turn=2, affinity=10.0, lost=False, user_text="委婉拒绝收养。",
    )
    assert llm_calls  # 提取 LLM 被调用
    vr = request.get("video_request")
    assert isinstance(vr, dict) and vr["submit"]["prompt"]
    req_trace = next(d for ev, d in trace if ev == "illustration.request")
    assert req_trace["video_prompt_chars"] > 0


def test_首轮隐藏思考中的成人词不会污染收养开局插画(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<think>检查做爱、性交、色情等词是否需要破甲。</think>\n"
        "<content>卡蜜莉亚接受了拒绝，俯身将幽影徽章留在长凳上。</content>"
    )

    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="卡蜜莉亚",
            scene="conflict", comfy_illustrate=True,
            persona="乌黑长发，猩红眼眸，成熟的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        reply,
        turn=2,
        affinity=10.0,
        lost=False,
        user_text="委婉拒绝收养，在孤儿院安定前不会离开。",
    )

    assert images == []
    assert request["scene_spec"]["rating"] == "sfw"
    assert request["scene_spec"]["narrative"] == "卡蜜莉亚接受了拒绝，俯身将幽影徽章留在长凳上。"
    assert "<think>" not in request["scene_spec"]["draft_prompt"]
    assert all(term not in request["scene_spec"]["draft_prompt"] for term in (
        "adult characters", "explicit", "intimate scene", "做爱", "性交", "色情",
    ))
    assert trace[-1][1]["inferred_scene"] == "conflict"


def test_非首轮新角色登场以外貌段为锚点触发插画(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    arrival = "骡子拉的平板车停在孤儿院门口，赶车人跳下来时带起一阵尘土。"
    appearance = (
        "方脸，浓眉，宽肩厚背，褐色短褂袖口挽到肘弯，露出结实小臂和掌心厚茧，"
        "咧嘴一笑时露出小虎牙。"
    )
    action = "岑葛把第一口药材木箱搬到门前石台上，招呼仍然不安的院长查看黄芪。"
    reply = (
        "<content>院长压低声音结束了谈话。\n\n" + arrival + "\n\n"
        "<encounter>\n[WHO] 岑葛（伪装药材商贩）\n"
        "[WHERE] 边地孤儿院门口\n[MOOD] 风尘仆仆的爽朗热络\n</encounter>\n\n"
        + appearance + "\n\n" + action + "\n</content>"
    )

    _, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="神恩大陆",
            scene="conflict", comfy_illustrate=True, persona="",
            history=[
                {"role": "user", "content": "此前的对话"},
                {"role": "assistant", "content": "此前的回复"},
            ], proxy="",
        ),
        deps,
        reply,
        turn=4,
        affinity=10.0,
        lost=False,
        user_text="如实告知",
    )

    assert images == []
    assert request["actors"] == ["岑葛"]
    assert request["anchor"] == appearance
    assert arrival in request["scene_spec"]["narrative"]
    assert appearance in request["scene_spec"]["narrative"]
    assert action in request["scene_spec"]["narrative"]
    assert request["scene_spec"]["encounter"]["who"] == "岑葛（伪装药材商贩）"
    assert request["scene_spec"]["rating"] == "sfw"
    assert request["scene_spec"]["aspect_ratio"] == "4:3"
    assert request["allow_anchor_fallback"] is True
    assert trace[-1][0] == "illustration.request"
    assert trace[-1][1]["status"] == "emitted"
    assert trace[-1][1]["reason"] == "character_encounter"


def test_首轮只有未闭合隐藏思考时不提交插画(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")

    _, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="卡蜜莉亚",
            scene="conflict", comfy_illustrate=True,
            persona="乌黑长发，猩红眼眸，成熟的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        "<think>规划时列举裸露和亲吻，但在输出正文前截断",
        turn=2,
        affinity=10.0,
        lost=False,
        user_text="委婉拒绝收养。",
    )

    assert images == []
    assert request == {}


def test_supervisor误判且主模型漏计划时明确成人剧情仍触发生图(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")

    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="dialogue",
            comfy_illustrate=True, persona="black hair, red eyes", history=[], proxy="",
            appearance_source="worldbook",
            illustration_actor_names=["顾凝霜", "白露谷"],
        ),
        deps,
        "她的喘息骤然变得急促，身体在高潮中剧烈颤抖。\n\n许久后，她终于平静下来。",
        turn=3,
        affinity=10.0,
        lost=False,
        user_text="第二天,描写顾凝霜的饥渴难耐与完全征服收为己用,肉戏尽可能的丰富",
    )

    assert clean.endswith("她终于平静下来。")
    assert images == []
    assert request["actors"] == ["顾凝霜"]
    assert "身体在高潮中剧烈颤抖" in request["scene_spec"]["narrative"]
    assert request["scene_spec"]["protected_narrative"] == request["scene_spec"]["narrative"]
    assert request["scene_spec"]["profile_prompt"].isascii()
    assert request["anchor"] == "她的喘息骤然变得急促，身体在高潮中剧烈颤抖。"
    assert trace[-1][0] == "illustration.request"
    assert trace[-1][1]["status"] == "emitted"
    assert trace[-1][1]["reason"] == "local_scene_fallback"


def test_普通剧情漏画面计划时自动插画仍发出高潮请求(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(
        ra, "_narr", lambda _state, key: "顾凝霜" if key == "在场" else "",
    )

    clean, images, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="dialogue",
            comfy_illustrate=True, history=[{"role": "user", "content": "上一轮"}],
            proxy="", appearance_source="worldbook",
            illustration_actor_names=["顾凝霜", "白露谷"], prompt_profile="anima_tags",
        ),
        deps,
        (
            "<content>\n她睁开的眼睛里有什么动了一下，嘴唇微张，终究没出声。\n\n"
            "我把竹简揣进袖子里，冲值夜弟子摆了摆手，继续往山道上走。\n</content>"
        ),
        turn=5,
        affinity=10.0,
        lost=False,
        user_text="继续生成内容",
    )

    assert clean.endswith("</content>")
    assert images == []
    assert request["actors"] == ["顾凝霜"]
    assert request["anchor"]
    assert request["scene_spec"]["narrative"]
    assert trace[-1][0] == "illustration.request"
    assert trace[-1][1]["status"] == "emitted"
    assert trace[-1][1]["reason"] == "missing_plan_fallback"


def test_本地高潮降级从状态栏在场恢复角色并选择角色画幅(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(
        ra, "_narr", lambda _state, key: "顾凝霜" if key == "在场" else "",
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="dialogue",
            comfy_illustrate=True, history=[], proxy="", appearance_source="worldbook",
            illustration_actor_names=["顾凝霜", "苏映晚", "白露谷"],
            prompt_profile="anima_tags",
        ),
        deps,
        "她安静地坐在栏杆旁，随后在高潮中剧烈颤抖。",
        turn=3,
        affinity=10.0,
        lost=False,
        user_text="继续推进剧情",
    )

    assert request["actors"] == ["顾凝霜"]
    assert request["scene_spec"]["aspect_ratio"] == "3:4"
    profile_prompt = request["scene_spec"]["profile_prompt"]
    assert len(profile_prompt.splitlines()) == 2
    assert profile_prompt.isascii()
    assert "I can't help" not in profile_prompt


def test_comfy采用主生成高潮计划并保留指定锚点(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)
    reply = (
        "她跃上高台，披风在雷光中扬起。\n\n人群终于安静下来。"
        '<illustration>{"anchor":"披风在雷光中扬起。",'
        '"camera":"35mm low angle","composition":"triangular composition",'
        '"aspect_ratio":"3:2",'
        '"subjects":[{"name":"白露谷","description":"silver-haired swordswoman","weight":1.4}],'
        '"prompt":"lightning, ruined hall","motion":2}</illustration>'
    )

    clean, images, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白露谷", scene="dialogue", comfy_illustrate=True),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert clean == "她跃上高台，披风在雷光中扬起。\n\n人群终于安静下来。"
    assert images == []
    assert request["anchor"] == "披风在雷光中扬起。"
    assert request["actors"] == ["白露谷"] and request["motion"] == 2
    assert request["scene_spec"]["aspect_ratio"] == "3:2"
    assert "她跃上高台，披风在雷光中扬起" in request["scene_spec"]["narrative"]
    assert "35mm low angle" in request["prompt"]
    assert "triangular composition" in request["prompt"]
    assert request["scene_spec"]["profile_prompt"].isascii()


def test_anima缺少同轮成稿时本地编译且不再请求独立Profile(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)
    reply = (
        "顾凝霜跃上高台，披风在雷光中扬起。"
        '<illustration>{"anchor":"顾凝霜跃上高台，披风在雷光中扬起。",'
        '"camera":"35mm low angle","composition":"triangular composition",'
        '"aspect_ratio":"3:2",'
        '"subjects":[{"name":"顾凝霜","description":"silver-haired swordswoman"}],'
        '"prompt":"adult woman, silver hair, jumping, flowing cape, lightning, ruined hall",'
        '"motion":2}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", card_name="白露谷", scene="climax", comfy_illustrate=True,
            prompt_profile="anima_tags", appearance_source="worldbook",
            illustration_actor_names=["顾凝霜"],
        ),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    compiled = request["scene_spec"]["profile_prompt"]
    assert compiled.splitlines()[0].startswith("masterpiece, best quality")
    assert "silver hair, jumping, flowing cape, lightning" in compiled
    assert "jumping" in compiled.splitlines()[1].lower()
    assert "visible action remains" not in compiled.lower()
    assert "stated action" not in compiled.lower()
    assert "current clothing" not in compiled.lower()
    assert "character identity" not in compiled
    assert "无法协助" not in compiled and "I can't help" not in compiled


def test_多人高潮已有一名合法角色时仍从正文补齐其余在场角色(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "顾凝霜抓住苏映晚的手腕，两人一同穿过殿门。"
        '<illustration>{"anchor":"顾凝霜抓住苏映晚的手腕，两人一同穿过殿门。",'
        '"camera":"low angle","composition":"diagonal composition",'
        '"subjects":[{"name":"顾凝霜","description":"black-haired swordswoman"}],'
        '"prompt":"2women, gripping wrist, walking through doorway","motion":1}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", card_name="白露谷", scene="climax", comfy_illustrate=True,
            appearance_source="worldbook",
            illustration_actor_names=["顾凝霜", "苏映晚", "白露谷"],
        ),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert request["actors"] == ["顾凝霜", "苏映晚"]


@pytest.mark.parametrize(
    ("target", "other", "configured"),
    [
        ("苏映晚", "顾凝霜", ["顾凝霜", "苏映晚"]),
        ("顾凝霜", "苏映晚", ["苏映晚", "顾凝霜"]),
    ],
)
def test_主模型漏插画计划时外貌资料不得污染当前目标角色(
    target, other, configured, monkeypatch, tmp_path,
):
    """复现：配置顺序靠前的旧角色曾夺走当前目标的 single LoRA。"""
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    ctx = _ctx(
        repo_id="work", card_name="作品名", scene="climax", comfy_illustrate=True,
        appearance_source="worldbook", illustration_actor_names=configured,
        prompt_profile="krea2",
    )
    ctx["_illustration_visual_profiles"] = (
        f"{other}：【外貌】{other}专属外貌锚点\n"
        f"{target}：【外貌】{target}专属外貌锚点"
    )

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps, "<content>我不能协助这项请求。</content>",
        turn=2, affinity=10.0, lost=False, user_text=f"本轮只描写{target}",
    )

    assert request["actors"] == [target]
    assert f"{target}专属外貌锚点" in request["scene_spec"]["appearance"]
    assert f"{other}专属外貌锚点" not in request["scene_spec"]["appearance"]


def test_高潮正文单人优先于用户旧角色并只保留该角色外貌(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    ctx = _ctx(
        repo_id="work", card_name="作品名", scene="climax", comfy_illustrate=True,
        appearance_source="worldbook", illustration_actor_names=["顾凝霜", "苏映晚"],
        prompt_profile="krea2",
    )
    ctx["_illustration_visual_profiles"] = (
        "顾凝霜：【外貌】顾凝霜专属外貌锚点\n"
        "苏映晚：【外貌】苏映晚专属外貌锚点"
    )

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps, "<content>高潮画面里，苏映晚独自跪倒在石阶前。</content>",
        turn=2, affinity=10.0, lost=False, user_text="顾凝霜刚才已经离开",
    )

    assert request["actors"] == ["苏映晚"]
    assert request["scene_spec"]["appearance"] == (
        "苏映晚：【外貌】苏映晚专属外貌锚点"
    )


def test_高潮正文双人保留两份外貌供多角色LoRA串联(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    ctx = _ctx(
        repo_id="work", card_name="作品名", scene="climax", comfy_illustrate=True,
        appearance_source="worldbook", illustration_actor_names=["苏映晚", "顾凝霜"],
        prompt_profile="krea2",
    )
    ctx["_illustration_visual_profiles"] = (
        "苏映晚：【外貌】苏映晚专属外貌锚点\n"
        "顾凝霜：【外貌】顾凝霜专属外貌锚点"
    )

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps,
        "<content>高潮画面里，顾凝霜扶住苏映晚，两人一同跌坐在石阶前。</content>",
        turn=2, affinity=10.0, lost=False, user_text="继续",
    )

    assert request["actors"] == ["顾凝霜", "苏映晚"]
    assert "顾凝霜专属外貌锚点" in request["scene_spec"]["appearance"]
    assert "苏映晚专属外貌锚点" in request["scene_spec"]["appearance"]


def test_中断续写把半成品正文作为预填而不是从头再来():
    """2026-09-01 用户需求：被打断的半成品正文要接续写，不能从头再来。"""
    wire = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "她转过身，披风扬起。" + "续" * 200 + "（已打断）"},
    ]
    out = ag._resume_interrupted_messages(wire)
    # 半成品正文保留为最后一条 assistant（预填），后缀剥掉，并追加续写指令
    assert out[-2] == {"role": "assistant", "content": "她转过身，披风扬起。" + "续" * 200}
    assert out[-1]["role"] == "user"
    assert "从断点直接继续" in str(out[-1]["content"])
    assert "（已打断）" not in str(out[-2]["content"])


def test_中断续写半成品过短时不续写():
    """2026-09-04 收口：短稿同样剥掉「（已打断）」标记（防标记字面进模型输入被照抄），
    但不追加续写指令（新轮重写比续写更稳）。"""
    wire = [{"role": "user", "content": "继续"}, {"role": "assistant", "content": "短" + "（已打断）"}]
    out = ag._resume_interrupted_messages(wire)
    assert out[-1] == {"role": "assistant", "content": "短"}
    assert len(out) == len(wire)  # 未追加续写指令


def test_中断续写_wire以本轮用户结尾时替换尾部user避免双user():
    """2026-09-04 收口补充：roleplay wire 恒以本轮 user 结尾（[system, dialogue…,
    user(text)]），续写指令若简单 append 会形成 [assistant 半成品, user 本轮输入,
    user 续写指令] 双 user——本轮真实输入被夹成历史、指令语义稀释。命中时尾部 user 应被
    替换为「续写指令 + 原输入拼接」。"""
    wire = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "上一轮输入"},
        {"role": "assistant", "content": "她转身，披风扬起。" + "续" * 200 + "（已打断）"},
        {"role": "user", "content": "远处传来马蹄声"},
    ]
    out = ag._resume_interrupted_messages(wire)
    # 结构不变（未追加新消息）：尾部 user 被替换为合并指令
    assert len(out) == len(wire)
    assert out[-2] == {"role": "assistant", "content": "她转身，披风扬起。" + "续" * 200}
    assert out[-1]["role"] == "user"
    assert "从断点直接继续" in out[-1]["content"]
    assert "远处传来马蹄声" in out[-1]["content"]  # 原输入拼进合并指令，不丢
    assert "（已打断）" not in out[-2]["content"]



def test_视觉高潮段优先决定角色顺序防止LoRA用错():
    """2026-09-01 用户实锤：全文先提 A 后提 B，但画面主体是 B，single 模式加载了 A 的
    LoRA。无插画计划时，priority_text（高潮段原文）内的出现顺序优先于全文首现顺序。"""
    assert ag._resolve_illustration_request_actors(
        ["谢若冰", "星织恋"],
        planned=[],
        user_text="谢若冰与星织恋同框",
        narrative="谢若冰先到泉边，星织恋随后入画。",
        present="谢若冰、星织恋",
        encounter=[],
        priority_text="星织恋的指尖挑开谢若冰的衣带，画面焦点锁在星织恋身上。",
    ) == ["星织恋", "谢若冰"]


def test_表格在场状态不在场的角色不进入插画演员():
    """2026-09-01 用户实锤：高潮段只有谢若冰（封域内），星织恋在封域外被点名——
    点名提及不等于画面在场。表格在场状态=不在场的角色必须从 request_actors 剔除，
    否则提示词写成 2girls、实际只有一个。"""
    assert ag._resolve_illustration_request_actors(
        ["谢若冰", "星织恋"],
        planned=[],
        user_text="继续",
        narrative="谢若冰在泉中施术，星织恋在封域外石廊打坐。",
        present="谢若冰；星织恋",
        encounter=[],
        absent={"星织恋"},
    ) == ["谢若冰"]



def test_高潮人物只取最终锚点片段而不带入前文离场角色(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    ctx = _ctx(
        repo_id="work", card_name="作品名", scene="climax", comfy_illustrate=True,
        appearance_source="worldbook", illustration_actor_names=["顾凝霜", "苏映晚"],
        prompt_profile="krea2",
    )
    ctx["_illustration_visual_profiles"] = (
        "顾凝霜：【外貌】顾凝霜专属外貌锚点\n"
        "苏映晚：【外貌】苏映晚专属外貌锚点"
    )
    reply = (
        "<content>顾凝霜转身离开石室。\n\n"
        "苏映晚在石阶前骤然失去平衡，独自跪倒。</content>"
        '<illustration>{"anchor":"苏映晚在石阶前骤然失去平衡，独自跪倒。",'
        '"subjects":[{"name":"苏映晚","description":"adult woman"}],'
        '"prompt":"adult woman, kneeling on stone steps","motion":1}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps, reply, turn=2, affinity=10.0, lost=False, user_text="继续",
    )

    assert request["actors"] == ["苏映晚"]
    assert "顾凝霜专属外貌锚点" not in request["scene_spec"]["appearance"]
    assert request["scene_spec"]["subjects"] == [{
        "name": "苏映晚", "description": "adult woman", "weight": 1.0,
    }]


def test_comfy拒绝无效中文内联提示词并从场景本地兜底(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "她站在窗边。"
        '<illustration>{"anchor":"她站在窗边。","camera":"low angle",'
        '"visual_thesis":"窗上倒影与本人形成对望",'
        '"hierarchy":"眼睛与倒影为第一视觉中心，房间逐渐概括",'
        '"palette_material":"冷蓝玻璃与暖金肤色",'
        '"lighting_logic":"窗外月光穿过玻璃照亮眼睛并把倒影压入冷色阴影",'
        '"composition":"centered","subjects":[{"name":"白露谷","description":"1girl"}],'
        '"prompt":"rim light","profile_prompt":"主模型直接生成的完整画面描述。","motion":0}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白露谷", scene="dialogue", comfy_illustrate=True,
             prompt_profile="krea2"),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert request["prompt"] == ""
    assert request["scene_spec"]["profile"] == "krea2"
    assert request["scene_spec"]["profile_prompt"].isascii()
    assert "主模型直接生成" not in request["scene_spec"]["profile_prompt"]
    assert request["scene_spec"]["narrative"] == "她站在窗边。"
    assert request["scene_spec"]["art_direction"] == {
        "visual_thesis": "窗上倒影与本人形成对望",
        "hierarchy": "眼睛与倒影为第一视觉中心，房间逐渐概括",
        "palette_material": "冷蓝玻璃与暖金肤色",
        "lighting_logic": "窗外月光穿过玻璃照亮眼睛并把倒影压入冷色阴影",
    }


def test_主模型把动作峰值写成静态肖像时纠正锚点并本地重建Profile(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "她抬手，以暗影在信笺上写下三条命令。\n\n"
        "她把信笺对折，信笺化作黑色流光穿出帷幔。\n\n"
        "她靠回椅背，嘴角弯起极浅弧度。\n\n不急。"
        '<illustration>{"anchor":"她靠回椅背，嘴角弯起极浅弧度。","camera":"中近景",'
        '"composition":"中心构图","subjects":[{"name":"卡蜜莉亚","description":"冷艳女性"}],'
        '"prompt":"卡蜜莉亚靠在椅背上微笑","profile_prompt":"卡蜜莉亚靠在椅背上，嘴角带着浅笑的静态肖像。",'
        '"motion":0}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="卡蜜莉亚", scene="dialogue", comfy_illustrate=True),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert request["anchor"] == "她把信笺对折，信笺化作黑色流光穿出帷幔。"
    assert request["scene_spec"]["narrative"] == request["anchor"]
    assert request["scene_spec"]["profile"] == "anima_tags"  # 2026-08-31 默认协议切 Anima
    assert request["scene_spec"]["profile_prompt"].isascii()
    assert "smile" not in request["scene_spec"]["profile_prompt"].lower()
    assert request["prompt"]
    assert request["scene_spec"]["draft_prompt"] in request["prompt"]
    assert request["scene_spec"]["subjects"] == [{
        "name": "卡蜜莉亚", "description": "冷艳女性", "weight": 1.0,
    }]


def test_主模型误把结尾外部钩子当高潮时从真实状态快照恢复角色(tmp_path):
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    reply = (
        "<status>\n[所在] 白露谷·破木栏旁\n[在场] 顾凝霜\n</status>\n"
        "<content>她在湿布擦过锁骨时骤然绷紧，汗水沿肩颈滑落，随后全身剧烈颤抖。\n\n"
        "我背着包裹沿山道离开。\n\n"
        "台下两个值夜弟子正在交班，远处红衣在雾中鲜艳。</content>"
        '<illustration>{"anchor":"台下两个值夜弟子正在交班，远处红衣在雾中鲜艳。",'
        '"camera":"medium wide shot","composition":"environmental composition",'
        '"subjects":[{"name":"我","description":"adult man walking"}],'
        '"prompt":"adult man, walking, mountain path, distant watchtower","motion":1}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白露谷", scene="dialogue",
            comfy_illustrate=True, appearance_source="worldbook",
            illustration_actor_names=["顾凝霜", "苏映晚"], prompt_profile="anima_tags",
        ),
        deps, reply, turn=5, affinity=10.0, lost=False, user_text="继续推进剧情",
    )

    assert request["anchor"].startswith("她在湿布擦过锁骨")
    assert request["actors"] == ["顾凝霜"]
    assert "她在湿布擦过锁骨" in request["scene_spec"]["narrative"]
    assert "walking" not in request["prompt"]
    assert "walking" not in request["scene_spec"].get("profile_prompt", "")


def test_高潮重定向后最终profile保留体位破损服装与状态地点(tmp_path):
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    reply = (
        "<status>\n[所在] 缠蛇山·机关天牢内层牢房\n[在场] 苏映晚\n</status>\n"
        "<content>他把她翻成仰卧姿势。\n\n"
        "他把她的双腿压上肩膀，两人面对面。\n\n"
        "高潮袭来，她的大腿绷直，手腕扯动锁链。\n\n"
        "那旗还在那儿。</content>"
        '<illustration>{"anchor":"那旗还在那儿。","camera":"close-up",'
        '"composition":"centered","subjects":[{"name":"苏映晚",'
        '"description":"ink-black hair, dark-red eyes; red lotus robe torn into remnants at her waist"}],'
        '"prompt":"static portrait","profile_prompt":"A generic portrait.","motion":0}</illustration>'
    )
    ctx = _ctx(
        repo_id="work", thread_id="work", card_name="作品名", scene="climax",
        comfy_illustrate=True, appearance_source="worldbook",
        illustration_actor_names=["苏映晚"], prompt_profile="krea2",
    )
    ctx["_illustration_visual_profiles"] = (
        "苏映晚：【外貌】华贵墨发，狭长暗红美眸，丰腴熟躯；"
        "【穿着】红莲纹饰华袍。"
    )

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps, reply, turn=6, affinity=10.0, lost=False, user_text="继续",
    )

    profile = request["scene_spec"]["profile_prompt"]
    assert request["anchor"].startswith("高潮袭来")
    assert "legs raised over the partner's shoulders" in profile
    assert "face-to-face missionary position" in profile
    assert "torn remnants of a red lotus patterned robe" in profile
    assert "confinement cell" in profile
    assert request["scene_spec"]["field_ledger"]["action"]["covered"] is True


@pytest.mark.parametrize("actor", ["顾凝霜", "苏映晚", "任意角色"])
def test_高潮重定向保留主计划已验证角色而不依赖正文重复姓名(
    actor, monkeypatch, tmp_path,
):
    from app.services import character_state, scene_illustration

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    monkeypatch.setattr(
        scene_illustration, "resolve_illustration_anchor",
        lambda _story, _anchor, **_kw: "她跌坐在石板上。",
    )
    monkeypatch.setattr(
        scene_illustration, "illustration_scene_excerpt",
        # A′ 起取材层多一个关键字参数 `subjects`；替身照收不误，锁定行为不变。
        lambda _story, _anchor, **_kw: "她跌坐在石板上。",
    )
    reply = (
        "<content>她跌坐在石板上。\n\n远处的风铃响了。</content>"
        f'<illustration>{{"anchor":"远处的风铃响了。","camera":"medium shot",'
        f'"composition":"rule of thirds","subjects":[{{"name":"{actor}",'
        '"description":"adult woman with black hair"}],'
        '"prompt":"adult woman, black hair, sitting on stone","motion":0}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="作品名", scene="climax",
            comfy_illustrate=True, appearance_source="worldbook",
            illustration_actor_names=[actor, "另一角色"], prompt_profile="krea2",
        ),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert request["actors"] == [actor]
    assert request["scene_spec"]["subjects"][0]["name"] == actor


def test_RunContext含NPC目标增量时仍剥离控制块并发出插画请求(monkeypatch, tmp_path):
    from app.services import character_state

    captured = {}
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    def writeback(*_args, **kwargs):
        captured["raw"] = kwargs["raw_deltas"]
        return 10.0, 10.0

    monkeypatch.setattr(ra, "writeback", writeback)
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    ctx = RunContext(
        thread_id="work", message="委婉拒绝收养", output_dir=str(tmp_path), repo_id="work",
        card_name="神恩大陆", comfy_illustrate=True, prompt_profile="krea2",
    )
    ctx["scene"] = "conflict"
    ctx["_agency_goal_deltas"] = [{
        "field": "叙事/卡蜜莉亚·当前目标", "op": "set", "value": "长期观察主角",
        "evidence": "World Agent依据本轮场景推导",
    }]
    reply = (
        "院长看向石凳上的黑色匣子。"
        '<illustration>{"anchor":"院长看向石凳上的黑色匣子。","camera":"low angle",'
        '"composition":"rule of thirds","subjects":[{"name":"匣子","description":"black box"}],'
        '"prompt":"black box, stone bench","profile_prompt":"完整画面提示词。","motion":0}</illustration>'
    )

    clean, images, request, _audio = ag._agency_writeback(
        ctx, deps, reply, turn=2, affinity=0.0, lost=False,
    )

    assert clean == "院长看向石凳上的黑色匣子。"
    assert images == []
    assert "black box, stone bench" in request["prompt"]
    assert request["scene_spec"]["profile_prompt"].isascii()
    assert request["anchor"] == "院长看向石凳上的黑色匣子。"
    assert captured["raw"] == [{
        "field": "叙事/卡蜜莉亚·当前目标", "op": "set", "value": "长期观察主角",
        "evidence": "World Agent依据本轮场景推导",
    }]
    assert "profile_prompt" not in clean


def test_插画后处理异常时不把内部控制块和提示词回退到正文(monkeypatch, tmp_path):
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("writeback failed")))
    reply = (
        "可见剧情正文。"
        '<状态更新>[]</状态更新>'
        '<表格更新>[]</表格更新>'
        '<illustration>{"anchor":"可见剧情正文。","camera":"low angle",'
        '"composition":"centered","subjects":[{"name":"角色"}],'
        '"prompt":"secret image prompt","motion":0}</illustration>'
    )

    clean, images, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="角色", comfy_illustrate=True),
        deps, reply, turn=2, affinity=0.0, lost=False,
    )

    assert clean == "可见剧情正文。"
    assert images == [] and request == {}
    assert "secret image prompt" not in clean
    assert "状态更新" not in clean and "表格更新" not in clean


def test_插画JSON被模型截断时清除提示词并把降级图片锚定高潮段(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<think>规划俯身、凝视、光影和高潮构图。</think>\n"
        "<content>日常铺垫。\n\n"
        "她俯身凝视着他，长发在午后的光影里垂落。\n\n"
        "她弯下腰，将乌木匣子搁在长凳上。\n\n"
        "院长从门后走出来询问情况。</content>\n"
        '<illustration>{"anchor":"院长从门后走出来询问情况。","camera":"中近景",'
        '"visual_thesis":"不应显示给用户的提示词","aspect_'
    )
    ctx = _ctx(repo_id="work", card_name="神恩大陆", comfy_illustrate=True)
    ctx["history"] = []

    clean, images, request, _audio = ag._agency_writeback(
        ctx, deps, reply, turn=1, affinity=0.0, lost=False,
    )

    assert images == []
    assert "<illustration>" not in clean
    assert "visual_thesis" not in clean and "不应显示给用户的提示词" not in clean
    assert request["anchor"] == "她弯下腰，将乌木匣子搁在长凳上。"
    offset = ag._illustration_anchor_offset(clean, request)
    assert offset is not None
    assert clean[:offset].endswith(request["anchor"])
    assert "院长从门后走出来" in clean[offset:]


def test_krea2高潮按nsfw语言边界校验(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    valid = (
        "中景低机位拍摄。侧向暖光勾勒轮廓。人物保留稳定外貌。"
        "She wears the clothing established by the current scene. "
        "She leans against the rail while facing the viewer. "
        "背景为当前地点。主体位于三分线。"
    )
    reply = (
        "高潮正文。"
        '<illustration>{"anchor":"高潮正文。","camera":"low angle",'
        '"composition":"rule of thirds","subjects":[{"name":"白露谷","description":"1girl"}],'
        f'"prompt":"rim light","profile_prompt":"{valid}","motion":0}}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白露谷", scene="climax", comfy_illustrate=True,
             prompt_profile="krea2"),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert "rim light" in request["prompt"]
    assert valid not in request["prompt"]
    assert request["scene_spec"]["profile_prompt"]
    assert valid not in request["scene_spec"]["profile_prompt"]
    assert request["scene_spec"]["rating"] == "nsfw"


def test_主剧情同轮Krea完整提示词隐藏并直接进入scene_spec(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    profile = (
        "1woman, black hair, torn purple robe, low-angle close medium view, broken rail, "
        "dawn side light, foreground frame\n"
        "A low-angle close medium view places the adult woman beside the broken rail as she turns sharply. "
        "Cold dawn side light follows her black hair and torn purple robe while the ruined village recedes into mist. "
        "The foreground rail frames her face and reaching hand, with coherent anatomy, clean edges, controlled detail, "
        "stable perspective, restrained negative space, and polished image fidelity."
    )
    reply = (
        "<content>铺垫。\n\n她在破木栏旁猛然转身。</content>"
        '<illustration>{"anchor":"她在破木栏旁猛然转身。","camera":"low angle",'
        '"composition":"foreground frame","subjects":[{"name":"顾凝霜",'
        '"description":"adult woman with black hair and a torn purple robe"}],'
        f'"prompt":"turning, broken rail, dawn light","profile_prompt":"{profile}","motion":2}}</illustration>'
    )

    clean, _, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白露谷", scene="climax", comfy_illustrate=True,
             prompt_profile="krea2", illustration_actor_names=["顾凝霜"]),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert clean == "<content>铺垫。\n\n她在破木栏旁猛然转身。</content>"
    assert "profile_prompt" not in clean
    assert request["scene_spec"]["profile_prompt"] == profile


def test_anima锚点纠正后保留带正文证据的Agent动作成稿(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<content>她拔剑跃起，披风在雷光中扬起。\n\n"
        "战斗结束，她靠回墙边沉默。\n\n那道雷光仍留在空气里。</content>"
        '<illustration>{"anchor":"那道雷光仍留在空气里。","camera":"low angle",'
        '"composition":"diagonal composition","subjects":[{"name":"顾凝霜",'
        '"description":"silver-haired swordswoman pulling a broken sword free"}],'
        '"visual_facts":[{"kind":"action","fact":"her hand pulling the broken sword free",'
        '"evidence":"她拔剑跃起，披风在雷光中扬起。"}],'
        '"prompt":"jumping, flowing cape, lightning",'
        '"profile_prompt":"ruined hall, lightning, low angle, silver-haired swordswoman, jumping,\n'
        'The silver-haired swordswoman jumps while pulling the broken sword free.","motion":2}</illustration>'
    )

    _, _, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白露谷", scene="climax", comfy_illustrate=True,
             prompt_profile="anima_tags", illustration_actor_names=["顾凝霜"]),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    prompt = request["scene_spec"]["profile_prompt"]
    assert request["scene_spec"]["field_ledger"]["visual_facts"]["covered"] is True
    assert "pulling the broken sword free" in prompt.splitlines()[0]
    assert "pulling the broken sword free" in prompt.splitlines()[1]
    assert "visible action remains" not in prompt.lower()


def test_主剧情同轮Profile拒答时本地兜底且不把拒答交给前端(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    refusal = "I'm Claude Code. I won't generate this content — the request is sexually explicit."
    reply = (
        "<content>她在破木栏旁猛然转身。</content>"
        '<illustration>{"anchor":"她在破木栏旁猛然转身。","camera":"low angle",'
        '"composition":"foreground frame","subjects":[{"name":"顾凝霜",'
        '"description":"adult woman with black hair and a purple robe"}],'
        f'"prompt":"turning, broken rail, dawn light","profile_prompt":"{refusal}","motion":2}}</illustration>'
    )

    clean, _, request, _audio = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白露谷", scene="climax", comfy_illustrate=True,
             prompt_profile="krea2", illustration_actor_names=["顾凝霜"]),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert refusal not in clean
    assert refusal not in request["scene_spec"]["profile_prompt"]
    assert "visible action" in request["scene_spec"]["profile_prompt"]


def test_同轮Profile先经过与正文相同的AI输出正则(monkeypatch, tmp_path):
    from app.services import character_state, regex_engine

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    profile = (
        "1woman, black hair, purple robe, close medium view, broken rail, side light, "
        "layered misty background\n"
        "A close medium view follows PRESET_TOKEN as the adult woman turns beside a broken rail. "
        "Cold side light defines her face, hands, purple robe, and layered misty background with clean detail."
    )
    reply = (
        "<content>她在破木栏旁猛然转身。</content>"
        '<illustration>{"anchor":"她在破木栏旁猛然转身。","camera":"low angle",'
        '"composition":"foreground frame","subjects":[{"name":"顾凝霜",'
        '"description":"adult woman with black hair and a purple robe"}],'
        f'"prompt":"turning, broken rail, dawn light","profile_prompt":"{profile}","motion":2}}</illustration>'
    )
    ctx = _ctx(
        repo_id="work", card_name="白露谷", scene="climax", comfy_illustrate=True,
        prompt_profile="krea2", illustration_actor_names=["顾凝霜"],
    )
    ctx["_regex_scripts"] = [regex_engine.RegexScript(
        find_regex="/PRESET_TOKEN/g",
        replace_string="her jet-black hair",
        placement=[regex_engine.Placement.AI_OUTPUT],
    )]

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps, reply, turn=2, affinity=10.0, lost=False,
    )

    compiled = request["scene_spec"]["profile_prompt"]
    assert "PRESET_TOKEN" not in compiled
    assert "her jet-black hair" in compiled


def test_回合号使用完整快照而不是裁剪历史(monkeypatch):
    from app.services import chat_snapshot

    full = [
        {"role": "assistant", "content": f"回复{i}"}
        for i in range(12)
    ]
    monkeypatch.setattr(chat_snapshot, "load_prompt_history", lambda _thread_id: full)

    assert ag._next_story_turn({
        "repo_id": "work",
        "history": [{"role": "assistant", "content": "裁剪后仅剩一轮"}],
    }) == 13


def test_自动插画从已配置角色名机械识别在场角色():
    prompt, motion, actors = ag._build_image_prompt(
        _ctx(
            card_name="Lyra", illustration_actor_names=["Lyra", "Nia", "Mira"],
        ),
        paragraph="Lyra 与 Nia 在长廊奔跑。", appearance="", wardrobe="", locale="",
    )

    assert prompt and motion == 2
    assert actors == ["Lyra", "Nia"]


def test_插画槽事件位于锚点且前后文本不丢失():
    text = "高潮段落。\n\n后续状态。"
    events = ag._ordered_illustration_events(text, [{
        "id": "slot-1", "prompt": "p", "motion": 0, "actors": [],
        "anchor_offset": len("高潮段落。"),
    }])

    assert events == [
        {"delta": "高潮段落。"},
        {"illustrate_request": {"prompt": "p", "motion": 0, "actors": []}, "id": "slot-1"},
        {"delta": "\n\n后续状态。"},
    ]
    assert "".join(event.get("delta", "") for event in events) == text


def test_输出正则改写兜底锚点后仍按最终正文创建高潮槽():
    final_reply = "她全身______，在高潮中剧烈颤抖。\n\n许久后，她终于平静下来。"
    request = {
        "anchor": "她全身痉挛，在高潮中剧烈颤抖。",
        "allow_anchor_fallback": True,
    }

    offset = ag._illustration_anchor_offset(final_reply, request)

    assert offset == len("她全身______，在高潮中剧烈颤抖。")


def test_流式插画只发槽位和最终正文偏移():
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 2, "actors": ["Lyra"],
        "anchor_offset": 5,
    }])

    assert events == [{
        "illustrate_request": {
            "prompt": "p", "motion": 2, "actors": ["Lyra"], "offset": 5,
        },
        "id": "slot-1",
    }]


def test_插画事件透传视频协议可选字段_v1_5():
    # V1.5/B1：rec 带 video 字段 → 事件 request 透传（有值才带）
    events = ag._ordered_illustration_events("正文", [{
        "id": "slot-1", "prompt": "p", "motion": 1, "actors": [],
        "video_mode": "firstlast",
        "first_frame_desc": "雨夜门口的暖黄灯笼",
        "last_frame_desc": "三人举杯同框",
        "prev_tail_desc": "上一楼层收伞",
        "last_frame_url": "data:image/png;base64,xx",
        "transition": "reuse",
    }])
    assert events == [
        {"delta": "正文"},
        {
            "illustrate_request": {
                "prompt": "p", "motion": 1, "actors": [],
                "video_mode": "firstlast",
                "first_frame_desc": "雨夜门口的暖黄灯笼",
                "last_frame_desc": "三人举杯同框",
                "prev_tail_desc": "上一楼层收伞",
                "last_frame_url": "data:image/png;base64,xx",
                "transition": "reuse",
            },
            "id": "slot-1",
        },
    ]


def test_插画事件transition空值不携带_v1_5_w1():
    # V1.5/W1：rec 无 transition 或为空 → 事件 request 不带 transition 字段（旧数据兼容）
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 3, "actors": [],
        "anchor_offset": 0, "transition": "",
    }])
    assert events == [{
        "illustrate_request": {
            "prompt": "p", "motion": 3, "actors": [], "offset": 0,
        },
        "id": "slot-1",
    }]


def test_流式插画透传视频协议可选字段_v1_5():
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 3, "actors": ["Lyra"],
        "anchor_offset": 5, "video_mode": "climax",
        "first_frame_desc": "高潮动作瞬间",
    }])
    assert events == [{
        "illustrate_request": {
            "prompt": "p", "motion": 3, "actors": ["Lyra"], "offset": 5,
            "video_mode": "climax",
            "first_frame_desc": "高潮动作瞬间",
        },
        "id": "slot-1",
    }]


def test_插画事件无视频字段时保持原状_旧数据兼容():
    events = ag._ordered_illustration_events("正文", [{
        "id": "slot-1", "prompt": "p", "motion": 0, "actors": [],
    }])
    assert events == [
        {"delta": "正文"},
        {
            "illustrate_request": {"prompt": "p", "motion": 0, "actors": []},
            "id": "slot-1",
        },
    ]


def test_插画事件未带video_request不下发视频提示词_正常链路():
    # 正常链路：未配置视频工作流（produce 层未编译 video_request）→ 事件不带视频字段
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 3, "actors": ["纪含章", "林屿"],
        "anchor_offset": 0,
        "scene_spec": {
            "narrative": "纪含章猛地起身撞桌",
            "appearance": "纪含章米色针织开衫",
            "wardrobe": "全员日常私服",
            "locale": "面馆内景",
            "actors": ["纪含章", "林屿"],
            "rating": "sfw",
            "negative_prompt": "低质量；畸形手",
        },
    }])
    request = events[0]["illustrate_request"]
    assert "video_prompt" not in request
    assert "video_params" not in request


def test_未带video_request时video_config不下发_正常链路():
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 3, "actors": ["甲"],
        "anchor_offset": 0,
        "video_config": {"base_url": "https://vid.example", "model": "h3-mini",
                         "size": "1280x720", "proxy": ""},
        "scene_spec": {
            "narrative": "甲挥拳", "appearance": "甲黑衣", "wardrobe": "日常",
            "locale": "街角", "actors": ["甲"], "rating": "sfw",
        },
    }])
    request = events[0]["illustrate_request"]
    assert "video_prompt" not in request
    assert "video_params" not in request


def test_事件层复用produce编译的video_request_v1_5():
    # produce 层已编译并透传（rec.video_request）→ 事件层直接复用，不重复编译、内容一致
    vrequest = {
        "mode": "climax",
        "submit": {"prompt": "使用视频模型生成，15 seconds，16:9。\n\n[动作]：甲挥拳。"},
        "reference_binding": {}, "warnings": [],
    }
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 3, "actors": ["甲"],
        "anchor_offset": 0, "video_request": vrequest,
        "scene_spec": {"narrative": "甲挥拳", "actors": ["甲"], "rating": "sfw"},
    }])
    req = events[0]["illustrate_request"]
    assert req["video_prompt"] == vrequest["submit"]["prompt"]
    assert req["video_params"]["mode"] == "climax"
    assert req["video_params"]["warnings"] == []


def test_插画事件无scene_spec则不生成视频提示词_v1_5():
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 2, "actors": [],
        "anchor_offset": 0,
    }])
    assert "video_prompt" not in events[0]["illustrate_request"]
    assert "video_params" not in events[0]["illustrate_request"]


def test_视频提示词motion强度影响运镜_正常链路():
    from app.services import video_prompt
    base_spec = {
        "narrative": "角色动作", "appearance": "外貌", "wardrobe": "服装",
        "locale": "场景", "actors": ["角色"], "rating": "sfw",
    }
    def compile_for(motion):
        spec = dict(base_spec)
        spec["motion"] = motion
        return video_prompt.compile_climax_video_prompt(spec)

    assert "低角度仰拍，摄像机以快速弧线围绕主体运动" in compile_for(3)
    assert "镜头绕主体旋转90度" in compile_for(2)
    assert "摄像机缓缓向主体的面部移动，画面逐渐收窄" in compile_for(0)


def test_正文最终化后在记忆维护前立即发插画请求():
    emitted = []
    out = {
        "result_text": "最终正文",
        "illustrate_recs": [{
            "id": "slot-1", "prompt": "完整提示词", "motion": 1,
            "actors": ["Lyra"], "anchor_offset": 2,
        }],
    }
    assert ag._emit_roleplay_ready(_ctx(stream_sink=emitted.append), out) is True
    assert emitted == [
        {"replace": "最终正文"},
        {"illustrate_request": {
            "prompt": "完整提示词", "motion": 1, "actors": ["Lyra"], "offset": 2,
        }, "id": "slot-1"},
    ]


def test_正文最终化即时通道补发音频对白请求():
    # 回归：audio_recs 漏发会导致 eager 分支跳过 audio_request，前端永远收不到台词。
    emitted = []
    out = {
        "result_text": "最终正文",
        "audio_recs": [{
            "id": "audio-req-work-3",
            "lines": [{"speaker": "苏映晚", "text": "我认输。",
                       "emotion": {"neutral": 1}}],
        }],
    }
    assert ag._emit_roleplay_ready(_ctx(stream_sink=emitted.append), out) is True
    assert emitted == [
        {"replace": "最终正文"},
        {"audio_request": {
            "lines": [{"speaker": "苏映晚", "text": "我认输。", "emotion": {"neutral": 1}}],
        }, "id": "audio-req-work-3"},
    ]


def test_独立表格维护只写数据库且不把维护响应放进对话(monkeypatch, tmp_path):
    from app.services import table_store

    repo_id = "work"
    table_store.save(str(tmp_path), repo_id, table_store.default_tables())
    captured = {}
    traces = []

    def maintain(base, key, model, system, user, **kwargs):
        captured.update(system=system, user=user)
        return json.dumps([{
            "op": "insert", "table": table_store.GLOBAL_TABLE,
            "values": {"时间": "第五日", "地点": "边地孤儿院", "世界状态": "平静", "世界规则": "未变"},
        }], ensure_ascii=False)

    monkeypatch.setattr(ag._llm, "chat", maintain)
    monkeypatch.setattr(
        ag.run_trace, "emit", lambda ctx, event, **data: traces.append((event, data)),
    )
    deps = ra.AgencyDeps(chat_fn=maintain, rng=random.Random(0), state_base=str(tmp_path))
    ctx = _ctx(
        output_dir=str(tmp_path), repo_id=repo_id, message="留在孤儿院",
    )

    ag._table_maintenance(ctx, deps, repo_id, "剧情正文。" * 80, turn=1)

    global_table = next(
        table for table in table_store.load(str(tmp_path), repo_id)
        if table["name"] == table_store.GLOBAL_TABLE
    )
    assert global_table["rows"][0][:2] == ["第五日", "边地孤儿院"]
    assert "<表格更新>" not in captured["system"] + captured["user"]
    assert captured["user"].endswith("剧情正文。" * 80)
    assert any(event == "agent.completed" and data.get("agent") == "table_maintenance"
               for event, data in traces)


# ── 2026-09-18 兜底状态抽取：主协议失守 → 置信号 → 维护调用顺带补写回 ──

def _signal_deps(tmp_path) -> ra.AgencyDeps:
    return ra.AgencyDeps(
        chat_fn=lambda *a, **k: "", rng=random.Random(0), state_base=str(tmp_path))


def test_wb_apply_state_协议双缺失时置兜底抽取信号(monkeypatch, tmp_path):
    monkeypatch.setattr(aag.run_trace, "emit", lambda ctx, event, **data: None)
    ctx = _ctx(output_dir=str(tmp_path), repo_id="work", message="你好")
    _, raw, _, _, snapshot, _ = aag._wb_apply_state(
        ctx, _signal_deps(tmp_path), "她微微一笑。", turn=38, repo_id="work",
        card_name="谢若冰")
    assert snapshot == "" and raw == []
    assert ctx["_agency_state_fallback"] == (True, True)


def test_wb_apply_state_缺状态更新只置增量信号(monkeypatch, tmp_path):
    monkeypatch.setattr(aag.run_trace, "emit", lambda ctx, event, **data: None)
    ctx = _ctx(output_dir=str(tmp_path), repo_id="work", message="你好")
    clean = "<status>\n[第38轮]\n[好感] 谢若冰=40\n</status>\n\n她微微一笑。"
    aag._wb_apply_state(ctx, _signal_deps(tmp_path), clean, turn=38, repo_id="work",
                        card_name="谢若冰")
    assert ctx["_agency_state_fallback"] == (True, False)


def test_wb_apply_state_缺status只置快照信号(monkeypatch, tmp_path):
    monkeypatch.setattr(aag.run_trace, "emit", lambda ctx, event, **data: None)
    ctx = _ctx(output_dir=str(tmp_path), repo_id="work", message="你好")
    clean = ('她微微一笑。\n<状态更新>'
             '[{"field":"数值/谢若冰·好感度","op":"add","value":1,"evidence":"笑"}]</状态更新>')
    aag._wb_apply_state(ctx, _signal_deps(tmp_path), clean, turn=38, repo_id="work",
                        card_name="谢若冰")
    assert ctx["_agency_state_fallback"] == (False, True)


def test_wb_apply_state_协议齐全不置信号(monkeypatch, tmp_path):
    monkeypatch.setattr(aag.run_trace, "emit", lambda ctx, event, **data: None)
    ctx = _ctx(output_dir=str(tmp_path), repo_id="work", message="你好")
    clean = ("<status>\n[第38轮]\n[好感] 谢若冰=40\n</status>\n\n她微微一笑。\n<状态更新>"
             '[{"field":"数值/谢若冰·好感度","op":"add","value":1,"evidence":"笑"}]</状态更新>')
    aag._wb_apply_state(ctx, _signal_deps(tmp_path), clean, turn=38, repo_id="work",
                        card_name="谢若冰")
    assert "_agency_state_fallback" not in ctx


def _preset_state(tmp_path, repo_id: str, card: str, *, value: float, turn: int):
    from app.services import character_state

    st = character_state.load_state(str(tmp_path), repo_id, card)
    # 2026-09-23：预置值用 `set` 播种（`add` 命中不存在的好感度字段已被新闸拒收）。
    deltas = character_state.parse_deltas(
        [{"field": f"数值/{card}·好感度", "op": "set", "value": value, "evidence": "前情"}],
        turn=turn, source=character_state.SRC_AUTO, card_name=card, existing_state=st)
    character_state.apply_deltas(st, deltas)
    st.快照 = character_state.Snapshot(f"[第{turn}轮]\n[好感] {card}={value:g}", turn)
    character_state.save_state(str(tmp_path), st)


def test_独立表格维护兜底信号驱动状态补写回(monkeypatch, tmp_path):
    from app.services import character_state

    repo_id, card = "work", "谢若冰"
    captured = {}
    traces = []

    def maintain(base, key, model, system, user, **kwargs):
        captured.update(system=system, user=user)
        return json.dumps({
            "ops": [],
            "state_deltas": [{"field": "数值/谢若冰·好感度", "op": "add", "value": 2,
                              "evidence": "她收下了信物"}],
            "status_snapshot": "[第38轮] 夜谈\n[好感] 谢若冰=42",
        }, ensure_ascii=False)

    monkeypatch.setattr(aag._llm, "chat", maintain)
    monkeypatch.setattr(
        aag.run_trace, "emit", lambda ctx, event, **data: traces.append((event, data)))
    deps = ra.AgencyDeps(chat_fn=maintain, rng=random.Random(0), state_base=str(tmp_path))
    _preset_state(tmp_path, repo_id, card, value=40, turn=37)

    ctx = _ctx(output_dir=str(tmp_path), repo_id=repo_id, card_name=card, message="送出信物")
    ctx["_agency_state_fallback"] = (True, True)
    aag._table_maintenance(ctx, deps, repo_id, "她收下了信物，耳根微红。" * 5, turn=38)

    st2 = character_state.load_state(str(tmp_path), repo_id, card)
    assert st2.数值["谢若冰·好感度"].value == 42.0
    assert st2.快照.turn == 38
    assert "[好感] 谢若冰=42" in st2.快照.text  # 增量已先应用，对账后 42 与引擎现值一致
    assert "【上轮状态栏】" in captured["user"] and "第37轮" in captured["user"]
    assert any(e == "agent.completed" and d.get("agent") == "table_maintenance"
               and d.get("state_fallback") for e, d in traces)


def test_独立表格维护兜底信号但模型回旧数组时只应用表格ops(monkeypatch, tmp_path):
    from app.services import character_state, table_store

    repo_id, card = "work", "谢若冰"
    traces = []
    table_store.save(str(tmp_path), repo_id, table_store.default_tables())

    def maintain(base, key, model, system, user, **kwargs):
        return json.dumps([{
            "op": "insert", "table": table_store.GLOBAL_TABLE,
            "values": {"时间": "第五日", "地点": "边地孤儿院", "世界状态": "平静", "世界规则": "未变"},
        }], ensure_ascii=False)

    monkeypatch.setattr(aag._llm, "chat", maintain)
    monkeypatch.setattr(
        aag.run_trace, "emit", lambda ctx, event, **data: traces.append((event, data)))
    deps = ra.AgencyDeps(chat_fn=maintain, rng=random.Random(0), state_base=str(tmp_path))
    _preset_state(tmp_path, repo_id, card, value=40, turn=37)

    ctx = _ctx(output_dir=str(tmp_path), repo_id=repo_id, card_name=card, message="留在孤儿院")
    ctx["_agency_state_fallback"] = (True, True)
    aag._table_maintenance(ctx, deps, repo_id, "剧情正文。" * 80, turn=38)

    st2 = character_state.load_state(str(tmp_path), repo_id, card)
    assert st2.快照.turn == 37  # 旧数组无状态字段：优雅降级，状态写回不做
    global_table = next(t for t in table_store.load(str(tmp_path), repo_id)
                        if t["name"] == table_store.GLOBAL_TABLE)
    assert global_table["rows"][0][:2] == ["第五日", "边地孤儿院"]
    assert not any(e == "agent.completed" and d.get("state_fallback") for e, d in traces)


def test_独立表格维护快照兜底不重复应用增量(monkeypatch, tmp_path):
    from app.services import character_state

    repo_id, card = "work", "谢若冰"
    captured = {}
    traces = []

    def maintain(base, key, model, system, user, **kwargs):
        captured.update(system=system, user=user)
        return json.dumps({
            "ops": [],
            "state_deltas": [{"field": "数值/谢若冰·好感度", "op": "add", "value": 2,
                              "evidence": "模型抗命多给的增量"}],
            "status_snapshot": "[第38轮] 夜谈\n[好感] 谢若冰=41",
        }, ensure_ascii=False)

    monkeypatch.setattr(aag._llm, "chat", maintain)
    monkeypatch.setattr(
        aag.run_trace, "emit", lambda ctx, event, **data: traces.append((event, data)))
    deps = ra.AgencyDeps(chat_fn=maintain, rng=random.Random(0), state_base=str(tmp_path))
    _preset_state(tmp_path, repo_id, card, value=40, turn=37)

    ctx = _ctx(output_dir=str(tmp_path), repo_id=repo_id, card_name=card, message="夜谈")
    ctx["_agency_state_fallback"] = (False, True)
    aag._table_maintenance(ctx, deps, repo_id, "她收下了信物。" * 5, turn=38)

    st2 = character_state.load_state(str(tmp_path), repo_id, card)
    assert st2.数值["谢若冰·好感度"].value == 40.0  # 主协议增量已应用过：维护增量不重复计
    assert st2.快照.turn == 38
    assert "[好感] 谢若冰=40" in st2.快照.text  # 快照对账到引擎现值
    assert "state_deltas 固定输出 []" in captured["system"]
    assert "【上轮状态栏】" in captured["user"]


def test_独立表格维护无信号时不要求状态抽取(monkeypatch, tmp_path):
    from app.services import table_store

    repo_id = "work"
    table_store.save(str(tmp_path), repo_id, table_store.default_tables())
    captured = {}

    def maintain(base, key, model, system, user, **kwargs):
        captured.update(system=system, user=user)
        return json.dumps([], ensure_ascii=False)

    monkeypatch.setattr(aag._llm, "chat", maintain)
    monkeypatch.setattr(aag.run_trace, "emit", lambda ctx, event, **data: None)
    deps = ra.AgencyDeps(chat_fn=maintain, rng=random.Random(0), state_base=str(tmp_path))
    ctx = _ctx(output_dir=str(tmp_path), repo_id=repo_id, message="留在孤儿院")
    aag._table_maintenance(ctx, deps, repo_id, "剧情正文。" * 80, turn=1)
    assert "state_deltas" not in captured["system"]
    assert "只输出 JSON 数组" in captured["system"]


def test_roleplay先交付正文与插画但维护完成后才结束Agent回合(monkeypatch):
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    foreground_done = threading.Event()
    emitted = []

    class _Deps:
        renderer = None

    monkeypatch.setattr(agr, "_agency_prelude", lambda ctx, text: (_Deps(), 1, 0.0, ""))
    monkeypatch.setattr(agr, "_agency_propose", lambda *args, **kwargs: ("", False))
    monkeypatch.setattr(agr, "_resolve_worldbook", lambda *args: "")
    monkeypatch.setattr(agr, "_resolve_preset", lambda *args, **kwargs: ([], None, False, [], []))
    monkeypatch.setattr(ra, "recall_chronicle", lambda *args, **kwargs: "")
    monkeypatch.setattr(agr, "_rag_recall_text", lambda *args: "")
    monkeypatch.setattr(ag._llm, "chat_messages_stream", lambda *args, **kwargs: "<content>最终正文</content>")
    monkeypatch.setattr(
        agr,
        "_agency_writeback",
        lambda *args: ("最终正文", [], {
            "prompt": "完整提示词", "motion": 1, "actors": ["Lyra"], "anchor": "",
        }, {}),
    )

    def slow_maintenance(*args):
        maintenance_started.set()
        release_maintenance.wait(2)

    monkeypatch.setattr(am, "_agency_maintenance", slow_maintenance)  # 消费方 _agency_maintenance_async 已迁 agency_maintenance
    ctx = _card_ctx(
        repo_id="work", thread_id="work", comfy_illustrate=True,
        stream_sink=emitted.append,
    )
    result = {}
    def run_roleplay():
        result.update(ag.roleplay_node({
            "user_text": "继续剧情", "images": [], "_ctx": ctx,
        }))
        foreground_done.set()

    worker = threading.Thread(
        target=run_roleplay,
    )
    worker.start()
    try:
        # 2026-08-30 改约：维护转后台线程，正文/插画先发布，turn 立即完成——
        # 慢维护不得挂住对话完成（旧契约「维护完成后才结束」反转）。
        assert maintenance_started.wait(1)
        assert [event.keys() & {"replace", "illustrate_request"} for event in emitted] == [
            {"replace"}, {"illustrate_request"},
        ]
        assert foreground_done.wait(2), "维护慢时对话也必须完成"
        assert result["result_text"] == "最终正文"
        assert result["_eager_result"] is True
    finally:
        release_maintenance.set()
        worker.join(2)
    ag.join_maintenance_threads()
    assert not worker.is_alive()


# ── 路由界限（Autopilot P1 前置）：委派强命令层 vs 剧情默认 ──────────────────

def test_有卡批量出图进委派不走剧情():
    # 路由界限核心用例：规模词+生成动作 = 高置信委派，不允许掉进 roleplay
    out = _dispatch("帮我批量出 20 张变体图", ctx=_ctx(
        card_name="角色卡", character_dir="cards"))
    assert out["route"] == "plan"


def test_剧情内画像请求仍走剧情():
    out = _dispatch("她提笔画了一幅像", ctx=_ctx(
        card_name="角色卡", character_dir="cards"))
    assert out["route"] == "roleplay"


def test_无卡委派意图也进委派():
    out = _dispatch("整理全部世界书条目", ctx=_ctx(card_name="", character_dir=""))
    assert out["route"] == "plan"


def test_委派层不劫持带图附件的图生图反推():
    out = _dispatch("批量处理这些图", images=["data:image/png;base64,x"], ctx=_ctx())
    assert out["route"] != "plan"


def test_委派疑问句不进委派():
    out = _dispatch("为什么批量出图失败了？", ctx=_ctx(
        card_name="角色卡", character_dir="cards"))
    assert out["route"] == "roleplay"


def test_带图文档任务进委派不被反推劫持():
    # 看图反推外貌→生成套装文档：文档交付强命令允许带图附件进委派
    out = _dispatch(
        "根据这张图反推角色外貌，结合时尚穿搭文档整理生成四季套装文档",
        images=["data:image/png;base64,x"], ctx=_ctx())
    assert out["route"] == "plan"


def test_有卡对话中途整理文档仍进委派():
    # 普通对话商讨角色外貌后要求整理成综合文档：文档交付压过剧情默认
    out = _dispatch("把上面商讨的角色外貌结果整理成一份综合文档",
                    ctx=_card_ctx())
    assert out["route"] == "plan"


def test_plan路由在主管候选集中():
    ctx = _ctx()
    assert "plan" in ag._available_routes(False, ctx)


def test_自愈进度提示只在流式模式进通道():
    """截断自愈重试提示走流式 trace 事件；非流式模式气泡走 trace 列表，不直接进通道。"""
    events: list[dict] = []
    ctx = _ctx(stream_sink=events.append, stream_output=False)
    ag._notify_stream_trace(ctx, "⚠️ 提示")
    assert events == []

    ctx["stream_output"] = True
    ag._notify_stream_trace(ctx, "⚠️ 提示")
    assert events == [{"trace": "⚠️ 提示"}]


def test_续写消息序列残缺输出回喂加断点指令():
    """截断续写：残缺输出作为最后一条 assistant 回喂（保格式），指令要求从断点
    直接续写、不重复不重开、不输出思考块；不破坏既有 system/对话顺序。"""
    wire = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "上一轮"},
        {"role": "assistant", "content": "上轮回复"},
        {"role": "user", "content": "本轮输入"},
    ]
    partial = "<think>决策。</think>\\n<content>正文前半段，写到一半"
    out = ag._roleplay_continuation_messages(wire, partial)

    assert out[:4] == wire  # 既有消息原样在前
    assert out[4] == {"role": "assistant", "content": partial}  # 残缺输出全文回喂
    instruction = out[5]
    assert instruction["role"] == "user"
    assert "从断点直接继续" in instruction["content"]
    assert "禁止重复已有内容" in instruction["content"]
    assert "不要输出 <think>" in instruction["content"]
    assert "</content>" in instruction["content"]

def test_可见文本兜底链拆think防跨界吞正文():
    """_visible_roleplay_text 与主链同源：先拆 think 前缀再提取，幻影协议标签无法跨界吞正文。"""
    reply = (
        "<think>格式确认：- <content> 正文 - <状态更新> 块</think>\n"
        "<content>正文核心段落。</content>\n"
        '<状态更新>[{"k":1}]</状态更新>'
    )
    out = ag._visible_roleplay_text(reply)

    assert "正文核心段落。" in out
    assert "格式确认" in out  # think 前缀原样保留
    assert "[{\"k\":1}]" not in out  # 真状态块照常剥除（think 内幻影引述按设计保留）



# ── 固化流程预设：自由循环成功 → 草稿配方 + [[recipe:id|name]] 内联卡 ──────────

def test_自由循环成功固化出内联配方卡(monkeypatch, tmp_path):
    """事前固化询问 → 回「要」→ 自由循环跑完 → 草稿配方 + [[recipe:id|name]] 内联卡。

    2026-09-10：固化清单里没有同类时，plan 节点会**先问一句**要不要固化（省 token 的
    前置询问），不再直接开跑。所以本用例先断言「只问不跑」，再回「要」走完整条固化链。
    """
    from app.services import capability_sandbox
    from app.services import plan_tasks as _pt
    from app.services import recipe_offer_store as _offer_store

    # 按命名空间分桶的假存储：配方与固化询问共用一个替身但不互相串味。
    store: dict = {}

    class _FakeProgress:
        @staticmethod
        def load(namespace):
            return dict(store.get(namespace) or {})

        @staticmethod
        def save(namespace, tasks, limit=100):
            store[namespace] = dict(tasks)

    monkeypatch.setattr(_pt, "task_progress_store", _FakeProgress)
    monkeypatch.setattr(_offer_store, "task_progress_store", _FakeProgress)
    monkeypatch.setattr(_pt, "_agent_access_mode", lambda: "full")

    decisions = [
        {"tool": "doc.create_repo",
         "params": {"base": str(tmp_path), "rel_path": "固化测试.md",
                    "content": "# 四季套装"}},
        {"done": True, "reply": "已生成套装文档"},
    ]
    calls = {"n": 0}

    def fake_chat(base, key, model, system, user, **kw):
        idx = min(calls["n"], len(decisions) - 1)
        calls["n"] += 1
        return json.dumps(decisions[idx], ensure_ascii=False)

    task_text = "生成一份角色外貌综合文档"
    # 注意：ctx 里**不能**给 thread_id——本节点 `repo_id = ctx.repo_id or ctx.thread_id`，
    # 给了 thread_id 作品域就变成 `<tmp>/<thread_id>/`，落盘路径跟着变。
    ctx = _ctx(output_dir=str(tmp_path), chat_fn=fake_chat)
    capability_sandbox._reset_for_tests()
    try:
        # 第 1 轮：清单里没有同类 → 只问，不跑（零 LLM 调用），询问落库待答
        asked = ag.plan_compiler_node({
            "user_text": task_text, "images": [],
            "_ctx": dict(ctx, message=task_text),
        })
        assert "要不要为它固化成一条" in asked["result_text"]
        assert calls["n"] == 0
        assert store["recipe_offers"], "询问必须落库，否则用户没法回答"

        # 第 2 轮：回「要」→ 用询问里存的**原始任务文本**继续（本轮消息只是这个词）
        out = ag.plan_compiler_node({
            "user_text": "要", "images": [],
            "_ctx": dict(ctx, message="要"),
        })
    finally:
        for lease in capability_sandbox.active():
            capability_sandbox.revoke(lease["id"])
        capability_sandbox._reset_for_tests()

    assert "已生成套装文档" in out["result_text"]
    assert "[[recipe:" in out["result_text"] and out["result_text"].rstrip().endswith("]]")
    # 文档真写进了作品 docs/（固化的是真实成功轨迹，不是嘴上说说）
    assert (tmp_path / "docs" / "固化测试.md").is_file()
    recipes = list(store["plan_recipes"].values())
    assert len(recipes) == 1
    recipe = recipes[0]
    assert recipe["status"] == "draft" and recipe["origin"] == "fabric"
    assert [s["operation"] for s in recipe["plan"]["steps"]] == ["doc.create_repo"]
    # 答复轮消费掉询问，不留悬空状态
    assert not store["recipe_offers"]


def test_固化清单注入full循环上下文(monkeypatch, tmp_path):
    from app.services import capability_sandbox
    from app.services import plan_tasks as _pt

    progress: dict = {}

    class _FakeProgress:
        @staticmethod
        def load(namespace):
            return dict(progress)

        @staticmethod
        def save(namespace, tasks, limit=100):
            progress.clear()
            progress.update(tasks)

    monkeypatch.setattr(_pt, "task_progress_store", _FakeProgress)
    monkeypatch.setattr(_pt, "_agent_access_mode", lambda: "full")
    progress["r1"] = {
        "id": "r1", "name": "套装文档流程", "intent": "生成套装文档",
        "status": "saved", "origin": "fabric",
        "plan": {"steps": [{"operation": "doc.create_repo"}]}, "created_at": 1,
    }

    def fake_chat(base, key, model, system, user, **kw):
        assert "【固化流程预设】" in system or "【固化流程预设】" in user, \
            "已保留配方清单必须注入自由循环上下文"
        return json.dumps({"done": True, "reply": "ok"}, ensure_ascii=False)

    capability_sandbox._reset_for_tests()
    try:
        ag.plan_compiler_node({
            "user_text": "再来一次", "images": [],
            "_ctx": _ctx(output_dir=str(tmp_path), chat_fn=fake_chat),
        })
    finally:
        for lease in capability_sandbox.active():
            capability_sandbox.revoke(lease["id"])
        capability_sandbox._reset_for_tests()


def test_live_trace_过程事件映射为sink文本并同落盘(monkeypatch, tmp_path):
    """555631d 可视化验收：非流式节点（计划编译/自由循环）内部阻塞时，思考/回填/
    工具/步骤受阻等事件实时转成对话流 trace 文本（前端「执行过程」面板），
    同事件仍落盘 run_trace（GET /agent/trace 离线回看数据源）。"""
    from app.services import run_trace
    trace_file = tmp_path / "agent-trace.jsonl"
    monkeypatch.setattr(run_trace, "TRACE_FILE", trace_file)
    emitted: list[dict] = []
    emit = ag._live_trace(_ctx(
        repo_id="work", turn_id="t1", thread_id="work",
        stream_sink=emitted.append,
    ), "plan_compiler")

    emit("model.request")
    emit("plan.sections_filled", count=14, stage="pre_validate")
    emit("tool.call", operation="file.list_dir")
    emit("plan.step_blocked", operation="workflow.submit_batch", reason="needs_approval")
    emit("plan.compiled", status="ok", steps=3)
    emit("llm.retry-started", attempt=2)  # 未特化事件走兜底行，仍可见而非黑盒

    assert [e["trace"] for e in emitted] == [
        "🤔 模型正在思考…",
        "🧩 回填 14 个套装提示词",
        "🔧 调用工具：file.list_dir",
        "⛔ 步骤受阻：workflow.submit_batch（needs_approval）",
        "✅ 计划编译完成（3 步）",
        "🔄 llm.retry-started",
    ]
    events = run_trace.read_recent("work", turn_id="t1")
    assert [item["event"] for item in events] == [
        "model.request", "plan.sections_filled", "tool.call",
        "plan.step_blocked", "plan.compiled", "llm.retry-started",
    ]


def test_answer_node_用户停止显示中断而非回答失败(monkeypatch):
    """2026-09-04 A 方案：answer_node 走 _chat_with_optional_stream（非流式时也经
    chat_messages_stream 可取消通道）。cancel_event 置位 → 流式层抛 RoleplayInterrupted
    → answer_node 必须把它映射为 _interrupted 标记（此前 except Exception 会显示成
    「回答失败：用户已停止生成…」）。"""
    from app.services import roleplay_turn as rt

    stop = threading.Event()
    ctx = _ctx(repo_id="work", thread_id="work", cancel_event=stop,
               chat_base="b", chat_key="k", chat_model="m")

    def fake_stream(base, key, model, messages, on_delta=None, **_kw):
        stop.set()  # 第一个 delta 前用户已点停止
        if on_delta is not None:
            on_delta("半句")  # on_delta 内查 cancel → 抛 RoleplayInterrupted
        return "半句"

    monkeypatch.setattr(ag._llm, "chat_messages_stream", fake_stream)
    out = ag.answer_node({"user_text": "你好", "images": [], "_ctx": ctx})
    assert out.get("_interrupted") is True
    assert not (out.get("result_text") or "").startswith("回答失败")
    assert rt.RoleplayInterrupted is not None  # 引用保真


def test_answer_node_非用户停止的普通失败仍报回答失败(monkeypatch):
    """answer_node 的 except Exception 兜底不能被 RoleplayInterrupted 分支吃掉：
    真实模型错误（非停止）仍走「回答失败」文案。"""
    ctx = _ctx(repo_id="work", thread_id="work",
               chat_base="b", chat_key="k", chat_model="m")

    def broken_stream(base, key, model, messages, on_delta=None, **_kw):
        raise RuntimeError("上游 502")

    monkeypatch.setattr(ag._llm, "chat_messages_stream", broken_stream)
    out = ag.answer_node({"user_text": "你好", "images": [], "_ctx": ctx})
    assert not out.get("_interrupted")
    assert (out.get("result_text") or "").startswith("回答失败")
    assert "502" in out["result_text"]


def test_answer_node_首字未到也能立即停止(monkeypatch):
    """2026-10-21 用户实锤：点停止后上游仍跑到 61.4s（网关记「客户端断开」，
    $1.3031、输出 0）。取消检查此前只挂在 on_delta 回调里——中转缓冲式下发时
    首字可能 40~80s 才到，那段时间停止信号无人消费。
    锁：_chat_with_optional_stream 必须把 should_abort 接到流式层（无增量也能停）。
    """
    from app.services import roleplay_turn as rt

    stop = threading.Event()
    ctx = _ctx(repo_id="work", thread_id="work", cancel_event=stop,
               chat_base="b", chat_key="k", chat_model="m")
    seen: dict = {}

    def fake_stream(base, key, model, messages, on_delta=None, should_abort=None, **_kw):
        seen["should_abort"] = should_abort
        assert should_abort is not None  # 接线：缺失即回到「只能等下一个 delta」
        stop.set()                        # 用户在首字到达前点了停止
        assert should_abort() is True
        raise rt.RoleplayInterrupted("用户已停止生成")

    monkeypatch.setattr(ag._llm, "chat_messages_stream", fake_stream)
    out = ag.answer_node({"user_text": "你好", "images": [], "_ctx": ctx})
    assert out.get("_interrupted") is True
    assert seen["should_abort"] is not None


# ── ② 纪要引用透出（2026-09-18）：召回引用行进 trace 列表 + 流式通道 ─────────

def test_本轮引用行随roleplay_trace透出(monkeypatch, tmp_path):
    """② 纪要引用透出：召回命中后「📖 本轮引用」行并入节点 trace（非流式随节点
    trace 下发，无明细）；计数 = 纪要条数 + 知识条目行数。无命中 → 无引用行。"""
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
        curator_gate=1.0, index_fn=lambda text, title: None,
    )

    def fake_recall(*a, **k):
        out = k.get("citations_out")
        if out is not None:
            out.extend([
                {"turn": "4-6", "layer": 0, "overview": "温泉夜话坦白身世"},
                {"turn": "10-12", "layer": 0, "overview": "雪山救援后态度转暖"},
            ])
        return "往事纪要候选"

    monkeypatch.setattr(agr, "_agency_prelude", lambda ctx, text: (deps, 1, 0.0, ""))
    monkeypatch.setattr(agr, "_agency_propose", lambda ctx, d, aff, wb, text="": ("", False))
    monkeypatch.setattr(agr, "_agency_writeback",
                        lambda ctx, d, reply, turn, aff, lost: (reply, [], {}, {}))
    monkeypatch.setattr(agr, "_resolve_preset", lambda ctx, wb, **k: ([], None, False, [], []))
    monkeypatch.setattr(agr, "_resolve_worldbook", lambda ctx, q: "")
    monkeypatch.setattr(agr, "_rag_recall_text",
                        lambda *a, **k: "- 知识条目甲\n- 知识条目乙")
    monkeypatch.setattr(agr, "_table_recall_text", lambda *a, **k: "")
    monkeypatch.setattr(ra, "recall_chronicle", fake_recall)
    monkeypatch.setattr(am, "_table_maintenance", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ra, "maybe_illustrate", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)

    def fake_chat_stream(base, key, model, messages, on_delta, *, temperature, **kw):
        return "<content>正文</content>"

    monkeypatch.setattr(ag._llm, "chat_messages_stream", fake_chat_stream)
    ctx = _card_ctx(output_dir=str(tmp_path), repo_id="work")
    out = ag.roleplay_node({"user_text": "继续剧情", "images": [], "_ctx": ctx})
    ag.join_maintenance_threads()

    cite_lines = [ln for ln in (out.get("trace") or []) if ln.startswith("📖 本轮引用")]
    assert cite_lines == ["📖 本轮引用：往事纪要 ×2 · 知识条目 ×2"]


def test_本轮引用flush_并入trace且流式通道带明细():
    pushed: list[dict] = []
    ctx = {"stream_output": True, "stream_sink": pushed.append}
    ctx["_rp_citations"] = {
        "text": "📖 本轮引用：往事纪要 ×1 · 知识条目 ×0",
        "detail": "[4-6] 温泉夜话坦白身世",
    }
    trace: list = ["🎭 剧情扮演中…"]
    ag._rp_flush_citation_trace(ctx, trace)
    assert trace == ["🎭 剧情扮演中…", "📖 本轮引用：往事纪要 ×1 · 知识条目 ×0"]
    assert pushed == [{"trace": "📖 本轮引用：往事纪要 ×1 · 知识条目 ×0",
                       "detail": "[4-6] 温泉夜话坦白身世"}]
    assert "_rp_citations" not in ctx  # flush 消费掉暂存，不残留到下一轮


def test_本轮引用flush_非流式只落trace_无暂存无操作():
    trace: list = []
    ag._rp_flush_citation_trace({}, trace)  # 无暂存：无操作
    assert trace == []
    ctx = {"_rp_citations": {"text": "📖 本轮引用：往事纪要 ×1", "detail": "d"}}
    ag._rp_flush_citation_trace(ctx, trace)  # 非流式：只落 trace，不进通道
    assert trace == ["📖 本轮引用：往事纪要 ×1"]


def test_流式trace通知可携带明细_两参调用与旧线逐字节一致():
    """② 纪要引用透出：_notify_stream_trace 新增可选 detail；两参旧调用线格式
    必须与改动前逐字节一致（既有自愈提示锁不变）。"""
    pushed: list[dict] = []
    ctx = {"stream_output": True, "stream_sink": pushed.append}
    ag._notify_stream_trace(ctx, "🔧 调用工具：x", detail='{"a":1}')
    assert pushed == [{"trace": "🔧 调用工具：x", "detail": '{"a":1}'}]
    ag._notify_stream_trace(ctx, "⚠️ 提示")
    assert pushed[-1] == {"trace": "⚠️ 提示"}  # 无 detail 不携带（旧前端宽松兼容）


# ── 高潮点判据档（2026-09-18）：四态降级 + 缺块路径短路语义 ──────────────────────
# 判据档**常开**（用户定案去掉启用开关）：任何一步不成立都必须返回 ""，让调用方短路到
# 现有中文词表。该短路是本层的**失败上界**——最坏情况就是回到词表档，不会中断出图。

_ANCHOR_CLEAN = "前文第一段。\n\n浑身无力的清夏靠在公告栏上。\n\n后文收束段。"
_ANCHOR_OK_RAW = (
    '{"anchor": "浑身无力的清夏靠在公告栏上。", "composition": "中景", '
    '"subjects": ["清夏"], "reason": "事后定型态"}'
)


def _rubric_on():
    """monkeypatch 用：把「用户判据」塞进 illustration_prompt_store 的生效入口。"""
    return lambda: "用户判据"


def _trace_capture(monkeypatch):
    from app.services import run_trace

    trace: list = []
    monkeypatch.setattr(run_trace, "emit", lambda ctx, event, **data: trace.append((event, data)))
    return trace


def test_判据为空时零调用且短路回词表(monkeypatch):
    """防御性闸：store 侧保证判据非空，但一旦拿不到判据也绝不能去调模型。"""
    from app.services import illustration_prompt_store

    monkeypatch.setattr(illustration_prompt_store, "effective_climax_anchor_rubric", lambda: "")
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: pytest.fail("无判据不得发起调用"))
    trace = _trace_capture(monkeypatch)

    assert aag._illustration_anchor_intent(_ctx(), _ANCHOR_CLEAN, {"present": ["清夏"]}) == ("", {})
    assert trace[-1][1]["status"] == "skipped"
    assert trace[-1][1]["reason"] == "rubric_empty"


def test_判据空正文早退留痕(monkeypatch):
    from app.services import illustration_prompt_store

    monkeypatch.setattr(illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on())
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: pytest.fail("正文为空不得发起调用"))
    trace = _trace_capture(monkeypatch)

    assert aag._illustration_anchor_intent(_ctx(), "   ", {"present": []}) == ("", {})
    assert trace[-1][1]["status"] == "skipped"
    assert trace[-1][1]["reason"] == "narrative_empty"


def test_判据缺模型配置静默早退(monkeypatch):
    from app.services import illustration_prompt_store

    monkeypatch.setattr(illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on())
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: pytest.fail("缺配置不得发起调用"))
    trace = _trace_capture(monkeypatch)

    assert aag._illustration_anchor_intent({}, _ANCHOR_CLEAN, {"present": []}) == ("", {})
    assert trace[-1][1]["status"] == "skipped"
    assert trace[-1][1]["reason"] == "model_config_missing"


def test_判据调用异常留痕并降级(monkeypatch):
    from app.services import illustration_prompt_store

    def boom(*_a, **_k):
        raise RuntimeError("上游 400：content filter")

    monkeypatch.setattr(illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on())
    monkeypatch.setattr(aag._llm, "chat", boom)
    trace = _trace_capture(monkeypatch)

    assert aag._illustration_anchor_intent(_ctx(), _ANCHOR_CLEAN, {"present": []}) == ("", {})
    data = trace[-1][1]
    assert data["status"] == "error" and data["error_type"] == "RuntimeError"
    assert "400" in data["error_message"]


def test_判据anchor不落文判无效(monkeypatch):
    from app.services import illustration_prompt_store

    monkeypatch.setattr(illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on())
    monkeypatch.setattr(
        aag._llm, "chat",
        lambda *a, **k: '{"anchor": "她心满意足地笑了。", "reason": "编的"}',
    )
    trace = _trace_capture(monkeypatch)

    assert aag._illustration_anchor_intent(_ctx(), _ANCHOR_CLEAN, {"present": []}) == ("", {})
    data = trace[-1][1]
    assert data["status"] == "invalid" and data["parsed"] is True
    assert data["anchor_candidate"] == "她心满意足地笑了。"


def test_判据命中时带只读合同且必带超时(monkeypatch):
    """验收两条红线：system = 用户判据 + 程序侧只读合同；调用必带超时。"""
    from app.services import illustration_prompt_store, scene_illustration as si

    monkeypatch.setattr(illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on())
    captured: dict = {}

    def fake_chat(base, key, model, system, user, **kw):
        captured.update(system=system, user=user, timeout=kw.get("timeout"))
        return _ANCHOR_OK_RAW

    monkeypatch.setattr(aag._llm, "chat", fake_chat)
    trace = _trace_capture(monkeypatch)

    anchor, detail = aag._illustration_anchor_intent(
        _ctx(), _ANCHOR_CLEAN, {"present": ["清夏"]},
    )
    assert anchor == "浑身无力的清夏靠在公告栏上。"
    assert detail["subjects"] == ["清夏"]
    assert captured["system"].startswith("用户判据")
    assert si.ANCHOR_INTENT_CONTRACT in captured["system"]
    assert captured["timeout"] is not None and captured["timeout"] > 0
    assert "在场角色：清夏" in captured["user"]
    data = trace[-1][1]
    assert data["status"] == "ok" and data["subjects"] == ["清夏"]


# ── 第 57 刀 · 多批生图：判据采集时同步落「去重 + 取前 N」列表 ──────────────────


def _anchor_multi_raw() -> str:
    """判据吐**多锚点数组**：同带相邻两条（应并成一处）+ 一条异带（应留）。"""
    return (
        '{"anchors": ['
        '{"anchor": "浑身无力的清夏靠在公告栏上。", "composition": "中景", '
        '"subjects": ["清夏"], "reason": "事后定型态", "score": 30},'
        '{"anchor": "后文收束段。", "composition": "远景", '
        '"subjects": [], "reason": "收束", "score": 70}'
        "]}"
    )


def _anchor_multi_clean() -> str:
    # 段 0 环境 / 段 1 清夏（无接触）/ 段 2 收束 —— 两条锚点带均为 `.` ⇒ 不并、各留一条。
    return "前文第一段。\n\n浑身无力的清夏靠在公告栏上。\n\n后文收束段。"


def test_第57刀_判据采集同步落去重后的锚点列表进ctx(monkeypatch):
    """验收：判据命中时，除返回单条锚点外，还要把**去重 + 取前 N** 的列表写进 ctx。

    ⚠ 两条链必须**同源**：列表第 0 条恒等于 `resolve_anchor_intent` 单条路径的锚点，
      否则单张出图与多批出图第一张会各画一处。
    """
    from app.services import illustration_prompt_store

    monkeypatch.setattr(
        illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on(),
    )
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: _anchor_multi_raw())
    _trace_capture(monkeypatch)
    ctx = _ctx()

    anchor, _ = aag._illustration_anchor_intent(
        ctx, _anchor_multi_clean(), {"present": ["清夏"]},
    )

    # 单条路径：分最高那条（收束段 70 > 清夏段 30）
    assert anchor == "后文收束段。"
    items = ctx["illustration_anchor_intents"]
    # 默认数量 0 ⇒ 1 ⇒ 只留分最高那条 ⇒ 与单条路径**同源**
    assert len(items) == 1
    assert items[0]["anchor"] == anchor


def test_第57刀_面板数量大于锚点数时全部出图(monkeypatch):
    """口径锁（用户定案）：数量 ≥ 锚点数 ⇒ 全部出图；结果保持正文序。"""
    from app.services import illustration_prompt_store

    monkeypatch.setattr(
        illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on(),
    )
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: _anchor_multi_raw())
    _trace_capture(monkeypatch)
    ctx = _ctx(illustration_batch_count=5)

    aag._illustration_anchor_intent(ctx, _anchor_multi_clean(), {"present": ["清夏"]})

    items = ctx["illustration_anchor_intents"]
    # 正文序：清夏段在前、收束段在后（不是按分数 70/30 排）
    assert [item["anchor"] for item in items] == [
        "浑身无力的清夏靠在公告栏上。", "后文收束段。",
    ]


def test_第57刀_面板数量小于锚点数时按分取前N(monkeypatch):
    """口径锁：数量 < 锚点数 ⇒ 按 score 降序取前 N，结果仍保持正文序。"""
    from app.services import illustration_prompt_store

    monkeypatch.setattr(
        illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on(),
    )
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: _anchor_multi_raw())
    _trace_capture(monkeypatch)
    ctx = _ctx(illustration_batch_count=1)

    aag._illustration_anchor_intent(ctx, _anchor_multi_clean(), {"present": ["清夏"]})

    assert [item["anchor"] for item in ctx["illustration_anchor_intents"]] == ["后文收束段。"]


def test_第57刀_锚点列表只在判据命中时落ctx(monkeypatch):
    """零回归锁：判据不命中（evidence 不过）⇒ **不写** `illustration_anchor_intents`，
    调用方照旧走单条/词表路径，行为与改造前逐字一致。"""
    from app.services import illustration_prompt_store

    monkeypatch.setattr(
        illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on(),
    )
    bad = '{"anchors": [{"anchor": "她心满意足地笑了。", "score": 99}]}'
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: bad)
    _trace_capture(monkeypatch)
    ctx = _ctx()

    assert aag._illustration_anchor_intent(
        ctx, _ANCHOR_CLEAN, {"present": ["清夏"]},
    ) == ("", {})
    assert "illustration_anchor_intents" not in ctx


def test_第57刀_面板数量非法时收敛到单张():
    """边界锁：负数 / 非数 / None ⇒ 1（单张），绝不透传脏值。"""
    assert aag._anchor_batch_count({}) == 1
    assert aag._anchor_batch_count({"illustration_batch_count": 0}) == 1
    assert aag._anchor_batch_count({"illustration_batch_count": -4}) == 1
    assert aag._anchor_batch_count({"illustration_batch_count": "x"}) == 1
    assert aag._anchor_batch_count({"illustration_batch_count": None}) == 1
    assert aag._anchor_batch_count({"illustration_batch_count": 7}) == 7


# ── 第 57 刀 · 第 3 步：多事件派发（用户要求统一验证 1／2／999 张三档）──────────


def test_第57刀_指定锚点与判据档同一条链且不重复调判据(monkeypatch):
    """零回归锁 ＋ 省调用锁：

    `anchor_override` 与「判据刚好给出同一锚点」**逐字节等价**（同一锚点 ⇒ 同一画面），
    且**不再调用判据 LLM** —— 多批的第 2..N 张若各花一次判据调用，N 张图就多 N-1 次成本。
    """
    anchor = "浑身无力的清夏靠在公告栏上。"
    calls: list = []
    monkeypatch.setattr(
        asc, "_illustration_anchor_intent",
        lambda ctx, clean, sig: (calls.append(1), ("", {}))[1],
    )

    via_override = aag._wb_comfy_scene_target(
        {}, "", {}, _ANCHOR_CLEAN, _anchor_sig(), _anchor_pool(),
        prompt_override="", motion=0, user_text="",
        anchor_override=anchor, subjects_override=["清夏"],
    )

    assert calls == []                                   # 未调判据 LLM
    assert via_override["requested_anchor"] == anchor
    via_judge = _run_scene_target_ctx(
        monkeypatch, _ANCHOR_CLEAN, (anchor, {"subjects": ["清夏"]}), {},
    )
    assert via_override == via_judge                     # 与判据档逐字节等价


def _stub_comfy_chain(monkeypatch) -> dict:
    """把多批编译链换成轻量假件；返回 {built: 被编译的锚点, video: compile_video 实参}。

    假件**模拟真实门**：`compile_video` 且 `ctx["comfy_video"]` 才产出 `video_request`
    （与 `_wb_frames_and_video` 同构），这样「每张 extra 到底有没有视频成稿」在单测里
    就是可断言的事实，而不是只看一个布尔形参。
    """
    built: list[str] = []
    video: list = []

    def fake_target(ctx, repo_id, plan, clean, sig, pool, *, prompt_override, motion,
                    user_text, anchor_override="", subjects_override=None):
        built.append(anchor_override)
        return {"request_prompt": f"p:{anchor_override}", "plan_retargeted": False,
                "requested_anchor": anchor_override, "motion": 7}

    def fake_frames(ctx, illustrate_req, scene_spec, clean, transition_decision, motion,
                    *, compile_video=True):
        video.append(compile_video)
        if not compile_video or not ctx.get("comfy_video"):
            return ""
        anchor = str(illustrate_req.get("anchor") or "")
        illustrate_req["video_mode"] = str(ctx.get("video_mode") or "climax")
        illustrate_req["video_request"] = {
            "mode": "climax",
            "submit": {"prompt": f"VIDEO[{anchor}]"},
        }
        return f"VIDEO[{anchor}]"

    monkeypatch.setattr(asc, "_wb_comfy_scene_target", fake_target)
    monkeypatch.setattr(asc, "_wb_comfy_scene_spec", lambda *a, **k: {"narrative": "n"})
    monkeypatch.setattr(asc, "_wb_comfy_profile", lambda *a, **k: {"narrative": "n"})
    monkeypatch.setattr(
        asc, "_wb_comfy_illustrate_env",
        lambda ctx, plan, sig, target, spec, *, motion: {
            "prompt": target["request_prompt"], "anchor": target["requested_anchor"],
            "motion": motion,
        },
    )
    monkeypatch.setattr(asc, "_wb_frames_and_video", fake_frames)
    return {"built": built, "video": video}


# A（仲裁后按插入点去重）要求锚点**真的在正文里**（`illustration_anchor_offset` 内部有
# 相似度→词表的多级兜底，假锚点会全部塌缩到同一个兜底 offset）⇒ 测试文本必须含各锚点。
_ANCHOR_TEXT = "甲处的画面。\n\n乙处的画面。\n\n丙处的画面。\n\n丁处的画面。"


def _run_extra(ctx, *, primary_anchor="", motion=0, primary_prompt=""):
    return aag._wb_comfy_extra_requests(
        ctx, "", {}, _ANCHOR_TEXT, _anchor_sig(), _anchor_pool(),
        primary_anchor=primary_anchor, primary_motion=motion,
        prompt_override="", transition_decision="", user_text="",
        primary_prompt=primary_prompt,
    )


def _anchors(*pairs):
    return {"illustration_anchor_intents": [
        {"anchor": name, "score": score} for name, score in pairs
    ]}


def test_第57刀_第1张_批量数目缺省时不产出任何额外请求(monkeypatch):
    """三档之一：**1 张**（批量数目 0/缺省 = 1）⇒ 只出主图，零额外请求、零额外编译。"""
    stub = _stub_comfy_chain(monkeypatch)
    ctx = _anchors(("甲处", 10), ("乙处", 90))

    assert _run_extra(ctx, primary_anchor="乙处") == []
    assert stub["built"] == []


def test_第57刀_第2张_按分取前二且跳过已出图的主锚点(monkeypatch):
    """三档之二：**2 张** ⇒ 取分最高两条；主图已占了其中一条 ⇒ 只多编译**剩下那一条**。"""
    stub = _stub_comfy_chain(monkeypatch)
    ctx = {**_anchors(("甲处", 10), ("乙处", 90), ("丙处", 50)),
           "illustration_batch_count": 2}

    out = _run_extra(ctx, primary_anchor="乙处")

    assert [item["anchor"] for item in out] == ["丙处"]      # 乙处=主图、丙处=新增
    assert stub["built"] == ["丙处"]
    # 2026-10-31 用户定案：高潮多张＝每张各配一支动作延伸视频 ⇒ 第 2 张照常编视频
    assert stub["video"] == [True]


def test_第57刀_第999张_所有锚点都出图且保持正文序(monkeypatch):
    """三档之三：**999 张**（≥ 锚点数）⇒ 所有锚点各出一张，**结果保持正文序**
    （图跟着剧情位置摆，不按分数排）。"""
    stub = _stub_comfy_chain(monkeypatch)
    ctx = {**_anchors(("甲处", 10), ("乙处", 90), ("丙处", 50), ("丁处", 30)),
           "illustration_batch_count": 999}

    out = _run_extra(ctx, primary_anchor="乙处")

    assert [item["anchor"] for item in out] == ["甲处", "丙处", "丁处"]   # 去掉主图乙处
    assert stub["built"] == ["甲处", "丙处", "丁处"]
    assert stub["video"] == [True, True, True]


# ── 2026-10-31 · 高潮多批各配一支视频（用户口径「多张高潮图视频＝一个参考图一个动作
#    延伸视频，生成多个批次」）：旧口径 extras 传 compile_video=False ⇒ 第 2..N 张
#    事件里没有 video_prompt ⇒ 前端闸门判 false ⇒ N 张图只有 1 支视频。───


def test_视频多批_高潮多张每张各带一支视频成稿(monkeypatch):
    """验收：climax + comfy_video ⇒ 每张 extra 自己的 `video_request`，且**逐张不同**
    （各取材到自己那一处画面，不是把主图那支复制 N 份）。"""
    _stub_comfy_chain(monkeypatch)
    ctx = {**_anchors(("甲处", 10), ("乙处", 90), ("丙处", 50)),
           "illustration_batch_count": 9, "comfy_video": True}

    out = _run_extra(ctx, primary_anchor="乙处")

    prompts = [str(((item.get("video_request") or {}).get("submit") or {}).get("prompt") or "")
               for item in out]
    assert prompts == ["VIDEO[甲处]", "VIDEO[丙处]"]
    assert all(item.get("video_mode") == "climax" for item in out)


def test_视频多批_视频工作流未配时extra不产出视频请求(monkeypatch):
    """零成本锁：`comfy_video` 关（没配视频模板）⇒ extras 不得带 `video_request`，
    与主链路「关＝零 token」同构。"""
    _stub_comfy_chain(monkeypatch)
    ctx = {**_anchors(("甲处", 10), ("乙处", 90)),
           "illustration_batch_count": 9, "comfy_video": False}

    out = _run_extra(ctx, primary_anchor="乙处")

    assert out and all("video_request" not in item for item in out)


def test_视频多批_首尾帧的extra不编正片仍走段桥(monkeypatch):
    """边界锁：firstlast 的 extra 由**段桥**承载视频（transition 编译），
    本步不得再编一支正片（否则一次出两支、且帧语义重复）。"""
    stub = _stub_comfy_chain(monkeypatch)
    ctx = {**_anchors(("甲处", 10), ("乙处", 90)),
           "illustration_batch_count": 9, "comfy_video": True, "video_mode": "firstlast"}

    out = _run_extra(ctx, primary_anchor="乙处", primary_prompt="主画面")

    assert stub["video"] == [False]
    assert all("video_request" not in item for item in out)


def test_视频多批_出口每条事件各带自己的视频成稿():
    """出口锁（service 层绿≠事件带得出）：extras 的 `video_request` 必须**逐条**进
    `illustrate_request` 事件，否则前端闸门永远拿不到 `video_prompt`
    ⇒ 第 2..N 张又没有视频（2026-10-31 用户报的正是这个形状）。"""
    events = aag._streamed_illustration_events([
        {"id": "illo-1", "prompt": "p1", "motion": 1, "actors": [], "anchor_offset": 5,
         "video_mode": "climax", "video_request": {"submit": {"prompt": "V1"}}},
        {"id": "illo-1-1", "prompt": "p2", "motion": 1, "actors": [], "anchor_offset": 40,
         "video_mode": "climax", "video_request": {"submit": {"prompt": "V2"}}},
    ])

    requests = [event["illustrate_request"] for event in events]
    assert [item.get("video_prompt") for item in requests] == ["V1", "V2"]
    assert [item.get("video_mode") for item in requests] == ["climax", "climax"]
    assert [event["id"] for event in events] == ["illo-1", "illo-1-1"]


def test_段桥_转场编译起点是上一画面终点是本画面(monkeypatch):
    """2026-10-31 用户口径「前面的图片放首帧、后面的图片放尾帧生成视频」的**文字面**锁。

    `build_video_request(mode="transition")` 的形参语义（video_prompt.py 真源）：
    `prev_tail_desc`=起点描述、`first_frame_desc`=**终点**描述。旧口径把同一个
    `_prev_pic`（上一画面）同时喂给两个形参 ⇒ 编出的分镜首尾同文，视频原地不动。

    主锚点取正文**最早**那条（甲处），extra 落在它之后 ⇒ 「前面的图片」就是主图。
    """
    from app.services import video_prompt as vp

    _stub_comfy_chain(monkeypatch)
    captured: list[dict] = []

    def fake_build(**kwargs):
        captured.append(kwargs)
        return {"mode": "transition", "submit": {"prompt": "T"}}

    monkeypatch.setattr(vp, "build_video_request", fake_build)
    ctx = {**_anchors(("甲处", 90), ("乙处", 10)),
           "illustration_batch_count": 9, "comfy_video": True, "video_mode": "firstlast"}

    out = _run_extra(ctx, primary_anchor="甲处", primary_prompt="上一画面")

    assert captured, "firstlast 的 extra 必须编译段桥转场请求"
    assert out[0]["transition_video_request"]["submit"]["prompt"] == "T"
    assert captured[0]["prev_tail_desc"] == "上一画面"       # 起点=前面的图片
    assert captured[0]["first_frame_desc"] == "p:乙处"       # 终点=后面的图片（本锚点画面）
    assert captured[0]["prev_tail_desc"] != captured[0]["first_frame_desc"]


def test_段桥_链按正文序逐张传递上一画面(monkeypatch):
    """链锁（第 74 刀）：链序＝**正文序**，不是派发序（`select_anchor_intents` 返回列表序）。

    夹具故意把派发序排成 丙处→乙处（`_ANCHOR_TEXT` 里乙处在前），主图=甲处（正文最早）：
      · 旧写法按派发序 ⇒ 丙处（正文最晚）吃到「主画面」当起点、乙处反倒从丙处起步，
        与前端按 `anchor_offset` 取到的「上一张成图」指向不同画面（≥3 张必错位）。
      · 现在按偏移排 ⇒ 编译顺序与起点逐张跟着剧情走。
    """
    from app.services import video_prompt as vp

    _stub_comfy_chain(monkeypatch)
    captured: list[dict] = []
    monkeypatch.setattr(vp, "build_video_request",
                        lambda **kwargs: (captured.append(kwargs),
                                          {"mode": "transition", "submit": {"prompt": "T"}})[1])
    ctx = {**_anchors(("甲处", 90), ("丙处", 60), ("乙处", 50)),
           "illustration_batch_count": 9, "comfy_video": True, "video_mode": "firstlast"}

    out = _run_extra(ctx, primary_anchor="甲处", primary_prompt="主画面")

    assert [item["anchor"] for item in out] == ["丙处", "乙处"]   # 派发序仍是列表序
    # 编译序＝正文序：乙处先、丙处后；起点逐张前移，绝不回头取更晚的画面
    assert [item["prev_tail_desc"] for item in captured] == ["主画面", "p:乙处"]
    assert [item["first_frame_desc"] for item in captured] == ["p:乙处", "p:丙处"]


def test_段桥_主图之前的桥起点退上一楼层尾帧(monkeypatch):
    """边界锁：主锚点不在正文开头时，排在它**之前**的那张 extra 在本楼层没有「前面的图片」。
    起点必须退到**上一楼层的尾帧画面**（`_resolve_prev_tail_desc`，与主槽正片 W3 转场同一真源），
    而不是空串（空串＝分镜没有起点），更不是拿更晚的主图倒回去当起点。"""
    from app.services import video_prompt as vp

    _stub_comfy_chain(monkeypatch)
    captured: list[dict] = []
    monkeypatch.setattr(vp, "build_video_request",
                        lambda **kwargs: (captured.append(kwargs),
                                          {"mode": "transition", "submit": {"prompt": "T"}})[1])
    monkeypatch.setattr(asc, "_resolve_prev_tail_desc", lambda ctx: "上一楼层收束画面")
    ctx = {**_anchors(("甲处", 10), ("乙处", 90), ("丙处", 50)),
           "illustration_batch_count": 9, "comfy_video": True, "video_mode": "firstlast"}

    out = _run_extra(ctx, primary_anchor="乙处", primary_prompt="主画面")

    # 正文序：甲处 < 乙处(主图) < 丙处 ⇒ 甲处起步于上楼层尾帧，丙处起步于主图
    assert [item["anchor"] for item in out] == ["甲处", "丙处"]
    assert [item["prev_tail_desc"] for item in captured] == ["上一楼层收束画面", "主画面"]
    assert [item["first_frame_desc"] for item in captured] == ["p:甲处", "p:丙处"]


def test_段桥_出口每条事件各带自己的转场成稿():
    """出口锁：段桥的 `transition_video_request` 必须逐条进 `illustrate_request` 事件，
    且**不得**带 `video_prompt`（前端 `isSegmentBridgeEvent` 以「无正片＋有转场」认桥）；
    第 74 刀补：`bridge_prev_slot`（图片1 取自哪一张成图）同批出口，两个出口都要带。"""
    from app.services import agent_stream as astream

    recs = [
        {"id": "illo-1", "prompt": "p1", "motion": 1, "actors": [], "anchor_offset": 5,
         "video_mode": "climax", "video_request": {"submit": {"prompt": "V1"}}},
        {"id": "illo-1-1", "prompt": "p2", "motion": 1, "actors": [], "anchor_offset": 40,
         "video_mode": "firstlast", "video_request": {}, "batch_index": 1,
         "transition_video_request": {"submit": {"prompt": "T2"}},
         "bridge_prev_slot": "illo-1:last"},
    ]

    for events in (aag._streamed_illustration_events(recs),
                   astream._ordered_illustration_events(_ANCHOR_TEXT, recs)):
        # 有序出口会夹带 delta 文本事件 ⇒ 只取出图请求
        requests = [event["illustrate_request"] for event in events
                    if "illustrate_request" in event]
        assert [item.get("video_prompt") for item in requests] == ["V1", None]
        assert [item.get("transition_video_prompt") for item in requests] == [None, "T2"]
        assert [item.get("bridge_prev_slot") for item in requests] == [None, "illo-1:last"]
        assert [item.get("batch_index") for item in requests] == [None, 1]


def test_第57刀_首尾帧模式不参与多批(monkeypatch):
    """第 59 刀改约：firstlast 模式下 extras **不再整跳过** —— 每张编译为段桥
    （首帧=上一张画面、尾帧=本锚点画面；衔接不上 ⇒ 前端退高潮片段延续）。
    无 comfy_video（视频工作流未配）⇒ 桥编译短路、rec 无视频字段（纯图降级）。"""
    stub = _stub_comfy_chain(monkeypatch)
    ctx = {**_anchors(("甲处", 10), ("乙处", 90)), "illustration_batch_count": 9,
           "video_mode": "firstlast"}

    assert _run_extra(ctx, primary_anchor="乙处") != []
    assert stub["built"] != []


def test_第57刀_锚点不足两条时不出额外请求(monkeypatch):
    """边界锁：只有一个锚点（或无列表）⇒ 没有「别的锚点」可出 ⇒ 零额外请求。"""
    stub = _stub_comfy_chain(monkeypatch)

    assert _run_extra({**_anchors(("甲处", 10)), "illustration_batch_count": 5}) == []
    assert _run_extra({"illustration_batch_count": 5}) == []
    assert stub["built"] == []


def test_第57刀_主锚点被仲裁挪走后原锚点不再重复出图(monkeypatch):
    """去重锁：仲裁把主图挪到别的段时，**判据原本那条也不该再出一张**（否则同一处画两张）。"""
    stub = _stub_comfy_chain(monkeypatch)
    ctx = {**_anchors(("甲处", 90), ("乙处", 50)), "illustration_batch_count": 9}

    # 主图判据选的是「甲处」(900 分最高)，但仲裁把它挪到了「别处」⇒ 甲处不得再出。
    out = _run_extra(ctx, primary_anchor="别处")

    assert [item["anchor"] for item in out] == ["乙处"]
    assert stub["built"] == ["乙处"]


def test_第57刀_多槽流式事件各带自己的偏移():
    """派发锁：N 条 rec ⇒ N 个事件，**各带自己的 offset**（前端据此各插到自己的位置）。"""
    events = aag._streamed_illustration_events([
        {"id": "illo-1", "prompt": "p1", "motion": 1, "actors": ["甲"], "anchor_offset": 5},
        {"id": "illo-2", "prompt": "p2", "motion": 1, "actors": ["甲"], "anchor_offset": 40},
    ])

    assert [event["illustrate_request"]["offset"] for event in events] == [5, 40]
    assert [event["id"] for event in events] == ["illo-1", "illo-2"]


def test_第57刀_有序事件按偏移切正文且逐槽插入不丢字():
    """派发锁：非流式路径按 offset 升序切正文 + 逐槽插入；文本不重不漏、槽按位置排。"""
    text = "甲乙丙丁戊己庚辛壬癸" * 10
    events = ag._ordered_illustration_events(text, [
        {"id": "slot-b", "prompt": "pb", "anchor_offset": 60},
        {"id": "slot-a", "prompt": "pa", "anchor_offset": 30},   # 故意逆序传入
    ])

    assert [event.get("id") for event in events if "illustrate_request" in event] == [
        "slot-a", "slot-b",
    ]
    assert "".join(event["delta"] for event in events if "delta" in event) == text


# ── 第 57 刀 · 多批派发【集成】：真接线（判据真解析 → 落 ctx → 主链 → 额外链）───

_MULTI_TEXT = "<content>苏映晚跪倒在石阶前。\n\n她抬手拭去唇角血痕。</content>"
_MULTI_ANCHOR_RAW = (
    '{"anchors": ['
    '{"anchor": "苏映晚跪倒在石阶前。", "composition": "中景", "subjects": ["苏映晚"], '
    '"reason": "高潮动作", "score": 90},'
    '{"anchor": "她抬手拭去唇角血痕。", "composition": "特写", "subjects": ["苏映晚"], '
    '"reason": "余韵", "score": 40}'
    "]}"
)
_MULTI_JUDGED_ANCHORS = {"苏映晚跪倒在石阶前。", "她抬手拭去唇角血痕。"}


def _run_multi_writeback(monkeypatch, tmp_path, ctx, text=_MULTI_TEXT):
    """跑**真** `_agency_writeback` 的 comfy 分支：判据真解析、真落 ctx、真走额外链。

    只换掉两处外部副作用：① 模型调用（换成固定的多锚点 JSON）；
    ② Profile 编译（会打外部 LLM，与本测试无关）。
    """
    from app.services import character_state, illustration_prompt_store

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    monkeypatch.setattr(
        illustration_prompt_store, "effective_climax_anchor_rubric", lambda: "用户判据")
    monkeypatch.setattr(aag._llm, "chat", lambda *a, **k: _MULTI_ANCHOR_RAW)
    monkeypatch.setattr(asc, "_wb_comfy_profile", lambda ctx, plan, spec, retargeted: spec)

    _, _, request, _audio = ag._agency_writeback(
        ctx, deps, text, turn=2, affinity=10.0, lost=False, user_text="继续",
    )
    return request


def _multi_ctx(**over) -> dict:
    ctx = _ctx(
        repo_id="work", card_name="作品名", scene="climax", comfy_illustrate=True,
        appearance_source="worldbook", illustration_actor_names=["苏映晚"],
        prompt_profile="krea2",
    )
    ctx.update(over)
    return ctx


def test_第57刀_集成_单张不挂额外请求键(monkeypatch, tmp_path):
    """三档之一【集成】：批量数目未开 ⇒ `illustrate_req` 上**根本没有** `extra_requests` 键。
    键不存在（而不是空列表）才是真正的零回归 —— 下游 `finalize_turn` 走的是老分支。"""
    request = _run_multi_writeback(monkeypatch, tmp_path, _multi_ctx())

    assert request and request.get("anchor")
    assert "extra_requests" not in request


def test_第57刀_集成_批量两张时挂上另一条锚点(monkeypatch, tmp_path):
    """三档之二【集成】：批量 2 ⇒ 额外链跑通，挂上**另一条**判据锚点（各带自己的提示词）。"""
    request = _run_multi_writeback(
        monkeypatch, tmp_path, _multi_ctx(illustration_batch_count=2),
    )

    extras = request.get("extra_requests") or []
    assert len(extras) == 1
    extra = extras[0]
    assert extra["anchor"] in _MULTI_JUDGED_ANCHORS
    assert extra["anchor"] != request["anchor"]                   # 不是主图那一处
    assert extra["actors"] == request["actors"]                   # 同一批在场角色
    # 逐点独立编译：各取材到**自己那一段**（不是把主图画面复制一份）——
    # 这正是「多张图各插自己的位置」能成立的前提。
    assert extra["scene_spec"]["narrative"] != request["scene_spec"]["narrative"]


def test_第57刀_集成_单张与批量两张的主图完全一致(monkeypatch, tmp_path):
    """零回归锁【集成】：开不开多批，**主图那条请求逐字节不变**（多批只是「多加几条」）。"""
    single = _run_multi_writeback(monkeypatch, tmp_path, _multi_ctx())
    multi = _run_multi_writeback(
        monkeypatch, tmp_path, _multi_ctx(illustration_batch_count=2),
    )

    assert {key: value for key, value in multi.items() if key != "extra_requests"} == single


# ── 第 57 刀 ④：把「画面人称」告知判据（只加调用侧，不动用户判据文本）──────────


def test_第57刀_未手选人称时判据user消息逐字节不变(monkeypatch):
    """零回归锁：`illustration_viewpoint` 为空（自动档）⇒ 提示为空串
    ⇒ 判据 user 消息与改造前**逐字节相同**。"""
    from app.services import illustration_prompt_store

    monkeypatch.setattr(
        illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on(),
    )
    captured: dict = {}

    def fake_chat(base, key, model, system, user, **kw):
        captured["user"] = user
        return _ANCHOR_OK_RAW

    monkeypatch.setattr(aag._llm, "chat", fake_chat)
    _trace_capture(monkeypatch)

    # 不传人称键 / 传空串 —— 两者都必须与「旧格式」（在场角色 + 正文）一致
    for ctx in (_ctx(), _ctx(illustration_viewpoint="")):
        aag._illustration_anchor_intent(ctx, _ANCHOR_CLEAN, {"present": ["清夏"]})
        assert captured["user"].split("\n\n")[0] == "在场角色：清夏", captured["user"][:80]
        assert "画面人称" not in captured["user"]


def test_第57刀_手选第一人称时把画面人称告知判据(monkeypatch):
    """验收：手选第一人称 ⇒ 判据 user 消息带上「画面人称」与选点要求；"""
    from app.services import illustration_prompt_store

    monkeypatch.setattr(
        illustration_prompt_store, "effective_climax_anchor_rubric", _rubric_on(),
    )
    captured: dict = {}

    def fake_chat(base, key, model, system, user, **kw):
        captured.update(user=user, system=system)
        return _ANCHOR_OK_RAW

    monkeypatch.setattr(aag._llm, "chat", fake_chat)
    _trace_capture(monkeypatch)

    aag._illustration_anchor_intent(
        _ctx(illustration_viewpoint="first_person"), _ANCHOR_CLEAN, {"present": ["清夏"]},
    )

    assert "画面人称：第一人称" in captured["user"]
    assert "优先" in captured["user"] and "避开" in captured["user"]
    # ⚠ 红线：用户判据正文**一字不改**（不许往判据里加约束/加门）——
    #   提示只进 user 消息，system 仍＝「用户判据 + 程序侧只读合同」
    assert captured["system"].startswith("用户判据")
    assert "画面人称" not in captured["system"]


def test_第57刀_手选第三人称不加第一人称提示(monkeypatch):
    """边界锁：第三人称不吃第一人称那套选点提示（口径不同：第三人称是「找视角展示完整内容」）。"""
    assert aag._anchor_intent_viewpoint_hint({"illustration_viewpoint": "third_person"}) == ""
    assert aag._anchor_intent_viewpoint_hint({}) == ""
    assert aag._anchor_intent_viewpoint_hint({"illustration_viewpoint": "first_person"}) != ""


# ── 第 57 刀 ③：第一人称「画面事实筛分」接缝 ──────────────────────────────────


def test_第57刀_筛分接缝生效时就地改scene_spec并留痕(monkeypatch):
    """验收：applied ⇒ 就地把 appearance/wardrobe 换成筛分后的值，并派发 trace。"""
    trace = _trace_capture(monkeypatch)
    scene = {
        "viewpoint": "first_person",
        "narrative": "他把指尖压在她唇角。",
        "appearance": "朱唇饱满、宽厚圆硕的臀",
        "wardrobe": "月白中衣、蚕丝白袜",
    }

    verdict = aag._wb_first_person_frame_filter(_ctx(), scene)

    assert verdict["applied"] is True
    assert scene["appearance"] == "朱唇饱满"
    assert scene["wardrobe"] == "月白中衣"
    assert trace[-1][0] == "illustration.first_person_filter"
    assert trace[-1][1]["removed"] == ["宽厚圆硕的臀", "蚕丝白袜"]


def test_第57刀_筛分接缝不生效时一个键都不动(monkeypatch):
    """零回归锁：第三人称 ⇒ 返回空判定、`scene_spec` **逐字节不变**、**不发** trace。"""
    trace = _trace_capture(monkeypatch)
    scene = {
        "viewpoint": "third_person",
        "narrative": "他把指尖压在她唇角。",
        "appearance": "朱唇饱满、宽厚圆硕的臀",
        "wardrobe": "月白中衣、蚕丝白袜",
    }
    before = dict(scene)

    assert aag._wb_first_person_frame_filter(_ctx(), scene) == {}
    assert scene == before
    assert trace == []


def test_第57刀_多批的每一张都过画面事实筛分(monkeypatch):
    """真机实锤（2026-09-24 · 2 张实测）：主图筛了、第 2 张没筛 ⇒ 第 2 张提示词仍带
    `broad rounded hips`／`white silk stockings`（还多了第一人称禁用的
    `environmental composition`）。锁：extras 循环里**必须**调用筛分接缝，
    且**必须**在 Profile 编译**之前**（字段账本会把装不下的部位写进稿子）。"""
    order: list[str] = []
    _stub_comfy_chain(monkeypatch)
    # ⚠ 必须在打桩**之后**再补记录，否则会被 `_stub_comfy_chain` 的桩覆盖掉
    monkeypatch.setattr(
        asc, "_wb_first_person_frame_filter",
        lambda ctx, scene: order.append("filter") or {},
    )
    monkeypatch.setattr(asc, "_wb_comfy_profile", lambda *a, **k: order.append("profile") or {})

    ctx = {**_anchors(("甲处", 10), ("乙处", 90)), "illustration_batch_count": 9}
    aag._wb_comfy_extra_requests(
        ctx, "", {}, _ANCHOR_TEXT, _anchor_sig(), _anchor_pool(),
        primary_anchor="乙处", primary_motion=0,
        prompt_override="", transition_decision="", user_text="",
    )

    assert order == ["filter", "profile"]


def test_第57刀_单张时不触发画面事实筛分(monkeypatch):
    """零回归锁：批量数目 < 2 ⇒ 提前短路 ⇒ 连筛分接缝都不该被调用。"""
    calls: list = []
    monkeypatch.setattr(
        asc, "_wb_first_person_frame_filter", lambda ctx, scene: calls.append(1) or {},
    )
    _stub_comfy_chain(monkeypatch)

    ctx = {**_anchors(("甲处", 10), ("乙处", 90))}
    aag._wb_comfy_extra_requests(
        ctx, "", {}, "正文", _anchor_sig(), _anchor_pool(),
        primary_anchor="乙处", primary_motion=0,
        prompt_override="", transition_decision="", user_text="",
    )

    assert calls == []


def test_第57刀_仲裁后同位只出一张(monkeypatch):
    """A 锁（2026-09-24 用户拍板）：两个不同的判据锚点若被收敛到**同一处**
    （真机实锤：仲裁把「手就那么悬…」与 extras 的锚点都改写成「那两团肉…」）
    ⇒ 只出一张，绝不叠放。"""
    order: list[str] = []

    def converged_target(ctx, repo_id, plan, clean, sig, pool, *, prompt_override,
                         motion, user_text, anchor_override="", subjects_override=None):
        # 模拟「仲裁收敛」：无论输入哪个锚点，都改写成同一段
        return {"request_prompt": "p", "plan_retargeted": False,
                "requested_anchor": "同一段。", "motion": 7}

    monkeypatch.setattr(asc, "_wb_comfy_scene_target", converged_target)
    monkeypatch.setattr(asc, "_wb_comfy_scene_spec", lambda *a, **k: {"narrative": "n"})
    monkeypatch.setattr(
        asc, "_wb_first_person_frame_filter",
        lambda ctx, scene: order.append("filter") or {},
    )
    monkeypatch.setattr(asc, "_wb_comfy_profile", lambda *a, **k: order.append("profile") or {})
    monkeypatch.setattr(
        asc, "_wb_comfy_illustrate_env",
        lambda ctx, plan, sig, target, spec, *, motion: {
            "prompt": "p", "anchor": target["requested_anchor"], "motion": motion},
    )
    monkeypatch.setattr(asc, "_wb_frames_and_video", lambda *a, **k: None)

    text = "前文。\n\n同一段。\n\n后文。"
    ctx = {**_anchors(("甲处", 90), ("乙处", 50)), "illustration_batch_count": 9}
    out = aag._wb_comfy_extra_requests(
        ctx, "", {}, text, {}, {},
        primary_anchor="同一段。", primary_motion=0,
        prompt_override="", transition_decision="", user_text="",
    )

    assert out == []                      # 第 2 张与主图同位 ⇒ 跳过
    assert order == []                    # 连编译都不做（省一次 LLM）


def _anchor_sig(**over) -> dict:
    sig = {
        "visible_story": _ANCHOR_CLEAN, "present": ["清夏"], "at_climax": True,
        "character_encounter": False, "encounter_anchor": "",
        "encounter_narrative": "", "encounter_actors": [],
        "local_scene_fallback": False, "missing_plan_fallback": True,
        "first_story_reply": False, "wardrobe": "", "locale": "",
    }
    sig.update(over)
    return sig


def _anchor_pool() -> dict:
    return {
        "known": [], "actor_values": [], "absent": [],
        "climax_paragraph": "", "request_actors": [], "request_appearance": "",
    }


def _run_scene_target(monkeypatch, judge_result):
    from app.services import scene_illustration as si

    intent = judge_result if isinstance(judge_result, tuple) else (judge_result, {})
    monkeypatch.setattr(
        asc, "_illustration_anchor_intent",
        lambda ctx, clean, sig: intent,
    )
    out = aag._wb_comfy_scene_target(
        {}, "", {}, _ANCHOR_CLEAN, _anchor_sig(), _anchor_pool(),
        prompt_override="", motion=0, user_text="",
    )
    return out, si.fallback_illustration_anchor(_ANCHOR_CLEAN)


def test_缺块路径判据返回空时锚点仍是词表结果(monkeypatch):
    """失败上界：判据档拿不到结果时，缺块路径的锚点与接入判据档之前逐字一致。"""
    out, keyword = _run_scene_target(monkeypatch, "")
    assert out["requested_anchor"] == keyword


def test_缺块路径判据命中时优先于词表(monkeypatch):
    out, keyword = _run_scene_target(monkeypatch, "浑身无力的清夏靠在公告栏上。")
    assert out["requested_anchor"] == "浑身无力的清夏靠在公告栏上。"
    assert out["requested_anchor"] != keyword  # 词表本来选的是别的段


# 仲裁机（scene_illustration.resolve_illustration_anchor）用的夹具：第 3 段词表评分
# = 8（决战）+4（转身）+4（回眸）= 16，第 2 段 = 0 ⇒ 差 ≥3，仲裁必然推翻判据锚点。
_ARB_TEXT = (
    "第一段只是铺陈。\n\n判据档选的这一句很平淡。\n\n他在决战前转身，回眸看了她一眼。"
)
_ARB_JUDGE = "判据档选的这一句很平淡。"
_ARB_KEYWORD = "他在决战前转身，回眸看了她一眼。"


def _run_scene_target_on(monkeypatch, text, judge_result, **sig_over):
    from app.services import scene_illustration as si

    intent = judge_result if isinstance(judge_result, tuple) else (judge_result, {})
    monkeypatch.setattr(
        asc, "_illustration_anchor_intent",
        lambda ctx, clean, sig: intent,
    )
    out = aag._wb_comfy_scene_target(
        {}, "", {}, text, _anchor_sig(visible_story=text, **sig_over), _anchor_pool(),
        prompt_override="", motion=0, user_text="",
    )
    return out, si.fallback_illustration_anchor(text)


def test_缺块路径的判据锚点也要过既有仲裁机(monkeypatch):
    """接线一致性：缺块路径（76% 回合）的判据锚点必须与「同轮有计划」那条支路一样，
    过一遍 `resolve_illustration_anchor`。

    2026-09-18 真机复跑实锤（`deepseek-v4-flash-0731`，4 run × 3 采样）：缺块路径原先整条
    绕过了这台仲裁机 ⇒ 同一三档降级链，有计划那条支路有纠偏网、缺块这条没有，
    导致判据档在 L1/L3 明显劣于词表档（L1 距离 55.3 vs 35、L3 19.0 vs 12）。
    接上仲裁后两段改善、且 L2 完美选点零误伤（推翻 0/12）。
    """
    out, keyword = _run_scene_target_on(monkeypatch, _ARB_TEXT, _ARB_JUDGE)
    assert keyword == _ARB_KEYWORD
    assert out["requested_anchor"] == keyword  # 被仲裁机推翻 ⇒ 改用词表档
    assert out["requested_anchor"] != _ARB_JUDGE


def test_缺计划兜底路径不再被时间跳转闸压住纠偏(monkeypatch):
    """接线锁（2026-10-31 真机轮 C · thread `c5c60de2` 第 13 轮）：缺计划兜底那条支路
    必须给仲裁机传 `respect_scene_boundary=False`，而**有计划**那条支路仍传 `True`。

    真机形状：正文里有独立成行的 `———` 时间跳转分隔 ⇒ `_anchors_cross_scene_boundary`
    返回 True ⇒ 旧行为把两道推翻闸一起关掉 ⇒ 判据档选的尾部帧原样交付，插画槽落到
    `<content>` 末尾（用户实锤「图片插在最尾部」）。那道闸的设计前提是保护**显式意图帧**
    （`plan["anchor"]` / `anchor_override`），缺计划路径没有帧要保护，故不该压住纠偏。
    """
    from app.services import scene_illustration as si

    text = f"第一段只是铺陈。\n\n{_ARB_KEYWORD}\n\n———\n\n{_ARB_JUDGE}"
    seen: dict = {}
    real = si.resolve_illustration_anchor

    def _spy(_text, _anchor="", **kwargs):
        seen.update(kwargs)
        return real(_text, _anchor, **kwargs)

    monkeypatch.setattr(si, "resolve_illustration_anchor", _spy)
    out, keyword = _run_scene_target_on(monkeypatch, text, _ARB_JUDGE)

    assert keyword == _ARB_KEYWORD
    assert si._anchors_cross_scene_boundary(text, _ARB_KEYWORD, _ARB_JUDGE)  # 闸确实会命中
    assert seen["respect_scene_boundary"] is False
    assert out["requested_anchor"] == _ARB_KEYWORD  # 纠偏恢复 ⇒ 回到高潮段

    # 对照组：同一段正文，**有显式主计划**时那道闸必须仍然生效（旧行为零回归）。
    seen_plan: dict = {}

    def _spy_plan(_text, _anchor="", **kwargs):
        seen_plan.update(kwargs)
        return real(_text, _anchor, **kwargs)

    monkeypatch.setattr(si, "resolve_illustration_anchor", _spy_plan)
    plan_out = aag._wb_comfy_scene_target(
        {}, "", {"anchor": _ARB_JUDGE}, text,
        _anchor_sig(visible_story=text, missing_plan_fallback=False), _anchor_pool(),
        prompt_override="", motion=0, user_text="",
    )
    assert seen_plan["respect_scene_boundary"] is True
    assert plan_out["requested_anchor"] == _ARB_JUDGE  # 计划点名的那一帧不被推翻


def test_初遇锚点不过仲裁机(monkeypatch):
    """边界锁：新角色登场的首帧锚点是**有意指定**的画面，不得过仲裁机。

    仲裁机带「高潮纠偏」语义（`fallback_score >= requested_score + 3` 就推翻），
    把它套到首帧上会把画面从开篇拉到高潮段——这正是 `first_frame_anchor_offset`
    当初存在的原因。所以接线只扩到「缺块 + 判据」这条支路，不就手扩到 encounter 支路。
    """
    out, _ = _run_scene_target_on(
        monkeypatch, _ARB_TEXT, "",
        character_encounter=True, encounter_anchor=_ARB_JUDGE,
        encounter_narrative=_ARB_TEXT, encounter_actors=["清夏"],
    )
    assert out["requested_anchor"] == _ARB_JUDGE
    assert out["requested_anchor"] != _ARB_KEYWORD


# ── 第 48 刀 · B2（2026-09-22 用户定案）：判据锚点被仲裁机推翻时，画面主体**收窄**而非
# 整条撤回、退回「在场全集」。真机实锤（thread `09f79e6f`，2026-09-22T12:28）：那一帧剧情
# 只该画一个人，而契约的 `frame_subjects` 被撤回 ⇒ 按 `actors`（在场角色卡全集＝两名女性）
# 判成双人帧 ⇒ 成稿 `2girls` ＋「多人提示词必须单独描述第一/第二角色」判红。


def _run_scene_target_arb(monkeypatch, judge_result, *, ctx=None, pool=None, **sig_over):
    """第 48 刀夹具：走「仲裁机必然推翻判据锚点」那条支路，且可注入 ctx / pool / 在场角色。"""
    intent = judge_result if isinstance(judge_result, tuple) else (judge_result, {})
    monkeypatch.setattr(asc, "_illustration_anchor_intent", lambda _ctx, clean, sig: intent)
    monkeypatch.setattr(
        asc, "_resolve_illustration_request_actors", lambda *a, **k: ["甲", "乙"],
    )
    return aag._wb_comfy_scene_target(
        ctx or {}, "", {}, _ARB_TEXT,
        _anchor_sig(visible_story=_ARB_TEXT, present=["甲", "乙"], **sig_over),
        pool if pool is not None else dict(_anchor_pool(), known=["甲", "乙"]),
        prompt_override="", motion=0, user_text="",
    )


def test_第48刀_判据锚点被推翻时主体收窄到在场角色(monkeypatch):
    """B2 正例：撤回时收窄到「判据主体 ∩ 在场角色」——不在场的名字不再算画面主体。"""
    judged = (_ARB_JUDGE, {"subjects": ["甲", "丙"], "composition": "双人同框"})
    out = _run_scene_target_arb(
        monkeypatch, judged, ctx={"illustration_viewpoint": "third_person"},
    )

    assert out["requested_anchor"] == _ARB_KEYWORD  # 仲裁机确实推翻了判据锚点（画面换段）
    assert out["request_actors"] == ["甲", "乙"]  # 归因不缩：在场角色仍是两人
    assert out["frame_subjects"] == ["甲"]  # ∩ 在场角色 ⇒ 不在场的「丙」剔出画面主体

    from app.services import image_prompt_profiles as ipp

    # 契约据此看到 1 个画面主体 ⇒ 单人帧；旧行为这里拿到的是 `actors`＝2 ⇒ 双人判据误触发。
    assert ipp._contract_subject_names(
        {"frame_subjects": out["frame_subjects"], "actors": out["request_actors"]},
    ) == ["甲"]


def test_第48刀_手选第一人称下同一条收窄(monkeypatch):
    """同一收窄与手选人称无关（两档都不再退回 `actors`）。"""
    judged = (_ARB_JUDGE, {"subjects": ["甲", "丙"], "composition": "双人同框"})
    out = _run_scene_target_arb(
        monkeypatch, judged, ctx={"illustration_viewpoint": "first_person"},
    )

    assert out["frame_subjects"] == ["甲"]


def test_第48刀_判据主体全不在场时零回归退回在场集合(monkeypatch):
    """失败上界：交集为空 ⇒ 空表 ⇒ 契约退回 `actors`，与旧行为逐字节一致。"""
    judged = (_ARB_JUDGE, {"subjects": ["丙", "丁"], "composition": "双人同框"})
    out = _run_scene_target_arb(
        monkeypatch, judged, ctx={"illustration_viewpoint": "third_person"},
    )

    assert out["frame_subjects"] == []

    from app.services import image_prompt_profiles as ipp

    assert ipp._contract_subject_names(
        {"frame_subjects": out["frame_subjects"], "actors": out["request_actors"]},
    ) == ["甲", "乙"]


def test_第48刀_收窄只作用于契约主体_取材仍按未收窄的判据主体(monkeypatch):
    """边界锁（用户 2026-09-22 明令）：别把「先生成内容、再考虑视角如何最大程度呈现内容」
    这条链毁了 —— B2 只改 `frame_subjects`，取材扩窗拿到的仍是**未收窄**的判据主体。"""
    from app.services import scene_illustration as si

    seen: dict = {}
    real_excerpt = si.illustration_scene_excerpt

    def _spy(text, requested_anchor="", *, subjects=None):
        seen["subjects"] = list(subjects or [])
        return real_excerpt(text, requested_anchor, subjects=subjects)

    monkeypatch.setattr(si, "illustration_scene_excerpt", _spy)
    judged = (_ARB_JUDGE, {"subjects": ["甲", "丙"], "composition": "双人同框"})
    out = _run_scene_target_arb(
        monkeypatch, judged, ctx={"illustration_viewpoint": "third_person"},
    )

    assert out["frame_subjects"] == ["甲"]  # 契约主体已收窄
    assert seen["subjects"] == ["甲", "丙"]  # 取材层仍是未收窄的判据主体（零改动）


# ── 第 49 刀 · A2（2026-09-22 用户定案「先 B 再 A2」）：把「收窄」从**有条件**（锚点被推翻）
# 升级为**无条件** —— 只要判据主体里有 ∉ 在场角色的名字就剔。真机（轮 A · 09:54 · thread
# `09f79e6f`）：判据 `subjects=['沈映澜','洛渊']`（把不入画的主人公算进画面主体）、
# `actors=['沈映澜']` ⇒ 旧行为 `frame_subjects` 保持 2 人 ⇒ 契约判成双人帧 ⇒ 成稿
# `2girls, adult woman, adult man`（多画一个人）。
# 零 LLM 量测（同 thread 三轮）：生效 **1/3 格**（轮 A）、零误伤（轮 B 逐字节不变、轮 C
# 交集空 ⇒ 回落 `actors` ＝旧行为）。


def _run_scene_target_no_arb(monkeypatch, judge_result, *, ctx=None, pool=None,
                             actors=None, **sig_over):
    """第 49 刀夹具：把仲裁机**钉成恒等**（锚点不会被推翻）⇒ 隔离出「无条件收窄」这条路径。"""
    from app.services import scene_illustration as si

    intent = judge_result if isinstance(judge_result, tuple) else (judge_result, {})
    monkeypatch.setattr(asc, "_illustration_anchor_intent", lambda _ctx, clean, sig: intent)
    monkeypatch.setattr(
        si, "resolve_illustration_anchor", lambda _clean, anchor, **_kw: anchor,
    )
    monkeypatch.setattr(
        asc, "_resolve_illustration_request_actors",
        lambda *a, **k: ["甲", "乙"] if actors is None else list(actors),
    )
    return aag._wb_comfy_scene_target(
        ctx or {}, "", {}, _ARB_TEXT,
        _anchor_sig(visible_story=_ARB_TEXT, present=["甲", "乙"], **sig_over),
        pool if pool is not None else dict(_anchor_pool(), known=["甲", "乙"]),
        prompt_override="", motion=0, user_text="",
    )


def test_第49刀_锚点未被推翻时主体也收窄到在场角色(monkeypatch):
    """A2 正例：收窄**不再以「锚点被推翻」为条件** —— 判据把不在场者算进画面主体时无条件剔掉。

    真机轮 A（09:54）：判据 `subjects=['沈映澜','洛渊']`（主人公不入画却算成主体）、
    `actors=['沈映澜']` ⇒ 旧行为保持 2 人 ⇒ 契约判双人帧 ⇒ 成稿多画一个人。
    ⚠ 构造上刻意让「不在场者」也在 `known` 里 ⇒ 「第 42 刀 B（∉ `known` 剔）」**测不出来**，
    **只有 A2（∉ 在场角色剔）能修** —— 真机上 `known` 可能为空（未绑卡）⇒ 那条闸整体空转
    （见技能 `demiurge-viewpoint-subject-scope` §8 的元教训）。
    """
    judged = (_ARB_JUDGE, {"subjects": ["甲", "乙"], "composition": "双人同框"})
    out = _run_scene_target_no_arb(
        monkeypatch, judged, actors=["甲"], ctx={"illustration_viewpoint": "first_person"},
    )

    assert out["requested_anchor"] == _ARB_JUDGE  # 锚点**未**被推翻（隔离变量）
    assert out["request_actors"] == ["甲"]
    assert out["frame_subjects"] == ["甲"]  # 不在场的「乙」被剔（无条件收窄）


def test_第49刀_判据主体全在场时逐字节不变(monkeypatch):
    """零回归锁：判据主体本来就是在场角色的子集 ⇒ 收窄是 no-op。"""
    judged = (_ARB_JUDGE, {"subjects": ["甲", "乙"], "composition": "双人同框"})
    out = _run_scene_target_no_arb(
        monkeypatch, judged, ctx={"illustration_viewpoint": "first_person"},
    )

    assert out["frame_subjects"] == ["甲", "乙"]


def test_第49刀_在场角色取不到时不收窄(monkeypatch):
    """保守边界：本帧取不到在场角色（`actors` 空）⇒ **不动**判据主体（等于旧行为）。

    否则会把判据主体整条清空 ⇒ 契约再回落一次空表，白白丢掉唯一的正向证据。
    """
    judged = (_ARB_JUDGE, {"subjects": ["甲", "乙"], "composition": "双人同框"})
    pool = dict(_anchor_pool(), known=["甲", "乙"], request_actors=[])
    out = _run_scene_target_no_arb(monkeypatch, judged, actors=[], pool=pool)

    assert out["request_actors"] == []
    assert out["frame_subjects"] == ["甲", "乙"]  # 保留判据主体，不收窄


# ── 第 50 刀 · B3（2026-09-22 用户定案「主人公**不是被剔除，而是等效镜头**」）：
# 判据锚点句里的**名字**未必是本帧主体 —— 第一人称下它常是「镜头」（被看向／被亲吻的
# 主人公）。而收敛（`filter_anchor_subjects` 第③步）**只认字面名字** ⇒ 锚点句用**代词**
# 写主体、用**名字**写镜头时，收敛会把**主体丢掉、只留下镜头**。
# 真机轮 C（thread `09f79e6f`，12:28）：判据 `subjects=['沈映澜','洛渊']`、锚点句
# 「**她**掖完被子，把视线从墙上收回来，落到**洛渊**脸上。」⇒ 收敛成 `['洛渊']`
# （＝镜头；演员表 `actors=['谢若冰','沈映澜']` 里并不含他）⇒ 与在场角色**无交集**
# ⇒ 第 49 刀 A2 把它收成空表 ⇒ 契约退回 `actors` ⇒ 单人帧被画成 `2girls`（多画一个人）。
# 修法：收敛结果与在场角色**无交集** ⇒ 视为收敛失败 ⇒ 回退到**未收敛的判据主体**再按
# 在场收窄（同一份判据，只是跳过收敛这一步；正确画面＝她一人 ＋ 视线朝镜头）。

_A50_TEXT = "\n\n".join([
    "---",
    "甲把被子掖好，坐在床沿。",
    "她把视线从墙上收回来，落到乙脸上。",
])
_A50_ANCHOR = "她把视线从墙上收回来，落到乙脸上。"


def _run_scene_target_50(monkeypatch, judge_result, *, ctx=None, actors=None):
    """第 50 刀夹具：仲裁机钉成恒等（隔离「收敛失败 ⇒ 回退」这一条路径）。

    锚点句**只含「乙」的字面名字**、而主体用**代词「她」**写 —— 与真机轮 C 同构。
    """
    from app.services import scene_illustration as si

    intent = judge_result if isinstance(judge_result, tuple) else (judge_result, {})
    monkeypatch.setattr(asc, "_illustration_anchor_intent", lambda _ctx, clean, sig: intent)
    monkeypatch.setattr(
        si, "resolve_illustration_anchor", lambda _clean, anchor, **_kw: anchor,
    )
    monkeypatch.setattr(
        asc, "_resolve_illustration_request_actors",
        lambda *a, **k: ["甲", "乙"] if actors is None else list(actors),
    )
    return aag._wb_comfy_scene_target(
        ctx or {}, "", {}, _A50_TEXT,
        _anchor_sig(visible_story=_A50_TEXT, present=["甲", "乙"]),
        dict(_anchor_pool(), known=["甲", "乙"]),
        prompt_override="", motion=0, user_text="",
    )


def test_第50刀_收敛结果与在场角色无交集时回退到判据主体(monkeypatch):
    """B3 正例（真机轮 C 型）：锚点句用**代词**写主体、用**名字**写镜头。

    判据 `subjects=['甲','乙']`；锚点句「她把视线…落到**乙**脸上。」只含字面名字「乙」
    ⇒ 收敛把主体「甲」丢掉、只留镜头「乙」；而 `actors=['甲']`（乙不在场）⇒ 交集空
    ⇒ 第 49 刀 A2 会把它清成空表、契约回落在场全集。第 50 刀回退到**未收敛的判据主体**
    ∩ 在场角色 ⇒ `['甲']`（＝她一人 ＋ 视线朝镜头；主人公是**等效镜头**、不当画面主体）。
    """
    judged = (_A50_ANCHOR, {"subjects": ["甲", "乙"], "composition": "单人近景"})
    out = _run_scene_target_50(
        monkeypatch, judged, actors=["甲"], ctx={"illustration_viewpoint": "first_person"},
    )

    assert out["request_actors"] == ["甲"]
    assert out["frame_subjects"] == ["甲"]


def test_第50刀_收敛结果与在场角色有交集时不回退(monkeypatch):
    """零回归锁：收敛结果与在场角色**有交集** ⇒ 尊重收敛结论（第 49 刀行为逐字节不变）。

    判据 `['甲','乙']`、锚点句含字面名字「甲」⇒ 收敛 = `['甲']`；`actors=['甲','乙']`
    ⇒ 交集非空 ⇒ **不得**回退成 `['甲','乙']`。
    """
    judged = ("甲把被子掖好，坐在床沿。", {"subjects": ["甲", "乙"], "composition": "双人同框"})
    out = _run_scene_target_50(
        monkeypatch, judged, actors=["甲", "乙"],
        ctx={"illustration_viewpoint": "first_person"},
    )

    assert out["frame_subjects"] == ["甲"]


def test_第50刀_判据主体里没有在场角色时不回退(monkeypatch):
    """保守边界：交集空、但**未收敛的判据主体同样补不回来** ⇒ 不动（回退只在有新解时发生）。

    此时保持第 49 刀既有行为：交回 A2 收窄 ⇒ 空表 ⇒ 契约回落 `actors`（零回归兜底）。
    """
    judged = (_A50_ANCHOR, {"subjects": ["乙"], "composition": "单人近景"})
    out = _run_scene_target_50(
        monkeypatch, judged, actors=["甲"], ctx={"illustration_viewpoint": "first_person"},
    )

    assert out["frame_subjects"] == []


# ---------------------------------------------------------------------------
# 前置观测（2026-09-22）：`illustration.request` 必须记入人称判定与画面主体
#
# 动机＝全量 `agent-trace.jsonl` 里 `viewpoint` **0 命中** ⇒ 「同一帧人称判两次」与
# 「判据主体收敛失败」都不可观测，只能靠真机成图反推。观测字段**只进 trace、不进请求**。
# ---------------------------------------------------------------------------
def test_前置观测_illustration_request记入人称与画面主体(monkeypatch):
    """真链路 target ⇒ trace 载荷能读到人称与画面主体（第 50 刀同构场景）。

    场景＝手选第一人称 + 锚点句「**她**把视线…落到**乙**脸上。」⇒ 收敛失败回退
    ⇒ 画面主体 `['甲']`（＝她一人；主人公是等效镜头、不当画面主体）。
    ⚠ 同一锚点句因含「她」被判自动档 `third_person` ⇒ 与手选冲突 ⇒ `conflict=True`：
    这正是「段内有她/他 ⇒ 第三人称」这条过宽判据（D2）的现成实证。
    """
    trace = []
    monkeypatch.setattr(
        aag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    target = _run_scene_target_50(
        monkeypatch,
        (_A50_ANCHOR, {"subjects": ["甲", "乙"], "composition": "单人近景"}),
        actors=["甲"], ctx={"illustration_viewpoint": "first_person"},
    )

    aag._wb_emit_illustration_trace(
        {}, {"prompt": "x"}, plan={},
        sig=_anchor_sig(visible_story=_A50_TEXT, present=["甲", "乙"]),
        pool=dict(_anchor_pool(), known=["甲", "乙"]),
        target=target, scene="", local_scene="", video_prompt_text="",
    )

    assert trace[-1][0] == "illustration.request"
    payload = trace[-1][1]
    assert payload["viewpoint"] == "first_person"
    assert payload["viewpoint_detail"]["source"] == "override"
    assert payload["viewpoint_detail"]["auto"] == "third_person"
    assert payload["viewpoint_detail"]["conflict"] is True
    assert payload["frame_subjects"] == ["甲"]
    # 2026-09-22 追加观测：叙事者姓名/来路（诊断「手选第一人称仍画出主人公」必需）
    assert "narrator" in payload and "narrator_source" in payload


def test_前置观测_缺键时不炸且记空值(monkeypatch):
    """防御锁：`target` 未提供观测源字段 ⇒ 记空值，不抛错、不改行为（零回归）。"""
    trace = []
    monkeypatch.setattr(
        aag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )

    aag._wb_emit_illustration_trace(
        {}, {"prompt": "x"}, plan={}, sig=_anchor_sig(present=["甲"]),
        pool=dict(_anchor_pool(), known=["甲"]),
        target={
            "request_prompt": "x", "prompt_source": "fallback",
            "plan_retargeted": False, "request_actors": ["甲"],
        },
        scene="", local_scene="", video_prompt_text="",
    )

    payload = trace[-1][1]
    assert payload["viewpoint"] == ""
    assert payload["viewpoint_detail"] == {}
    assert payload["frame_subjects"] == []
    assert payload["narrator"] == "" and payload["narrator_source"] == ""


def test_缺块路径判据档主体名驱动取材扩窗(monkeypatch):
    """A′ 接线（§7.3 第 3/5 条）：判据档的 `subjects` 必须回传到取材层。

    锚点句「那么，我该选哪一边呢？」不含任何主体名 ⇒ 取材必须取回场景边界后的
    整段画面，而不是只给这一句台词。这条同时锁住「detail 被丢掉」的旧行为不再回来。
    """
    text = "\n\n".join([
        "---",
        "明澜把双手贴到墙上，把重心压得很低。",
        "清夏也把重心压低，贴住墙面上前。",
        "那么，我该选哪一边呢？",
    ])
    judged = ("那么，我该选哪一边呢？", {"subjects": ["明澜", "清夏"], "composition": "双人同框"})

    out, _ = _run_scene_target_on(
        monkeypatch, text, judged, present=["明澜", "清夏"],
    )

    assert out["requested_anchor"] == judged[0]
    assert "明澜把双手贴到墙上" in out["scene_narrative"]
    assert "清夏也把重心压低" in out["scene_narrative"]


def test_缺块路径判据档无主体名时取材维持旧行为(monkeypatch):
    """判据档只回 anchor、`subjects` 为空 ⇒ 取材层无信号 ⇒ 与接入 A′ 之前逐字一致。"""
    text = "\n\n".join([
        "---",
        "明澜把双手贴到墙上，把重心压得很低。",
        "那么，我该选哪一边呢？",
    ])

    out, _ = _run_scene_target_on(
        monkeypatch, text, ("那么，我该选哪一边呢？", {}), present=["明澜"],
    )

    assert out["requested_anchor"] == "那么，我该选哪一边呢？"
    assert out["scene_narrative"] == "那么，我该选哪一边呢？"


def test_缺块路径词表档用在场角色全集驱动扩窗(monkeypatch):
    """§7.3 第 4 条词表档口径：判据档拿不到结果时，主体名集合回退 `sig["present"]`。

    夹具里词表档选中的锚点「她缓缓转身回眸。」不含任何在场角色名 ⇒ 扩窗取回前一段。
    """
    text = "\n\n".join([
        "---",
        "明澜把双手贴到墙上，把重心压得很低。",
        "她缓缓转身回眸。",
    ])

    out, _ = _run_scene_target_on(
        monkeypatch, text, "", present=["明澜", "清夏"],
    )

    assert out["requested_anchor"] == "她缓缓转身回眸。"
    assert "明澜把双手贴到墙上" in out["scene_narrative"]


def _run_scene_target_ctx(monkeypatch, text, judge_result, ctx, pool=None, **sig_over):
    """带 ctx 的 `_wb_comfy_scene_target` 夹具（手选人称 / 主人公性别都从 ctx 进）。"""
    intent = judge_result if isinstance(judge_result, tuple) else (judge_result, {})
    monkeypatch.setattr(asc, "_illustration_anchor_intent", lambda ctx, clean, sig: intent)
    return aag._wb_comfy_scene_target(
        ctx, "", {}, text, _anchor_sig(visible_story=text, **sig_over),
        pool if pool is not None else _anchor_pool(),
        prompt_override="", motion=0, user_text="",
    )


def test_手选人称三档落到画面人称(monkeypatch):
    """第 23 刀：「多元数据插入」面板的人称按钮组必须真正落到画面人称，并留冲突痕。

    用户 2026-09-20 定案：自动档＝按锚点段自动判（旧行为不变）；手选合法档位 ⇒ 手选优先；
    手选与自动不一致时 `conflict=True` 留痕供审计。
    """
    text = "\n\n".join(["---", "他站在窗前，望着雨幕。", "雨水顺着玻璃滑落。"])
    auto = _run_scene_target_ctx(monkeypatch, text, "", {})
    first = _run_scene_target_ctx(monkeypatch, text, "", {"illustration_viewpoint": "first_person"})
    third = _run_scene_target_ctx(monkeypatch, text, "", {"illustration_viewpoint": "third_person"})

    assert first["viewpoint"] == "first_person"
    assert first["viewpoint_detail"]["source"] == "override"
    assert first["viewpoint_detail"]["auto"] == auto["viewpoint"]
    assert first["viewpoint_detail"]["conflict"] is (auto["viewpoint"] != "first_person")
    assert third["viewpoint"] == "third_person"
    assert third["viewpoint_detail"]["source"] == "override"
    # 自动档 / 非法档位 ⇒ 与不传 override 逐字节一致（零回归）
    assert auto["viewpoint_detail"] == _run_scene_target_ctx(
        monkeypatch, text, "", {"illustration_viewpoint": ""},
    )["viewpoint_detail"]


def test_主人公性别注入到外观段(monkeypatch):
    """第 23 刀：主人公性别作为**画面事实**注入外观段（世界书解析不到 ⇒ 通用表列兜底）。

    ⚠ **第 37 刀修订**（2026-09-21 用户实锤）：性别是**第三人称**特性——面板按钮文案即
    「第三人称：取主人公性别入画」；第一人称下主人公＝镜头本身、不占画面 ⇒ 一律不注入。
    修订前只判「主人公在 `request_actors` 里」，而**自动档无信号时默认第一人称**
    （此时 `_viewpoint_clause` 已在说「叙事者不入画」）⇒ 注入＝自相矛盾的双口径。
    """
    text = "\n\n".join(["---", "甲站在窗前，望着雨幕。", "雨水顺着玻璃滑落。"])
    _fact_tables = [{"columns": ["姓名", "性别", "外貌特征"], "rows": [["甲", "男", "高大"]]}]
    # 双消费方（2026-10-23 拆域后）：_protagonist_gender 在 aa、_wb_comfy_scene_target 在 asc
    monkeypatch.setattr(aa, "_repo_tables_for_facts", lambda ctx, repo_id: _fact_tables)
    monkeypatch.setattr(asc, "_repo_tables_for_facts", lambda ctx, repo_id: _fact_tables)

    # 第三人称（手选）⇒ 注入
    out = _run_scene_target_ctx(
        monkeypatch, text, "",
        {"user_name": "甲", "_illustration_visual_profiles": "甲：高大体格。",
         "illustration_viewpoint": "third_person"},
        pool=dict(_anchor_pool(), request_actors=["甲"]),
    )
    assert "甲" in out["request_actors"]
    assert "【性别】男" in out["request_appearance"]

    # 第一人称（手选）⇒ 主人公＝镜头 ⇒ 不注入（第 37 刀）
    out_first = _run_scene_target_ctx(
        monkeypatch, text, "",
        {"user_name": "甲", "_illustration_visual_profiles": "甲：高大体格。",
         "illustration_viewpoint": "first_person"},
        pool=dict(_anchor_pool(), request_actors=["甲"]),
    )
    assert "【性别】" not in out_first["request_appearance"]

    # 主人公不在本帧主体（第一人称 ⇒ 叙事者不占画面）⇒ 不注入
    out2 = _run_scene_target_ctx(
        monkeypatch, text, "",
        {"user_name": "甲", "_illustration_visual_profiles": "乙：矮小。"},
        pool=dict(_anchor_pool(), request_actors=["乙"]),
    )
    assert "【性别】" not in out2["request_appearance"]


# ── 2026-09-23（A 治本）：curator 可写集剔除机制层 + 补回在场角色卡 ──

def test_curator可写集剔除机制层并补回在场角色卡(tmp_path):
    """A 治本（真机根因）：原先 allowed 直接取本轮召回集 `_selected_worldbook_indices`，
    而 `constant=True` 条目「全程恒开、全文注入」⇒ **永远落在可写集里**；角色卡
    （constant=False）只有被召回时才在 ⇒ curator 想记「谢若冰的最新状态」时无处可写，
    只能灌进伪机制条目。真机实锤：`id=1 全局机制·{{user}}(少宗主)`（175 字符）**通篇是
    谢若冰的身体状态**、`id=2 全局机制·龙阳决与双修`（39 字符）写「星织恋…往返抚弄其经脉」。

    现口径＝召回集**剔除机制层** ＋ **补上在场角色的角色卡**（与 `CURATOR_SYSTEM`
    「机制、规则、历史背景、地点常识等条目默认只读；角色条目中的长期剧情动态是最主要的
    worldbook_update 对象」一致）；展示视图与写入白名单共用本函数 ⇒ 看得到＝写得进。
    """
    repo_id = "work"
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [{"entries": [
        {"comment": "全局机制·龙阳决与双修", "content": "机制正文", "constant": True},
        {"comment": "角色卡·谢若冰", "content": "【角色卡·谢若冰】底座", "keys": ["谢若冰"]},
        {"comment": "角色卡·旁人", "content": "【角色卡·旁人】底座", "keys": ["旁人"]},
    ]}])
    ctx = {
        "output_dir": str(tmp_path), "repo_id": repo_id,
        "_selected_worldbook_indices": [0],        # 机制条目被召回（constant ⇒ 恒开）
        "_worldbook_character_names": ["谢若冰"],   # 本轮在场角色
    }

    allowed = aga._curator_writable_indices(ctx, repo_id)

    assert 0 not in allowed    # 机制层被剔除，即便它在召回集里
    assert 1 in allowed        # 在场角色卡被补回，即便没被召回
    assert 2 not in allowed    # 不在场角色卡不补


def test_curator可写集取不到世界书时退回旧口径(tmp_path):
    """等价矩阵锁：世界书读不到 / 无条目 ⇒ 逐字退回旧口径（不改变行为）。"""
    ctx = {"output_dir": "", "repo_id": "missing", "_selected_worldbook_indices": [0, 2]}
    assert aga._curator_writable_indices(ctx, "missing") == frozenset({0, 2})


def test_第58刀_插图偏移吸附到段落末尾():
    """主图/extras/首尾帧同走 `_illustration_anchor_offset` ⇒ 一处吸附全覆盖
    （2026-09-24 用户 999 实测口径：插图插到段落下方，不切进段落中间）。"""
    clean = "第一段高潮。\n\n第二段，锚点句在中间。后续还有半句。\n\n第三段。"
    off = ag._illustration_anchor_offset(clean, {"anchor": "锚点句在中间。"})
    assert off is not None
    assert "后续还有半句。" in clean[:off]      # 同段剩余内容在插入点之前
    assert clean[off:].startswith("\n\n") or off == len(clean)


def test_第58刀_锚点定位失败时吸附不越界():
    """虚构锚点 ⇒ 评分选段兜底（既有语义，见 test_scene_illustration 的同名口径）；
    但返回的偏移**仍必须吸附到段落末尾**（吸附对所有路径一视同仁）。"""
    clean = "铺垫段。\n\n高潮动作。\n\n事后余韵与收束。"
    off = ag._illustration_anchor_offset(
        clean, {"anchor": "模型虚构且正文不存在的句子", "allow_anchor_fallback": False},
    )
    assert off is not None                       # 评分选段兜底（既有语义）
    assert clean[off:].startswith("\n\n") or off == len(clean)


# ── 第 59 刀（视频连续化）：firstlast 模式下 extras = 段桥 —— 首帧=上一张画面、
#    尾帧=本锚点画面、transition_video_request 复用 mode="transition" 编译；
#    衔接不上 ⇒ 前端退高潮片段延续（该槽按普通图出）。


def test_第59刀_firstlast模式extras编译段桥(monkeypatch):
    _stub_comfy_chain(monkeypatch)
    ctx = {
        **_anchors(("甲处的画面。", 70), ("乙处的画面。", 50), ("丙处的画面。", 60)),
        "video_mode": "firstlast",
        "comfy_video": True,
        "illustration_batch_count": 99,
    }

    extras = _run_extra(ctx, primary_anchor="甲处的画面。", primary_prompt="p:甲处的画面")

    assert len(extras) == 2
    # 链式：首桥的「上一张」= 主图画面；第二桥的「上一张」= 第一桥的画面
    first, second = extras
    assert first["video_mode"] == "firstlast"
    assert first["transition"] == "regenerate"
    assert first["prev_tail_desc"] == "p:甲处的画面"
    assert first["first_frame_desc"] == "p:甲处的画面"
    assert first["last_frame_desc"] == "p:乙处的画面。"
    assert second["prev_tail_desc"] == "p:乙处的画面。"
    assert second["first_frame_desc"] == "p:乙处的画面。"
    assert second["last_frame_desc"] == "p:丙处的画面。"
    # 桥视频走现有 transition 编译（起点=prev_tail、终点=first_frame）
    for extra in extras:
        bridge = extra.get("transition_video_request") or {}
        assert bridge, "段桥必须编译 transition_video_request"
        submit = bridge.get("submit") or {}
        assert submit.get("prompt"), "桥视频必须有提示词"


# ── 第 66 刀：POV 覆盖面的「防呆登记」接线锁（2026-09-28）────────────────────────
#
# 告警必须**真的接进 `illustration.profile` 事件的派发点**，否则「可观测」是句空话。
# 真机取证：`prompt_profile` **33/33 全 `anima_tags`** ⇒ `natural_language` 零使用，
# 而它刻意没有机械层（手册红线 79 ④）⇒ 一旦被选用，要能在 trace 里**看见**。


def test_第66刀_告警已接进profile事件派发点():
    import inspect

    from app.services import agent_agency as aag

    src = inspect.getsource(aag._wb_comfy_profile)
    assert '"illustration.profile"' in src
    assert "viewpoint_coverage_warning=" in src


def test_采样额度不得与显式temperature重复传参():
    """2026-10-20 实锤（用户「明明没点停止却显示已停止生成」）：

    `RunContext._legacy()` 漏了 `preset_sampling` 键 ⇒ `_roleplay_sampling` 恒返回空
    sampling ⇒ `roleplay_node` 里四处 `temperature=temp, **roleplay_sampling`
    从不冲突（latent bug）。修好 `_legacy()` 后 sampling 带上 `temperature` ⇒
    `TypeError: got multiple values for keyword argument 'temperature'` ⇒
    整轮 roleplay 崩在发请求之前，`_roleplay_failure_result` 按中断语义收尾，
    前端显示「已停止生成」。

    回归锁两层：
    ① 功能层——预设带 temperature 时，剔除后的 kwargs 与显式 temperature 可共存；
    ② 结构层——源码里不得再出现「未过滤的 `**roleplay_sampling`」与 temperature 同现。
    """
    from app.services import agent_graph as _ag

    ctx = _ctx(preset_sampling={"temperature": 1.0, "openai_max_tokens": 600000})
    sampling = _ag._roleplay_sampling(ctx)
    assert sampling.get("temperature") == 1.0, "预设 temperature 必须真的生效（否则本锁失效）"

    kwargs = {k: v for k, v in sampling.items() if k != "temperature"}

    def _accept(**kw):  # 模拟四处调用点的关键字展开
        return kw

    merged = _accept(temperature=0.9, **kwargs)
    assert merged["temperature"] == 0.9, "显式 temperature 必须是最终生效值"
    assert merged.get("max_tokens") == 604000

    # 结构层：未过滤的 spread 一律不得出现
    # 只扫 `roleplay_node` 本体（风险就在那几处调用点）；剥掉行内注释，
    # 避免匹配到说明文字。
    src = inspect.getsource(_ag.roleplay_node)
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    import re as _re

    hits = _re.findall(r"\*\*roleplay_sampling(?!_kwargs)", code)
    assert not hits, f"仍有未过滤的 **roleplay_sampling（{len(hits)} 处）"


def test_采样kwargs按接收方签名过滤_预设新增字段不会打挂整轮():
    """2026-10-20 第二次实锤（同一症状复现）：

    修完 `temperature` 重复传参后，用户仍看到「已停止生成」——这次是
    `_chat_with_optional_stream() got an unexpected keyword argument 'top_k'`。
    预设采样有 6 个字段（temperature/top_p/top_k/frequency_penalty/presence_penalty/
    max_tokens），而 `_chat_with_optional_stream` 只认 temperature/top_p/max_tokens/
    max_duration_seconds。**黑名单逐个剔永远追不上预设新增字段。**

    回归锁：`_roleplay_sampling_kwargs` 必须按**接收方真实签名**白名单过滤，
    且必须包含 `top_k`/`frequency_penalty`/`presence_penalty` 这些"会炸"的键在表外。
    """
    from app.services import agent_graph as _ag
    from app.services.agent_request_context import from_payload

    accepted = _ag._stream_sampling_keys()
    for dangerous in ("top_k", "frequency_penalty", "presence_penalty"):
        assert dangerous not in accepted, f"{dangerous} 不应直接透传给流式调用"

    ctx = from_payload({
        "preset_sampling": {
            "temperature": 1.0, "top_p": 0.98, "top_k": 50,
            "frequency_penalty": 0.0, "presence_penalty": 0.0,
            "openai_max_tokens": 600000,
        },
        "model": "claude-opus-4-8",
    })
    kwargs = _ag._roleplay_sampling_kwargs(ctx)

    # 能过去的只有接收方认识的 + temperature 已剔除
    assert sorted(kwargs) == ["max_tokens", "top_p"]
    assert kwargs["max_tokens"] == 604000
    assert kwargs["top_p"] == 0.98

    # 与调用点的显式 temperature 共存，不重复
    def _accept(**kw):
        return kw

    merged = _accept(temperature=0.9, **kwargs)
    assert merged["temperature"] == 0.9
