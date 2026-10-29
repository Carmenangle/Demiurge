import base64
import contextlib
import importlib.util
import json
import shutil
import sys
import time
import types
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, relative: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


staging = _load("stage_installer_dependencies", "scripts/stage_installer_dependencies.py")
installers = _load("build_installers", "scripts/build_installers.py")
delivery = _load("runtime_delivery", "scripts/runtime_delivery.py")
updater = _load("updater_manifest", "scripts/updater_manifest.py")


def _fake_mingit(tmp_path: Path) -> tuple[Path, dict]:
    archive = tmp_path / "mingit.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cmd/git.exe", b"MZ fake git")
        bundle.writestr("mingw64/bin/git.exe", b"MZ fake git core")
        bundle.writestr("COPYING", b"GPL")
    mingit = {
        "version": "test",
        "url": "https://example.invalid/mingit.zip",
        "sha256": staging.sha256_file(archive),
    }
    return archive, mingit


def _clean_staging_tree(tmp_path: Path) -> Path:
    archive, mingit = _fake_mingit(tmp_path)
    target = tmp_path / "staging"
    staging.stage_mingit(target, archive, mingit)
    return target


def test_installer_config_uses_strict_resource_allowlist():
    staging.assert_installer_config()
    staging.assert_per_machine_overlay()

    conf = json.loads(
        (ROOT / "desktop" / "tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    bundle = conf["bundle"]
    assert bundle["active"] is True
    assert bundle["targets"] == ["nsis"]
    assert conf["mainBinaryName"] == "Demiurge"
    assert bundle["resources"] == {"installer-staging/dependencies": "dependencies"}
    assert bundle["windows"]["nsis"]["installMode"] == "currentUser"
    assert bundle["windows"]["nsis"]["compression"] == "lzma"
    assert bundle["windows"]["webviewInstallMode"]["type"] == "embedBootstrapper"


def test_installer_variant_config_normalizes_version_and_merges_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(installers, "WORK_DIR", tmp_path)
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY_PATH", raising=False)
    machine = installers.variant_config("machine", "0.15.0", installers.PER_MACHINE_OVERLAY)
    payload = json.loads(machine.read_text(encoding="utf-8"))
    assert payload["version"] == "0.15.0"
    assert payload["bundle"]["windows"]["nsis"]["installMode"] == "perMachine"
    assert payload["bundle"]["createUpdaterArtifacts"] is False

    user = json.loads(installers.variant_config("user", "0.15.0", None).read_text(encoding="utf-8"))
    assert user == {"version": "0.15.0", "bundle": {"createUpdaterArtifacts": False}}
    assert installers.normalize_version("v0.15") == "0.15.0"


def test_assert_installer_config_rejects_repo_directories_and_wrong_exe_name(tmp_path):
    conf = json.loads((ROOT / "desktop" / "tauri" / "tauri.conf.json").read_text(encoding="utf-8"))

    leaky = json.loads(json.dumps(conf))
    leaky["bundle"]["resources"] = {"../../presets": "presets"}
    leaky_path = tmp_path / "leaky.conf.json"
    leaky_path.write_text(json.dumps(leaky), encoding="utf-8")
    with pytest.raises(RuntimeError, match="bundle.resources"):
        staging.assert_installer_config(leaky_path)

    wrong_name = json.loads(json.dumps(conf))
    wrong_name["mainBinaryName"] = "demiurge-shell"
    wrong_name_path = tmp_path / "wrong-name.conf.json"
    wrong_name_path.write_text(json.dumps(wrong_name), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Demiurge.exe"):
        staging.assert_installer_config(wrong_name_path)

    # WebView2 离线整包 205 MiB，会把安装包撑成系统组件包；尺寸漂移必须被拦下。
    heavy = json.loads(json.dumps(conf))
    heavy["bundle"]["windows"]["webviewInstallMode"]["type"] = "offlineInstaller"
    heavy_path = tmp_path / "heavy.conf.json"
    heavy_path.write_text(json.dumps(heavy), encoding="utf-8")
    with pytest.raises(RuntimeError, match="WebView2"):
        staging.assert_installer_config(heavy_path)

    machine_first = json.loads(json.dumps(conf))
    machine_first["bundle"]["windows"]["nsis"]["installMode"] = "perMachine"
    machine_first_path = tmp_path / "machine.conf.json"
    machine_first_path.write_text(json.dumps(machine_first), encoding="utf-8")
    with pytest.raises(RuntimeError, match="currentUser"):
        staging.assert_installer_config(machine_first_path)


def test_stage_mingit_only_emits_dependencies_tree(tmp_path):
    target = _clean_staging_tree(tmp_path)

    assert (target / "dependencies" / "git" / "cmd" / "git.exe").is_file()
    manifest = json.loads(
        (target / "dependencies" / "MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest["dependencies"]["git"]["version"] == "test"
    assert {path.name for path in target.iterdir()} == {"dependencies"}
    assert staging.audit_staging(target) == []


def test_stage_mingit_rejects_checksum_mismatch(tmp_path):
    archive, mingit = _fake_mingit(tmp_path)
    mingit["sha256"] = "0" * 64

    with pytest.raises(RuntimeError, match="SHA256"):
        staging.stage_mingit(tmp_path / "staging", archive, mingit)
    assert not (tmp_path / "staging" / "dependencies").exists()


def test_stage_mingit_requires_git_executable(tmp_path):
    archive = tmp_path / "mingit.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cmd/not-git.exe", b"MZ")
    mingit = {
        "version": "test",
        "url": "https://example.invalid/mingit.zip",
        "sha256": staging.sha256_file(archive),
    }

    with pytest.raises(RuntimeError, match="cmd/git.exe"):
        staging.stage_mingit(tmp_path / "staging", archive, mingit)


def test_audit_rejects_presets_userdata_and_backend_data(tmp_path):
    target = _clean_staging_tree(tmp_path)
    for relative in (
        "presets/GrayWill-private.json",
        "userdata/settings.json",
        "backend/data/user_state.json",
        "dependencies/git/.env",
        "dependencies/git/userdata/state.json",
    ):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    errors = staging.audit_staging(target)
    joined = "\n".join(errors)
    assert "presets/GrayWill-private.json" in joined
    assert "userdata/settings.json" in joined
    assert "backend/data/user_state.json" in joined
    assert "dependencies/git/.env" in joined


def test_audit_rejects_unlisted_dependency_files(tmp_path):
    target = _clean_staging_tree(tmp_path)
    (target / "dependencies" / "extra-tool.exe").write_bytes(b"MZ")

    errors = staging.audit_staging(target)
    assert any("依赖白名单" in error for error in errors)


def test_forbidden_entries_flags_installer_payload():
    entries = [
        "Demiurge.exe",
        "dependencies\\git\\cmd\\git.exe",
        "dependencies\\MANIFEST.json",
        "presets\\GrayWill-a.json",
        "userdata\\state.json",
        "backend\\data\\user_state.json",
        "dependencies\\git\\.env",
    ]

    offenders = installers.forbidden_entries(entries)
    assert offenders == [
        "backend\\data\\user_state.json",
        "dependencies\\git\\.env",
        "presets\\GrayWill-a.json",
        "userdata\\state.json",
    ]


def test_verify_install_tree_requires_dependencies_and_rejects_leaks(tmp_path):
    for relative in (
        "Demiurge.exe",
        "dependencies/git/cmd/git.exe",
        "dependencies/MANIFEST.json",
        "uninstall.exe",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert installers.verify_install_tree(tmp_path) == []

    leaked = tmp_path / "presets" / "GrayWill-private.json"
    leaked.parent.mkdir()
    leaked.write_text("{}", encoding="utf-8")
    errors = installers.verify_install_tree(tmp_path)
    assert any("presets/GrayWill-private.json" in error for error in errors)


def test_uninstall_residue_reports_three_places(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    registry = {"present": False, "key": ""}

    def open_key(_root, key):
        registry["key"] = key
        if not registry["present"]:
            raise FileNotFoundError(key)
        return contextlib.nullcontext()

    monkeypatch.setitem(
        sys.modules, "winreg", types.SimpleNamespace(HKEY_CURRENT_USER=0, OpenKey=open_key)
    )

    assert installers.uninstall_residue(install_dir) == [f"安装目录：{install_dir}"]

    shutil.rmtree(install_dir)
    start_menu = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Demiurge"
    start_menu.mkdir(parents=True)
    assert installers.uninstall_residue(install_dir) == [f"开始菜单：{start_menu}"]

    shutil.rmtree(start_menu)
    assert installers.uninstall_residue(install_dir) == []

    registry["present"] = True
    assert installers.uninstall_residue(install_dir) == [
        f"注册表：HKCU\\{installers.UNINSTALL_REGISTRY_KEY}"
    ]
    assert registry["key"] == installers.UNINSTALL_REGISTRY_KEY


def test_uninstall_residue_excludes_the_app_data_key_by_design():
    # `HKCU\Software\Demiurge\Demiurge`（上次安装路径）由 Tauri 模板绑在「删除应用数据」勾选框上，
    # 静默卸载清不掉也不该清：验收口径只含安装目录、开始菜单与 Uninstall 登记项。
    labels = [label for label, _ in installers.residue_targets(Path("app"), Path("appdata"))]
    assert labels == ["安装目录", "开始菜单"]
    assert installers.UNINSTALL_REGISTRY_KEY == (
        "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Demiurge"
    )


def test_nsi_facts_read_the_active_install_mode_branch():
    # NSIS 模板把两种 RequestExecutionLevel 都写进脚本，只有 !define INSTALLMODE 选中那条生效。
    template = """!define INSTALLMODE "{mode}"
!if "${{INSTALLMODE}}" == "perMachine"
  RequestExecutionLevel admin
!endif
!if "${{INSTALLMODE}}" == "currentUser"
  RequestExecutionLevel user
!endif
!insertmacro MUI_PAGE_DIRECTORY
"""
    user = installers.nsi_facts(template.format(mode="currentUser"))
    machine = installers.nsi_facts(template.format(mode="perMachine"))

    assert user["install_mode"] == "currentUser"
    assert user["request_execution_level"] == "user"
    assert user["directory_page"] is True
    assert machine["install_mode"] == "perMachine"
    assert machine["request_execution_level"] == "admin"
    assert installers.nsi_facts("!define INSTALLMODE \"currentUser\"")["directory_page"] is False


def test_runtime_delivery_pin_matches_release_contract():
    pin = delivery.load_pin(ROOT / "release" / "runtime-delivery.json")
    portable = json.loads((ROOT / "release" / "portable-windows.json").read_text(encoding="utf-8"))
    tauri = json.loads(
        (ROOT / "desktop" / "tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )

    assert pin["target"] in portable["targets"]
    assert pin["version"] == delivery.normalize_version(tauri["version"])
    assert pin["manifest"] == delivery.manifest_name(pin["tag"], pin["target"])
    assert pin["release_base"].endswith(f"/download/{pin['tag']}")
    assert pin["required_layers"] == ["base", "application"]
    assert pin["optional_layers"] == ["rag"]


def test_runtime_delivery_pin_render_rejects_unsafe_tag_and_repository():
    assert delivery.normalize_version("v0.15") == "0.15.0"
    pin = delivery.render_pin("v0.15", "Carmenangle/Demiurge")
    assert pin["release_base"] == (
        "https://github.com/Carmenangle/Demiurge/releases/download/v0.15"
    )
    assert pin["manifest"] == "Demiurge_update_windows_v0.15.json"
    assert pin["version"] == "0.15.0"

    macos = delivery.render_pin("v0.15", "Carmenangle/Demiurge", target="macos-arm64-full-rag")
    assert macos["manifest"] == "Demiurge_update_macos_v0.15.json"

    for tag in ("v0.15/../x", "v0.15.0.1", "v0.15-beta", ".."):
        with pytest.raises(ValueError):
            delivery.render_pin(tag, "Carmenangle/Demiurge")
    with pytest.raises(ValueError):
        delivery.render_pin("v0.15", "../../evil")


def _manifest_fixture(pin: dict) -> dict:
    sha = "a" * 64

    def layer(archive: str, size: int) -> dict:
        return {
            "archive": archive,
            "size": size,
            "sha256": sha,
            "assets": [{"name": archive, "size": size, "sha256": sha}],
        }

    target = pin["target"]
    return {
        "schema_version": 2,
        "app_version": pin["tag"],
        "target": target,
        "edition": "full-rag",
        "base_id": "base-test",
        "application_id": "app-test",
        "rag_id": "rag-test",
        "layers": {
            "base": layer(f"Demiurge_base_windows_{pin['tag']}.zip", 10),
            "application": layer(f"Demiurge_application_windows_{pin['tag']}.zip", 20),
            "rag": layer(f"Demiurge_rag_windows_{pin['tag']}.zip", 30),
        },
    }


def test_runtime_delivery_manifest_validation_rejects_drift():
    pin = delivery.load_pin(ROOT / "release" / "runtime-delivery.json")
    manifest = _manifest_fixture(pin)
    delivery.validate_manifest(manifest, pin)
    delivery.validate_manifest(manifest, pin, include_rag=True)

    wrong_target = json.loads(json.dumps(manifest))
    wrong_target["target"] = "windows-arm64-full-rag"
    with pytest.raises(ValueError, match="target"):
        delivery.validate_manifest(wrong_target, pin)

    wrong_version = json.loads(json.dumps(manifest))
    wrong_version["app_version"] = "v9.9.9"
    with pytest.raises(ValueError, match="app_version"):
        delivery.validate_manifest(wrong_version, pin)

    missing_layer = json.loads(json.dumps(manifest))
    del missing_layer["layers"]["application"]
    with pytest.raises(ValueError, match="application"):
        delivery.validate_manifest(missing_layer, pin)

    bad_size = json.loads(json.dumps(manifest))
    bad_size["layers"]["base"]["size"] = 11
    with pytest.raises(ValueError, match="体积"):
        delivery.validate_manifest(bad_size, pin)

    unsafe_asset = json.loads(json.dumps(manifest))
    unsafe_asset["layers"]["base"]["assets"][0]["name"] = "../base.zip"
    with pytest.raises(ValueError, match="文件名"):
        delivery.validate_manifest(unsafe_asset, pin)

    duplicate_asset = json.loads(json.dumps(manifest))
    shared_name = duplicate_asset["layers"]["base"]["assets"][0]["name"]
    duplicate_asset["layers"]["rag"]["archive"] = shared_name
    duplicate_asset["layers"]["rag"]["assets"][0]["name"] = shared_name
    with pytest.raises(ValueError, match="同一个资产名"):
        delivery.validate_manifest(duplicate_asset, pin)

    missing_rag_id = json.loads(json.dumps(manifest))
    missing_rag_id["rag_id"] = ""
    with pytest.raises(ValueError, match="rag_id"):
        delivery.validate_manifest(missing_rag_id, pin)


def test_runtime_delivery_split_parts_drop_archive_extension():
    sha = "a" * 64
    layer = {
        "id": "rag-test",
        "archive": "Demiurge_rag_windows_v0.30.zip",
        "size": 20,
        "sha256": sha,
        "assets": [
            {"name": "Demiurge_rag_windows_v0.30.part01", "size": 10, "sha256": sha},
            {"name": "Demiurge_rag_windows_v0.30.part02", "size": 10, "sha256": sha},
        ],
    }
    delivery.validate_layer("rag", layer)

    # 分卷名从归档基名派生（去掉 .zip），用户看到的是 Demiurge_rag_windows_v0.30.part01，
    # 不是 ...zip.part01；清单与壳两侧都按同一规则校验。
    layer["assets"][0]["name"] = "Demiurge_rag_windows_v0.30.zip.part01"
    with pytest.raises(ValueError, match="分卷名"):
        delivery.validate_layer("rag", layer)


def test_runtime_delivery_assets_must_not_enter_installer_staging(tmp_path):
    target = _clean_staging_tree(tmp_path)
    leaked = [
        target / "runtime-delivery.json",
        target / "Demiurge_update_windows_v0.1.0.json",
        target / "dependencies" / "Demiurge_base_windows_v0.1.0.zip",
        target / "dependencies" / "runtime-pack",
    ]
    for path in leaked:
        if path.name == "runtime-pack":
            path.mkdir(parents=True)
        else:
            path.write_text("{}", encoding="utf-8")

    errors = "\n".join(staging.audit_staging(target))
    assert "runtime-delivery.json" in errors
    assert "Demiurge_update_windows_v0.1.0.json" in errors
    assert "Demiurge_base_windows_v0.1.0.zip" in errors
    assert "runtime-pack" in errors


def test_updater_config_requires_signed_https_and_no_downgrades(tmp_path):
    updater.assert_updater_config()
    conf = json.loads(
        (ROOT / "desktop" / "tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    # 当前嵌入的是自检夹具同一把测试公钥；换生产公钥时这里与 testdata 要一起更新，
    # 避免「配置里还是测试密钥、自检却验的是另一把」。
    test_pubkey = (ROOT / "desktop" / "tauri" / "testdata" / "updater-test.pub").read_text(
        encoding="utf-8"
    ).strip()
    assert conf["bundle"]["createUpdaterArtifacts"] is True
    assert conf["plugins"]["updater"]["pubkey"] == test_pubkey
    assert conf["plugins"]["updater"]["requireSignedVersion"] is True
    assert conf["plugins"]["updater"]["windows"]["installMode"] == "passive"
    assert all(
        endpoint.startswith("https://")
        for endpoint in conf["plugins"]["updater"]["endpoints"]
    )

    def mutated(mutator) -> Path:
        payload = json.loads(json.dumps(conf))
        mutator(payload)
        path = tmp_path / f"mutated-{time.time_ns()}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    with pytest.raises(RuntimeError, match="requireSignedVersion"):
        updater.assert_updater_config(
            mutated(lambda data: data["plugins"]["updater"].update(requireSignedVersion=False))
        )
    with pytest.raises(RuntimeError, match="allowDowngrades"):
        updater.assert_updater_config(
            mutated(lambda data: data["plugins"]["updater"].update(allowDowngrades=True))
        )
    with pytest.raises(RuntimeError, match="installMode"):
        updater.assert_updater_config(
            mutated(
                lambda data: data["plugins"]["updater"]["windows"].update(installMode="quiet")
            )
        )
    with pytest.raises(RuntimeError, match="createUpdaterArtifacts"):
        updater.assert_updater_config(
            mutated(lambda data: data["bundle"].update(createUpdaterArtifacts=False))
        )
    with pytest.raises((RuntimeError, ValueError), match="HTTPS"):
        updater.assert_updater_config(
            mutated(
                lambda data: data["plugins"]["updater"].update(
                    endpoints=["http://example.invalid/latest.json"]
                )
            )
        )


def test_updater_manifest_binds_signature_version_and_rejects_drift():
    signature_path = (
        ROOT / "desktop" / "tauri" / "testdata" / "updater-fixture.bin.sig"
    )
    signature, signed_version = updater.load_signature(signature_path)
    assert signed_version == "0.1.0"

    manifest = updater.render_manifest(
        version="v0.1.0",
        signature_b64=signature,
        signature_version=signed_version,
        artifact_name="Demiurge-Setup-User-0.1.0.exe",
        base_url="https://example.invalid/download/v0.1.0",
        notes="fixture",
    )
    assert manifest["version"] == "0.1.0"
    assert manifest["notes"] == "fixture"
    assert manifest["platforms"] == {
        "windows-x86_64": {
            "signature": signature,
            "url": (
                "https://example.invalid/download/v0.1.0/"
                "Demiurge-Setup-User-0.1.0.exe"
            ),
        }
    }

    with pytest.raises(ValueError, match="签名绑定版本"):
        updater.render_manifest(
            version="0.2.0",
            signature_b64=signature,
            signature_version=signed_version,
            artifact_name="Demiurge-Setup-User-0.2.0.exe",
            base_url="https://example.invalid/download/v0.2.0",
        )
    with pytest.raises(ValueError, match="HTTPS"):
        updater.render_manifest(
            version="0.1.0",
            signature_b64=signature,
            signature_version=signed_version,
            artifact_name="Demiurge-Setup-User-0.1.0.exe",
            base_url="http://example.invalid/download/v0.1.0",
        )
    for name in (
        "../evil.exe",
        "Demiurge-Setup-Machine-0.1.0.exe",
        "Demiurge Setup User.exe",
    ):
        with pytest.raises(ValueError, match="更新资产"):
            updater.render_manifest(
                version="0.1.0",
                signature_b64=signature,
                signature_version=signed_version,
                artifact_name=name,
                base_url="https://example.invalid/download/v0.1.0",
            )


def test_updater_signature_requires_version_in_trusted_comment(tmp_path):
    # 老版本 Tauri CLI 的签名不带 version 字段：requireSignedVersion 会拒绝，
    # 生成器也必须在这里提前拦下，而不是产出一份装上就报错的清单。
    raw = (
        ROOT / "desktop" / "tauri" / "testdata" / "updater-fixture.bin.sig"
    ).read_text(encoding="utf-8").strip()
    decoded = base64.b64decode(raw).decode("utf-8").splitlines()
    decoded[2] = "trusted comment: timestamp:1\tfile:updater-fixture.bin"
    broken = tmp_path / "no-version.sig"
    broken.write_text(
        base64.b64encode("\n".join(decoded).encode("utf-8")).decode("ascii"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="version"):
        updater.load_signature(broken)


def test_variant_config_disables_updater_artifacts_without_signing_key(tmp_path, monkeypatch):
    monkeypatch.setattr(installers, "WORK_DIR", tmp_path)
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY_PATH", raising=False)

    payload = json.loads(
        installers.variant_config("user", "0.15.0", None).read_text(encoding="utf-8")
    )
    assert payload["version"] == "0.15.0"
    assert payload["bundle"]["createUpdaterArtifacts"] is False

    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "test-key")
    machine = json.loads(
        installers.variant_config(
            "machine", "0.15.0", installers.PER_MACHINE_OVERLAY
        ).read_text(encoding="utf-8")
    )
    assert "createUpdaterArtifacts" not in machine["bundle"]
    assert machine["bundle"]["windows"]["nsis"]["installMode"] == "perMachine"
    assert json.loads(
        (installers.PER_MACHINE_OVERLAY).read_text(encoding="utf-8")
    ) == {"bundle": {"windows": {"nsis": {"installMode": "perMachine"}}}}


def _fake_installer(directory: Path, name: str = "Demiurge-Setup-User-0.1.0.exe") -> Path:
    installer = directory / name
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_bytes(b"exe")
    Path(str(installer) + ".sig").write_text("c2ln", encoding="utf-8")
    return installer


def test_preserve_updater_signature_copies_next_to_installer(tmp_path, monkeypatch):
    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "test-key")
    bundle = tmp_path / "bundle"
    source = _fake_installer(bundle, "Demiurge_0.1.0_x64-setup.exe")
    output = tmp_path / "out"
    output.mkdir()
    target = output / "Demiurge-Setup-User-0.1.0.exe"
    target.write_bytes(b"exe")
    since = time.time() - 5

    signature = installers.preserve_updater_signature(source, target, since=since)
    assert signature is not None
    assert signature == Path(str(target) + ".sig")
    assert signature.read_text(encoding="utf-8") == "c2ln"

    # 上一次构建留下的旧签名不能被当成本次产物。
    with pytest.raises(RuntimeError, match="本次构建"):
        installers.preserve_updater_signature(source, target, since=time.time() + 5)

    # 没有签名密钥时不做任何事（CI/本机未配置 secret 的寻常构建）。
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY")
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY_PATH", raising=False)
    assert installers.preserve_updater_signature(source, target, since=since) is None


def test_discover_updater_artifact_skips_machine(tmp_path):
    user = _fake_installer(tmp_path)
    _fake_installer(tmp_path, "Demiurge-Setup-Machine-0.1.0.exe")
    assert updater.discover_artifact(tmp_path, "v0.1.0") == user

    user.unlink()
    with pytest.raises(ValueError, match="currentUser"):
        updater.discover_artifact(tmp_path, "0.1.0")


def test_updater_public_key_text_decodes_conf_envelope():
    """SHA256SUMS.pub 导出的就是客户端验签用的那把钥（conf 信封 → 标准 minisign 公钥文本）。"""
    pub = installers.updater_public_key_text()
    wrapped = base64.b64decode(
        (ROOT / "desktop/tauri/testdata/updater-test.pub").read_text(encoding="utf-8").strip()
    ).decode("utf-8")
    assert pub == wrapped
    assert pub.startswith("untrusted comment: minisign public key")
    assert pub.splitlines()[1].startswith("RW")


def test_sign_checksums_invokes_signer_and_binds_version(tmp_path, monkeypatch):
    """阵营一纪律：有钥匙 ⇒ signer sign 落盘 .sig 并把版本钉进参数；未产出 ⇒ 显式失败。"""
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text("abc  Demiurge-Setup-User-0.1.0.exe\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_cli(args, *, cwd=None):
        calls.append(list(args))
        Path(str(sums) + ".sig").write_text("c2ln", encoding="utf-8")

    monkeypatch.setattr(installers, "run_cli", fake_run_cli)
    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "test-key")

    sig = installers.sign_checksums(sums, version="0.15.0")
    assert sig == Path(str(sums) + ".sig")
    assert calls == [["signer", "sign", str(sums), "--app-version", "0.15.0"]]

    calls.clear()
    Path(str(sums) + ".sig").unlink()
    installers.sign_checksums(sums)
    assert calls == [["signer", "sign", str(sums)]]  # 无版本号＝裸签名仍合法

    # signer 声称成功但没落 .sig ⇒ 发布必须显式失败，不允许静默无签名
    def fake_run_cli_noout(args, *, cwd=None):
        calls.append(list(args))

    monkeypatch.setattr(installers, "run_cli", fake_run_cli_noout)
    Path(str(sums) + ".sig").unlink()
    with pytest.raises(RuntimeError, match="未产出"):
        installers.sign_checksums(sums)


def test_sign_checksums_skips_without_key(tmp_path, monkeypatch, capsys):
    """未配置钥匙 ⇒ 留痕跳过（HTTPS 下载仍受 TLS 保护），不碰 signer。"""
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY_PATH", raising=False)
    called: list[tuple] = []
    monkeypatch.setattr(installers, "run_cli", lambda *a, **k: called.append(a))

    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text("abc  x.exe\n", encoding="utf-8")
    assert installers.sign_checksums(sums, version="0.1.0") is None
    assert called == []
    assert "未签名" in capsys.readouterr().out


def _minisign_envelope(key_id: bytes, *, version: str = "") -> str:
    trusted = "trusted comment: timestamp:0\tfile:updater-key-check.txt"
    if version:
        trusted += f"\tversion:{version}"
    payload = b"ED" + key_id + b"\x00" * 64
    text = "\n".join(
        (
            "untrusted comment: test",
            base64.b64encode(payload).decode("ascii"),
            trusted,
            base64.b64encode(b"\x00" * 64).decode("ascii"),
        )
    )
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_updater_pubkey_and_fixture_signature_share_key_id():
    """conf 内嵌公钥与仓库签名夹具必须同钥；key id 是发布门禁的比较依据。"""
    fixture = ROOT / "desktop" / "tauri" / "testdata" / "updater-fixture.bin.sig"
    key_id = updater.minisign_key_id(fixture)
    assert key_id == updater.updater_pubkey_bytes()[2:10]
    assert updater.assert_signature_matches_pubkey(fixture) == key_id[::-1].hex().upper()


def test_signature_from_other_key_is_rejected(tmp_path):
    """真机最危险的错配：私钥与 conf 公钥不是一把，构建能过、更新必失败。"""
    signature = tmp_path / "other.sig"
    signature.write_text(_minisign_envelope(b"\x01" * 8), encoding="utf-8")
    with pytest.raises(RuntimeError, match="不是同一把钥"):
        updater.assert_signature_matches_pubkey(signature)


def test_updater_manifest_rejects_signature_from_other_key(tmp_path, monkeypatch):
    """生成 latest.json 前必须对真实安装器签名再比对，不只依赖构建期检查。"""
    artifact = tmp_path / "Demiurge-Setup-User-0.1.0.exe"
    artifact.write_bytes(b"MZ")
    Path(str(artifact) + ".sig").write_text(
        _minisign_envelope(b"\x02" * 8, version="0.1.0"), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "updater_manifest.py",
            "--version",
            "0.1.0",
            "--artifact-dir",
            str(tmp_path),
            "--base-url",
            "https://example.invalid/download/v0.1.0",
            "--output",
            str(tmp_path / "latest.json"),
        ],
    )
    with pytest.raises(RuntimeError, match="不是同一把钥"):
        updater.main()


def test_signing_key_check_skips_without_key(monkeypatch):
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY_PATH", raising=False)
    called: list[tuple] = []
    monkeypatch.setattr(installers, "run_cli", lambda *a, **k: called.append(a))
    assert installers.assert_signing_key_matches_updater_key() is None
    assert called == []


def test_signing_key_check_passes_with_matching_key(tmp_path, monkeypatch):
    monkeypatch.setattr(installers, "WORK_DIR", tmp_path)
    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "test-key")
    expected = updater.updater_pubkey_bytes()[2:10]
    challenges: list[Path] = []

    def fake_run_cli(args, *, cwd=None):
        challenge = Path(args[2])
        challenges.append(challenge)
        assert challenge.is_file()
        Path(str(challenge) + ".sig").write_text(
            _minisign_envelope(expected), encoding="utf-8"
        )

    monkeypatch.setattr(installers, "run_cli", fake_run_cli)
    assert installers.assert_signing_key_matches_updater_key() == expected[
        ::-1
    ].hex().upper()
    assert challenges and not challenges[0].exists()
    assert not Path(str(challenges[0]) + ".sig").exists()


def test_signing_key_check_rejects_mismatch_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(installers, "WORK_DIR", tmp_path)
    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "test-key")

    def fake_run_cli(args, *, cwd=None):
        challenge = Path(args[2])
        Path(str(challenge) + ".sig").write_text(
            _minisign_envelope(b"\x03" * 8), encoding="utf-8"
        )

    monkeypatch.setattr(installers, "run_cli", fake_run_cli)
    with pytest.raises(RuntimeError, match="不是同一把钥"):
        installers.assert_signing_key_matches_updater_key()
    assert not (tmp_path / "updater-key-check.txt").exists()
    assert not (tmp_path / "updater-key-check.txt.sig").exists()
