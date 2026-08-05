"""角色动态状态单一属主：按 repo_id 存「可变状态」，core 不在这里。

设计见 ARCHITECTURE.md「剧情能动性引擎」支柱 1。分工：
- core（人设根基/外观/死穴/机制/弧线）写在卡文件（character_store 的 card.json），**永不由本模块自动更**。
- state（好感度数值 + 态度/心情/所在等叙事字段）按 repo_id 作用域、随剧情走，本模块拥有。

每次更新 = 一条带证据的 StateDelta（from→to + 证据 + turn + source）。AI 召回读到
「态度: 戒备 (因第3章救援)」= 角色发展而非矛盾；人为改若不带证据 → source=user,证据空，
供上层识别为「设定注入」而非剧情。

落盘：<base>/<safe repo_id>/state.json，物理隔离不同游玩。base 由调用方注入，不读 config。
纯逻辑（parse_deltas/apply_deltas/render_state_block）无 I/O，可单测；load/save 是唯一 I/O。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.pathnames import safe_seg

STATE_FILE = "state.json"
SRC_AUTO = "auto"
SRC_USER = "user"
_KIND_NUM = "数值"
_KIND_NARR = "叙事"
_HISTORY_CAP = 200  # 审计历史封顶，防无限膨胀（超出丢最旧）

AFFINITY_FIELD = "好感度"       # 好感度字段名（本模块单一属主，roleplay_agency 引用）
_NUM_UNBOUNDED = 1e9
# 已知数值字段的默认边界：好感度是 -100(厌恶)..100(喜爱) 连续标量，不搞卡内档位那套。
# 首次由 delta/set 创建且未显式带 min/max 时用此表；未登记字段仍无界（±1e9）。
_FIELD_BOUNDS: dict[str, tuple[float, float]] = {AFFINITY_FIELD: (-100.0, 100.0)}
_STATE_LABELS = ("身体状态", "精神状态", "生理状态", "心理状态", "外观状态", "伤势状态", "衣着状态")


def _canonical_leaf(
    leaf: str, card_name: str, owner_hints: dict[str, str] | None = None,
) -> str:
    """把同一角色字段的中点、旧拼接和无归属单角色写法归为一个稳定键。"""
    value = (leaf or "").strip()
    separated = re.match(r"^(.+?)[·：:](.+)$", value)
    if separated:
        return f"{separated.group(1).strip()}·{separated.group(2).strip()}"
    if value in _STATE_LABELS:
        owner = (owner_hints or {}).get(value) or card_name
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
    op: str            # add（数值）| set（叙事）
    value: Any         # add: 数值增量；set: 新字符串值
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
        fld = f"{kind}/{_canonical_leaf(leaf, card_name, hints)}"
        op = str(item.get("op") or "").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        if kind == _KIND_NUM:
            if op != "add":
                continue
            out.append(StateDelta(turn, fld, "add", _num(item.get("value")), evidence, source))
        elif kind == _KIND_NARR:
            if op != "set":
                continue
            val = str(item.get("value") or "").strip()
            if not val:
                continue
            out.append(StateDelta(turn, fld, "set", val, evidence, source))
    return out


def apply_deltas(state: CharacterState, deltas: list[StateDelta]) -> CharacterState:
    """按序应用 delta，原地更新 state 并逐条追加审计历史（含 from→to）。返回同一对象。

    数值字段不存在时以 0 起算并沿用默认边界；叙事字段不存在时直接创建。
    """
    consolidate_fields(state)
    for d in deltas:
        leaf = d.leaf()
        if d.kind() == _KIND_NUM:
            nf = state.数值.get(leaf)
            before_n = nf.value if nf else 0.0
            lo, hi = _bounds(leaf, nf)
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
        entry = state.历史.pop()
        fld = str(entry.get("field") or "")
        if "/" not in fld:
            undone += 1
            continue
        kind, leaf = fld.split("/", 1)
        frm = entry.get("from")
        t = int(entry.get("turn") or 0)
        if kind == _KIND_NUM:
            nf = state.数值.get(leaf)
            lo, hi = _bounds(leaf, nf)
            state.数值[leaf] = NumericField(_num(frm), lo, hi, t, "", SRC_USER)
        elif kind == _KIND_NARR:
            state.叙事[leaf] = NarrativeField(str(frm or ""), t, "", SRC_USER)
        undone += 1
    return undone


def delete_field(state: CharacterState, field_path: str) -> bool:
    """删除一个状态字段（"数值/好感度" 或 "叙事/态度"），并记一条审计（to=None）。返回是否删到。

    人工管理状态表用（浏览器 UI）。删不存在的字段返回 False。
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
        prov = f" ({nf.evidence})" if nf.evidence else ""
        lines.append(f"{state.card_name}·{leaf}: {_fmt_num(nf.value)}{prov}")
    for leaf, sf in state.叙事.items():
        prov = f" ({sf.evidence})" if sf.evidence else ""
        lines.append(f"{state.card_name}·{leaf}: {sf.value}{prov}")
    if not lines:
        return ""
    return "【当前状态】\n" + "\n".join(lines)


def render_snapshot_injection(state: CharacterState) -> str:
    """把上轮 <status> 快照原文重注入主控叙述。空快照返回空串。

    从文件重建（非翻聊天记录），故上下文压缩后状态栏也不丢——AI 据此续写并刷新状态栏。
    """
    text = (state.快照.text or "").strip()
    if not text:
        return ""
    return "【上轮状态栏（据此延续并按本轮剧情更新，格式不变）】\n" + text


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


# ── I/O：唯一落盘接缝，其余全是纯逻辑 ──

def state_path(base: str, repo_id: str) -> Path:
    return Path(base) / safe_seg(repo_id, strip=False) / STATE_FILE


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
