"""P9 特征测试：7 处 JSON 小仓库的读写行为锁（**先于实现写**）。

只用公开 API 描述当前行为；实现换成共用 `<json_file_store>` 原语后，本文件必须逐条仍绿。

三节：
- §1 列表型四仓库（mcp / regex / agent / skills）：缺文件 / 损坏 / 顶层非列表 → `[]`，
  `save_*` 补 id + 丢弃非 dict + 整体覆盖写；`get_*` 按 id 命中。
- §2 dict 型三处（builtin_agents / palette_pref / prose_style）：缺文件 / 损坏 / 顶层非 dict → 空 dict。
- §3 **容错面差异锁（刻意，不得抹平）**：7 处的 `except` 元组并不一致 ——
  列表型与 `builtin_agents` 收尾是 `TypeError`（**不含** `OSError`），
  `palette_pref` / `prose_style` 收尾是 `OSError`。
  P9 明确**不**把它们并成全集：并集会把「文件被占用 / 权限不足」（`OSError`）从
  **裸异常 500** 静默成**空结果**，属于行为改动（硬约束 §2「不改判定逻辑」）。
  谁顺手统一谁红。

⚠ 隔离方式：一律 `monkeypatch.setattr(<模块>, "DATA_DIR", tmp_path)`，
即钉住「`_path()` 在**调用时**读模块全局 `DATA_DIR`」这条既有契约
（`tests/test_builtin_agents.py:15` / `tests/test_conftest_isolation.py:47` 也依赖它）。
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from app.services import (
    agent_store,
    builtin_agents,
    json_file_store,
    mcp_store,
    palette_pref,
    prose_style,
    regex_store,
    skills_store,
)

# ── §1 列表型四仓库表：(模块, 文件名, load, save, get | None) ────────────────
LIST_PARAMS = "module,filename,load_name,save_name,get_name"
LIST_STORES = [
    pytest.param(mcp_store, "mcp_config.json", "load_servers", "save_servers", "get_server", id="mcp"),
    pytest.param(regex_store, "regex_scripts.json", "load_scripts", "save_scripts", None, id="regex"),
    pytest.param(agent_store, "agents.json", "load_agents", "save_agents", "get_agent", id="agent"),
    pytest.param(skills_store, "skills.json", "load_skills", "save_skills", None, id="skills"),
]

# ── §3 读路径表：(模块, 需 patch 的取路径函数, 调用的读函数, 故障时期望值, 是否吞 OSError)
READ_PARAMS = "module,patch_attr,call_name,expected,swallows_oserror"
READ_TARGETS = [
    pytest.param(mcp_store, "_path", "load_servers", [], False, id="mcp"),
    pytest.param(regex_store, "_path", "load_scripts", [], False, id="regex"),
    pytest.param(agent_store, "_path", "load_agents", [], False, id="agent"),
    pytest.param(skills_store, "_path", "load_skills", [], False, id="skills"),
    pytest.param(builtin_agents, "_path", "load_overrides", {}, False, id="builtin_agents"),
    pytest.param(palette_pref, "_path", "_read_all", {}, True, id="palette_pref"),
    pytest.param(prose_style, "_config_path", "_read_config_file", {}, True, id="prose_style"),
]


class _Unreadable:
    """假 Path：`is_file()` 为真、`read_text()` 抛 OSError（模拟文件被占用 / 权限不足）。"""

    def is_file(self) -> bool:
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        raise OSError("模拟：文件被占用")


class _Boom:
    """假 Path：`is_file()` 为真、`read_text()` 抛 UnicodeDecodeError（非 UTF-8 文件）。"""

    def is_file(self) -> bool:
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


def _redirect(monkeypatch, module, tmp_path) -> None:
    """把仓库落盘目录指到 tmp（钉「调用时读模块全局 DATA_DIR」）。"""
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)


# ── §1 列表型四仓库 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(LIST_PARAMS, LIST_STORES)
def test_缺文件返回空列表(monkeypatch, tmp_path, module, filename, load_name, save_name, get_name):
    _redirect(monkeypatch, module, tmp_path)
    assert module._path() == tmp_path / filename
    assert getattr(module, load_name)() == []


@pytest.mark.parametrize(LIST_PARAMS, LIST_STORES)
def test_损坏JSON返回空列表(monkeypatch, tmp_path, module, filename, load_name, save_name, get_name):
    _redirect(monkeypatch, module, tmp_path)
    (tmp_path / filename).write_text("{ not json", encoding="utf-8")
    assert getattr(module, load_name)() == []


@pytest.mark.parametrize(LIST_PARAMS, LIST_STORES)
def test_顶层非列表返回空列表(monkeypatch, tmp_path, module, filename, load_name, save_name, get_name):
    """顶层是 dict / JSON null / 数字 / 字符串 ⇒ 一律 []（**不**抛、**不**强转）。"""
    _redirect(monkeypatch, module, tmp_path)
    path = tmp_path / filename
    for payload in ("{}", "null", "3", '"str"'):
        path.write_text(payload, encoding="utf-8")
        assert getattr(module, load_name)() == [], payload


@pytest.mark.parametrize(LIST_PARAMS, LIST_STORES)
def test_save补id且保留已有id(monkeypatch, tmp_path, module, filename, load_name, save_name, get_name):
    _redirect(monkeypatch, module, tmp_path)
    saved = getattr(module, save_name)([{"name": "无 id"}, {"id": "keep-me", "name": "有 id"}])
    assert len(saved[0]["id"]) == 32 and all(c in "0123456789abcdef" for c in saved[0]["id"])
    assert saved[1]["id"] == "keep-me"
    # 落盘后再读一次：id 稳定（不会二次补）
    assert [item["id"] for item in getattr(module, load_name)()] == [item["id"] for item in saved]


@pytest.mark.parametrize(LIST_PARAMS, LIST_STORES)
def test_save丢弃非dict项且不就地改入参(monkeypatch, tmp_path, module, filename, load_name, save_name, get_name):
    _redirect(monkeypatch, module, tmp_path)
    first = {"name": "无 id"}
    source = [first, "not-a-dict", 3, None, {"id": "b"}]
    saved = getattr(module, save_name)(source)
    assert len(saved) == 2
    assert saved[0]["name"] == "无 id" and saved[0]["id"] != ""
    assert saved[1]["id"] == "b"
    # 调用方原列表里的 dict **不得**被就地补 id（实现走 `dict(s)` 拷贝）
    assert first == {"name": "无 id"}

    assert getattr(module, save_name)(None) == []
    assert getattr(module, save_name)([]) == []


@pytest.mark.parametrize(LIST_PARAMS, LIST_STORES)
def test_save落盘为utf8原文且indent2(monkeypatch, tmp_path, module, filename, load_name, save_name, get_name):
    _redirect(monkeypatch, module, tmp_path)
    saved = getattr(module, save_name)([{"id": "x", "name": "生图工具"}])
    raw = (tmp_path / filename).read_text(encoding="utf-8")
    assert raw == json.dumps(saved, ensure_ascii=False, indent=2)
    assert "生图工具" in raw                      # ensure_ascii=False：中文不转义


@pytest.mark.parametrize(LIST_PARAMS, LIST_STORES)
def test_save自动建目录(monkeypatch, tmp_path, module, filename, load_name, save_name, get_name):
    """DATA_DIR 不存在时 save 自行 mkdir(parents=True) —— 与 `DATA_DIR.mkdir` 同效。"""
    target = tmp_path / "deep" / "data"
    _redirect(monkeypatch, module, target)
    assert not target.exists()
    getattr(module, save_name)([{"id": "a"}])
    assert (target / filename).is_file()


def test_get按id命中(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_store, "DATA_DIR", tmp_path)
    mcp_store.save_servers([{"id": "s1", "name": "一"}, {"id": "s2", "name": "二"}])
    assert mcp_store.get_server("s2") == {"id": "s2", "name": "二"}
    assert mcp_store.get_server("nope") is None
    assert mcp_store.get_server("") is None


def test_agent_store的get对空id短路(monkeypatch, tmp_path):
    """`agent_store.get_agent` 有 `if not agent_id` 前置短路 ⇒ 手写文件里 id 为空串也不误命中。"""
    monkeypatch.setattr(agent_store, "DATA_DIR", tmp_path)
    (tmp_path / "agents.json").write_text('[{"id": "", "name": "脏数据"}]', encoding="utf-8")
    assert agent_store.get_agent("") is None
    assert agent_store.get_agent("other") is None


def test_enabled_servers按开关过滤(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_store, "DATA_DIR", tmp_path)
    mcp_store.save_servers([{"id": "a", "enabled": True}, {"id": "b", "enabled": False}])
    assert [s["id"] for s in mcp_store.enabled_servers()] == ["a"]


# ── §2 dict 型三处 ──────────────────────────────────────────────────────────


def test_builtin_agents覆盖读法(monkeypatch, tmp_path):
    _redirect(monkeypatch, builtin_agents, tmp_path)
    assert builtin_agents._path() == tmp_path / "builtin_agents.json"
    path = tmp_path / "builtin_agents.json"
    assert builtin_agents.load_overrides() == {}
    path.write_text("{ not json", encoding="utf-8")
    assert builtin_agents.load_overrides() == {}
    path.write_text('["a"]', encoding="utf-8")
    assert builtin_agents.load_overrides() == {}


def test_palette_pref读法(monkeypatch, tmp_path):
    _redirect(monkeypatch, palette_pref, tmp_path)
    assert palette_pref._path() == tmp_path / "palette_pref.json"
    path = tmp_path / "palette_pref.json"
    assert palette_pref.load("r1") == {"colors": [], "name": ""}
    path.write_text("{ not json", encoding="utf-8")
    assert palette_pref.load("r1") == {"colors": [], "name": ""}
    path.write_text('["a"]', encoding="utf-8")
    assert palette_pref.load("r1") == {"colors": [], "name": ""}


def test_prose_style配置读法(monkeypatch, tmp_path):
    _redirect(monkeypatch, prose_style, tmp_path)
    assert prose_style._config_path() == tmp_path / "prose_style.json"
    default = {"enabled": True, "extra": [], "removed": [], "review_every": 5}
    path = tmp_path / "prose_style.json"
    assert prose_style.load_config() == default
    path.write_text("{ broken", encoding="utf-8")
    assert prose_style.load_config() == default
    path.write_text('["a"]', encoding="utf-8")
    assert prose_style.load_config() == default


# ── §3 容错面差异锁（刻意保留，不得并成全集）──────────────────────────────


@pytest.mark.parametrize(READ_PARAMS, READ_TARGETS)
def test_非UTF8文件一律退默认值(
    monkeypatch, module, patch_attr, call_name, expected, swallows_oserror,
):
    """`UnicodeDecodeError` 在 7 处**全部**被吞 ⇒ 非 UTF-8 文件退默认，不抛。"""
    monkeypatch.setattr(module, patch_attr, lambda: _Boom())
    assert getattr(module, call_name)() == expected


@pytest.mark.parametrize(READ_PARAMS, READ_TARGETS)
def test_读盘OSError按各模块原容错面走(
    monkeypatch, module, patch_attr, call_name, expected, swallows_oserror,
):
    """- `swallows_oserror=False`（5 处收尾 `TypeError`）：`OSError` **必须继续外抛**；
    - `swallows_oserror=True`（palette_pref / prose_style 收尾 `OSError`）：**必须**吞成默认值。

    ⚠ 这条锁的就是「不许并成全集」：并集会让前 5 处静默吞掉读盘失败（500 → 空结果）。
    """
    monkeypatch.setattr(module, patch_attr, lambda: _Unreadable())
    if swallows_oserror:
        assert getattr(module, call_name)() == expected
    else:
        with pytest.raises(OSError):
            getattr(module, call_name)()


def test_OSError吞与不吞在公开面上可见(monkeypatch):
    """公开面复核：palette_pref.load / prose_style.load_config 读盘失败退默认（不 500）。"""
    monkeypatch.setattr(palette_pref, "_path", lambda: _Unreadable())
    assert palette_pref.load("r1") == {"colors": [], "name": ""}

    monkeypatch.setattr(prose_style, "_config_path", lambda: _Unreadable())
    assert prose_style.load_config() == {
        "enabled": True, "extra": [], "removed": [], "review_every": 5,
    }


# ── §4 原语本身 + 反回退源码锁 ──────────────────────────────────────────────

# 7 处读路径：(模块, 读函数名)
READ_FUNCS = [
    pytest.param(mcp_store, "load_servers", id="mcp"),
    pytest.param(regex_store, "load_scripts", id="regex"),
    pytest.param(agent_store, "load_agents", id="agent"),
    pytest.param(skills_store, "load_skills", id="skills"),
    pytest.param(builtin_agents, "load_overrides", id="builtin_agents"),
    pytest.param(palette_pref, "_read_all", id="palette_pref"),
    pytest.param(prose_style, "_read_config_file", id="prose_style"),
]

# 4 处列表型写路径：(模块, 写函数名)
WRITE_FUNCS = [
    pytest.param(mcp_store, "save_servers", id="mcp"),
    pytest.param(regex_store, "save_scripts", id="regex"),
    pytest.param(agent_store, "save_agents", id="agent"),
    pytest.param(skills_store, "save_skills", id="skills"),
    # 清单外同族 1 处：它是 load_overrides 的配对写侧，不收等于同模块内仍留着落盘格式副本
    pytest.param(builtin_agents, "save_overrides", id="builtin_agents"),
]

SEVEN_MODULES = [
    pytest.param(mcp_store, id="mcp"),
    pytest.param(regex_store, id="regex"),
    pytest.param(agent_store, id="agent"),
    pytest.param(skills_store, id="skills"),
    pytest.param(builtin_agents, id="builtin_agents"),
    pytest.param(palette_pref, id="palette_pref"),
    pytest.param(prose_style, id="prose_style"),
]


def _func_ast(module, name):
    """取模块里某个顶层函数的 AST 节点（**不看 docstring**，只按语法结构断言）。"""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{module.__name__} 里找不到顶层函数 {name}")


def _dotted(node) -> str:
    """把 `a.b.c` / `name` 形式的调用目标拍平成点号串。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _calls(node, dotted: str) -> list:
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call) and _dotted(n.func) == dotted
    ]


