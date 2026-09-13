from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import docs
from app.services import doc_library


def _seed(root, rel: str, text: str) -> None:
    """newline="" —— Windows 默认会把 \\n 写成 \\r\\n，接口原样返回磁盘内容，这里保持字面一致。"""
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8", newline="")


def test_读文档_返回路径标题正文(tmp_path):
    _seed(tmp_path, "docs/guide/a.md", "# 工作流导入\n\n正文一段\n")

    result = doc_library.read_doc("docs/guide/a.md", tmp_path)

    assert result["path"] == "docs/guide/a.md"
    assert result["title"] == "工作流导入"
    assert result["content"] == "# 工作流导入\n\n正文一段\n"


def test_无一级标题时回退文件名(tmp_path):
    _seed(tmp_path, "docs/guide/plain.md", "没有标题的正文\n")

    result = doc_library.read_doc("docs/guide/plain.md", tmp_path)

    assert result["title"] == "plain"


def test_反斜杠路径按正斜杠解析(tmp_path):
    _seed(tmp_path, "docs/guide/win.md", "# 反斜杠\n")

    result = doc_library.read_doc("docs\\guide\\win.md", tmp_path)

    assert result["title"] == "反斜杠"


def test_文档过大被拒(tmp_path):
    _seed(tmp_path, "docs/guide/big.md", "x" * (doc_library.MAX_DOC_BYTES + 1))

    with pytest.raises(ValueError, match="过大"):
        doc_library.read_doc("docs/guide/big.md", tmp_path)


@pytest.mark.parametrize("bad", [
    "",
    "   ",
])
def test_空路径被拒(tmp_path, bad):
    with pytest.raises(ValueError, match="为空"):
        doc_library.read_doc(bad, tmp_path)


@pytest.mark.parametrize("bad", [
    "/docs/guide/a.md",
    "C:/repo/docs/guide/a.md",
    "C:\\repo\\docs\\guide\\a.md",
])
def test_绝对路径与盘符被拒(tmp_path, bad):
    with pytest.raises(ValueError, match="相对仓库根"):
        doc_library.read_doc(bad, tmp_path)


@pytest.mark.parametrize("bad", [
    "docs/../backend/app/main.py",
    "../README.md",
    "docs/./../README.md",
])
def test_穿越到仓库外被拒(tmp_path, bad):
    with pytest.raises(ValueError):
        doc_library.read_doc(bad, tmp_path)


@pytest.mark.parametrize("bad", [
    "README.md",              # 仓库根的 README 不在 docs/ 白名单内
    "backend/app/main.py",
    "docs/guide/a.png",
    "docs/guide/a",
])
def test_白名单外与非md被拒(tmp_path, bad):
    _seed(tmp_path, "docs/guide/a.md", "# a\n")
    _seed(tmp_path, "README.md", "# root\n")

    with pytest.raises(ValueError):
        doc_library.read_doc(bad, tmp_path)


def test_文档不存在(tmp_path):
    (tmp_path / "docs").mkdir()

    with pytest.raises(ValueError, match="不存在"):
        doc_library.read_doc("docs/guide/missing.md", tmp_path)


def test_路径是目录而非文件(tmp_path):
    (tmp_path / "docs" / "notafile.md").mkdir(parents=True)

    with pytest.raises(ValueError, match="目录"):
        doc_library.read_doc("docs/notafile.md", tmp_path)


def test_符号链接越界被拒(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# 越界\n", encoding="utf-8")
    link_dir = tmp_path / "docs"
    link_dir.mkdir()
    try:
        (link_dir / "escape.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")

    with pytest.raises(ValueError):
        doc_library.read_doc("docs/escape.md", tmp_path)


def test_路由把非法路径映射为400(tmp_path):
    with pytest.raises(HTTPException) as exc:
        docs.get_doc("backend/app/main.py")

    assert exc.value.status_code == 400


def test_路由正常返回文档内容(tmp_path, monkeypatch):
    _seed(tmp_path, "docs/guide/routed.md", "# 路由读文档\n")
    monkeypatch.setattr(doc_library, "repo_root", lambda: tmp_path)

    result = docs.get_doc("docs/guide/routed.md")

    assert result["title"] == "路由读文档"
