# 参考架构（ARCHITECTURE）

给后续加功能的人：读这一页，就知道东西该放哪、不该放哪。目标是**加功能时不破坏已立起来的接缝**。

## 一句话总览

- 后端 `backend/app`：FastAPI。**路由薄、服务深**——路由只做 HTTP 适配（收参、调服务、包异常），业务逻辑全在 `services/`。
- 前端 `frontend/src`：React。**组件管渲染、lib 管逻辑**——有状态/编排/纯算法进 `lib/`，组件只消费。
- 二者用 OpenAI 兼容接口 + SSE 通信；模型配置由前端从「设置」透传。密钥只允许落在被 Git 忽略的 `backend/data/user_state.json`，不得进入源码、日志、评估报告或发布包。

## 核心原则（改代码前先记住）

1. **深模块**：小接口，藏大行为。加逻辑时先问「调用方需要知道几件事」，越少越好。
2. **删除测试**：想删一个模块时，复杂度是消失（它是穿透层，本就不该有）还是在多个调用方重现（它在挣钱，留着）？加新模块也用这把尺子。
3. **接口即测试面**：纯逻辑（无 I/O）要能被单测。写在服务/lib 里、导出，不要埋进路由体或 React 组件闭包。
4. **单一属主**：同一份配置/协议/清洗规则只在一处定义，别处引用。

## 后端分层

```
routers/   HTTP 适配层。只做：解析请求模型 → 调 services → 把领域异常包成 HTTPException。
           ★ 不要在这里写业务逻辑、循环、文件 I/O、拼 ComfyUI 请求。
services/  深模块层。业务逻辑全在这。彼此可依赖，但不 import routers。
```

已就位的深模块（加功能时优先复用，别另起炉灶）：

