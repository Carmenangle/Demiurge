# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


root = Path(os.environ["LAF_BUILD_ROOT"])
work_dir = Path(os.environ["LAF_BUILD_WORK_DIR"])
runtime_name = os.environ["LAF_BUILD_RUNTIME_NAME"]
icon = os.environ.get("LAF_BUILD_ICON") or None

datas = []
binaries = []
# External RAG packages are deliberately excluded from Analysis, so their
# standard-library-only imports are not discovered from bytecode.
hiddenimports = ["app.main", "unittest.mock", *sorted(sys.stdlib_module_names)]
for module in ("chromadb", "langchain_chroma", "langgraph", "langchain_mcp_adapters"):
    module_datas, module_binaries, module_hidden = collect_all(module)
    datas += module_datas
    binaries += module_binaries
    hiddenimports += module_hidden

a = Analysis(
    [str(root / "scripts" / "runtime_entry.py")],
    pathex=[str(root / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "chromadb.test", "chromadb.server", "pytest",
        # sklearn/scipy 是 RAG 层（reranker）的依赖，base 层只需 chromadb 声明的 numpy。
        # 开发机 venv 装过 reranker 依赖时，sklearn/.libs 里的 msvcp140.dll（14.29，VS2019
        # 时代）会被打进 base 层；冻结进程的 _MEIPASS 位于 DLL 搜索路径最前，RAG 层 torch 的
        # c10.dll 需要 14.4x+ 的 msvcp140，拿到这份旧版就会 DllMain 初始化失败（WinError
        # 1114）。CI 的 venv 只装 requirements.txt 恰好躲过，所以必须显式排除，不能依赖构建
        # 环境是否干净。
        "sentence_transformers", "transformers", "torch",
        "sklearn", "scipy",
    ],
    noarchive=False,
    optimize=0,
)

# Analysis 仍负责发现 app 使用的第三方依赖，但业务代码不进入 PYZ。
external_app_pure = [
    entry for entry in a.pure
    if entry[0] != "app" and not entry[0].startswith("app.")
]
pyz = PYZ(external_app_pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=runtime_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name=runtime_name,
)
