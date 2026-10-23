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

## 架构深化进度（2026-08-11 起，滚动追加）

1. **工作流抓取事务（已完成）**：`workflowCapture` 统一拥有完整画布载入、单次帧请求、可选 ops、原生 `graphToPrompt` 抓取、来源校验、重试、超时与清理。`WorkflowCard`、`AIBuildView` 共用同一 Interface；卡片不再实现隐藏 iframe 事务。模板工作流是拓扑真源，单节点 iframe 只回传参数；捕获结果必须保留真源全部节点，残缺旧草稿自动回退模板，禁止单节点画布覆盖完整图。
2. **会话生成生命周期（已完成）**：`generationLifecycle` 以正交的 Agent/ComfyUI 槽保存前台状态：Agent 完成后对话立即可继续，不能因仍在运行的工作流而排队；任一槽完成不得清掉另一槽。前端发送入口用同步 busy ref 封住 React reducer 尚未生效的同帧连续发送竞态；已有活动 Agent 或持久队列时，新输入必须入队。异步快照瘦身只能回写其启动时的同一版消息，禁止旧数组覆盖期间新到的正文。`agent_runner` 无论最终持久化、记忆或 Trace 是否失败都必须在最外层 `finally` 释放 thread 并发送 SSE 终态。`workflowGenerationRuntime` 统一拥有多个 ComfyUI pending 的持久化、提交时仓库归属快照、轮询节奏、连续 `not_found`、恢复检查、超时保留和并发 finalize 双闸。后台活动面板只读 pending，不得删除生成真源；原图或会话槽未持久化时必须保留任务重试。
3. **剧情回合事务（已完成）**：`roleplay_turn` 统一拥有主生成 → 状态/插画写回 → 输出正则 → 正文与插画请求提前发布 → Curator/纪要/表格维护 → Agent 终态的顺序。ComfyUI 收到请求后独立运行；Agent 只在维护完成后释放同仓库下一轮，避免下一轮读取旧表格、纪要或世界书。`agent_graph` 保留路由及上下文装配。
4. **Agent 调用事务（已完成）**：前端 `AgentInvocation` + `agentInvocationBody` 统一编码即时 SSE 与后台队列的完整上下文；后端 `agent_request_context.from_payload` 统一把 HTTP 请求和队列 payload 转为 `RunContext`。角色卡、世界书、历史、代理、插画和模型字段只映射一次。
5. **工作流端口编排（已完成）**：`workflow_port_planner` 统一拥有编排提示词、模型调用、JSON 容错、强制编排、模型名校验、LoRA 和色板增强顺序；`ai_text` router 只做请求适配和错误映射。
6. **会话历史事务（已完成）**：`ConversationHistoryRuntime` 统一拥有编辑、删除、重生成裁剪、检查点恢复、导入重载的状态发布与持久化顺序，保证 React 状态、同步 ref、本地缓存和后端快照不分叉。
7. **AI 搭建工作区生命周期（已完成）**：`BuildWorkspaceRuntime` 取代浅层 `useBuildSession`，统一拥有会话代际、最近会话、自动保存资格和后台终态任务认领；任务仅在会话成功回载后标记完成，临时失败可以重试。
8. **聊天与表格展示编排（已完成）**：`useChatAgentQueue`、`useChatPresentationAssets`、`useChatUnreadTracker`、`useChatTransfer` 和 `useTableWorkflows` 分别拥有队列、头像表情、未读、导入导出和表格工作流；`useChatSession`、`ChatView`、`TableModal` 只连接状态、依赖与展示。
9. **双端 wire 合同（已完成）**：`shared/schemas/agent-invocation.schema.json` 是 Agent 调用字段真源；`scripts/generate_wire_contracts.py` 生成并校验 TypeScript/Python 类型，生成物随源码发布，运行时不依赖生成器。
10. **真实浏览器恢复门禁（已完成）**：Playwright 在隔离端口运行真实前端、模拟 API 与 ComfyUI，覆盖刷新恢复、后台生成跨刷新可见及 `messageId + slotId` 原位替换。
11. **Full RAG 可重复发布（已完成）**：Base/Application/RAG 内容寻址；RAG 顶层依赖和完整传递依赖锁分离，平台 Torch 仍由目标矩阵固定。便携启动默认 8010，并允许 `DEMIURGE_PORT` 在验收或端口冲突时覆盖；Windows x64 已从正式归档全新解压并通过启停、HTTP 和 Full RAG 自检。
12. **结构化输出 Runtime（深化完成）**：`structured_output.invoke` 统一拥有 native JSON Schema → 单次 legacy fallback → Pydantic 校验 → `structured.output` Trace 的完整调用事务；Supervisor 真实生产调用已经穿过该 Interface，Provider 只有显式提供 `structured_chat_fn` 才启用原生约束，禁止猜能力和双花 Token。
13. **完整快照事务与性能（深化完成）**：`scenario_lab` v2 先写暂存目录再原子发布；SQLite backup 连接显式关闭，未变化非数据库文件复用上一 manifest 的哈希，多候选分支中途失败回滚已创建 payload 与 Chroma。前端分支编排进入 `scenarioBranchRuntime`，`AppBody` 只负责 Adapter 和提示。
14. **RAG/纪要写事务（深化完成）**：generation 重试和批量文档导入由 `rag_store` 单一拥有；纪要替换导入在一个 SQLite 事务内完成，任何条目失败都保留旧纪要。router 不再拥有重试、循环写入或先删后写顺序。
15. **扩展模块依赖门禁（已完成）**：import-linter 从 13 条增至 17 条，新增 Structured/Replay、Scenario/Procedure、Visual/Lease 和 Continuity 方向合同；领域词典由根目录 `CONTEXT.md` 作为稳定真源。
16. **产物版本快照与产物卡收紧（已完成）**：`artifact_versions` 在 fabric done 时把当前产物组自动存档到 `作品根/_versions/<seq>-<时间戳>/`（sha256 去重），支持版本列表/回档（回档前先预存档当前状态，双向可逆）/删除中途版本；预览弹层新增「版本历史」区块（查看/回档/删除）。同步收紧：仅 `done` 终局下发 artifacts（中间「需要批准/执行未完成」不再携带），前端 autoLoad 只对完成态消息兜底补卡。`_versions` 目录必须被 `collect_artifacts` 排除，常量唯一属主在 `collection_artifacts`（VERSIONS_DIR），`artifact_versions` 反向引用。
17. **能力参数环境注入与卡归属（已完成，2026-09-09）**：计划可编排能力的 `base`/`repo_id`/`output_dir`/`cwd` 由**运行环境注入，模型传值作废**——清单唯一属主 = `capability_registry.ENV_INJECTED_OPS` + `ENV_PARAM_SOURCES` + `inject_env_params`；三处归一（`plan_tasks.submit_task` / `plan_compiler` / `fabric_loop`）共用，执行层 `plan_tasks._env_params_for_handler` 按 handler **真实签名**兜底补齐（计划来自编译/配方重放/fabric 任一来源都不漏；回归含自动一致性断言——handler 必需参数命中 `ENV_PARAM_SOURCES` 就必须登记）。卡目录归属由 `character_store` 的 `_work.json` 标记唯一表达（落卡能力写入），`character.embed_worldbook` / `workspace.export_to_library` 只处理「归属匹配」或「卡名=作品名」的卡，其余跳过——**禁止 glob 全库 `*/card.json`**（作品库根多作品混放时会跨作品污染）。
18. **消息前缀缓存 static-first（2026-09-12，P0–P4）**：Agent 每一步都把「system + 已有对话 + 工具结果」整段重发，前缀逐字节稳定才能吃到上游自动前缀缓存。合同落在两处：roleplay 主循环装配见 `docs/tech-manual/A-roleplay-illustration/02-backend-roleplay-engine.md`「prompt 前缀缓存与 usage 口径」；fabric 自由循环见 `B-agent-orchestration/09-backend-autopilot.md` §3.6 与其红线 11。铁律：**`messages[0]`/静态 system 恒为静态、动态内容（进度卡/时间戳/文件大小）一律走尾部临时消息；`messages` 是原始日志、append-only，压缩只追加替换声明（`_append_surface_replace`）并在**每个发送点**投影（`_project_messages`）；工具集冻结**。`cached_tokens` 是上游粗粒度上报（P0 观测期曾恒为同一个数），**禁止按字节反推命中率**。产物：usage 三口上报 + 逐字节稳定性回归（`tests/test_fabric_loop.py`）。
19. **上下文合同：任务会话登记（2026-09-11 P1，09-12 补产物指针）**：讨论/委派型任务的「接续」从**猜词**改为**查登记**——`task_session_store`（命名空间 `task_sessions`，复用 `task_progress_store`，TTL 6h，一线程一活跃任务）记 `created_intent`/`materials`/`product_paths`，接续轮先 `active(thread_id)` 重建材料，`plan_compiler` 的词表判定降为兜底（实锤：一句「不用 ComfyUI 的提示词…」无任何讨论动词 → 词表全不命中 → 掉回 roleplay、附件与规范注入整段失效）。**必须与 `fabric_checkpoints` / `recipe_offers` 物理隔离**——混进 checkpoint 命名空间会让对话里的「批准」被 `_fabric_approval_word` 当成别的语义授权去追加授权。
20. **能力租约过期语义（2026-09-14 实盘死循环修复）**：`capability_sandbox.grant_operation` **拒绝给已过期租约追加授权**（此前只查「存在+未撤销」→ 追加"成功"→续跑 `authorize` 查过期再拦 → 批准死循环）；新增 `lease_registered`（在册判定，允许已过期）与 `renew`（只延 `expires_at`、**不扩授权清单**）。批准分支现在：过期 → `renew` 救活 → 追加授权 → 续跑且**断点保留**；租约彻底不在册（后端重启后内存租约未恢复）才删断点并明示重新发起。
21. **fabric 交付前置自检 + 提示词卡结构规范（2026-09-14）**：`fabric_loop._SYSTEM` 静态段新增【交付前置自检】——关键缺口先出「待确认清单」（逐项 + 建议默认值 + 理由）再交付，无缺口交付末尾注明代做假设；配套知识库《设计讨论-角色服装提示词规范》补 §3.1 提示词卡显式结构（纯文本平铺「正面提示词：/负面提示词：」、五件套逐件成句+材质+hex）、§4 量化下限（≥6 语义段 ≥150 字、负面词不得省略）、§5 未定细节先过目。静态段改动属 prefix-cache 安全区（static-first）。
22. **灵感卡多图插入契约（2026-09-14）**：`InspirationAttachment.imageUrls` **只装用户手动勾选的图**（无勾选才回退卡内全图）；发送时逐张进图片栏（`insertImage`），卡片附件只留文本语义（防重复携带）；「插入对话」按钮计数只认手动勾选（自动预勾仅服务「保存素材库/发送画布」）。
23. **卡归属收口：done 前内嵌 + 前序产出档案（2026-09-14 实盘修复）**：`fabric_loop` 的 done 前世界书内嵌兜底此前用 `Path(output_dir).glob("*/card.json")` 扫**作品库根下每张卡**（实盘 `userdata/pictures/` 根下并列 妈妈娼馆(40 条)/御仙(59 条)/玫瑰与繁花(36 条)，而快照 59 条 ⇒ 前两张会被整体覆盖）——正是 #17 明令禁止的写法；同源的只读扫描 `fabric_work._scan_main_card(root=库根)` 会把别的作品的卡当成「本作品前序产出」写进 system 上下文。两处统一收口到**归属判定单一属主 `character_store.owned_card_dirs(base, repo_id)`**：候选卡集合 = `[d / "card.json" for d in owned_card_dirs(output_dir, repo_id)]`，无命中即跳过；档案侧改为逐卡目录 `_scan_one_card`，不再对库根 glob。内嵌判据 `len(_emb) < len(_wb_entries)` 保持保守（本次不扩行为，避免覆盖用户手工增补的条目）。**残留（有意未修）**：`_scan_charfacts(root=库根)` 仍读 `<库根>/_prep/charfacts/*.txt`——该 `_prep` 无归属标记，属「作品域解析多副本」同族问题。
24. **域/设置属主收口（2026-09-14 架构审计 S1+S2）**：① `fabric_loop._local_chat_config` 用 `Path(__file__).resolve().parents[1] / "data"` 拼出 **`backend/app/data/user_state.json`（该文件不存在）** ⇒ `read_text` 恒抛错被吞 ⇒ 混合模式（本地改写 / 云端 400 降级本地决策）**全链静默失效**；真源改 `config.DATA_DIR`（与 `capability_handlers._embed_config_from_state` 同源），回归 `test_fabric_loop.py::test_本地聊天模型配置读DATA_DIR真源` 锁死（**已红检**；当前配置 9 个 chatModels 全云端零本地 ⇒ 潜伏 bug，修前修后现网行为相同）。② `artifact_versions._domains` 去掉跨模块 import 私有名 `collection_artifacts._domains_of`，改调**公开属主** `repo_meta.artifact_domains`（行为等价，真实库 7 个 repo_id 对拍全等）。**域解析口径下修**：4 处不是 4 份独立实现，而是「1 真属主 + 3 薄包装」，空 `repo_id` 两种口径（HTTP `[]` vs 原语 `[root]`）是 2026-09-10 明文定案、**非缺陷**。**未收口（暂缓，纯卫生、正确性零收益）**：`user_state.json` 9 处自读合属主（S3–S5）。
25. **卡号载荷唯一出口（2026-09-14 架构审计 C6）**：纪要卡号 `T<层>-<位序>` 的位序是**读路径派生值**（不落库），必须回填后序列化才有意义。此前 `/narrative/`、`/narrative/search`、`/narrative/export` 三个出口**各自**记得调 `narrative_store.with_sequence`——09-13 的 `T1-13` 事故就是 `/narrative/` 漏调一次（store/service 单测全绿也照漏）。现收口为 `narrative_store.dump_entries(base, repo_id, items, *, include_sealed=False)`：**唯一允许产出 `card_id` 的出口**（内部先 `with_sequence` 再序列化），三个出口一律改调它，路由层不再自建 DTO。回归：`tests/test_narrative_store.py`（回填位序 / 导出走全量序含封口 / 空库三条）+ `tests/test_narrative_router.py`（spy 断言三出口都走 `dump_entries`，且 `not hasattr(router, "_dump")`）。
26. **插画提交参数对象化（2026-09-14 架构审计 C7）**：`useChatSession.submitIllustration` 原为 **17 个位置参数**，失败槽重试快照按签名序存数组、「重新生成」用**魔术下标 `args[7] = "manual"`** 翻 `source`——签名插一个参数就静默错位，且 TS 不报错（数组按 `Parameters<...>` 展开）。现收成单一入参 `IllustrationSubmit`（17 字段）；快照即该对象、重试按名覆盖 `{ ...snapshot, source: "manual" }`；跨层传递的 `retryArgs` 在 `chatSessionEvents.markMediaSlotFailed` / `useChatSession.discardFailedIllustration` 声明为 **`unknown` 不透明快照**（消费方不做形状假设）。**红线：入参 `prompt` 与编译链重写的局部 `prompt` 必须分离**（解构为 `promptIn` 再 `let prompt`）——写成 `const { prompt } = input` 会让五处重赋值触发 `tsc` TS2588，而 vitest 走 esbuild 只剥类型、跑绿也发现不了 ⇒ **`npm run build` 是这条的必过关**。回归 `frontend/src/lib/useChatSession.contract.test.ts`（源码形态锁 + const/let 分离 + 快照不透明两侧）。
27. **表格行级 op 事务接缝（2026-09-14 架构审计 C5）**：`routers/table.py` 的 `rows`/`update`/`delete` 三个端点此前各自手写 `load → apply_ops → if n: save`，「无变化不落盘」这条口径散在路由里（服务层不够深）。现收口为 `table_store.mutate(base, repo_id, ops)`：读 → 应用 op → **有变化才原子落盘**，返回 `(最终表列表, 成功条数)`；路由只剩「拼 op + 回吐表」，`400` 校验仍留在路由。回归 `tests/test_table_store.py`（有变化才落盘 / 无变化不建文件 / 空 base 静默 / 串行多 op 一次落盘）+ `tests/test_table_router.py`（三端点行为 + **spy 断言都走 `mutate`**，防有人改回路由层内联编排）。并发注记：`save` 已是 `tmp.write_text → tmp.replace` 原子写（防读到半截 JSON），无锁只意味着两个并发写可能丢一次更新——单用户本地串行请求下风险极低，本轮**有意不引入锁**（审计 C5 结论：不修没有 bug，收口只为服务变深）。
28. **画布生成生命周期单一属主（2026-09-15 C4-A Step 1–6 收口）**：画布（`CanvasStageFlow.runWorkflowTask`）与弹窗（`WorkflowToolModal.handleRun`）此前**各自重写**「提交→进度→轮询→归档」整条链（自持 `pollWorkflowResult` + `finally` 兜底 + 直调 `api/comfyui.finalizeGeneration`），与对话侧 `WorkflowGenerationRuntime.start` 构成**两个属主**：同一条 pending 有两条归档路径，而画布那条**零判重**（`finalized`/`cancelled` 是 runtime 实例内集合）——后果被后端幂等键（`_workflow_key(thread_id, prompt_id)`）与前端 `upsertMessages` 按 id 合并吸收，残留代价只是「重复动作 + 状态机污染」。收口后：**判定核心唯一属主**＝`workflowGenerationRuntime`（`advanceWorkflowTick` 纯函数；两个停顿口径 `first_pending` 与 `created_at` 都保留，否则不是零行为变化）；**两个门面**＝`start(input, observer)`（后台守望，画布/弹窗用；内部 `track` 登记 pending，后台活动标记随之成为 `start` 的内部动作）与 `pollWorkflowResult`（同步 `await`、**不写 pending**，只留给对话首尾帧路径——它必须 `await` 取 `result.images[0]` 当另一帧底图）。归档统一到对话同一条 `finalizeGeneration`（画布经必填 prop `onFinalizeWorkflow` 注入，用 ref 持有以免进投影 effect 依赖）⇒ 「入库失败」不再有假 toast（旧实现谎报「稍后会自动入库」并立刻删 pending，而后端**无后台收集器**），改为 runtime 保留 pending 自动重试。终态表抽成纯函数 `frontend/src/lib/canvasWorkflowRun.ts` 的 `canvasRunEffects`（5 终态 → 事件序列，**只有 `timed_out` 保留占位且不派发 done**；漏派发 `done` ⇒ 画布占位与 `ChatView.canvasWfRuns` 永久残留）。顺带修 R8（`deriveWorkflowMediaType` 按抓取图节点类名推导 mediaType——画布、弹窗、对话 `/s` 出图、重新生成四处；默认 image、只向上升级；`SaveAnimatedWEBP`/`LoadVideo`/`VideoHelperSuite` 不升级）、R9（`inspect()` 重 `start` 必须转发 `mediaType`/`runId`/`baseSlotRef`，否则刷新恢复退图片 20 分钟超时）、R10（弹窗同一 `laf-canvas-wf-run-graph` 重复派发两次）。**两条接线红线**：① 画布/弹窗**不得**再出现 `pollWorkflowResult`（源码真源断言在 `frontend/src/lib/useChatSession.contract.test.ts`）；② 帧路径**不得**改成 `start`（会登记 pending ⇒ 刷新恢复时被当对话任务收工）。已删 dead export `trackCanvasWorkflow`/`untrackCanvasWorkflow`（其 JSDoc 自称「画布运转自持轮询」，Step 3 后已失真）。**残留（未修）**：运转进行中关闭弹窗再重开 ⇒ 新实例守卫为假、可再次提交（要修需给 pending 加「工具卡」标识并在挂载时恢复，涉及存储 schema）。
29. **前端画布「投影占位 / 事件订阅 / 节点投影」接缝收口（2026-09-16 T2.1-A/B1/B2/C/D1/D2）**：`frontend/src/views/CanvasStageFlow.tsx`（3346 行 / 99 hook）单文件同时承载「投影编排 + 6 个订阅 effect（8 个 `addEventListener` 调用点）+ 1 处元素级 `toggle` 委托 + 视图装配」，而该组件**没有任何组件测试**（架构审计 R5）⇒ 拆分必须先补「真事件派发」行为网。按三个接缝外移，**只搬收口**（判定逻辑与用户可见文案 / 超时 / 错误码 / 落盘格式零改动）：
   ① **占位节点构造** → `frontend/src/lib/canvasPlaceholderNodes.ts`（174 行；`streamingPlaceholderPrompt` / `buildStreamingPlaceholderNodes` / `buildDraftInputNodes` / `buildWfRunNodes`，`WfRun`/`PendingInput` 两个组件内局部 type 一并迁属主；`streamingPosRef.current` 改为显式入参 `streamingPos` ⇒ 纯函数不再隐式依赖组件 ref）。
   ② **画布 wf 四事件订阅**（`laf-canvas-wf-run` / `-run-graph` / `-progress` / `-done`）→ `frontend/src/lib/useCanvasWfEvents.ts`（108 行；列表增删仍归 `lib/wfRunList`，**effect 依赖仍是 `[placePlaceholder]`**）。
   ③ **其余 5 处订阅 + 元素级委托** → `frontend/src/lib/useCanvasEventBridge.ts`（258 行；`useDetailToggleTracking` / `useCanvasGenerationSaved` / `useCanvasWorkflowToolCreate` / `useCtxMenuOutsideDismiss` / `useCanvasInspirationDrop`，hook 调用点**原地替换**原 effect ⇒ effect 执行顺序不变）。
   ④ **节点投影 / 合并判定 / 连线派生** → `frontend/src/lib/canvasProjection.ts`（721 行；`buildContentNodes` / `buildToolCards`（三源合并 + 同模板去重）/ `buildStoryNodes` / `buildInspirationNodes` / `buildReferenceImageNodes` / `mergeProjectedNodes`（**纯函数**：保留选中与 ReactFlow 实际坐标/父子关系、内容未变返回 **`prev` 原引用** ⇒ React 不重渲染不闪）/ `buildCanvasEdges`（id 集合**返回**给调用方写 ref）；**机器逐字搬运**，投影 effect 只剩组装）。
   ⑤ **小型模态覆盖层**（编辑介绍文字 / 新建灵感卡 / 组重命名 / 删除确认 / 连线引用）→ `frontend/src/components/canvas/CanvasDialogs.tsx`（236 行；**挂载判定留在视图**（`{cond && <Xxx/>}`），外部符号以**同名 props** 注入 ⇒ JSX 体逐字不变；`DeletePendingDialog` 文案三分支与 `onConfirm` 副作用顺序、`EdgeInfoDialog` `promptOf` 取词顺序逐字保留）。
   ⑥ **大型覆盖层三件**（右键菜单 / 编辑灵感卡 / 节点详情面板）→ `frontend/src/components/canvas/CanvasContextMenu.tsx`（267 行，26 props：节点分类删除五分支、参考图/组删除的 ref 同步与 `persistNow`、「删除组（保留内容）」断父链转绝对坐标）/ `CanvasDialogs.tsx` 追加 `InspirationEditDialog`（236 → 358 行；渲染期取数 `editCard`/`editImage` 随体迁入；`replaceInspirationImage` 走隐藏 file input 的 ref 原生 `click`）/ `frontend/src/components/canvas/CanvasDetailPanel.tsx`（241 行，10 props：剧情 92vw 大窗两栏、正文 `runScripts → renderMarkdown` 管线、`detailHtmlRef` 只挂剧情分支 bot-html）。方法同 ⑤。
   主文件 **3346 → 2677 → 2560 → 2105 行**（投影 effect 581 → 498 → 58；返回区 5 个小型 + 3 个大型模态覆盖层随 D1/D2 外移；lucide 只剩视图自用三个图标，`AudioPlayer`/`renderMarkdown`/`runScripts` 死 import 已清）。**D2 附加红线**：详情面板剧情左列媒体是**并行条件挂载**（video / image / audio 行各自判定，非互斥「优先级」——行为网建模错成互斥会误报）；`detailHtmlRef` 只在剧情分支挂上（非剧情夹具下恒 null）。**红线（防「顺手统一」）**：每个 hook 的 **effect 依赖数组就是它的行为面（重订阅时机）**，逐字保持改造前的取值（`[detailStoryText]` / `[refresh, loadHistoryUrls]` / `[]` / `[ctxMenu]` / `[findFreeSpot, persistNow]`）；四处保留形态不得整理 —— ① 详情面板 `dataset.detailsBound` 守卫 + **刻意不解绑**（监听随该 DOM 元素生命周期）、② `laf-generation-saved` 的 `alive` **只护挂载路径**（事件回调**不**检查它）、③ 工作流工具去重是「画布节点 ∪ 本地队列」**两处都查**且 id 稳定派生 `wftool-<templateId>`、④ 右键菜单监听是**捕获**阶段（`capture: true`，去掉后行为网抓不到——元素事件照样冒泡到 window）。
   **C 附加红线**：`mergeProjectedNodes` 的末条 `&& o.data === n.data` **不可删**（删了「运转工作流」首次点击静默失败——`nn` 是旧数据）；剧情楼层**必须**跳过正在生成的消息（`streamingId`）、首次投影**接替**生成中占位的原位并置 `placeholderConsumedByStoryRef`。
   回归：七份接缝测试共 **172 例**（`canvasPlaceholderNodes.test.ts` 8 / `useCanvasWfEvents.test.tsx` 13 / `useCanvasEventBridge.test.tsx` 17 / `canvasProjection.test.ts` 50 / `CanvasDialogs.test.tsx` 49（D2 追加 `InspirationEditDialog` 8 行为例 + 搬移/形态锁 3 例；原 5 组件锁随 D2 加严） / `CanvasContextMenu.test.tsx` 18 / `CanvasDetailPanel.test.tsx` 17）；受控红检 **5 + 6 + 7 + 9 + 9 + 9 道**（D2 九道全中预测；其中「依赖数组收窄」「去掉 `, true`」**行为网全绿、只有源码形态锁变红**；C 的 M2 首轮 **0 红** ⇒ 补强用例取值；D2 文案变异必须**内部变异**——`toContain` 子串断言与 needle 子串计数对「加后缀」免疫）；前端全量 **110 文件 / 1193 例 0 失败** + `tsc --noEmit` 0 + `vite build` 0。画布合同见 `docs/tech-manual/F-frontend/14-frontend-canvas.md` §5（红线 16 / 17 / 18 / 19）。
30. **对话引擎「插画提交链」巨函数外移（2026-09-16 T2.2-A）**：`frontend/src/lib/useChatSession.ts`（2577 行）内 `submitIllustration` 全链（原 L1081–1636，约 555 行：preset 解析、`resolveMediaSlot` 槽位认领、LoRA 元数据校验与触发词、视频/首尾帧模板判定、ComfyUI 提交、`pollResult` 轮询与槽位回填、失败快照 retryArgs）是引擎最大单块。**先补特征测试再动**（`useIllustrationChain.test.tsx` 19 例），再**机器逐字搬移** → 新属主 `frontend/src/lib/useIllustrationChain.ts`（642 行，自定义 hook，**单一 deps 对象注入 14 个闭包依赖**：settings/templates/repo/threadId/cardNames/promptProfile/characterBaseImages/chat/modelProxies/effectivePersona/messagesRef/setMessages/pollResult/discardFailedIllustration）；主文件 **2577 → 2026 行**（其余函数与 3 个调用点 `submitIllustration`/`retryIllustration` 留视图重试链）。`Model` 类型 type-only 回引属主（前端无 import-linter 循环契约）。既有 `useChatSession.contract.test.ts` 锁**改指新属主并加严，不删**。回归：新属主行为网 19 例 + contract 全绿；受控红检 **9 道全中预测**（M9 修正：快照语句变异同时命中 contract C7 形态锁 ⇒ 预测 2 红）；前端全量 **111 文件 / 1212 例 0 失败** + `tsc --noEmit` 0 + `vite build` 0。对话引擎合同见 `docs/tech-manual/F-frontend/12-frontend-chat-engine.md` §6。

31. **对话引擎「计划任务实时上屏」订阅外移（2026-09-17 T2.2-B1）**：`frontend/src/lib/useChatSession.ts`（2026 → **1970 行**）内「计划采集产物逐张实时上屏」的 `useEffect`（8 行语义注释 + 48 行体）**逐字节搬移** → 新属主 `frontend/src/lib/usePlanTaskFeed.ts`（91 行，自定义 hook，**5 名同名 deps 注入**：`threadId` / `recoveryActiveRef` / `agentBusyRef` / `activeThreadRef` / `setMessages`；`fetchSnapshot`、`deletedMessageTombstones`、`subscribePlanTaskActivities`、`pickFreshAssistantMedia` / `appendUnknownMessages` 由新模块自带 import）。主文件同步清掉 **2 条死 import**（这三个符号已无其它消费方），行集合差 `A\B` **恰 2 行**（即那两条 import，无内容丢失）——与「读 diff 相信」不同，这是可证伪的纯搬移核验。**三条红线**：① **effect 依赖数组 `[threadId]` 就是本接缝的行为面**（＝重订阅时机），逐字保持，不得「顺手补全依赖」；② 调用点必须留在视图内**原 effect 的位置**（hook 调用序 = effect 执行序）；③ 轮询节奏（`2000ms`；`settleLeft` running=60 / 终态=6）与 `laf-generation-saved` 的 `detail: threadId` 派发条件逐字保留（该事件在视图另有 3 处**既有合法**派发点 ⇒ 形态锁只钉「新属主恰一次」，不要求视图缺席）。回归 `frontend/src/lib/usePlanTaskFeed.test.tsx`（**18 例**：行为网 12〔订阅/解绑、无本会话任务不轮询、running 60 轮、终态 6 轮、忙碌与切走**顺延不丢扫尾**、**拉快照挂起期间切走不得污染当前线程**、快照经 `appendUnknownMessages` 上屏、墓碑过滤、`laf-generation-saved` 派发与 `seenIds` 去重、卸载清理〕＋ 等价矩阵 1〔10 组 activities 网格，断言 0/6/60 三个分支全部踩到，防空转〕＋ 形态锁 5）；**受控红检 6 道**（M4「视图调用点参数互换」与 M5「依赖数组收窄为 `[]`」**行为网全绿、只有形态锁变红**——又一次实证「行为网抓不到分层回退」；M3 首轮 **0 红** 是变异选错：tick 首行早退已覆盖同语义 ⇒ 补「挂起期间切走」用例后才 1 红）。前端全量 **112 文件 / 1230 例 0 失败** + `tsc --noEmit` 0 + `vite build` 0。手册见 `docs/tech-manual/F-frontend/12-frontend-chat-engine.md` §2 / §5 / §6。