| 模块 | 拥有什么 | 加相关功能时 |
|------|---------|------------|
| `comfyui_client` | 与 ComfyUI 的全部 HTTP 对话（提交/轮询/取图/打断/上传），统一 `ComfyError` | 新增 ComfyUI 交互 → 加到这里，别在路由直接拼请求 |
| `comfy_launcher` | ComfyUI **本地进程**生命周期：配置读写 + 独立解释器发现（整合包、`.venv/venv`、显式路径）+ 写 extra-paths YAML + 拉起子进程，持有进程句柄，统一 `LaunchError`。禁止回退应用 Embedded Runtime。**与 comfyui_client 是两个接缝（进程 vs HTTP），别合并** | 改启动/配置逻辑 → 这里，别在路由写 subprocess/文件 I/O |
| `workflow_submission` | ComfyUI 模板/画布提交事务：校验在线、读取、转换、注入和提交 | `/submit` 与 `/submit_graph` 路由只映射 `WorkflowSubmissionError` |
| `llm` | 建模型 + `normalize_base_url`（/v1 规则）+ `flatten_content`（分段展平） | 需要 base_url 归一或展平 LLM 输出 → 调这里，别内联 |
| `agent_graph` | 多 Agent 主编排：Supervisor 是语义分派的单一属主，模型输出 `route/confidence/alternatives`；代码只校验合法路由、附件与工具能力 | 改任务理解 → 改 Supervisor 提示；不要再增加关键词/正则路由，也不要在路由或前端复制判定 |
| `agent_context` | Agent 上下文窗口：各取 6 条、token 预算、历史文本与依赖上文的执行提示词整理 | 改上下文选择/裁剪 → 这里；`agent_graph` 不内联 token 算法 |
| `tool_agent_adapter` | 把遗留 `image_agent` ReAct 流适配成专家节点结果 | `agent_graph` 不直接依赖其长参数和事件细节；替换旧实现只改 Adapter 后面 |
| `generation_approval` | 提示词审批状态机 + 已批准的图像/视频执行 + 失败语义 | 改确认/更改/取消/重提流程 → 这里；`agent_graph` 只调用其 Interface |
| `image_gen` | 云端文生图 `/images/generations` 与带参考图 `/images/edits`，统一超时、质量参数和 64–3840px 尺寸边界 | 新增图像供应商请求规则 → 这里，调用方不拼 payload |
| `rag_backend` | RAG 基础设施 Adapter；`EmbedConfig`、OpenAI/Ollama/本地嵌入兼容、嵌入模型缓存与 Chroma 单例缓存 | 新增嵌入后端或修改缓存键 → 只改这里；上层不直接创建 Chroma/Embedding |
| `rag_store` | 普通知识库与生成资产索引：系统资料、仓库文档、generation 元数据、Hybrid 检索入口 | 新增普通知识库操作 → 加到这里，签名收 `EmbedConfig`；不得再放节点索引逻辑 |
| `rag_retrieval` | 普通知识库的纯 BM25Plus、RRF 融合；不依赖 Chroma、路由或工作流 | 修改普通 RAG 排序算法 → 这里；I/O 留给 `rag_store/rag_backend` |
| `rag_middleware` | 搭建需求的查询拆分、架构能力映射和可选 LLM 重写 | 新增模型架构/能力同义词 → 只改这里，别塞进搭建编排 |
| `node_store` | 节点索引存储：完整包管理、能力分块、迁移就绪判断、单路 Dense+BM25+RRF；分块命中按 `pack_id` 聚合 | 改 collection、分块持久化或单路召回 → 这里；不得依赖 `rag_store` |
| `node_index` | 节点索引编排：`object_info` 同步、卸载包清理、多查询加权融合、MMR、一次最终精排 | 改同步、多查询或排序策略 → 这里；安装事实仍不归它拥有 |
| `reranker` | 普通知识库和节点索引共用的可选 Cross-Encoder Adapter；拥有缓存代际、活跃推理计数和 Accelerator Handoff | 权重不完整、依赖缺失或推理失败必须返回空；ComfyUI 提交前通过其 Interface 释放显存 |
| `node_candidates` | 节点候选解析；`object_info` 是安装事实，RAG 只补候选 | 四种搭建模式统一消费该 Module，不能用 RAG 空结果判断未安装 |
| `workflow_build_turn` | 搭建回合：统一需求校验、完整历史视图、当前工作流快照、查询优化与节点候选 | 四种搭建模式必须先准备同一个 Build Turn，不能各自裁剪历史或重建查询 |
| `workflow_graph_rules` | 工作流图规则：解释 `object_info`、规整 widget、拆缺失节点、硬校验与结构审核 | 新增图规则 → 只改这里；`workflow_builder` 只消费规则结果 |
| `workflow_builder` | 完整、增量、直连和顾问四种搭建策略；模型调用、重试及结果组装 | 不得重新拥有历史整理、节点候选或图规则 Implementation |
| `chat_stream_protocol` | 对话流事件 wire 协议 v1；把 Agent 内部领域事件编码成 `protocol/version/type/data` | 新增事件先扩展这里和前端解码联合；不允许路由手拼 SSE payload |
| `sse` | SSE 传输 Adapter；分帧、异常信封与 `[DONE]` 收尾 | 只负责传输，payload 语义交给 `chat_stream_protocol` |
| `rag_evaluation` | Hit/MRR/Recall/延迟统计与 RAGAS 四字段记录构造 | 修改检索链后运行固定评估集；未运行 Judge 不得宣称生成质量合格 |
| `generation_store` | 「生成完成→留存→入库→写快照」后端管线 | 后端出图后的持久化 → 走 `persist_image` |
| `workflow_injector` | 纯注入（套值 + 提示词），无 I/O | 改注入规则 → 这里，可直接单测 |
| `image_store` / `image_utils` | 端点存图（抛异常）/ agent 存图（回退原 url） | 注意两者错误语义不同，别合并 |
| `local_media` | 本地媒体白名单、MIME、Range 校验与分块读取 | `/local-view` 只做 FastAPI Response 适配；文件读取规则进这里 |
| `pathnames` | `safe_seg` 文件名清洗单点 | 需要清路径片段 → 用它 |
| `workflow_parser` / `workflow_convert` | UI↔API 转换；`PASSTHROUGH_TYPES` 共享 | 改穿透集 → 改 parser 的共享常量；**PrimitiveNode 差异是有意的，别合并** |

路由拆分：`ai.py` 是聚合器，`include_router` 了 `ai_common`（模型请求基类 + 错误映射）/`ai_text`（单轮文本端点）/`ai_agent`（智能体 SSE）/`ai_chat`（多轮对话 SSE）。加 AI 端点 → 归到对应子路由，不要塞回一个大文件。

## 前端分层

```
components/  展示组件。收 props，渲染。可有本地 UI 态（开关/选中）。
             ★ 不要在这写持久化、SSE 编排、跨组件业务状态。
lib/         逻辑层。有状态 hook、编排、纯算法。
api/         后端调用封装 + wire 序列化。
stores/      全局状态（仓库/设置）。
types/       共享类型。
```

已就位的 lib（加功能时优先复用）：

