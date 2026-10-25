"""角色动态状态单一属主：按 repo_id 存「可变状态」，core 不在这里。

设计见 ARCHITECTURE.md「剧情能动性引擎」支柱 1。分工：
- core（人设根基/外观/死穴/机制/弧线）写在卡文件（character_store 的 card.json），**永不由本模块自动更**。
- state（好感度数值 + 态度/心情/所在等叙事字段）按 repo_id 作用域、随剧情走，本模块拥有。

每次更新 = 一条带证据的 StateDelta（from→to + 证据 + turn + source）。AI 召回读到
「态度: 戒备 (因第3章救援)」= 角色发展而非矛盾；人为改若不带证据 → source=user,证据空，
供上层识别为「设定注入」而非剧情。

落盘：<base>/<仓库名>/state.json（2026-09-18 ③ 起与 chat/图/文档同域；旧 <base>/<safe repo_id>/
由 `repo_meta.engine_repo_dir` 惰性迁移）。base 由调用方注入，不读 config。
纯逻辑（parse_deltas/apply_deltas/render_state_block）无 I/O，可单测；load/save 是唯一 I/O。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.services import prompt_clean, repo_meta

STATE_FILE = "state.json"
SRC_AUTO = "auto"
SRC_USER = "user"
_KIND_NUM = "数值"
_KIND_NARR = "叙事"
_HISTORY_CAP = 1000  # 审计历史封顶，防无限膨胀（超出丢最旧）。
# M1 审计 #2（2026-09-06）：原 200 在长剧情下会让 rollback_from_turn 还原错值
# （静默错账实测复现）；提到 1000 覆盖数百回合，仍超窗时由 rollback_from_turn
# 显式上报不完整字段（绝不静默还原错值）。

AFFINITY_FIELD = "好感度"       # 好感度字段名（本模块单一属主，roleplay_agency 引用）
_AFFINITY_SUFFIX = f"·{AFFINITY_FIELD}"  # 多角色归属写法「角色·好感度」（本模块多处共用）
_NUM_UNBOUNDED = 1e9
# 已知数值字段的默认边界：好感度是 -100(厌恶)..100(喜爱) 连续标量，不搞卡内档位那套。
# 首次由 delta/set 创建且未显式带 min/max 时用此表；未登记字段仍无界（±1e9）。
_FIELD_BOUNDS: dict[str, tuple[float, float]] = {AFFINITY_FIELD: (-100.0, 100.0)}
_STATE_LABELS = ("身体状态", "精神状态", "生理状态", "心理状态", "外观状态", "伤势状态", "衣着状态")
# 主角/第一人称代词不入角色状态表（2026-09-01 用户定案）：AI 常把「我/主角/你/玩家」
# 当角色名写进状态表，但第一人称的「我」不是剧情角色，不应占一行。
_PROTAGONIST_OWNERS = frozenset({
    "主角", "我", "我们", "你", "您", "玩家", "宿主", "用户", "user", "player",
})


def _is_protagonist_leaf(leaf: str) -> bool:
    """字段归属是否为主角（第一人称视角）——是则不入角色状态表（纯逻辑）。"""
    if "·" not in (leaf or ""):
        return False
    return leaf.split("·", 1)[0].strip() in _PROTAGONIST_OWNERS


def _is_affinity_leaf(leaf: str) -> bool:
    """是否好感度字段：裸键 `好感度`（单主角写法）或多角色归属 `角色名·好感度`。

    「add 无字段拒收」闸的**适用范围限定**（红线：判据改动只加适用范围限定）——
    只对好感度生效，其他数值字段（如 金币）维持旧行为，否则会外溢成全局行为变更。
    """
    return leaf == AFFINITY_FIELD or leaf.endswith(_AFFINITY_SUFFIX)


def _canonical_leaf(
    leaf: str, card_name: str, owner_hints: dict[str, str] | None = None,
) -> str:
    """把同一角色字段的中点、旧拼接和无归属单角色写法归为一个稳定键。

    无归属裸字段只继承「同 label 唯一显式角色」（owner_hints）；没有唯一角色时
    保留裸键，**不再回退 card_name**——合集卡名（如「苍梧仙闻录」）不是角色名，
    回退会把卡名误记成角色（2026-09-01 用户实锤）。裸键由 consolidate_fields
    的 explicit_leaves 清理（有显式归属时）或前端 fallback 分组兜底。
    """
    value = (leaf or "").strip()
    separated = re.match(r"^(.+?)[·：:](.+)$", value)
    if separated:
        return f"{separated.group(1).strip()}·{separated.group(2).strip()}"
    if value in _STATE_LABELS:
        owner = (owner_hints or {}).get(value)
        return f"{owner}·{value}" if owner else value
    for label in _STATE_LABELS:
        if value.endswith(label) and len(value) > len(label):
            return f"{value[:-len(label)].strip()}·{label}"
    return value


def consolidate_fields(state: "CharacterState") -> "CharacterState":
    """合并存量别名；同角色同字段冲突时保留 turn 最新的值。"""
    hints = _owner_hints(state)

    def merge(fields: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for leaf, value in fields.items():
            key = _canonical_leaf(leaf, state.card_name, hints)
            current = result.get(key)
            if current is None or int(value.turn) >= int(current.turn):
                result[key] = value
        # 无归属裸键被显式归属键淘汰（旧拼接残留）；「好感度」例外——单主角模式的
        # 全局好感度就是裸键「好感度」，删掉会让好感度字段凭空消失/换新
        # （2026-09-01 用户实锤），故排除在清理集之外。
        explicit_leaves = {
            key.split("·", 1)[1] for key in result if "·" in key
        } - {AFFINITY_FIELD}
        for leaf in explicit_leaves:
            result.pop(leaf, None)
        return result

    state.数值 = merge(state.数值)
    state.叙事 = merge(state.叙事)
    return state


def _owner_hints(state: "CharacterState") -> dict[str, str]:
    """同一种状态只出现一个明确角色时，供无归属旧字段继承该角色。"""
    owners: dict[str, set[str]] = {}
    for leaf in (*state.数值.keys(), *state.叙事.keys()):
        canonical = _canonical_leaf(leaf, "")
        if "·" not in canonical:
            continue
        owner, label = canonical.split("·", 1)
        if label in _STATE_LABELS and owner:
            owners.setdefault(label, set()).add(owner)
    return {label: next(iter(values)) for label, values in owners.items() if len(values) == 1}


def _bounds(leaf: str, nf: "NumericField | None") -> tuple[float, float]:
    """取某数值字段的 [min,max]：已存在字段沿用其边界；否则查已知字段表，再退无界。"""
    if nf is not None:
        return nf.min, nf.max
    return _FIELD_BOUNDS.get(leaf, (-_NUM_UNBOUNDED, _NUM_UNBOUNDED))


@dataclass
class NumericField:
    """数值状态（如好感度）：delta 用 add 累加，clamp 在 [min,max]。"""
    value: float
    min: float = -1e9
    max: float = 1e9
    turn: int = 0
    evidence: str = ""
    source: str = SRC_AUTO

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "min": self.min, "max": self.max,
                "turn": self.turn, "evidence": self.evidence, "source": self.source}


@dataclass
class NarrativeField:
    """叙事状态（态度/心情/所在等）：delta 用 set 覆盖。"""
    value: str
    turn: int = 0
    evidence: str = ""
    source: str = SRC_AUTO

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "turn": self.turn,
                "evidence": self.evidence, "source": self.source}


@dataclass
class StateDelta:
    """一次状态变更。field = "数值/好感度" 或 "叙事/对{{user}}态度"。"""
    turn: int
    field: str
    op: str            # add（数值增量）| set（叙事覆盖 / 数值首建初值）
    value: Any         # add: 数值增量；set: 新字符串值（叙事）或初始数值（数值首建）
    evidence: str = ""
    source: str = SRC_AUTO

    def kind(self) -> str:
        return self.field.split("/", 1)[0]

    def leaf(self) -> str:
        return self.field.split("/", 1)[1] if "/" in self.field else self.field


@dataclass
class Snapshot:
    """显示层状态栏快照：AI 每轮吐的 <status> 整块原文，引擎不解析、原样存。

    抗压缩核心——状态栏不靠聊天记录续命，落进文件、下轮重注入（对治原酒馆填表丢上下文）。
    """
    text: str = ""
    turn: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "turn": self.turn}


@dataclass
class CharacterState:
    """某作品(repo_id)下某卡的可变状态。core 不在此，只在卡文件。"""
    card_name: str
    repo_id: str
    数值: dict[str, NumericField] = field(default_factory=dict)
    叙事: dict[str, NarrativeField] = field(default_factory=dict)
    历史: list[dict[str, Any]] = field(default_factory=list)
    快照: Snapshot = field(default_factory=Snapshot)

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_name": self.card_name,
            "repo_id": self.repo_id,
            "数值": {k: v.to_dict() for k, v in self.数值.items()},
            "叙事": {k: v.to_dict() for k, v in self.叙事.items()},
            "历史": self.历史[-_HISTORY_CAP:],
            "快照": self.快照.to_dict(),
        }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_deltas(
    raw: Any, *, turn: int, source: str = SRC_AUTO, card_name: str = "",
    existing_state: CharacterState | None = None,
) -> list[StateDelta]:
    """把上层（LLM 抽取/人为编辑）产出的原始 JSON 归一成 StateDelta 列表（纯逻辑）。

    非法项跳过（不抛）：field 必须以「数值/」或「叙事/」开头；数值只认 add，叙事只认 set。
    证据为空**不拒绝**——保留下来供上层识别人为注入。source 由调用方声明（auto/user）。
    """
    if not isinstance(raw, list):
        return []
    out: list[StateDelta] = []
    hints = _owner_hints(existing_state) if existing_state is not None else None
    for item in raw:
        if not isinstance(item, dict):
            continue
        fld = str(item.get("field") or "").strip()
        if "/" not in fld:
            continue
        kind, leaf = fld.split("/", 1)
        canonical = _canonical_leaf(leaf, card_name, hints)
        # 主角/第一人称不入角色状态表（2026-09-01 用户定案）：AI 常把「我/主角/你」
        # 当角色名写进状态表，这些字段应留在 <status> 状态栏，不占状态表一行。
        if _is_protagonist_leaf(canonical):
            continue
        fld = f"{kind}/{canonical}"
        op = str(item.get("op") or "").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        if source != SRC_USER:
            # 2026-09-13 用户实锤：模型按防拦截预设写正文，evidence 引用正文时把 `@` 拆字
            # 标记一并带出（状态表「依据」显示成 @为@争@被@插@…）。表格文本不参与网关
            # 检测 ⇒ 落库前机械还原；人为编辑（source=user）原样保留，不动用户输入。
            evidence = prompt_clean.sanitize_table_text(evidence)
        if kind == _KIND_NUM:
            # 数值只认两种：add（相对现值累加）与 set（角色首次登场时设初始值）。
            # set 的实际生效门在 apply_deltas——只对尚不存在的字段播种，已存在一律忽略，
            # 防模型用 set 把累积好感一键重置（等价 godmode）。
            if op not in ("add", "set"):
                continue
            out.append(StateDelta(turn, fld, op, _num(item.get("value")), evidence, source))
        elif kind == _KIND_NARR:
            if op != "set":
                continue
            val = str(item.get("value") or "").strip()
            if source != SRC_USER:
                # 同 evidence：叙事字段值也可能整段引用防拦截正文。
                val = prompt_clean.sanitize_table_text(val)
            if not val:
                continue
            out.append(StateDelta(turn, fld, "set", val, evidence, source))
    return out


def apply_deltas(
    state: CharacterState, deltas: list[StateDelta],
    *, rejected: list[dict[str, Any]] | None = None,
) -> CharacterState:
    """按序应用 delta，原地更新 state 并逐条追加审计历史（含 from→to）。返回同一对象。

    数值：add 命中**不存在的好感度字段**时**拒收并上报**（2026-09-23 用户口径，见下）；
    set 只对**尚不存在**的字段播种初值（角色首次登场时按剧情因果给出的初始好感/恶感），
    字段已存在时整条忽略——防止模型用 set 把累积值一键重置（2026-09-22 用户口径：
    初次登场不应全为 0）。叙事：set 覆盖，字段不存在时直接创建。

    ⚠ 拒收闸（2026-09-23）：旧行为是 `before_n = nf.value if nf else 0.0` ⇒ add 命中不存在
    字段时**静默从 0 起算并建值**。这是不可逆的：字段一旦以 0 存在，后续 `set` 永远被
    `:279-281` 忽略、add 只能在 0 上累加 ⇒ 该角色初值被钉死。回滚路径更隐蔽——add 建的
    字段（审计 `from=0.0`）被 `_undo_entry` **打回 0 而字段保留**（`_tmp/sim_affinity.py`
    场景 C/E 实测）。故此处拒收，逼模型走 `set` 播种（`set` 口径要求带因果依据）。
    **适用范围限定**：拒收**只**对好感度字段（`_is_affinity_leaf`）生效；其他数值字段的**值**
    仍按 0 起算建（闸不外溢，见 `_tmp/sim_affinity*` 回归）。
    ⚠ 配套修复（2026-09-23 同日）：`add` 命中不存在的**非好感度**数值字段时，**审计的 `from`
    改记 `None`**（与 `set` 分支同口径）⇒ `_undo_entry` 回滚时 **pop 掉字段**，不再「打回 0
    留一个空壳」。值语义完全不变（仍 0 + delta），只改审计记法。
    ⚠ 历史遗留：改动**之前**已落盘的 `from=0.0` 审计条不受影响（改代码不改数据），回滚它们
    仍会留下「值=0 的空壳」。
    拒收项在传入 `rejected` 时逐条收集（本模块保持纯逻辑：不落盘、不改审计），供上层上报。
    """
    consolidate_fields(state)
    for d in deltas:
        leaf = d.leaf()
        if d.kind() == _KIND_NUM:
            nf = state.数值.get(leaf)
            lo, hi = _bounds(leaf, nf)
            if d.op == "set":
                if nf is not None:
                    # 已存在 → 拒绝覆盖（含非零累积值与已播种初值）；不记审计，回落忽略。
                    continue
                before_n: float | None = None   # None = 字段此前不存在（回滚等价删除）
                after_n = max(lo, min(hi, _num(d.value)))
                state.数值[leaf] = NumericField(after_n, lo, hi, d.turn, d.evidence, d.source)
                _log(state, d, before_n, after_n)
                continue
            if nf is None and _is_affinity_leaf(leaf):
                if rejected is not None:
                    rejected.append({
                        "field": f"{_KIND_NUM}/{leaf}", "op": "add",
                        "value": _num(d.value), "turn": d.turn,
                        "evidence": d.evidence, "reason": "affinity_field_absent",
                    })
                continue
            if nf is None:
                # 字段此前不存在：**值**仍按旧的 0 起算（闸不外溢，见上），但审计的 `from`
                # 改记 `None`——与 set 分支（:302）同口径。`_undo_entry` 的判据是
                # 「`from is None` ⇒ 此前不存在 ⇒ 直接 pop 字段」，所以只有这样才能让
                # 回滚删干净；旧写法 `nf.value if nf else 0.0` 会把 0 写成 from，
                # `_undo_entry` 只打回 0 而**保留字段**，回滚后留下一个「值=0 的空壳」
                # （比字段消失更难救：`set` 之后永远被忽略、`add` 只能在 0 上加）。
                before_n = None
                after_n = max(lo, min(hi, _num(d.value)))
            else:
                before_n = nf.value
                after_n = max(lo, min(hi, before_n + _num(d.value)))
            state.数值[leaf] = NumericField(after_n, lo, hi, d.turn, d.evidence, d.source)
            _log(state, d, before_n, after_n)
        else:
            sf = state.叙事.get(leaf)
            before_s = sf.value if sf else ""
            state.叙事[leaf] = NarrativeField(str(d.value), d.turn, d.evidence, d.source)
            _log(state, d, before_s, str(d.value))
    if len(state.历史) > _HISTORY_CAP:
        state.历史 = state.历史[-_HISTORY_CAP:]
    # 应用后再归一一次：delta 的 leaf 若在 parse 时无 owner_hints（如首轮、无
    # existing_state），落到裸键后会与存量显式键并存（2026-09-01 回归实证），
    # 末尾 consolidate 把裸键并回显式键（turn 新值覆盖旧值）。
    consolidate_fields(state)
    return state


def _log(state: CharacterState, d: StateDelta, before: Any, after: Any) -> None:
    state.历史.append({
        "turn": d.turn, "field": d.field, "op": d.op,
        "from": before, "to": after, "evidence": d.evidence, "source": d.source,
    })


def current_turn(state: CharacterState) -> int:
    """当前回合 = 快照与各字段 turn 的最大值。人工改用它，不推进剧情回合。"""
    turns = [state.快照.turn]
    turns += [f.turn for f in state.数值.values()]
    turns += [f.turn for f in state.叙事.values()]
    return max(turns) if turns else 0


def set_fields(state: CharacterState, edits: list[dict[str, Any]], *,
               turn: int, source: str = SRC_USER) -> int:
    """人工设定：把字段设为**精确值**（数值 set-value 而非累加，叙事 set-string），
    记 source=user、证据空——供上层识别为设定注入而非剧情。返回成功条数（纯逻辑）。

    非法项跳过：field 须以「数值/」或「叙事/」开头；数值值须可转 float。
    """
    consolidate_fields(state)
    done = 0
    for e in edits:
        if not isinstance(e, dict):
            continue
        fld = str(e.get("field") or "").strip()
        if "/" not in fld:
            continue
        kind, leaf = fld.split("/", 1)
        leaf = _canonical_leaf(leaf, state.card_name)
        fld = f"{kind}/{leaf}"
        if kind == _KIND_NUM:
            nf = state.数值.get(leaf)
            lo, hi = _bounds(leaf, nf)
            before = nf.value if nf else 0.0
            after = max(lo, min(hi, _num(e.get("value"))))
            state.数值[leaf] = NumericField(after, lo, hi, turn, "", source)
            _log(state, StateDelta(turn, fld, "set", after, "", source), before, after)
            done += 1
        elif kind == _KIND_NARR:
            val = str(e.get("value") or "").strip()
            sf = state.叙事.get(leaf)
            before_s = sf.value if sf else ""
            state.叙事[leaf] = NarrativeField(val, turn, "", source)
            _log(state, StateDelta(turn, fld, "set", val, "", source), before_s, val)
            done += 1
    if len(state.历史) > _HISTORY_CAP:
        state.历史 = state.历史[-_HISTORY_CAP:]
    return done


def rollback_last(state: CharacterState, *, n: int = 1) -> int:
    """撤销最近 n 条变更：逆序把字段还原到审计条目的 from 值，并移除这些历史条目。
    还原后 source=user（人为回滚痕迹）。返回实际撤销条数（纯逻辑）。"""
    undone = 0
    for _ in range(max(0, n)):
        if not state.历史:
            break
        undone += _undo_entry(state, state.历史.pop())
    return undone


def _leaf_value(state: CharacterState, field_path: str):
    """读字段现值（数值取 value，叙事取 value）；不存在返回 None。"""
    if "/" not in (field_path or ""):
        return None
    kind, leaf = field_path.split("/", 1)
    if kind == _KIND_NUM:
        nf = state.数值.get(leaf)
        return nf.value if nf else None
    nn = state.叙事.get(leaf)
    return nn.value if nn else None


def rollback_from_turn(state: CharacterState, *, turn: int) -> dict:
    """撤销 turn 及之后的所有状态变更（M1 记忆封口，2026-09-06 用户定案）：
    被删除/重新生成回合造成的状态变化等效清除——逆序回放审计历史还原 from 值，
    更早回合的变更与审计保留。返回 {"undone": 条数, "incomplete": 字段列表}（纯逻辑，幂等）。

    ⚠ 第 58 刀（2026-09-24 用户工作流实锤）：**初次登记（from=None）豁免回滚**。
    用户的工作流是「同一条消息反复重新生成直到满意」——若把字段创建也撤销，回滚后
    注入状态不再含该角色 ⇒ 模型不再写她 ⇒ 静默永久缺失（真机：师傅好感度两度被卷走）。
    逆序回放到「字段创建」这条时，该字段的后续变更已被还原、值恰好停在**初值**上
    ⇒ 保留字段＝保留初值（语义正确），并重盖一条同回合的「登记保留」审计让撤销链不断
    （下次同封口回滚会再次命中本豁免 ⇒ 幂等）。显式撤销（rollback_last／delete_field／
    purge_field）不受此豁免影响。

    incomplete = 还原后仍带「≥封口回合」变更痕迹的字段——其更早审计已被
    _HISTORY_CAP 截断，无法精确还原（此时保守保留现值并显式上报，绝不静默错账）。
    """
    undone = 0
    oldest_popped: dict[str, int] = {}   # 字段 → 本窗口内被撤的最老条目回合
    kept_seeds: dict[str, Any] = {}      # 第 58 刀：豁免回滚的初次登记（字段 → 保留的初值）
    rescue_keep: list[dict] = []         # 已是「登记保留」的条目：原样放回（幂等，不计 undone）
    while state.历史 and int(state.历史[-1].get("turn") or 0) >= turn:
        entry = state.历史.pop()
        fld = str(entry.get("field") or "")
        t = int(entry.get("turn") or 0)
        if str(entry.get("source") or "") == "rescue":
            rescue_keep.append(entry)    # 上次豁免留下的保留条目 ⇒ 原样放回，幂等
            continue
        frm = entry.get("from")
        if ((frm is None or (isinstance(frm, str) and not frm.strip()))
                and "/" in fld):
            cur = _leaf_value(state, fld)
            if cur is not None:
                # 初次登记豁免：保留字段（此刻值＝初值）。⚠ 重盖的审计**循环外**补——
                # 在循环内 append 会被本循环再次弹出（重盖 turn==封口回合 ⇒ 死循环，
                # 2026-09-24 实锤：test_character_state 卡死 4 分钟）。
                kept_seeds[fld] = cur
                undone += 1
                if fld not in oldest_popped or t < oldest_popped[fld]:
                    oldest_popped[fld] = t
                continue
        undone += _undo_entry(state, entry)
        if fld not in oldest_popped or t < oldest_popped[fld]:
            oldest_popped[fld] = t
    # 第 58 刀：为豁免保留的初次登记重盖审计（循环外补，撤销链不断 ⇒ 下次同封口
    # 回滚命中 source=rescue 分支原样放回 ⇒ 幂等）。
    for seed_fld, seed_val in kept_seeds.items():
        state.历史.append({
            "turn": turn, "field": seed_fld, "op": "set", "from": None,
            "to": seed_val, "evidence": "初次登记豁免回滚（同回合重生成工作流）",
            "source": "rescue",
        })
    # ⚠ rescue_keep 按 pop 顺序收集（大 turn → 小 turn，降序），必须 reversed 放回以
    #   维持「历史按 turn 升序、[-1] 最大」的循环终止前提（否则递减封口回滚时会被低 turn
    #   条目挡住、循环提前终止、rescue 残留 —— 2026-09-24 审计）。
    state.历史.extend(reversed(rescue_keep))
    rescue_paths = {str(e.get("field") or "") for e in rescue_keep}
    # 审计 #2 不完整性判定（保守，只报「无法证明精确还原」）：
    # R1 字段完全没撤到但当前变更回合 ≥ 封口回合 → 其在封口范围内的审计条目已被截断；
    # R2 撤到了、但该字段最老被撤条目回合 > 封口回合 → 封口边界与撤销链之间存在断层，
    #    可能有同范围的更早变更已被 _HISTORY_CAP 丢弃。两种都可能有残留，显式上报。
    # 第 58 刀：豁免保留的初次登记是**有意保留**，不算「无法证明精确还原」。
    incomplete = set(oldest_popped) - set(kept_seeds) - rescue_paths
    for kind, fields in (("数值", state.数值), ("叙事", state.叙事)):
        for leaf, val in fields.items():
            path = f"{kind}/{leaf}"
            if path in kept_seeds or path in rescue_paths:
                continue
            if path not in oldest_popped and int(val.turn or 0) >= turn:
                incomplete.add(path)
    for name in list(oldest_popped):
        if oldest_popped[name] <= turn:
            incomplete.discard(name)
    return {"undone": undone, "incomplete": sorted(incomplete)}


def _undo_entry(state: CharacterState, entry: dict) -> int:
    """按审计条目还原单次变更（rollback_last / rollback_from_turn 共用）。
    from 为空/None 表示字段此前不存在 → 直接移除字段（等效清除，不留空壳）。
    还原字段的 turn 盖「还原值所属时代」：取剩余审计里同字段最近一条的回合
    （找不到=更早于审计窗口，盖 0）——供 rollback_from_turn 的不完整性判定，
    不能盖被撤销条目自己的回合（否则幂等复查会误报残留）。"""
    fld = str(entry.get("field") or "")
    if "/" not in fld:
        return 1
    kind, leaf = fld.split("/", 1)
    frm = entry.get("from")
    era = next(
        (int(h.get("turn") or 0) for h in reversed(state.历史)
         if str(h.get("field") or "") == fld),
        0,
    )
    if frm is None or (isinstance(frm, str) and not frm.strip()):
        state.数值.pop(leaf, None) if kind == _KIND_NUM else state.叙事.pop(leaf, None)
        return 1
    if kind == _KIND_NUM:
        nf = state.数值.get(leaf)
        lo, hi = _bounds(leaf, nf)
        state.数值[leaf] = NumericField(_num(frm), lo, hi, era, "", SRC_USER)
    elif kind == _KIND_NARR:
        state.叙事[leaf] = NarrativeField(str(frm), era, "", SRC_USER)
    return 1


def delete_field(state: CharacterState, field_path: str) -> bool:
    """删除一个状态字段（"数值/好感度" 或 "叙事/态度"），并记一条审计（to=None）。返回是否删到。

    人工管理状态表用（浏览器 UI）。删不存在的字段返回 False。
    ⚠ 这是**可撤销**的删除：`_undo_entry` 按审计里的 `from` 把字段写回（等价「撤销删除」），
    所以下一次「重新生成 / 回滚」会让它复现。要**断根**（重生成也拉不回）请用 `purge_field`。
    好感度数值字段会顺带摘掉快照 [好感] 行里的该角色——否则旧数字继续注入，模型照旧走 add。
    """
    consolidate_fields(state)
    if "/" not in (field_path or ""):
        return False
    kind, leaf = field_path.split("/", 1)
    leaf = _canonical_leaf(leaf, state.card_name)
    field_path = f"{kind}/{leaf}"
    turn = current_turn(state)
    before: Any
    if kind == _KIND_NUM and leaf in state.数值:
        before = state.数值.pop(leaf).value
        _log(state, StateDelta(turn, field_path, "delete", None, "", SRC_USER), before, None)
    elif kind == _KIND_NARR and leaf in state.叙事:
        before = state.叙事.pop(leaf).value
        _log(state, StateDelta(turn, field_path, "delete", None, "", SRC_USER), before, None)
    else:
        return False
    if len(state.历史) > _HISTORY_CAP:
        state.历史 = state.历史[-_HISTORY_CAP:]
    if kind == _KIND_NUM and _is_affinity_leaf(leaf):
        drop_snapshot_affinity(state, _owner_of_affinity_leaf(leaf))
    return True


def purge_field(state: CharacterState, field_path: str) -> bool:
    """彻底清除一个状态字段：删字段 **并把该字段的全部审计条一并移除**（断根）。

    与 `delete_field` 的分工（2026-09-23 用户拍板，修「UI 删除 vs 重生成」打架）：
    `delete_field` 会记一条 `op=delete` 审计（`from=旧值, to=None`），而回滚/重生成按审计
    把 `from` 写回 ⇒ 等价于「撤销删除」＝字段复现。这语义本身没错（删除应当可撤销），
    但后果是**任何手工删除都会被下一次「重新生成」拉回来**——重生成链路
    （`ai_agent.py:133` mode=regenerate → `memory_rollback.py` → `rollback_from_turn`）
    正是按审计链还原的。
    本函数不记审计、并删除该字段的历史 ⇒ **审计链里再无该字段可供还原**，重生成与回滚
    都无法恢复它。⛔ 不可撤销：调用方须二次确认（UI 侧）。
    好感度数值字段会顺带从快照 [好感] 行摘掉该角色（否则旧数字继续注入、模型照旧走 add）。
    """
    consolidate_fields(state)
    if "/" not in (field_path or ""):
        return False
    kind, leaf = field_path.split("/", 1)
    leaf = _canonical_leaf(leaf, state.card_name)
    field_path = f"{kind}/{leaf}"
    if kind == _KIND_NUM and leaf in state.数值:
        state.数值.pop(leaf)
    elif kind == _KIND_NARR and leaf in state.叙事:
        state.叙事.pop(leaf)
    else:
        return False
    state.历史 = [h for h in state.历史 if str(h.get("field") or "") != field_path]
    if kind == _KIND_NUM and _is_affinity_leaf(leaf):
        drop_snapshot_affinity(state, _owner_of_affinity_leaf(leaf))
    return True


def from_dict(data: Any, *, repo_id: str, card_name: str) -> CharacterState:
    """从导出的 dict 重建 CharacterState（导入用）。非法/缺字段安全跳过，边界重新钳定。

    repo_id/card_name 以调用方参数为准（导入到当前作品线，不沿用文件里的旧 id）。
    """
    st = CharacterState(card_name=card_name, repo_id=repo_id)
    if not isinstance(data, dict):
        return st
    for leaf, d in (data.get("数值") or {}).items():
        if isinstance(d, dict):
            lo, hi = _num(d.get("min"), -1e9), _num(d.get("max"), 1e9)
            st.数值[str(leaf)] = NumericField(
                max(lo, min(hi, _num(d.get("value")))), lo, hi,
                int(d.get("turn") or 0), str(d.get("evidence") or ""),
                str(d.get("source") or SRC_USER))
    for leaf, d in (data.get("叙事") or {}).items():
        if isinstance(d, dict):
            st.叙事[str(leaf)] = NarrativeField(
                str(d.get("value") or ""), int(d.get("turn") or 0),
                str(d.get("evidence") or ""), str(d.get("source") or SRC_USER))
    hist = data.get("历史")
    if isinstance(hist, list):
        st.历史 = [h for h in hist if isinstance(h, dict)][-_HISTORY_CAP:]
    snap = data.get("快照")
    if isinstance(snap, dict):
        st.快照 = Snapshot(str(snap.get("text") or ""), int(snap.get("turn") or 0))
    return consolidate_fields(st)


def render_state_block(state: CharacterState) -> str:
    """组装【当前状态】注入块（紧凑 kv + 内联 provenance）。空状态返回空串。

    对标设计支柱 3 的召回分区：状态用 kv、内联「(原X,因Y)」证据；往事用散文由别处组装。
    """
    lines: list[str] = []
    for leaf, nf in state.数值.items():
        if _is_protagonist_leaf(leaf):
            continue
        prov = f" ({nf.evidence})" if nf.evidence else ""
        lines.append(f"{state.card_name}·{leaf}: {_fmt_num(nf.value)}{prov}")
    for leaf, sf in state.叙事.items():
        if _is_protagonist_leaf(leaf):
            continue
        prov = f" ({sf.evidence})" if sf.evidence else ""
        lines.append(f"{state.card_name}·{leaf}: {sf.value}{prov}")
    if not lines:
        return ""
    return "【当前状态】\n" + "\n".join(lines)


# ── 待建好感度字段清单（2026-09-23 用户口径）──────────────────────────────
# 播种规则（`state_instruction` / `_DELTA_RULES`）只说「状态表里还没有某角色的好感度
# 字段时必须 set 播种」，但**模型看不到"谁缺字段"**：它只看到【当前状态】块里**已有**
# 的字段 ⇒ 已登场却始终没被建过好感度的角色永远轮不到（真机实锤：苏姑姑在「重要角色
# 表」有行、状态表里有 4 条叙事字段，好感度字段一直不存在，「角色状态表」里她的卡片
# 只有状态没有好感度）。这里把差集确定性算出来，供注入侧点名，播种不再靠模型自觉。

def missing_affinity_owners(
    state: CharacterState, candidates: Iterable[str] = (),
) -> list[str]:
    """候选角色里、状态表中还没有好感度字段的（保持 candidates 顺序、去重）。

    - 已有显式属主 `X·好感度` 的角色不再列入（裸键 `好感度` 是单主角写法，不据此
      认定任何候选已有值，宁缺勿猜）；
    - 主角代词（`_PROTAGONIST_OWNERS`）与空名一律排除；
    - 候选来自调用方（通用表「重要角色表」姓名列）：候选里**状态表完全没有字段**的
      新角色同样列入——规则要求按因果播种，不要求它先在状态表里留过痕迹。
    """
    have = {
        leaf[: -len(_AFFINITY_SUFFIX)].strip()
        for leaf in state.数值
        if leaf.endswith(_AFFINITY_SUFFIX) and leaf[: -len(_AFFINITY_SUFFIX)].strip()
    }
    out: list[str] = []
    for raw in candidates:
        name = str(raw or "").strip()
        if not name or name in have or name in _PROTAGONIST_OWNERS or name in out:
            continue
        out.append(name)
    return out


def render_missing_affinity_block(names: Sequence[str]) -> str:
    """把「待建好感度字段」名单渲染成注入段（空名单 → 空串，调用方据此零成本跳过）。

    只点名与动作，**不重复档位数字**：档位口径的真源是 `state_instruction` 与
    `table_update._DELTA_RULES` 两处，这里多写一份会变成第三处同义文案漂移源。
    """
    picked = [str(n).strip() for n in names if str(n or "").strip()]
    if not picked:
        return ""
    return (
        "【待建好感度字段】下列角色已在数据表中登场、但状态表里还没有好感度字段："
        + "、".join(picked)
        + "。本轮必须为每个角色用 field=\"数值/角色名·好感度\" op=\"set\" 播种「当前应有的好感度」"
        "（按世界观、人设与既有关系定值，禁止从 0 起算、禁止跳过；引擎仅在字段不存在时采纳）。"
    )


def render_snapshot_injection(state: CharacterState) -> str:
    """把上轮 <status> 快照原文重注入主控叙述。空快照返回空串。

    从文件重建（非翻聊天记录），故上下文压缩后状态栏也不丢——AI 据此续写并刷新状态栏。
    注入前先做好感行对账（reconcile_snapshot_affinity）：快照里的数字是模型上轮自述，
    可能与引擎账漂移（2026-09-18 实锤：模型自述 52，引擎 2.0，重注入被照抄 → 漂移固化）。
    """
    text = reconcile_snapshot_affinity(state)
    if not text:
        return ""
    return "【上轮状态栏（据此延续并按本轮剧情更新，格式不变）】\n" + text


# ── 快照好感行机械对账（双账本对齐，2026-09-18 用户实锤）────────────────────
# status 行是模型自述、引擎数值是 delta 累加，两套账独立演化必然漂移；快照每轮
# 重注入/漏战报兜底都会被模型照抄 → 漂移固化。对账＝用 state.数值 真源覆盖行内
# 数字，纯逻辑、幂等、解析不出条目时原样返回（绝不改写其余文本）。

_AFFINITY_LINE_RE = re.compile(
    r"^(?P<head>\[(?:好感|好感度)\])(?P<body>[^\n]*)$", re.MULTILINE)
_AFF_ENTRY_RE = re.compile(
    r"^(?P<name>[^＝=:\n；;]+)[＝=:](?P<num>-?\d+(?:\.\d+)?)(?P<rest>.*)$")
# 删除哨兵：命中「引擎没有、行内有」的条目时把该段标记为 `_DROP_ENTRY`，
# 收尾时连同相邻分隔符一并清掉（见 `_rebuild_affinity_body`）。
_DROP_ENTRY = "\x00DROP\x00"


def _engine_affinities(state: CharacterState) -> dict[str, float]:
    """从 state.数值 提取好感度：`角色·好感度` → {角色: 值}；仅一行裸 `好感度` 时
    归到该唯一行（单主角存档的常见写法，快照行内角色名即归属）。"""
    out: dict[str, float] = {}
    suffix = _AFFINITY_SUFFIX
    for leaf, nf in state.数值.items():
        if leaf.endswith(suffix):
            owner = leaf[:-len(suffix)].strip()
            if owner and not _is_protagonist_leaf(leaf):
                out[owner] = nf.value
    if not out:
        bare = state.数值.get(AFFINITY_FIELD)
        if bare is not None:
            out[""] = bare.value  # 空名 = 无归属，调用方在快照行恰一条目时归属之
    return out


def reconcile_snapshot_affinity(state: CharacterState, text: str = "") -> str:
    """把快照文本里 [好感] 行的数字替换为引擎现值（纯逻辑，幂等）。

    - 行内已有条目：名字能在引擎账里找到 → 数字换成引擎现值，`/档位（描述）` 保留。
    - 引擎有、行内缺的角色 → 追加 `；名字=值`。
    - **引擎没有、行内有的条目 → 从行里删掉该条目**（2026-09-23 修，见下）。
    - 无 [好感] 行 / 行内解析不出任何条目 / 引擎无好感字段 → 原样返回。
    text 为空时取 state.快照.text。其余行（时间/在场/进度…）是叙事文本，不做机械
    对账（无稳定字段名映射），以【当前状态】块注入为真源。

    ⚠ 为什么「引擎没有 ⇒ 删」而不是「原样保留」（2026-09-23 深夜实锤死锁）：
    旧行为是 `engine.get(name) → None → continue`，即原样保留。于是模型在
    `<status>[好感]` 行里自编的数字（真机 `沈映澜=38`、`=28`）被固化下来，而
    `render_missing_affinity_block` 同时注入「她还没有好感度字段、本轮必须 set」
    ⇒ 下一轮 prompt 同屏出现两条互斥信息，模型相信快照行「已经有值」而**永不播种**，
    死锁可无限循环（引擎侧点名成功、`raw_deltas` 恒为空、输出无 `<状态更新>` 段）。
    引擎账才是真源 ⇒ 引擎没有的条目就是幻觉，必须从注入文本里抹掉。
    命中面已量：全库仅 3 条（`谢若冰=0`/`苏姑姑=18`/`沈映澜=38`），全部确为幻觉、零误伤。
    """
    text = (text or state.快照.text or "").strip()
    if not text:
        return ""
    m = _AFFINITY_LINE_RE.search(text)
    if not m:
        return text
    engine = _engine_affinities(state)
    if not engine:
        return text
    anonymous = engine.pop("", None)
    body = m.group("body")
    segments = re.split(r"([；;])", body)
    # split 保留分隔符：奇数位是 `；`，偶数位是条目段
    seen: set[str] = set()
    changed = False
    parsed_any = False
    for i in range(0, len(segments), 2):
        seg = segments[i]
        em = _AFF_ENTRY_RE.match(seg.strip())
        if not em:
            continue
        parsed_any = True
        name = em.group("name").strip(" ·：:（）()")
        if anonymous is not None and name and name not in engine and not seen:
            val: float | None = anonymous
        else:
            val = engine.get(name)
        if val is None:
            # 引擎账里没有这个名字 ⇒ 行内该条目是模型自述产生的幻觉。
            # 抹掉条目本身（连同它前面那个分隔符），避免与「待建好感度字段」
            # 点名块在同一轮 prompt 里自相矛盾（详见证言条：播种死锁）。
            segments[i] = _DROP_ENTRY
            changed = True
            continue
        seen.add(name)
        new_num = _fmt_num(val)
        if new_num != em.group("num"):
            changed = True
        lead = " " if seg.startswith(" ") and i == 0 else ""
        segments[i] = f"{lead}{name}={new_num}{em.group('rest')}"
    if not parsed_any:
        return text
    # 先清掉被标记删除的条目（连同相邻分隔符），再补 missing —— 否则 `segments[-1]`
    # 可能停在哨兵上，`tail` 会被接到一个空段里。
    segments = _rebuild_affinity_body(segments)
    missing = [n for n in engine if n not in seen]
    if missing:
        changed = True
        tail = "；".join(f"{n}={_fmt_num(engine[n])}" for n in missing)
        if segments and segments[-1] == "":
            segments[-1] = tail
        elif segments:
            segments.append("；")
            segments.append(tail)
        else:
            segments.append(tail)
    if not changed:
        return text
    # 行首前导空格可能挂在被删的条目上（如 `[好感] 沈映澜=38；谢若冰=40` 删首条）
    # ⇒ 重组后若无前导空格则补一个，保持 `[好感] 名字=值` 的可读格式。
    body_out = "".join(segments)
    if body_out and not body_out.startswith(" "):
        body_out = " " + body_out
    if not body_out.strip():
        # 行内条目被删空 ⇒ 保留行头本身（`[好感]`），不整行抹除，避免改变快照行数
        # 影响其它按行解析的逻辑；空行内容天然不构成误导。
        body_out = ""
    new_line = m.group("head") + body_out
    return text[:m.start()] + new_line + text[m.end():]


def _rebuild_affinity_body(segments: list[str]) -> list[str]:
    """把带 `_DROP_ENTRY` 哨兵的段列重组为干净段列。

    `segments` 形如 `[条目0, '；', 条目1, '；', ...]`（split 保留分隔符）。规则：
    - 删除段与**紧邻其前**的分隔符（否则会留下 `；；` 或行首 `；`）；
      行首条目被删时改删**其后**的分隔符（无前分隔符可用）。
    - 连续删除时只保留最后一个删除段前面的分隔符判定，避免越删越乱。
    行内全删时返回 `[]`（调用方据此决定是否保留裸行头）。
    """
    out: list[str] = []
    i = 0
    n = len(segments)
    while i < n:
        seg = segments[i]
        if seg == _DROP_ENTRY:
            # 删掉本段；若上一个已落到 out 的是分隔符，一并弹出
            if out and out[-1] in ("；", ";"):
                out.pop()
            elif not out and i + 1 < n and segments[i + 1] in ("；", ";"):
                # 行首条目被删：跳过紧随的分隔符
                i += 1
            i += 1
            continue
        out.append(seg)
        i += 1
    # 收尾：若剩下的是「分隔符开头」或「分隔符结尾」，清掉多余分隔符
    while out and out[0] in ("；", ";"):
        out.pop(0)
    while out and out[-1] in ("；", ";"):
        out.pop()
    # 合并可能出现的连续分隔符
    merged: list[str] = []
    for seg in out:
        if seg in ("；", ";") and merged and merged[-1] in ("；", ";"):
            continue
        merged.append(seg)
    return merged


def _owner_of_affinity_leaf(leaf: str) -> str:
    """`X·好感度` → `X`；裸 `好感度`（单主角写法，无归属）→ 空串（不摘快照条目）。"""
    return leaf[: -len(_AFFINITY_SUFFIX)].strip() if leaf.endswith(_AFFINITY_SUFFIX) else ""


def drop_snapshot_affinity(state: CharacterState, owner: str) -> bool:
    """从快照 [好感] 行移除某角色的条目（删字段的配套动作，纯逻辑、幂等）。返回是否改动。

    为什么必须做：`reconcile_snapshot_affinity` 对「引擎无、行内有」的条目走的是
    `val = engine.get(name)` → None → `continue`，即**原样保留**。所以删掉
    `数值/X·好感度` 之后，快照行里的旧数字会继续注入模型 ⇒ 模型据此认为该角色
    「已登记」，照样发 `add`（真机实锤：手工删字段后仍走 add 累加）。删字段必须把
    这条痕迹一并抹掉，删除才算真生效。
    行内条目全空时保留 `[好感]` 行本身（`<status>` 模板结构不破，交由模型重新播种）。
    """
    owner = (owner or "").strip()
    text = state.快照.text or ""
    if not owner or not text:
        return False
    m = _AFFINITY_LINE_RE.search(text)
    if not m:
        return False
    segments = re.split(r"([；;])", m.group("body"))
    kept: list[str] = []
    changed = False
    for i in range(0, len(segments), 2):
        em = _AFF_ENTRY_RE.match(segments[i].strip())
        if em and em.group("name").strip(" ·：:（）()") == owner:
            changed = True
            continue
        kept.append(segments[i].strip())
    if not changed:
        return False
    body = "；".join(s for s in kept if s)
    new_line = f"{m.group('head')} {body}".rstrip()
    state.快照 = Snapshot(text[:m.start()] + new_line + text[m.end():], state.快照.turn)
    return True


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


# ── I/O：唯一落盘接缝，其余全是纯逻辑 ──

def state_path(base: str, repo_id: str) -> Path:
    return repo_meta.engine_repo_dir(base, repo_id) / STATE_FILE


def load_state(base: str, repo_id: str, card_name: str) -> CharacterState:
    """读某作品某卡的状态；无文件/损坏返回空状态（card_name/repo_id 已填）。"""
    st = CharacterState(card_name=card_name, repo_id=repo_id)
    if not (base and repo_id):
        return st
    p = state_path(base, repo_id)
    if not p.is_file():
        return st
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return st
    if not isinstance(data, dict):
        return st
    for leaf, d in (data.get("数值") or {}).items():
        if isinstance(d, dict):
            st.数值[leaf] = NumericField(
                _num(d.get("value")), _num(d.get("min"), -1e9), _num(d.get("max"), 1e9),
                int(d.get("turn") or 0), str(d.get("evidence") or ""),
                str(d.get("source") or SRC_AUTO))
    for leaf, d in (data.get("叙事") or {}).items():
        if isinstance(d, dict):
            st.叙事[leaf] = NarrativeField(
                str(d.get("value") or ""), int(d.get("turn") or 0),
                str(d.get("evidence") or ""), str(d.get("source") or SRC_AUTO))
    hist = data.get("历史")
    if isinstance(hist, list):
        st.历史 = [h for h in hist if isinstance(h, dict)][-_HISTORY_CAP:]
    snap = data.get("快照")
    if isinstance(snap, dict):
        st.快照 = Snapshot(str(snap.get("text") or ""), int(snap.get("turn") or 0))
    return consolidate_fields(st)


def save_state(base: str, state: CharacterState) -> None:
    """把状态写入 <base>/<repo_id>/state.json。base/repo_id 为空则跳过。"""
    if not (base and state.repo_id):
        return
    p = state_path(base, state.repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