def test_原语容错面常量内容钉死():
    """两个预设就是 7 处历史容错面的原文；**并成全集（把 OSError 塞进 STANDARD）即红**。"""
    assert json_file_store.ERRORS_STANDARD == (
        json.JSONDecodeError, UnicodeDecodeError, TypeError,
    )
    assert json_file_store.ERRORS_WITH_OS == (
        json.JSONDecodeError, UnicodeDecodeError, OSError,
    )
    assert OSError not in json_file_store.ERRORS_STANDARD


def test_原语errors没有默认值():
    """刻意不给默认值：给了默认就等于替调用方选容错面（必然对某一侧变成行为改动）。"""
    for fn in (json_file_store.read_json, json_file_store.read_list, json_file_store.read_dict):
        param = inspect.signature(fn).parameters["errors"]
        assert param.default is inspect.Parameter.empty, fn.__name__
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__


def test_原语落盘格式单点属主():
    """`ensure_ascii=False / indent=2` 的唯一属主；改这里等于改 7 处落盘格式。"""
    assert json_file_store._JSON_DUMP_ARGS == {"ensure_ascii": False, "indent": 2}


def test_原语read_json缺失与损坏都返回None(tmp_path):
    path = tmp_path / "x.json"
    assert json_file_store.read_json(path, errors=json_file_store.ERRORS_STANDARD) is None
    path.write_text("{", encoding="utf-8")
    assert json_file_store.read_json(path, errors=json_file_store.ERRORS_STANDARD) is None
    path.write_text('{"a": 1}', encoding="utf-8")
    assert json_file_store.read_json(path, errors=json_file_store.ERRORS_STANDARD) == {"a": 1}


