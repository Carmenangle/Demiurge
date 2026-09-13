"""作品前序产出档案扫描（2026-09-07 用户定案：重发的资本）。

背景：每次新 run（重发完整指令）都是全新 LLM 上下文——卡纲、目标方案、
_prep/charfacts 素材、worldbook.json 条目、B 态主卡都落盘了，但新 run 的模型
不知道它们存在、内容是什么，于是重新 survey→重读全文→重生成，用户视为
「前面把全文给 agent 读是白读」。

治本：run 启动前扫描作品已有产出，生成【前序产出档案】注入 system 上下文，
让模型明确：已有产出在哪里、内容概要、本次任务应基于其继续/修改/优化，
不再从头通读重建；需要细节时读对应文件而非重读全书。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_BRIEF_MARK = "【前序产出档案】"


def _safe_read_head(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars]
    except OSError:
        return ""


def _repo_folder(output_dir: str, repo_id: str) -> Path | None:
    """作品目录（仓库输出文件夹），失败回退 None。"""
    try:
        from app.services import repo_meta
        p = repo_meta.repo_folder_path(output_dir, repo_id)
        return Path(p)
    except Exception:  # noqa: BLE001 - 元信息不可用不阻断
        return None


def _scan_docs(root: Path, out: list[str]) -> None:
    """docs/*.md（卡纲 / 目标方案 / 流程记录）。"""
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return
    for f in sorted(docs_dir.glob("*.md")):
        size = f.stat().st_size
        head = _safe_read_head(f, 500).replace("\n", " ")[:300]
        out.append(f"- 文档 {f.name}（{size}B）概要：{head or '（空）'}")


def _scan_charfacts(root: Path, out: list[str]) -> None:
    """_prep/charfacts/*.txt（角色素材段）。"""
    cf = root / "_prep" / "charfacts"
    if not cf.is_dir():
        return
    files = sorted(cf.glob("*.txt"))
    if not files:
        return
    listed = "、".join(f"{f.name}({f.stat().st_size // 1024}KB)" for f in files)
    out.append(f"- 角色素材段 _prep/charfacts/ 共 {len(files)} 个：{listed[:1200]}")


def _scan_worldbook(root: Path, out: list[str]) -> None:
    """作品世界书快照 worldbook.json：条目数与标题。"""
    snap = root / "worldbook.json"
    if not snap.is_file():
        return
    try:
        book = json.loads(snap.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    entries = book.get("entries") or []
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not entries:
        out.append("- 世界书快照 worldbook.json 存在但无条目")
        return
    total_chars = sum(len(str(e.get("content") or "")) for e in entries)
    titles = "；".join(
        str(e.get("comment") or e.get("name") or f"条目{i}")
        for i, e in enumerate(entries[:30], 1))
    out.append(
        f"- 世界书快照 worldbook.json：{len(entries)} 条（共约 {total_chars} 字）。"
        f"已有标题：{titles[:1500]}")


def _scan_main_card(root: Path, out: list[str]) -> None:
    """B 态主卡：<root>/<卡名>/card.json（含内嵌 entries 才是 B 态）。"""
    for f in sorted(root.glob("*/card.json")):
        try:
            size = f.stat().st_size
            card = json.loads(f.read_text(encoding="utf-8"))
            book = (card.get("data") or {}).get("character_book") or {}
            entries = book.get("entries") or []
            n = len(entries) if isinstance(entries, list) else len(entries or {})
            first_mes = bool(str(card.get("first_mes") or "").strip())
            state = (f"B 态：内嵌 {n} 条，first_mes={'有' if first_mes else '无'}"
                     if n else "A 态骨架：未内嵌条目，不合格")
            out.append(f"- 主卡 {f.parent.name}/card.json（{size}B）：{state}")
        except (OSError, json.JSONDecodeError):
            continue


def scan_work_assets(output_dir: str, repo_id: str = "") -> dict[str, Any]:
    """扫描作品已有产出，返回结构化清单。永不抛异常（扫描失败给空清单）。"""
    result: dict[str, Any] = {"output_dir": output_dir, "repo_id": repo_id}
    try:
        if not (output_dir or "").strip():
            return result
        root = Path(output_dir)
        lines: list[str] = []
        _scan_docs(root, lines)
        _scan_charfacts(root, lines)
        _scan_worldbook(root, lines)
        _scan_main_card(root, lines)
        folder = _repo_folder(output_dir, repo_id)
        if folder is not None and folder != root:
            has = len(lines)
            _scan_docs(folder, lines)
            _scan_charfacts(folder, lines)
            _scan_worldbook(folder, lines)
            _scan_main_card(folder, lines)
            if len(lines) > has:
                result["repo_folder"] = str(folder)
        result["items"] = lines
    except Exception:  # noqa: BLE001 - 扫描失败不阻断 run
        result["error"] = "scan failed"
    return result


def build_work_brief(output_dir: str, repo_id: str = "",
                     max_chars: int = 4000) -> str:
    """生成【前序产出档案】上下文文本；无产出返回空串。

    注入位置：system（每步决策可见、不被 history 压缩丢弃）——
    让模型开局就知道已有卡纲/素材/条目/主卡，在其上继续，禁止重新通读重建。
    """
    assets = scan_work_assets(output_dir, repo_id)
    items = assets.get("items") or []
    if not items:
        return ""
    body = "\n".join(items)
    if len(body) > max_chars:
        body = body[:max_chars] + "…（截断）"
    return (
        f"{_BRIEF_MARK}以下文件是本作品此前运行已落盘的产出（路径均真实存在）：\n"
        f"{body}\n"
        "执行要求：任务是在此基础上继续/修改/优化，禁止重新通读小说全文重建已有内容；"
        "需要细节时用 file.read_text 读上述对应文件（素材段/卡纲/世界书快照），"
        "不要整本 read_text 重读原文。"
    )