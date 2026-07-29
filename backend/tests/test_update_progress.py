"""git/pip 进度解析。正则跑在别人的输出格式上，是最容易悄悄失效的部分。"""
import pytest

from app.services import update_progress as up


def test_parses_git_receiving_with_bytes_and_speed():
    ev = up.parse_git_line(
        "Receiving objects:  47% (1234/2600), 12.34 MiB | 3.21 MiB/s")
    assert ev == {
        "phase": "download", "percent": 47,
        "objects_done": 1234, "objects_total": 2600,
        "received_bytes": int(12.34 * 1024**2),
        "speed_bps": int(3.21 * 1024**2),
    }


def test_parses_git_done_line():
    ev = up.parse_git_line(
        "Receiving objects: 100% (2600/2600), 45.6 MiB | 5.00 MiB/s, done.")
    assert ev["percent"] == 100
    assert ev["objects_done"] == ev["objects_total"] == 2600


def test_parses_git_receiving_without_speed():
    """刚开始传输时还没有速度字段，不能因此整行不匹配。"""
    ev = up.parse_git_line("Receiving objects:   1% (26/2600)")
    assert ev["percent"] == 1
    assert ev["speed_bps"] == 0
    assert ev["received_bytes"] == 0


def test_parses_resolving_deltas_phase():
    ev = up.parse_git_line("Resolving deltas:  73% (900/1233)")
    assert ev == {"phase": "resolve", "percent": 73}


@pytest.mark.parametrize("line", [
    "remote: Counting objects: 100% (5/5), done.",
    "From https://github.com/foo/bar",
    "Already up to date.",
    "",
])
def test_ignores_non_progress_git_lines(line):
    assert up.parse_git_line(line) is None


def test_parses_pip_download_mb():
    ev = up.parse_pip_line(
        "  Downloading numpy-2.1.0-cp313-win_amd64.whl (12.6 MB)")
    assert ev == {"kind": "download",
                  "file": "numpy-2.1.0-cp313-win_amd64.whl",
                  "bytes": int(12.6 * 1024**2)}


def test_parses_pip_download_kb():
    ev = up.parse_pip_line("  Downloading tqdm-4.66.4-py3-none-any.whl (78 kB)")
    assert ev["bytes"] == 78 * 1024


def test_distinguishes_cached_from_download():
    """命中缓存不占网络，但用户仍该看到这个包。"""
    ev = up.parse_pip_line("  Using cached six-1.16.0-py2.py3-none-any.whl (11 kB)")
    assert ev["kind"] == "cached"
    assert ev["file"] == "six-1.16.0-py2.py3-none-any.whl"


def test_parses_installing_collected_packages():
    ev = up.parse_pip_line("Installing collected packages: six, tqdm, numpy")
    assert ev == {"kind": "installing", "packages": ["six", "tqdm", "numpy"]}


@pytest.mark.parametrize("line", [
    "Requirement already satisfied: numpy in ./lib (2.1.0)",
    "Successfully installed tqdm-4.66.4",
    "",
])
def test_ignores_other_pip_lines(line):
    assert up.parse_pip_line(line) is None


def test_split_handles_carriage_returns():
    """git 用 \\r 原地刷新，不按 \\r 切就只能看到最后一次。"""
    chunk = ("Receiving objects:  10% (1/10)\r"
             "Receiving objects:  50% (5/10)\r"
             "Receiving objects: 100% (10/10)\n")
    lines = up.split_progress_stream(chunk)
    assert len(lines) == 3
    assert [up.parse_git_line(x)["percent"] for x in lines] == [10, 50, 100]


@pytest.mark.parametrize("value,unit,want", [
    ("1", "B", 1),
    ("1", "KB", 1024),
    ("1", "KiB", 1024),
    ("2.5", "MB", int(2.5 * 1024**2)),
    ("1", "GiB", 1024**3),
    ("bad", "MB", 0),
])
def test_to_bytes(value, unit, want):
    assert up.to_bytes(value, unit) == want


@pytest.mark.parametrize("n,want", [
    (0, "0 B"), (512, "512 B"), (1024, "1.0 KB"),
    (int(1.5 * 1024**2), "1.5 MB"), (2 * 1024**3, "2.0 GB"),
])
def test_human_bytes(n, want):
    assert up.human_bytes(n) == want
