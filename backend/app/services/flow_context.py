"""current_flow_doc：会话/作品「当前流程文档」句柄（2026-09-04 §3 设计 A）。

固化链（口述设计 → 服装参考 md → 出图清单文档 → 固化01 生图）跨轮/跨会话续跑时，
"接着/继续/基于刚才" 这类指代容易断——聊天缓存既不便宜也不该承担"记忆"。本模块把
**最近一次由智能编造写下的流程文档**按 repo_id 持久化为轻量句柄：

- mark_doc：doc.create_repo / file.write_text 等 handler 成功后登记（{kind, path, step,
  updated_at}，每 repo 保留最近 MAX_DOCS 条）；
- current_doc / resume_hint：智能编造节点在本轮用户输入像"延续"时，把句柄注入编译
  /自由循环上下文，让模型先读该文档再行动（纯机械、零额外 LLM 调用）；
- 存储：DATA_DIR/flow_docs/<repo_id>.json（gitignored 运行态）；repo_id 做安全校验，
  拒绝路径穿越；写盘原子替换。登记失败只跳过，绝不影响文档写入本身。

与角色卡/世界书/状态表正交：只登记"文档类"产物句柄，不碰剧情状态。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

FLOW_DOCS_DIR_NAME = "flow_docs"
MAX_DOCS = 6  # 每 repo 保留最近 6 条（防无限膨胀）
_TS_ISO = "%Y-%m-%dT%H:%M:%S%z"


def _root() -> Path:
    from app.config import DATA_DIR  # 调用时取属性，便于测试 monkeypatch

    return Path(DATA_DIR) / FLOW_DOCS_DIR_NAME


def _safe_key(repo_id: str) -> str:
    """repo_id 安全化：非空、无路径成分/盘符/点前缀，用作文件名。"""
    key = str(repo_id or "").strip()
    if not key or key.startswith("."):
        raise ValueError("非法 repo_id（空或隐藏前缀）")
    if any(ch in key for ch in ("/", "\\", ":", "\x00")):
        raise ValueError("非法 repo_id（含路径成分）")
    if key == ".." or key.endswith(".."):
        raise ValueError("非法 repo_id")
    return key


def _state_path(key: str) -> Path:
    return _root() / f"{key}.json"


def _read(key: str) -> list[dict]:
    try:
        raw = _state_path(key).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []


def _write(key: str, docs: list[dict]) -> None:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    target = _state_path(key)
    fd, tmp = tempfile.mkstemp(dir=str(root), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(docs, fh, ensure_ascii=False)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mark_doc(repo_id: str, *, kind: str, path: str, step: str) -> dict:
    """登记一条流程文档句柄；返回最新条目。kind 建议 doc_repo / md_file。"""
    key = _safe_key(repo_id)
    entry = {
        "kind": str(kind or "doc").strip(),
        "path": str(path or "").strip(),
        "step": str(step or "").strip()[:120],
        "updated_at": time.strftime(_TS_ISO),
    }
    docs = [d for d in _read(key) if d.get("path") != entry["path"]]
    docs.append(entry)
    docs = docs[-MAX_DOCS:]
    _write(key, docs)
    return dict(entry)


def current_doc(repo_id: str) -> dict | None:
    """返回最近一条流程文档句柄；无则 None。repo_id 非法按无处理（读路径不抛）。"""
    try:
        key = _safe_key(repo_id)
    except ValueError:
        return None
    docs = _read(key)
    return docs[-1] if docs else None


def clear(repo_id: str) -> None:
    key = _safe_key(repo_id)
    try:
        _state_path(key).unlink(missing_ok=True)
    except OSError:
        pass


def resume_hint(repo_id: str, user_text: str) -> str:
    """本轮输入像「延续上一流程」且有登记句柄时，返回提示文本；否则空串。

    提示不含 LLM 调用：读句柄 + 判断是否延续语（agent_context.is_context_dependent），
    命中则把上次的文档路径/类型/步骤交给模型，让模型先读文档再行动。
    """
    if not str(user_text or "").strip():
        return ""
    try:
        from app.services.agent_context import is_context_dependent

        if not is_context_dependent(str(user_text)):
            return ""
    except Exception:  # noqa: BLE001 - 判断失败宁可不注入
        return ""
    doc = current_doc(repo_id)
    if not doc:
        return ""
    kind = str(doc.get("kind") or "doc")
    path = str(doc.get("path") or "")
    step = str(doc.get("step") or "")
    if not path:
        return ""
    ts = str(doc.get("updated_at") or "")[:19]
    label = "流程文档" if kind == "doc_repo" else "文档"
    parts = [f"【当前{label}句柄】本作品/会话上次写下的 {label}：{path}"]
    if step:
        parts.append(f"（经 {step}")
        if ts:
            parts[-1] += f"，{ts}"
        parts[-1] += "）"
    parts.append("若本轮是在延续该流程，先读取/引用它再行动；若已换任务请忽略。")
    return "\n".join(parts)