def test_原语normalize_items不就地改入参且丢非dict():
    original = {"name": "无 id"}
    source = [original, "x", 1, None, {"id": "keep"}]
    out = json_file_store.normalize_items(source)
    assert len(out) == 2
    assert out[1] == {"id": "keep"}
    assert len(out[0]["id"]) == 32
    assert original == {"name": "无 id"}          # 未就地补 id
    assert json_file_store.normalize_items(None) == []


def test_原语write_list返回落盘后的列表(tmp_path):
    path = tmp_path / "sub" / "x.json"
    saved = json_file_store.write_list(path, [{"name": "无 id"}])
    assert json.loads(path.read_text(encoding="utf-8")) == saved


def test_原语find_by_id与内联写法逐字同形():
    """刻意**不加** isinstance 守卫：命中即返回；未命中时一路走到脏项，与内联 `for` 同样炸。"""
    assert json_file_store.find_by_id([{"id": "a"}, "脏数据"], "a") == {"id": "a"}
    with pytest.raises(AttributeError):
        json_file_store.find_by_id([{"id": "a"}, "脏数据"], "zzz")


@pytest.mark.parametrize("module", SEVEN_MODULES)
def test_七处都必须走原语(module):
    """接线锁：7 个模块都必须 `from app.services import json_file_store`。"""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "app.services"
        and any(alias.name == "json_file_store" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert found, f"{module.__name__} 未 import json_file_store"


@pytest.mark.parametrize("module,name", READ_FUNCS)
def test_读函数不得再自己解析JSON或写try兜底(module, name):
    """反回退锁：读路径必须只剩一次原语调用 —— 不得再出现 `json.loads` / `try`。

    ⚠ 用 AST 断言而非裸子串：docstring 里正当提及 `json.loads`/`except` 不算违规
    （P8 踩过：裸子串锁会被自己的历史说明判红）。
    """
    node = _func_ast(module, name)
    assert _calls(node, "json.loads") == [], "读函数又自己 json.loads 了"
    assert [n for n in ast.walk(node) if isinstance(n, ast.Try)] == [], "读函数又自己写 try 兜底了"


@pytest.mark.parametrize("module,name", WRITE_FUNCS)
def test_写函数不得再自己补id或落盘(module, name):
    """反回退锁：列表型写路径只剩一次 `write_list` —— 不得再出现 `uuid4` / `json.dumps` / `write_text`。"""
    node = _func_ast(module, name)
    assert _calls(node, "uuid4") == [], "写函数又自己补 id 了"
    assert _calls(node, "json.dumps") == [], "写函数又自己 dumps 了"
    assert _calls(node, "write_text") == [], "写函数又自己落盘了"
    assert _calls(node, "mkdir") == [], "写函数又自己 mkdir 了"