32. **对话引擎「在途生图任务恢复」effect 外移（2026-09-17 T2.2-B2）**：`frontend/src/lib/useChatSession.ts`（1970 → **1941 行**）内「进入仓库/切回时恢复『进行中的生图任务』」的那段（1 行语义注释 + `resumedRef` = `useRef<Set<string>>` + 34 行 `useEffect`，共 36 行）**逐字节搬移** → 新属主 `frontend/src/lib/useWorkflowResume.ts`（73 行，自定义 hook，**7 名同名 deps 注入**：`threadId` / `loadedRef` / `workflowRuntime` / `workflowObserver` / `settings` / `discardFailedIllustration` / `cancelForegroundRun`；`comfyRegenerationUrl` 由新模块自带 import）。职责不变：等本仓库消息加载完（最多 40×100ms）后遍历 `workflowRuntime.list()`，按 `recoveryAction` 分派 —— `skip` 跳过；`expire` 先查一次 ComfyUI history，只有仍 `watching`（真在跑）才交给失败槽 `resume_expired` 并出队，`not_found` 则放行；其余（`inspect`）只查一次。**三条红线**：① **依赖数组 `[threadId]`（含其上 `eslint-disable-next-line react-hooks/exhaustive-deps`）就是本接缝的行为面**（＝重订阅时机）——`workflowObserver` / `settings` 每渲染都是新对象，一旦「顺手补全依赖」恢复链会每次渲染重跑（重复 inspect、重复登记）；② `resumedRef` **必须跨 effect 重订阅保留**（同一 `prompt_id` 只处理一次）⇒ 随 hook 一起迁入，不得改为 effect 内重建；③ 40×100ms 等加载轮询、过期任务「**先 `inspect` 再判 `watching`**」的顺序、以及 `cancelForegroundRun(p.prompt_id)` **位于 `if (p.target)` 之外无条件出队**（无落点的遗留任务也必须出队，否则该 pid 永远留在前台队列 ⇒ 后续出图不订阅进度 WS）逐字保留。回归 `frontend/src/lib/useWorkflowResume.test.tsx`（**20 例**：行为网 14〔等加载就绪前后、40 轮上限、空队列、`skip` 不登记、`inspect` 只查一次、`watching` 归档 + 先查后判顺序、`not_found` 放行、**无落点仍无条件出队**、多条逐条且登记在查 history 之前、出图地址推导与回落、等加载期卸载、history 挂起期卸载不得归档、**换 settings/observer 不重跑而换线程才重跑**、**resumedRef 跨重订阅保留**〕＋ 等价矩阵 1〔9 组 `action × outcome × 落点` 的副作用签名网格，断言 `none` / `cancel` / `discard+cancel` 三分支全部踩到，防空转〕＋ 形态锁 5）；**受控红检 7 道全中预测**（M5「视图调用点实参互换」**行为网与等价矩阵全绿、只有形态锁变红**——根因是**结构性的**：行为网直接 mount hook 并注入自建 deps，根本不经过 `useChatSession` 的调用点，故任何只改 VIEW 调用点的变异天然无感；类型系统也只拦反向那一半，因为「少参函数」可赋给「多参函数」类型）。既有 P22 接线锁**改指新属主并加严，不删**（`cancelForegroundRun(` 由「视图恰 3 处」改为「视图 2 + 新属主 1 = 3」，并新增**新属主内不得出现裸 `workflowRuntime.cancel(`** 的反向锁）。前端全量 **113 文件 / 1250 例 0 失败** + `tsc --noEmit` 0 + `vite build` 0。手册见 `docs/tech-manual/F-frontend/12-frontend-chat-engine.md` §2 / §5 / §6。

33. **对话引擎「会话加载链」effect 外移（2026-09-17 T2.2-B3-b）**：`frontend/src/lib/useChatSession.ts`（1941 → **1767 行**）内「进入仓库/切换时加载消息」的 `useEffect`（1 行语义注释 + **162 行**体，原 L426–587，其后锚点 `// APPEND_HERE`）连同模块顶层 `loadOpeningMessage`（13 行）**逐字节搬移** → 新属主 `frontend/src/lib/useSessionLoad.ts`（246 行，自定义 hook，**21 名同名 deps 注入**：`threadId` / `chatKey` / `repo` / `setMessages` / `loadedRef` / `messagesRef` / `recoveryActiveRef` / `activeThreadRef` / `recoveryTokenRef` / `agentBusyRef` / `bgRunningRef` / `abortRef` / `scenarioSnapshotTurnRef` / `snapTimer` / `wsUnsubRef` / `workflowRuntime` / `startAgentRecovery` / `settings` / `openingCardName` / `effectivePersona` / `dispatch`；其余 16 个自由符号由新模块自带 import）。职责不变：三级兜底（`home` 草稿区 → localStorage 轻量快取 → 后端快照 `restoreSubmittedMediaSlots` + `pruneUnsubmittedMediaSlots` → langgraph 历史 + 卡 `first_mes` 开场白）→ **无条件**接切回竞态短轮询（8×2500ms；仍在 running 转完整后台恢复）。**前置刀 B3-a**：`homeDraft` 原是本视图的模块级 `let`，被四个消费方共享（加载链读 / 持久化 effect 写 / `ConversationHistoryRuntime.persist` 回调写 / `clearHome` 清）⇒ 先抽成 `lib/homeDraft.ts` 单例（**32 行**）+ `read/write/clear` 访问器（ESM import 绑定对导入方**只读**，视图与 hook 各持一份裸 `let` 会状态分裂），`useChatSession` 1941 → 1938。**四条红线**：① **依赖数组 `[threadId]` 就是本接缝的行为面**（＝重订阅时机）——`settings` / `repo` 每渲染都是新对象，补进依赖会让整条三级兜底反复重跑；② 三级兜底的**顺序与早退闸**逐字保留——`maybeStartBgPoll` 必须**无论快照是否有内容都执行**（放在兜底 early-return 之后＝后台化失效），且「成功返回的空快照是历史真源、仅请求失败才允许本地缓存兜底」这条判据同时是「本地已渲染就早退」与「否则继续兜底到历史」的闸（取反＝整条下游链改道）；③ 卸载清理按序复位 refs / 关进度 WS / `dispatch({ t: "reset" })` 逐字保留；④ 调用点必须留在视图内**原 effect 的位置**（hook 调用序 = effect 执行序）。回归 `frontend/src/lib/useSessionLoad.test.tsx`（**29 例**：行为网 23〔home 草稿区 4；三级兜底 10，含**成功空快照清本地并落历史**、孤儿槽 `resume_unsubmitted` 上报、单条摘要补图、卡 `first_mes` 宏替换、**换 settings·repo 引用不重跑而换线程才重跑**；切回竞态短轮询 7，含 **未增长即稳定不再轮询**、8×2500ms 上限、挂起期卸载不续轮；卸载清理 2〕＋ 等价矩阵 1〔8 组 `isHome × 快照请求状态 × 本地命中` 落点网格，断言 homeDraft / local / snapshot / history 四分支全踩〕＋ 形态锁 5）；另 `frontend/src/lib/homeDraft.test.ts`（**8 例**：行为网 5 + 形态锁 3，读访问器改指新属主并加严，不删锁）。**受控红检 7 道全中预测**，还原后两文件 sha1 逐字节一致（`seam 5B0A1479…` / `view ECFCF776…`）；M3「本地兜底判据取反」一变红 7 处（该判据是双语义闸门 ⇒ 用例须覆盖闸门两侧）；M7「视图调用点实参顺序对调」**行为网与等价矩阵全绿、只有形态锁变红**——行为网直接 mount hook、注入自建 deps，不经 `useChatSession` 调用点，结构性盲区同 B2。前端全量 **115 文件 / 1287 例 0 失败** + `tsc --noEmit` 0 + `vite build` 0。手册见 `docs/tech-manual/F-frontend/12-frontend-chat-engine.md` §2 / §3.1 / §5 / §6。

34. **对话引擎「持久化链 + 进度分片」外移（2026-09-17 T2.2-B4）**：`frontend/src/lib/useChatSession.ts`（1767 → **1705 行**）内两处收口同时外移。**B4-a 进度分片**：模块级 `persistedWfProgress` / `persistedWfNode` 两条 `Map`（含 7 行 P18 语义注释）+ 组件内两条 `useState` + 两条包装 setter（写分片 + setState）+ 3 行切线程重对齐 `useEffect` **逐字节搬移** → 新属主 `frontend/src/lib/useWfProgressShard.ts`（**46 行**，`useWfProgressShard(threadId) => { wfProgress, wfNode, updateWfProgress, updateWfNode }`，**1 名 deps**）；**B4-b 持久化链**：`persistDataUri`（11 行含注释）+ `slimSnapshot`（1 行）+ 「消息变化时持久化」`useEffect`（2 行注释 + 41 行体，600ms 防抖 + 两道 `canCommitSnapshot` 闸）**逐字节搬移** → 新属主 `frontend/src/lib/useSessionPersist.ts`（**112 行**，**15 名同名 deps 注入**：`threadId` / `chatKey` / `repo` / `settings` / `messages` / `setMessages` / `loadedRef` / `messagesRef` / `activeThreadRef` / `agentBusyRef` / `bgRunningRef` / `snapTimer` / `scenarioSnapshotTurnRef` / `streamingId` / `regeneratingIds`；其余 8 个自由符号由新模块自带 import）。职责不变：`loadedRef` 闸（加载完成前连防抖 timer 都不排）→ `home` 只写 `homeDraft` 内存草稿区并 early-return → 600ms 防抖 → 第一道闸（后台任务 / 流式 / 重生成进行中 / 快照链失效）→ `await slimSnapshot`（`data:URI` 落盘转 local-view 小地址）→ **第二道闸**（Agent 接管 / 后台任务 / 快照链失效；守「图片落盘期间又追加消息 / 开始 Agent / 切仓库」）→ localStorage 写**轻量快照**（`capturedGraph` 置 null）＋ 后端写**全量** → 按 `turn` 抬水位触发 `createScenarioSnapshot`（失败回滚 `turn-1`）→ 瘦身有差异才回写内存。**六条红线**：① 依赖数组**逐字保持**（含 `settings.outputDir` / `repo?.id` / `regeneratingIds.size` 三个**成员表达式** —— 故注入 `settings` / `repo` **整对象**而非拆标量）＝重订阅时机＝行为面；② 防抖 600ms 与**两道**闸的前后位置逐字保留（删第二道 ⇒ `await` 期间的并发改动不再被拦）；③ `home` 分支必须 **early-return**（去掉即同时落 localStorage / 后端）；④ 包装 setter 必须**先写分片再 setState**、分片读写必须带 `threadId` 键（P18：退回单例即跨仓库串）；⑤ 重对齐 effect 依赖保持 `[threadId]`（同一线程内重渲染不得覆写进度）；⑥ 两处调用点必须留在视图内**原位置**（hook 调用序 ＝ effect 执行序）；加上⑦「落点判定要连**副作用痕迹**一起断言」——`skip-loading`（闸前早退、连 timer 都不排）与 `none`（timer 排了但闸后早退）**写点都是 0**，只判「有没有写」分不开，须另取痕迹（此处 `snapTimer.current === null`），且 `commit` 本身就是**双写**（localStorage + 后端快照），不可写成「落点唯一」。回归 `frontend/src/lib/useWfProgressShard.test.tsx`（**10 例**：行为网 6〔首挂载读分片 / 包装 setter 双写 / 切线程重对齐 / 双向不串 / **跨卸载重挂载保留** / 重挂载读的是当前值〕＋ 形态锁 4）与 `frontend/src/lib/useSessionPersist.test.tsx`（**22 例**：行为网 18〔加载闸、`home` 草稿区、600ms 防抖与写点内容（本地瘦身 vs 后端全量）、防抖合并、第一道闸三连、第二道闸含 **`await` 期间被推进的真实竞态**、回写与无谓回写抑制、超额吞错、回合水位抬升与失败回滚、重订阅时机（换 `settings` 无关字段不重跑 / 换 `outputDir` 才重跑）〕＋ 等价矩阵 1〔**16 组** `loaded × isHome × 第一道闸 × 第二道闸` 落点网格〕＋ 形态锁 3）；`homeDraft.test.ts` / `usePlanTaskFeed.test.tsx` / `useChatSession.contract.test.ts` 三处**既有锁随之改指新属主并加严**（改指新属主、补锚点存在断言，**不删锁**）。**受控红检 10 道全中预测**（M1 7／M2 4／M3 7／M4 3／M5 3／M6 3／M7 3／M8 **1·纯形态锁**／M9 2·形态锁／M10 **1·纯形态锁**——后三道是「行为网抓不到」的结构盲区：调用点实参顺序对调、分层回退、删恒真守卫），还原后三文件 sha1 逐字节一致（`view 8561D842…` / `shard 42C1FAA3…` / `persist DA63725A…`）。门禁：`tsc --noEmit` 0 / 前端 **117 文件 1321 例 0 失败** / `vite build` 0。

35. **引擎域路径统一：新增唯一属主 `repo_meta.engine_repo_dir`（2026-09-18 ③）**：修一处**数据损坏级路径错轨**。引擎族（state / tables / chronicle / 事实 / 认知 / CI / 世界观快照 / 世界书会话）一度用 `Path(base) / safe_seg(repo_id, strip=False)`（**UUID 目录**），而 chat / 图 / 文档 / 画布 / 卡用 `repo_meta.folder_name()`（**仓库名文件夹，保中文**）——同一作品两套落点。决定性差异在 `safe_seg` 把非 `[A-Za-z0-9._-]` 字符（含中文）换成 `_`，而 `safe_dir` 保留中文 ⇒ `safe_seg(uuid) ≠ folder_name(uuid)` **永不重合**（必须迁移）；而 `home` / 纯 ASCII 名时二者**完全重合**（零迁移）——这就是迁移分支的判据。磁盘实证：`凌霄仙母录` 的 `state.json` 分裂成两份（引擎处 `turn=36` / 仓库名处 `turn=21`，另有 `SAVE01/` 旧扁平第三份）。**修法**：新增**唯一路径属主** `repo_meta.engine_repo_dir(output_dir, repo_id) -> Path`，12 处调用点（11 个模块）全部改指，迁移逻辑与路径口径**单点唯一**。口径与 `repo_folder_path` 一致但**只算路径、不写 marker**（读路径零副作用，与建目录的 `repo_folder` 分工）：有仓库名 → `<根>/<仓库名>`；子仓库 → `<根>/<父名>/<子名>`；**无仓库名（home / 无记录）或无 base → 退回 `safe_seg(repo_id)`（与老口径逐字节一致 ⇒ 零迁移）**。惰性迁移：老口径目录有数据且新位置无数据 → 整目录 `rename`；**新位置已有数据 → 绝不覆盖**（防一个分叉的陈旧副本盖掉活跃真源）→ 老目录挪到 `<新位置>/.engine_legacy_<旧段>/` 留档；幂等；`OSError` 静默留痕不阻断。**四条红线**：① **「无仓库名退回 `safe_seg`」是本刀能安全落地的**唯一**前提**——大量既有测试以 `str(tmp_path)` 作 base、`folder_name("r1")` 查 `user_state.json` 查不到 ⇒ 退回老口径 ⇒ 既有测试全部保持绿色（零回归）；若改成「无仓库名也走仓库名段」会一次性打爆全套 P2/P3 契约；② 老新重合时短路（`new_path == root / seg`）**行为上冗余但仍保留**（`folder_name` 对无仓库名本就返回 `safe_seg`，且老目录不存在时下方 `legacy.is_dir()` 已早退 ⇒ 红检 M12 实测 0 红）——留作**显式意图声明**与「`folder_name` 对 home 语义变更」的护栏，不依赖隐式巧合；③ **新位置已有数据绝不 rename 覆盖**（红检 M3/M11：覆盖或丢弃留档各红 1）；④ **12 处必须全部改指同一属主**（源码形态锁扫 `app/services/*.py`，剥注释后正则 `Path\(base\)\s*/\s*safe_seg\(\s*repo_id\s*,\s*strip=False\s*\)` 必须零命中）——散改会重新长回错轨。回归 `backend/tests/test_repo_engine_paths.py`（**22 例**：属主口径 5〔中文仓库名 / 子仓库嵌父 / home 与无记录退回 UUID 段 / 空 base 或空 repo / 有仓库名但无 base〕、迁移 4〔旧有数据新无 → 整目录搬 / 新已有数据 → 不覆盖且留档 / 同名备份目录不重复搬且不抛 / 幂等 mtime 不变〕、重合 1、**跨模块同域参数化** 2〔5 个有独立路径函数（character_state / narrative_store / table_store / manual_table_fill / worldbook_store）＋ 4 个只有 `_connect` 的 DB 模块（temporal_fact_store / character_belief / narrative_ci / visual_preference，真建库后 `PRAGMA database_list` 取实际落盘路径核对）〕、`visual_preference` 读 visual_ci 同域 1、世界书会话同域 1、**源码形态锁** 1）；**受控红检 12 道**（M1 5 ／ M2 2 ／ M3 1 ／ M4–M9 各 2 ／ M10 1 ／ M11 1 ／ M12 **0·确认冗余**；M8 首轮锚点写错常量名（`REPO_SNAPSHOT_FILE` 实为 `REPO_WORLDBOOK_FILE`）命中 0 次 ⇒ 修锚点，非锁失灵）。⚠ **门禁盲区（本刀发现）**：`mypy_files.txt` 契约清单**不含** `repo_meta.py` / `manual_table_fill.py` / `worldbook_store.py` / `agent_context_assembly.py` 四个本次改动的文件（清单刻意只收零错误文件）⇒ **本刀核心新码 `engine_repo_dir` 不在 mypy 覆盖内**。已单独按文件跑 mypy 核对：报错仅 6 条、**全部落在未改动函数**（`repo_meta` L408/416 的 chromadb 重写块、`worldbook_store` L374、`agent_context_assembly` L503/550/702），与改动行**交集为空**；未扩清单（会逼出无关清理＝范围蔓延），此为**已知口径**、非回归。门禁：`ruff check app tests` 0 / `mypy '@mypy_files.txt'` **49 文件** clean / `lint-imports` **17 kept, 0 broken** / 后端全量 **3055 例 0 失败 0 错误 0 跳过**（junitxml 判绿；较基线 3033 **+22** ＝ 新测试文件）。

36. **生图提示词「前端可改」配置属主：新增 `illustration_prompt_store`（2026-09-18 · 高潮点判据 LLM 可行性验证）**：多元数据插入的「高潮点锚点」此前只有**同轮 `<illustration>` 计划 + 中文关键词表**两级（`scene_illustration.fallback_illustration_anchor`）。本刀插入**中间一层 LLM 判据**：同轮缺计划时先问一次「哪一句之后适合出图」（判据＝**呈现度**，即该句之前的文本是否足以构成一帧画面），拿不到结果就退回关键词表（关键词表**零改动**）。**新增唯一配置属主** `backend/app/services/illustration_prompt_store.py`：当前只暴露 `climax_anchor` 一条（视频/生图提示词模板**有意未开**，待可行性验证后再议）；落盘 `DATA_DIR / illustration_prompts.json`，读用 `json_file_store.read_dict(..., errors=ERRORS_WITH_OS)`（坏 JSON 退回默认、不抛）。**判据档常开、无「启用」开关**（2026-09-18 用户定案「开关没意义」）——本层是「词表之前的一次更聪明的尝试」，**失败有上界**（最坏退回词表档、不中断出图），所以不需要一个「用不用它」的选择；`effective_climax_anchor_rubric()` 因此**永不返回空串**（缺文件 / 坏 JSON / 判据留空一律回缺省 v8）；保存空串时后端回填 `DEFAULT_CLIMAX_ANCHOR_RUBRIC`。**判据文本可改、输出合同只读**（`ANCHOR_INTENT_CONTRACT` / `ANCHOR_INTENT_FIELDS = ("anchor","composition","subjects","reason")`）：用户编辑的是「想让模型怎么想」的 rubric，程序读的是四字段 **＋ anchor 必须逐字落在 narrative 里**（`anchor_evidence_in_text` **严格匹配、不做模糊匹配**——落不上即丢弃本轮判据退回关键词表）。**跨段锚点必须收敛到首段**（`_anchor_intent_first_paragraph`，2026-09-18 三段固定文本实锤）：模型给出 `A。\n\nB。` 这类跨段串时 evidence 因**归一化剥空白**而放行，但下游 `illustration_anchor_offset` 是「破甲还原后逐字匹配、命中不了才评分兜底」⇒ 跨段串命中不了 ⇒ 退回词表同款评分、插入点被拉到正文中部（实测 45% 处，真位置 99.6% 处）⇒ **判据白判**；收敛后逐字可定位，且仍是已校验串的子串（校验结论不变），跨段时明细带 `anchor_raw` 留模型原话给 Trace。解析容错 `parse_anchor_intent` 三级（原串 `json.loads` → 全角引号归一后 `json.loads` → 逐字段正则兜底），吞 `<think>` 块与代码围栏。**`_ANCHOR_MATCH_STRIP_RE` 含半角 `, .`**（是 `illustration_anchor_offset` 剥离集的**超集**，为取证宽容，但仍非模糊匹配）。接线在 `agent_agency._illustration_anchor_intent(ctx, clean, sig)`（**四态** `skipped / error / invalid / ok` 全进 trace，`skipped` 带 `reason`＝`rubric_empty` 防御闸 / `narrative_empty` / `model_config_missing`；`timeout=60s`），调用点 `_wb_comfy_scene_target` 写成降级链 `判据 LLM 结果 or fallback_illustration_anchor(clean)`。前端：`MediaInsertModal` 标题行**右上角齿轮按钮**（`aria-label="提示词设置"`）→ `IllustrationPromptModal`（**无开关**，只有 rubric 编辑 + 恢复默认 + 只读合同 `<pre>`）；API `GET/POST /ai/prompt/settings`（`ClimaxAnchorPromptConfig` / `IllustrationPromptSettingsReq`，保存只接受合法字段）。回归：后端 `test_illustration_prompt_store.py` **9 例** ＋ `test_scene_illustration.py` **+12 例**（解析容错 / 证据校验 / 跨段收敛 / 合同拼接）＋ `test_agent_dispatch.py` **+9 例**（四态 + 集成降级：拿不到判据时与关键词表**同结果**、命中时判据胜出）；前端 `illustrationPrompts.test.ts` 2 ＋ `IllustrationPromptModal.test.tsx` 4 ＋ `MediaInsertModal.test.tsx` +1 ＝ **17 例**。受控红检 **4 道全中预测**（判据空闸 / 证据校验 / 关键词回落 / 合同拼接），还原后 sha1 逐字节一致。门禁：`ruff` 0 / `mypy @mypy_files.txt` **49 文件** clean / `lint-imports` **17 kept, 0 broken** / `tsc --noEmit` 0 / 后端全量 **3088 例 0F 0E 0S** / 前端 119 文件 **1330 例** 0 失败。手册见 `docs/tech-manual/A-roleplay-illustration/03-backend-illustration-pipeline.md` §3.4。

37. **插画锚点「降级链仲裁机覆盖收口」（2026-09-18 · 真机复跑驱动）**：修一处**接线不一致**。三档降级链（同轮 `<illustration>` 计划 → 判据 LLM → 中文词表）里，`scene_illustration.resolve_illustration_anchor`（把模型给的锚点与词表档比 `_anchor_score`，**词表档高 ≥3 分就推翻**）**只在 `if plan:` 分支里跑** ⇒ 只保护「同轮有计划」那条支路（约 24% 回合），而**缺块路径**（判据档唯一生效处，约 76% 回合）**整条绕过纠偏网**。设计稿 `docs/illustration-anchor-intent-design.md` §5.2 早已记「§2.2 的推翻逻辑需一并重审」，属**遗留未决项**（并非本次新发明）。真机复跑取证（生产模型 `deepseek-v4-flash-0731`，4 run × REPEAT=3）：该缺口使判据档在 L1/L3 反而**劣于**词表档（L1 位置距离 55.3 vs 词表档 35、L3 19.0 vs 12）；把判据锚点也过一遍仲裁机预演 ⇒ L1 `55.3→35.0`、L3 `19.0→12.0`、**L2 `0.0→0.0`（推翻 0/12）**＝**零误伤、两段改善**。**修法**：`agent_agency._wb_comfy_scene_target` 新增局部标志 `fallback_anchor_needs_arbitration`，判定条件由 `if plan:` 改为 `if plan or fallback_anchor_needs_arbitration:`。**三条红线**：① **只扩到「缺块 + 判据」这条支路，不得就手扩到 encounter 支路**——首帧/初遇锚点是**有意指定**的画面，而仲裁机带「高潮纠偏」语义，套上去会把画面从开篇拉到高潮段（`first_frame_anchor_offset` 当初正是为此存在）⇒ 红线形态＝「改成 `if plan or requested_anchor:` 必须被边界锁抓住」；② `fallback_anchor_needs_arbitration` **只能在缺块 elif 分支内置真**（在 `character_encounter` 分支置真 = ① 的反向失败）；③ **词表档与判据档的判定逻辑本身零改动**（本刀只改「谁过仲裁」，不改任何打分/解析/校验）——`DEFAULT_CLIMAX_ANCHOR_RUBRIC` sha1 保持 `6e6dd6a5…`、len 861、system 1298。**副作用（有意识取舍）**：接上后判据档在 L1/L3 会整体**收敛到词表档基线**（抬高了下界、同时抹平方差），L3 一个距期望仅 3 个百分点的好选点也会被推翻。回归 `backend/tests/test_agent_dispatch.py`（新增 2 例：`test_缺块路径的判据锚点也要过既有仲裁机`〔特征网，接线前必红〕＋ `test_初遇锚点不过仲裁机`〔边界锁〕；**既有两条** `test_缺块路径判据返回空时锚点仍是词表结果` / `test_缺块路径判据命中时优先于词表` 在该夹具上仲裁**不推翻**、断言逐字不变，天然成为「仲裁不误伤好选点」的回归网，故**改指加严、不删锁**）。**受控红检 2/2 精准**（M5 退回 `if plan:` → 特征网红；M6 扩为 `if plan or requested_anchor:` → 边界锁红），还原后 `agent_agency.py` sha1 `b9a078df192d…` 逐字节一致。门禁：定向 255/0F/0E/0S；`ruff check app` 0；`lint-imports` 17 kept / 0 broken；⚠ `agent_agency.py` **不在** `mypy_files.txt`（该清单刻意只收零错误文件），单跑 mypy 报 5 处**既有**错误（L232-235 / L657，与本刀改动区 L1023-1055 无关）。真源：设计稿 §4.3 / §5.2 / §6。
38. **插画取材层「锚点 → 窗口」A′ 落地（2026-09-18 · 用户拍板走 A′、否决 B）**：修一处**取材机制**缺陷（不是改判据产出内容）。根因：`scene_illustration._visual_action_context` 的分支④（锚点句既非高潮/结果句、也不含任何动作词）**只返回锚点那一句** ⇒ 锚点落在台词/独白/拟声（如 L3「那么，我该插进哪一边呢？」）时窗口 12 字、**画面角色数 0**，下游提示词编译拿不到任何画面事实。**修法（唯一属主：取材层）**：`illustration_scene_excerpt(text, requested_anchor="", *, subjects=None)` ＋ `_visual_action_context(paragraphs, index, *, subjects=None)`；分支④ 新增「`subjects` 非空且锚点句**不含其中任何名字** ⇒ 向前取到**场景边界窗口**（`\n\n` 连接 `[场景边界, 锚点]`、截尾 5000 字）」，否则维持单句旧行为。**`subjects=None / []` ⇒ 逐字节等于旧行为**——这是本刀零回归的前提（§8.8 同族教训：安全落地的前提常是「老行为被完整保留」）。**`_scene_boundary_start(paragraphs, index)` 由分支①② 的扩窗逻辑逐字抽出**（纯搬移、行为不变），锚点档扩窗与高潮档走**同一把尺**；沿用生产 `max(0, index-40)` 的 **40 段上限**（去掉上限窗口变 4460 字）。**两条主体名口径**（特征测试各锁一条）：判据档取 `_illustration_anchor_intent` 返回的 `detail["subjects"]`；词表档没有判据字段 ⇒ 回退 `sig["present"]`（在场角色全集，通常 ⊇ 判据 subjects ⇒ 更易扩窗）。`_frame_subject_names(raw)` 统一归一（非序列/全空 ⇒ `[]` ⇒ 视为「无信号」＝旧行为）。**三条红线**：① **仲裁机内部调用**（`resolve_illustration_anchor` → `illustration_scene_excerpt`）**刻意不传 `subjects`**，否则 `requested_is_action_outcome` 的输入口径改变、A′ 产生非零二阶影响——**不许「顺手统一」**；② **encounter 首帧支路刻意排除**（与 37 的红线同源）；③ **四态 trace 字段不动**（`_illustration_anchor_intent` 只是把返回从 `str` 改为 `(anchor, detail)`，`skipped/error/invalid/ok` 与 `reason` 逐字保留）。**同批机械闸**：`_is_non_narrative_placeholder`（正则整段全匹配，含「图片加载中…」/`loading`）同时接在 `illustration_scene_excerpt` 与 `protected_illustration_scene_excerpt` 的切段过滤上——真机留样实锤判据曾**把它选成锚点**、扩窗也曾把它带进窗口末尾（设计稿 §6.5 遗留项，此处收口）。**⚠ 设计修正（实测推翻设计稿 §8.2）**：原计划的 `composition ∈ {双人同框, 多人同框} ⇒ 也扩窗` 的 `if/elif` **被否决**——真机留样里 L2 期望锚点「塞蕾娜正微笑着。」**3/3 次**被判据自标 `composition=双人同框`，按该规则会把 L2 的 8 字特写错误扩成整段；故**只用 `subjects` 单条件**，`composition` 仍**未接下游**。**已知上界（不是缺陷）**：锚点句**含部分**主体名时不扩窗（如「她只掰开了一边」只提到两名主体之一），故「多人同框全都在窗口内」的上界落在**选点**、归判据 LLM，本机制只**向前**取材 ⇒ 锚点落在提臀段之后才成立（与用户「不改高潮点产出内容」一致）。**零 LLM 探针** `scripts/probe_window_metrics.py`（读三段语料 ＋ `backend/_probe_anchor_chain_samples.json` 真机留样，输出 `backend/_window_metrics.txt`；**刻意不报段号**——切段口径随空段与占位串过滤浮动）。实测（真机留样复算）：**L1 `14→14`、L2 `8→8`（逐字不变＝零误伤），L3 期望锚点 `12→649` 字（关键画面词覆盖 `-----+` → `++-+++`）**，判据档 L3 样本 `15→735` / `6→843`。回归 `backend/tests/test_scene_illustration.py`（新增 10 例：旧行为逐字保持 / 含主体名维持单句 / 无主体名扩到场景边界 / 两档口径各一 / 40 段上限 / 占位串识别不误伤叙事 / 扩窗剔除占位串 / 既有高潮分支窗口内也剔除 / 占位串不得充当取材锚点段 / 仲裁机内部调用不传 subjects）＋ `backend/tests/test_agent_dispatch.py`（新增缺块路径判据档扩窗·无主体名维持旧行为·词表档用在场全集三条 + 既有断言改指 `(anchor, detail)` 元组）。**受控红检 8/8 全中预测**，还原后 `scene_illustration.py` / `agent_agency.py` sha1 逐字节一致。门禁：后端全量 **3106/0F/0E/0S**、`ruff check app tests` All checks passed、`lint-imports` 17 kept / 0 broken、`mypy '@mypy_files.txt'` 49 success（⚠ `scene_illustration.py` / `agent_agency.py` **不在**该清单，属已知盲区）。真源：`docs/illustration-window-handoff-2026-09-18.md` §7.3 / §8。

