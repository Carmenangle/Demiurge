"""卸载分档真源的门禁：分类不变量、源码完整性、生成物防漂移、NSIS 删除矩阵。

跑：`cd backend && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_data_layout.py -q`

三条纪律（2026-11-02 P0 定案）：
- 完整性闸**只扫源码文本**（ast / 正则），绝不看本机真实目录——失败消息里不许出现真实作品名。
- NSIS 矩阵跑在 `tmp_path` 合成 home 上，且必须显式设 `DEMIURGE_SHELL_HOME`：不设＝钩子指向
  真实 `%LOCALAPPDATA%\\Demiurge`，测试会把开发机的缓存与日志删掉。
- 删除面只按白名单逐名取档：未登记名字必须原样留下，这是「新增条目忘定档 ⇒ 多留一个文件，
  不是误删作品」的机制保证。
"""
from __future__ import annotations

import ast
import codecs
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest

from app import data_layout

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "desktop" / "tauri" / "installer" / "uninstall-hooks.nsh"


def _load(module_name: str, relative: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# —— 分类不变量 ————————————————————————————————————————————————


def test_every_registered_entry_has_a_known_tier_and_kind():
    for scope, entries in data_layout.SCOPES.items():
        for name, (tier, kind) in sorted(entries.items()):
            assert tier in (data_layout.CACHE, data_layout.SETTINGS, data_layout.PRODUCT, data_layout.CONTAINER), (
                f"{scope}/{name} 档位不合法：{tier}"
            )
            assert kind in (data_layout.DIR, data_layout.FILE), f"{scope}/{name} 形态不合法：{kind}"
            assert data_layout.tier(scope, name) == tier
            assert data_layout.kind(scope, name) == kind


def test_tiers_are_mutually_exclusive_within_a_scope():
    for scope, entries in data_layout.SCOPES.items():
        by_tier: dict[str, set[str]] = {}
        for name, (tier, _kind) in entries.items():
            by_tier.setdefault(tier, set()).add(name)
        tiers = list(by_tier)
        for i, left in enumerate(tiers):
            for right in tiers[i + 1 :]:
                assert not (by_tier[left] & by_tier[right]), f"{scope}: {left} 与 {right} 重叠"


def test_container_is_only_the_userdata_parent():
    containers = [
        (scope, name)
        for scope, entries in data_layout.SCOPES.items()
        for name, (tier, _kind) in entries.items()
        if tier == data_layout.CONTAINER
    ]
    assert containers == [(data_layout.SCOPE_SHELL_HOME, "userdata")]


def test_unknown_names_are_never_in_the_deletion_face():
    assert data_layout.tier(data_layout.SCOPE_USERDATA, "zzz_not_registered") == data_layout.UNKNOWN
    assert data_layout.kind(data_layout.SCOPE_USERDATA, "zzz_not_registered") is None
    for scope in data_layout.SCOPES:
        for include_settings in (False, True):
            names = {name for name, _tier, _kind in data_layout.deletion_names(scope, include_settings=include_settings)}
            assert "zzz_not_registered" not in names


def test_deletion_face_splits_cache_and_settings_only():
    for scope in data_layout.SCOPES:
        cache_only = {name for name, tier, _k in data_layout.deletion_names(scope, include_settings=False)}
        with_settings = {name for name, tier, _k in data_layout.deletion_names(scope, include_settings=True)}
        assert cache_only <= with_settings
        assert {n for n, (tier, _k) in data_layout.SCOPES[scope].items() if tier == data_layout.SETTINGS} <= with_settings
        assert not any(
            data_layout.tier(scope, name) not in (data_layout.CACHE, data_layout.SETTINGS)
            for name in with_settings
        )
        for name, tier, _kind in data_layout.deletion_names(scope, include_settings=True):
            assert tier == data_layout.tier(scope, name)


def test_backup_stems_are_settings_entries():
    for scope, stems in data_layout.SETTINGS_BACKUP_STEMS.items():
        for stem in stems:
            assert data_layout.tier(scope, stem) == data_layout.SETTINGS, f"{scope}/{stem}"


# —— 完整性闸：源码里挂在 DATA_DIR / 壳 home 下的名字都必须登记 ————————————


def _string_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_dotted(node.func)}(...)"
    return type(node).__name__


