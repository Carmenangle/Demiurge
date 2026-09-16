"""全局正则脚本持久化：读写 data/regex_scripts.json。

全局正则 = 跨作品生效的一组 ST 格式正则脚本（camelCase 原样存，喂 regex_engine 前归一）。
区别于卡内嵌 regex.json（随卡、只该卡生效）。两者都由 agent_graph/前端合并后喂引擎。

落盘模式交 `<json_file_store>` 统一：缺失/损坏返回空列表，不抛。
纯读写，不解析/不跑正则（那是 regex_engine 的事）。
"""
from __future__ import annotations

from pathlib import Path

from app.config import DATA_DIR
from app.services import json_file_store


def _path() -> Path:
    return DATA_DIR / "regex_scripts.json"


def load_scripts() -> list[dict]:
    return json_file_store.read_list(_path(), errors=json_file_store.ERRORS_STANDARD)


def save_scripts(scripts: list[dict]) -> list[dict]:
    """整体覆盖写，为缺 id 的项补 id。返回落盘后的列表。"""
    return json_file_store.write_list(_path(), scripts)
