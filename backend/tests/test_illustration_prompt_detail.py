"""第 27 刀（2026-09-20）：兜底成稿的**体态细节**与**旁观角色**，以及散文通道只收非动作事实。

用户实锤（原话）：
  「情况一的 anima_tags·第二行（文段）很明显不够详细，只说了一个角色跪下掰开臀部，
    但本应该是弯腰翘起屁股掰开臀部，且第二个角色根本没有描述内容」
  「niji_sections 的情况和 anima_tags 一样」

三层根因：
  ① `_SCENE_ACTION_RULES` 缺「跪姿 + 展示臀部」的**同句**体态规则 ⇒ 兜底体态只剩 `kneeling`；
  ② 缺「旁观」动作规则 ⇒ 具名却无可映射动作的角色退化成 `is present in frame`（等于从画面消失）；
  ③ `complete_field_coverage` 把**动名词事实**塞进 anima 文段的 `depicted with` **名词槽** ⇒
     `The three adult characters are depicted with withdrawing the fingers.`（病句）。
     ⚠ 但**不得**顺手把动作片段从散文通道排除：krea2 / natural_language 没有 tags 行，
     散文段是动作事实的唯一落点（试改即 7 例红灯，见下方反向锁）。

⚠ 反向锁：体态/旁观规则都必须是**同句**锚定，跨句一律不触发（第 21 刀「跨句动作片段串味」同族）。
"""

import re

import pytest

from app.services import image_prompt_profiles as profiles

# 用户实锤的兜底场景原文（`_tmp/item25/final_prompts.txt` 情况①）
_情况一 = "明澜跪在床边，把双手贴在臀肉上掰开屁股。我紧捏着明澜的臀部。清夏在旁边看着。"
_情况一角色 = ["明澜", "清夏"]


def _场景(narrative: str, actors, **extra) -> dict:
    """只给叙事、不给外貌/草稿：把「事实必须来自正文出处」这条前提钉死。"""
    scene = {
        "narrative": narrative,
        "appearance": "",
        "draft_prompt": "",
        "locale": "",
        "actors": list(actors),
        "rating": "nsfw",
    }
    scene.update(extra)
    return scene


# ── ① 体态：跪姿展示臀部必须补出「弯腰 / 抬臀」这一层 ──

def test_跪姿展示臀部补出弯腰抬臀体态():
    tags = profiles._scene_action_tags(_场景(_情况一, _情况一角色))

    assert "bending forward with her buttocks raised" in tags
    assert "kneeling" in tags
    assert "both hands spreading her buttocks apart" in tags


def test_体态规则不凭空触发():
    """反向锁：正文没有跪姿展示臀部时，体态串一条都不许出现。"""
    tags = profiles._scene_action_tags(
        _场景("她安静地站在窗边，看着外面下雨。", ["明澜", "清夏"])
    )

    assert "bending forward with her buttocks raised" not in tags


def test_体态规则不跨句串味():
    """⚠ 第 21 刀同族：跪在上一句、展示臀部在下一句 ⇒ 不得拼出体态。"""
    tags = profiles._scene_action_tags(
        _场景("明澜跪在床边，喘着气。清夏掰开臀肉，露出小穴。", ["明澜", "清夏"])
    )

    assert "bending forward with her buttocks raised" not in tags


# ── ② 旁观角色必须有可见描述，不得只写「在场」 ──

def test_旁观角色不退化成仅在场():
    sentences = profiles._multi_actor_sentences(_场景(_情况一, _情况一角色))

    assert len(sentences) == 2
    assert sentences[0].startswith("The primary adult character on the left side of the frame")
    assert sentences[1].startswith("The second adult character on the right side of the frame")
    assert "is present in frame" not in sentences[1]
    assert "watching from the sidelines" in sentences[1]


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
def test_四档兜底都写出体态与旁观描述(profile):
    prompt = profiles.deterministic_fallback(profile, _场景(_情况一, _情况一角色))

    assert "bending forward with her buttocks raised" in prompt
    assert "is present in frame" not in prompt
    assert "watching from the sidelines" in prompt


# ── ③ 散文通道只收非动作事实（动作片段归 tags 通道） ──

def test_anima文段不把动作片段写成depicted_with病句():
    """`depicted with ` 后面只能接名词短语/独立主格；接动名词会产出病句。"""
    scene = _场景("我悄悄抽出了正在抚弄阴阜的手指。", ["明澜"])
    prompt = chr(10).join((
        "masterpiece, best quality, 1girl, warm directional light",
        "A lone adult woman stands still in a warm room under directional light.",
    ))

    repaired, _ledger = profiles.complete_field_coverage("anima_tags", prompt, scene)
    tags_line, prose_line = repaired.splitlines()[:2]

    assert "withdrawing the fingers" in tags_line          # 动作照旧进 tags 通道
    assert "depicted with withdrawing the fingers" not in repaired
    assert "withdrawing the fingers" not in prose_line      # 但不进散文通道


