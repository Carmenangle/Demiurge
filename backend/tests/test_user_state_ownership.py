"""user_state.json 读取收口锁（2026-10-24 审计改进 A，条目 24 S3–S5 落地）。

合同：
- 读取唯一属主 = `repo_meta.load_user_state`（缺失/损坏/非 dict 恒回 {}）；
- 路径唯一属主 = `repo_meta.user_state_path`；
- `app/services/` 下**禁止**再出现字面量 `"user_state.json"`（docstring 叙述不锁）；
  routers 层同禁（2026-10-24 审计中期项后 user_state.py 的读/路径也已收口，
  写侧经 `repo_meta.user_state_path` 取路径，全层不再需要字面量）。
- 注入接缝 = `monkeypatch.setattr(repo_meta, "DATA_DIR", ...)`（模块级绑定，与
  conftest / test_smithery_key_narrowing 同款；绝不触碰真实 user_state.json）。
"""
import ast
from pathlib import Path

from app.services import repo_meta


def test_读取属主_缺失回空dict(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    assert repo_meta.load_user_state() == {}


def test_读取属主_损坏JSON回空dict(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    (tmp_path / "user_state.json").write_text("{not-json", encoding="utf-8")
    assert repo_meta.load_user_state() == {}


def test_读取属主_非dict根回空dict(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    (tmp_path / "user_state.json").write_text("[1,2,3]", encoding="utf-8")
    assert repo_meta.load_user_state() == {}


def test_读取属主_合法文件透传(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    (tmp_path / "user_state.json").write_text('{"repos": [{"id": "r1"}]}', encoding="utf-8")
    assert repo_meta.load_user_state() == {"repos": [{"id": "r1"}]}


def test_路径属主跟随注入(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    assert repo_meta.user_state_path() == tmp_path / "user_state.json"


def test_services层禁止字面量user_state_json():
    """形态锁：除属主 repo_meta 外，services 层不得出现字面量 "user_state.json"。

    用 ast 字符串常量精确匹配——docstring 里作为历史叙述提及不算违例
    （fabric_loop/worldbook_store 的治本史注保留）。
    """
    offenders: list[str] = []
    for path in sorted(Path("app/services").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "user_state.json":
                if path.name != "repo_meta.py":
                    offenders.append(path.as_posix())
                break
    assert offenders == [], f"user_state.json 自读收口被破坏：{offenders}"


def test_routers层禁止字面量user_state_json():
    """routers 层锁：读/路径全走 repo_meta 属主，全层（含写侧 user_state.py）无字面量。

    mcp.py 的 docstring 叙述（T6.3 收敛史注）不算违例——ast 精确匹配字符串常量，
    长文本 docstring 不会命中。
    """
    offenders: list[str] = []
    for path in sorted(Path("app/routers").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "user_state.json":
                offenders.append(path.as_posix())
                break
    assert offenders == [], f"routers 层出现字面量 user_state.json：{offenders}"
