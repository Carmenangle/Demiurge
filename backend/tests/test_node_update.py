"""更新流程：结果校验（防「假更新」）与敏感依赖闸门。

用真 git 仓库跑，不 mock subprocess —— 「假更新」这个 bug 的根因正是
没有真的去核对 HEAD，用 mock 测就等于把要防的东西假设掉了。
"""
import subprocess
import time

import pytest

from app.services import node_update


@pytest.fixture(autouse=True)
def isolate_progress_persistence(monkeypatch):
    monkeypatch.setattr(node_update.task_progress_store, "save", lambda *_args, **_kwargs: None)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    _git(path, "commit", "-q", "--allow-empty", "-m", "c1")
    return path


@pytest.fixture
def clone_pair(tmp_path):
    """上游 + 克隆，克隆有上游追踪分支（等于真实插件目录的形态）。"""
    upstream = _init_repo(tmp_path / "upstream")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t.t")
    _git(clone, "config", "user.name", "t")
    return upstream, clone


def _wait(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        p = node_update.progress()
        if p.get("finished"):
            return p
        time.sleep(0.05)
    raise AssertionError("更新任务超时未结束")


def test_head_and_upstream_detection(clone_pair):
    _, clone = clone_pair
    assert node_update.head(str(clone))
    assert node_update.has_upstream(str(clone)) is True


def test_no_upstream_is_rejected(tmp_path):
    """detached HEAD / 无上游的仓库 pull 必然失败，要提前说清而不是硬跑。"""
    repo = _init_repo(tmp_path / "solo")
    node_update.start(str(repo))
    p = _wait()
    assert p["error"]
    assert "上游追踪分支" in p["error"]


def test_non_git_dir_is_rejected(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    node_update.start(str(d))
    p = _wait()
    assert "不是 git 安装" in p["error"]


def test_unchanged_repo_reports_no_change(clone_pair):
    """核心回归：没有新提交时必须明说「没有任何改动」，不能报「已更新」。"""
    _, clone = clone_pair
    node_update.start(str(clone))
    p = _wait()
    assert p["error"] == ""
    assert p["changed"] is False
    assert "没有任何改动" in p["message"]
    assert p["old"] == p["new"]


def test_real_update_reports_changed_commits(clone_pair):
    """有新提交时 old/new 必须不同，且据此判定 changed。"""
    upstream, clone = clone_pair
    (upstream / "f.txt").write_text("x", encoding="utf-8")
    _git(upstream, "add", "f.txt")
    _git(upstream, "commit", "-q", "-m", "c2")

    node_update.start(str(clone))
    p = _wait()
    assert p["error"] == ""
    assert p["changed"] is True
    assert p["old"] and p["new"] and p["old"] != p["new"]
    assert "已更新" in p["message"]
    assert (clone / "f.txt").is_file()


def test_no_requirements_says_so(clone_pair):
    upstream, clone = clone_pair
    (upstream / "code.py").write_text("pass", encoding="utf-8")
    _git(upstream, "add", "code.py")
    _git(upstream, "commit", "-q", "-m", "c2")
    node_update.start(str(clone))
    p = _wait()
    assert p["changed"] is True
    assert "没有 requirements.txt" in p["message"]


def test_skip_deps_leaves_env_alone(clone_pair):
    upstream, clone = clone_pair
    (upstream / "requirements.txt").write_text("tqdm\n", encoding="utf-8")
    _git(upstream, "add", "requirements.txt")
    _git(upstream, "commit", "-q", "-m", "c2")
    node_update.start(str(clone), python_exe="", skip_deps=True)
    p = _wait()
    assert p["changed"] is True
    assert p["deps"] == []


def test_missing_python_does_not_silently_skip_deps(clone_pair):
    """找不到解释器时要说清依赖没处理，不能假装完成。"""
    upstream, clone = clone_pair
    (upstream / "requirements.txt").write_text("tqdm\n", encoding="utf-8")
    _git(upstream, "add", "requirements.txt")
    _git(upstream, "commit", "-q", "-m", "c2")
    node_update.start(str(clone), python_exe="")
    p = _wait()
    assert "依赖未处理" in p["message"]


def test_rejects_concurrent_runs(clone_pair):
    _, clone = clone_pair
    node_update._PROGRESS.clear()
    node_update._PROGRESS.update(node_update._blank())
    node_update._PROGRESS["running"] = True
    try:
        assert node_update.start(str(clone))["already_running"] is True
    finally:
        node_update._PROGRESS["running"] = False


def test_sensitive_set_is_normalized():
    """SENSITIVE 比较要对 -/_ 和大小写不敏感，否则 Pillow/opencv_python 会漏判。"""
    assert node_update._norm("Pillow") == "pillow"
    assert node_update._norm("opencv_python") == "opencv-python"
    assert node_update._norm("huggingface_hub") == "huggingface-hub"


def test_progress_snapshot_shape_before_any_run():
    node_update._PROGRESS.clear()
    p = node_update.progress()
    for key in ("running", "finished", "deps", "received_bytes", "speed_bps",
                "old", "new", "changed", "pending_sensitive"):
        assert key in p


def test_install_target_is_confined_to_custom_nodes(tmp_path):
    target = node_update.install_target(str(tmp_path), "https://github.com/acme/example-node.git")
    assert target == (tmp_path / "custom_nodes" / "example-node").resolve()


@pytest.mark.parametrize("url", [
    "http://github.com/acme/node.git",
    "https://example.com/acme/node.git",
    "file:///tmp/node",
])
def test_install_target_rejects_untrusted_repository(tmp_path, url):
    with pytest.raises(ValueError):
        node_update.install_target(str(tmp_path), url)


def test_core_switch_rejects_invalid_version(tmp_path):
    with pytest.raises(ValueError):
        node_update.start_core_switch(str(tmp_path), "../../other")


def test_tracked_node_install_reports_successful_terminal_state(monkeypatch, tmp_path):
    target = tmp_path / "custom_nodes" / "example-node"

    def clone(_repository, clone_target, _proxy=""):
        clone_target.mkdir(parents=True)
        (clone_target / ".git").mkdir()
        return True, ""

    monkeypatch.setattr(node_update, "install_target", lambda *_args: target)
    monkeypatch.setattr(node_update, "_clone_with_progress", clone)

    result = node_update.start_install(
        str(tmp_path),
        "https://github.com/acme/example-node.git",
    )
    assert result["already_running"] is False
    progress = _wait()
    assert progress["finished"] is True
    assert progress["error"] == ""
    assert progress["phase"] == "done"
    assert progress["changed"] is True
    assert progress["task_kind"] == "node-install"
    assert progress["subject"] == "example-node"
    assert progress["target_path"] == str(target)
    assert "安装完成" in progress["message"]