39. **插画锚点「造词回退 + 取材选址相似度兜底」第六/七刀（2026-09-18 · 用户口径修正驱动）**：用户当面纠正了两件事——① 度量对象不是**窗口字数**，是「**两名角色各自的动作是否在窗口内**」；② 真问题是**选点靠后**（「ai 提取的高潮点总是靠后的单人戏段」），**窗口起点不存在问题**；③ 要的是**通用性**（「不是只有这个情况为特例，而是在所有情况都能…」），为单一样本特调＝不合格。**第六刀（判据层，`resolve_anchor_intent`）**：新增 `_anchor_intent_recover` —— 判据给出的 anchor **逐字不在正文**时，逐段算 `SequenceMatcher` ratio，取 `>= _ANCHOR_NEEDLE_MATCH_RATIO` 里**最相似的真实段落**作为锚点，并打标 `anchor_recovered=True` + 保留 `anchor_raw`；拿不到仍 `return "", parsed` 走词表档。**为什么放行「位置意图」是安全的**：**锚点句本身不是画面来源**——画面始终来自取材窗口（`illustration_scene_excerpt`），锚点只回答「插在哪附近」⇒ 模型被改写过的那句，其位置意图仍有效，只需换成一个**逐字可查证的真实段落**。`anchor_evidence_in_text` **一字未改**（仍是审查函数，结论不变）——回退是它**下方**的一层补救，不是放宽它。**第七刀（取材层，`illustration_scene_excerpt`）**：修一处**断链**——旧址只有 `needle in paragraph` 逐字命中 ＋ **无阈值**的 `max(最相似)`；判据层回退后下游仍可能拿**模型改写原句**选址 ⇒ 逐字必然落空，旧代码虽能取对但**任意无关文字都会硬凑一段**（图插到无关位置）。现改为「逐字命中 → **带阈值相似度兜底**（低于阈值交词表档/末段）→ 词表档」。**两条红线**：① **两层必须共用同一个阈值常量** `_ANCHOR_NEEDLE_MATCH_RATIO = 0.6`（旧的一次性 `_ANCHOR_INTENT_RECOVER_RATIO` 已删），否则出现「判据层说找到了、取材层又换一段」的分叉；② **`subjects` 只影响①/②选出的那一段怎么扩窗，不影响选址**（A′ 的零回归语义不变）。阈值 0.6 有**实测余量**：真实造词锚点与目标段 ratio≈**0.893 / 0.727**，无关段 ≤**0.26**。**通用性用合成切片锁死**（不依赖任何场景词、不分单人/多人：厨房场景用例）。**判据 v11（`illustration_prompt_store`）**：**撤掉**旧句「故事继续往下走、焦点收窄到其中一人之后的位置一律不合格」——它是把锚点往后推的直接推手；§三 加一句**只在多角色分支生效**的优先级（动作同框 > 关系帧）并**显式写明「不适用于单人」**（正是 v7/v9/v10 三次失败暴露的过度应用面）。**基线变化**：rubric `6e6dd6a55294`/861 → **`8c08032c240a`/1101**；system 1298 → **1538**；**contract `1fd942387eca` 与 `ANCHOR_INTENT_FIELDS` 未动**。⚠ **v11 ≠ 放开「加约束」**：judge header 那条「不要再加约束/加门」（v7/v9/v10 三次净亏损）**仍然有效**，v11 是**换主轴而非加约束**，之后再加条款仍须先做 A/B。**真机复采样（`deepseek-v4-flash-0731`，L3 × 4 run，P 档）**：空锚点 `1/3 → 0/4`、**4/4「逐字落文：是」**（造词被兜住）；`pct>60` 的「靠后单人戏」`0/2 → 0/4`（症状消失，词表档基线是 64%）；reason **4/4 提到动作同框**（§3 改动生效）。⚠ **但方差没消、只换了形态**：新分布 `5/1/49/50`（双峰），失败从「靠后」变成「靠前/跳段」⇒ **属执行/采样问题，不是判据语义缺口**（与 §4.3 结论④同源），出路是**机械化构图校验**或换更强模型，**不是继续加判据文字**。⚠ **同时纠正 §4.3 表里一个旧数字**：L3「距离均值 29.8」是**接仲裁机之前**的口径且混了空锚点样本，与接上后的 12.0 不可直接相减；**能同口径比较的只有「空锚点率」与「靠后率」**。回归：`test_scene_illustration.py` 追加 **5 例**（造词回退到真实段落/不得凭空过闸/逐字命中不走回退/回退拿不到才退词表/通用性锁）＋ **3 例**（取材层改写句能命中/不够像不硬凑/**两层同源阈值**），`test_illustration_prompt_store.py` 追加 **4 例**（多人优先动作同框/旧禁令已撤/单人不受波及/不得退化成选剧烈过程）；**受控红检 8/8 全中预测**（含「阈值失效/过严」「回退被拆」「回退放行幻觉」「Trace 丢痕迹」「逐字命中被污染」「常量被拆两份」「兜底丢证据」），还原后两文件 sha1 逐字节一致、`.bak7` 零残留。门禁：定向 **90/0F/0E/0S**；后端全量 **3118/0F/0E/0S**（基线 3106，+12）；`ruff` 0；`lint-imports` 17 kept / 0 broken；`check_hardcode` 0；`mypy app/services/scene_illustration.py` Success；前端 **119 文件 / 1330 例 0 失败** + `tsc --noEmit` 0。真源：`docs/illustration-anchor-intent-design.md` §7、`docs/illustration-window-handoff-2026-09-18.md`、手册 `03` §3.4 / §5 红线 18。

40. **插画判据 v12：给「收束」加适用范围（2026-09-19 · 四轮真机 A/B 驱动）**：把 `illustration_prompt_store.DEFAULT_CLIMAX_ANCHOR_RUBRIC` 的 §明确排除 第三条由「角色正处于剧烈动作的过程之中…画出来是重复；」改为**带适用范围前置条件**的同一条——「（**若此刻确实存在正在进行的动作**，应当选该动作**收束**的那一瞬——做完、刚做完、停下来，而不是动作最烈的那一瞬；**此刻若并没有正在进行的动作**（对话、心理、静止的对峙或余韵），**本条不适用**，按下面【优先选】与【落点判据】判断。）」。**基线变化**：rubric `8c08032c240a`/1101 → **`16b1a2d70a30`/1221**；system 1538 → **1658**；**contract `1fd942387eca` 与 `ANCHOR_INTENT_FIELDS` 未动**（仅判据文案，程序侧只读合同一字未改）。**为什么是「加适用范围」而不是「加约束」**——四轮真机 A/B（`docs/illustration-anchor-ab-2026-09-19.md`，`deepseek-v4-flash-0731`）给出**可复用的方法论**：① **A 档**（§排除三 加**无条件**的「应当选动作收束的那一瞬」）救 L1（52.8→24.6）却**毁 L2**（11.3→**41.2**，命中 5/8→2/5）——该条款被**过度应用到无动作的文戏**，模型转去抓「意象定型」句、丢掉结尾表情帧，正是 §36 那条「多约束互相污染」的**第四次重演**（前三次：v7 加「画面须容纳 ≥2 角色」→0/3；v9 加「呈现度分相」→1/3；v10 把「主题同框」升为前置门→L1 3/3→0/3）；② **B 档**（换掉 §三 的分岔依据：主题类型 → **画面主体数**）治 L3（31.0→17.4）却**伤 L1**（→48.4）；③ **AB 组合**（两处同改）**互相抵消**——L3 从 B 单独的 17.4 **吐回 38.6** ⇒ **「不许往判据加约束」的适用范围不止「加条款」：同时动两条主轴同样污染；一次 A/B 只准改一处，否则归因不可分辨**（新教训）；④ **v12（只给同一句加适用范围）＝唯一「三格全部不劣于 base」的变体**：L1 52.8→**40.1**（按用户新验收口径命中 **0/8→3/8**）、L2 11.3→**0.5**（命中 5/8→**7/8**、邻中 8/8）、L3 31.0→**27.9**。⇒ **判据改动的第四条路＝「限定适用面」**（区别于「加条款」「换主轴」「改结构」），而 §36 的禁令**仍然成立**——v12 没加约束，只把一条已有语义**收窄到它本来该管的范围**。**代价**：判据 1101→1221 字，**+120 token/次**；实测判据仅占单次调用的 **11~25%**（大头是**全文正文重传**，单次合计 5624~12461 tok）⇒ **≈ 单次 2%，不得以 token 为由否决判据改动**。**机械锁**：`test_illustration_prompt_store.py` 新增 `test_收束判据必须限定在存在动作时生效`（要求「收束」正面指引与「不适用」范围声明**成对出现**——`excl[find("收束"):]` 里必须有「不适用」），**受控红检 2/2**（v12 通过 / A 档无条件必红）；**既有四条性质锁未动**（多人分支含动作 / 旧禁令已撤 / 单人显式排除 / 排除项保留「剧烈动作的过程之中」+「辨不清」）。**L1 验收口径（用户 2026-09-19 定案）**：**「无论是开始还是结束的定格都可以」** ⇒ **预备态定格**（「宥夏用手掰开一边屁股。」＝L3 那组动作的单人版）与**完结态定格**（「浑身无力的宥夏靠在公告栏上。」）**都算命中**；只排除**过程态**（动作进行中、姿态未定型，如「开始轻轻脱下自己的内裤」）。✅ **验收脚本已同步（09-19）**：`scripts/probe_anchor_chain.py` 的 `CASES` 对 L1 已给**多判定词**（`["靠在公告栏上", "掰开一边屁股", "肉棒抽了出来"]`，落点 84% / 32% / 83%）⇒ 预备态与完结态**都能判中**；`expect_anchor`（位置距离参考点）**保持完结态不变**（命中＝验收，距离＝辅助，职能不同）。**仍未解决**：L3 未治（27.9，逊 B 档 17.4）且**方差未消**（`2/3/47/50/21/10/50/10`，一半跳开头）；**B 不可与 v12 简单叠加**（AB 已证互相抵消）；**词表档 `_STATIC_RESOLUTION_TERMS` 含「微笑」且是扣分项** ⇒ L2 词表档恒选不到期望句（§39 挂账未修）；样本量 8 次未达「断言方差需 ≥10 次并报分布」的门槛。真源：`docs/illustration-anchor-ab-2026-09-19.md`（九节）、设计稿 **§8**、手册 `03` §3.4 / §5 红线 19 / §6、回归手册 `17` §3.2.3。
41. **ComfyUI 后端低完整性拉不起：MIC NO_WRITE_DOWN + `tempfile` 无界重试（2026-10-21）**：根因**不在代码在令牌**——用户启动链（`start-dev.bat → powershell → Start-Process → uvicorn`）上任一环节被沙箱化，子进程经 `Popen` **继承低强制性完整性**（`Mandatory Label\Low Mandatory Level`，S-1-16-4096）。Windows MIC 使低进程**只能写低标签对象**；无标签对象视为中等，故「目录看起来可写」的 ACL（`Authenticated Users:(M)`）**完全不参与**判定 ⇒ 写不了无标签的 ComfyUI 安装树与用户 profile。**致命放大器是本机 CRT `os.TMP_MAX = 2**31-1`**：CPython `tempfile._mkstemp_inner` 把 `PermissionError` 当**可重试**（`isdir && access(W_OK)` 即 continue）⇒ 低进程被 MIC 拒绝时**空转约 2³¹ 次**（py-spy 抓到 `seq≈198 万` 仍在跑、约 1 核数天），**日志静默、无 traceback**。任何库只要启动期往不可写目录建临时文件就会把整个进程挂死。**五修复**（env 重定向 + 守卫，非根治）：`TEMP/TMP`→`DATA_DIR/tmp`、`NUMBA_CACHE_DIR`→`DATA_DIR/numba_cache`、5 个 `LORA_MANAGER_*_DB`→`DATA_DIR/comfyui_node_cache`、新增 `comfyui-ext/laf_tmp_guard/prestartup_script.py`（把 `_mkstemp_inner` 换成 `PermissionError` 首次即抛、`FileExistsError` 仍重试 8 次的等价重写，**探针走 launcher 的 `execute_prestartup_script` 在真实 ComfyUI 进程内、torch import 前执行**）、`start()` 加 `is_managed()` 守卫（冷启动 8~10 分钟里 reload autostart + 连点曾叠 3 个进程互抢 `comfyui.db` 锁与 8188）。**根治＝后端跑 Medium**（用户启动上下文）；env 重定向只保证「能起来」。**诊断手法可复用**：`whoami /groups` 看 Mandatory Label（**别只看 `icacls`**）；py-spy `dump --pid X --locals` 看 seq 计数器与失败的 dir。**附带坑**：Medium 进程的 CPython venv 读不了低标签树里的 `pyvenv.cfg`（`failed to open`，但 `cat`/.NET `OpenRead` 都能读）⇒ 跑测试要把 venv 骨架（`Scripts/` + `pyvenv.cfg`）复制到中等树 + `PYTHONPATH=<原 venv>/Lib/site-packages`。手册 `C/04` §3.8 + 红线 15-17。
42. **turnNo 跳号两波：高位水印顶高复用号 → 纯函数 `resolveStoryTurn` + 存量五真源回填（2026-10-21）**：`useChatSession.runFreeText` 原 `turnNo = Math.max(显式 storyTurn, 链尾+1, 水印+1)` **三项取大**且入口**无条件** `rememberStoryTurn(turnNo)` ⇒ `regenerateMessage` 传的 `reusedTurn`（重生成复用被删回合号，M1 有意设计）被记成水印 ⇒ 下一次重生成同一条消息时 `hwm+1` 比复用号大 1 ⇒ **每重试一次号 +1，中间结果全被替换，楼层只剩一条、号虚高**（第 58 刀引入水印时「不改重生成复用号」的意图被 `Math.max` 打破）。**修法**：新增纯函数 `frontend/src/lib/storyTurn.ts:resolveStoryTurn(explicit, messages, highWater) -> {turnNo, remember}`——显式 `storyTurn` **原样返回、不抬水印、不参与取大**；新回合才 `max(链尾+1, hwm+1)` 并抬水印。**第二波**：修复只保证新增回合不跳号，而 `nextStoryTurnNo` 取 `max+1` ⇒ 不修存量会从 17 起号 ⇒ 前端补 `syncStoryTurnHighWater(threadId, messages)`（水印高于链尾 ⇒ 对应回合已删，写回链尾）**自愈**，不再需要用户手清 localStorage。存量回填 **20 处 / 5 个真源**（`chat.json` 4 / `state.json` 10 / `table_progress.json` 7 游标 / `temporal_facts.db` 4 行 / `chronicle.db` rowid=10 区间 + `meta.last_turn`），脚本 `_tmp/fix_turn17.py`（dry-run 默认 / 先备份 / **逐字节格式校验**）。**⚠ 格式坑今天连踩三次**：`chat.json` 是 `json.dumps` **默认分隔符单行**；`state.json`/`table_progress.json` 是 `indent=2` + **CRLF** 无尾随换行；`worldbook.json` 是 `indent=2` + **全 CRLF** 无尾随换行 ⇒ 按 compact/LF 假设写回会污染整个文件。**不动**：`narrative_ci.db` 诊断留痕（会让内容哈希 id 与记录不一致）、世界书 `entries[n].id/display_index`（条目编号不是回合号）、`field_ledger.__field_repair__.count`。
43. **前缀缓存二次攻坚：断点修复 + `late` 单向棘轮 + 杠杆 A（2026-10-21~22）**：真机账单命中率 **8.3%**（网关账单有数，但 API 响应 `usage` 对 claude 链路**不回**缓存字段、对 deepseek 链路**回**——「客户端观测不到」的旧结论要按链路修正）。**根因一**：`llm._stable_prefix_tail` 的第二断点落在**会变的剧情轮之后**（10-19 用「同回合重试」测量 ⇒ 首异落在 msg30；跨回合首异在 msg18）⇒ 缓存块每轮都变、永远读不中。修法＝增加第二类边界：**`<history>` 开标签行也算每轮边界**且取**最早**边界（判据＝「该块 strip 后以 `<history>` 结尾」；只**提到** `<history>` 的静态块不算——同 10-19「裸标签 `user last input` 被 COT 指南骗到」的教训）。真机 7 轮全部 `tail=16`、缓存块 32,693 字 ⇒ **50.3%**。**根因二（结构性，`worldbook.py`）**：会话**中途**激活的锚点走 `_SESSION_LATE` ⇒ 渲染进 **history 之后的动态段**（追加入锚点段会破坏其前缀）⇒ 缓存零收益；且动态段 `dynamic_items.append(entry.content)` **豁免 `max_chars=8000`**（只有召回段吃预算）⇒ 从「召回段（有预算、不相关就丢）」搬进「动态段（无预算、每轮全文）」**prompt 反而变大**（13,623 → 24,765 字）。而 `late.discard` 只在 `initial_build=True`（seq 为空）且条目**本轮新登记**时执行（`if identity in known: continue` 会跳过所有已在 seq 里的条目）⇒ **`late` 是单向棘轮，回滚 `constant` 也不回退**。**杠杆 A 已落地**（`_tmp/lever_a.py`）：4 条长期在场条目（按召回频次：母牛化 7/7、兴登堡 6/7、奥古斯特 6/7、吾妻 4/7）标 `constant=True` + `worldbook_session.json` 写空 + **重启后端** ⇒ 4 条全部脱离 `late` 进锚点段。**实测**：重建后第一轮 6.0%（前缀作废的一轮全价，设计内），第二轮 **68.6%**（预测 68.7%）；动态段 17,639 → 8,801 字、prompt 42,771 → 39,354 tok（−8.0%）。**天花板修正**：原报 83.7% **作废**——动态段其实是**三块**（世界书 late 3,210 字 + 表格行召回 1,798 + **全表上下文 `table_context()` ≈3,760 字固定成本**，后两块因 `\n\n` 连接会**粘连成「一条 4,226 字角色表」的假象**），其中 43% 不可消除；真实可达 **≈81%**。**剩余未落**：B 动态段加预算（**已无标的空间**——A 后 `dynamic_items` 只剩 3,210 字；且必须排除 `table_context()`，否则会把 6 张表整个挤掉）、C late 走底座快照（本会话 0/11 条含 `【剧情进展·动态】` 标记 ⇒ **已作废**）、D 主对话换 `deepseek-flash`（claude 该网关 `cached` 恒 0 + 2.76 vs 0.70 tok/字 = **4.1 倍**）、E **全表上下文瘦身**（−16.3% 成本，**省钱幅度反超搬世界书的 −12.6%** ⇒ 搬位置只把 1.0 价换 0.1 价，砍内容直接删 token，**命中率是虚荣指标，账单看绝对成本**）。
44. **表格累积型字段长度治理（2026-10-22，方案 C）**：修一处**数据膨胀**——表格的累积型字段被写成整段剧情复述（真机实测：任务表「进展」263/305/531 字、全局表「世界状态」528 字、角色表「过往经历」300 字），占读侧全表 4,003 字的 **47%**，且内容**与纪要/时序事实重复** ⇒ 每轮白付 token。**为什么不是改 `mode`**：`render_tables_block` 早有 retrieval 分支（`mode=retrieval` 只出结构 + 身份列清单，行交 `table.retrieve` 召回），但「任务与事件表」回答的是「我现在在干什么」，改成 retrieval 会让本轮 query 没命中的任务彻底不可见 ⇒ **把连续性真源变成碰运气召回**，否决；读侧截断也只治标（数据还在膨胀）。**修法＝写入侧兜底**（读侧渲染规则一字未动）：新增 `table_store._LENGTH_CAPPED_COLUMNS`（`进展120/世界状态220/过往经历180/人际关系120/在场状态120/当前状态120/备注100`，上限按**实测列长分布**定而非拍脑袋）+ `_clip_cell()`（**按句读收敛**：在 `[limit/2, limit]` 内从后往前找最后一个 `。！？；!?;\n…` 命中即截，无句读才硬切补 `…`；**幂等**——只剥尾部 `…` 不碰正文句读）+ `_clip_op_values()`（只对有上限的列做 `str()` 归一，`数量` 等 int 列不得变 str）；接线只在 `apply_ops` 的 `insert`/`update` **分支入口各一行** ⇒ 覆盖全部 6 个写入点。`table_update._OPS_RULES` 同步加文案（「只写当前状态 + 下一步，不写过程，超出会被截断＝白写」）。**三条红线**：① 身份列（`名称`/`姓名`/`物品名称`/`技能名称`）**永不截断**——是 update/delete 的定位键；② `时间`/`地点`/`世界规则` **永不截断**——真源；③ **不得改 `mode`**（全表都在是任务/主角表的价值前提）。**存量迁移** `_tmp/clip_tables.py`（dry-run 默认/先备份/逐字节格式校验）：11 处，累积型字段 **3,615 → 1,814 字（−49.8%）**；读侧全表 **3,760 → 2,749 字（−26.9%）**，加召回行合计 **≈1,309 字** ⇒ 命中率 69.5% → **71.0%**、成本 **−6.2%**。⚠ **此前报的「省 2,500 字 / −19.7%」是高估**：重要角色表是 retrieval、读侧本就不显示其行，截它只影响写侧与召回侧 ⇒ **小段瘦身对命中率极不敏感**（全表只占总量 6.3%）。回归 `test_table_store.py` +8 例（句读收敛/硬切/幂等/身份列与真源列不截/非字符串列不改类型/文案锁）。

45. **ComfyUI 启动链上下文归位：AlekPet 600 秒文件锁 + 同装单实例 + 受限快速失败（2026-10-23）**：用户实锤同一安装（`ComfyUI_aaaki\ComfyUI`）TE 启动器 **82 秒**起、`start-dev` 要 **近 9 分钟**。**根因不是 Manager**（`FETCH ComfyRegistry` 在 `Starting server` 之后的后台线程）；是 AlekPet 节点 `__init__.py:297` 模块级 `installNodes()` → `FileLock(.install.lock, timeout=600)`（`:247`），`filelock 3.29` 的 Windows 实现把 `os.open`/`msvcrt.locking` 的 EACCES(errno 13) 当「锁被占用」（`filelock/_windows.py:76/83`）静默轮询到超时；自定义节点 import 在绑定 8188 之前 ⇒ 端口全程不监听（Demiurge 日志 238 次 boot 里 9 次 `600.1 seconds (IMPORT FAILED)`、22 次 `could not be acquired`、10 次 asset DB 锁警告；TE 同安装实例 AlekPet import 仅 3.8 秒）。两族触发并存：① 后端低完整性（41 条）子进程写不了安装树；② 多实例（后端 autostart / start-dev 的 POST / 前端懒拉起 + `--reload` 后 `_proc` 归零 + 外部启动器）互抢 `.install.lock` 与 `comfyui.db`。**五修复**：① **spawn 规格唯一属主** `_build_spec()`（start / launch_detached 共用 argv/env/cwd/日志路径）；② **同装单实例** `find_install_processes()` / `_match_install_pids()` 按命令行识别（launcher 子进程命令行**不含安装目录** ⇒ 解释器路径也算；按路径边界匹配，`...\ComfyUI` 不误伤 `...\ComfyUI_TE`），`start()` 幂等判据变为 `is_up OR is_managed OR 同装 pid`；③ **受限上下文快速失败** `install_tree_writable()` 双探针（新建 + **打开已存在 `main.py`**；低进程能新建低标签文件却打不开中标签文件）⇒ `LaunchError(503)` + 可操作指引，不再让人等 600 秒；④ **开发链路换上下文**：`LAF_COMFY_AUTOSTART=0`（`main.py` 尊重）+ `scripts/start_comfy.py`（`launch_detached`，不接管 `_proc`）+ `scripts/stop-dev.ps1` 与 `stop(url, path, python_path)` 同口径按命令行清「启动中未监听」实例（默认 `python_path` 为空时按 `find_python` 解析同装解释器）再按端口兜底；⑤ **规划/执行拆分（同日二次实锤）**：沙箱按**映像路径**降级——工作区内可执行文件一律 Low、工作区外 Medium（同一 `whoami.exe` 内外对照 + ctypes 逐级读令牌实测），后端与 venv python 都在工作区内 ⇒ 此前「用户身份下跑 `start_comfy.py`」仍被降级、用户两次实跑「还是失败」。改为 `build_plan()` 只出 JSON（不 spawn、不判可写），`start-dev.ps1`（`System32` ⇒ Medium）用 ComfyUI 自带解释器 spawn，并用**同一解释器**跑 `comfy_probe_write.py` 双探针后才拉起；旧的 POST 回退删除。**验证**：受限 shell 跑 `start_comfy.py` → 退出码 1 且指向「安装树不可写」；拆分后 `start-dev.ps1` 直跑 → ComfyUI 令牌 **Medium**、8188 **~117 秒**起、`system_stats` 200，该 boot 无 600.1 秒锁（TE 基线 82 秒）；`test_comfy_launcher.py` **29 例**通过（同装不叠加 / 503 不 Popen / stop 按 pid / stop 未配置解释器时解析同装解释器 / launch_detached 不接管 / build_plan 不判可写不 spawn）。**未根治**：应用不提升权限、不绕过沙箱；后端仍跑 Low（沙箱决定）。手册 `C/04` §3.7 / §3.9 + 红线 16/18。


47. **编排域按簇拆分为独立属主（已完成 · 2026-10-23，方案 B 再拆）**：
   `agent_agency.py` 2589→666 行（维护/插画四域迁出：`agency_maintenance` / `agency_illustration_actors` /
   `agency_scene_state` / `agency_scene_comfy`）；`agent_graph.py` 2271→590 行（五域迁出：
   `agent_graph_state`（AgentState 底座）/ `agent_graph_supervisor` / `agent_graph_nodes` /
   `agent_graph_plan` / `agent_graph_roleplay`）。搬移块 sha1 逐字节一致，旧名经原模块
   **全量 re-export 门面**零破坏（含测试 monkeypatch 面）；源码形态锁一律「改指新属主、
   不删锁且加严」。`_agency_writeback` 对已迁接缝走**模块属性调用**（`agency_scene_comfy._wb_x(...)`）
   ——测试补丁打新属主时主函数直调路径才一并生效。共享模块级状态（`_MAINTENANCE_*`、
   `_STREAM_SAMPLING_KEYS` 懒加载缓存）随消费域整体迁，不拆双真源。mypy 门禁同步扩到 62 文件
   （`agent_contracts.CtxLike = RunContext | dict`，60+ 处 `ctx: dict` 签名加宽；RunContext 本就有
   dict 协议）。门禁：pytest 4269/1F/7E/2S、lint-imports 17 kept、ruff 0、mypy 62 OK。

48. **ComfyUI 运行态守护（已完成 · 2026-10-23，运行时稳健性补强）**：
   `comfy_watchdog` 消掉两个静默失败场景：① 托管进程会话中途死亡（OOM/驱动复位）后按已存
   配置**幂等自动重启**——只救自己托管过的进程（外部整合包/手动拉起的死活不管，杀了夺端越权）；
   冷启动阻塞数百秒 ⇒ 尝试放 daemon 线程且不并发；窗口内连续重启达上限即停手留痕，端口恢复
   （用户手动修好）后复位。② autostart 失败不再被启动线程丢弃——落 uvicorn 日志 + 后台活动
   面板条目（target 跳节点管理），端口恢复后自动清除。状态经 activity_feed 只读投影
   （kind `comfy_watchdog`），不碰 /jobs 注册表。前端：SSE 断流（非 AbortError）给可读文案
   （「进度不丢，可在后台活动面板查看」），且 error 事件在**已有部分正文**时追加警示块——
   修复半途断流被静默吞掉（正文「莫名没了结尾」）。门禁：pytest 4283（1F `test_audio_merge` 系基线临时目录残留、
   7E `test_node_update` 系沙箱拦下 `git clone` 派生的 MSYS `sh.exe`——49 已逐条复现并更正口径，
   **两者单独重跑均不能一概判绿**）/ mypy 63 文件 / lint-imports 17 kept / ruff 0 / 前端 1632 例 + tsc 0 + dist 重建。

49. **scene 壁纸页面内实时渲染（已完成 · 2026-10-23，桌面化第三刀）**：用户实锤「静态预览把多层拼好的图当单张图 ⇒
   部件错位 / 过于放大 / 明明该是效果图5却糊成图4」，故把 Wallpaper Engine 的 scene 作品从「只贴 preview」升级为
   浏览器内实时绘制。**后端只交字节**：`wallpaper_library.scene_package(workshop_id)` 只认纯数字 id
   （`_WE_SCENE_ID_RE`）且必须落在已发现的工坊根下（越界无从构造，非数字/不存在一律 `ValueError`→404），
   `GET /api/user-state/wallpapers/workshop/scene-pkg` 用 `FileResponse` 原样交出 `scene.pkg`
   （`application/octet-stream` + `Cache-Control: private, max-age=86400`），**后端不解析包格式**；
   扫描侧 `has_scene_package` 只给 `type=scene` 且包文件存在的条目标 `scene_pkg=true`（web/application 仍回落 preview）。
   本机实测：177 个工坊条目里 **122 个带 `scene.pkg`**；包体中位 **13.3 MiB**、最小 0.57、最大 **132.3**（5 个 >50 MiB）
   ⇒ 实时渲染的数据入口是 MB 级 HTTP 全量，这是能力边界而非缺陷。
   **前端不依赖 Node**（便携 Runtime 只有 Python）：解码在 Web Worker 里跑，`frontend/src/lib/scene/` 拷入 MIT
   `dsh-plugin-wallpaper-engine` v0.6.8 的两个文件（`vendor/we-scene-manifest.js` 解 PKGV/LZ4/TEX-DXT，
   `vendor/we-scene-player-html.js` 自包含 WebGL 播放器），来源/改动清单/SHA-256 记在 `vendor/NOTICE.md`；
   `node:buffer|fs|path|zlib` 由 `shims/` 顶上（buffer@6.0.3 / fflate@0.8.2 / jpeg-js@0.4.4；Node 的
   `maxOutputLength` zip-bomb 上限在 fflate 的 `{out}` 上**静默截断**，故改流式 `Unzlib` 逐块累加、越界即抛；
   `nodeFs` 一律抛错——只走内存包分支，真碰到 fs 说明入口传错，不该降级）。
   🔴 **两条同源合同（实测得出，改回去就静默回落）**：① 播放器文档**不能用 `about:srcdoc`**——Chromium/Edge 下
   srcdoc 的 `window.location.origin` 实测为 `'null'`，播放器自己的 `ev.origin === window.location.origin` 入站校验
   与出站 `postMessage(..., origin)` 双向都判死 ⇒ 宿主的 fit/pause 进不去、回落通知出不来；改为 **`blob:` 文档 iframe**
   （继承创建者 origin）且**不设 sandbox**。② 垫片注入的清单响应必须是上游路由的信封 `{ok:true, manifest}`
   （vendored 播放器判 `data.ok && data.manifest`），给裸清单会被当「清单不可用」直接回落。垫片靠「播放器 HTML 只有
   1 个 `<script>`」做函数式 replace，`decodeScenePackage.test.ts` 锁住该形态与 MIT 头（CRLF 签出已归一）。
   **属主划分**：`decodeScenePackage`（包字节→清单+资源，纹理条数即 `textureCount`，条目表推导出的路径必然可读故无「缺纹理」通道）/
   `sceneWorker`（worker 协议）/ `scenePlayer`（blob 文档挂载 + 控制消息 + 双 URL revoke）/
   `sceneBackdrop`（装配、`center→cover` 映射、ready/fallback 状态机、渲染上下文反复丢失达 3 次永久回落静态预览）。
   dock 的「填充方式」真正传到播放器：`center`（CSS `none`＝原尺寸）对 3840×2160 场景就是用户说的「过于放大」，映射成 `cover`。
   **验证**（真浏览器，`_tmp/wallpaper-scene/*.png` + `frontend/e2e-manual/wallpaper-scene.spec.ts` 三例）：
   ① ready 后读同源 iframe 的 `__weRenderErr` 与 canvas 尺寸，**注入 CSS 只留壁纸层后「盖住画布截图必变」**（证明画布真有像素，
   不信可见性），并断言 dock 换档后播放器收到的 `dsh-set-fit` 序列为 `["contain","cover"]`、cover/contain 像素不同；
   ② 包内取不到场景结构 → `data-scene-status=fallback` 且 `<img class=app-backdrop-media>` 可见、控制台留痕；
   ③ 用实测会动帧的 `2986502697` 断言**同机位两帧（间隔 2s）画面必变**——静态预览做不到这件事，这是「实时」而非「贴首帧」的判据。
   另抽查 10 件 scene 作品：9 件 1.1–4.7 s 到 ready 且画布有像素，其中 1 件两帧间有变化（其余为静态构图或脚本驱动，见边界）。
   **顺带收口的标签漂移**：壁纸库/控制台原先对 scene 作品一律标「静态预览」「低清」「浏览器无法渲染」，实时渲染接入后
   这些断言变成假话 ⇒ 新增「实时渲染」判据（`rendersLive`＝条目带 `scene_pkg`；`isLowResPreview` 对可实时渲染者恒 false，
   预览图只当封面不参与画质评价），应用名后缀与 dock 提示同步分支；`wallpaper-workshop.spec.ts` 改为**按工坊 id 搜索**锁定
   一张 web 作品断言「静态预览」，并新增场景卡「实时渲染」角标断言（原来靠 `hasText` 撞第一页，属偶然通过）。
   **能力边界（上游播放器本身的范围，不是接坏了）**：只实现 `genericimage2` 等常用 shader + particle/2D 图层，
   **无 puppet 骨骼绑定与完整脚本引擎**——本机 122 个 scene 作品里 30 个 `scripted:true`、0 个含 puppet；
   脚本驱动的行为不跑，鼠标视差因壁纸层 `pointer-events:none` 不跟手；同源 iframe 仍占渲染主线程，
   工作线程隔离要改 OffscreenCanvas（后续项）。
   **门禁**：后端全量 **4289/1F/7E/2S**（1F `test_scenario_lab::test_full_snapshot_and_isolated_fork` 全量跑红、
   单跑 7 例全绿＝并发/环境偶发；7E 全在 `test_node_update`，夹具要真 `git clone`，沙箱下 `sh.exe` 建
   `\BaseNamedObjects\msys-2.0S5-*` 得 `0xC0000022` ACCESS_DENIED 直接 fatal，`--local`/`--no-hardlinks` 同样复现
   ⇒ 与本片无关且**单独重跑也红**）；`ruff check app tests` 0 / `lint-imports` 17 kept 0 broken /
   `mypy '@mypy_files.txt'` 63 文件 clean / `check_hardcode` 0 / `test_wallpaper_library.py` 25 例绿；
   前端 scene 28 例 + 定向 63 例绿、全量 **1660 例 1 红**（`RichInput.draft` 配额用例 monkeypatch
   `Storage.prototype.setItem`，本机 PATH 上的 Node v25 自带 `localStorage` 桩不在 jsdom 的 `Storage` 链上⇒补丁打不进去；
   带 `--localstorage-file` 复跑 1659/1660 绿，`RichInput*` 本次零改动）、`tsc` 0 + `vite build` 0。
   另记一条**基线陷阱**：`--basetemp` 落在 `backend/` 内会让 `find_ffmpeg()`（无配置时以 cwd 为候选根 `glob("**/imageio_ffmpeg/binaries/ffmpeg*.exe")`）
   命中上一轮遗留在 `_tmp/bt/**` 的夹具，把 `test_find_ffmpeg_无配置返回None` 判红——TEMP/basetemp 必须放仓库外。
