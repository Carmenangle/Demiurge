from scripts.check_hardcode import scan


def test_hardcode_guard_rejects_machine_path_even_in_config(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "config.py").write_text(
        'OUTPUT_DIR = Path(r"D:\\private\\output")\n', encoding="utf-8",
    )

    hits = scan(app_dir)

    assert len(hits) == 1
    assert "D:\\private\\output" in hits[0]


def test_hardcode_guard_allows_portable_config_path(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "config.py").write_text(
        'OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", DATA_DIR / "output"))\n',
        encoding="utf-8",
    )

    assert scan(app_dir) == []


def test_hardcode_guard_rejects_bare_port_fallback_outside_config(tmp_path):
    # 2026-10-24 审计收编：`or 8188` 这类裸端口回退与完整地址同样逃逸端口真源
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "service.py").write_text(
        "def probe(url):\n    port = urlparse(url).port or 8188\n",
        encoding="utf-8",
    )

    hits = scan(app_dir)

    assert len(hits) == 1
    assert "8188" in hits[0]


def test_hardcode_guard_rejects_bare_backend_port(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "helper.py").write_text('probe("http://127.0.0.1:8010/api")\n', encoding="utf-8")

    hits = scan(app_dir)

    assert len(hits) == 1
    assert "8010" in hits[0]


def test_hardcode_guard_allows_bare_port_in_config(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "config.py").write_text("DEFAULT_PORT = 8188\n", encoding="utf-8")

    assert scan(app_dir) == []
