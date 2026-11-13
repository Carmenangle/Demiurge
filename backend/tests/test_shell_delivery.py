"""壳补齐进度账本的读侧合同（2026-11-03 P3-b）。

`delivery.json` 是壳与后端之间**第一条跨进程通道**：写入方是桌面壳
（`desktop/tauri/src/runtime_delivery.rs` 的 `DeliveryLedger`），读取方只有 `shell_delivery`。
两侧不在同一份测试里跑，所以最容易坏的是「文件名对不上」和「陈旧口径各自演化」——
本文件把这两条钉住，外加三条读侧纪律（只读、僵尸不算在跑、坏档不打扰面板）。

跑：`cd backend && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_shell_delivery.py -q`
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app import data_layout
from app.services import shell_delivery

ROOT = Path(__file__).resolve().parents[2]
SHELL_DELIVERY = ROOT / "desktop" / "tauri" / "src" / "runtime_delivery.rs"


@pytest.fixture
def home_data(monkeypatch, tmp_path: Path) -> Path:
    """把 `DATA_DIR` 指到 tmp：本文件的用例都要造真文件，绝不能碰真实运行态。"""
    monkeypatch.setattr(shell_delivery, "DATA_DIR", tmp_path)
    return tmp_path


def _write(home: Path, value: object) -> Path:
    path = home / shell_delivery.LEDGER_NAME
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


def _ledger(now: float, **overrides) -> dict:
    value = {
        "schema_version": 1, "layer": "rag", "label": "RAG 增强层", "state": "running",
        "stage": "下载分件", "done_bytes": 5242880, "total_bytes": 10485760,
        "speed_bps": 2097152, "started_at": now - 8, "updated_at": now,
        "shell_pid": 4321, "error": "",
    }
    value.update(overrides)
    return value


def test_文件名与档位两侧一致(home_data: Path):
    """跨进程通道的第一条：两侧点名的文件必须是同一个，且它在卸载分档里是缓存。

    壳那边改名字（或后端这边改名）⇒ 面板从此永远看不到补齐进度，而两侧单测各自都还是绿的。
    """
    source = SHELL_DELIVERY.read_text(encoding="utf-8")
    assert f'const LEDGER_NAME: &str = "{shell_delivery.LEDGER_NAME}";' in source

    tier = data_layout._USERDATA[shell_delivery.LEDGER_NAME]
    assert tier == (data_layout.CACHE, data_layout.FILE), "进度账本只能是缓存：它不含用户内容"
    assert shell_delivery.ledger_path() == home_data / shell_delivery.LEDGER_NAME


def test_没有账本时安静返回空(home_data: Path):
    assert shell_delivery.read_ledger() is None


@pytest.mark.parametrize("broken", ["", "{not json", "[]", '{"state": "sleeping"}', "{}"])
def test_坏档与未知状态都不抛异常(home_data: Path, broken: str):
    """账本只是可观测性：写坏它不该把 `/api/activity/background` 带崩。"""
    _write(home_data, broken)
    assert shell_delivery.read_ledger() is None


def test_新鲜运行中账本按字段读出(home_data: Path):
    now = time.time()
    _write(home_data, _ledger(now))

    value = shell_delivery.read_ledger(now)
    assert value is not None
    assert value["layer"] == "rag" and value["label"] == "RAG 增强层"
    assert value["state"] == "running" and value["stage"] == "下载分件"
    assert value["done_bytes"] == 5242880 and value["total_bytes"] == 10485760
    assert value["speed_bps"] == 2097152


def test_运行中超过陈旧窗口就不再显示(home_data: Path):
    """壳崩掉时没人把 `running` 改成终态：陈旧判据放在读侧，写侧不需要「收掉上次的僵尸账」。"""
    now = time.time()
    _write(home_data, _ledger(now - 60, updated_at=now - shell_delivery.RUNNING_STALE_S - 1))
    assert shell_delivery.read_ledger(now) is None

    _write(home_data, _ledger(now, updated_at=now - shell_delivery.RUNNING_STALE_S + 5))
    assert shell_delivery.read_ledger(now) is not None


def test_终态不受陈旧窗口约束(home_data: Path):
    """保留期是面板的事（`activity_feed._KEEP_DONE_S`）：读侧不能把刚成功的补齐吞掉。"""
    now = time.time()
    old = now - 3600
    for state in ("done", "failed"):
        _write(home_data, _ledger(now, state=state, started_at=old, updated_at=old))
        value = shell_delivery.read_ledger(now)
        assert value is not None and value["state"] == state


def test_缺字段与脏数字宽容取值(home_data: Path):
    """壳字段会随版本增减（`schema_version`）：少一个键不能让整条投影消失。"""
    now = time.time()
    _write(home_data, {"state": "running", "updated_at": now, "done_bytes": "abc",
                       "total_bytes": None, "speed_bps": True})

    value = shell_delivery.read_ledger(now)
    assert value is not None
    assert value["layer"] == "" and value["label"] == "" and value["error"] == ""
    assert value["done_bytes"] == 0 and value["total_bytes"] == 0 and value["speed_bps"] == 0
    assert value["started_at"] == pytest.approx(now)


def test_读侧不写盘(home_data: Path):
    """只读投影源：本模块任何路径都不得创建/修改账本文件（唯一写入方是壳）。"""
    _write(home_data, _ledger(time.time(), state="done"))
    before = (home_data / shell_delivery.LEDGER_NAME).read_bytes()

    shell_delivery.read_ledger()
    shell_delivery.read_ledger(time.time() + 10_000)

    assert (home_data / shell_delivery.LEDGER_NAME).read_bytes() == before
