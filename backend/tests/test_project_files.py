from pathlib import Path, PurePosixPath

import pytest

from app.services import project_files


def test_project_root_使用后端仓库配置而非调用方路径(monkeypatch, tmp_path):
    monkeypatch.setattr(project_files.repo_meta, "output_dir_from_state", lambda: str(tmp_path))
    def fake_repo_folder(base, repo_id):
        folder = Path(base) / repo_id
        folder.mkdir()
        return folder
    monkeypatch.setattr(project_files.repo_meta, "repo_folder", fake_repo_folder)

    root = project_files.project_root("作品一")

    assert root == (tmp_path / "作品一").resolve()
    assert root.is_dir()


def test_project_root_未配置或未选作品时拒绝(monkeypatch):
    monkeypatch.setattr(project_files.repo_meta, "output_dir_from_state", lambda: "")
    with pytest.raises(project_files.ProjectFileError, match="仓库文件夹"):
        project_files.project_root("作品一")
    monkeypatch.setattr(project_files.repo_meta, "output_dir_from_state", lambda: "D:/works")
    with pytest.raises(project_files.ProjectFileError, match="先选择"):
        project_files.project_root("home")


def test_源库目录只从后端状态读取(monkeypatch):
    monkeypatch.setattr(project_files.repo_meta, "_load_state", lambda: {
        "settings": {"characterDir": "D:/cards", "presetDir": "D:/presets"},
    })
    assert project_files.repo_meta.setting_dir_from_state("characterDir") == "D:/cards"
    assert project_files.repo_meta.setting_dir_from_state("presetDir") == "D:/presets"


def test_utf8文件可创建读取列出和精确替换(tmp_path):
    root = tmp_path.resolve()

    assert project_files.write_text(root, "角色/塞西莉亚.json", '{"name":"塞西莉亚"}\n') > 0
    assert project_files.read_text(root, "角色/塞西莉亚.json") == '{"name":"塞西莉亚"}\n'
    assert project_files.file_exists(root, "角色/塞西莉亚.json") is True
    assert project_files.list_files(root) == ["角色/塞西莉亚.json"]
    assert project_files.replace_text(root, "角色/塞西莉亚.json", "塞西莉亚", "院长") == 1
    assert project_files.read_text(root, "角色/塞西莉亚.json") == '{"name":"院长"}\n'


@pytest.mark.parametrize("path", ["../outside.txt", "角色/../../outside.txt", "C:/outside.txt"])
def test_拒绝目录穿越和绝对路径(tmp_path, path):
    with pytest.raises(project_files.ProjectFileError):
        project_files.write_text(tmp_path.resolve(), path, "x")


def test_windows绝对路径判定不依赖宿主平台(monkeypatch):
    monkeypatch.setattr(project_files, "Path", PurePosixPath)

    for path in (
        "C:/outside.txt", r"C:\outside.txt", r"C:outside.txt",
        r"\\server\share\outside.txt", r"\outside.txt",
    ):
        with pytest.raises(project_files.ProjectFileError):
            project_files._relative_path(path)


def test_拒绝符号链接逃逸(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建目录符号链接")

    with pytest.raises(project_files.ProjectFileError, match="超出"):
        project_files.write_text(root.resolve(), "link/escaped.txt", "x")
    assert not (outside / "escaped.txt").exists()


def test_拒绝二进制非utf8和超限内容(tmp_path):
    root = tmp_path.resolve()
    (root / "binary.bin").write_bytes(b"a\x00b")
    (root / "gbk.txt").write_bytes("中文".encode("gbk"))

    with pytest.raises(project_files.ProjectFileError, match="二进制"):
        project_files.read_text(root, "binary.bin")
    with pytest.raises(project_files.ProjectFileError, match="UTF-8"):
        project_files.read_text(root, "gbk.txt")
    with pytest.raises(project_files.ProjectFileError, match="超过"):
        project_files.write_text(root, "large.txt", "x" * (project_files.MAX_TEXT_BYTES + 1))


def test_重复文本默认拒绝模糊替换(tmp_path):
    root = tmp_path.resolve()
    project_files.write_text(root, "script.txt", "same\nsame\n")

    with pytest.raises(project_files.ProjectFileError, match="出现 2 次"):
        project_files.replace_text(root, "script.txt", "same", "new")
    assert project_files.replace_text(
        root, "script.txt", "same", "new", replace_all=True,
    ) == 2


def test_PNG附件可原子写入读取且拒绝伪图片(tmp_path):
    png = project_files.PNG_SIGNATURE + b"payload"
    assert project_files.write_png(tmp_path, "角色卡/塞西莉亚/avatar.png", png) == len(png)
    assert project_files.read_png(tmp_path, "角色卡/塞西莉亚/avatar.png") == png
    with pytest.raises(project_files.ProjectFileError, match="有效的 PNG"):
        project_files.write_png(tmp_path, "bad.png", b"not-png")