def _backend_data_dir_names() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """扫 `backend/app/**/*.py` 里 `DATA_DIR / <字面量|同文件常量>` 的直接子条目名。

    走 ast 而不是正则：正则会把注释和 docstring 里的 `DATA_DIR/user_state.json` 当成代码。
    真源文件自身跳过——它描述模式，不是写入点。
    """
    files = [
        py for py in sorted((ROOT / "backend" / "app").rglob("*.py"))
        if py.name != "data_layout.py"
    ]
    tables: dict[Path, dict[str, str]] = {}
    trees: dict[Path, ast.Module] = {}
    global_table: dict[str, str] = {}
    for py in files:
        try:
            with warnings.catch_warnings():
                # 被扫文件里既有的无效转义（`\w` 之类）会在 ast.parse 时报 SyntaxWarning，
                # 那是那条正则自己的问题，不该混进本闸的输出里。
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 源码坏了自己会红
            continue
        trees[py] = tree
        tables[py] = _string_constants(tree)
        global_table.update(tables[py])

    found: dict[str, list[str]] = {}
    unresolved: dict[str, list[str]] = {}
    for py, tree in trees.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            left = _dotted(node.left).rstrip("(...)")
            if left.split(".")[-1] != "DATA_DIR":
                continue
            right = node.right
            literal = _literal_name(right)
            if literal is not None:
                found.setdefault(literal, []).append(py.name)
            elif isinstance(right, ast.Name):
                value = tables[py].get(right.id) or global_table.get(right.id)
                if value is None:
                    unresolved.setdefault(right.id, []).append(py.name)
                else:
                    found.setdefault(value, []).append(py.name)
            else:
                unresolved.setdefault(_dotted(right), []).append(py.name)
    return found, unresolved