def test_krea2与natural散文段仍是动作事实的唯一通道():
    """⚠ 反向锁（第 27 刀试改时被 **7 例红灯**拦下）：krea2 / natural_language **没有 tags 行**，
    散文段是动作事实的**唯一**落点 ⇒ 不得以「动作只进 tags 通道」为由把它们从散文段里排除
    （`test_所有模板把连续高潮编译为复合动作并让动态破损服装覆盖基础服装` 就是靠它过的）。
    """
    scene = _场景(
        "她仰卧在床上，双腿压上对方肩膀，面对面保持传教士体位。", ["甲"],
        appearance="", wardrobe="",
    )
    seed = "A medium shot places an adult woman in a room under directional side light."

    for profile in ("krea2", "natural_language"):
        completed, _ledger = profiles.complete_field_coverage(profile, seed, scene)
        assert "legs raised over the partner's shoulders" in completed, profile


def test_anima文段仍复述外貌真事实():
    """既有锁（不得被上面两刀波及）：外貌类**名词事实**照旧补进文段。"""
    scene = _场景("她站在廊下。", ["甲"], appearance="narrow dark-red eyes")
    prompt = chr(10).join((
        "1girl, solo, narrow dark-crimson eyes, pale robe,",
        "The adult woman stands under the eaves.",
    ))

    repaired, _ledger = profiles.complete_field_coverage("anima_tags", prompt, scene)

    assert "narrow dark-red eyes" in repaired.splitlines()[1]


# ── 第 28 刀（2026-09-20 真机）：① krea2 散文补全必须成句；③ 动作槽开关必须真能关掉 ──

def test_krea2补全不得在句号后接小写片段():
    """第 28 刀①（真机实锤）：旧实现把账本片段 `"; ".join(...) + "."` 拼在模型稿后 ⇒
    `… stands still in the room. withdrawing the fingers; a clear primary visual focus, …`
    —— 模型句已收尾，后面却接**小写动词片段**，用户实锤为断句。

    定案＝**只改标点、不改字**（句号→逗号 ⇒ 片段变成模型句的分词状语 / 同位语续列）：
      ✗ 首字母大写 ⇒ 破字段验收的**逐字**锁（`bedchamber` / `a glowing spiral object …`，实测 3 例红灯）；
      ✗ 加引导句 ⇒ 与「砍掉模板尾巴」的用户口径相反。
    """
    scene = _场景("我悄悄抽出了正在抚弄阴阜的手指。", ["明澜"])
    seed = "An adult woman stands still in the room."

    repaired, _ledger = profiles.complete_field_coverage("krea2", seed, scene)

    assert "withdrawing the fingers" in repaired
    assert ", withdrawing the fingers;" in repaired      # 片段挂成续列，不再是新句
    assert ". withdrawing the fingers" not in repaired
    assert not re.search(r"\.\s+[a-z]", repaired), repaired


def test_krea2补全只改标点不改字():
    """反向锁（对应上一条）：事实片段必须**逐字**保留——大写 / 改写 / 丢词都是回归。"""
    scene = _场景("我悄悄抽出了正在抚弄阴阜的手指。", ["明澜"])
    seed = "An adult woman stands still in the room."

    repaired, _ledger = profiles.complete_field_coverage("krea2", seed, scene)

    for detail in [
        "withdrawing the fingers",
        "a scene-appropriate medium shot with coherent perspective and readable depth of field",
        "consistent anatomy, stable contours, clean edges, and restrained detail",
    ]:
        assert detail in repaired, detail
    assert repaired.rstrip().endswith(".")


def test_anima文段动作槽开关真能关掉():
    """第 28 刀③（探针实锤）：旧实现只清空 `_scene_action_tags(scene)`，
    从 `facts` 参数传进来的**动名词会绕过开关** ⇒ 仍输出
    `In this instant, the concrete action is withdrawing the fingers.`。修后动名词也受开关管。"""
    scene = _场景("她安静地站着。", ["甲"])

    off = profiles._anima_content_description(
        scene, ["withdrawing the fingers"], include_actions=False,
    )
    assert "withdrawing the fingers" not in off
    assert "concrete action" not in off

    # 反向锁：开关打开时动名词仍必须进动作槽（第 27 刀的行为不许回退）
    on = profiles._anima_content_description(
        scene, ["withdrawing the fingers"], include_actions=True,
    )
    assert "concrete action is withdrawing the fingers" in on


# ── 第 29 刀（2026-09-21 用户定案）：动作事实的「同部位已写过动作」冲突闸 ──
#
# 用户实锤（i28 32 格 · L3·NSFW多人）：
#   「niji_sections 第三名角色出镜了…不过它的 `with one hand touching a thigh` 和
#    `withdrawing the fingers` 冲突，且 `withdrawing the fingers` 没有指明…是哪名角色的臀部」
#   「anima_tags 的 `The two adult characters are depicted with both palms pressed against
#    her own buttocks.`」（第 31 刀后该串合并为 `both hands spreading her buttocks apart`，
#    且因以**施动部位**开头而走动作槽 ⇒ `depicted with` 病句形态本身也不再出现）
# 定案：**同部位已有动作表述 ⇒ 该条动作事实不追加**；形态默认值照旧补；抽不出部位的事实不受管。