| 模块 | 职责 |
|------|------|
| `useChatSession` | 聊天会话引擎：messages + 生成生命周期 + 三级持久化 + 全部编排。ChatView 只消费它 |
| `chatGeneration` | 生成流程的纯判定/整形：图像门（`needsImageInput`/`hasImageProvided`）+ 文本打分（`pickBestText`）+ 快照瘦身（`slimSnapshot`，persist 由调用方注入）。**从 useChatSession 闭包抽出，可直接单测** |
| `generationLifecycle` | 生成三态 reducer（idle/agent/workflow）+ 派生 selector。**新增生成状态改这里，别加影子 ref** |
| `workflowOrchestration` | 工作流输入口编排 hook（读节点→AI 出计划→写画布） |
| `viewRouting` | `parseHash`/`buildHash`/`calcSize` 纯函数 |
| `lafLock` | laf_lock 子帧 postMessage 协议原语（`lockUrl`/`postToFrame`/`isLafMessage`） |
| `opResults` | `fmtOpResults` 编排结果格式化 |
| `workflowTemplatePicker` | 工作流模板搜索、最近记录排序与 localStorage 持久化 |
| `generationPreferences` | 比例/分辨率/质量/自定义宽高，以及按仓库恢复和保存的 Hook；任意尺寸能力由图像模型配置声明 |
| `agentRecovery` | Agent SSE 断开后的后台状态与快照补偿轮询；完成后由 `useChatSession` 合并消息并刷新资产库 |
| `useResizableChatInput` | 输入框拖动、键盘调整与高度持久化 |
| `useChatMaintenance` | token 提醒、完整压缩与清缓存事务；拥有确认、快照提交和错误反馈 |
| `api/chatStreamProtocol` | 对话流事件 v1 判别联合与唯一解码入口；未知版本/事件立即失败 |
| `chatSessionEvents` | 消息归并纯 reducer；消费已解码事件并更新文本、媒体、审批与路由选择 |

api 序列化器（**加带模型配置的端点时必用，别手拆三元组**）：
- `chatBody(chat)` → `base_url/api_key/model`
- `ragEmbed(embed)` → `base_url/api_key/embed_model/embed_model_dir/reranker_model_dir`（RAG POST）
- `sseEmbed(embed)` → `embed_base_url/embed_api_key/embed_model/embed_model_dir/reranker_model_dir`（SSE）

## RAG 依赖方向

```text
普通对话/客服
  → rag_store（普通知识库与检索入口）
    → rag_retrieval（BM25Plus/RRF 纯算法）
    → reranker（可选 Cross-Encoder）
    → rag_backend（嵌入 + Chroma Adapter）

AI 搭工作流
  → node_candidates（object_info 安装事实 + 候选解析）
    → node_index（同步 + 多查询融合/MMR/精排）
      → rag_middleware（查询拆分与架构能力映射）
      → node_store（完整包/能力分块 + 单路 Hybrid）
        → rag_backend（嵌入 + Chroma Adapter）
```

禁止反向依赖：`rag_backend` 不知道知识库、节点或工作流；`node_store` 不依赖 `rag_store`；`rag_retrieval` 不依赖 Chroma。该方向由 `backend/.importlinter` 的 `rag-stack-layers` 合同执行。

节点索引包含两个 collection：`node_index` 保存完整插件包，供管理页查看和人工编辑；`node_index_chunks_v1` 保存按顶层 category、最多 12 节点的能力分块，只用于召回。只有当前全部包都有分块且没有卸载插件残留时，`node_store` 才原子切换到分块检索。

人工编辑插件包正文会把该包标记为 `manual`，并同时重建对应能力分块；后续增量或全量同步只刷新真实节点清单，不得覆盖人工正文。完整包、能力分块和就绪缓存的一致性由 `node_store` 单一拥有。

## AI 搭工作流依赖方向

```text
workflow_builder（四种搭建策略）
  → workflow_build_turn（搭建回合）
    → node_candidates（安装事实 + 候选）
      → node_index（检索编排）
        → rag_middleware（查询规划）
  → workflow_graph_rules（图规整、校验、审核）
  → workflow_merge（增量合并）
```

该方向由 `backend/.importlinter` 的 `workflow-build-stack-layers` 合同执行。`workflow_convert` 获取 `object_info` 必须经过 `comfyui_client` Adapter；转换 Module 只拥有“请求失败时使用内置映射”的降级语义。

## 对话流事件方向

```text
Agent 内部领域事件
  → chat_stream_protocol（版本化 wire 信封）
    → sse（分帧与传输）
      → api/chatStreamProtocol（前端解码）
        → chatSessionEvents（消息 reducer）
          → useChatSession（生命周期副作用）
```

新增事件必须沿该方向逐层显式实现并补双端测试。`useChatSession` 不得按原始字段猜事件类型，`api/ai.ts` 不得重新展开多类事件回调。

