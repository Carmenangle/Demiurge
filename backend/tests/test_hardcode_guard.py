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