_真机L3模型稿_anima = chr(10).join((
    "masterpiece, best quality, 2girls, two adult women kneeling on a soft surface, "
    "each with both hands gripping and pulling apart their own buttocks, "
    "the third adult character kneeling in the center foreground,",
    "The scene centers on the two women's exposed lower bodies as their fingers spread "
    "her cheeks wide, while the third figure crouches close and watches them.",
))
_真机L3模型稿_niji = chr(10).join((
    "On the left side of the frame, the primary adult character, her hands reaching behind "
    "to spread her own buttocks, her wet pussy and anus exposed, a mix of shyness and "
    "surrender in her downward gaze; in the middle the third adult character sits back "
    "with one hand touching a thigh, observing both with intent focus",
    "refined illustration, controlled palette",
    "low-angle medium shot, dim intimate room",
    "--ar 4:3 --niji 6",
))
_动作事实场景narrative = "明澜跪在床边，把双手贴在臀肉上掰开屁股。我悄悄抽出了正在抚弄的手指。她的臀部轻轻晃着。"


def test_冲突闸前置证据_两条动作事实确实在账本里():
    """第 31 刀后：掰开臀部只剩**一条**具体串（`both hands spreading her buttocks apart`），
    原「双掌贴臀」支已删 ⇒ 这里同时钉住「新串在」与「旧串不在」两个方向。"""
    scene = _场景(_动作事实场景narrative, ["明澜", "清夏"])

    tags = profiles._scene_action_tags(scene)
    assert "withdrawing the fingers" in tags
    assert "swaying her hips" in tags
    assert "both hands spreading her buttocks apart" in tags
    assert "both palms pressed against her own buttocks" not in tags


def test_anima同部位已写过动作则不再追加裸动作串与depicted_with病句():
    scene = _场景(_动作事实场景narrative, ["明澜", "清夏"], appearance="narrow dark-red eyes")

    repaired, _ledger = profiles.complete_field_coverage(
        "anima_tags", _真机L3模型稿_anima, scene,
    )
    _tags_line, _prose_line = repaired.splitlines()[:2]

    assert "swaying her hips" not in repaired
    assert "both hands spreading her buttocks apart" not in repaired
    # 具体病句形态：复数主语 ＋ 单数物主（`The two adult characters are depicted with both
    # palms pressed against her own buttocks.`）必须消失；第 31 刀后该串以**施动部位**开头、
    # 走动作槽 ⇒ `depicted with` 这种形态本身也不该再出现。
    assert "depicted with both hands spreading" not in repaired
    # 闸只掐动作事实：外貌类名词事实照旧补进成稿（`depicted with <名词短语>` 是第 27 刀的既有设计）
    assert "narrow dark-red eyes" in repaired


def test_niji段尾不再追加与模型动作互斥的动作事实():
    scene = _场景(_动作事实场景narrative, ["明澜", "清夏"])

    repaired, _ledger = profiles.complete_field_coverage(
        "niji_sections", _真机L3模型稿_niji, scene,
    )
    lines = repaired.splitlines()

    assert len(lines) == 4
    assert lines[3] == "--ar 4:3 --niji 6"          # 段4 参数逐字节不变
    assert "withdrawing the fingers" not in repaired  # 与 `one hand touching a thigh` 互斥
    assert "swaying her hips" not in repaired
    assert not lines[0].rstrip().endswith("swaying her hips")  # 不再挂裸串尾巴


def test_无同部位动作时动作事实照旧追加():
    """反向锁：模型稿没有任何「身体部位＋动作」⇒ 冲突闸必须放行（零回归）。"""
    scene = _场景(_动作事实场景narrative, ["明澜", "清夏"])
    seed = "An adult woman stands still in the room under soft directional light."

    for profile in ("krea2", "natural_language"):
        completed, _ledger = profiles.complete_field_coverage(profile, seed, scene)
        assert "withdrawing the fingers" in completed, profile


def test_抽不出部位的动作事实不受冲突闸影响():
    """反向锁：`penetrative intercourse` 这类**没有部位词**的事实逐字节不受管
    （合同锁要求它逐字出现在成稿里）。"""
    scene = _场景("他重新推进去了，高潮骤然袭来。她掰开臀肉，露出小穴。", ["明澜", "清夏"])
    assert "penetrative intercourse" in profiles._scene_action_tags(scene)
    assert profiles._action_fact_conflicts(_真机L3模型稿_niji, "penetrative intercourse") is False
    assert profiles._action_fact_conflicts(_真机L3模型稿_niji, "swaying her hips") is True

    completed, _ledger = profiles.complete_field_coverage("krea2", _真机L3模型稿_niji, scene)
    assert "penetrative intercourse" in completed
