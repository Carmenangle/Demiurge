"""固化知识库（DATA_DIR/agent_knowledge/*.md）只读服务 + 注入模式配置。

智能编造 Agent 的流程规范/映射表固化目录：agent_graph 每次会话启动时扫描（技能
文档按需装载 / 普通知识常驻注入，见 agent_graph._knowledge_catalog_text）；本模块
是同一目录的唯一属主——目录名/上限常量、元数据列举、正文读取与注入模式配置都
收敛在这里，供对话注入与设置页「固化知识库」展示共用同一份真源。

真源目录: DATA_DIR/agent_knowledge/（gitignored 运行态，仅 *.md）。
注入模式配置: DATA_DIR/user_state.json settings.agentKnowledge（gitignored 运行态）。
"""
from __future__ import annotations

import json
from pathlib import Path

# ── 目录与上限常量（唯一属主：对话注入与设置页展示共用）────────────────────────
KNOWLEDGE_DIR_NAME = "agent_knowledge"
KNOWLEDGE_MAX_FILES = 4          # 单次注入最多携带的常驻文档数（防上下文爆炸）
KNOWLEDGE_PER_FILE_CHARS = 20000  # 单文档注入/预览的字符上限

# 注入模式（A3 设置页开关）：smart=技能目录+按需 load_doc（默认）；always=全量常驻（老行为）
MODE_SMART = "smart"
MODE_ALWAYS = "always"


def _root() -> Path:
    # 延迟导入：允许测试 monkeypatch config.DATA_DIR 后按调用时取值。
    from app.config import DATA_DIR
    return DATA_DIR / KNOWLEDGE_DIR_NAME


# ── 技能头（frontmatter）解析：三固化等流程规范带 skill 元数据 → 按需装载 ────
# 参考 deepseek harness SKILL.md 约定（name/触发描述/工具清单）；无头文档仍作
# 普通常驻知识（agent_graph 目录注入见 _knowledge_catalog_text）。

SKILL_META_FIELDS = ("skill", "whenToUse", "tools")
_HEAD_META_CHARS = 4096  # frontmatter 只会在文件头几 KB 内，读头解析即可


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """剥文档头 YAML（--- … ---）。返回 (元数据, 正文)；无头或格式坏 → ({}, 原文)。"""
    meta: dict = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key not in SKILL_META_FIELDS:
            continue
        value = value.strip().strip('"').strip("'")
        if key == "tools":  # 只支持单行 [a, b, c]
            value = [t.strip().strip('"').strip("'") for t in value.strip("[]").split(",")
                     if t.strip().strip('"').strip("'")]
        if value:
            meta[key] = value
    return meta, text[end + 4:].lstrip("\n")


def _meta_of(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(_HEAD_META_CHARS)
    except OSError:
        return {}
    meta, _ = _split_frontmatter(head)
    return {key: meta[key] for key in SKILL_META_FIELDS if key in meta}


def _truncated_by_chars(path: Path) -> bool:
    """文件是否超出单文档字符上限（只读上限+1 字符即可判定，避免整读大文件）。"""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(KNOWLEDGE_PER_FILE_CHARS + 1)
    except OSError:
        return False
    return len(head) > KNOWLEDGE_PER_FILE_CHARS


def list_docs() -> list[dict]:
    """按文件名升序列出固化知识文档元数据（与 agent_graph 历史注入顺序一致）。

    返回 [{name(文件主名), file(完整文件名), size(字节), mtime(毫秒), truncated}]；
    目录缺失或为空返回 []。单文件超出字符上限时 truncated=True。
    """
    root = _root()
    if not root.is_dir():
        return []
    docs: list[dict] = []
    for path in sorted(root.glob("*.md")):
        try:
            stat = path.stat()
        except OSError:
            continue
        doc = {
            "name": path.stem,
            "file": path.name,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime * 1000),
            "truncated": _truncated_by_chars(path),
        }
        meta = _meta_of(path)
        if meta:
            doc.update(meta)  # skill/whenToUse/tools：技能文档才有；普通知识无这些键
        docs.append(doc)
    return docs


def read_doc(name: str) -> dict:
    """按文件主名或完整文件名读取正文，返回 {name, file, size, mtime, content, truncated}。

    只允许 agent_knowledge/ 下的 *.md；拒绝路径穿越（../、绝对路径、盘符、
    子目录）。文件缺失/非法名分别抛 FileNotFoundError / ValueError。
    带 frontmatter（skill）的文档 content 只回正文（去头），并附 skill/whenToUse/tools；
    无头普通文档行为不变（content=全文）。
    """
    root = _root()
    if not name or name.startswith(".") or "/" in name or "\\" in name or ":" in name:
        raise ValueError("非法知识文档名")
    candidate = root / name
    if candidate.suffix != ".md":
        candidate = root / f"{name}.md"
    # 解析后再校验一次仍在根目录内（防符号链接/编码绕过）
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except OSError as exc:
        raise FileNotFoundError(name) from exc
    if resolved.parent != root_resolved or resolved.suffix != ".md":
        raise ValueError("非法知识文档名")
    if not resolved.is_file():
        raise FileNotFoundError(name)
    try:
        stat = resolved.stat()
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FileNotFoundError(name) from exc
    meta, body = _split_frontmatter(text)
    truncated = len(body) > KNOWLEDGE_PER_FILE_CHARS
    doc = {
        "name": resolved.stem,
        "file": resolved.name,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime * 1000),
        "content": body[:KNOWLEDGE_PER_FILE_CHARS],
        "truncated": truncated,
    }
    if meta:
        doc.update({key: meta[key] for key in SKILL_META_FIELDS if key in meta})
    return doc


# ── 注入模式配置（A3 设置页开关，user_state 运行态真源）───────────────────────
# settings.agentKnowledge = {mode: "smart"|"always", always_docs: [主名…]}
# smart（默认）：技能文档目录注入 + 命中场景 knowledge.load_doc 拉全文；
#               always_docs 里的技能文档逐份回退为常驻全量注入；
# always：全量常驻注入（老行为，KNOWLEDGE_MAX_FILES 生效）。


def _user_state_path() -> Path:
    from app.config import DATA_DIR
    return DATA_DIR / "user_state.json"


def injection_config() -> dict:
    """读取注入模式配置；缺失/坏档 → {}（调用方按 smart 缺省处理）。"""
    try:
        state = json.loads(_user_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cfg = (state.get("settings") or {}).get("agentKnowledge")
    return cfg if isinstance(cfg, dict) else {}


def save_injection_config(cfg: dict) -> dict:
    """读-改-写 user_state.json 的 settings.agentKnowledge（保留其它 settings 键）。"""
    path = _user_state_path()
    state: dict = {}
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    if not isinstance(state, dict):
        state = {}
    settings = dict(state.get("settings") or {})
    settings["agentKnowledge"] = {
        "mode": MODE_SMART if str(cfg.get("mode") or MODE_SMART) != MODE_ALWAYS else MODE_ALWAYS,
        "always_docs": [str(name) for name in (cfg.get("always_docs") or [])
                        if str(name).strip()],
    }
    state["settings"] = settings
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings["agentKnowledge"]