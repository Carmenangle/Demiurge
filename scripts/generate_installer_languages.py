"""由 tauri 自带语言文件生成「卸载勾选框 + 卸载完成弹窗」文案覆盖，供 `customLanguageFiles` 使用。

为什么要有这个生成器（2026-11-02 两条实测事实决定了形状）：

1. `bundle.windows.nsis.customLanguageFiles` 是**整体替换** tauri 自带的语言文件，不是追加
   ⇒ 覆盖文件必须自带全部 `LangString`，少一条该语言就在那条上读到空串（makensis 只警告 6040，不出错）。
   所以这里逐字复制上游副本，只改 `deleteAppData` 一行。
2. tauri 写目标文件时自己加 BOM ⇒ 仓库里的覆盖文件必须**不带 BOM**，否则落盘变双 BOM，
   makensis 报 `Invalid command: "﻿;"` 整个 bundle 挂掉（第一次尝试就是这么死的）。

输入是仓库里的 **`upstream/<lang>.nsh`＝tauri 原样副本**（真身取证办法见下面的刷新规程）。
🔴 不能拿构建产物 `target/release/nsis/x64/<lang>.nsh` 当输入：一旦本覆盖生效，那个文件就是**我们自己的文件**
（tauri 只是抄过去再加 BOM），拿它再生成会把表头逐轮叠加。

刷新规程（升级 tauri 后走一次）：临时摘掉 `tauri.conf.json` 的 `customLanguageFiles` ⇒ 跑一次
`build_installers.py` ⇒ 把落盘的 `<lang>.nsh` 剥掉 BOM 后覆盖 `upstream/` ⇒ 再跑本脚本并核对新增的 `LangString`。

跑法：
- 生成/刷新覆盖：`python scripts/generate_installer_languages.py`
- 防漂移：`python scripts/generate_installer_languages.py --check`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "desktop" / "tauri" / "installer" / "languages" / "upstream"
DEST = ROOT / "desktop" / "tauri" / "installer" / "languages"
BOM = b"\xef\xbb\xbf"
#: 表头标记：出现在「上游」输入里就说明输入被污染成我方文件了（构建产物陷阱）。
MARKER = "customLanguageFiles 会整体替换 tauri 的语言文件"

#: 三档合同里「删除应用程序数据」不区分产物，用户会以为作品仓库也被删 ⇒ 文案必须点名「不删什么」。
#: 勾选框是 400×25 的单行控件 ⇒ 中文 26 字 / 英文 60 字以内，由 test_data_layout 锁住。
LABELS = {
    "SimpChinese": ("${LANG_SIMPCHINESE}", "删除设置与缓存（不删作品仓库/预设/角色卡/世界书）"),
    "English": ("${LANG_ENGLISH}", "Delete settings and caches (works and presets are kept)"),
}

#: 卸载完成弹窗的文案（2026-11-02 待拍板项 5 定案：把「删了什么、什么还留在盘上」当面说清）。
#: 由 `generate_uninstall_hooks.py` 生成的钩子引用 `$(DemiurgeUninstKeep)` / `$(DemiurgeUninstWiped)`，
#: 并在其后**拼上运行时解析出的壳 home**（`DEMIURGE_SHELL_HOME` 覆盖也照实显示），所以这里不写路径。
#: 升级模式（`$UpdateMode = 1`）不弹窗：它静默跑在覆盖安装中间。
DONE_MESSAGES: dict[str, tuple[str, dict[str, str]]] = {
    "SimpChinese": (
        "${LANG_SIMPCHINESE}",
        {
            "DemiurgeUninstKeep": (
                r"卸载完成。$\r$\n缓存（运行时与已下载的分件）已清除。$\r$\n"
                r"设置、凭据与作品数据都保留在：$\r$\n"
            ),
            "DemiurgeUninstWiped": (
                r"卸载完成。$\r$\n设置与凭据（API 密钥、联机令牌、窗口记忆）已连同缓存一起清除。$\r$\n"
                r"作品数据（仓库索引、会话、预设、角色卡、世界书、RAG 索引、模型权重）仍保留在：$\r$\n"
            ),
        },
    ),
    "English": (
        "${LANG_ENGLISH}",
        {
            "DemiurgeUninstKeep": (
                r"Uninstall finished.$\r$\nThe cache (runtime and downloaded parts) was removed.$\r$\n"
                r"Settings, credentials and all works are still here:$\r$\n"
            ),
            "DemiurgeUninstWiped": (
                r"Uninstall finished.$\r$\nSettings and credentials (API keys, LAN token, window memory)"
                r" were removed along with the cache.$\r$\n"
                r"Your works (repo index, chats, presets, character cards, worldbooks, RAG index,"
                r" model weights) are still here:$\r$\n"
            ),
        },
    ),
}

HEADER = (
    "; demiurge：卸载勾选框文案（deleteAppData）+ 卸载完成弹窗文案（DemiurgeUninstKeep/Wiped）覆盖；"
    "其余条目是 tauri 自带语言文件的原样副本——\n"
    "; customLanguageFiles 会整体替换 tauri 的语言文件，所以这里必须写全。\n"
    "; 由 scripts/generate_installer_languages.py 从 upstream/<lang>.nsh 生成；升级 tauri 后按脚本顶部的刷新规程重取上游。\n"
    "; 本文件必须存成无 BOM 的 UTF-8：tauri 落盘时自己加 BOM，带两个 BOM 会让 makensis 报 Invalid command。\n"
)


def _render(language: str) -> str:
    source = UPSTREAM / f"{language}.nsh"
    text = source.read_text(encoding="utf-8-sig")
    if MARKER in text:
        raise SystemExit(
            f"{source} 里已经带我方表头——那是被 customLanguageFiles 覆盖过的构建产物，不是 tauri 原文。"
            "按脚本顶部「刷新规程」重取 upstream/。"
        )
    token, label = LABELS[language]
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if line.startswith("LangString deleteAppData ")]
    if len(hits) != 1:
        raise SystemExit(f"{source}: deleteAppData 应当恰好一条，实际 {len(hits)} 条")
    lines[hits[0]] = f'LangString deleteAppData {token} "{label}"'
    done_token, done_messages = DONE_MESSAGES[language]
    if done_token != token:
        raise SystemExit(f"{language} 的语言常量和勾选框那组不一致：{done_token} != {token}")
    added = []
    for name in sorted(done_messages):
        if any(line.startswith(f"LangString {name} ") for line in lines):
            raise SystemExit(f"{source}: tauri 原文里已有 {name}，我方文案会撞名")
        added.append(f'LangString {name} {done_token} "{done_messages[name]}"')
    return HEADER + "\n".join(lines) + "\n" + "\n".join(added) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只比对，不写盘（防漂移门禁）")
    args = parser.parse_args()

    missing = [language for language in LABELS if not (UPSTREAM / f"{language}.nsh").is_file()]
    if missing:
        print(f"[languages] FAIL：缺上游副本 {missing}（{UPSTREAM}）")
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    for language in LABELS:
        text = _render(language)
        path = DEST / f"{language}.nsh"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                drift.append(path.relative_to(ROOT).as_posix())
        else:
            path.write_text(text, encoding="utf-8")
        raw = path.read_bytes()
        if raw.startswith(BOM):
            raise SystemExit(f"{path.name} 带 BOM——tauri 会再加一个，makensis 必挂")
        entries = sum(1 for line in text.splitlines() if line.startswith("LangString "))
        print(f"[languages] {path.name}: {len(raw)} B, entries={entries}, bom=False")

    if drift:
        print("语言覆盖与 upstream 副本不一致（重跑生成器）：", *drift, sep="\n- ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