def _literal_name(node: ast.AST) -> str | None:
    """取出「直接子条目名」的字面量：支持 `"x"` 与 `Path("x")` 两种写法。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and _dotted(node.func) == "Path" and len(node.args) == 1:
        inner = node.args[0]
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            return inner.value
    return None


def test_every_backend_data_dir_entry_is_classified():
    found, unresolved = _backend_data_dir_names()
    assert not unresolved, f"DATA_DIR 子条目名解析不出字面量，无法定档：{unresolved}"
    registered = set(data_layout.SCOPES[data_layout.SCOPE_USERDATA])
    missing = sorted(set(found) - registered)
    assert not missing, (
        "后端往 DATA_DIR 下写了未登记的条目，卸载分档不认识它（先补 data_layout 再动代码）："
        + ", ".join(f"{name}（{sorted(set(found[name]))[:3]}）" for name in missing)
    )


def test_shell_home_children_in_rust_are_classified():
    """壳 home 直属子条目（`home()` / `home_dir` 的 `.join("…")`）必须登记。

    `selftest.rs` 不在扫描面：它造的是合成 home 探针夹具，不是路径合同。
    """
    pattern = re.compile(r'\b(?:home\(\)|home_dir)\s*\.join\(\s*"([^"]+)"')
    found: dict[str, list[str]] = {}
    for rs in sorted((ROOT / "desktop" / "tauri" / "src").glob("*.rs")):
        if rs.name == "selftest.rs":
            continue
        for match in pattern.finditer(rs.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(match.group(1), []).append(rs.name)
    registered = set(data_layout.SCOPES[data_layout.SCOPE_SHELL_HOME])
    assert not (set(found) - registered), f"壳 home 出现未登记条目：{sorted(set(found) - registered)}"
    assert found, "壳 home 扫描为空：正则失效了，完整性闸等于没跑"
    assert registered <= set(found), f"登记了但壳源码里找不到的名字：{sorted(registered - set(found))}"


# —— 生成物：防漂移 + 结构红线 ————————————————————————————————


@pytest.fixture(scope="module")
def generator():
    return _load("generate_uninstall_hooks", "scripts/generate_uninstall_hooks.py")


def test_generated_hooks_are_up_to_date(generator):
    assert HOOKS.is_file(), "卸载钩子生成物缺失"
    assert HOOKS.read_text(encoding="utf-8") == generator.render(), (
        "生成物过期：跑 `python scripts/generate_uninstall_hooks.py`"
    )


def test_generated_hook_never_touches_product_or_whole_home(generator):
    content = generator.render()
    assert 'RmDir /r "$DemiurgeShellHome"' not in content
    assert 'Delete "$DemiurgeShellHome"' not in content
    assert 'RmDir /r "$DemiurgeShellHome\\userdata"' not in content

    prefixes = {
        data_layout.SCOPE_SHELL_HOME: "$DemiurgeShellHome",
        data_layout.SCOPE_USERDATA: "$DemiurgeShellHome\\userdata",
    }
    allowed = {
        f"{prefixes[scope]}\\{name}"
        for scope in prefixes
        for name, _tier, _kind in data_layout.deletion_names(scope, include_settings=True)
    }
    allowed |= {f"{prefixes[scope]}\\{stem}.bak-*" for scope, stems in data_layout.SETTINGS_BACKUP_STEMS.items() for stem in stems}
    targets = re.findall(r'(?:RmDir /r|Delete) "([^"]+)"', content)
    assert targets, "生成物里没有任何删除指令，等于没接线"
    assert set(targets) <= allowed, f"删除面出现非白名单条目：{sorted(set(targets) - allowed)}"

    # 反向锁：删除指令的 (scope, 叶子名) 不允许命中任何产物或容器条目。
    target_pairs = {
        (
            data_layout.SCOPE_USERDATA
            if target.startswith(prefixes[data_layout.SCOPE_USERDATA])
            else data_layout.SCOPE_SHELL_HOME,
            target.rsplit("\\", 1)[-1],
        )
        for target in targets
    }
    product_pairs = {
        (scope, name)
        for scope, entries in data_layout.SCOPES.items()
        for name, (tier, _kind) in entries.items()
        if tier in (data_layout.PRODUCT, data_layout.CONTAINER)
    }
    assert not (target_pairs & product_pairs), f"删除面命中产物/容器：{sorted(target_pairs & product_pairs)}"
    content.encode("ascii")


def test_generated_hook_uses_string_compare_for_tauri_state_vars():
    # NSIS 的 IntCmp 不把空串排在 1 之后：$UpdateMode 未赋值时是 ""，用 IntCmp 会把每次
    # 正常卸载都当成「升级中」，一个字节都不删（本仓 2026-11-02 实测踩过）。
    content = HOOKS.read_text(encoding="utf-8")
    assert "IntCmp $UpdateMode" not in content and "IntCmp $DeleteAppDataCheckboxState" not in content
    assert "StrCmp $UpdateMode 1 demiurge_update_skip" in content
    assert "StrCmp $DeleteAppDataCheckboxState 1 demiurge_settings_yes" in content
    # 回执必须真的追加：FileOpen 的 a 模式不自动定位到文件末尾。
    assert "FileSeek $9 0 END" in content


def test_uninstall_receipt_is_deliberately_off_the_whitelist():
    # 回执文件名不进 data_layout ⇒ tier=unknown ⇒ 删除面永远不碰它。登记成 cache 会让钩子
    # 在半途删掉自己正在写的文件（每行都是 open-write-close），前面积的行就丢了。
    assert data_layout.tier(data_layout.SCOPE_SHELL_HOME, "uninstall.log") == data_layout.UNKNOWN
    content = HOOKS.read_text(encoding="utf-8")
    delete_targets = set(re.findall(r'(?:RmDir /r|Delete) "([^"]+)"', content))
    assert not any(target.endswith("uninstall.log") for target in delete_targets)
    assert '$DemiurgeShellHome\\uninstall.log' in content, "回执路径没接上，卸载不留痕"


def test_tauri_config_wires_the_hook():
    data = json.loads((ROOT / "desktop" / "tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    nsis = data["bundle"]["windows"]["nsis"]
    assert nsis["installerHooks"] == "installer/uninstall-hooks.nsh"


def test_uninstall_checkbox_copy_overrides_tauri_default():
    # 勾选框文案来自 `$(deleteAppData)`（生成的 installer.nsi 里 un.ConfirmShow 用它建控件）。
    # tauri 默认「删除应用程序数据」不区分产物，与三档合同对不上，所以用 customLanguageFiles 覆盖。
    # 两条实测约束（2026-11-02 真实构建 + backend/_tmp/nsis_encoding_probe.py）：
    #   - customLanguageFiles 整体**替换** tauri 的语言文件 ⇒ 覆盖文件必须写全所有 LangString；
    #   - tauri 落盘时自己加 BOM ⇒ 仓库里必须存**无 BOM** UTF-8，双 BOM 会让 makensis 报 Invalid command。
    conf = json.loads((ROOT / "desktop" / "tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    nsis = conf["bundle"]["windows"]["nsis"]
    overrides = nsis["customLanguageFiles"]
    assert set(overrides) == set(nsis["languages"]), "每种可显示语言都要有覆盖文件，否则该语言读到空串"
    for language, relative in overrides.items():
        path = ROOT / "desktop" / "tauri" / relative
        assert path.is_file(), f"缺语言覆盖文件：{relative}"
        raw = path.read_bytes()
        assert not raw.startswith(codecs.BOM_UTF8), f"{relative} 不能带 BOM：tauri 会再加一个，makensis 报 Invalid command"
        lines = raw.decode("utf-8").splitlines()
        entries = [line for line in lines if line.startswith("LangString ")]
        assert len(entries) >= 20, f"{relative} 只有 {len(entries)} 条 LangString：替换语义下必须写全 tauri 的条目"
        matched = [line for line in entries if line.startswith("LangString deleteAppData ")]
        assert len(matched) == 1, f"{relative} 应当只定义一次 deleteAppData：{matched}"
        token = "${LANG_SIMPCHINESE}" if language == "SimpChinese" else "${LANG_ENGLISH}"
        assert token in matched[0], matched[0]
        text = matched[0].split('"')[1]
        assert "应用" not in text and "application data" not in text.lower(), "沿用 tauri 原文案等于没覆盖"
        assert ("缓存" in text) or ("cache" in text.lower()), f"文案要正面说出删的是什么：{text}"
        assert len(text) <= (26 if language == "SimpChinese" else 60), f"勾选框只有一行 400px，文案过长会被裁：{text}"


def test_uninstall_completion_popup_tells_what_is_left_where():
    """卸载完成要当面说清「删了什么、什么还在盘上」（路线图待拍板项 5，随 P2 动壳收口）。

    两侧都要盯：文案真源在语言覆盖文件里（生成的 `.nsh` 必须纯 ASCII，只能引用 `$(...)`
    再拼运行时解析出的壳 home），所以钩子那边管「弹几次、什么模式弹」，语言这边管「每种语言
    两种结局都有词」。缺一条 LangString 时 makensis 只警告 6040 并把文案读成空串——静默门禁
    全绿，交互卸载却给用户看一个只剩路径的框。
    """
    hook = HOOKS.read_text(encoding="utf-8")
    boxes = [line for line in hook.splitlines() if line.strip().startswith("MessageBox ")]
    assert len(boxes) == 2, f"卸载完成弹窗应当恰好两条（勾选/未勾选各一）：{boxes}"
    assert all("/SD IDOK" in line for line in boxes), "缺 /SD ⇒ 静默卸载（出包门禁与升级）卡在没人点的模态框"
    assert "MessageBox" not in hook.split("demiurge_update_skip:", 1)[1], "升级模式覆盖安装中途不许弹"
    assert '"$(DemiurgeUninstWiped)$DemiurgeShellHome\\userdata"' in hook, "清除设置后要说清作品留在 userdata"
    assert '"$(DemiurgeUninstKeep)$DemiurgeShellHome"' in hook, "保留设置时路径要给到壳 home 根"

    conf = json.loads((ROOT / "desktop" / "tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    nsis = conf["bundle"]["windows"]["nsis"]
    for language, relative in nsis["customLanguageFiles"].items():
        text = (ROOT / "desktop" / "tauri" / relative).read_text(encoding="utf-8")
        token = "${LANG_SIMPCHINESE}" if language == "SimpChinese" else "${LANG_ENGLISH}"
        bodies: dict[str, str] = {}
        for name in ("DemiurgeUninstKeep", "DemiurgeUninstWiped"):
            hits = [line for line in text.splitlines() if line.startswith(f"LangString {name} ")]
            assert len(hits) == 1, f"{relative} 里 {name} 应当恰好一条：{hits}"
            assert token in hits[0], f"{name} 要绑本语言常量：{hits[0]}"
            bodies[name] = hits[0].split('"')[1]
            assert ("缓存" in bodies[name]) or ("cache" in bodies[name].lower()), bodies[name]
            assert ("保留" in bodies[name]) or ("still here" in bodies[name]), bodies[name]
        if language != "SimpChinese":
            continue
        for word in ("设置", "凭据"):
            assert word in bodies["DemiurgeUninstKeep"], bodies["DemiurgeUninstKeep"]
            assert word in bodies["DemiurgeUninstWiped"], bodies["DemiurgeUninstWiped"]
        assert "作品" in bodies["DemiurgeUninstWiped"], bodies["DemiurgeUninstWiped"]


def test_language_override_is_generated_and_upstream_copy_is_pristine():
    """覆盖文件必须由生成器产出，且 `upstream/` 里存的必须是 **tauri 原文**。

    🔴 2026-11-02 的实坑：一旦 `customLanguageFiles` 生效，构建落盘的
    `target/release/nsis/x64/<lang>.nsh` 就是我们自己的文件（tauri 只是抄过去加 BOM）。
    拿它当生成器输入会把表头逐轮叠加、并把 `upstream/` 悄悄换成我方文案——那时覆盖与上游相同，
    上一条测试照样绿，勾选框却再也覆盖不掉任何东西。所以这里正面锁「上游仍是上游」。
    """
    gen = ROOT / "scripts" / "generate_installer_languages.py"
    drift = subprocess.run(
        [sys.executable, str(gen), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert drift.returncode == 0, f"语言覆盖与 upstream 不一致，重跑生成器：\n{drift.stdout}{drift.stderr}"
    for language, expected in (
        ("SimpChinese", "删除应用程序数据"),
        ("English", "Delete the application data"),
    ):
        upstream = ROOT / "desktop" / "tauri" / "installer" / "languages" / "upstream" / f"{language}.nsh"
        assert upstream.is_file(), f"缺上游副本：{upstream}"
        text = upstream.read_text(encoding="utf-8")
        hits = [line for line in text.splitlines() if line.startswith("LangString deleteAppData ")]
        assert len(hits) == 1, hits
        assert hits[0].split('"')[1] == expected, (
            f"{upstream.name} 里的 deleteAppData 不是 tauri 原文 ⇒ upstream 被污染成我方文件了"
        )
        assert "整体替换 tauri 的语言文件" not in text, f"{upstream.name} 里混进了我方生成器表头"


# —— NSIS 删除矩阵：合成 home，三档 + 未登记 ——————————————————


NSI_TEMPLATE = r"""Name "demiurge-hook-matrix"
OutFile "OUTFILE"
RequestExecutionLevel user
InstallDir "TMPDIR\install"
Caption "demiurge-hook-matrix"

