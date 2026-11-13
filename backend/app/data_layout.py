"""卸载/清理的分档真源：`%LOCALAPPDATA%\\Demiurge` 与其 `userdata` 子目录里，每个条目属于哪一档。

三档语义（2026-11-02 用户定案，逐字口径见 `docs/ROADMAP-INSTALLER-AND-UPDATE.md`）：

- `cache` 缓存：可再生 ⇒ 卸载**总删**，不给选项。
- `settings` 设置：配置与凭据 ⇒ **仅**勾选「删除应用程序数据」时删。
- `product` 产物：不可再生（作品仓库、预设、角色卡、世界书、会话快照、RAG 索引、模型权重）
  ⇒ **任何情况都不删**。
- `container` 容器：本身不删，由它的子条目逐名定档（`userdata`）。

未登记的名字一律返回 `unknown`，而删除面只按白名单逐名取 `cache`/`settings` ⇒ `unknown` 等价于
「不删」。判据是白名单不是黑名单：新增落盘条目若忘了定档，后果是多留一个文件，不是误删用户作品。

本模块是**纯登记表**（零依赖、不读写磁盘），因此住 `app/` 根而不进 `services/`：`services` 平面有一条
形态锁禁止除属主 `repo_meta` 外出现字面量 `user_state.json`，而这份表要**点名**该文件归哪一档——
点名 ≠ 读取，把表做成 service 只会逼出豁免条款、削弱那条锁。

消费方（属主唯一，禁止各自复制名单）：
- `scripts/generate_uninstall_hooks.py` 生成 `desktop/tauri/installer/uninstall-hooks.nsh`
  （NSIS `NSIS_HOOK_POSTUNINSTALL`）。
- `backend/tests/test_data_layout.py` 完整性闸：扫后端与壳源码，凡挂在 `DATA_DIR` / 壳 home 下的
  名字都必须在这里登记。
"""
from __future__ import annotations

CACHE = "cache"
SETTINGS = "settings"
PRODUCT = "product"
CONTAINER = "container"
UNKNOWN = "unknown"

DIR = "dir"
FILE = "file"

SCOPE_SHELL_HOME = "shell-home"
SCOPE_USERDATA = "userdata"

#: 壳 home（`%LOCALAPPDATA%\Demiurge`，`DEMIURGE_SHELL_HOME` 可覆盖）直属条目。
#: 名字真源＝`desktop/tauri/src/paths.rs` 与 `runtime_delivery.rs`。
_SHELL_HOME: dict[str, tuple[str, str]] = {
    "runtime": (CACHE, DIR),  # 三层解包内容 + current.json
    "downloads": (CACHE, DIR),  # 交付分件与 .work-* 暂存
    "logs": (CACHE, DIR),  # desktop.log / runtime.log / self-test.log
    "runtime.pid": (CACHE, FILE),
    "desktop.json": (SETTINGS, FILE),  # 壳侧窗口几何与选择记忆
    "webview2": (SETTINGS, DIR),  # WebView2 用户数据目录
    "userdata": (CONTAINER, DIR),  # 逐名定档，见 _USERDATA
}

#: 卸载回执 `uninstall.log` 故意**不登记**：它由生成的钩子逐行追加，登记成缓存会让删除循环
#: 在半途删掉自己正在写的文件（open-write-close 每行一次），前面的行就丢了。未登记 ⇒ unknown
#: ⇒ 删除面不碰，正是想要的行为；由 `test_uninstall_receipt_is_deliberately_off_the_whitelist` 锁住。

