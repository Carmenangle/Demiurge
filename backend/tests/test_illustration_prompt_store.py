"""illustration_prompt_store 单测：落盘形状 + 坏值容错 + 判据档常开。

覆盖真实失败路径：缺文件 / 坏 JSON / 字段类型不符 / 判据留空，都必须回缺省判据而不是抛错；
且 `effective_climax_anchor_rubric()` **永不返回空串**——判据档常开（2026-09-18 用户定案
去掉启用开关），它的失败上界是「退回中文词表档」，所以没有"用不用它"这个选择，
但「拿不到判据就不调模型」这条防御性闸仍然保留（见 test_agent_dispatch 的 rubric_empty 例）。
"""
from __future__ import annotations

import json

from app.services import illustration_prompt_store as store


def point_at(monkeypatch, tmp_path):
    """把落盘路径指到 tmp（不碰真实 DATA_DIR）。"""
    path = tmp_path / "illustration_prompts.json"
    monkeypatch.setattr(store, "_config_path", lambda: path)
    return path


def test_缺文件回缺省判据(monkeypatch, tmp_path):
    point_at(monkeypatch, tmp_path)
    config = store.load_config()
    assert config["climax_anchor"] == {"rubric": store.DEFAULT_CLIMAX_ANCHOR_RUBRIC}
    assert store.effective_climax_anchor_rubric() == store.DEFAULT_CLIMAX_ANCHOR_RUBRIC