; The completion popup text ships in the language override files; this build declares stubs with
; language index 0 so the real hook's $(DemiurgeUninstKeep) / $(DemiurgeUninstWiped) references
; actually resolve instead of silently compiling to an empty string (makensis only warns 6040).
LangString DemiurgeUninstKeep 0 "stub keep "
LangString DemiurgeUninstWiped 0 "stub wiped "

; Same shape as tauri's generated installer.nsi: hooks file included above the two Var
; declarations, macro expanded inside the uninstall section.
!include "HOOKSFILE"

Var UpdateMode
Var DeleteAppDataCheckboxState

Section
  CreateDirectory "$INSTDIR"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section -Uninstall
  ReadEnvStr $UpdateMode DEMIURGE_TEST_UPDATE_MODE
  ReadEnvStr $DeleteAppDataCheckboxState DEMIURGE_TEST_DELETE_APP_DATA
  !insertmacro NSIS_HOOK_POSTUNINSTALL
SectionEnd
"""


def _makensis() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "tauri" / "NSIS" / "makensis.exe",
        Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
        Path(r"C:\Program Files\NSIS\makensis.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    pytest.skip("本机没有 makensis（tauri 的 NSIS 工具链），删除矩阵只能在 Windows 出包机上跑")


@pytest.fixture(scope="module")
def uninstaller_exe(tmp_path_factory) -> Path:
    makensis = _makensis()
    work = tmp_path_factory.mktemp("hook-matrix-build")
    nsi = work / "matrix.nsi"
    nsi.write_text(
        NSI_TEMPLATE.replace("OUTFILE", str(work / "matrix.exe").replace("\\", "\\\\"))
        .replace("TMPDIR", str(work).replace("\\", "\\\\"))
        .replace("HOOKSFILE", str(HOOKS).replace("\\", "\\\\")),
        # BOM 必须带：makensis 对无 BOM 的脚本按 ACP 解码，而工作目录路径本身可能含非 ASCII
        # （本机用户目录就是），无 BOM 直接 Bad text encoding 编译失败。
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [str(makensis), str(nsi)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
    )
    assert result.returncode == 0, f"makensis 编译失败：\n{result.stdout[-3000:]}\n{result.stderr[-1000:]}"
    exe = work / "matrix.exe"
    assert exe.is_file()
    return exe


def _seed_home(home: Path) -> dict[tuple[str, str], Path]:
    """按真源逐名铺设条目（含产物与一个未登记名字），返回 (scope, name) → 路径。"""
    seeded: dict[tuple[str, str], Path] = {}
    prefixes = {
        data_layout.SCOPE_SHELL_HOME: home,
        data_layout.SCOPE_USERDATA: home / "userdata",
    }
    for scope, prefix in prefixes.items():
        for name, (tier, kind) in sorted(data_layout.SCOPES[scope].items()):
            if tier == data_layout.CONTAINER:
                continue
            path = prefix / name
            if kind == data_layout.DIR:
                path.mkdir(parents=True, exist_ok=True)
                (path / "blob.bin").write_bytes(b"x")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            seeded[(scope, name)] = path
    unknown = home / "userdata" / "zzz_not_registered"
    unknown.mkdir(parents=True)
    (unknown / "blob.bin").write_bytes(b"x")
    seeded[(data_layout.SCOPE_USERDATA, "zzz_not_registered")] = unknown
    return seeded


def _run_nsis(exe: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    """按原文拼命令行：NSIS 的 `/D=` 与 `_?=` 必须是行尾原文，不能被引号包起来。

    subprocess 传 list 时会给含空格的参数加引号，NSIS 就认不出这两个参数——实测 `/D` 被忽略、
    装到脚本里的 `InstallDir`，`_?=` 被忽略则卸载器复制进 %TEMP% 后台跑，父进程立刻返回。
    本机 pytest 临时目录带空格（`Carmen angle`），所以只能走原文。
    """
    line = " ".join(args)
    assert '"' not in line, f"路径含双引号，命令行会被截断：{line}"
    return subprocess.run(
        f'"{exe}" {line}',
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=env,
        timeout=300,
    )


def _run_cycle(exe: Path, home: Path, *, checked: bool, updating: bool) -> str:
    install = home.parent / f"install-{home.name}"
    env = dict(os.environ)
    env["DEMIURGE_SHELL_HOME"] = str(home)
    env["DEMIURGE_TEST_DELETE_APP_DATA"] = "1" if checked else ""
    env["DEMIURGE_TEST_UPDATE_MODE"] = "1" if updating else ""
    _run_nsis(exe, env, "/S", f"/D={install}")
    seeded = _seed_home(home)
    _run_nsis(install / "uninstall.exe", env, "/S", f"_?={install}")
    receipt = (home / "uninstall.log").read_text(encoding="utf-8") if (home / "uninstall.log").is_file() else ""
    tiers: dict[str, list[Path]] = {}
    for (scope, name), path in seeded.items():
        tiers.setdefault(data_layout.tier(scope, name), []).append(path)
    if updating:
        assert all(p.exists() for paths in tiers.values() for p in paths), "升级模式删了东西"
        assert "SKIP  update mode" in receipt
        assert "MSG   completion" not in receipt, "升级模式覆盖安装中途弹了卸载完成窗"
    else:
        assert all(not p.exists() for p in tiers[data_layout.CACHE]), "档 1 缓存没删干净"
        assert all(p.exists() for p in tiers[data_layout.PRODUCT]), "产物被删了"
        assert all(p.exists() for p in tiers[data_layout.UNKNOWN]), "未登记名字被删了（白名单失效）"
        settings_survived = all(p.exists() for p in tiers[data_layout.SETTINGS])
        assert settings_survived is (not checked), f"设置档删除条件不对：checked={checked} survived={settings_survived}"
        # 静默卸载里 MessageBox 被 /SD 跳过，弹窗分支只有回执能证明走的是哪条。
        wiped = "MSG   completion: settings removed, product kept"
        kept = "MSG   completion: everything kept under shell home"
        assert (wiped if checked else kept) in receipt, receipt
        assert (kept if checked else wiped) not in receipt, receipt
    return receipt


def test_silent_uninstall_removes_cache_only(tmp_path, uninstaller_exe):
    receipt = _run_cycle(uninstaller_exe, tmp_path / "home-plain", checked=False, updating=False)
    assert "=== uninstall: cache always, settings only if checked ===" in receipt
    assert "SKIP  settings (checkbox off)" in receipt
    for scope in data_layout.SCOPES:
        for name, _tier, _kind in data_layout.deletion_names(scope, include_settings=False):
            assert f"OK    {scope}/{name}" in receipt, f"{scope}/{name} 没有删除回执"
            assert f"KEEP  {scope}/{name}" not in receipt


def test_checked_uninstall_also_removes_settings(tmp_path, uninstaller_exe):
    receipt = _run_cycle(uninstaller_exe, tmp_path / "home-checked", checked=True, updating=False)
    assert "SKIP  settings" not in receipt
    for scope in data_layout.SCOPES:
        for name, _tier, _kind in data_layout.deletion_names(scope, include_settings=True):
            assert f"OK    {scope}/{name}" in receipt, f"{scope}/{name} 没有删除回执"
    assert "BAK   userdata/user_state.json.bak-*" not in receipt  # 铺底没造 .bak，回执必须诚实


def test_checked_uninstall_removes_the_backup_family(tmp_path, uninstaller_exe):
    home = tmp_path / "home-bak"
    install = tmp_path / "install-bak"
    env = dict(os.environ)
    env["DEMIURGE_SHELL_HOME"] = str(home)
    env["DEMIURGE_TEST_DELETE_APP_DATA"] = "1"
    _run_nsis(uninstaller_exe, env, "/S", f"/D={install}")
    _seed_home(home)
    backup = home / "userdata" / "user_state.json.bak-20261022"
    backup.write_bytes(b"x")
    _run_nsis(install / "uninstall.exe", env, "/S", f"_?={install}")
    assert not backup.exists(), "勾选后 .bak 同族备份没跟着设置一起清除"
    assert "BAK   userdata/user_state.json.bak-*" in (home / "uninstall.log").read_text(encoding="utf-8")


def test_update_mode_deletes_nothing(tmp_path, uninstaller_exe):
    receipt = _run_cycle(uninstaller_exe, tmp_path / "home-update", checked=True, updating=True)
    assert "=== uninstall" not in receipt
    assert "SKIP  settings" not in receipt


def test_hook_is_inert_when_the_shell_home_is_absent(tmp_path, uninstaller_exe):
    # 本机从未运行过（home 不存在）⇒ 一个字节都不动，也不许造出回执。
    home = tmp_path / "home-missing"
    install = tmp_path / "install-missing"
    env = dict(os.environ)
    env["DEMIURGE_SHELL_HOME"] = str(home)
    _run_nsis(uninstaller_exe, env, "/S", f"/D={install}")
    _run_nsis(install / "uninstall.exe", env, "/S", f"_?={install}")
    assert not home.exists()


_RUN_STARTED_AT = time.time()


def teardown_module(_module) -> None:
    """本文件的测试目录由 pytest 的 tmp_path 管理；这里只守一条：真实壳 home 不许被写。

    只看 mtime 落在本轮之内：本机真做过一次卸载也会留下 uninstall.log，那不该把测试判红。
    """
    real_home = Path(os.environ.get("DEMIURGE_SHELL_HOME") or (Path(os.environ.get("LOCALAPPDATA", "")) / "Demiurge"))
    receipt = real_home / "uninstall.log"
    if receipt.is_file() and receipt.stat().st_mtime >= _RUN_STARTED_AT - 1:
        raise AssertionError(f"测试写进了真实壳 home：{receipt}")