## 加功能决策树

- **要调 ComfyUI？** → HTTP 原语进 `comfyui_client`；模板/画布提交事务进 `workflow_submission`；路由只映射领域异常。
- **要改智能体分派/上下文/审批？** → 分派进 `agent_graph`，上下文窗口进 `agent_context`，审批与已批准执行进 `generation_approval`。
- **要调对话/生图模型？** → 复用 `llm` / `image_gen`；前端用 `chatBody` 拼请求。
- **要碰普通知识库？** → 领域操作进 `rag_store`，纯排序进 `rag_retrieval`，嵌入/Chroma 进 `rag_backend`。
- **要碰节点索引？** → 单路存储与召回进 `node_store`，同步/多查询融合进 `node_index`，安装判断只读 `object_info`。
- **要改 AI 搭工作流？** → 回合上下文进 `workflow_build_turn`，图规则进 `workflow_graph_rules`，模式差异与模型重试才进 `workflow_builder`。
- **要动生成流程/持久化？** → 后端进 `generation_store`，前端进 `useChatSession`。
- **要加页面/子帧交互？** → 组件收 props 渲染；协议走 `lafLock`；纯算法进 `lib/` 并导出。
- **写了带分支的纯函数？** → 放服务/lib 顶层并导出，配一个测试（见下）。

## 测试

```bash
# 后端（用项目 venv，系统 python 缺依赖）
cd backend && ./.venv/Scripts/python.exe -m pytest -q
# 前端
cd frontend && npm test

# 后端架构/静态门禁
cd backend
$env:PYTHONUTF8=1; ./.venv/Scripts/lint-imports.exe
./.venv/Scripts/python.exe -m mypy '@mypy_files.txt'
./.venv/Scripts/python.exe -m ruff check app/

# 发布闭包（基础离线依赖 + 主题资产）
cd ..
./backend/.venv/Scripts/python.exe scripts/release_preflight.py

# 固定 Runtime 目标矩阵与定向测试
./backend/.venv/Scripts/python.exe scripts/runtime_release.py matrix
cd backend && ./.venv/Scripts/python.exe -m pytest -q tests/test_runtime_release.py tests/test_runtime_config.py

# RAG 固定真实评估（报告写入被忽略的 data）
cd backend
./.venv/Scripts/python.exe scripts/evaluate_rag.py --output data/rag_evaluation_report.json
```

测试放：后端 `backend/tests/test_*.py`，前端与源码同目录 `*.test.ts`。
第一批已覆盖：注入/清洗/展平/base_url 归一/标签提取（后端），calcSize/buildHash/fmtOpResults/lafLock（前端），以及 `EmbedConfig`、parser↔convert 契约。**加纯逻辑就顺手补一条测试**——接缝已经就位，写测试几乎零成本。

## 主题资产生成

`scripts/theme_asset_pipeline.py` 是主题资产处理的唯一 Implementation，拥有透明清理、裁剪、缩放、WebP/PNG 编码、图集排版、引用顺序和目标路径安全。

每个主题只在 `scripts/theme_assets/<theme>.json` 声明源文件、处理参数和前端目标槽位。`process_<theme>_theme_assets.py` 只能作为兼容命令入口选择清单，不得新增图像处理函数。新增主题优先复用已有 `op/preprocess/transform`；确需新变换时只扩展 pipeline 并给所有清单复用。

## 发布架构

发布有两个独立 Module，不得互相复制 Implementation：

- `release_preflight` 拥有源码版发布闭包校验，`release.ps1` 只调用它再执行 Git 归档。
- `runtime_release` 拥有 Embedded Runtime 的目标矩阵、依赖选择、前端构建、PyInstaller 组装、Reranker 权重闭包、SHA256 清单、跨平台归档和 GitHub 资产分片。

源码版集中保证：

- `backend/requirements.txt` 在 CPython 3.10–3.14/win_amd64 的完整传递依赖可仅从 `vendor/pip` 离线解析；
- `frontend/package-lock.json` 的完整依赖可仅从 `vendor/npm` 离线解析；
- `styles.css` 引用的主题素材真实存在；
- `scripts/theme_assets/*.json` 声明的全部产物已经生成；
- 上述主题素材没有被 Git 忽略，执行 `git add -A` 后能进入 archive。

源码版不含 `backend/requirements-reranker.txt`、Embedding/Reranker 模型权重、`backend/data`、`.env` 或用户状态。可选模型依赖缺失只能让 RAG 降级，不能阻断基础应用安装。

### Runtime Release Seam