#: `userdata`（安装版）/ `backend/data`（开发态）直属条目。名字真源＝后端 `DATA_DIR / "<name>"`。
_USERDATA: dict[str, tuple[str, str]] = {
    # —— 档 1 缓存：可再生
    "logs": (CACHE, DIR),  # comfyui-stdout.log 与 run_trace
    "tmp": (CACHE, DIR),
    "temp": (CACHE, DIR),  # ComfyUI 自己按 --temp-directory 建
    "numba_cache": (CACHE, DIR),
    "comfyui_node_cache": (CACHE, DIR),
    "thumbs": (CACHE, DIR),
    "wallpaper_thumbs": (CACHE, DIR),
    "task_progress": (CACHE, DIR),
    "transfer": (CACHE, DIR),  # 配置包导出/导入暂存
    "exports": (CACHE, DIR),
    "builtin_agents.json": (CACHE, FILE),  # 内置 Agent 定义的播种缓存
    "delivery.json": (CACHE, FILE),  # 壳后台补齐可选层的进度账本：壳写、后端只读投影（P3-b）
    # —— 档 2 设置：配置与凭据（勾选「删除应用程序数据」才走）
    "user_state.json": (SETTINGS, FILE),  # 设置档：settings + selection（作品索引已拆去 repos.json）
    "comfy_config.json": (SETTINGS, FILE),
    "comfy_hosts.json": (SETTINGS, FILE),
    "comfy_extra_paths.yaml": (SETTINGS, FILE),
    "capability_leases.json": (SETTINGS, FILE),
    "prompt_approvals.json": (SETTINGS, FILE),
    "regex_scripts.json": (SETTINGS, FILE),
    "illustration_prompts.json": (SETTINGS, FILE),
    "prose_style.json": (SETTINGS, FILE),
    "palette_pref.json": (SETTINGS, FILE),
    "mcp_config.json": (SETTINGS, FILE),
    "agent_plugins.json": (SETTINGS, FILE),
    "remote_backend.json": (SETTINGS, FILE),  # 手机侧联机地址
    "lan_token.txt": (SETTINGS, FILE),  # 凭据：scripts/start-lan.ps1 落盘
    "privacy_denylist.txt": (SETTINGS, FILE),  # 本机专名清单，含真名，必须随设置清除
    # —— 档 3 产物：不可再生
    "repos.json": (PRODUCT, FILE),  # 作品索引（repo_id→名称/父子）：2026-11-02 P1 从设置档拆出来
    "chat_snapshots": (PRODUCT, DIR),
    "chat_archives": (PRODUCT, DIR),  # 删除前归档（软删层）：清缓存/压缩/清空覆盖掉的历史，恢复对话用它
    "chat_facts": (PRODUCT, DIR),
    "chat_revisions.json": (PRODUCT, FILE),
    "chat_initiative.json": (PRODUCT, FILE),  # 主动聊天冷却（按 thread_id）
    # 2026-11-13 补登记（P6-C 聊天态新落点）：先按「不可再生产物」保守定档 ⇒ 卸载不删；
    # 是否把摘要/状态改判 cache/settings 待用户裁决，猜错档误删用户数据比多留一个文件严重。
    "chat_checkpoint_images": (PRODUCT, DIR),  # checkpoint 内联图片外置，旧会话回填要用
    "chat_context_summaries": (PRODUCT, DIR),  # 线程级滚动上下文摘要
    "chat_followup.json": (PRODUCT, FILE),  # 追问／续聊冷却与计数
    "chat_read.json": (PRODUCT, FILE),  # 已读状态
    "scenario_snapshots": (PRODUCT, DIR),
    "checkpoints.db": (PRODUCT, FILE),
    "app.db": (PRODUCT, FILE),
    "assets.db": (PRODUCT, FILE),  # 现无代码写入者；历史资产主表，按不可再生处理
    "chroma": (PRODUCT, DIR),  # RAG 索引（用户定案：算产物）
    "assets": (PRODUCT, DIR),
    "attachments": (PRODUCT, DIR),
    "workflows": (PRODUCT, DIR),
    "templates": (PRODUCT, DIR),
    "prompt_templates": (PRODUCT, DIR),
    "comfyui-input": (PRODUCT, DIR),
    "comfyui-output": (PRODUCT, DIR),
    "models": (PRODUCT, DIR),  # 权重
    "backgrounds": (PRODUCT, DIR),
    "wallpapers": (PRODUCT, DIR),
    "agent_knowledge": (PRODUCT, DIR),  # 用户固化的技能/知识文档
    "flow_docs": (PRODUCT, DIR),
    # 以下六项 P0 暂按「不可再生 ⇒ 产物」归类，用户可否决（否决只改本表一处）
    "agents.json": (PRODUCT, FILE),
    "skills.json": (PRODUCT, FILE),
    "procedure_skills.json": (PRODUCT, FILE),
    "scenario_experiments": (PRODUCT, DIR),
    "build_sessions": (PRODUCT, DIR),
    "_archive_agent_knowledge": (PRODUCT, DIR),  # 现无代码写入者；旧知识库归档
}

SCOPES: dict[str, dict[str, tuple[str, str]]] = {
    SCOPE_SHELL_HOME: _SHELL_HOME,
    SCOPE_USERDATA: _USERDATA,
}

#: 这些 `settings` 文件的 `.bak-*` 同族备份也按设置处理（勾选时一起删）。
#: `user_state.json` 拆分前的备份里可能还带着作品索引副本，那不是「产物被删」的通道：活索引在
#: `repos.json`（产物档，任何删除都不碰），副本随设置一起清掉反而是对的（它含凭据）。
#: 反过来 `repos.json.bak-*` **故意不登记** ⇒ `unknown` ⇒ 删除面不碰：产物档的备份仍是产物。
SETTINGS_BACKUP_STEMS: dict[str, tuple[str, ...]] = {SCOPE_USERDATA: ("user_state.json",)}


def tier(scope: str, name: str) -> str:
    """条目的档位；未登记返回 `unknown`（删除面据此不动它）。"""
    entry = SCOPES[scope].get(name)
    return UNKNOWN if entry is None else entry[0]


def kind(scope: str, name: str) -> str | None:
    """条目形态（`dir`/`file`）；未登记返回 None。"""
    entry = SCOPES[scope].get(name)
    return None if entry is None else entry[1]


def deletion_names(scope: str, *, include_settings: bool) -> list[tuple[str, str, str]]:
    """该 scope 里应当删除的条目，按名字排序返回 `(name, tier, kind)`。

    产物、容器与未登记条目永远不进来——这是删除面的唯一入口，NSIS 生成脚本只能用它。
    """
    wanted = {CACHE, SETTINGS} if include_settings else {CACHE}
    entries = [(name, meta) for name, meta in SCOPES[scope].items() if meta[0] in wanted]
    return sorted((name, meta[0], meta[1]) for name, meta in entries)
