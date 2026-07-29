from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _requirements(path: Path) -> set[str]:
    return {
        line.split("#", 1)[0].strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }


def test_local_reranker_stays_out_of_offline_base_dependencies():
    base = _requirements(ROOT / "backend" / "requirements.txt")
    optional = _requirements(ROOT / "backend" / "requirements-reranker.txt")

    assert not any(item.startswith("sentence-transformers") for item in base)
    assert any(item.startswith("sentence-transformers>=5.0") for item in optional)


def test_domestic_mirror_install_does_not_pass_an_empty_proxy_argument():
    script = (ROOT / "scripts" / "start-dev.ps1").read_text(encoding="utf-8")

    assert 'pip install --proxy ""' not in script
    assert "pip install --isolated -i $IndexUrl" in script
    assert '$comfyPy = "python"' not in script
    assert "python_embeded\\python.exe" in script


def test_runtime_reassembly_helpers_do_not_require_python():
    shell = (ROOT / "scripts" / "join-runtime.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "join-runtime.ps1").read_text(encoding="utf-8")

    assert "python3" not in shell
    assert "python.exe" not in powershell.lower()


def test_cross_platform_source_release_entrypoints_exist():
    assert (ROOT / "scripts" / "source_release.py").is_file()
    assert (ROOT / "scripts" / "release.sh").is_file()
    assert (ROOT / "start-dev.sh").is_file()
    assert (ROOT / "release" / "source-targets.json").is_file()


def test_runtime_release_validates_windows_vendor_on_windows_runner():
    workflow = (ROOT / ".github" / "workflows" / "runtime-release.yml").read_text(
        encoding="utf-8"
    )

    assert "vendor-closure:" in workflow
    assert "runs-on: windows-2025" in workflow
    assert 'PYTHONUTF8: "1"' in workflow
    assert "needs: [plan, quality, vendor-closure]" in workflow
    assert "python scripts/release_preflight.py" in workflow
    assert "source-build:" in workflow
    assert "python scripts/source_release.py build" in workflow
    assert '.release-assets/*' not in workflow
    assert '--output-dir "release-assets"' in workflow