def test_用户改过的判据生效(monkeypatch, tmp_path):
    path = point_at(monkeypatch, tmp_path)
    path.write_text(
        json.dumps({"climax_anchor": {"rubric": "自定义判据"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert store.effective_climax_anchor_rubric() == "自定义判据"


def test_没有启用字段这个概念(monkeypatch, tmp_path):
    """cfg 里残留历史的 `enabled` 键也不该被读出来（开关已下线，未知字段一律丢弃）。"""
    path = point_at(monkeypatch, tmp_path)
    path.write_text(
        json.dumps({"climax_anchor": {"enabled": False, "rubric": "自定义判据"}}),
        encoding="utf-8",
    )
    assert store.load_config()["climax_anchor"] == {"rubric": "自定义判据"}
    assert "enabled" not in store.settings_payload()["climax_anchor"]


def test_坏JSON按缺省不抛(monkeypatch, tmp_path):
    path = point_at(monkeypatch, tmp_path)
    path.write_text("{不是 json", encoding="utf-8")
    assert store.effective_climax_anchor_rubric() == store.DEFAULT_CLIMAX_ANCHOR_RUBRIC


def test_判据字段类型不符回缺省(monkeypatch, tmp_path):
    path = point_at(monkeypatch, tmp_path)
    path.write_text(json.dumps({"climax_anchor": {"rubric": 42}}), encoding="utf-8")
    assert store.effective_climax_anchor_rubric() == store.DEFAULT_CLIMAX_ANCHOR_RUBRIC


def test_判据只有空白回缺省(monkeypatch, tmp_path):
    path = point_at(monkeypatch, tmp_path)
    path.write_text(json.dumps({"climax_anchor": {"rubric": "   \n  "}}), encoding="utf-8")
    assert store.effective_climax_anchor_rubric() == store.DEFAULT_CLIMAX_ANCHOR_RUBRIC


def test_保存只留合法字段且判据留空回缺省(monkeypatch, tmp_path):
    path = point_at(monkeypatch, tmp_path)
    saved = store.save_config({"climax_anchor": {"rubric": "   ", "未知字段": "x"}, "垃圾顶层": {"a": 1}})
    assert saved == {"climax_anchor": {"rubric": store.DEFAULT_CLIMAX_ANCHOR_RUBRIC}}
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    assert store.effective_climax_anchor_rubric() == store.DEFAULT_CLIMAX_ANCHOR_RUBRIC


def test_判据永不返回空串(monkeypatch, tmp_path):
    """常开的前提：任何坏输入都不得让判据变成空串（空串＝判据档静默失效）。"""
    path = point_at(monkeypatch, tmp_path)
    for bad in ("{不是 json", json.dumps({"climax_anchor": None}), json.dumps([]),
                json.dumps({"climax_anchor": {"rubric": 0}}), json.dumps({"climax_anchor": {"rubric": "  "}})):
        path.write_text(bad, encoding="utf-8")
        assert store.effective_climax_anchor_rubric(), f"判据为空串：{bad!r}"


def test_设置载荷带只读合同且缺省判据不含合同(monkeypatch, tmp_path):
    """可改/只读的分界：判据里不能出现输出合同，合同必须由程序侧拼接。"""
    point_at(monkeypatch, tmp_path)
    payload = store.settings_payload()
    assert payload["default_rubric"] == store.DEFAULT_CLIMAX_ANCHOR_RUBRIC
    assert payload["output_contract"]
    assert "只输出严格 JSON" not in store.DEFAULT_CLIMAX_ANCHOR_RUBRIC


# ---------------------------------------------------------------------------
# 2026-09-18 第六刀：多人场景「动作同框」压过「关系帧」
#
# 真机留样实证（`backend/_probe_anchor_chain_samples.json`，L3 三条）没有一条落在用户点名
# 的区间（「你们两个都自己用手掰开。」→「那么，我该插进哪一边呢？」）内：分别落在
# 「两人都无法把视线从我身上移开。」（关系定格）、「图片加载中...」（占位串）、空。
# judge 的 reason 自述「无须锚定后文单焦点动作」——它是**照旧判据执行**的：
# 旧【落点判据】三 明文禁止「故事继续往下走…更激烈、更露骨」的位置，把多人场景指向
# 同框静止的「关系帧」，而用户要的是**两名角色各自翘臀掰开的动作帧**。
#
# ⚠ 历史教训（本文件顶部注释 + 设计稿 §4.1）：往判据里加约束曾三次实测净亏
# （v7/v9/v10 与「主题同框前置门」），根因是**多约束互相污染、被过度应用到单人场景**。
# 故本刀的铁律是：**新增语义只出现在多人分支内，且必须显式声明单人场景不适用**。
# 这四条是**性质锁**（锁口径，不锁字面），调判据文案不需要改测试。
# ---------------------------------------------------------------------------


def test_多人分支优先动作同框而不是关系帧():
    """多人分支必须把「各自有可见动作」抬到「同框静止关系帧」之上（第六刀核心）。"""
    rubric = store.DEFAULT_CLIMAX_ANCHOR_RUBRIC
    assert "动作" in rubric, "判据里必须出现「动作」这一优先级"
    # 多人分支里要同时出现「动作」与「同框/相对位置」，且动作在前（＝优先）
    multi_at = rubric.find("这几个人处在什么关系里")
    assert multi_at >= 0, "判据缺少「这几个人处在什么关系里」多人分支"
    branch = rubric[multi_at:rubric.find("四、", multi_at)]
    assert "动作" in branch, "多人分支内必须引入「动作同框」优先级"
    assert branch.find("动作") < branch.find("同框", 0) or "优先" in branch


def test_旧判据那句显式禁令已撤掉():
    """旧句「即使后面那一句更激烈、更露骨，也不要改成它」是病根，必须撤掉。"""
    assert "即使后面那一句更激烈、更露骨，也不要改成它" not in store.DEFAULT_CLIMAX_ANCHOR_RUBRIC


def test_单人场景不得被动作同框规则波及():
    """历史教训：加约束会被过度应用到单人场景 ⇒ 多人口径必须显式排除单人。

    ⚠ 2026-09-19 受控红检发现本锁原本**虚设**：旧断言只要 branch 里出现「单人」二字即可，
    而句尾「动作同框规则**不适用于单人**」本身就含「单人」—— 把前面
    「画面里只有一个角色时，本条不适用」**整句删掉**仍然判绿。
    现改为「**条件 + 后果**都要在」：既要出现条件（只有一个角色 / 仅一名 / 单人场景），
    又要在该处**之后**出现「不适用」。缺一即红（受控红检 3/3：删整句 / 只留条件 / 只留后果 均红）。
    """
    rubric = store.DEFAULT_CLIMAX_ANCHOR_RUBRIC
    multi_at = rubric.find("这几个人处在什么关系里")
    assert multi_at >= 0
    branch = rubric[multi_at:rubric.find("四、", multi_at)]
    cond_at = -1
    for key in ("只有一个角色", "仅一名", "单人场景"):
        cond_at = branch.find(key)
        if cond_at >= 0:
            break
    assert cond_at >= 0, (
        "多人分支必须显式写明**条件**（画面里只有一个角色时不适用），"
        "否则会重演 v7/v9/v10「约束被过度应用到单人场景」的净亏"
    )
    assert "不适用" in branch[cond_at:], (
        "单人排除必须给出**后果**（本条不适用）；只有条件没有后果等于没排除"
        "—— 旧锁只查「单人」二字，被句尾的「不适用于单人」蒙混过关（受控红检实测）"
    )


def test_动作同框不得退化成选剧烈动作过程():
    """【明确排除】里「剧烈动作过程单帧辨不清」这条不能被新规则吃掉。"""
    rubric = store.DEFAULT_CLIMAX_ANCHOR_RUBRIC
    assert "剧烈动作的过程之中" in rubric
    assert "辨不清" in rubric


# ---------------------------------------------------------------------------
# 2026-09-19 v12：收束判据是**有适用条件的**，不是无条件条款
#
# A/B 实锤（`docs/illustration-anchor-ab-2026-09-19.md`，真机 8 次/格）：
#   · 无条件版（A 档，只加「应当选动作收束的那一瞬」）⇒ 救 L1（52.8→24.6）却**毁 L2**
#     （11.3→41.2，命中 5/8→2/5）——该条款**被过度应用到无动作的文戏**，模型转去抓
#     「意象定型」句，丢掉结尾表情帧。即本文件上面那条「多约束污染」史训的**第四次重演**。
#   · 加前置条件版（v12）⇒ **三格全赢**：L1 52.8→40.1（新验收口径命中 0/8→3/8）、
#     L2 11.3→0.5（命中 5/8→7/8）、L3 31.0→27.9。
# ⇒ 本锁要求：「收束」这一正面指引必须与「无动作时不适用」的范围声明**成对出现**；
#   有人改回无条件版就立刻红。**不锁字面**——只要两件语义都在同一句里即可。
# 真源：设计稿 §7.5 与根 `MEMORY.md`「判据 A/B 四轮」条；本锁是那次 A/B 的机械化沉淀。
# ---------------------------------------------------------------------------


def test_收束判据必须限定在存在动作时生效():
    """「收束」正面指引必须带范围声明，否则会重演 A 档对 L2 的净亏。"""
    rubric = store.DEFAULT_CLIMAX_ANCHOR_RUBRIC
    excl = rubric[rubric.find("【明确排除】"):rubric.find("【优先选】")]
    at = excl.find("收束")
    assert at >= 0, "排除项必须给出正面指引：动作存在时应选「收束」的那一瞬"
    assert "不适用" in excl[at:], (
        "「收束」必须紧跟范围声明（无动作时本条不适用）——"
        "无条件版会把无动作文戏也管辖掉，L2 实测命中 5/8→2/5、距离 11.3→41.2"
    )


# ---------------------------------------------------------------------------
# 2026-09-19 v12.1（分支入口口径）—— **已试行，A/B 净亏，已回退到 v12**
#
# 原假设：判据分支入口写的是含糊的「多角色剧情」（会被按**角色表人数**读），而用户 09-19 定案口径是
#   「**画面主体数**」（第一人称叙事者「我」不占画面位置、第三人称「主人公」算一个）。若 L2
#   （角色表 3 人、画面实际只 1 人）被误读进多角色分支，§明确排除四 会把**正确答案那一帧**也排除掉。
#
# 实测（`scripts/_ab_v121.py`，真机 `deepseek-v4-flash-0731`，n=6/格，逐格距离均值）：
#   · L1 45.5 → 47.8（略退）   · **L2 9.7 → 16.8**（邻中 5/6→3/6，**明确退步**）   · L3 29.5 → 22.5（n=6，方差内不可判）
#   reason 文本显示：写明「画面主体数」后，模型反而更积极去找**两人同框**的帧
#   （"罗瑟琳…与塞蕾娜…同框" 占 77%）⇒ 把 L2 的单人表情帧挤掉。
#   ⇒ **同一「多约束污染」失效模式的第 N 次重演**：入口口径这处措辞本身就是新的扰动量，
#     「多角色剧情」虽含糊，但在当前判据里**恰好**把单人场景挡在多人分支之外，动它反而更糟。
#
# 结论：**rubric 保持 v12（sha1 `16b1a2d70a30`、1221 字）不动**。此前的
#   `test_分支入口必须按画面主体数定义而不是含糊的多角色剧情` **已删除**——它与「保持 v12」的
#   决策直接冲突，且该口径修法已被 A/B 证否。若将来仍要治「入口口径」，**不得再靠改 rubric 措辞**，
#   应走「机械预判画面主体数」等非 rubric 路径（见 `docs/illustration-anchor-l3-2026-09-19.md`）。
# 真源：`docs/illustration-anchor-l3-2026-09-19.md`、`scripts/_v121ab.txt`。
# ---------------------------------------------------------------------------