## 后端分层

```
routers/   HTTP 适配层。只做：解析请求模型 → 调 services → 把领域异常包成 HTTPException。
           ★ 不要在这里写业务逻辑、循环、文件 I/O、拼 ComfyUI 请求。
services/  深模块层。业务逻辑全在这。彼此可依赖，但不 import routers。
```

已就位的深模块（加功能时优先复用，别另起炉灶）：

| 模块 | 拥有什么 | 加相关功能时 |
|------|---------|------------|
| `comfyui_client` | 与 ComfyUI 的全部 HTTP 对话（提交/轮询/取图/打断/上传），统一 `ComfyError`。`/prompt` 允许 30 秒完成大型工作流的同步节点校验，禁止在返回 `prompt_id` 前用 10 秒短超时掐断。`history.status_str=error`/`execution_error` 必须归一为带节点与异常摘要的 `failed` 终态，禁止因 `completed=false` 误报为持续 `running`。多阶段采样只把持久化 `output` 当最终图；主输出节点误指 `temp` 时回退全图的持久化输出 | 新增 ComfyUI 交互 → 加到这里，别在路由直接拼请求；结果状态变化同步前端 `GenResult`、轮询/恢复分支和双端测试 |
| `comfy_launcher` | ComfyUI **本地进程**生命周期：配置读写 + 独立解释器发现（整合包、`.venv/venv`、显式路径）+ 写 extra-paths YAML + 拉起子进程，持有进程句柄，统一 `LaunchError`。Windows 外部进程按监听端口终止时用独立管道读取 `netstat`，不得假定 `subprocess.run().stdout` 非空。禁止回退应用 Embedded Runtime。**与 comfyui_client 是两个接缝（进程 vs HTTP），别合并** | 改启动/配置逻辑 → 这里，别在路由写 subprocess/文件 I/O |
| `workflow_submission` | ComfyUI 模板/画布提交事务：校验在线、读取、转换、注入和提交；`/submit_graph` 提交前按实时 `object_info.output_node` 验证至少一个可执行输出节点 | `/submit` 与 `/submit_graph` 路由只映射 `WorkflowSubmissionError`；无输出残片必须在本地拒绝，不得请求 ComfyUI |
| `model_downloader` / `workflow_downloader` | CivitAI、Hugging Face 模型与工作流下载任务；统一公开 `phase/downloaded/total/speed_bps/target_dir/saved_files`，流式写临时文件后再校验并落盘。状态经 `task_progress_store` 节流持久化，重启后保留历史并把在途任务归一为 `interrupted` | 新下载源仍复用同一任务状态合同；未知总量显示不定进度，失败必须清理 `.part`，禁止先整文件读入内存或用完成提示代替真实终态 |
| `node_update` | 节点 Git 安装/更新、ComfyUI 本体拉取/切版和依赖预检；解析 Git/Pip 输出为对象进度、接收字节、速度与依赖清单，HEAD 变化才算真实更新。当前任务原子持久化，重启中断不会继续伪装运行 | 节点安装只允许可信 Git HTTPS 源并限制在 `custom_nodes`；共享依赖默认停在确认态；路由和前端统一消费 `UpdateProgress`，不得恢复 Manager 黑盒队列的“队列清空即成功”判断 |
| `task_progress_store` | 后台任务进度的 UTF-8 JSON 原子快照、容量限制和重启中断归一 | 只存可恢复的状态，不存密钥和线程对象；高频调用方必须节流写盘，测试必须隔离真实 `DATA_DIR` |
| `llm` | 建模型 + `normalize_base_url`（/v1 规则）+ `flatten_content`（分段展平）+ `chat_messages_stream`（流式增量与完整原文双输出） | 需要 base_url 归一、展平或流式调用 → 调这里，别内联；流式已由项目层控制重试，SDK 内层重试关闭，避免重试乘法 |
| `agent_graph` | 多 Agent 主编排。编辑模式（`workspace_mode=edit`）默认直达 `edit_node`，但**委派强命令先放行到 `plan`**（2026-09-05 修复：制作合集卡/角色卡/世界书类请求在编辑模式下不再被只读编辑 Agent 拦截，见 `plan_compiler.is_doc_delegation_intent` 的卡交付模式）；无卡对话、带附件请求仍由 Supervisor 语义分派；**有卡纯文本直达 Roleplay**；**委派强命令层**（Autopilot P1，规模词×资产动作/显式计划语言/卡与文档交付，疑问信号一票否决）零模型直达 `plan` 路由，模糊表达保持剧情默认（fail-safe：委派误入只编译计划可取消，剧情误入污染记忆不可逆）。**智能编造执行分派（2026-09-06）**：full 模式走自由循环；approval 模式内容生成型任务（卡/书/文档交付，固化02/03 场景，`is_doc_delegation_intent` 命中）走**自由循环断点审批**（durable/expensive 能力逐个确认，checkpoint 存 `fabric_checkpoint`，批准后 `grant_operation` 追加租约授权并从断点续跑）；approval 其余（批量生图等确定性任务）走计划编译+机械执行——机械执行器不调 LLM，承载不了「读素材→LLM 写条目」的内容生成 | 编辑模式普通排错请求仍直达 `edit_node`（受限文件工具）；委派强命令不得进入 Roleplay/生图/MCP 分派；模糊任务理解仍改 Supervisor；强执行规则必须保守、可单测；**路由界限改动必须先分明三轴判别并跑路由单测矩阵**（见 docs/ROADMAP-AUTOPILOT.md「路由界限」）；approval 自由循环意图判定同样只读 `ctx.message`（剥离附件全文），防小说正文污染疑问/规模词 |
| `agent_support` / `agent_retrieval` / `agent_context_assembly` / `agent_fabric` / `agent_agency` / `agent_stream` | `agent_graph` 方案 B 拆分（2026-09-16，主文件 5184→1736，见 `docs/memory/agent-graph-seam-plan-2026-09-16.md`）后的六个单向接缝域，全部逐字节搬移 + re-export 兼容：`agent_support`=跨簇微助手（`_proxy_kw`）；`agent_retrieval`=检索召回域（世界书/RAG/知识召回接缝）；`agent_context_assembly`=上下文装配域（历史/角色/世界书/人设注入文本装配）；`agent_fabric`=自由循环域（fabric 循环/断点审批/checkpoint/知识目录）；`agent_agency`=能动性域（表格维护/`_agency_writeback`/媒体动作提取）；`agent_stream`=流式出口域（流式开关/续写消息/插画事件排序）。主文件留驻：AgentState、supervisor 路由、八个媒体节点、`plan_compiler_node`、`roleplay_node`、`_build_graph`/`_graph`、`stream_multi_agent` | 依赖**严格单向**：`agent_graph` → 六模块，六模块互不反向、不 import `agent_graph`（勿新增反向 import）；改域内逻辑 → 对应模块；**monkeypatch 判据=谁在调用期查这个名字**——消费方已搬走的 patch 改指新属主，消费方留驻的照旧打 `ag` 走 re-export；re-export 名单是兼容层，新代码不得新增跨簇裸引用 |
| `roleplay_turn` | 剧情回合执行事务：主生成后依次做状态/插画写回、输出正则、原位锚点、正文与插画请求提前发布，再同步完成本轮表格、纪要、Curator/世界书维护后返回 Agent 终态 | 改发布与维护顺序只动这里并测公开 Interface；正文/插画必须先发布以启动 ComfyUI，但下一轮 Agent 不得在维护完成前读取旧状态；维护响应不得写入对话 |
| `edit_agent` / `edit_agent_profiles` / `edit_session` / `edit_publication` | 编辑主管确定性选择六类专家；`edit_session` 在工具层强制只读/写入意图、先列后写、先读后覆盖和写前+完成前机械校验；`edit_publication` 把小仓库产物受控发布到后端设置中的 characterDir/presetDir，并支持 PNG 附件保存为头像/表情；排错可按当前 repo/turn 读取脱敏 Trace | 普通文件工具只操作当前小仓库；源库根只读后端 user_state，不信任请求路径；角色卡内部必须扁平且 worldbook/regex 只走侧车；PNG 卡元数据迁移仍走导入 UI |
| `edit_import_adapter` | 外部 JSON 资源到 Demiurge 的确定性转换：纯角色卡只生成扁平 card，内嵌内容拆为 worldbook/regex 侧车；独立世界书统一 entries/key/enabled；预设剥离连接鉴权并补项目扩展；正则补 ID 和运行字段后编译校验 | 只读当前小仓库内源 JSON，默认拒绝覆盖目标且不删除源文件；PNG 卡必须走现有角色卡导入入口保留头像与元数据 |
| `edit_artifacts` | 编辑产物机械校验：Demiurge 归一化角色卡、世界书侧车、预设 identifier/order、注入字段、`thinking_chains`、三层正则字段与编译、Python 语法及基础 JSON | 自动推断必须先按项目文件名和归一化卡字段识别，禁止因 `card.json.regex_scripts` 把整卡误判为正则；格式失败写 Trace，禁止把模型自述当验证结果 |
| `project_files` | 当前小仓库文件权限单一属主：根由 `repo_meta.output_dir_from_state()` + `repo_folder(repo_id)` 后端解析；提供列出、UTF-8 读取、原子写入、精确替换 | 拒绝绝对路径、`.`/`..`、符号链接逃逸、二进制/非 UTF-8、目录和超过 1 MiB 的文件；不提供删除；模型/前端不能指定根目录 |
| `agent_graph.roleplay_node` | 酒馆 Agent（剧情扮演节点）：作品可绑定多张角色卡，并由 `opening_card_name` 决定空会话第一句及首个真实剧情回复唯一可注入的角色描述；后续在世界书检索后，只把本轮输入直接角色名与本轮输入精确触发的世界书 key 当作角色出场证据，均未命中时才回退最近一条 AI 剧情。历史回退按角色最后出现分句识别离开/离场/不在、否定离场及重新入场；角色名采用最长非重叠实体匹配，避免“莉亚/塞西莉亚”串卡。`constant`、Dense、BM25 条目可以提供设定但不得激活角色卡。`_resolve_personas` 只发送选中卡的非空字段，并把 `description/personality/scenario/mes_example` 分别送入对应预设 marker；禁止因已绑定而全量发送或把四类字段折叠成描述。后续叠世界书/表格记忆。模型漏 `<status>` 时沿用已持久化快照；未闭合 `<状态更新>` 或 `<illustration>` 仍按尾部控制块剥离；本地高潮兜底锚点被输出正则改写后，只能在最终显示正文重新定位。状态/表格/插画后处理异常时必须用结构化解析器返回干净可见正文，禁止把原始控制块或提示词回退给前端 | 单卡 `card_name` 仅为开场卡兼容别名；其他绑定卡不得出现在开场；同轮多卡仅在本轮均有直接/精确证据时注入；空描述不注入；有卡作品的通用对话统一并入 roleplay |
| `agent_context` | Agent 上下文窗口：各取 6 条、token 预算、历史文本与依赖上文的执行提示词整理。生成历史按 **前端显式可见历史 → 已存在的聊天快照 → 仅快照文件不存在时才回退 checkpoint** 取值；显式 `[]` 和空快照都表示历史已清空，禁止旧 checkpoint 复活已删消息 | 改上下文选择/裁剪 → 这里；`agent_graph` 不内联 token 算法。任何新入口都必须遵守同一历史优先级 |
| `task_session_store` | 任务会话登记单一属主（上下文合同 P1，2026-09-11）：讨论/委派型任务**首次命中即登记**，接续轮先查登记（显式状态，不从对话猜词）；材料（附件 fileId / 首链任务句 `created_intent` / 产物路径 `product_paths`）每轮从登记重建。TTL 6h、一线程一活跃任务、上限 50 条，存储复用 `task_progress_store` | 改接续判定或材料重建 → 这里；**禁止并入 `fabric_checkpoints` / `recipe_offers` 命名空间**（会让对话里的「批准」被当成别的语义授权）；登记失败不得阻断主流程 |
| `prompt_compiler` | 最终消息编译接缝：OpenAI 兼容档保留历史后 system 原位；Claude 兼容档把历史后约束编译为贴近末轮 user 的本轮执行合同，不得全部前移到 system 头。Roleplay 启用自动插画时，完整插画说明仍在头部事实上下文，但还必须把短 `near_generation_contract` 作为最后一条生成近端 system 放在当前 user 前，避免长预设、世界书及“全部中文”等宽泛规则稀释当前 Profile 格式与事实覆盖要求。输出最终 messages、provider profile 与逐段位置 manifest | `provider_profile` 描述接口 wire，不描述模型家族；OpenAI Chat Completions 代理即使承载 Claude 模型仍选 OpenAI 兼容。旧设置统一迁到 OpenAI 档，只有 v2 后用户明确选择才保留 Claude 档；Roleplay 禁止按模型名猜供应商。Compiler 只拥有消息位置，不拥有预设、世界书或表格内容 |
| `agent_contracts` | 编排契约单一属主：`RunContext`(dataclass) + `ModelConfig` + `AgentEvent`。多卡固定字段为 `card_names/opening_card_name`，`card_name` 是兼容别名；生图外貌来源为 `appearance_source=worldbook|character_card`。`history_override` 保存本次请求显式上传的可见历史；`stream_output/stream_sink` 控制节点实时增量出口。**RunContext 既是 dataclass 又当 dict 用**，由 `extras` 与 `_legacy()` 支撑 | 新固定字段必须同时加入 dataclass、`_legacy()`、实时请求、后台队列和双端测试；图节点新增跨节点标记还必须声明进 `AgentState`；**动了这些方法必须重启后端** |
| `agent_request_context` | 即时请求与持久队列共用的 `RunContext` 构造真源；统一默认值、多卡去重、开场卡兼容、显式空历史和全部模型/代理/插画字段 | 新请求字段只在请求模型、前端 `AgentInvocation` 编码和这里各声明一次；禁止 router 或 worker 再手写第二套转换 |
| `agent_knowledge` | 固化知识库唯一属主（2026-09-04 skill 化）：`DATA_DIR/agent_knowledge/*.md`（gitignored 运行态）的目录/上限常量、元数据列举、正文读取与**注入模式配置**收敛于此；技能文档带 frontmatter（`skill`/`whenToUse`/`tools`，read 时 content 去头）。注入模式 `settings.agentKnowledge={mode: smart\|always, always_docs}`（user_state 真源）：smart（默认）= `agent_graph._knowledge_catalog_text` 只注入【固化技能库】目录行+指令，命中场景由模型先 `knowledge.load_doc` 拉全文（capability knowledge 类别，readonly）；always=老行为全量常驻（上限只管无 frontmatter 普通知识）；always_docs 名单回退常驻。GET/PUT `/agents/knowledge/config`（须在 `/knowledge/{name}` 前）；设置页 AgentPanel 有模式开关 | 目录/上限/配置读写只在此模块；三固化 skill 文档 tools 引用必须真实存在于 capability 注册表（测试合同防漂移）；novel.* 脚本辅助层（固化02 §2.5）独立服务 `novel_tools.py`，extract/charfacts 支持 work_dir 作品域 base（推导在 handlers，服务层纯路径） |
| `chat_agent_queue` | 忙时消息持久队列；headless worker 必须把 `MultiAgentRequest` 的工作区模式、历史、卡、预设、人设、世界书、插画、流式选择和模型参数完整还原成 `RunContext`。排队 payload 的历史只是入队时快照；任务认领执行时重新读取当前可见 `chat_snapshot`，保证看到前一轮最终正文。认领跳过 `thread_admission` 活跃 thread 的任务（保持 queued 可取消，消除认领→RunAlreadyActive→退回抖动）；cancel 支持 queued 直接取消与 running 协作取消（发取消信号后抢占标记 cancelled，worker 迟到的 `_finish` 被 `status='running'` 守卫拒绝、终态不复活；已落终态的任务取消时 `get()` 以事实为准不覆盖）；`_public` 只暴露 payload 的 `images/message` 摘要，不泄露含密钥的完整 payload | 新增直连参数时同步补队列映射与回归测试；编辑任务排队后仍必须走编辑 Agent；禁止用入队时旧 history 覆盖执行时快照 |
| `thread_admission` | 线程准入单一真源：同一 thread 只允许一个活动运行（抢占登记，重复抛 `RunAlreadyActive`）、带所有权校验的 release（旧运行不得误删新登记）、协作取消信号；30 分钟泄漏看门狗在 `is_active/active_threads/admit` 前自动清理崩溃未 release 的登记并记日志，防止单次崩溃永久阻塞该 thread | 零重依赖（threading/dataclass）。「谁在跑/能不能再开/如何取消」只在这里改，agent_runner 与队列 worker 复用 |
| `run_trace` | Agent 单轮结构化追踪：每次请求由 `RunContext.turn_id` 贯穿，按 UTF-8 JSONL 写 `backend/data/logs/agent-trace.jsonl`；记录原始/处理后输入、Supervisor 与各 Agent、完整模型消息与输出、世界书/RAG 注入、纪要/知识写入、状态写回、独立表格维护及首尾错误。按大小轮转并递归脱敏密钥，追踪失败不阻断主流程 | 新增运行阶段先复用 `run_trace.emit(ctx, event, **data)`；不得把 API key/token 放入自由文本。环境变量：`LAF_AGENT_TRACE`、`LAF_AGENT_TRACE_MAX_BYTES`、`LAF_AGENT_TRACE_BACKUPS` |
| `tool_agent_adapter` | 把遗留 `image_agent` ReAct 流适配成专家节点结果 | `agent_graph` 不直接依赖其长参数和事件细节；替换旧实现只改 Adapter 后面 |
| `generation_approval` | 提示词审批状态机 + 已批准的图像/视频执行 + 失败语义 | 改确认/更改/取消/重提流程 → 这里；`agent_graph` 只调用其 Interface |
| `capability_registry` / `capability_handlers` | Autopilot 能力面单一属主（P0）：能力**显式注册**（不做 AST 扫描），注册期闸门校验 operation 唯一性与枚举字段；manifest 由 `scripts/generate_capability_manifest.py` 导出（`backend/app/generated/capability_manifest.json` 随源码发布），`--check` 进 npm/CI 门禁防漂移；`validate_handlers` 逐条 import 验证「模块：函数」；`with_availability` 运行时按已配置模型打 available 标记。handlers 只做薄适配（参数透传既有 services + 逐条失败隔离） | 注册新能力 → 这里加条目并重新生成 manifest；agent 内部协作用的中间服务不进清单；handler 不得藏业务，更不得退回任意 shell/MCP（未知动作拒绝不降级） |
| `plan_compiler` / `plan_validator` | Autopilot P1 计划编译：`is_delegation_intent` 零 LLM 委派判定（路由界限强命令层）；`compile_plan` 经 `structured_output` 把意图编译成 `GenerationPlan`（校验失败带错误重试一次，仍败如实返回不编造）；`plan_validator` 纯函数校验能力存在/params 合 schema/inputs_from 无环/模型缺口/审批汇总一致性/budgets 防巨型计划/**params 路径必须落在作品域内（防越权写，执行期由 capability_sandbox path 租约兜底）**；`save_plan` 落 `<作品>/plans/*.plan.json`（执行真源）+ 单向渲染 `.plan.md`（手改不回灌）。编译节点只编译不执行，编译后自动投递 plan_tasks 队列 | 改编译 prompt/校验规则 → 这里；计划执行必须走 P2 plan_tasks，不得在编译节点偷偷执行；每计划必须带 budgets |
| `plan_tasks` | 智能编造计划 P2/P3/P4 执行器单一属主：SQLite `plan_tasks`+`plan_task_steps` 租约式 FIFO worker（心跳/唤醒/取消，`main.py` 常驻）；步骤按 capability_registry 的「模块:函数」分发（未注册能力拒绝执行，无 shell/MCP 兜底）；**两档访问标准（P1）**：`approval`（默认）走审批闸门（durable/expensive 与越域 readonly 读取执行前 capability_sandbox.authorize，无租约→awaiting_approval，批准=一次性租约 ttl=预算×2）；`full`（用户显式开启）提交时自动签通配租约（approved_by=full_mode，审计可查），免逐项审批、越域只读随通配租约放行，但**配额照扣**（expensive done+失败尝试均耗配额）、写入路径域照拦；失败隔离（单步 failed 其余 blocked→partial）、Doom Loop（同步骤连败≥2→blocked）、终态判定（全 done/skipped 才 done，部分产出→partial）；inputs_from 链式传参（点引用/整包回退/同键累积）；幂等（规范化 hash 去重）+ 配方固化/实例化 + 进度快照进 task_progress_store。submit/prompt_ids 等运行环境值由编译器/执行器归一，**模型产出的占位与编造值一律被确定性覆写**。full 不豁免路径域校验（plan_validator 提交期硬闸门） | 加新执行语义 → 这里与 capability_registry；改审批/配额阈值 → approve_task/`_run_task` 闸门段；计划链路不得旁路队列直调 handler |
| `recipe_match` / `recipe_offer_store` | 固化流程**复用判定**与**事前固化询问**单一属主（2026-09-10）：`recipe_match` 零 LLM 判定「本轮请求在已保留配方里有无同类」（中文 2-gram shingle + Jaccard，阈值 0.55，短句不判，**宁可漏问不可多问**；只比 `status=saved`）与「配方能否硬重放」（`replay_safety`：步骤参数含 >200 字长文本即判 `skeleton`——内容型参数固化时被原样存下，硬重放只会把旧内容再写一遍）；`recipe_offer_store` 存待答询问（命名空间 `recipe_offers`）。**红线：询问不得并入 `fabric_checkpoints`**——后者 `_RESUMABLE` 含 `awaiting_approval`，而 `_fabric_approval_word` 会把 `pending()` 第一条当「待批准的工具」去 `grant_operation`，混入即让一句「批准」变成 durable 能力授权 | 改判定口径/阈值 → 只改 `recipe_match`；改询问字段/失效策略 → 只改 `recipe_offer_store`；答复短词（`要`/`直接跑`）必须保留 `supervisor_node` 里的**零 LLM 强制路由回 plan**（编辑模式分支之前），否则会被主管判成普通对话、询问永远悬空 |
| `prose_style` | 剧情正文去 AI 味词表单一属主（**一份两用**）：`lint` 纯函数确定性检测（固定搭配/标点密度/句式模板/自问自答/句长节拍器/跨轮开场趋同），检测前 `restore_jailbreak` 只读还原拆字；`effective_phrases` 合成内置词表+用户增−删（`data/prose_style.json`）；`style_prompt_segment` 供 roleplay system 生成侧预防（enabled=False 时 system 逐字节不变）。文风诊断码属主在本模块，narrative_ci 并流复用 | 词表增删/新检测规则 → 这里，lint 与生成侧注入共用同一属主，两处各自维护即漂移；**检测不涂改**：只产出诊断，禁止自动改写正文真源 |
| `style_review` | S2 LLM 活人感通审：采样闸门（`review_every` 每轮 N 一次，0=关，正文≥120 字）→ `structured_output` 结构化判定 → `style_living_review` 诊断入 Narrative CI 诊断流（info 级，处置走既有 RESOLUTIONS 生命周期）。走 `_agency_maintenance` 维护通道，不新开并行通道 | 调采样频率/判定维度 → 这里；失败静默降级只记 trace，永不阻断正文或维护 |
| `image_gen` | 云端文生图 `/images/generations` 与带参考图 `/images/edits`，统一超时、质量参数和 64–3840px 尺寸边界；`_load_image_bytes` 支持 data-uri/http(s)/**本地文件路径**(角色底图,桌面单机后端直读同机文件)，并对 `/comfyui/local-view` 回环 URL 剥离显式代理（系统代理无法转发 localhost → 502），外部 URL 代理行为不变 | 新增图像供应商请求规则 → 这里，调用方不拼 payload |
| `image_prompt_profiles` | 多元数据插入提示词协议单一属主，三层顺序固定：①剧情事实底座 ②跨模型艺术决策 ③转换为 Krea2、Anima、GPT Image/Banana 或 Niji。视觉编译采用三层合同：已明确画面事实直接采用且禁止改写；生成完整画面所需的当前服装、动作/姿态、地点环境、人物位置、镜头、构图、光影和背景任一缺失时，同轮 Agent 必须依据上下文补出具体答案，禁止留空、占位语或通用模板；补全必须可由时间、地点、天气、情绪、人物处境和视觉因果解释，且不得引入重要新人物、关键道具、新事件或改变剧情结果。角色姓名只作“高潮人物→外貌条目→LoRA 配置”的本地关联键，四 Profile 最终正文移除原姓名；多角色分别落实外貌、服装、位置与动作。正常路径以同轮 Agent 输出的开放 `visual_facts[{kind,fact,evidence}]` 为通用视觉事实合同：kind 不限枚举，fact 是英文可画事实，evidence 必须逐字存在于本轮高潮正文；四 Profile 逐条验收 fact。Krea2 的具体人物、当前服装、动作、道具、地点和空间关系必须构成段落主体，质量描述只作收尾；Anima 固定两行：首行把作品固定质量 tags 与全部具体内容 tags 连成同一逗号序列并以逗号收尾，次行只写解释同一事实的一至三句连续英文自然语言；不得把 tags 与文段混在第二行，也不得输出 `Her body:`、`Bound:`、`Position:` 等标题式冒号小段。归一器必须幂等，重复编译不得丢内容 tags；若 Agent 误写成“首行环境 tags＋次行分号人物/动作描述”，只把其中具体英文画面事实确定性复制到首行并把次行改成句子，抽象艺术分析、权重主体和拒答仍不得进入成稿。字段账本对英文稳定外貌与当前服装逐项验收，同义描述可保留 Agent 动作方案，但精确条目事实必须补入两行。前端最终提交只能替换已知识别的质量 tags，不能整行覆盖内容 tags；二次元媒介 tags 也只能追加在首行，第二行文段保持不变。任何没有可靠英文专业名词的剧情物件均由同轮 `visual_facts` 按“材质→几何外形与尺度→可见结构/运动→当前功能→实际交互”建立可画身份，只填写正文证据支持的维度；禁止音译、照抄专名、写 `unknown object`，也禁止为某个物件建立固定场景 fallback。后续缺陷必须修正高潮规划、证据提取、通用事实协议或模板编译整体合同。坏 JSON、拒答或漏项时只能保留通用规则已经确认的事实，宁可明确缺失也不得猜测或注入特定内容。`inline_generation_instruction` 是头部完整合同，`near_generation_contract` 是生成近端短合同；两者仍属于同一次主模型调用，不触发第二次模型调用。Profile 完成后 LoRA 元数据阶段才机械注入精确触发词和作者质量建议 | 新增模型提示词协议或格式约束 → 只改这里和对应测试；开放事实必须有正文证据；负面词禁止混入正向文本 |
| `lora_index` | LoRA 数据保存单一属主：按完整文件名保存触发词、作者建议提示词和建议权重；建议权重归一到 0–2，自动元数据同步不得覆盖手填值 | LoRA 选择器只采用当前选中模型的建议权重；编辑弹窗可把作者示例提炼并回填为质量/风格/光影/材质/作者标签。自动插画与工作流卡只读取当前实际生效 LoRA，排除分级词、人物外貌、服装、动作、关系、场景事实及 `close-up/wide angle/shot/composition/perspective` 等镜头控制。工作流“选择完毕”检测最终 API 图，有精确记录时先询问是否覆盖；同意后才覆盖权重，把触发词放正向 CLIP 第一行、去重质量词放第二行开头，负向 CLIP 不改。`/s` 只提交已确认图，不得再次覆盖用户值；禁止回退旧记录 |
| `rag_backend` | RAG 基础设施 Adapter；`EmbedConfig`、OpenAI/Ollama/本地嵌入兼容、嵌入模型缓存与 Chroma 单例缓存；localhost/127.0.0.1/::1 回环端点强制直连，显式代理也不得介入 | 新增嵌入后端或修改缓存键 → 只改这里；上层不直接创建 Chroma/Embedding |
| `rag_store` | 普通知识库与生成资产索引：系统资料、仓库文档、generation 元数据、Hybrid 检索入口；`include_system` 控制是否并入全局系统库，剧情召回固定 `False` 只读当前作品 | 新增普通知识库操作 → 加到这里，签名收 `EmbedConfig`；不得再放节点索引逻辑 |
| `rag_retrieval` | 普通知识库的纯 BM25Plus、RRF 融合；中文连续文本用双字 token，禁止以“与/用”等单字公共噪声制造跨角色命中；不依赖 Chroma、路由或工作流 | 修改普通 RAG 排序算法 → 这里；I/O 留给 `rag_store/rag_backend` |
| `worldbook` | 卡内嵌/独立世界书检索：解析 character_book 条目、constant 常驻 vs 非常驻拆分、按作品建独立 collection `worldbook_<repo_id>`。索引按正文 hash **条目级增删**，Curator 改一条只重嵌一条；运行时由 `schedule_index` 先做本地差异检查再后台同步，无变化不建线程。首次确有缺失条目时经 `rag_status/worldbook` 即时提示，主对话仍立即用关键词+内存 BM25；已有向量则 Dense+BM25，不得让索引阻塞主 Roleplay。激活窗口固定为本轮输入＋最近一组对话，不扫描全部旧历史；`assemble_selection` 同时返回全部注入 index 与 `keyword_indices` 激活来源，优先级 keyword→constant→retrieved | 改世界书激活/注入 → 这里；collection 与剧情/生图的 `repo_<id>` 物理隔离；主 Roleplay 的实际选择结果是本轮 Curator 可更新范围的唯一真源；只有当前输入精确 key 可参与角色卡选择，constant/Dense/BM25 不得冒充角色出场；关键词触发是 ST 语义，勿删 |
| `rag_middleware` | 搭建需求的查询拆分、架构能力映射和可选 LLM 重写 | 新增模型架构/能力同义词 → 只改这里，别塞进搭建编排 |
| `node_store` | 节点索引存储：完整包管理、能力分块、迁移就绪判断、单路 Dense+BM25+RRF；分块命中按 `pack_id` 聚合 | 改 collection、分块持久化或单路召回 → 这里；不得依赖 `rag_store` |
| `node_index` | 节点索引编排：`object_info` 同步、卸载包清理、多查询加权融合、MMR、一次最终精排 | 改同步、多查询或排序策略 → 这里；安装事实仍不归它拥有 |
| `reranker` | 普通知识库和节点索引共用的可选 Cross-Encoder Adapter；拥有缓存代际、活跃推理计数和 Accelerator Handoff | 权重不完整、依赖缺失或推理失败必须返回空；ComfyUI 提交前通过其 Interface 释放显存 |
| `node_candidates` | 节点候选解析；`object_info` 是安装事实，RAG 只补候选 | 四种搭建模式统一消费该 Module，不能用 RAG 空结果判断未安装 |
| `workflow_build_turn` | 搭建回合：统一需求校验、完整历史视图、当前工作流快照、查询优化与节点候选 | 四种搭建模式必须先准备同一个 Build Turn，不能各自裁剪历史或重建查询 |
| `workflow_graph_rules` | 工作流图规则：解释 `object_info`、规整 widget、拆缺失节点、硬校验与结构审核 | 新增图规则 → 只改这里；`workflow_builder` 只消费规则结果 |
| `workflow_builder` | 完整、增量、直连和顾问四种搭建策略；模型调用、重试及结果组装 | 不得重新拥有历史整理、节点候选或图规则 Implementation |
| `workflow_port_planner` | 工作流输入口 AI 编排事务：提示词、模型调用、JSON 容错、强制意图、模型名校验、LoRA 注入、色板注入 | `ai_text` router 只映射 `WorkflowPortPlanError`；增强顺序固定为模型校验 → LoRA → 色板 |
| `chat_stream_protocol` | 对话流事件 wire 协议 v1；把 Agent 内部领域事件编码成 `protocol/version/type/data`。流式正文用 `delta`，完成后用 `replace` 校正为清洗/写回后的最终文本；`illustrate_request.id + offset` 保持媒体槽稳定且原位，`turn_id` 随事件贯穿到前端供提交回报关联本轮 Trace | 新增事件先扩展这里和前端解码联合；不允许路由手拼 SSE payload；媒体槽 ID 不得在提交/轮询/恢复阶段重建 |
| `sse` | SSE 传输 Adapter；分帧、异常信封与 `[DONE]` 收尾 | 只负责传输，payload 语义交给 `chat_stream_protocol` |
| `rag_evaluation` | Hit/MRR/Recall/延迟统计与 RAGAS 四字段记录构造 | 修改检索链后运行固定评估集；未运行 Judge 不得宣称生成质量合格 |
| `generation_store` | 「生成提交/完成→留存→入库→写快照」后端管线。Agent 获得 thread 所有权后、调用任何模型前，必须按 `user_message_id` 先将用户文字与输入图片幂等写入权威快照；已有前端富消息时保持原样。Roleplay 发出最终 `replace`/`illustrate_request` 时再即时持久化正文与稳定媒体槽，禁止形成“助手回复存在、触发它的用户消息缺失”的孤儿回合。自动路径调用 ComfyUI 前必须通过 `claim_illustration_submission` 原子认领 `messageId + slotId`，同槽重复/迟到事件失败关闭；ComfyUI 返回 `prompt_id` 后再通过 `persist_illustration_submission` 绑定同槽；带目标槽的自动插画只原位回填，不追加对话轮、不写提示词历史；失败写 `illustration.failed` Trace 并删除快照槽 | 后端出图后的持久化 → 走 `persist_image`；不得依赖前端生成结束后的全量快照补写用户消息；不得先提交 ComfyUI、再用 `slot_bound=false` 事后发现目标无效；自动插画目标消息已删除、槽已完成或已认领时禁止提交第二个任务；generation RAG 是资产库成员与提示词真源，禁止扫描磁盘自动补录；普通资产删除只删 RAG、保留文件，裂图清理只删可确认已丢失的 RAG——local-view 条目按磁盘存在性判定，legacy remote-view 直链仅在 ComfyUI 明确 404 时删，ComfyUI 未起/探测异常（无法判定存在性）一律保留 |
| `workflow_injector` | 纯注入（套值 + 提示词），无 I/O | 改注入规则 → 这里，可直接单测 |
| `image_store` / `image_utils` | 端点存图（抛异常）/ agent 存图（回退原 url） | 注意两者错误语义不同，别合并 |
| `chat_snapshot` | 会话显示与生成历史真源（前端完整消息流）。`to_prompt_history` 只提取快照里仍存在的 user/assistant 文本；`load_prompt_history` 在文件存在但为空或损坏时返回 `[]`，失败关闭，不能回退旧 checkpoint。前端完整保存带单调 `revision`，`save_if_newer` 拒绝较旧请求，并保留传入快照中同一槽的服务端 `submissionClaim`、`promptId` 或已完成媒体，防止旧前端状态把提交回滚成 pending；传入快照已删除的消息/槽仍尊重删除，禁止复活。`claim_media_slot_submission` 按 `messageId + slotId` 原子认领；`bind_media_slot_prompt` 持久化 ComfyUI prompt_id；`resolve_media_slot` 原位替换 pending 槽或已完成图片/视频；`remove_media_slot` 只在失败时删除 pending 槽并合并相邻正文。落点：配了"仓库文件夹"则随图片同落 `<仓库文件夹>/<作品名>/chat.json`，否则回退旧位置；`_path` 负责惰性迁移 | 路径解析在 `_path`；导入/导出走 `ai_chat`；服务端媒体状态只合并到前端仍保留的同槽，绝不能借此复活用户删除的数据 |
| `repo_meta` | 仓库元信息 + "仓库文件夹"根解析：`output_dir_from_state()` 读 user_state.json 的 settings.outputDir(单一真源)；目录名可读但目录身份只认 `_repo.json.id`。当前名字对应目录被另一 UUID 占用时使用 UUID 后缀隔离；改名遗留目录按 marker 找回并惰性迁移，禁止新同名仓库复用旧 `chat.json`。仓库暂时不在 user_state 时，生成归档仍按 marker 找回原目录。`rename_folder` 改名迁移文件夹+重写快照/RAG 绝对路径(`snap_folder=dst` 定位随文件夹移动后的 chat.json) | 作品文件夹命名/改名迁移 → 这里；`_repo.json` 标记是迁移识别"作品文件夹"的依据，仓库名不是身份 |
| `local_media` | 本地媒体白名单、MIME、Range 校验与分块读取 | `/local-view` 只做 FastAPI Response 适配；文件读取规则进这里 |
| `pathnames` | `safe_seg` 文件名清洗单点 | 需要清路径片段 → 用它 |
| `workflow_parser` / `workflow_convert` | UI↔API 转换；`PASSTHROUGH_TYPES` 共享。UI 的无名 `widgets_values` 优先按节点自身 `inputs[].widget.name` 还原，旧格式再回退实时 `object_info` 与内置表；新版 `COMBO` schema 也是 widget | 改穿透集 → 改 parser 的共享常量；**PrimitiveNode 差异是有意的，别合并**；不得让全量 `object_info` 超时导致模板默认参数丢失 |
| `character_card` | 角色卡格式单一属主：解析 TavernCard V1/V2/V3（JSON 或 PNG 内嵌 `chara`/`ccv3` tEXt，base64）、归一到 `NormalizedCard`、拆出内嵌 `character_book`/`regex_scripts`。纯逻辑无 I/O，可单测 | 新增卡格式/字段 → 只改这里；PNG tEXt 解析是自实现（无第三方依赖），别引库 |
| `character_store` | 角色卡落盘：每张卡=一个文件夹，`description/first_mes/creator_notes` 可原位编辑（空值有效），稳定头像为 `avatar.png`，可选表情为 `expressions/<表情名>.png`。绑定保存把整组卡快照到当前仓库；运行时按当前小仓库→父作品→旧快照→源库读取。`characterPortrait` 用最长非重叠角色名识别最后明确发言者，当前回复无角色名时参考最近一组“上一条 AI + 当前用户”保持非开场角色连续性；表情评分只看该发言者所在分句，避免多人情绪串用，再对该卡全部自定义表情名做词面+情绪语义评分；表情名不限制固定枚举，未命中回退头像 | 头像/表情只在角色卡的媒体弹窗管理且只接受 PNG，不得放回绑定弹窗；表情文件名应表达适用情绪/状态（可复合命名）；不得用全局第一角色名决定多角色头像；卡正文编辑保留其他字段和侧车；快照已有卡不覆盖 |
| `worldbook_store` | 独立世界书与当前小仓库快照落盘。Curator 上下文只渲染主 Roleplay 本轮实际注入的 index；`apply_repo_ops` 在服务端再次校验允许集合，越界 `worldbook_update` 拒绝，`worldbook_add` 仍允许。角色更新只替换唯一 `【剧情进展·动态】` 并保留基础底座 | 独立世界书增删改 → 这里；检索/条目解析仍归 `worldbook`。角色长期动态是主要更新对象；机制/规则/历史背景默认只读，除非正文明确永久改变且有直接 evidence；即时外观状态归状态表 |
| `regex_engine` | 正则引擎单一属主：对标 ST engine.js 纯逻辑。`run_scripts(text, placement, scripts, *, is_markdown, is_prompt, is_edit, depth, skip_depth_gated)`——三档过滤(markdownOnly仅显示/promptOnly仅发送/皆非改存储源)+placement(1用户输入/2AI输出/3快捷命令/5世界信息/6推理/**7出图提示词=Demiurge 扩展，破甲还原+清洗成干净 booru 串**)+depth门控；`skip_depth_gated`=跳过设了 min/maxDepth 的脚本，处理**本轮实时输入**时置真（深度语义只该作用于历史楼层，而 live 输入生成时尚未入历史，否则「删 history 最后一条用户消息」maxDepth=1 会把 depth=0 的当前输入误擦空→用户消息不进 prompt）；`from_st_dict` 归一 camelCase；JS→Python 方言转换(命名组 `(?<x>)`→`(?P<x>)`、flag、`$1`/`$<name>`/`{{match}}`/trimStrings)。无 I/O 可单测 | 改正则语义/新 placement → 这里；**前端 `lib/regexEngine.ts` 是同逻辑 JS 版**(显示层 markdownOnly 用，原生 JS 正则方言天然一致)，两者行为须对齐；roleplay_node 对 live 用户输入调 `_apply_regex(skip_depth_gated=True)` |
| `regex_store` | 全局正则脚本持久化：`data/regex_scripts.json`（ST 格式数组，跨作品生效）。区别于卡内嵌 `regex.json`(随卡、`character_store.read_regex` 读)。落盘仿 `agent_store` | 全局正则增删改 → 这里；`agent_graph._resolve_regex_scripts` 合并全局+卡内喂引擎，前端「正则」按钮(顶栏方法功能栏)管这组 |
| `preset_store` | 偏置预设单一属主：解析 ST OpenAI 预设(prompts/prompt_order/8 marker/采样参数，如 GrayWill)+ `assemble_messages(preset, markers, history)` 按各片段 role 组装**多条消息**(chatHistory marker 处原位插历史，ST 深度注入语义) + `assemble_system` 单串档(降级) + `substitute_macros`(`{{char}}`/`{{user}}`，缺省 user 回退「我」；**`{{lastUserMessage}}`/`{{lastCharMessage}}`**——ST 深度重注入范式配套宏，大小写不敏感，**无对应 marker 才留字面**避免误清空；marker 值也过替换) + **`select_chains(preset, scene, affinity, turn)`**(按真状态选思维链 `thinking_chains`，返回尾部/头部)。落盘 `presetDir/<安全名>.json`；预设正则兼容 Demiurge 根键 `regexScripts` 与 ST 原生 `extensions.regex_scripts`，编辑时必须写回原位置，禁止制造两个真源 | 预设解析/组装/选链/增删改 → 这里；`roleplay_node._resolve_preset` 有激活预设走它(marker 填卡字段+世界书+`last_user_message`(=本轮实时输入)/`last_char_message`(=历史末条 AI)，采样温度透传，选中链尾部作独立 system 落历史后)，前端「预设」按钮(顶栏方法功能栏，仅剧情模式)管这组含思维链编辑区；**重注入 prompt(用 `{{lastUserMessage}}`)+ `skip_depth_gated` 是防用户消息被历史级删除正则擦空的双保险**。GrayWill 已启用「用户最新输入」且保持「所有最新输入」关闭（二选一）；该项在 chatHistory 后（`order[130]` vs `order[33]`），组装为完整独立 `role=user` 消息且无宏残留。预设热读，纯数据改动无需重启；RunContext 代码改动仍需重启 |

编辑模式协议：前端兼容 ID 仍为 `code`，wire 统一序列化为 `workspace_mode=edit`；`RunContext.workspace_mode` 是后端真源，实时与排队路径必须一致。编辑主管根据本轮文本确定性选择六类专家，专家配置由 `builtin_agents` 暴露。当前作品根只能由 `project_files` 根据后端状态和 `repo_id` 解析；角色卡/预设发布根只能由 `repo_meta.setting_dir_from_state` 读取后端持久化设置。

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
| `useChatSession` | 聊天会话引擎：messages、聊天/队列编排、快照与媒体落盘 Adapter。新输入只在 Agent 槽占用时进入持久队列；ComfyUI 槽占用时直接开始下一轮对话。快照图片瘦身是异步事务：只有消息数组仍是任务起点的同一版本、仓库仍相同且 Agent 未启动时，才允许写 localStorage、后端快照和 React 内存；版本失效必须在任何写入前整体放弃。删除、重新生成、显式历史、导航后台化和自动插画提交合同保持不变；ComfyUI pending/轮询/恢复/finalize 顺序委托 `workflowGenerationRuntime` | 不得只保护 `setMessages` 而仍让旧瘦身任务写持久层；不得重新内联 pending localStorage、轮询计数或恢复分支；不得用 `wfRunning` 阻塞普通对话；界面提示、媒体原位回填和进度 WS 留在 Hook |
| `useChatAgentQueue` / `useChatTransfer` | 队列提交/取消/恢复和会话导入导出事务 | `useChatSession` 只传依赖与消费结果，不得重新复制 API 顺序 |
| `useChatPresentationAssets` / `useChatUnreadTracker` | 角色头像表情投影与后台完成未读跟踪 | `ChatView` 只渲染投影结果，不直接维护跨刷新状态 |
| `useTableWorkflows` | 表格统计、重建、手动补表与覆盖确认的前端事务 | `TableModal` 只保留弹窗本地输入和展示，不直接编排 API |
| `chatGeneration` | 生成流程的纯判定/整形：图像门、文本打分、快照瘦身、纯文本历史、重跑消息、当前 LoRA 精确绑定和作者建议质量词筛选。提示词组装是固定两阶段合同：先由 Profile 融合「剧情高潮提炼＋角色条目稳定外貌」；Profile 完成后再按实际加载 LoRA 的完整文件名查表，机械注入精确触发词和筛选后的作者质量建议，并与已生成提示词大小写不敏感去重；查表失败必须显式失败，禁止提交缺失第三阶段的图。Anima 最终结构固定为首行「当前 LoRA 触发词 → 作者建议质量词 → 作品固定质量词 → 剧情内容 tags」的单一逗号序列，第二行仅为英文解释文段；`imagePromptProfiles.illustrationTemplateValues` 按语义独立组值 | pending/轮询/finalize 不属于此 Module；提示词质量词不受尺寸换算影响，采样参数仍归工作流模板 |
| `generationLifecycle` | Agent/ComfyUI 正交双槽 reducer + 派生 selector；只由 Agent 槽阻塞新对话，工作流槽继续独立保留 prompt、进度和停止所有权。**新增生成状态改这里，别加影子 ref** |
| `workflowGenerationRuntime` | ComfyUI 生成生命周期深模块：可同时持有 ComfyUI FIFO 中多个 `prompt_id`，负责 pending 存储、提交时 `threadId/repoId/outputDir` 归属快照、提交后守望、恢复检查、轮询节奏、连续丢失、超时保留和幂等 finalize 双闸 | Hook 通过 Observer 接收完成/失败/释放/超时；前台与恢复路径必须共用同一 Runtime 实例。新任务提交给 ComfyUI 原生队列，不在浏览器另造易丢失的预提交队列；仓库切换不得改变已提交任务的归属 |
| `workflowOrchestration` | 工作流输入口编排 hook（读节点→AI 出计划→写画布） |
| `workflowCapture` | laf_lock 帧事务：单次请求/回复及隐藏完整画布的 load→ops→原生 API 图抓取；统一来源校验、重试、超时和清理。NodeCard IFrame 载入前通过 `clearComfyStorage` 清理 localStorage/sessionStorage 中工作流残留，防止之前打开的大工作流被 ComfyUI 会话恢复覆盖导致只显示整图而非单个节点。`clearComfyStorage` 是 async（localStorage/sessionStorage 同步清 + `await _clearComfyIndexedDB` 等 IndexedDB 删除完成）；`installLoadGraphLock` 在文件顶层 + LOCK/FULL setup 双点包装 `app.loadGraphData` 加互斥锁（任意时刻只跑一个载图），防止 ComfyUI 初始化恢复流程 `loadGraphData(旧图)` 与 laf_lock `loadGraphData(新图)` 交叉造成 `_nodes` 同 id 重复节点/丢节点；`applyLoad` 有 `applyLoadRunning` 防重入 | `WorkflowCard`、`AIBuildView` 不得自行监听同类 `api_prompt` 生命周期；节点局部画布的自愈时序仍归各自展示场景 |
| `workflowTemplateExposure` | 参数清单与画布选择节点时，将未连线字段确定性转换为 `ExposedField`；字段名、label、semantic、默认值均保持原工作流定义，节点类型只生成不可见内部 binding | 旧人工别名迁移到 binding；禁止仅凭 `width/height/text/image` 猜用途；移除节点同步移除暴露字段 |
| `workflowLoraData` | 多元数据生成的 LoRA 确定性补全：按最终 API 图精确识别 LoRA，提炼作者质量标签，生成建议权重与正向 CLIP 覆盖 ops；触发词固定第一行，去重质量词固定第二行开头，负向 CLIP 不得修改 |
| `viewRouting` | `parseHash`/`buildHash`/`calcSize` 纯函数 |
| `lafLock` | laf_lock 子帧 postMessage 协议原语（`lockUrl`/`postToFrame`/`isLafMessage`） |
| `opResults` | `fmtOpResults` 编排结果格式化 |
| `workflowTemplatePicker` | 工作流模板搜索、最近记录排序与 localStorage 持久化 |
| `generationPreferences` | 比例/分辨率/质量/自定义宽高，以及按仓库恢复和保存的 Hook；任意尺寸能力由图像模型配置声明 |
| `agentRecovery` | Agent SSE 断开后的后台状态与快照补偿轮询；完成后由 `useChatSession` 合并消息并刷新资产库 |
| `useResizableChatInput` | 输入框拖动、键盘调整与高度持久化 |
| `useChatMaintenance` | token 提醒、完整压缩与清缓存事务；拥有确认、快照提交和错误反馈 |
| `api/chatStreamProtocol` | 对话流事件 v1 判别联合与唯一解码入口；未知版本/事件立即失败 |
| `chatSessionEvents` | 消息归并纯 reducer；消费已解码事件并更新文本、媒体、审批与路由选择。文本与 `media-slot` 都是有序 `parts`，后续 delta 追加在槽后；完成按 slotId 原位替换 pending 槽或已有图片/视频，失败只删除 pending 槽并合并相邻正文 |
| `textTools` | 文本清理、拼接、字符间插入、统计、UTF-8 转义/反解和 OpenCC 简繁转换的浏览器端纯函数 | 新文本工具算法进这里并补 Vitest；`views/tools` 只持有表单状态和展示，输入文本不得上传后端 |
| `quickTextTools` | 快捷工具浮标到 `textTools` 的纯适配层：统一默认选项、分隔符解码与紧凑统计结果 | `QuickToolsWidget` 只持有本地 UI 状态；不得复制六项文本算法。快捷工具与后台活动通过 `laf-floating-panel-open` 互斥展开 |
| `AppBody` | 根壳下面的页面分派器及所有页面级 `React.lazy` 边界；首页未选择仓库/作品时展示最近 3–5 个作品，选中父仓库时展示其子作品，选中作品才进入对话 | `App.tsx` 只拥有导航、仓库选择、根状态和弹窗；父仓库仅有一个子作品时自动选择；新增页面映射进 `AppBody`。聊天、设置、资产、工作流、节点和工具不得重新静态导入根壳 |
| `repoPresentation` | 仓库展示纯逻辑：最近作品排序、父仓库最后使用时间汇总与本地化显示 | `lastUsedAt` 由真实打开/选择作品更新；卡片作品数来自仓库树，资产数来自 generation 索引，禁止扫描磁盘猜测 |

模式合同：显示名称“编辑模式”和 `code → edit` wire 映射归 `viewRouting`；`ChatView` 只传 `workMode`，`useChatSession` 必须把它同时送入实时请求和后台队列。

前端加载合同：根入口控制在约 300 kB 未压缩以内；`textTools/opencc-js` 属按需大字典，只允许在打开完整文本工具或快捷面板后加载。快捷工具进一步拆成常驻 `QuickToolsWidget` 外壳和首次点击才加载的 `QuickToolsPanel`。

Demiurge 的产品目标是 PC 本地工作台，不承诺移动端可用性。界面变更至少验证 1280×720、1920×1080、2560×1440，以及 Windows 125%/150% 缩放等效视口；聊天区与用户详情区保持主要宽度，顶部常用动作继续一键可达，不能为压缩视觉把它们藏入“更多”菜单。

api 序列化器（**加带模型配置的端点时必用，别手拆三元组**）：
- `chatBody(chat)` → `base_url/api_key/model`
- `ragEmbed(embed)` → `base_url/api_key/embed_model/embed_model_dir/reranker_model_dir`（RAG POST）
- `sseEmbed(embed)` → `embed_base_url/embed_api_key/embed_model/embed_model_dir/reranker_model_dir`（SSE）

跨端字段合同：

- `shared/schemas/agent-invocation.schema.json` 是 Agent 调用 wire 的唯一字段真源。
- `scripts/generate_wire_contracts.py` 生成 `frontend/src/generated/wireContracts.ts` 与 `backend/app/generated/wire_contracts.py`；生成物必须提交，终端 Runtime 不运行生成器。
- 修改 schema 后先生成，再运行 `python scripts/generate_wire_contracts.py --check`；CI、前端编码器和后端请求模型覆盖测试共同阻止字段漂移。

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

节点索引包含两个 collection：`node_index` 保存完整插件包，供管理页查看和人工编辑；`node_index_chunks_v1` 保存按顶层 category、最多 12 节点的能力分块，只用于召回。只有当前全部包都有分块且没有卸载插件残留时，`node_store` 才原子切换到分块检索。远程嵌入 Adapter 对文档按单条单批最多 2000 字限制请求，避免 CPU Ollama 上的大节点包超过上下文或 120 秒超时；原始文档仍完整存入 Chroma。同步按包隔离错误，单包失败进入 `failed/failures` 后继续后续包，不得整批终止。

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

## 对话附件契约（File Attachment · 2026-09-01 定案 A1+B1+C1增强+D1）

用户拖放/粘贴/选择文件到输入框 → **附件卡片**（非文本化塞入输入框）。参照市场（Claude.ai 服务端文本提取、Codex CLI 非图片文件=路径引用/stdin 文本、Claude Code 按需读盘）与本地优先定位，定案：

- **A1 三栏并存**：图片 → 既有图片栏（vision 多模态）；视频/音频 → 媒体栏（dataURI/URL 预览）；其余任意类型（md/txt/code/office/pdf/zip…）→ 通用文件栏，按 MIME 给图标 + 文件名卡片。
- **B1 会话级存储**：文件落 `userdata/attachments/<thread_id>/<uuid>-<name>`（运行时 `DATA_DIR/attachments/`），file_id 即 uuid 前缀，删除会话/快照清理时连带删。
- **C1 增强（agent 消化）**：`MultiAgentRequest.attachments` 随消息透传；后端 agent 链路把附件转「文件参考」段落进上下文——文本类读全文（100k 封顶），docx/xlsx 零依赖 zip+xml 提取，PDF 可选 pypdf 提取（失败降级），纯二进制只元信息（文件名+mime+size+ext）。**图片不重灌图像 token**（沿用插画红线），音视频不进 prompt。
- **D1 历史回放**：消息 `parts` 持久化 `{type:"file", fileId, name, mime, size}`，file_id 是真源；`GET /api/attachments/{file_id}` 流式下载供回放只读卡片。base64 不落消息（防快照膨胀）。

### 单一属主与真源
- **文件字节属主**：`attachment_store`（后端服务）。前端只拿 file_id 元信息，不持有原始字节。
- **wire 透传属主**：`shared/schemas/agent-invocation.schema.json` 增 `attachments` → 跑 `scripts/generate_wire_contracts.py` 生成双端契约；`MultiAgentRequest`、`RunContext`、`AgentInvocation`、`agentInvocationBody` 同步补字段（漏一处 = 附件在链路静默丢失）。
- **前端 UI 属主**：`RichInput` 内附件栏；提交时 `RichContent.attachments` 进 `createAgentInvocation`。
- **历史回放属主**：`chatSessionEvents` reducer 支持 `file` part + `ChatMessages` 渲染只读卡片。
- **能力环境参数属主**：`capability_registry.ENV_INJECTED_OPS`（哪些能力的哪些参数由运行环境决定）+ `ENV_PARAM_SOURCES`（参数→环境值映射）+ `inject_env_params`（唯一写入入口）；执行层兜底 `plan_tasks._env_params_for_handler`。新增落盘能力只改注册表一处，三处归一与执行层自动继承。
- **卡目录归属属主**：`character_store.WORK_MARKER_FILE`（`_work.json`，值 = repo_id）——落卡能力写入、消费方（embed/export）只读；与 `repo_meta` 的 `_repo.json`（仓库文件夹标记）语义不同，**不得复用文件名**。

### 边界与红线
- 禁止把附件全文当用户文本塞进输入框（旧 `buildFileAttachmentText` 伪文本化路径废弃；`isTextFile` 白名单只保留给「+」选择器的 accept 提示，不再做静默丢弃判定）。
- 二进制/大文件不整体进 prompt（容量红线）；文本类封顶 100k 字，超限截断并注明。
- 附件与消息快照同生命周期：删除会话/快照清理必须同步删 `attachments/<thread_id>/`（B1 连带）。
- PDF/docx/xlsx 提取是**可选能力**：库缺失或解析失败 → 降级为元信息段落，不阻断对话，不新增硬依赖。

## 加功能决策树

- **要调 ComfyUI？** → HTTP 原语进 `comfyui_client`；模板/画布提交事务进 `workflow_submission`；路由只映射领域异常。
- **要改智能体分派/上下文/审批？** → 分派与图装配进 `agent_graph`（留驻 supervisor 路由/roleplay/plan 编译节点）；域接缝按六模块落点：检索→`agent_retrieval`，上下文装配→`agent_context_assembly`，自由循环→`agent_fabric`，能动性维护→`agent_agency`，流式→`agent_stream`，跨簇微助手→`agent_support`（均单向向下，见属主条目表）；上下文窗口进 `agent_context`，审批与已批准执行进 `generation_approval`。
- **要调对话/生图模型？** → 复用 `llm` / `image_gen`；前端用 `chatBody` 拼请求。
- **要碰普通知识库？** → 领域操作进 `rag_store`，纯排序进 `rag_retrieval`，嵌入/Chroma 进 `rag_backend`。
- **要碰节点索引？** → 单路存储与召回进 `node_store`，同步/多查询融合进 `node_index`，安装判断只读 `object_info`。
- **要改 AI 搭工作流？** → 回合上下文进 `workflow_build_turn`，图规则进 `workflow_graph_rules`，模式差异与模型重试才进 `workflow_builder`。
- **要动生成流程/持久化？** → 后端进 `generation_store`；前端生命周期顺序进 `workflowGenerationRuntime`，媒体投影与界面反馈进 `useChatSession`。
- **要加页面/子帧交互？** → 组件收 props 渲染；协议走 `lafLock`；纯算法进 `lib/` 并导出。
- **要加一个会被计划编排的落盘能力？** → 在 `capability_registry` 注册，并把它的环境参数加进 `ENV_INJECTED_OPS`（别在三处归一里各写一份）；handler 用 `base`/`repo_id` 时假定由执行环境注入、不信模型传值；卡目录类操作走 `character_store` 的归属标记，禁止扫全库。
- **写了带分支的纯函数？** → 放服务/lib 顶层并导出，配一个测试（见下）。

## 测试

```bash
# 后端（用项目 venv，系统 python 缺依赖）
cd backend && ./.venv/Scripts/python.exe -m pytest -q
# 前端
cd frontend && npm test
cd frontend && npm run check:wire && npm run test:e2e && npm run build

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
cd backend && ./.venv/Scripts/python.exe -m pytest -q tests/test_runtime_entry.py tests/test_runtime_release.py tests/test_portable_release.py tests/test_unix_portable_release.py

# RAG 固定真实评估（报告写入被忽略的 data）
cd backend
./.venv/Scripts/python.exe scripts/evaluate_rag.py --output data/rag_evaluation_report.json
```

测试放：后端 `backend/tests/test_*.py`，前端与源码同目录 `*.test.ts`。
第一批已覆盖：注入/清洗/展平/base_url 归一/标签提取（后端），calcSize/buildHash/fmtOpResults/lafLock（前端），以及 `EmbedConfig`、parser↔convert 契约。**加纯逻辑就顺手补一条测试**——接缝已经就位，写测试几乎零成本。

**判绿口径（2026-09-15）**：① pytest **只认 `--junitxml` 的 `tests` / `failures` / `errors`**，别看 exit 码——本机 safe-delete 守卫会让全绿的测试以 exit=1 收尾（跑前置 `CODEBUDDY_SAFE_DELETE_ENABLED=0`）；② 前端用 `vitest run --reporter=junit` 同理；③ `tsc` / `npm run build` 是「只改类型或签名形态」这类改动的**必过关**（vitest 走 esbuild 会剥掉类型检查，定向全绿也可能 `tsc` 报错）。当前实测数字见上节「当前基线」。

## 主题资产生成

**决策已反转（2026-07-29，见 `docs/memory/theme-removal.md`）**：早先的「最小样式 + 自动主题资产管线」已放弃，恢复起源项目整套样式与 `frontend/public` 素材。当前树**没有** `scripts/theme_asset_pipeline.py`、`scripts/theme_assets/<theme>.json` 或 `process_<theme>_theme_assets.py`——ARCHITECTURE 旧描述已过时，不得再按旧文新增生成管线。

现行主题体系：
- `frontend/src/styles.css`（当前约 8400 行）是主题 Runtime 接口与主题变量的单一真源，`[data-theme="bright|night|eye-care|green|gray|liquid-glass"]` 块提供视觉差异。
- 主题登记单一属主：`frontend/src/stores/settings.ts` 的 `ManualTheme` / `MANUAL_THEMES` / `normalizeTheme` / `resolveTheme`；设置页卡片清单 `views/settings/GeneralPanel.tsx` 的 `THEMES` 必须与之同步。
- `frontend/public/` 下的 `backgrounds/controls/ornaments/support/textures` 五目录为**素材型主题**（bright/night/eye-care/green/gray）的素材，随源码与 Runtime 发布；缺素材从起源项目 `d:/tool/ComfyUI/ComfyUI-Wrapping-paper` 取。
- **独立主题 `liquid-glass`（2026-10-23；同日二轮升级为控制台主题）不使用任何素材**：配置真源是 `Settings.liquidGlass`（独立于全站 `chatBg*`），`App.tsx` 组 `--lg-*` 变量并写 `data-lg-sidebar` / `data-lg-settings` 门控；`LiquidGlassBackdrop` 承载图片/视频壁纸（滤镜、翻转、倍速、暂停条件、自动轮换），`LiquidGlassDock`（lazy）提供右侧拉绳与玻璃/壁纸/效果/拉绳四页控制台。全部规则限定在 `html[data-theme="liquid-glass"]` 作用域，不改既有主题的公共规则。壁纸库 `LiquidGlassWallpaperLibrary` 按 `liquidGlass.wallpaperDir` 浏览文件夹（`POST /api/user-state/wallpapers/scan` 分页 + `hidden`/`hidden_only`；`GET /api/user-state/wallpapers/thumb` 出缩略图，缓存 `backend/data/wallpaper_thumbs`），对用户壁纸目录**只读**：隐藏只记 `wallpaperHidden` 路径清单，不删除、不写入源目录。控制台「自定义壁纸」另走**专属可写存储** `liquidGlass.wallpaperStoreDir`（空＝`backend/data/wallpapers`，支持 `~`/环境变量展开、必须绝对路径）：`GET /api/user-state/wallpapers/store` 读状态/计数，`POST …/wallpapers/upload` 仅收 JPG/JPEG/PNG/MP4 且重名去重，`POST …/wallpapers/store/migrate` 只搬直接媒体文件并返回 `mapping` 供前端改写 `wallpapers[].path`（禁止父/子目录互迁）；与只读壁纸库是两条独立通道。壁纸库另内置 **Wallpaper Engine 创意工坊**来源（`POST /api/user-state/wallpapers/workshop`）：后端从 Steam 注册表（`HKCU\Software\Valve\Steam\SteamPath`、`HKLM\SOFTWARE\WOW6432Node\Valve\Steam\InstallPath`）、`steamapps/libraryfolders.vdf` 与环境变量发现库，扫描 `<library>/steamapps/workshop/content/431960/*/project.json`；视频/图片作品直接应用，scene 作品自 2026-10-23 第三刀起**在页面内实时渲染**（`scene_pkg=true` 的条目由 `frontend/src/lib/scene/` 在 Web Worker 里解包、用同源 WebGL 播放器实时绘制；`project.json` 的 `file` 指向包内 `scene.json`，磁盘上没有可读的 scene 媒体，唯一数据入口是 `GET /api/user-state/wallpapers/workshop/scene-pkg?workshop_id=<数字id>` 交出的 `scene.pkg` 原始字节，后端不解析包格式）；web/application 无法在浏览器渲染，回落到作品自带 preview 并标 `preview_only`（卡片标「静态预览」）；未配置 `wallpaperDir` 时前端默认展示创意工坊。两路扫描均支持 `query` 名称搜索（本地按相对路径/文件名、工坊按标题/路径/工坊 id，`casefold` 不区分大小写，先过滤后分页并回显 `query`）；工坊 item 另返回 `preview` 与真实 `preview_width/preview_height`，只有预览图且短边 <480 的条目由前端标「低清」并说明浏览器无法渲染 `scene.pkg`，不伪装成高清效果。视频卡封面优先使用作品自带 preview（GIF 可动），视频只在悬停时挂载；`LiquidGlassBackdrop` 对媒体加载失败做 ≤2 次退避重试，并在窗口聚焦/页面恢复可见时再恢复。
- 发布门禁：`scripts/release_preflight.py` 的 `missing_css_assets` 校验 `styles.css` 引用的主题资产存在且未被 Git 忽略；前端 `npm run build` 负责无 unresolved-asset warning。
- 新增素材型主题：在 `styles.css` 接入共享 Interface（把 `[data-theme="<theme>"]` 加进 `:is(...)` 结构选择器列表），并补 `frontend/public` 对应素材；不需要生成脚本。新增无素材主题（如 liquid-glass）：两处登记 + 单独作用域样式，不改共享 Interface 与既有主题。

每个主题只在 `scripts/theme_assets/<theme>.json` 声明源文件、处理参数和前端目标槽位。`process_<theme>_theme_assets.py` 只能作为兼容命令入口选择清单，不得新增图像处理函数。新增主题优先复用已有 `op/preprocess/transform`；确需新变换时只扩展 pipeline 并给所有清单复用。

## 发布架构

源码上传与终端用户 Runtime 是两条独立发布链。`scripts/release_preflight.py` 是源码上传边界的单一检查入口：它检查 Git 已追踪文件和未忽略的未追踪文件，阻断密钥、私钥、运行数据库、模型权重、超大文件及误纳入的用户目录，同时验证 `styles.css` 引用的主题资产存在且未被忽略。脚本只读，不暂存、不提交、不推送。

源码必须包含 `backend/app`、`frontend/src`、`desktop/` 桌面壳源码、测试、依赖清单、`frontend/public` 主题资产和 `comfyui-ext` 扩展源码。源码禁止包含 `backend/data`、`userdata`、未清洗的 `presets`、`docs/memory`、会话、角色卡、世界书、生成图片、RAG 索引、模型/LoRA 权重、日志、环境目录和构建产物（含 `desktop/bin`、`desktop/obj`、`desktop/publish`）。唯一允许的预设目录是由 `build_resource_pack.py` 生成的 `presets/Demiurge-presets-regex`。详细清单见 `docs/RELEASE.md`。

归档只能基于用户确认后的提交执行 `git archive HEAD`，禁止直接压缩工作目录。上传前必须逐项审计 `git status`；当前工作区存在大量历史改动时，禁止用未经审计的 `git add -A`。

`scripts/runtime_release.py` 是固定 Runtime 的单一构建 Implementation，`release/runtime-targets.json` 是平台、架构、Python 与 Torch 的唯一矩阵。终端用户仅发布 Full RAG，包含 Python、基础后端依赖、已构建前端、Chroma/BM25，以及平台对应的 Torch、Transformers 与 SentenceTransformers 依赖层；不包含 Embedding/Reranker 权重、Hugging Face 缓存、ComfyUI、密钥、用户数据或 RAG 索引。

Runtime 采用 Base/Application/RAG 分层清单，每层逐文件归档并记录 SHA256，超过 GitHub 单资产限制时按 1.9 GB 分片。`runtime_entry.py` 只负责组装层路径、设置可写数据目录、执行模块自检并在 8010 同源服务已构建前端；PyInstaller 明确排除的 Torch/Transformers/SentenceTransformers/Scipy/Sklearn 由 RAG 层拥有，专用 MetaPath Finder 必须先于冻结 Finder 从外置层解析其全部子模块，禁止冻结包与外置包混合导入。ComfyUI 必须使用独立 Python，禁止回退应用 Runtime。Windows `portable_release.py` 组装 `start-dev.bat`/`stop-dev.bat`/`install-shortcut.bat`、分层 Runtime、MinGit **与包根桌面壳 `Demiurge.exe`**（`--desktop-dir` 为必填：壳产物缺失或与包内既有文件冲突即构建失败，禁止产出「只有浏览器入口」的 Windows 用户包；`.pdb` 不进包）；启动脚本用 PID 文件绑定当前包的 Runtime，停止脚本必须核验进程路径，禁止仅按端口误杀。macOS/Linux `unix_portable_release.py` 生成可执行启动脚本（暂无原生壳）。终端用户包必须做到解压即用，不运行 pip、npm，也不依赖系统 Python/Node（桌面壳依赖 .NET 8 Desktop Runtime 与 WebView2 Runtime）。

RAG 构建使用 `backend/requirements-reranker.txt` 声明直接能力、`release/requirements-rag.lock` 固定跨平台传递依赖、`release/runtime-targets.json` 固定各平台 Torch。三者共同进入 RAG definition ID；禁止只写 `>=` 后让新构建静默漂移。CUDA 驱动和 ComfyUI Python 不属于 Demiurge 依赖层，构建可以复用已校验层或 pip wheel 缓存，但发布包必须自带隔离文件。Windows 便携启动可用 `DEMIURGE_PORT` 覆盖默认 8010；Runtime 接收对应 `LAF_RUNTIME_PORT`，默认用户行为不变。

`.github/workflows/runtime-release.yml` 在版本标签上执行双端门禁、三目标 Full RAG 矩阵构建、真实冻结 Runtime 自检和 Release 上传；`portable-release.yml` 只消费已校验分层资产组装 `00-USER-DOWNLOAD` 包。任何新平台、Torch 或 Python 版本只改目标矩阵；新增运行依赖先确认 Base 或 RAG 层属主，禁止把依赖同时写进工作流和多个脚本。

### 桌面壳（2026-10-23 已落地：.NET 8 WPF + WebView2）

桌面化只允许加一层**薄壳**：壳负责窗口、单实例、托盘、窗口位置与 Runtime 生命周期；业务能力、鉴权、数据与壁纸渲染仍全部走同源 HTTP，不得新增壳专用业务 IPC。实现位于 `desktop/Demiurge.Desktop`（WPF + WebView2，`net8.0-windows`，框架依赖发布），复用现有 Runtime 与端口合同，不复制业务。

**端口与复用**：唯一真源＝`DEMIURGE_PORT` → `LAF_RUNTIME_PORT` → 壳持久化值 → `8010`。前端 `API_BASE` 默认钉在 `http://127.0.0.1:8010/api`，因此壳**禁止静默换端口**；改端口必须同时以匹配的 `VITE_API_BASE` 重建前端。端口上已有服务时，只有 `/api/health` 返回 `{"status":"ok"}` 且 `/` 返回含 `id="root"` 的前端文档才复用（复用进程永不回收）；只有 API 的开发后端明确报错（提示设 `LAF_FRONTEND_DIST` 或关闭它），端口被非 Demiurge 程序占用时同样报错。

