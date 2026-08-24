# Demiurge 项目记忆

## 项目定位
- **Demiurge** = 本地优先的多智能体剧情创作、角色资料与 ComfyUI 工作流工作台（127.0.0.1:8010）。
- 整合剧情对话、角色卡、世界书、RAG 记忆、状态表、自动插画、工作流搭建、模型与节点管理。
- 本地优先，数据不进源码仓库。

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
- **工作流卡片节点展示 = 早期机制（唯一正确，勿再被旧文档带偏）**：N 个选中节点 → N 个独立 ComfyUI iframe（`NodeCard`），各自 `keepOnly` 单节点 + `request_graph` 刷新校验到只剩自己 + `node_size` 自适应大小；禁止改成「单共享 iframe 截图切割」（`SharedNodePreview`/方案 B）。

## 目录速查
- `backend/app/services/` 深模块（comfyui_client, agent_graph, worldbook, rag_*, edit_*, image_prompt_profiles, regex_engine 等）。
- `backend/app/routers/` 薄 HTTP 适配层。
- `frontend/src/lib/` 逻辑/编排（重测试）。
- `frontend/src/views/` 页面（ChatView, AIBuildView, NodeManagerView, ToolsView, CharacterCards, WorldBook, WorkflowTemplates, ModelDownload, repos, settings）。
- `docs/memory/` 工程决策记录（45+ 篇），排障与历史决策第一手资料。
- `scripts/` 发布/便携包/冻结 Runtime 自检。`release/` 分层规范。`.github/workflows/` 便携与 Runtime 发布矩阵。

## 当前状态（2026-08-24）
- 仓库 v0.1 Full RAG 便携版本；终端用户只发 Full RAG Edition。
- Canvas Mode 已落地：`generate` WorkMode 由 `CanvasView` 接管；@xyflow/react v12 自定义节点/连线/MiniMap/框选/右键/吸附辅助线；`canvas.json` 布局持久化；灵感卡四类自动导入+右键插入；工作流工具卡 `/w` + `WorkflowToolModal`；画布输入折叠小球可拖动。
- **画布投影真源契约（2026-08-24 强化）**：画布节点一律从「对话内容」实时投影，不依赖一次性事件/仅 canvas.json 持久化——
  - 生成内容节点：`filterGensByConversation` 按 `conversationUrls`（内存 messages 媒体 ∪ 后端历史媒体）过滤 generation_store；首次挂载必须先拉历史 URL 再投影（否则首进画布空白，2026-08-24 修复）。
  - 工作流模板节点：`projectWorkflowTools(messages)` 从对话 workflow 消息投影（同 templateId 去重，id=`wftool-<templateId>` 稳定）；重启后对话历史恢复 → 节点自动出现（2026-08-24 修复：原机制依赖一次性事件+canvas.json，重启即消失）。
  - 剧情节点：`projectStoryNodes(messages)` 从对话 assistant 剧情文本消息投影（每楼层一节点，id=`story-<messageId>`）；正文渲染跑显示层正则 markdownOnly（depth=0，折叠类生效、隐藏远层楼层不生效——用户拍板：单节点=单楼层，不应受隐藏楼层影响）。
  - 工具卡/剧情节点 id 统一稳定派生（不再随机 uuid）：布局位置以稳定 id 存 canvas.json，重启后投影可对上。事件机制降级为补充源（按 templateId 去重）。
- **画布实时投影契约（2026-08-23）**：任何新产出落库路径必须同步刷新消息来源（`onGenerated`→`reloadFromSnapshot` 或 `laf-generation-saved` 时重拉 `/ai/chat/history`），否则新节点要整页刷新才出现。
- **画布/弹窗运转超时合同（2026-08-23）**：一律复用 `pollWorkflowResult`（内部 `pollSchedule`：图片 5min 释放忙碌/20min 硬上限，视频 15min/60min），禁止硬编码短超时；`still_running` 表示 ComfyUI 仍在跑，必须保留「生成中」占位节点不派发 `laf-canvas-wf-done`；弹窗内自带进度条（modal-mask 会盖住页面级进度条）。
- 画布节点导入规则（用户拍板）：资产库不自动导入；世界书/角色卡有绑定才自动导入；对话实际产出自动导入。
- WorkflowCard 节点展示已回退早期机制（2026-08-23 修复）：对话卡片渲染 N 个 `NodeCard` 纵向排列（每节点独立 iframe）；`handleDone` 逐节点从 `laf-node-{msgId}-{nid}` 独立 iframe `request_node` 抓参；`SharedNodePreview`（单共享 iframe + request_thumb 截图）已整段删除。
- 角色/插画：Krea2 Profile 单段英文；LoRA 加载按 `single`/`multi` 合同；角色外貌从 `<status>` `[在场]` 精确读取；四 Profile 最终正文移除原姓名。
- 门禁参考：前端 vitest 67 files 473 passed，后端 ruff ✅，生产构建 ✅。

## Agent 工作约束（AGENTS.md）
- 只做用户明确要求；先读 ARCHITECTURE.md 与 docs/memory 相关部分再开工。
- 禁止破坏性 git 操作；搜索优先 rg。
- 改代码后同步修正文档漂移；跨层任务拆成可独立验证的小步。
- 完成前重读用户要求、检查 diff、核对验收条件。
- **动用户已定机制/做要求范畴外的事前，必须先征得用户同意**；交接文档若与用户原意冲突，以用户原话为准，先核对代码再动手（2026-08-23 工作流卡片机制被旧文档带偏的教训）。