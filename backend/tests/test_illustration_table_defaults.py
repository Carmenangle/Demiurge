# -*- coding: utf-8 -*-
"""第 26 刀：数据表（**上一轮**参数）作为插画兜底的「缺口填充」——只补空缺，剧情/状态优先。

用户口径（2026-09-20 22:28）：「先保证那些数据表里面的参数能被利用，但别忘记了表格本质是上轮的
旧参数，所以说如果新生成数据有这个内容，以新的为准……我其实建议是以剧情生成中提取为准，
如衣服战损、被撕破之类的」。

因此本文件锁两条：
- 状态表/快照都取不到时，地点/穿着从 `全局数据表·地点`、`主角信息表·穿着打扮` 补；
- 状态表（或本轮正文）**已经有值的时候，表格旧值一个字都不能盖上去**。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import agent_agency, table_store


# ── 夹具 ────────────────────────────────────────────────────────────────────

def _write_tables(base: str, repo_id: str, *, locale: str = "", wardrobe: str = "") -> None:
    """把数据表写进 `engine_repo_dir(base, repo_id)/tables.json`。"""
    tables = table_store.default_tables()
    ops: list[dict[str, object]] = []
    if locale:
        ops.append({"op": "update", "table": table_store.GLOBAL_TABLE, "values": {"地点": locale}})
    if wardrobe:
        ops.append({"op": "update", "table": table_store.PROTAGONIST_TABLE,
                    "values": {"穿着打扮": wardrobe}})
    table_store.apply_ops(tables, ops)
    table_store.save(base, repo_id, tables)


class _SF:
    """叙事字段桩：`roleplay_agency._narr` 只读 `.value`；快照读 `.text`。"""

    def __init__(self, value: str = ""):
        self.value = value
        self.text = value


class _St:
    def __init__(self, narr: dict[str, str] | None = None, snapshot: str = ""):
        self.叙事 = {key: _SF(value) for key, value in (narr or {}).items()}
        self.快照 = _SF(snapshot)


def _signals(base: str, *, narr: dict[str, str] | None = None) -> dict:
    """跑真实接缝 `_wb_scene_signals`（只读，无副作用）。"""
    return agent_agency._wb_scene_signals(
        {"output_dir": base, "repo_id": "home", "comfy_illustrate": False, "history": []},
        SimpleNamespace(renderer=None),
        _St(narr),
        "她跪在床边，把双手贴在臀肉上掰开屁股。",
        "",
        "nsfw",
        "",
        None,
        False,
    )


# ── illustration_defaults：取值口径 ────────────────────────────────────────

def test_illustration_defaults_取全局地点与主角穿着打扮(tmp_path):
    base = str(tmp_path / "works")
    _write_tables(base, "home", locale="寝殿", wardrobe="damp white robe")

    assert table_store.illustration_defaults(base, "home") == ("寝殿", "damp white robe")


def test_illustration_defaults_无表时返回空串而不抛(tmp_path):
    base = str(tmp_path / "works")

    assert table_store.illustration_defaults(base, "nobody") == ("", "")


def test_illustration_defaults_单卡空行时返回空串(tmp_path):
    """表在、列在，但这张卡还是空行 ⇒ 空串（不得拿别的行凑）。"""
    base = str(tmp_path / "works")
    _write_tables(base, "home")  # 不填任何值

    assert table_store.illustration_defaults(base, "home") == ("", "")


# ── 接线：只补空缺，绝不覆盖 ───────────────────────────────────────────────

def test_场景信号_状态表为空时用数据表旧值补缺(tmp_path):
    base = str(tmp_path / "works")
    _write_tables(base, "home", locale="寝殿", wardrobe="damp white robe")

    sig = _signals(base)

    assert sig["locale"] == "寝殿"
    assert sig["wardrobe"] == "damp white robe"


def test_场景信号_状态表已有值时数据表旧值不得覆盖(tmp_path):
    """**剧情/状态优先**：衣服战损、被撕破这类动态必须按本轮为准，表格只是上一轮的旧值。"""
    base = str(tmp_path / "works")
    _write_tables(base, "home", locale="寝殿", wardrobe="pristine white robe")

    sig = _signals(base, narr={"所在": "地牢", "衣着": "torn, battle-damaged robe"})

    assert sig["locale"] == "地牢", "状态表已有地点时不得被数据表旧值覆盖"
    assert sig["wardrobe"] == "torn, battle-damaged robe", "本轮衣着（战损）必须盖过表格旧值"


def test_场景信号_只有缺的那一项才补(tmp_path):
    """一半有一半没有：只补空缺那一半。"""
    base = str(tmp_path / "works")
    _write_tables(base, "home", locale="寝殿", wardrobe="damp white robe")

    sig = _signals(base, narr={"衣着": "torn robe"})

    assert sig["wardrobe"] == "torn robe"
    assert sig["locale"] == "寝殿", "地点仍空缺 ⇒ 用表里的旧值补"