**启动与回收**：便携模式从包根 `data/runtime/current.json` 取 `base_id`，启动 `data/runtime/base/<base_id>/Demiurge-Runtime.exe`（工作目录＝`data/runtime`），注入 `LAF_RUNTIME_ROOT`/`LAF_RUNTIME_STATE`/`LAF_DATA_DIR`（=`data/userdata`）/`LAF_NO_BROWSER=1`/`LAF_RUNTIME_PORT`，并写 `data/runtime/runtime.pid`；仓库模式用 `backend/.venv/Scripts/python.exe -m uvicorn app.main:app`，要求 `frontend/dist` 存在（`DEMIURGE_FRONTEND_DIST` 可覆盖），并设 `LAF_FRONTEND_DIST`。就绪判定＝`/api/health`，默认 180 秒（`DEMIURGE_READY_TIMEOUT_SECONDS` 5–900）。退出按 PID 两段式 `taskkill /T` → `/T /F` → `Kill(entireProcessTree:true)`，子进程另绑 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job 对象兜底；只回收自己启动的进程树，禁止按端口杀进程。

**单实例与壳状态**：`Local\Demiurge.Desktop.SingleInstance` 互斥 + `Local\Demiurge.Desktop.Activate` 事件；第二实例唤醒第一实例后立即退出。托盘菜单＝打开主界面/在浏览器中打开/退出；关窗即退出并回收 Runtime（不保留后台驻留）。窗口位置/大小/最大化与端口存 `%LOCALAPPDATA%\Demiurge\desktop.json`（启动时回夹到虚拟屏幕），日志 `logs\desktop.log`、`logs\runtime.log`、`logs\self-test.log`，WebView2 数据 `webview2\`；`DEMIURGE_SHELL_HOME` 整体覆盖该目录。WebView2 仅允许 loopback 导航，外链与 `NewWindow` 交系统浏览器；`DEMIURGE_WEBVIEW2_ARGS` 可注入调试参数（如 CDP 端口）。浏览器直连必须继续可用，壳不是运行前提。

**打包与分发（2026-10-23 第二刀）**：`runtime-release.yml` 的 `desktop-shell` job（windows-2025）做 `dotnet publish` → 跑 `--self-test` → 归档成固定名产物 `Demiurge-desktop-windows-x64.zip`；`portable_release.py` 以 `--desktop-dir` 把该产物平铺到包根（与 `data/` 同级），壳靠「向上找 `data/runtime/current.json`」识别便携包，所以放在包根或任意子目录都能启动。壳为单文件发布但**原生库不自解包**（`IncludeNativeLibrariesForSelfExtract=false`，`WebView2Loader.dll` 平铺在包根），避免 `%TEMP%` 不可写的账户起不来。包根另附 `install-shortcut.bat` / `scripts/install-shortcut.ps1`，生成指向包内 exe 的桌面与开始菜单快捷方式（`-DestinationDir` 可重定向用于测试）。浏览器入口 `start-dev.bat` 继续保留。

**验证与边界（2026-10-23 首刀）**：构建 0 警告 0 错误；`--self-test` 15/15；壳拉起开发 Runtime 约 7 秒就绪、页面同源 API 正常；第二实例 344 ms 退出；关窗 4 秒内回收 venv 重定向器 + 实际解释器两层并释放端口；外部复用的 Runtime 在壳退出后存活；仅 API 的 8010 被拒绝且原进程存活。第二刀（装配）实测：真实 `dotnet publish` + 真实 7z 组装出的用户包解压后，包根 `Demiurge.exe --self-test` 仍 15/15，且复用外部健康服务时 WebView2 正常渲染、壳退出后服务存活，快捷方式 `.lnk` 的 Target/WorkingDirectory/Icon 指向包内 exe。`.github` 两段工作流改动只能在下一次 tag 发布时真机验证。**未做**：安装器/自动更新/代码签名（本机无 NSIS/Inno Setup/signtool，签名需证书）、macOS/Linux 壳。scene 壁纸的**页面内实时渲染已在第三刀完成**（见「架构深化进度」49 与下段），当时记录的「没有页面内渲染器、没有任何 scene 同源路由」已作废：现在的路由是 `GET /api/user-state/wallpapers/workshop/scene-pkg`（只交包字节，不是上游插件的 `/wallpaper-engine/*` 前缀），解码与渲染全在前端，壳依旧不越权解码/渲染。细节见 `docs/tech-manual/G-contracts-release/16-contracts-and-gates.md` 与 `docs/memory/topic-release-audit-test.md` 同日「桌面壳」两节。

### Embedding Backend Seam

`rag_backend` 将嵌入来源拆为两个 Adapter：`_RemoteEmbeddings` 和 `_LocalEmbeddings`。`EmbedConfig.mode` 是唯一选择；缓存键只包含当前 Adapter 真正使用的字段，`reranker_dir` 不会分裂 Chroma。`model_probe` 复用 `rag_backend.local_model_files_status`，因此设置页的文件完整性判断与实际加载共享同一个 Interface。不得再让“填写了目录就自动切换”的隐式优先级回到上层路由或前端。

### Structured Output、Replay 与资产检索 Seam

`structured_output` 是结构化生成 Runtime：领域 Module 持有 Pydantic Schema，Runtime 只拥有原生 JSON Schema Adapter、单次旧文本回退 Adapter、统一校验错误和 `structured.output` Trace。Supervisor 的真实调用已经穿过 `invoke` Interface；模型 wire 尚无显式能力字段时不得按模型名或 URL 猜能力，只有调用方明确提供 `structured_chat_fn` 或模型对象才启用原生约束。原生成功不得再调用 legacy，原生能力失败也只允许一次 legacy 请求，禁止重试乘法和 Token 双花。

`trace_replay` 只离线重验录制响应和事件不变量，不调用模型，不写会话快照、SQLite、资产或 ComfyUI。全链重放必须等 RecordedModel 与内存 Store Adapter 完成，禁止用生产存储直接回放。

generation 资产检索与剧情 `retrieve` 物理隔离：展示 prompt 永远取 `metadata.prompt`，索引正文可组合 `prompt + tags + description`；VLM 描述只更新资产索引，不进入聊天历史。`visual_asset_index` 使用独立 `asset_visual_<repo>` collection 保存 Qwen3-VL-Embedding 图片向量，查询时与文本 Dense/BM25 通过 RRF 融合；模型未安装或未建视觉索引时必须无损回退文本搜索。

`visual_preference` 只保存作品内显式二选一、拒绝原因与 Elo 排序；它可以重排资产搜索结果，但不得删除图片、跨作品迁移偏好或自动改 LoRA。VLM 评分只能作为推荐 Adapter，用户最终选择仍是唯一反馈真源。

### Continuity、Belief 与 Narrative CI Seam

`continuity_compiler` 是剧情上下文预算的单一属主：角色实时状态和有效时序事实是权威约束，角色认知是角色私有上下文，Chronicle/RAG 只是证据候选。调用方不得把三者平铺成同等真相，也不得把完整图片或资产描述塞回聊天历史。

`temporal_fact_store` 管世界客观事实；`character_belief` 管指定角色在指定回合的 `knows/believes/suspects/misbelieves/conceals/unknown`。两者都只允许显式 `supersedes_id` 关闭旧记录；角色状态字段仍由 `character_state` 单一持有。Roleplay 只召回本轮选中角色的认知，禁止把甲角色所知泄漏给乙角色。

`narrative_ci` 在正文生成后做非阻断语义诊断，返回事实、时间、地点、关系和认知越权的证据；不得自动改写或净化正文。诊断状态只允许由用户/API 标记为 `fixed/foreshadow/retcon/accepted`，其中接受为伏笔或设定变更不等于删除证据。`trace_replay` 可把 Narrative CI 终态纳入离线回归，但不得借 Replay 写生产状态。

**文风诊断同流**（2026-08-29）：`prose_style`（确定性 lint，词表单一属主一份两用）与 `style_review`（采样制 LLM 活人感通审，走维护通道）产出 `style_*` 诊断码并入 `narrative_ci.evaluate`→`save` 同一诊断流，复用同一处置生命周期。红线原样延伸：文风检测与通审**永不阻断或改写正文**；`prose_style`（正文文风域）与 `prompt_clean`（提示词域）互不越界。

### Model Lease、Scenario 与 Procedure Seam

`model_lease` 是本进程 GPU 能力租约 Runtime。ComfyUI 提交优先级最高，可要求释放 VLM、Reranker 与本地 Embedding Adapter；各 Adapter 自己拥有真实卸载函数，Runtime 不 import 模型实现。租约解释的是本进程已知占用与排队原因，不等于跨进程精确显存计量。

`scenario_lab` 的完整快照同时包含作品文件、SQLite 数据库和仓库专属 Chroma collections。SQLite 使用 backup API 并显式关闭连接；不可变媒体优先硬链接；v2 manifest 记录版本、回合、文件 SHA-256、源 size/mtime 和向量集合。新快照先在同目录 staging 完成后原子发布；未变化非数据库文件复用上个 manifest 的哈希，避免每回合重新读取全部图片。恢复只允许空目标仓库，绝不覆盖正式时间线；文件或 Chroma 恢复失败必须清理本次写入，多候选实验后续分支失败必须回滚前面已完成候选。前端每个完成回合按 `turn:N` 去重创建快照；`scenarioBranchRuntime` 统一拥有保存消息、匹配/创建快照、fork、登记本地分支和导航顺序；从历史消息分支只能使用同回合快照，找不到时必须拒绝。反事实候选之间物理隔离，不自动合并，用户选择只记录正式候选 ID。

`capability_sandbox` 提供短期、可撤销、精确到 operation/path/domain/tool 的能力租约，并分两档访问标准：`approval`（默认，按 capability 逐条授权）与 `full`（通配放行，租约记录 `mode="full"` + `approved_by`，审计可查；仍受撤销/过期约束）。`plan_tasks` 在 full 模式提交任务时自动签 full 租约，approval 模式保持逐项审批。`procedure_skills` 从脱敏 Trace 提议流程，必须经用户审核、dry-run、受支持 Adapter 检查和能力授权后才执行。Prompt Skill 与 Procedure Skill 是两个 Module；外部 Smithery Skill/MCP 默认关闭并标记 `external_unreviewed`。当前 Procedure 执行面刻意只开放已注册安全 Adapter，未知动作不得退回任意 shell/MCP 执行。

### Supply Chain 与 Instruction Provenance

当前只完成小型硬化，不宣称完整 Artifact Trust：模型下载记录清洗后来源、格式风险、SHA-256，任务临时文件按 task id 隔离且拒绝覆盖已有目标；节点更新记录当前 commit、remote 与 requirements 哈希。Smithery 技能默认关闭并标记 `external_unreviewed`；角色卡与技能提示词经 `instruction_provenance` 标记为低权限外部内容，不能扩大工具、文件、联网或安装权限。长期 artifact/plugin manifest、expected hash 强制比对、requirements URL/VCS 策略与隔离安装仍是后续边界。

### Temporal Fact Ledger

`temporal_fact_store` 只拥有任意世界/实体事实的有效区间、证据哈希、来源、冲突和显式 `supersedes_id`。关闭旧事实必须由调用方提供同一 subject/predicate 的替代 ID，禁止模型凭空编造或无引用覆盖。P3②（2026-09-06 用户裁决）：抽取链为唯一「指认」通道——抽取模型可引用【现存事实】清单里的 id 前缀（resolve_supersedes 唯一解析，短/歧义/不存在一律降级普通 ADD），旧事实收口但时间线保留；无 DELETE（实体消亡写成新事实）；重复主张同 id 自动重新生效（re-affirm），与在替事实并存的矛盾由 conflicts 诊断兜底。 注入面按新近度优先（`as_of(recency_first=True)`，M2）：预算裁剪保留块内前几行，最新事实在压力下存活。好感度、态度、心情、所在及角色身体/衣着等仍由 `character_state` 单一持有，账本拒绝写入这些 predicate；纪要 rowid 会压缩/删除，账本身份只绑定稳定 repo、turn、内容与 evidence hash。M1 记忆封口（2026-09-06 用户定案）：删除消息/重新生成按回合级联封口——被删回合起仍有效事实 `seal_from_turn` 收口 `valid_to_turn`（timeline 保留可回放，注入侧等效清除）；回合号由前端持久化在 assistant 楼层（`story_turn` wire 字段），删除/重生成不倒退。

### 2026-08-10 运行态影响审计

> 这是历史审计快照；2026-08-11 的当前结构、门禁和评分见下节与 `docs/memory/architecture-performance-2026-08-11.md`。

- **Structured Output：部分生效。** Supervisor、纪要、手动填表已迁移到 Pydantic 合同；工作流搭建/端口编排复用统一 JSON 解析。真实 Trace 已记录 `legacy_text` 成功与坏 JSON 校验失败，证明统一错误面生效。当前模型 wire 仍未声明结构化能力，生产请求没有启用 `native_json_schema`；除 Supervisor 外的迁移点也尚未统一写 `structured.output` Trace。因此它现在主要减少解析分叉和静默格式漂移，尚未减少模型重试或保证原生约束解码。
- **Trace Replay：纵向 MVP 生效。** `/api/ai/trace/replay` 可对已录响应和 turn/route/illustration 事件不变量做无副作用复验。对仓库 `3d5de442-ba11-48d5-b254-0b6f27d6261b` 的真实 2 个回合复验得到 1 通过、1 失败，失败项准确指出缺少 `turn.completed`。它不是整轮重执行，也不比较文本质量；RecordedModel、内存 Store 和版本化脱敏 fixture 完成前禁止称为全链 Replay。
- **资产语义检索：文本链已生效，视觉链尚未产生数据收益。** 当前仓库 10 张 generation 资产可通过专用 Hybrid 接口返回语义结果；剧情 RAG 与聊天历史均未被污染。现有资产的 `description` 为 0，部分历史 prompt 已有编码乱码，故当前排序主要依赖英文 tags 和未损坏文本。Qwen3-VL-Embedding-2B 的固定 revision、4,255,140,312 字节 safetensors 与 SHA-256 已核验一致，但独立视觉 collection 尚未建索引；实测 GPU 仅余约 1.3 GiB，不能在不影响 ComfyUI 的情况下加载 4 GB 权重。自动描述与“构建视觉索引”已接入资产页，必须在用户实际执行并得到非空索引后再宣称图像向量检索生效。
- **供应链硬化：预防性生效，不是完整信任系统。** 新下载会隔离 `.part`、拒绝覆盖并计算 SHA-256；节点 requirements 拒绝 URL/VCS/editable/自定义索引来源；Smithery 技能默认关闭；真实多卡 persona 与技能片段均带低权限来源标记。下载身份仍只保存在任务状态，节点 provenance 仍是当前任务进度，低权限标记属于提示层约束而非进程沙箱；没有长期 per-artifact/per-plugin manifest 和 expected-hash 强制比对。
- **Temporal Fact Ledger：已进入只读剧情上下文。** Chronicle 可旁路写入世界事实，Roleplay 现在按当前回合召回有效事实并经 `continuity_compiler` 在 900-token 预算内注入；角色活状态仍由 `character_state` 单一持有。没有积累事实的旧仓库仍不会凭空获得收益，RAG/纪要也不会被提升为权威事实。

### 2026-08-11 架构与性能验收

本次只做保持现有行为的深化，不机械拆 `agent_graph`，不改变剧情、NSFW、提示词 Profile、LoRA、ComfyUI 或资产检索协议。当前架构综合 **9.1/10**：边界与依赖 9.3、状态事务 9.3、结构化与 Trace 9.0、性能 9.0、测试与回归 9.6、前端职责 9.0、文档与领域语言 9.0。评分依据是可执行合同与失败路径测试，不以文件数量或界面是否可见代替。

验收基线（**2026-08-11 当时快照，数字已过期，勿当当前基线引用**；当前口径见本文「测试」节与 `docs/tech-manual/G-contracts-release/17-regression-playbook.md`）：Ruff 通过；17/17 import-linter 合同保持；mypy 39 个契约文件通过；硬编码与 wire 门禁通过；后端 1212 项、前端 338 项、Playwright 3 项全通过；生产构建通过。
**当前基线（2026-09-15 实测复核，以本行为准）**：ruff `All checks passed!`；`lint-imports` = `Contracts: 17 kept, 0 broken`；mypy `Success: no issues found in 48 source files`（`backend/mypy_files.txt`）；后端 pytest **2686 passed / 0 failed**（191 个 `tests/test_*.py`；判绿只认 `--junitxml` 的 `tests/failures/errors`）；前端 vitest **95 文件 / 855 例**；`check:wire` 与 `check:manifest` 通过；`tsc` 与 `vite build` 通过。OpenCC 字典仍形成约 516 KiB gzip 的按需 chunk，但不进入首屏，只有打开快捷/完整文本工具才加载，因此记录为可观察的非阻断性能边界，不为消除 warning 改变繁简转换语义。

### Theme Runtime Seam

`styles.css` 的 production theme runtime Interface 统一拥有按钮九宫格尺寸、输入区和拖动手柄几何、模态内容层级、装饰指针隔离、进度节点定位、头像尺寸和着陆页几何。bright/night/eye-care/green/gray 主题块只提供视觉差异和素材 URL；新增素材型主题先接入共享 Interface（把 `[data-theme="<theme>"]` 加进这些 `:is(...)` 结构选择器列表），再补 `frontend/public` 对应素材，不得复制整段结构选择器。`liquid-glass` 是**无素材独立主题**：不接入素材 Interface、不写公共规则，玻璃/侧栏/内容面/壁纸全部由 `--lg-*` 变量驱动并收在 `html[data-theme="liquid-glass"]` 作用域；`.app-backdrop` 在其他主题下必须 `display:none`（设置页预览与运行态共用 `LiquidGlassBackdrop`）。当前**没有** `themeRuntime.test.ts`（该测试随旧管线移除）；Seam 的回归由 `scripts/release_preflight.py` 的 `missing_css_assets` + 前端 `npm run build`（无 unresolved-asset warning）共同承担。

## 剧情能动性引擎（联动链上半截 · 已落地，全绿）

> 这一节既是**设计合同**又是**已落地接缝**。六模块（`character_state`/`agency`/`scene_illustration`/`scene_renderers`/renderer 接口/`roleplay_agency` 子图编排）全部落地并全绿，已接进 `roleplay_node`。加功能按此接缝建，别另起炉灶。

### 要解决的本质问题

酒馆是**用户主导**：角色只在被触发时被动响应。Demiurge 要**角色有能动性**——用户没提，爱慕他的角色会自发行动（如舞会下药、用户短期失去主导权），且行为不写死在卡里也能合理涌现。核心洞察：**角色卡的 `死穴`/`攻略路径`/`个体机制` 不是描述，是行为生成器**；系统每拍主动求值「以当前状态+场景，这角色会不会自己动手、怎么动」，机制既是自主性的燃料，也是防 OOC 的护栏。

### 三条设计支柱（互相咬合成一个闭环）

1. **动态条目 = core/state 分离**。`core`（人设根基/外观/死穴/机制/弧线）写在卡文件、**永不自动更**，是视觉锚与一致性来源；`state`（好感度数值 + 态度/心情/所在等叙事字段）按 `repo_id` 作用域、随剧情更。每次更新是**带证据的 StateDelta**（`from→to`+证据+turn+source），AI 召回读到的是「态度:戒备(因第3章救援)」= 角色发展而非矛盾。**初次登场初值（2026-09-22 用户口径）**：数值字段除 `add` 增量外，允许 `set` 只在**字段尚不存在**时一次性播种初始值（好感度/恶感按世界观与剧情因果推导，不得默认从 0 起算；间接影响——未见面但因所属势力/听闻事迹/共同关系而先有好恶——同按因果定初值）；字段已存在时 `set` 整条忽略，防模型一键重置累积值（`character_state.apply_deltas` 单一门）。首次播种 `from=None`，回滚等价删除该字段。
2. **能动性三方 = `roleplay_node` 内部子图**。世界 Agent 提案（LLM）→ 裁判仲裁（**纯规则 0 LLM**）→ 主控叙述（LLM）→ 状态更新。World 默认每个剧情回合做一次语义判断（`gateBaseRate=1`，设为 `0` 才明确关闭）；首轮没有好感记录按中性 `0` 评估，敌对角色也不因负好感失去自主性。输入只含在场 NPC 命中的世界书 core、动态状态、近历史与本轮 user，不重复整本世界书。提案必须保留「持续目标 + 本轮具体行动」；持续目标机械写入 `叙事/角色名·当前目标` 供下轮续用，裁判成功则落实，已实际尝试但失败也必须在正文呈现为未遂，不能静默消失。
   **世界事件司门（2026-10-21）**：World 提案分三类——`npc`（在场 NPC 自主行动，旧语义）、`event`（事件/新角色登门）、`user_action`（用户单方面宣告结果的裁定），三类共用同一套 d100 六档（裁判仍纯规则、0 LLM）。`event` 的 `who/source` 必须命中当前仓库世界书快照的来源清单（`角色卡·X` 的角色名，`E<n>` 或含「大事件」的条目），由 `consult_world` 机械校验，命中不了即驳回（防凭空造人）；老仓库无世界书快照时事件提案降级关闭。`judge.eventsEnabled=0` 可关闭事件提案。directive 改为三段固定顺序（世界事件 → NPC 自主行动 → 用户行动裁定），且落在**最后一条** system（用户安排块之后），确保裁定不被末位用户安排覆盖。2026-09-23「用户安排最高优先级」随之部分修订：用户输入只定事实/界限/点名的时点/意图，成败与代价交 D100 与裁判，不再单方面成立。
3. **剧情插画 = state 的第二用途**。ComfyUI 自动插画开启时，每个真实 Roleplay 回复都必须在一次主生成中完成可见剧情和本轮最强视觉高潮的隐藏画面计划＋完整 `profile_prompt`；安静对话以人物关系、目光、动作或关键物件变化作为高潮，不得省略或退化成无关静态肖像。`<content>` 的预设/用户篇幅独立计算，`think/status/table/illustration/profile_prompt` 均不计入正文；配置了 Roleplay `maxTokens` 时，隐藏 Profile 预算追加在正文上限之外。`image_prompt_extract` 先严格解析 JSON；仅在失败时转义 JSON 字符串内部的裸换行/回车/制表符后重试，结构错误仍拒绝，防止一个未转义换行把完整 subjects、动作与 visual_facts 全部降级为空。触发、角色补漏、画幅降级和 `motion` 都是纯规则；主 Roleplay 漏计划或成稿拒答/格式失败时，写回接缝仍以正文最强视觉段发出请求并从 `scene_spec` 本地编译当前 Profile，禁止整条请求静默消失或前端再补调文本模型。主计划误选低强度结尾钩子时，后端重定向到正文更强的高潮段；只有计划中仍存在逐字证据落在纠正后窗口的 `visual_facts` 时，才允许保留 Agent 成稿并重新经过格式/字段验收，否则废弃错误动作、镜头、构图与同源成稿后从纠正段重建。
4. **场景分类 = 一次判断驱动两件事**。`scene_classify` 把本轮归到 `dialogue/action/emotion/conflict/nsfw/climax`，**复用 supervisor 那次路由 LLM 调用**产出（零额外往返），写进 `ctx["scene"]`：既选**条件思维链**（P1，`preset_store.select_chains` 按真状态 scene/affinity/turn 命中链），又决定分级与高潮降级策略。若 Supervisor 误判且主 Roleplay 漏掉 `<illustration>`，写回接缝会对本轮用户输入和还原后的正文再做一次保守纯规则判断；明确 `nsfw/climax` 才生成保守英文 tags，普通剧情只从可见正文选择最强视觉段并交给当前 Profile，禁止误添成人 tags，也禁止整条 ComfyUI 请求静默消失。

**Roleplay 输出预算门禁**：可见正文额度优先读取当前 ST 预设 `openai_max_tokens`；在其外固定追加 4000 token 的分析/状态/骰点预算，自动插画再追加当前 Profile 的 800–1000 token，禁止依赖 Provider 默认输出上限。模型已经打开但未闭合 `<content>` 时属于上游截断，必须在状态、纪要、Curator 与插画写回前失败关闭，不能把残缺正文当作成功回合；`model.request` Trace 必须记录最终 `max_tokens`。

### 模块（单一属主）— ✅=已落地 / ⏳=规划

| 模块 | 状态 | 拥有什么 | 边界 |
|------|------|---------|------|
| `character_state` | ✅ | 动态状态单一属主：按 `repo_id` 存可变状态（`数值`+`叙事`字段，每字段带 provenance 证据+turn+source）、`parse_deltas`/`apply_deltas`（带 from→to 审计历史，封顶 200；数值 `add` 累加 / `set` 仅对不存在字段播种初值，已存在忽略）、`render_state_block`（紧凑 kv+内联证据）。**手改/回滚(缺口6)**：`current_turn`/`set_fields`(设精确值非累加，数值 clamp，标 `source=user`)/`rollback_last`(还原到审计 `from` + 弹历史)。落盘 `<base>/<repo_id>/state.json` 物理隔离 | core 字段永不被本模块写；纯逻辑无 I/O 可单测，load/save 是唯一 I/O；base 由调用方注入不读 config；`state.py` 路由(GET/PATCH/rollback)只做 HTTP 适配，前端 StatePanel 消费 |
| `agency` | ✅ | 能动性**纯逻辑（0 I/O 0 LLM 全单测）**：`judge`(core依据→好感度门槛→掷骰) + `classify_roll(roll,chance)` **六档**(大成功/极难/困难/普通成功/失败/大失败，对齐正文 `<roll>` 语汇，骰≥96 恒大失败) + `should_consult_world`(廉价门控) + `tier_index`/`crossed_tier`(插画跨档)。裁判是确定性规则不是模型 | 吃好感度**快照**，**不 import `character_state`/`agent_graph`**；裁判不得做成 LLM 调用。正文 `<roll>` 由剧情推进 Agent 的 `rollInstruction` 提示词驱动(主模型打)，与本模块隐形裁判(管 NPC 自主行动)同一套六档语汇；2026-10-21 起 `judge` 还裁定 `kind=event/user_action`（事件需 who/source 且不吃好感门槛，用户行动同理） |
| `scene_illustration` | ✅ | 剧情插画**纯逻辑（0 I/O 0 LLM 全单测）**：`decide_trigger`(优先级 显式>失控>**场景nsfw\|climax**>跨档>每N段，跨档复用 `agency.crossed_tier`) + `build_scene_request`(从「段落动作+core外观+state衣着/场景」拼 prompt，非空过滤，出图管线的裸拼接降级档) + `fallback_illustration_anchor`（只在 `<content>` 正文评分，忽略 think/控制块，物体放置、具体体位与高潮动作优先；回忆性总结降权）+ `resolve_illustration_anchor`（模型锚定静态收束、比喻尾句或低强度钩子时纠正到正文最强可见动作；明确结果句与前文形成动作因果链时保留模型锚点）+ `illustration_scene_excerpt`（从当前场景边界连续保留姿态建立、关键物件、反复动作、身体反应到结果句的完整因果窗口，遇真正离场/转场截断）+ `illustration_anchor_offset`（高潮锚点逐字/破甲还原优先，轻微改写按段落相似度映射；指定锚点完全无效则失败关闭，禁止回退消息末尾）+ renderer 注册表(纯 dict) | 触发是规则不是导演 Agent；吃标量快照，**不 import `character_state`/`agent_graph`/`workflow_submission`/`image_gen`**（scene-illustration-purity 合同强制）；本模块不拥有出图管线 |
| `scene_classify` | ✅ | 场景分类**纯逻辑（0 I/O 0 LLM）**：`normalize_scene`(模型给的场景字段→合法标签，中文别名+子串匹配+兜底空串)。标签 `dialogue/action/emotion/conflict/nsfw/climax`。信号复用 supervisor 路由那次 LLM（零额外往返），此处只解析规整 | **不 import `llm`/`agent_graph`**（scene-classify-purity 合同强制）；只规整不调模型 |
| `image_prompt_extract` | ✅ | 自动插画的纯逻辑接缝：主 Roleplay 在正文后附隐藏 `<illustration>`，同时保存 anchor、镜头、艺术决策、subjects、画幅、动作草稿与当前 Profile 完整成稿。模块在 JSON 解析前允许调用方把块内容经过与正文相同的 `AI_OUTPUT` 正则，以还原预设改变的结构；随后剥块并校验 `anchor/camera/composition/subjects.weight/prompt/profile_prompt/motion/aspect_ratio`。模型若在同一个 JSON 字符串中插入完整 `<think>...</think>` 后继续输出，解析前只剥离该续写控制段并恢复前后文本；未闭合、无法恢复或仍为坏 JSON 才整体剥离并返回空计划。anchor 必须取所描绘高潮段末句；禁止无证据选余韵/对话尾句，但显式计划若位于 `---` 等时间跳转后的末尾揭示场景，必须保留该新场景，不能用前一时段分数更高的高潮覆盖。高潮上下文保留当前场景连续动作窗口，允许少量环境/反应桥段但遇离场/转场立即截断，防止稀疏抽样丢失复合动作或带入旧角色；视觉命题必须保留造成状态变化的动作链，静态肖像只能作次级信息。`visible_narrative_text` 只读取 `<content>`，排除思考、状态、表格和插画控制块，故隐藏成稿不显示、不入正文长度。比例只允许 `1:1/2:3/3:2/3:4/4:3/9:16/16:9`；主计划缺失时由 `scene_illustration.infer_aspect_ratio` 纯规则选择 | **不 import `llm`/`agent_graph`/`roleplay_agency`/`character_state`**；Profile 格式归 `image_prompt_profiles`，本模块只持有隐藏块协议；LoRA 触发词只按本次最终生效 LoRA 的完整文件名精确读取，空触发词不注入 |
| renderer 接口 | ✅ | 渲染器接口 `Renderer=Callable[[SceneRequest],str]` 在 `scene_illustration` 定义（register/get/available，纯 dict） | 新增图像格式=注册一个 renderer，不改 `scene_illustration` 触发逻辑 |
| `scene_renderers` | ✅ | renderer concrete 适配器（有 I/O，8 测试）：`cloud_renderer`(云→按 `req.actors` 命中 `CloudConfig.character_base_images` 取底图,有则 `image_gen.generate_with_images` 图生图锁角色一致性,否则 `generate` 纯文生图；未命中回退 `style_base_image`) + `comfy_renderer`(本地→`submit_template` 提交 + `fetch_result` 轮询取图 → 拼 `/view` 直链，sleep/now 注入可测)。工厂绑定运行期 config 产出 Renderer 闭包 | import `scene_illustration`+既有出图管线；`scene_illustration` 不反向 import（纯度合同保证无环）；只返回图片地址，不回灌图像 token |
| `narrative_memory` | ✅ | 纪要记忆**纯逻辑（0 I/O 0 LLM 全单测）**：默认每 3 个完整会话中的 assistant 回合新建一条独立 `overview/chronicle/dialogue/characters/keywords` 丰富纪要；召回把 FTS 命中与最近条目合并，优先当前出场人物，主 Roleplay 最多只注入 10 条短概览；旧分层压缩逻辑不得进入自动流程或删除频率索引 | **不 import `narrative_store`/`character_state`/`agent_graph`/`roleplay_agency`**（narrative-memory-purity 合同）；详细纪要与对白不回灌主上下文 |
| `narrative_store` | ✅ | 纪要落盘+召回（SQLite FTS5 trigram，按 repo_id 物理隔离 `<base>/<repo_id>/chronicle.db`）：丰富字段往返并自动迁移旧 schema；自动流程 append-only，历史进度落后超过一个频率区间时停止生成跨区间大卡并交给手动补表；用户编辑与手动重填可更新/删除指定 rowid 或仅删除相交消息范围；保留 `get/set_last_turn` 和 FTS 重建 | import `narrative_memory` 取类型+查询构造；base 由调用方注入不读 config（同 character_state）；局部覆盖禁止清空整库 |
| `memory_rollback` | ✅ | M1 记忆级联封口编排（2026-09-06 用户定案）：删除消息/重新生成时按回合封口派生记忆——纪要封口回合旁表（召回/最近/计数排除重叠条目，数据保留可审计）+ 抽取进度回退到未封口前沿、时序事实收口有效区间、角色状态按审计回滚；`/ai/memory/seal` 薄路由。**合同：会话展示内容（消息流/画布）就是完整的记忆与上下文，删除消息等效记忆清除** | 封口只由用户删除/重生成触发，纯存储操作零 LLM；表格/RAG/世界书暂无回合归属保持手动清理（用户定案）；封口语义不物理删除，保可回放 |
| `chat_facts` | ✅ | M3 正常对话长期事实记忆（2026-09-06 用户定案）：非剧情线程（answer 路由）复用 P3② 指认式原语自动维护——每 CADENCE=4 回合搭一次 LLM 抽取（对齐 curator 成本杠杆），落 `<DATA_DIR>/chat_facts/<thread_id>/temporal_facts.db`（与作品事实库物理隔离）；answer_node 注入当前有效事实块（recency-first，缺失不阻断） | 抽取失败/NSFW 拦截 → 静默跳过（最坏=不收敛，绝不产生错误记忆）；无 DELETE；roleplay 线程走自己的纪要/curator 链不走本模块 |
| `manual_table_fill` | ✅ | 基于 `chat_snapshot` 文本历史的手动补表工作流：统计每表频率/未记录/上次回合，按所选表、最近 N 层和批次调用填表 Agent；范围重叠先返回确认，覆盖只替换相交消息范围，不覆盖则逐表跳过已处理消息 | 图片/媒体槽经 `chat_snapshot.to_prompt_history` 排除；确认前不得调用模型或改数据；进度按 repo_id 落 `table_progress.json` |
| `roleplay_agency` | ✅ | 能动性子图编排。World 每轮从在场 NPC core 推导「长期目标→阶段目标→本轮动作」，提案 intent/goal 穿过裁判进入主叙事；多角色按各自 `角色名·好感度` 仲裁。**Recall 是零 LLM 检索接缝**，候选与 GrayWill、世界书、历史和本轮 user 合成一次主 Roleplay。Chronicle 保留周期抽取；Curator 默认 `gate=1` 写 RAG，并受控增改当前小仓库世界书快照，`gate=0` 可显式关闭；`consult_world`（2026-10-21）另解析 `kind` 并做事件来源白名单校验，清单来自当前仓库世界书快照（无快照则事件提案降级关闭） | import 接缝模块+llm，**不 import agent_graph**；Recall 不得恢复成独立生成；World/Curator 使用当前对话模型的独立代理选择 |

> 当前默认调用链：**有卡纯文本零 LLM 直达 → World 对在场 NPC 做目标/行动提案并由规则裁判 → 小仓库世界书快照/RAG/Chronicle 候选机械组装（当前人物相关的最近 10 条概览）→ GrayWill + 状态/只读表格 + 可见历史 + 本轮 user + 已裁定 NPC 行动一次主 Roleplay（自动插画开启时同轮生成独立达标的 `<content>` 与隐藏高潮计划/完整 Profile）→ 隐藏块按正文正则还原、剥离、校验，失败则本地事实编译 → 快速状态写回 → 立即发正文与锚点插画并由 ComfyUI 独立排队 → 表格维护 + Chronicle（每 3 轮，丰富纪要）+ Curator（默认开）完成 → Agent 终态并释放下一轮**。维护响应只写数据库/Trace，不进入对话；主计划缺失时仍从可见正文纯规则恢复请求。

> **代理合同**：保留一个全局代理地址。每个对话/生图/视频/远程嵌入模型有 `proxyMode=on|off|inherit`，旧配置与新模型默认 `on`；非本地端点中，`on` 使用全局地址，`off` 直连，`inherit` 跟随全局开关。localhost/127.0.0.1/::1 回环端点始终直连，前端不得透传代理，后端 Adapter 必须再次忽略显式代理。前端解析为 `chat_proxy_url/gen_proxy_url/video_proxy_url/embed_proxy_url`，后端按模型类型隔离；`proxy_url` 仍只表示联网搜索代理。
>
> **全局地址的来源与启用判定（2026-09-10 升级）**：全局代理地址**首选本机系统代理**（`settings.systemProxyUrl`，由 `GET /api/user-state/proxy-status` 回填；后端单一属主 `services/system_proxy.py`：WinINET 注册表 → 环境变量 → 手填兜底），手填地址降为兜底（`settings.proxyUrl`）。「全局开关」`settings.proxyEnabled` **由探测结果自动写回**，不再是用户手拨的开关（原手动开关已移除）。前端**两个取用口必须分清**（`lib/modelProxy.ts`）：`globalProxyAddress(s)` 只挑地址（`systemProxyUrl || proxyUrl`），配 `proxyEnabled` 交给 `resolveModelProxy`，供有模型级 `proxyMode` 的链路；`effectiveGlobalProxyUrl(s)` 含启用判定（未启用返回空串=直连），供无模型级模式的链路（发送、灵感、素材/模型下载、画布）。**红线**：模型级 `proxyMode="on"` 是用户显式强制，**不受探测结果约束**（`proxyEnabled=false` 仍生效），勿与 `inherit` 混为一条路径。**探测同源**：可用性判据必须与真实用途同源（`web_search.probe()`），不得用无关静态端点代替——「端口在听」「某站可达」都不等于「能搜到」。

### 依赖方向（已由 importlinter 合同强制：能动性纯逻辑 / 插画纯逻辑 / 纪要纯逻辑 / 场景分类纯逻辑 / 出图提取纯逻辑 / 能动性子图编排方向 / 编排不得依赖 agent_graph）

```text
supervisor_node（路由，那次 LLM 顺带产出 scene → 写 ctx["scene"]）
  → scene_classify（场景字段规整，纯逻辑 0 I/O 0 LLM）
agent_graph.roleplay_node（编排，升级为能动性子图）
  → preset_store.select_chains（按真状态 scene/affinity/turn 选思维链，纯函数）
  → character_state（状态读写 + delta 应用/回滚）
  → agency（门控 + 裁判，纯规则 0 LLM，纯函数）
  → worldbook（既有，卡内嵌世界书检索）
  → narrative_store（纪要 FTS5 落盘+召回，只增不改）
      → narrative_memory（抽取/压缩/召回查询，纯逻辑 0 I/O 0 LLM）
  → 主 Roleplay 同次输出 `<illustration>`（高潮锚点+镜头+构图+主体权重+画幅比例+prompt+motion）
      → image_prompt_extract（剥块/校验/组装，纯逻辑 0 I/O 0 LLM；英文 tags → 质量行+内容行，坏格式不提交）
  → 两条出图路径二选一（按 ctx.comfy_illustrate 分流）：
    ① 同步 renderer（云端默认）：scene_illustration 触发判定 → renderer 插件（gpt-image…）→ image_gen，出图后随 image_recs 回传
    ② 异步事件（前端已预设 ComfyUI 模板）：_build_renderer 返回 None 不同步付费，优先按主生成给出的原文锚点发 illustrate_request（稳定 slotId）
        → SSE 顺序：高潮段及前文 delta → media-slot → 后续正文 delta
        → 前端 useChatSession.submitIllustration：直接消费后端同轮校验或本地兜底后的 `scene_spec.profile_prompt`；`/ai/prompt/profile` 只保留手动/兼容入口，不是自动插画正常链路。随后按用户预设 Latent 最长边与画幅比例换算宽高，按模板 exposed 的隐藏 binding 组 values，提交 key 仍使用原字段名 → /comfyui/submit → workflowGenerationRuntime 后台守望
        → ComfyUI 返回 prompt_id 后异步回报最终 prompt/Profile/LoRA/权重/Latent/注入键到 /image-agent/illustration-submission，后端记 `illustration.submitted` Trace（turn_id 关联本轮）；回报失败与 Trace 写失败均不影响生图，禁止携带密钥或图片内容
        → 完成后以 messageId+slotId 原位替换；目标消息已删除则丢弃结果，不新增消息
        → 复用 laf_pending_gen_* + SupportWidget 徽记（进度/离开继续/点击返回）；motion>=2 且预设视频模板+smartVideo → 改出视频
```

> **异步出图闭环（多元数据插入）**：主 Roleplay 在一次响应中先完成独立满足篇幅的 `<content>`，再在隐藏 `<illustration>` 写视觉高潮的原文锚点、镜头、构图、主体权重、画幅、艺术决策与当前 Profile 完整成稿。隐藏块不显示、不进入正文长度；显式 Roleplay `maxTokens` 会追加 800–1000 token 的 Profile 预算。隐藏 JSON 在解析前复用正文 `AI_OUTPUT` 正则，成稿随后走 `IMAGE_PROMPT` 清洗与四 Profile 格式/视觉事实门禁；`I won't generate/create/...` 等整段拒答、漏块、坏 JSON 或错误格式均从同轮 `scene_spec` 确定性降级，禁止提交拒答或触发前端第二次文本调用。四 Profile 最终均为纯英文；Anima 最终严格两行，负面词只写独立 `negative_prompt`。Profile 完成后才查询实际 LoRA 元数据并机械注入精确触发词/作者质量词去重。世界书模式人物只来自角色 LoRA 绑定名；高潮画面正文中精确出现的人物是最终角色真源，只有正文未写姓名时才依次回退合法 subjects、本轮输入与状态快照 `[在场]`。视觉外貌资料只能描述已选角色，不得反向激活人物；单人高潮只保留一份外貌与角色 LoRA，双人高潮按画面出现顺序保留两份并串联角色 LoRA。高潮锚点重定向只能改变动作事实，不能覆盖仍与正文一致的合法 subjects；前端还须用经过后端过滤的 `scene_spec.subjects` 恢复事件层意外丢失的精确角色名。single 模式无角色命中时只允许使用已配置风格兜底，multi 模式必须存在默认风格；两者缺失均失败关闭。模板提交收到空 LoRA 栈时必须清零所有标准 LoRA 节点，禁止执行模板保存时的残留模型。计划缺失时画幅按人数、特写与横纵动作纯规则选择；多人物 encounter 固定 `4:3`。采样参数保留工作流原值；刷新恢复时没有 `prompt_id` 的预提交槽必须移除并记录 `resume_unsubmitted`。

多元数据插入顶部的“角色外貌来源”是互斥合同：`条目模式(worldbook)` 只读取当前小仓库世界书中本轮命中的 `角色卡·` 视觉条目，并允许按角色配置 LoRA/底图与兜底图片；`角色卡模式(character_card)` 只读取当前小仓库绑定角色卡的 `description`，按本轮出现卡名筛选，未点名时回退开场卡，同时禁止消费角色 LoRA、角色底图和兜底图片，旧预设残留也必须忽略，仅保留可选全局风格 LoRA。新请求不得在两种来源间隐式降级；缺字段的旧调用保留旧兼容回退。LoRA 选择器以 `/loras/available` 的磁盘扫描为真源；请求加载、瞬时失败或后端重启期间必须继续显示并保留作品已保存的绑定，自动重试后仍失败才展示错误与手动重试，禁止把“列表暂不可用”伪装成“无角色 LoRA”。

自动插画 LoRA 采用三态合同，旧预设缺字段时迁移为 `single`：`none` 只消费角色/风格底图，提交前把工作流内标准 LoRA 加载器权重归零；`single` 按高潮画面人物顺序串联全部已配置角色 LoRA，命中至少一个角色时严禁注入风格 LoRA 及其触发词，全部未命中才使用风格兜底；`multi` 固定先加载默认风格 LoRA，再按高潮人物顺序叠加每个角色 LoRA，完整文件名去重。冷倾雪＋虞妙玥的 multi 栈必须包含 Ogipote 默认风格、柳世熙与蔡秀晶，single 栈则只含柳世熙与蔡秀晶。角色配置全集只用于校验，禁止整体并入 `actors`；人物名单优先取最终高潮正文，正文未显式写名时再回退合法 subjects、本轮输入与状态 `[在场]`。外貌资料只按最终人物名单裁切，绝不能作为出场证据。主 Roleplay 的 `<illustration>.subjects` 必须列出高潮画面中每一名实际可见角色并使用配置中的精确名称；后端再以高潮正文校正并过滤 subjects。`<status>` 整体仍是不透明显示快照，但角色兜底必须确定性读取其中精确的 `[在场]` 单行；不能只读 `character_state.叙事[在场]`，因为真实预设通常只维护快照而不生成同名叙事 delta。相关回归必须走真实 `extract_status_snapshot → writeback → load_state`，禁止 mock `_narr` 伪造在场人物。`illustration.request` Trace 必须同时记录 `actor_candidates/status_actors/actors`，便于区分配置丢失、状态恢复失败和后续协议丢失。多人 LoRA 不新增 Agent 调用：`workflow_injector` 复用模板已标注的 `LoraLoaderModelOnly` 或 `LoraLoader` 为链首，动态复制后续加载器，串联 MODEL（完整加载器同时串联 CLIP），并把原下游统一重接到链尾。重生成快照必须保存 LoRA 栈和模式。Anima 使用风格 LoRA 时，内容行还必须机械加入手绘二次元媒介锁定词，避免只有触发词但基础模型仍落入真人域。

**纪要 vs 角色条目（两种数据，非二选一）**：`character_state` 记「结构化活状态」（好感度数值+态度/心情/所在），同一人物按身份替换字段值；`narrative_store`/`narrative_memory` 记「事件叙事」（概览+详细纪要+重要对白+出场人物），以完整会话快照计算回合，每到填表频率永久新建一条独立索引卡，自动流程只增不改且不压缩删除。展示编号固定为 `T<层级>-<rowid>`（如 `T1-1/T1-2`），回合区间只作副标题，编辑正文不得改变卡身份；按 trigram/人物相关性召回，主 Roleplay 只读最多 10 条概览。抽取失败跳过且旧纪要不动；用户显式手动重填时才允许按相交消息范围局部替换。FTS5 trigram 旁路召回（中文免分词）与世界书 Chroma 语义检索互补。

**通用多表（补 SillyTavern chatSheets 能力）**：`character_state`（好感度/角色状态）每轮更新，`narrative_store`（纪要）按独立频率新增；`table_store` 补其余七类默认表并落 `<output_dir>/<repo_id>/tables.json`。全局数据每轮完整替换唯一卡，保存上一轮结束后的时间、地点、世界状态与规则；主角信息按 `fillEvery` 更新唯一卡；重要角色按姓名一角色一卡，同名更新、新角色新增；技能/背包/任务按 `fillEvery` 增改，只有背包用尽或丢失允许删除，技能废除改为不可用，任务完成或失效仍保留；选项按 `fillEvery` 完整替换唯一卡，集中保存 AI 推导的用户后续动作。存量旧全局字段行、旧选项多行和无状态列技能表由 `load()` 兼容迁移。schema 也可由 TavernDB 模板或前端建表定义，**跳过好感度/纪要引擎表**避免重复。读侧与写侧必须分离：`tables_for_read` 每轮提供全部 full 表现值与 retrieval 表 schema，`fillEvery` 只控制 `tables_for_maintenance` 写回频率。retrieval 行使用独立候选池和字符配额，身份列精确命中优先；无嵌入配置时仍走确定性文本回退，不与普通知识 RAG 竞争 top-k。正文发出后由独立 `table_maintenance` 调用根据本轮 user、正文和表格规则生成纯 JSON 操作并写回，维护响应不得进入会话。失败或 JSON 截断只写 Trace 并保留旧表；`<表格更新>` 仅用于清洗旧模型残留，完整或未闭合块都丢弃，不再作为当前生成协议。自建表沿用用户定义的身份列与增删规则。`table_update` 由 importlinter 纯逻辑合同锁死；追踪记录表格注入、独立维护请求/响应和写回。数据表弹窗统一呈现通用表、角色状态卡和丰富纪要卡；纪要重建支持局部覆盖确认。

**角色状态唯一键**：`character_state` 在读取、自动写回和人工编辑时把 `角色名·字段`、旧的 `角色名身体状态` 及无归属 `身体状态` 规范为同一个 `角色名·身体状态` 键；无归属字段优先沿用同字段唯一已知 owner，不能盲目归到作品主卡。冲突按字段 `turn` 保留最新值。角色状态是当前值表，不是事件列表，同一角色同一字段必须覆盖，审计变化只追加到 `历史`。

**用户自建表（引导式，已落地）**：schema 两个来源——导入 TavernDB 模板（`import_template`）或前端「数据表」弹窗**引导式建表**（`create_table`/`drop_table`/`set_meta`）。对标 TavernDB 三张图（DDL/四段触发 SQL/发送模板）**全部翻译成无 SQL 的引导表单**：用户只填①表名 ②「这张表记什么」(note) ③「何时增/改/删」(rule，自然语言替代四段 SQL) ④逐列(列名+文本/数字类型+可选身份列)。列 meta：`colTypes`(列名→文本/数字)、`keyCol`(身份列，替代 SQL 的 `UNIQUE`——`apply_ops` 里 update/delete 优先按身份列值 `_locate` 定位同一条、回退行号；导入模板时 `_key_from_ddl` 从 DDL 的 `NOT NULL UNIQUE` 行尾中文注释反解身份列)。**note/rule/keyCol 经 `render_tables_block` 注入给 AI**——这是 AI 知道每表用途与增删改时机的唯一依据，自建表尤其依赖（老版只发列名+行，AI 不知表义，已修）。新表零改自动纳入 `table_instruction`（遍历 `load()` 动态生成）。

### 当前剧情稳定化基线（2026-08-05）

1. **输入与历史**：前端只上传当前可见文字历史；图片、媒体槽和自动插画资产不进入模型上下文。快照存在即为真源，空快照也不得回退 checkpoint。Claude 在 `llm.prepare_messages` 边界合并 system、严格交替并只去除明确的末轮重复。
2. **自主行动与主生成**：World 默认每个剧情回合判断在场 NPC 的持续目标和本轮动作，失败尝试也进入叙事；Recall 零 LLM。主 Roleplay 一次完成正文、状态增量和插画计划，只读表格上下文；控制块在任何成功或异常分支都必须剥离。
3. **知识维护**：Curator 只可更新本轮实际注入的小仓库世界书 index；角色长期动态进唯一动态区，基础外貌不被覆盖。世界书首次索引后台增量化，未完成时主生成使用关键词和内存 BM25。
4. **结构化数据**：角色状态按 `角色名·字段` 替换当前值；纪要默认每 3 个 assistant 回合 append 一张独立卡；全局、主角、重要角色、技能、背包、任务和选项分别遵守 `table_store` 的 singleton/keyed/deletePolicy 合同。
5. **插画计划**：只读可见 `<content>` 判断高潮和分级，隐藏/未闭合 think 不得触发成人降级。人物稳定外貌、当前变化和高潮事实先形成唯一视觉命题、主体层级、色材母题与光影因果，再转换为 Krea2、Anima、GPT Image/Banana 或 Niji。
6. **LoRA 融合**：按本次实际 LoRA 完整文件名读取触发词、建议权重和作者提示词；作者示例只提取质量/风格/镜头/光影/签名，排除人物、服装、动作、关系、场景和分级词。空触发词不注入，Civitai 的展示 `@` 不属于触发词。
7. **异步媒体**：Agent 从固定比例集合选画幅；计划缺失时由纯规则按人物数量、特写与横纵动作选择，不固定单一比例。用户只选 Latent 最长边，工作流保有采样参数。SSE 在锚点处建立稳定槽；本轮 Agent 完成后立即释放对话通道，已有 ComfyUI 任务继续后台运行，下一轮媒体请求提交到 ComfyUI 原生 FIFO。最终 output 按 `messageId + slotId` 原位回填，重新生图覆盖原图，失败只写 Trace。
8. **故障边界**：ComfyUI `execution_error`、提交失败和轮询失败都是终态，不能永久 pending 或污染对话。模型 `/models` 成功不代表推理健康；502 必须以最小推理和跨模型对照区分项目故障与供应商线路故障。

完整变更索引见 `docs/memory/stabilization-baseline-2026-08-05.md`。

禁止反向：`agency` 不知道存储；`scene_illustration` 不拥有出图；renderer 不拥有触发；角色卡直达只处理纯文本和强执行命令，带附件/模糊工具请求仍归 Supervisor。

### 插件化三插槽（复用既有 store，新增=注册项不改编排）

- **renderer**（图像格式）：ComfyUI workflow / gpt-image / 其它。ComfyUI 工作流就是一种 renderer = 你要的「节点管理」。
- **skills**（可下载 SKILLS 库）：`skills_store` 已在，提炼提示词/风格/角色行为片段做成可下载包。
- **MCP**（外部工具）：`mcp_store` / `tool_agent_node` 已在。

## 画布创作模式（Canvas Mode · 2026-08-20 落地记录）

> `generate` WorkMode 已由画布接管。画布是**内容区切换**（非独立模式），所有 WorkMode 均可画布/对话切换。画布节点实时投影 `generation_store`，布局/连线/视口持久化到每作品 `canvas.json`。

### 形态与接管

- `resolveHomeWorkspace("generate") → "canvas"`（`viewRouting.ts`）；`story`/`code` 保持 `chat`。
- 画布/对话是内容区切换：`ChatView` 内部 `contentView` 状态按作品记忆 `laf_view_<workId>`（默认 chat），功能栏只随 WorkMode 变。
- 画布输入复用对话 `RichInput`，提交走 `multiAgent()` → `POST /ai/multi-agent`（SSE，Supervisor 编排），**不是**已下线的 `POST /ai/image-agent/generate-image`。
- 模式合并：左上角模式下拉已移除，固定单一「创作」模式（`workMode` 恒 `story`）；`#/generate`、`#/code` 旧 hash 保留兼容。

### 节点类型（`CanvasNodeType`）

| 类型 | 说明 |
|------|------|
| `image-group` | 同 prompt 多次生成结果聚合（按 generation 索引聚合） |
| `video` | generation + 进度（视频槽原位回填，独立超时 450/630 tries） |
| `audio` | generation + 波形 |
| `input` | 新生成输入占位卡（`laf-canvas-input` 事件创建） |
| `workflow-tool` | `/w` 选模板生成的工作流工具卡；双击进 `WorkflowToolModal`（左=节点参数选择图+运转，右=AI 编排对话框） |
| `group` | ReactFlow v12 `parentId + extent=parent` 组容器（虚线框），子节点坐标转相对，持久化到 canvas.json |
| `inspiration-card` | 灵感卡（角色卡/世界书条目/预设/表格行四类），双击编辑、右键插入对话；自动从素材库导入（`importInspirationFromLibrary`），去重键 `kind+sourceRef` |

### 持久化与交互

- **canvas.json**：后端 `canvas_store.py` 原子写 `<output_dir>/<repo_id>/canvas.json`，只存布局/连线/视口/灵感卡；路由 `GET/POST /user-state/canvas-layout`。
- **@xyflow/react v12**：`CanvasStageFlow.tsx`——自定义 `CardNode` + `NodeResizer` + 连线手柄 + `MiniMap` + `Controls` + 框选多选 + 右键菜单 + 吸附辅助线。
- **右键菜单**：节点[编辑/删除/插入对话]；多节点[建组/批量删除]；空白[取消选择/建组/新建灵感卡]。点弹窗外自动关闭。
- **吸附辅助线**：`GuidesOverlay`（ReactFlow 内部子组件，合法 `useViewport`），拖动中心点对齐 ±10px snap + 滞回释放。
- **画布输入折叠**：`canvasInputFolded` 状态，折叠时右下角悬浮小球，展开时左下角收起按钮；折叠时 `RichInput` 保持挂载（`display:none`），输入内容不丢。
- **背景点阵**：ReactFlow `<Background variant=Dots>` 按画布坐标渲染（放大变疏/缩小变密），颜色用主题 `--dot` CSS 变量。

### 架构契约

- 画布不修改 generation 真源；节点是 `generation_store` 的只读投影 + 布局覆盖层。
- 组件管渲染、lib 管逻辑：`canvasRuntime.ts`（纯逻辑投影/排版/详情，可单测）、`canvasLayout.ts`（布局持久化）。
- `@xyflow/react` 的 `useViewport`/`useReactFlow` hook 只能在 `ReactFlowProvider`/`ReactFlow` 子组件内调用；外部视口需求用 `onMove` 回调或内部子组件。
- 闪烁治理：`setRfNodes` 用 updater 保留 `selected` 状态；位置差 <0.5px 返回原引用；edges `animated:false`；`CardNodeComponent` 包 `React.memo`；`fitView` 仅 mount 时一次性调用（`InitFitView` 子组件）。

## 多模态创作路线（剧情×图×视频×联网灵感 · 2026-08-19 方向指引 · 2026-08-20 进度同步）

> V1.1–V1.3（视频首帧底图注入、最小事实合同、视频槽全链路）与 M1.1–M1.2（搜索源 Adapter 注册表、图片素材搜索与预览）已落地；M1.3 受控下载基础链路部分落地（安全校验待补）。产品理想：剧情、图片、视频都是同一作品的一等创作数据，彼此按剧情事实联动；缺素材时联网检索并制成灵感卡回流创作。加功能沿此接缝走，别另起炉灶。逐步施工图（每步的改动位置、实现要点、验收与预排问题）见 `docs/ROADMAP-MULTIMODAL.md`。

### 现状真源（先读这个，防止重复建设）

- **剧情→图**：自动插画全链路已落地（能动性引擎支柱 3：高潮锚点 → 四 Profile → LoRA → ComfyUI/云端 → `messageId+slotId` 原位槽）。
- **剧情→视频**：V1.1–V1.3 已落地——smartVideo 切视频模板时已完成插画作为首帧底图注入（`resolveVideoBaseImage` 优先级：本槽已完成插画 > 最近一次已完成插画 > 用户手动指定 > 模板无图像口=文生视频；pending 不等待=时序红线）；视频 scene_spec 最小事实合同（motion/时长/镜头运动来自预设，零新增 LLM，禁止从模型输出读 camera）；视频槽独立超时（图片 150/210 tries，视频 450 tries/15min 释放 busy / 630/40min 上限）、SSE/快照/恢复/瘦身全链路验证（`durableFinalizeSucceeded` 合并 images+videos 校验）。云端 `video_gen` 仍仅文生视频无参考图（V1.4 等 Provider）。
- **联网灵感**：`web_search` 已收编为搜索源 Adapter 注册表（M1.1 ✅：`SearchAdapter` 协议 + DDG 默认实现，可插 Bing/SearXNG/商业 API）；**文字源现有两个（2026-09-10）**：`bing-cn`（cn.bing.com，国内**直连**可用）与 `ddg`（需代理）；未显式指定 provider 时按 `_AUTO_CHAIN=("bing-cn","ddg")` **依次回落**取首个有结果的源（= 不配代理也能搜），显式 provider 只试该源不回落；`inspiration.search_and_refine(..., search_provider=…)` 的 `search_provider` 随 `RunContext.search_provider` 从设置项透传（空=自动链）；`image_search`（M1.2 ✅：`BingImageSearchAdapter`，返回 `{thumb_url, full_url, source_url, title}`）；`inspiration.search_and_refine` 搜索摘要 → 对话模型整理成「标题+内容」中文总结 + 图片结果（失败降级 `images=[]`）；`/ai/image-proxy` 后端图片代理（`image_proxy.py`：限 http(s) + 5MB + image/* Content-Type）；`/comfyui/web-materials/*` 上网素材基础 CRUD（`image_store` 下载到 `_web_materials/`，M1.3 🔶 部分落地：基础链路就位，魔数/SSRF/域名策略/大小上限/原子落盘待补）；持久化由 `generation_store.persist_inspiration` 写会话快照（thread 级，M1.4 资产库化未开始）。
- **可复用底座已就位**：`visual_asset_index`（图向量+RRF）、`visual_preference`（二选一 Elo）、`model_lease`（GPU 租约）、`instruction_provenance`（外部内容低权限标记）、`model_downloader` 的受控下载事务（临时文件+校验+落盘）。

### 三阶段规划

**V1 视频联动深化（剧情→图→视频链路）**
- 图生视频走本地优先：已完成的插画作为视频模板首帧底图提交（角色一致性由底图锁定），复用 `workflow_submission` 提交事务与 `workflowGenerationRuntime` 后台守望；云端 `video_gen` 待 Provider 提供参考图能力再对齐，不自行猜接口。
- 视频 scene_spec 最小合同：复用主 Roleplay 同轮 `subjects/motion` 与 `scene_illustration` 纯规则，补镜头运动与时长字段；**不另调 LLM、不建四 Profile 级字段账本**。
- 视频仍走独立 media-slot 原位回填，SSE/快照/恢复/瘦身全部复用现有媒体槽合同，不加第二套视频通道。

**M1 灵感素材化（文字卡→素材卡）**
- `web_search` 收编为搜索源 Adapter 注册表（DDG 先行，接口可插 Bing/SearXNG/商业 API）；搜索逻辑只此一处，路由与 Agent 只适配，别各写抓取解析。
- 搜图链路：图片搜索 → 预览选择 → 受控下载入资产库（类型/大小/魔数校验 + 来源 provenance，复用 `model_downloader` 的临时文件+校验事务形态）→ 可设为角色底图（`CloudConfig.character_base_images`）、参考图或工作流底图。
- 灵感卡从会话快照升级为资产库可管理成员（generation RAG 合同：资产删除保留本地文件、禁止磁盘自动补录），保留「一键插对话」现有行为。

**M2 派生关系编排（多模态资产连续性）**
- 派生只存元数据：`derived_from`（本视频派生自哪张图/哪个 turn 的事实），禁止槽间强依赖图与级联自动重建。
- 资产库多模态化：按媒体类型（文/图/视频/灵感卡）与来源（生成/检索下载）筛选；视觉索引与偏好对视频封面帧生效。

### 排雷合同（红线，逐条对应真实教训）

1. **自动视频永不默认**：视频时延与成本是图片数量级倍数；保持「用户预设视频模板 + smartVideo + motion 门限」显式触发，禁止对齐自动插画的每回合节奏。
2. **视频事实合同保持最小**：只锁首帧底图、动作意图、时长；字段账本过严会在视频模型能力不足时大量重试失败（对照四 Profile 的事实账本是图片域奢侈品）。
3. **回合快照不复制视频**：视频文件留在资产库，`scenario_lab` 快照存引用（同盘硬链接/路径），防每回合快照与多分支复制的体积爆炸。
4. **搜索源单一属主**：搜索 Adapter 只在 `web_search` 接缝注册；禁止路由、Agent、前端各写一套抓取与解析。
5. **素材下载受控**：限 jpg/png/webp、限大小、魔数校验、可配域名策略、来源 provenance 标记；禁止任意 URL 直接落盘或进发布包。
6. **检索文本视为不可信输入**：搜索摘要/网页内容喂模型前截断并带来源标记（复用 `instruction_provenance` 低权限概念）；灵感卡不得自动改设置、自动下载或自动执行技能。
7. **密钥边界不变**：商业搜索/视频 API 密钥只进被忽略的 user_state.json，禁入源码、日志、Trace 与发布包。
8. **生成通道隔离维持**：本地视频走 ComfyUI FIFO 不占 Agent 通道；云端视频轮询在后台完成，禁止阻塞队列 worker 或前台对话。
9. **Token 边界维持**：图/视频只以 url+caption 进会话，绝不回灌图像 token 进历史；灵感卡在对话内一句话说明，禁止整卡标签刷屏（现有合同，对视频同样生效）。

## 明确不做的（别反复提议）

- 3 个空 router（runs/loras/assets）+ `list_ai` 桩：已挂载=在用端点，删了缩 API 面。（`characters` 已从空桩转为角色卡导入/列表/删除/导出真实端点。）
- 角色卡「一张卡=一个文件夹」的落盘格式（`character_store` 布局）是单一属主：卡本体、内嵌世界书、正则、原图、对话记录同处一个文件夹。Phase 2 世界书检索、Phase 3 正则/提示词都从这里读，别在别处另立卡的存储。
- 偏置预设注入已升级为**多消息通道**：`preset_store.assemble_messages` 按各片段自身 role 组装多条消息（`_llm.chat_messages` 收消息数组发模型），`chatHistory` marker 处原位插历史（还原 ST 深度注入语义）。`assemble_system` 单串档保留作降级。**思维链**（`thinking_chains`）经 `select_chains` 按真状态 scene/affinity/turn 选，尾部链作独立 system 落历史后·本轮 user 前（离生成点最近，遵守最严）——这比 ST 的字符串宏变量更准（真状态判断，非文本插值）。改注入结构 → 这里，别退回单 system 串。
- Claude 兼容中转的消息合同由 `llm.prepare_messages` 单独拥有：发送前把所有 system 按原顺序合并、连续 user/assistant 合并为严格交替，并仅对“倒数包装 user 已含末轮真实 user”的明确形态去重；非 Claude 消息结构不变。Roleplay Trace 记录该规范化后的实际发送结构。
- 当前小仓库世界书快照落在 `<output_dir>/<repo_id>/worldbook.json`：首次从卡快照/绑定独立书复制并合并，之后读取和 Curator 写回都只作用于该文件，不回写源卡或独立世界书，也不串到兄弟小仓库。画布世界书灵感卡双击打开时，entries 接口支持携带 `seed_from`（源书 base+name）就地播种快照（ensure_repo_snapshot 语义：已有快照永不覆盖），无需先跑一次剧情。主 Roleplay 的本轮实际注入 index 会进入 Trace，并成为 Curator 唯一可见、可更新集合；未注入 index 即使模型提交也由服务端拒绝并追踪。角色长期进展写入唯一动态区，基础设定不被覆盖；机制类默认只读；即时外观状态仍归状态表。`worldbook_add` 不受允许集合影响，禁止自动删除。
- 正则「后端跑存储/发送档 + 前端跑显示档」是两个 runtime 的同一逻辑（`regex_engine.py` / `lib/regexEngine.ts`）：显示层 markdownOnly 必须在前端渲染时跑（原生 JS 正则、不落库），改存储/发送在后端。别强行合并成一处。显示档来源按 GLOBAL→PRESET→CARD 合并：全局(`/regex/`)+当前激活预设(`/preset/regex`)+当前卡(`/characters/regex`)；编辑视图仍显示原始存储文本，不应应用 markdownOnly。
- 卡即作品=「大仓库(卡名) + 子仓库(对话记录)」两层（`repos.addCardWork` 返回 `{parentId,childId}`）：对话线挂**子仓库**(`repo.parentId` 有值)，资产库下钻双击子仓库进对话、能返回。别把卡建成顶层单仓库(会导致下钻进空子列表无法回对话)。
- 检查点/分支是**纯前端**：检查点=`localStorage laf_ckpt_<threadId>` 存到某条为止的消息切片、可回滚(不新建仓库)；分支=`repos.addBranch` 在同大仓库下建兄弟子仓库、拷贝消息切片进新线(localStorage+后端快照)后跳转。都不新增后端端点。
- 仓库三样绑定(卡/独立世界书/人设)是**前端存字段 `cardName`/`worldbookName`/`personaId` + 后端解析**：`resolveBinding` 自身优先缺则继承父仓库（子继承父，非合并），`App.activeWork` 把解析结果合并进 repo(id 不变)。绑定只保证「该给的资料都喂进上下文」，不改模型行为——别把"绑定后剧情稳"理解成模型质量保证。**世界书/人设注入只在 `roleplay_node`(需 `_has_card`)内跑**：故独立世界书/人设绑定要「同时绑了卡(自身或继承父)」才生效，单绑世界书不绑卡不进扮演流程——别在通用对话节点另接世界书注入。显式绑人设标 `persona_bound`，`_apply_work_persona` 见之不用作品快照 `persona.json` 覆盖（别去掉这个门，会让绑定失效）。
- ComfyUI 界面模式画布是**页级单例，靠 iframe `ready`→`post("load")` 一次性推工作流**：切模板必须让 iframe 重挂(key 含 `sourcePath`)才会重新 `ready` 载新图——换整张不同工作流用整帧重挂(语义=刷新重启)，别学起源项目单节点卡的"软重发 load"(那是换同图节点用的，见 [[../d--tool-ComfyUI-ComfyUI-Wrapping-paper/memory/orchestration-route-and-nodecard-race]])。画布空白另有跨标签共享 store 争用根因(看 `N:x [0]` + 关旁标签能好)，只提示不代码根治。
- ComfyUI 界面模式载图后**必须手动居中**（2026-09 实测）：ComfyUI v0.31.1 的 `app.graph.getBounding` 不存在、`c.fitViewToContent` 不存在；`node.pos/size` 是 `{0,1}` 对象不是数组。laf_lock 用 `graphBounds()` 手动遍历 `app.graph._nodes`（`n.pos[0]/n.pos[1]/n.size[0]/n.size[1]` 属性访问）算包围盒，`fitGraphToView()` 按 `fitNode` 同款公式设 `ds.scale/offset`。别依赖 `c.ds.fitToBounds`（收 `[x,y,w,h]`，且内部有 300×150 默认视口 hack）。
- ComfyUI 界面模式节点重复/丢失的根因（2026-09 修）：ComfyUI 初始化会异步 `loadPersistedWorkflow`（localStorage 的 `Comfy.Workflow.Draft.v2:*` / `workflow` / `workflow:<clientId>`）恢复旧工作流，其 `app.loadGraphData(旧图,true,true)` 与 laf_lock 的 `app.loadGraphData(新图,true,false)` 可能交叉——两个加载各自 `clear()` 再逐节点 `add`，交叉时 `graph._nodes` 出现同 id 两个节点对象（画布两个相同节点框），`getNodeById` 只映射一个（丢节点）。修法是 `installLoadGraphLock()` 串行化 `app.loadGraphData`（见 `workflowCapture` 合同），刷新后不再复现。前端侧：`WorkflowTemplates` 画布 effect 依赖 `picked` 会形成无限重载循环（load→node_title→setPicked→effect 重跑→ping_ready→ready→sendLoad），必须用 `pickedRef` + effect 依赖移除 `picked`。
- 数据表渲染：通用表/纪要/角色状态统一用**卡片式**（每行或每角色一卡、字段带标签两列网格、长文本占整行、`.card-grid` 多列流式）。角色状态必须按人物聚合，把该人物的好感度、状态、来源、依据放在同卡；长状态用 textarea 完整换行，禁止退回固定列宽 HTML 表格。
- AI 消息 HTML 渲染是**有意的、消毒后**：卡内正则把 `<status>`/`<roll>` 等标签换成带内联样式的 HTML 状态卡，`ChatMessages.looksLikeHtml` 检测块标签→`sanitizeHtml`(DOMPurify，留 style、禁 script/iframe/事件)→`dangerouslySetInnerHTML`。别改回纯文本(卡的状态栏设计就废了)，也别去掉消毒(XSS)。开场白 first_mes 的 HTML 同链路。
- 删仓库连带删卡文件夹：卡即作品下删大仓库=对 `target+子仓库` 的 cardName 集合调 `deleteCharacter` 再 `deleteRepo`，否则卡残留磁盘。别只删 localStorage 记录。
- `rag_backend._norm_url` 与 `image_gen._norm_url` 同名不同义：前者归一嵌入接口，后者归一图像接口，行为不同，别合并。
- 后端 `generation_store` 与前端 `useChatSession` 两条留存管线：运行环境不同，强合并增险。
- `ai_common` 的 `build_chat_model`/`chat` 薄封装：错误映射跨 8 端点复用，是深的。
- 模型可用性以真实最小推理为准，`/models` 目录成功只证明连接、鉴权和目录接口。剧情出现 `502 upstream_error` 时先按 `run_trace` 定位调用阶段，再用当前模型极短请求、同家族模型、跨家族模型逐级对照；短请求也失败时禁止误归因于上下文、Claude 消息规范化、RAG、表格或插画后处理。
- 剧情插画生成的图**只存 `url + 一句 caption`，绝不回灌为图像 token 进对话历史**：否则自动配图每回合翻倍烧 token。插画 prompt 从已跟踪的 state 构建，不额外拉上下文（见「剧情能动性引擎」支柱 3）。主 Roleplay 完成并剥离控制块后，最终正文 `replace` 与 `illustrate_request` 立即发出；前端把媒体提交给 ComfyUI 原生队列并独立守望，Agent 事务完成即允许下一轮对话，下一轮图片不作为模型输入。
- 多元数据插入（ComfyUI 异步出图/视频）**不新加同步 renderer 分支**：后端按高潮锚点发稳定槽事件，前端后台原位回填。多人 LoRA 的唯一图变换入口是 `workflow_injector.inject_lora_stack`，不得在前端或其他服务复制节点接线逻辑。`template_store` 仅在模板恰好有一个 `EmptyLatentImage` 且没有手动 Latent 语义时自动暴露宽高；多 Latent 工作流必须人工指定。智能模态使用主 Roleplay 同轮 `subjects/actors` 与本地 `infer_motion`，别另调 LLM 判断角色或图/视频。
- 后台活动点击返回作品必须同时更新 App 的父仓库 `repoId` 与小仓库 `workId`；只写 `#/chat/<threadId>` 不会驱动当前 App 状态。导航离开不是取消，显式停止才允许 abort。
- **停止生成必须能打断「无增量窗口」**（2026-10-21 用户实锤）：取消探针下沉到 `llm.chat_messages_stream` 流式读取层（`should_abort`，主线程每 0.2s 轮询），置位即抛 `StreamAborted` 并 `close()` 专属 `httpx.Client` 强制断开上游 TCP——**不得只在 `on_delta` 回调里查 `cancel_event`**（中转缓冲式下发时首字 40~80s 才到，那段时间停止不生效：thread 准入不释放、前端 60s 后提示「后端仍在收尾」、上游继续生成计费）。`StreamAborted` 不重试不包装；`agent_stream` 转 `RoleplayInterrupted`（中断≠失败）。未传 `should_abort` 的既有调用方保持旧行为。
- 从资产管理下钻打开小仓库时只切作品，不得把当前 `workMode` 强制改为剧情模式；hash 必须保持为当前模式。
- 多角色状态字段用 `角色名·字段` 明确归属；无分隔符的旧字段只兼容识别“身体/精神”等类别，展示时归回同一角色卡，禁止把状态类别当作角色名。同一字段已有任一明确归属键时，无归属旧副本必须淘汰，禁止回退到作品卡名形成重复角色卡；唯一例外是裸键 `好感度`——单主角模式的全局好感度就是它，被多角色字段连带淘汰会让好感度凭空消失，必须保留。第一人称主角（`主角/我/你/玩家` 等）不是剧情角色：AI 输出的主角字段直接丢弃、不注入主控叙述、前端不显示，主角自身状态只写 `<status>` 状态栏。
- 裁判（`agency`）是**纯规则引擎不是 LLM**：好感度档位阈值 + 掷骰 + core 一致性驳回。别改成「导演/裁判 Agent 每回合判断」——那与省 token 初衷冲突，且不可复现。创造力只留给「世界 Agent 生成动作」这一处。
- 角色 `core`（人设/外观/死穴/机制）**永不自动更**，只有用户能改；只有 `state`（好感度+态度/心情/所在）随剧情走 StateDelta。人为乱改若不带证据 → `source:user, 证据:空`，AI 据此识别为「设定注入」而非剧情，不强行自圆其说到卡死。别把 core 做成可自动生成（人设会崩）。
- `fabric_work._scan_charfacts(root=库根)` **有意跨作品**（2026-09-17 T6.2 用户拍板永久豁免，二轮审计 #23 同判）：前序产出档案的「角色素材段」扫库根 `_prep/charfacts/`，会把其他作品的素材段也列进来。不改的理由：charfacts 按作品命名可区分、库根级素材对冷启动有参考价值，收紧会让素材全在库根的老仓库瞬间「失忆」。已有形态锁（`test_fabric_work.py`）固化此豁免注释——别再当缺陷报，也别顺手「修」。
- `image_agent.analyze_image` 工具**已删除**（2026-09-17 用户拍板）：图生图（`image_to_image`）接口自己看图，「反推提示词」能力在 img2img 设计落地时即被有意砍掉，工具副本是死代码且 system 提示词虚假声称可调（一调即 unknown tool）。**「反推」活实现只存在于 supervisor 的 `analyze` 一等路由**（`agent_graph.analyze_node`，工具开关键 `analyze_image` 归属 `agent_store.TOOL_KEYS`，与本删除无关）。删除锁：`test_image_agent_build.py`（无此符号 / 工具表无此名 / 提示词无声称）——别重新加回单 agent 工具副本。
45. **世界书语义召回白名单准入 `smart_recall`（2026-10-22）**：修一处**召回段无「涉及」判据**——`recall_pairs = candidates − (keyword命中 ∪ constant)` ⇒ 召回段 100% 是「语义相似但没被点名」的条目，实测**有效率仅 ≈30%**（21 张角色卡 15 张、14 条事件 5 条 keyword 命中为 0 仍被硬塞；零命中 27 条内容型占 **48,252 字 = 内容型 62%**）。

   **对标 ST Vector Storage 的 `entry.vectorized`**（`public/scripts/extensions/vectors/`，Chromadb，`activateWorldInfo()`：条目级白名单 + `score_threshold=0.25` + `max_entries=5` + 旁路事件 `WORLDINFO_FORCE_ACTIVATE` 在 `world-info.js:4605` 优先于 constant/keyword 消费）。⚠ **ST 不对条目做任何模型预处理，直接 embedding 原文**——用户记的「用模型把条目拆成两个句子」是 summary indexing / HyDE，ST 只用模型对**聊天记录**做 summarize 生成 query 文本。Demiurge 同样是原文 embedding（`Document(page_content=entry.content)`），但多一层内容 sha1 增量。

   **修法**：`Entry` 加 `smart_recall: bool | None = None`（三态，`None`=按 `comment` 前缀推导）；`_RULE_PREFIXES=("全局机制","系统判定机制")` ⇒ 规则型默认 False（freq 对它们无意义，本应 constant）、内容型默认 True、空 comment 兜底内容型（**宁可多召回，不可静默失语**——误召回只费 token，漏召回会让 AI 在事实真空里编造）；`recall_eligible()` 是唯一判据入口。

   **四处过滤必须同步**（漏一处白名单形同虚设）：`ensure_indexed` 的 `retrievable`、`_index_delta` 的 `retrievable`（**必须在此**——`schedule_index` 直调它做变化探测，不过滤就不触发清理，库里会残留已踢出的旧向量）、`_sparse_retrieve` 的 documents（从内存 entries 直接构造 BM25，不经索引层）、`_retrieve`（不用改，只读库）。**存量清理自动**（`content_hash not in desired` ⇒ 判 stale 删除），无需手动 purge。

   🔴 **只影响语义召回，不影响 keyword 点名与 constant**——被踢出的条目一旦被提到，仍由 `_keyword_hits` 精准带回**锚点段**（`test_白名单排除条目_keyword点名仍能进锚点段` 钉死）。**Curator 不抹掉它**：`apply_repo_ops` 的 patch 按 key merge（`worldbook_edit.update_entry` → `target.update(...)`）⇒ 设计意图＝白名单属审计层，不该由 AI 运行时改。

   **判据体系四层**（`_tmp/wb_audit.py`，回答「那么多条目怎么保证确认谁该稳定开启」）：L1 类型（comment 前缀）/ L2 频次（`chat.json` × keys 命中轮数，**最硬**）/ L3 兜底（keys 覆盖 ⇒ 决定移出是否安全；21/21 角色卡含本名 ⇒ 几乎零损失）/ L4 轨迹（session `seq` 里有没有它 ⇒ 区分「长期在场」与「被硬塞」）。L2 分档：≥50% 高频（keyword 已稳定带进锚点段 ⇒ **不需要 constant**，不占「永不裁」预算）/ 10–50% 中频 / <10% 低频零命中（必须移出）。

   **实测**（69 条 / 25 轮 / 89,152 字）：规则型 11 条 freq 全低但 constant 正确；内容型 58 条 = 高频 11 条/9,787 字 + 中频 13 条/11,336 字 + 低频 7 条/9,016 字 + **零命中 27 条/48,252 字**；`seq=29` 里角色卡仅 4 张且全 constant ⇒ 其余 17 张只能靠语义召回进注入。**分工修正**：内容型「稳定开启」应交 keyword 通道，`constant` 只留规则型 11 条；杠杆 A 晋升的 3 张卡 L2 freq=0% 但 L4 在 `seq` ⇒ constant 有行为依据，可考虑退回非常驻（省 7,069 字）。

   **数据层工具**：`_tmp/wb_smart_recall.py`（`--mode tiered` 默认 / `--mode l2` 激进 / `--apply` 带备份 / 规则型前缀硬保护），tiered 候选 **28 条 / 46,626 字**、l2 候选 **30 条 / 49,893 字**（均未 apply）。⚠ L2/L4 是**当前剧情线**统计，换线会反转 ⇒ 不能钉死，新长篇重跑 audit。**顺带脏数据**：4 条 `uid=None`；`人物·韦尔纳(305字)` keys 空且与 190 字那条重复 ⇒ 永不被 keyword 命中（工具不自动踢 keys=0，交人工）。

   **漏选实测（2026-10-22 16:30，回答「宁可多选不能漏选」）**：🔴 `worldbook_session.json` **不能作激活证据**——`seq` 只登记锚点（keyword/constant），语义召回从不进 seq；文件 mtime 还早于 worldbook/chat ⇒ 漏选判定改用**重放对话 + 逐轮调线上真身 `assemble_selection_stable`**（`_tmp/wb_ab_stable.py`，每臂 session/state 隔离；dense 本机为空 ⇒ 语义=BM25）。实测（12 用户轮）：base 无关注入 **33%**（72,131/220,892 字）⇒ **l2 激进档 −62% 但新造 1 例漏选**，**tiered 分层档零漏选、−20%**，还治好 5 对既有漏选。**唯一红线 = 角色卡·埃姆登 轮23**：名字在 3,626 字消息第 227 字、扫描窗口只取尾 1,800 字 ⇒ keyword 永不可见，BM25 top-4 是唯一通道；她是 BM25 磁石（12 轮注入 11 轮、10 轮噪声）⇒ 按条目踢治不了，正是**阈值闸（RRF min_score）**标的。**L2 迟登场偏差修正**：弱涉及条目 `keys[0]` 在任意消息出现过就不踢（tiered 档；埃姆登卡/E3 埃姆登因此保留，奇尔沙治卡等 5 条身份 key 从不出现故踢）。既有漏选根因（与白名单无关）：窗口 1,800 字尾截断 / top-8 被高频旧卡占据 / keyword 只看最近 2 条。

   **绿灯决策设计 + A/B（2026-10-22 傍晚，用户提案对标 ST SP·数据库；P0 数据层已落 19:34，未提交）**：给白名单条目加 `description`/`trigger_when`/`tk` 元数据（索引化时批量生成，dry-run `_tmp/wb_skillify.py` 53/53 零质量问题），每轮由决策 Agent 依据**最近 2 条消息全文**（宽于检索窗口，两窗解耦零 prompt 成本）+ 候选池**元数据**（53 条仅 8,675 字，不给正文）绿灯选择。4 臂重放实测（`_tmp/wb_greenlight_ab.py`，24 次 LLM 调用零失败）：**绿灯臂 39/39 修复全部漏选（含 9 对强涉及）且无关注入 33%→19~21%**；纯放宽窗口臂 39/39 但噪声升至 36%、注入字 +32% ⇒ 窗口放宽不作主修法。。实现：`worldbook_greenlight.py`（绿灯选择 + skillify 兜底 + 写回）+ `assemble_selection_stable(recall_override=)` 召回轨替换 + `schedule_index(on_backfill=)` 异步兜底（与索引增量解耦，导入路径不阻塞）；门禁 4189/0F/0E/0S、ruff 过。🔴 实测修正：key 判据把「提及但明确不在场」当涉及（轮23 原文「尚未登场」⇒ LLM 不选正确），且元数据措辞显著影响选择（gen1 39/39 vs gen2 保守）⇒ 漏选率以真机试跑为准，重生成元数据后必须重跑 `_tmp/wb_p1_engine_replay.py`白名单在绿灯下仅剩 2 个点噪声收益且会挡掉 topic 相关条目 ⇒ **绿灯落地后白名单降级为软池或退场**。成本每轮 +1 次决策调用（≈8k token 输入）。双通道 `plotGreenlights`（任务驱动）留作剧情推进/预期式激活的机制载体——keyword/语义都是「文本已提及」的反应式，无法激活尚未提及的条目（下一女主冷启动）。

   **回归**：`test_worldbook.py` +9 例（42 passed）、worldbook 相关 6 文件 127 passed、全量 **4162 passed**（3 个既有失败在 `test_thumbnail_store.py` 的 `tests.test_asset_search` 导入，stash 对照验证与本次无关）。手册 `A/02` §3.7 新增小节。

46. **世界书新增条目重复闸 `DuplicateEntryError`（2026-10-22 L1，未提交）**：起因是韦尔纳占两条（190 字有 keys + 305 字 keys=[]），真机轮两条并注 ≈495 字/轮；三条写入路径（UI/curator `add_entry`、卡导入、独立书导入）此前**零重复检查**。判据经真实世界书 69 条校准，任一命中即拒：① 内容归一化后完全相同；② 归一化**共同前缀 ≥ 14 字**（真案例 16 字 / 合法对 0~6 字 / 全库仅 2 对 ≥12，12 字那对为边界假阳性 ⇒ 阈值 14）；③ comment 归一化后同名。🔴 **不用 difflib 相似度——校准实测反序**（真重复 0.239 < 合法机制对 0.272，中文叙述+模板化机制文本上判据不可用）；🔴 **不用 keys 判重**（角色卡与其 E 事件条目共享身份 key 是设计常态）。接线四处：`apply_repo_ops` 记 `rejections[reason=duplicate_entry]` 不中断同批、两个 router 转 409、导入路径 skipped+warnings；空内容不参与判重。测试 +10（edit 8 / router 1 / store 1）。
