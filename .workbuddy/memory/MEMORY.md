# Demiurge 项目记忆

## 项目定位
- **Demiurge** = 本地优先的多智能体剧情创作、角色资料与 ComfyUI 工作流工作台。
- 把剧情对话、角色卡、世界书、RAG 记忆、状态表、自动插画、工作流搭建、模型与节点管理放进一个本地 Web 工作台。
- 默认只监听本机回环地址（127.0.0.1:8010），不上公网。本地优先、数据不进源码仓库。

## 技术栈
- 前端：React + TypeScript + Vite（组件管渲染、lib 管逻辑/状态/编排）。
- 后端：FastAPI（路由薄、业务深，services 禁止 import routers）。
- Agent：LangGraph + OpenAI 兼容接口。
- RAG：Chroma + BM25 + SentenceTransformers + 可选 Reranker。
- 图像：独立 ComfyUI 进程（HTTP/WebSocket 接缝）。
- 存储：本地仓库、会话快照（历史真源）、角色/世界书数据、运行索引。

## 关键架构契约（改代码前必读 ARCHITECTURE.md）
- 会话快照是历史真源；已删消息不得被缓存/旧 checkpoint 复活。
- 自动插画按 `messageId + slotId` 原位回填，不追加对话轮，不阻塞前台聊天队列。
- 配置/协议/清洗规则/数据真源只有一个属主；新事件须同步更新后端协议、前端解码、reducer 与双端测试。
- 密钥只进被 Git 忽略的 `backend/data/user_state.json`。

## 目录速查
- `backend/app/services/` 深模块（comfyui_client, agent_graph, worldbook, rag_*, edit_*, image_prompt_profiles, regex_engine 等）。
- `backend/app/routers/` 薄 HTTP 适配层。
- `frontend/src/lib/` 逻辑/编排（重测试）。
- `frontend/src/views/` 页面（ChatView, AIBuildView, NodeManagerView, ToolsView, CharacterCards, WorldBook, WorkflowTemplates, ModelDownload, repos, settings）。
- `docs/memory/` 大量工程决策记录（45+ 篇），是排障与历史决策的第一手资料。
- `scripts/` 发布/便携包/冻结 Runtime 自检。`release/` 分层规范。`.github/workflows/` 便携与 Runtime 发布矩阵。

## 当前状态（2026-08-07）
- 仓库 v0.1 Full RAG 便携版本；终端用户只发 Full RAG Edition（含本地 Embedding/Reranker 依赖，不含模型权重）。
- 已做大量稳定化：扮演、World/Curator、世界书/RAG、状态/纪要/七类表、后台原位插画、四类提示词 Profile、LoRA 数据、ComfyUI 与界面修复。
- 许可证尚未选定（源码公开≠自动授权）。

## Agent 工作约束（AGENTS.md）
- 只做用户明确要求；先读 ARCHITECTURE.md 与 docs/memory 相关部分再开工。
- 禁止 `git reset --hard`/`git checkout --` 等破坏性操作；搜索优先 rg。
- 改代码后同步修正文档漂移；跨层任务拆成可独立验证的小步。
- 完成前重读用户要求、检查 diff、核对验收条件。