`release/runtime-targets.json` 是 Runtime Target 的单一 Interface；GitHub Actions 先通过 `runtime_release.py matrix` 读取矩阵，再为每个目标调用同一个 `build` Interface。平台判断、Torch 来源、PyInstaller 收集规则、模型版本、清单和分片不能散落到 workflow 或 PowerShell。

前端构建必须复制到 Runtime 工作目录后执行 `npm ci/build`，不得重建源码工作区的 `frontend/node_modules`；这样正在运行的 Vite 和主题开发可以与 Runtime 构建并行。

Release Edition：

| Edition | RAG 能力 | 终端用户安装 |
|---|---|---|
| `standard` | 远程/Ollama Embedding、Dense+BM25+RRF/MMR；未带本地 Cross-Encoder | 无 |
| `full-rag` | 标准版全部能力 + 固定 SentenceTransformers/Torch + 内置 Qwen3-Reranker-0.6B | 无 |

Runtime Target 目前为 Windows x64 Standard、Windows x64 Full RAG(CUDA)、macOS arm64 Standard、macOS arm64 Full RAG(MPS)、macOS x64 Standard。Intel Mac 不默认发布 CPU Full RAG，避免把无法进入交互精排的巨大依赖误称为完整版本。

`runtime_entry` 是进程启动 Adapter：只设置可写 `data/`、前端、ComfyUI 扩展和内置 Reranker 路径，再启动后端。`app.config` 消费这些路径；开发模式未设置环境变量时保持原目录语义。前端由后端同源提供，最终用户不再运行 `pip`、`npm`、Vite 或 `start-dev`。

应用 Embedded Runtime 与 ComfyUI Python 是两个不可混用的 Runtime。`comfy_launcher.find_python` 只接受用户显式路径、ComfyUI 内 `.venv/venv` 或常见整合包目录；找不到即返回可读错误。不得恢复 `sys.executable`/PATH Python 回退，否则 PyInstaller Runtime 会错误承担 ComfyUI 的节点与 Torch 依赖。

工作流模板与画布提交都汇聚到 `workflow_submission`。该 Module 在调用 ComfyUI `/prompt` 前执行 Accelerator Handoff：`reranker` 提升缓存代际并清空入口，等待活跃精排完成，再执行 GC 与 CUDA/MPS cache 释放。加载中的旧代模型即使稍后完成也不能回写缓存；下一次 RAG 查询先使用 Hybrid 结果并重新后台预热。

超过 GitHub 单资产上限的完整 RAG 包由 Module 自动切成小于 1.9GB 的有序分片并生成 SHA256 清单；`join-runtime.ps1/.sh` 只负责按清单流式还原和验签。

主题与 Runtime 保持独立 Seam：Theme Asset Pack 仍落在 `frontend/public`，Theme Runtime 仍在 `styles.css`。每个 Runtime Target 都从当前提交重新构建同一前端，因此新增或替换主题不需要修改 Runtime Target、Torch 或打包规则。

### Embedding Backend Seam

`rag_backend` 将嵌入来源拆为两个 Adapter：`_RemoteEmbeddings` 和 `_LocalEmbeddings`。`EmbedConfig.mode` 是唯一选择；缓存键只包含当前 Adapter 真正使用的字段，`reranker_dir` 不会分裂 Chroma。`model_probe` 复用 `rag_backend.local_model_files_status`，因此设置页的文件完整性判断与实际加载共享同一个 Interface。不得再让“填写了目录就自动切换”的隐式优先级回到上层路由或前端。

### Theme Runtime Seam

`styles.css` 的 production theme runtime Interface 统一拥有按钮九宫格尺寸、输入区和拖动手柄几何、模态内容层级、装饰指针隔离、进度节点定位、头像尺寸和着陆页几何。bright/night/eye-care/green/gray 主题块只提供视觉差异和素材 URL；新增主题先接入共享 Interface（把 `[data-theme="<theme>"]` 加进这些 `:is(...)` 结构选择器列表），再补自己的 Asset Pack，不得复制整段结构选择器。`themeRuntime.test.ts` 与各主题资产测试共同验证该 Seam。

## 明确不做的（别反复提议）

- 4 个空 router（runs/loras/characters/assets）+ `list_ai` 桩：已挂载=在用端点，删了缩 API 面。
- `rag_backend._norm_url` 与 `image_gen._norm_url` 同名不同义：前者归一嵌入接口，后者归一图像接口，行为不同，别合并。
- 后端 `generation_store` 与前端 `useChatSession` 两条留存管线：运行环境不同，强合并增险。
- `ai_common` 的 `build_chat_model`/`chat` 薄封装：错误映射跨 8 端点复用，是深的。
