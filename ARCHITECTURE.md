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

## 架构深化进度（2026-08-11）

1. **工作流抓取事务（已完成）**：`workflowCapture` 统一拥有完整画布载入、单次帧请求、可选 ops、原生 `graphToPrompt` 抓取、来源校验、重试、超时与清理。`WorkflowCard`、`AIBuildView` 共用同一 Interface；卡片不再实现隐藏 iframe 事务。模板工作流是拓扑真源，单节点 iframe 只回传参数；捕获结果必须保留真源全部节点，残缺旧草稿自动回退模板，禁止单节点画布覆盖完整图。
2. **会话生成生命周期（已完成）**：`workflowGenerationRuntime` 统一拥有 pending 持久化、提交时仓库归属快照、轮询节奏、连续 `not_found`、恢复检查、超时保留和并发 finalize 双闸。`useChatSession` 只提供媒体落盘与界面反馈 Adapter；前台轮询和刷新恢复不再各写一套收尾顺序。后台活动面板只读 pending，不得删除生成真源；原图或会话槽未持久化时必须保留任务重试。
3. **剧情回合事务（已完成）**：`roleplay_turn` 统一拥有主生成 → 状态/插画写回 → 输出正则 → 正文提前发布 → Curator/纪要/表格维护的顺序；正文与插画槽发布后，`post_turn_maintenance` 按作品串行执行维护，前台 Agent 立即结束并释放同仓库下一轮准入。`agent_graph` 保留路由及世界书、角色卡、预设、RAG、表格只读上下文装配。
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
| `agent_graph` | 多 Agent 主编排。编辑模式按 `workspace_mode=edit` 零模型直达 `edit_node`；无卡对话、带附件请求仍由 Supervisor 语义分派；**有卡纯文本直达 Roleplay** | 编辑模式不得进入 Roleplay/生图/MCP 分派；模糊任务理解仍改 Supervisor；强执行规则必须保守、可单测 |
| `roleplay_turn` | 剧情回合执行事务：主生成后依次做状态/插画写回、输出正则、原位锚点和正文提前发布；已即时发布的回合把非关键维护交给 `post_turn_maintenance`，未即时发布的离线路径仍同步完成维护 | 改正文发布与维护顺序只动这里并测公开 Interface；上下文装配仍归 `agent_graph`，不得让维护重新阻塞正文或把控制内容写入对话 |
| `post_turn_maintenance` | 正文交付后的表格、纪要、Curator/世界书维护调度；同一作品串行、不同作品独立，不占用前台对话准入 | 这里只拥有调度与异常隔离，不拥有维护规则；维护失败只记 Trace/日志，禁止回写或复活已删除的对话消息 |
| `edit_agent` / `edit_agent_profiles` / `edit_session` / `edit_publication` | 编辑主管确定性选择六类专家；`edit_session` 在工具层强制只读/写入意图、先列后写、先读后覆盖和写前+完成前机械校验；`edit_publication` 把小仓库产物受控发布到后端设置中的 characterDir/presetDir，并支持 PNG 附件保存为头像/表情；排错可按当前 repo/turn 读取脱敏 Trace | 普通文件工具只操作当前小仓库；源库根只读后端 user_state，不信任请求路径；角色卡内部必须扁平且 worldbook/regex 只走侧车；PNG 卡元数据迁移仍走导入 UI |
| `edit_import_adapter` | 外部 JSON 资源到 Demiurge 的确定性转换：纯角色卡只生成扁平 card，内嵌内容拆为 worldbook/regex 侧车；独立世界书统一 entries/key/enabled；预设剥离连接鉴权并补项目扩展；正则补 ID 和运行字段后编译校验 | 只读当前小仓库内源 JSON，默认拒绝覆盖目标且不删除源文件；PNG 卡必须走现有角色卡导入入口保留头像与元数据 |
| `edit_artifacts` | 编辑产物机械校验：Demiurge 归一化角色卡、世界书侧车、预设 identifier/order、注入字段、`thinking_chains`、三层正则字段与编译、Python 语法及基础 JSON | 自动推断必须先按项目文件名和归一化卡字段识别，禁止因 `card.json.regex_scripts` 把整卡误判为正则；格式失败写 Trace，禁止把模型自述当验证结果 |
| `project_files` | 当前小仓库文件权限单一属主：根由 `repo_meta.output_dir_from_state()` + `repo_folder(repo_id)` 后端解析；提供列出、UTF-8 读取、原子写入、精确替换 | 拒绝绝对路径、`.`/`..`、符号链接逃逸、二进制/非 UTF-8、目录和超过 1 MiB 的文件；不提供删除；模型/前端不能指定根目录 |
| `agent_graph.roleplay_node` | 酒馆 Agent（剧情扮演节点）：作品可绑定多张角色卡，并由 `opening_card_name` 决定空会话第一句及首个真实剧情回复唯一可注入的角色描述；后续在世界书检索后，只把本轮输入直接角色名与本轮输入精确触发的世界书 key 当作角色出场证据，均未命中时才回退最近一条 AI 剧情。历史回退按角色最后出现分句识别离开/离场/不在、否定离场及重新入场；角色名采用最长非重叠实体匹配，避免“莉亚/塞西莉亚”串卡。`constant`、Dense、BM25 条目可以提供设定但不得激活角色卡。`_resolve_personas` 只发送选中卡的非空字段，并把 `description/personality/scenario/mes_example` 分别送入对应预设 marker；禁止因已绑定而全量发送或把四类字段折叠成描述。后续叠世界书/表格记忆。模型漏 `<status>` 时沿用已持久化快照；未闭合 `<状态更新>` 或 `<illustration>` 仍按尾部控制块剥离；本地高潮兜底锚点被输出正则改写后，只能在最终显示正文重新定位。状态/表格/插画后处理异常时必须用结构化解析器返回干净可见正文，禁止把原始控制块或提示词回退给前端 | 单卡 `card_name` 仅为开场卡兼容别名；其他绑定卡不得出现在开场；同轮多卡仅在本轮均有直接/精确证据时注入；空描述不注入；有卡作品的通用对话统一并入 roleplay |
| `agent_context` | Agent 上下文窗口：各取 6 条、token 预算、历史文本与依赖上文的执行提示词整理。生成历史按 **前端显式可见历史 → 已存在的聊天快照 → 仅快照文件不存在时才回退 checkpoint** 取值；显式 `[]` 和空快照都表示历史已清空，禁止旧 checkpoint 复活已删消息 | 改上下文选择/裁剪 → 这里；`agent_graph` 不内联 token 算法。任何新入口都必须遵守同一历史优先级 |
| `prompt_compiler` | 最终消息编译接缝：OpenAI 兼容档保留历史后 system 原位；Claude 兼容档把历史后约束编译为贴近末轮 user 的本轮执行合同，不得全部前移到 system 头。输出最终 messages、provider profile 与逐段位置 manifest | `provider_profile` 是设置与请求 wire 的显式真源；只为旧调用保留模型名推断，Roleplay 禁止按模型名猜供应商。Compiler 只拥有消息位置，不拥有预设、世界书或表格内容 |
| `agent_contracts` | 编排契约单一属主：`RunContext`(dataclass) + `ModelConfig` + `AgentEvent`。多卡固定字段为 `card_names/opening_card_name`，`card_name` 是兼容别名；生图外貌来源为 `appearance_source=worldbook|character_card`。`history_override` 保存本次请求显式上传的可见历史；`stream_output/stream_sink` 控制节点实时增量出口。**RunContext 既是 dataclass 又当 dict 用**，由 `extras` 与 `_legacy()` 支撑 | 新固定字段必须同时加入 dataclass、`_legacy()`、实时请求、后台队列和双端测试；图节点新增跨节点标记还必须声明进 `AgentState`；**动了这些方法必须重启后端** |
| `agent_request_context` | 即时请求与持久队列共用的 `RunContext` 构造真源；统一默认值、多卡去重、开场卡兼容、显式空历史和全部模型/代理/插画字段 | 新请求字段只在请求模型、前端 `AgentInvocation` 编码和这里各声明一次；禁止 router 或 worker 再手写第二套转换 |
| `chat_agent_queue` | 忙时消息持久队列；headless worker 必须把 `MultiAgentRequest` 的工作区模式、历史、卡、预设、人设、世界书、插画、流式选择和模型参数完整还原成 `RunContext` | 新增直连参数时同步补队列映射与回归测试；编辑任务排队后仍必须走编辑 Agent |
| `run_trace` | Agent 单轮结构化追踪：每次请求由 `RunContext.turn_id` 贯穿，按 UTF-8 JSONL 写 `backend/data/logs/agent-trace.jsonl`；记录原始/处理后输入、Supervisor 与各 Agent、完整模型消息与输出、世界书/RAG 注入、纪要/知识写入、状态写回、独立表格维护及首尾错误。按大小轮转并递归脱敏密钥，追踪失败不阻断主流程 | 新增运行阶段先复用 `run_trace.emit(ctx, event, **data)`；不得把 API key/token 放入自由文本。环境变量：`LAF_AGENT_TRACE`、`LAF_AGENT_TRACE_MAX_BYTES`、`LAF_AGENT_TRACE_BACKUPS` |
| `tool_agent_adapter` | 把遗留 `image_agent` ReAct 流适配成专家节点结果 | `agent_graph` 不直接依赖其长参数和事件细节；替换旧实现只改 Adapter 后面 |
| `generation_approval` | 提示词审批状态机 + 已批准的图像/视频执行 + 失败语义 | 改确认/更改/取消/重提流程 → 这里；`agent_graph` 只调用其 Interface |
| `image_gen` | 云端文生图 `/images/generations` 与带参考图 `/images/edits`，统一超时、质量参数和 64–3840px 尺寸边界；`_load_image_bytes` 支持 data-uri/http(s)/**本地文件路径**(角色底图,桌面单机后端直读同机文件) | 新增图像供应商请求规则 → 这里，调用方不拼 payload |
| `image_prompt_profiles` | 多元数据插入提示词协议单一属主，三层顺序固定：①剧情事实底座 ②跨模型艺术决策 ③转换为 Krea2、Anima、GPT Image/Banana 或 Niji。角色姓名只作“剧情人物→外貌条目→LoRA 配置”的本地关联键，四 Profile 最终正文移除原姓名；多角色靠具体外貌、当前服装、动作和位置区分。Profile 必须逐项翻译实际发色、发型、发饰、五官、体型、配饰和鞋袜，禁止用 `identified character`、`preserve identity`、`established facial structure`、`defined by the bound model` 等占位句代替。条目 `【穿着】` 只作基线，wardrobe 或正文中的当前状态优先。角色视觉条目查询使用短历史＋本轮输入，但必须机械追加最近一次 `<status>` 的 `[在场]` 单行，禁止因状态栏被 2000 字裁剪或用户本轮只说“继续”而丢失稳定外貌；条目未声明瞳色等属性时不得推断。`inline_generation_instruction` 是主 Roleplay 同轮隐藏成稿的格式真源：Krea 单段、Anima tags＋英文关系描述、Natural 单段、Niji 四段；`inline_output_token_reserve` 只在用户显式配置正文上限时追加隐藏预算。四 Profile 共用视觉事实与拒答门禁，并生成外貌、当前服装、动作、地点、镜头、构图、光影、材质、质量的字段账本；可验证事实缺失时只局部补齐，不覆盖已经合格的高潮和艺术决策。拒答、漏块、硬错误或坏格式才由确定性兜底接管。自动链路不再触发第二次模型调用。Krea2 不做场景或 SFW/NSFW 分类，按“构图留白→角色服装→镜头透视→有机材质→光影色彩→画质完成度”六维输出单段英文。Profile 完成后 LoRA 元数据阶段才机械注入精确触发词和作者质量建议 | 新增模型提示词协议或格式约束 → 只改这里和对应测试；四种格式可不同，但姓名只作关联键、具体外貌与当前服装优先级不得降级；Krea2 禁止重新引入场景/分级分支；负面词禁止混入正向文本 |
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
| `generation_store` | 「生成提交/完成→留存→入库→写快照」后端管线。Roleplay 发出最终 `replace`/`illustrate_request` 时先即时持久化正文与稳定媒体槽；自动路径调用 ComfyUI 前必须通过 `claim_illustration_submission` 原子认领 `messageId + slotId`，同槽重复/迟到事件失败关闭；ComfyUI 返回 `prompt_id` 后再通过 `persist_illustration_submission` 绑定同槽；带目标槽的自动插画只原位回填，不追加对话轮、不写提示词历史；失败写 `illustration.failed` Trace 并删除快照槽 | 后端出图后的持久化 → 走 `persist_image`；不得先提交 ComfyUI、再用 `slot_bound=false` 事后发现目标无效；自动插画目标消息已删除、槽已完成或已认领时禁止提交第二个任务；generation RAG 是资产库成员与提示词真源，禁止扫描磁盘自动补录；普通资产删除只删 RAG、保留文件，裂图清理只删本地文件已缺失的 RAG |
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
| `useChatSession` | 聊天会话引擎：messages、聊天/队列编排、快照与媒体落盘 Adapter。删除、重新生成、显式历史、导航后台化和自动插画提交合同保持不变；ComfyUI pending/轮询/恢复/finalize 顺序委托 `workflowGenerationRuntime` | 不得重新内联 pending localStorage、轮询计数或恢复分支；界面提示、媒体原位回填和进度 WS 留在 Hook |
| `useChatAgentQueue` / `useChatTransfer` | 队列提交/取消/恢复和会话导入导出事务 | `useChatSession` 只传依赖与消费结果，不得重新复制 API 顺序 |
| `useChatPresentationAssets` / `useChatUnreadTracker` | 角色头像表情投影与后台完成未读跟踪 | `ChatView` 只渲染投影结果，不直接维护跨刷新状态 |
| `useTableWorkflows` | 表格统计、重建、手动补表与覆盖确认的前端事务 | `TableModal` 只保留弹窗本地输入和展示，不直接编排 API |
| `chatGeneration` | 生成流程的纯判定/整形：图像门、文本打分、快照瘦身、纯文本历史、重跑消息、当前 LoRA 精确绑定和作者建议质量词筛选。提示词组装是固定两阶段合同：先由 Profile 融合「剧情高潮提炼＋角色条目稳定外貌」；Profile 完成后再按实际加载 LoRA 的完整文件名查表，机械注入精确触发词和筛选后的作者质量建议，并与已生成提示词大小写不敏感去重；查表失败必须显式失败，禁止提交缺失第三阶段的图。Anima 最终结构固定为首行「当前 LoRA 触发词 → 作者建议质量词 → 作品固定质量词」，第二行「剧情 tags + 英文关系描述」；`imagePromptProfiles.illustrationTemplateValues` 按语义独立组值 | pending/轮询/finalize 不属于此 Module；提示词质量词不受尺寸换算影响，采样参数仍归工作流模板 |
| `generationLifecycle` | 生成三态 reducer（idle/agent/workflow）+ 派生 selector。**新增生成状态改这里，别加影子 ref** |
| `workflowGenerationRuntime` | ComfyUI 生成生命周期深模块：pending 存储、提交时 `threadId/repoId/outputDir` 归属快照、提交后守望、恢复检查、轮询节奏、连续丢失、超时保留和幂等 finalize 双闸 | Hook 通过 Observer 接收完成/失败/释放/超时；前台与恢复路径必须共用同一 Runtime 实例。仓库切换不得改变已提交任务的归属；展示层丢失 pending 不等于用户取消，只有显式 `cancel` 才禁止迟到结果归档。刷新加载后先用本地 pending 的 `promptId` 修复同目标快照槽，再清理真正未提交的孤儿槽；顺序不可颠倒 |
| `workflowOrchestration` | 工作流输入口编排 hook（读节点→AI 出计划→写画布） |
| `workflowCapture` | laf_lock 帧事务：单次请求/回复及隐藏完整画布的 load→ops→原生 API 图抓取；统一来源校验、重试、超时和清理。NodeCard IFrame 载入前通过 `clearComfyStorage` 清理 localStorage/sessionStorage 中工作流残留，防止之前打开的大工作流被 ComfyUI 会话恢复覆盖导致只显示整图而非单个节点 | `WorkflowCard`、`AIBuildView` 不得自行监听同类 `api_prompt` 生命周期；节点局部画布的自愈时序仍归各自展示场景 |
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

## 加功能决策树

- **要调 ComfyUI？** → HTTP 原语进 `comfyui_client`；模板/画布提交事务进 `workflow_submission`；路由只映射领域异常。
- **要改智能体分派/上下文/审批？** → 分派进 `agent_graph`，上下文窗口进 `agent_context`，审批与已批准执行进 `generation_approval`。
- **要调对话/生图模型？** → 复用 `llm` / `image_gen`；前端用 `chatBody` 拼请求。
- **要碰普通知识库？** → 领域操作进 `rag_store`，纯排序进 `rag_retrieval`，嵌入/Chroma 进 `rag_backend`。
- **要碰节点索引？** → 单路存储与召回进 `node_store`，同步/多查询融合进 `node_index`，安装判断只读 `object_info`。
- **要改 AI 搭工作流？** → 回合上下文进 `workflow_build_turn`，图规则进 `workflow_graph_rules`，模式差异与模型重试才进 `workflow_builder`。
- **要动生成流程/持久化？** → 后端进 `generation_store`；前端生命周期顺序进 `workflowGenerationRuntime`，媒体投影与界面反馈进 `useChatSession`。
- **要加页面/子帧交互？** → 组件收 props 渲染；协议走 `lafLock`；纯算法进 `lib/` 并导出。
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

## 主题资产生成

`scripts/theme_asset_pipeline.py` 是主题资产处理的唯一 Implementation，拥有透明清理、裁剪、缩放、WebP/PNG 编码、图集排版、引用顺序和目标路径安全。

每个主题只在 `scripts/theme_assets/<theme>.json` 声明源文件、处理参数和前端目标槽位。`process_<theme>_theme_assets.py` 只能作为兼容命令入口选择清单，不得新增图像处理函数。新增主题优先复用已有 `op/preprocess/transform`；确需新变换时只扩展 pipeline 并给所有清单复用。

## 发布架构

源码上传与终端用户 Runtime 是两条独立发布链。`scripts/release_preflight.py` 是源码上传边界的单一检查入口：它检查 Git 已追踪文件和未忽略的未追踪文件，阻断密钥、私钥、运行数据库、模型权重、超大文件及误纳入的用户目录，同时验证 `styles.css` 引用的主题资产存在且未被忽略。脚本只读，不暂存、不提交、不推送。

源码必须包含 `backend/app`、`frontend/src`、测试、依赖清单、`frontend/public` 主题资产和 `comfyui-ext` 扩展源码。源码禁止包含 `backend/data`、`userdata`、未清洗的 `presets`、`docs/memory`、会话、角色卡、世界书、生成图片、RAG 索引、模型/LoRA 权重、日志、环境目录和构建产物。唯一允许的预设目录是由 `build_resource_pack.py` 生成的 `presets/Demiurge-presets-regex`。详细清单见 `docs/RELEASE.md`。

归档只能基于用户确认后的提交执行 `git archive HEAD`，禁止直接压缩工作目录。上传前必须逐项审计 `git status`；当前工作区存在大量历史改动时，禁止用未经审计的 `git add -A`。

`scripts/runtime_release.py` 是固定 Runtime 的单一构建 Implementation，`release/runtime-targets.json` 是平台、架构、Python 与 Torch 的唯一矩阵。终端用户仅发布 Full RAG，包含 Python、基础后端依赖、已构建前端、Chroma/BM25，以及平台对应的 Torch、Transformers 与 SentenceTransformers 依赖层；不包含 Embedding/Reranker 权重、Hugging Face 缓存、ComfyUI、密钥、用户数据或 RAG 索引。

Runtime 采用 Base/Application/RAG 分层清单，每层逐文件归档并记录 SHA256，超过 GitHub 单资产限制时按 1.9 GB 分片。`runtime_entry.py` 只负责组装层路径、设置可写数据目录、执行模块自检并在 8010 同源服务已构建前端；ComfyUI 必须使用独立 Python，禁止回退应用 Runtime。Windows `portable_release.py` 组装 `start-dev.bat`/`stop-dev.bat`、分层 Runtime 与 MinGit；启动脚本用 PID 文件绑定当前包的 Runtime，停止脚本必须核验进程路径，禁止仅按端口误杀。macOS/Linux `unix_portable_release.py` 生成可执行启动脚本。终端用户包必须做到解压即用，不运行 pip、npm，也不依赖系统 Python/Node。

RAG 构建使用 `backend/requirements-reranker.txt` 声明直接能力、`release/requirements-rag.lock` 固定跨平台传递依赖、`release/runtime-targets.json` 固定各平台 Torch。三者共同进入 RAG definition ID；禁止只写 `>=` 后让新构建静默漂移。CUDA 驱动和 ComfyUI Python 不属于 Demiurge 依赖层，构建可以复用已校验层或 pip wheel 缓存，但发布包必须自带隔离文件。Windows 便携启动可用 `DEMIURGE_PORT` 覆盖默认 8010；Runtime 接收对应 `LAF_RUNTIME_PORT`，默认用户行为不变。

`.github/workflows/runtime-release.yml` 在版本标签上执行双端门禁、三目标 Full RAG 矩阵构建、真实冻结 Runtime 自检和 Release 上传；`portable-release.yml` 只消费已校验分层资产组装 `00-USER-DOWNLOAD` 包。任何新平台、Torch 或 Python 版本只改目标矩阵；新增运行依赖先确认 Base 或 RAG 层属主，禁止把依赖同时写进工作流和多个脚本。

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

### Model Lease、Scenario 与 Procedure Seam

`model_lease` 是本进程 GPU 能力租约 Runtime。ComfyUI 提交优先级最高，可要求释放 VLM、Reranker 与本地 Embedding Adapter；各 Adapter 自己拥有真实卸载函数，Runtime 不 import 模型实现。租约解释的是本进程已知占用与排队原因，不等于跨进程精确显存计量。

`scenario_lab` 的完整快照同时包含作品文件、SQLite 数据库和仓库专属 Chroma collections。SQLite 使用 backup API 并显式关闭连接；不可变媒体优先硬链接；v2 manifest 记录版本、回合、文件 SHA-256、源 size/mtime 和向量集合。新快照先在同目录 staging 完成后原子发布；未变化非数据库文件复用上个 manifest 的哈希，避免每回合重新读取全部图片。恢复只允许空目标仓库，绝不覆盖正式时间线；文件或 Chroma 恢复失败必须清理本次写入，多候选实验后续分支失败必须回滚前面已完成候选。前端每个完成回合按 `turn:N` 去重创建快照；`scenarioBranchRuntime` 统一拥有保存消息、匹配/创建快照、fork、登记本地分支和导航顺序；从历史消息分支只能使用同回合快照，找不到时必须拒绝。反事实候选之间物理隔离，不自动合并，用户选择只记录正式候选 ID。

`capability_sandbox` 提供短期、可撤销、精确到 operation/path/domain/tool 的能力租约；`procedure_skills` 从脱敏 Trace 提议流程，必须经用户审核、dry-run、受支持 Adapter 检查和能力授权后才执行。Prompt Skill 与 Procedure Skill 是两个 Module；外部 Smithery Skill/MCP 默认关闭并标记 `external_unreviewed`。当前 Procedure 执行面刻意只开放已注册安全 Adapter，未知动作不得退回任意 shell/MCP 执行。

### Supply Chain 与 Instruction Provenance

当前只完成小型硬化，不宣称完整 Artifact Trust：模型下载记录清洗后来源、格式风险、SHA-256，任务临时文件按 task id 隔离且拒绝覆盖已有目标；节点更新记录当前 commit、remote 与 requirements 哈希。Smithery 技能默认关闭并标记 `external_unreviewed`；角色卡与技能提示词经 `instruction_provenance` 标记为低权限外部内容，不能扩大工具、文件、联网或安装权限。长期 artifact/plugin manifest、expected hash 强制比对、requirements URL/VCS 策略与隔离安装仍是后续边界。

### Temporal Fact Ledger

`temporal_fact_store` 只拥有任意世界/实体事实的有效区间、证据哈希、来源、冲突和显式 `supersedes_id`。关闭旧事实必须由调用方提供同一 subject/predicate 的替代 ID，禁止模型猜测。好感度、态度、心情、所在及角色身体/衣着等仍由 `character_state` 单一持有，账本拒绝写入这些 predicate；纪要 rowid 会压缩/删除，账本身份只绑定稳定 repo、turn、内容与 evidence hash。

### 2026-08-10 运行态影响审计

> 这是历史审计快照；2026-08-11 的当前结构、门禁和评分见下节与 `docs/memory/architecture-performance-2026-08-11.md`。

- **Structured Output：部分生效。** Supervisor、纪要、手动填表已迁移到 Pydantic 合同；工作流搭建/端口编排复用统一 JSON 解析。真实 Trace 已记录 `legacy_text` 成功与坏 JSON 校验失败，证明统一错误面生效。当前模型 wire 仍未声明结构化能力，生产请求没有启用 `native_json_schema`；除 Supervisor 外的迁移点也尚未统一写 `structured.output` Trace。因此它现在主要减少解析分叉和静默格式漂移，尚未减少模型重试或保证原生约束解码。
- **Trace Replay：纵向 MVP 生效。** `/api/ai/trace/replay` 可对已录响应和 turn/route/illustration 事件不变量做无副作用复验。对仓库 `3d5de442-ba11-48d5-b254-0b6f27d6261b` 的真实 2 个回合复验得到 1 通过、1 失败，失败项准确指出缺少 `turn.completed`。它不是整轮重执行，也不比较文本质量；RecordedModel、内存 Store 和版本化脱敏 fixture 完成前禁止称为全链 Replay。
- **资产语义检索：文本链已生效，视觉链尚未产生数据收益。** 当前仓库 10 张 generation 资产可通过专用 Hybrid 接口返回语义结果；剧情 RAG 与聊天历史均未被污染。现有资产的 `description` 为 0，部分历史 prompt 已有编码乱码，故当前排序主要依赖英文 tags 和未损坏文本。Qwen3-VL-Embedding-2B 的固定 revision、4,255,140,312 字节 safetensors 与 SHA-256 已核验一致，但独立视觉 collection 尚未建索引；实测 GPU 仅余约 1.3 GiB，不能在不影响 ComfyUI 的情况下加载 4 GB 权重。自动描述与“构建视觉索引”已接入资产页，必须在用户实际执行并得到非空索引后再宣称图像向量检索生效。
- **供应链硬化：预防性生效，不是完整信任系统。** 新下载会隔离 `.part`、拒绝覆盖并计算 SHA-256；节点 requirements 拒绝 URL/VCS/editable/自定义索引来源；Smithery 技能默认关闭；真实多卡 persona 与技能片段均带低权限来源标记。下载身份仍只保存在任务状态，节点 provenance 仍是当前任务进度，低权限标记属于提示层约束而非进程沙箱；没有长期 per-artifact/per-plugin manifest 和 expected-hash 强制比对。
- **Temporal Fact Ledger：已进入只读剧情上下文。** Chronicle 可旁路写入世界事实，Roleplay 现在按当前回合召回有效事实并经 `continuity_compiler` 在 900-token 预算内注入；角色活状态仍由 `character_state` 单一持有。没有积累事实的旧仓库仍不会凭空获得收益，RAG/纪要也不会被提升为权威事实。

### 2026-08-11 架构与性能验收

本次只做保持现有行为的深化，不机械拆 `agent_graph`，不改变剧情、NSFW、提示词 Profile、LoRA、ComfyUI 或资产检索协议。当前架构综合 **9.1/10**：边界与依赖 9.3、状态事务 9.3、结构化与 Trace 9.0、性能 9.0、测试与回归 9.6、前端职责 9.0、文档与领域语言 9.0。评分依据是可执行合同与失败路径测试，不以文件数量或界面是否可见代替。

验收基线：Ruff 通过；17/17 import-linter 合同保持；mypy 39 个契约文件通过；硬编码与 wire 门禁通过；后端 1212 项、前端 338 项、Playwright 3 项全通过；生产构建通过。OpenCC 字典仍形成约 516 KiB gzip 的按需 chunk，但不进入首屏，只有打开快捷/完整文本工具才加载，因此记录为可观察的非阻断性能边界，不为消除 warning 改变繁简转换语义。

### Theme Runtime Seam

`styles.css` 的 production theme runtime Interface 统一拥有按钮九宫格尺寸、输入区和拖动手柄几何、模态内容层级、装饰指针隔离、进度节点定位、头像尺寸和着陆页几何。bright/night/eye-care/green/gray 主题块只提供视觉差异和素材 URL；新增主题先接入共享 Interface（把 `[data-theme="<theme>"]` 加进这些 `:is(...)` 结构选择器列表），再补自己的 Asset Pack，不得复制整段结构选择器。`themeRuntime.test.ts` 与各主题资产测试共同验证该 Seam。

## 剧情能动性引擎（联动链上半截 · 已落地，全绿）

> 这一节既是**设计合同**又是**已落地接缝**。六模块（`character_state`/`agency`/`scene_illustration`/`scene_renderers`/renderer 接口/`roleplay_agency` 子图编排）全部落地并全绿，已接进 `roleplay_node`。加功能按此接缝建，别另起炉灶。

### 要解决的本质问题

酒馆是**用户主导**：角色只在被触发时被动响应。Demiurge 要**角色有能动性**——用户没提，爱慕他的角色会自发行动（如舞会下药、用户短期失去主导权），且行为不写死在卡里也能合理涌现。核心洞察：**角色卡的 `死穴`/`攻略路径`/`个体机制` 不是描述，是行为生成器**；系统每拍主动求值「以当前状态+场景，这角色会不会自己动手、怎么动」，机制既是自主性的燃料，也是防 OOC 的护栏。

### 三条设计支柱（互相咬合成一个闭环）

1. **动态条目 = core/state 分离**。`core`（人设根基/外观/死穴/机制/弧线）写在卡文件、**永不自动更**，是视觉锚与一致性来源；`state`（好感度数值 + 态度/心情/所在等叙事字段）按 `repo_id` 作用域、随剧情更。每次更新是**带证据的 StateDelta**（`from→to`+证据+turn+source），AI 召回读到的是「态度:戒备(因第3章救援)」= 角色发展而非矛盾。
2. **能动性三方 = `roleplay_node` 内部子图**。世界 Agent 提案（LLM）→ 裁判仲裁（**纯规则 0 LLM**）→ 主控叙述（LLM）→ 状态更新。World 默认每个剧情回合做一次语义判断（`gateBaseRate=1`，设为 `0` 才明确关闭）；首轮没有好感记录按中性 `0` 评估，敌对角色也不因负好感失去自主性。输入只含在场 NPC 命中的世界书 core、动态状态、近历史与本轮 user，不重复整本世界书。提案必须保留「持续目标 + 本轮具体行动」；持续目标机械写入 `叙事/角色名·当前目标` 供下轮续用，裁判成功则落实，已实际尝试但失败也必须在正文呈现为未遂，不能静默消失。
3. **剧情插画 = state 的第二用途**。ComfyUI 自动插画开启时，每个真实 Roleplay 回复都必须在一次主生成中完成可见剧情和本轮最强视觉高潮的隐藏画面计划＋完整 `profile_prompt`；安静对话以人物关系、目光、动作或关键物件变化作为高潮，不得省略或退化成无关静态肖像。`<content>` 的预设/用户篇幅独立计算，`think/status/table/illustration/profile_prompt` 均不计入正文；配置了 Roleplay `maxTokens` 时，隐藏 Profile 预算追加在正文上限之外。触发、角色补漏、画幅降级和 `motion` 都是纯规则；主 Roleplay 漏计划或成稿拒答/格式失败时，写回接缝仍以正文最强视觉段发出请求并从 `scene_spec` 本地编译当前 Profile，禁止整条请求静默消失或前端再补调文本模型。主计划误选低强度结尾钩子时，后端必须重定向到正文得分更高的高潮段，废弃错误动作、镜头、构图与同源成稿并从纠正段重建。
4. **场景分类 = 一次判断驱动两件事**。`scene_classify` 把本轮归到 `dialogue/action/emotion/conflict/nsfw/climax`，**复用 supervisor 那次路由 LLM 调用**产出（零额外往返），写进 `ctx["scene"]`：既选**条件思维链**（P1，`preset_store.select_chains` 按真状态 scene/affinity/turn 命中链），又决定分级与高潮降级策略。若 Supervisor 误判且主 Roleplay 漏掉 `<illustration>`，写回接缝会对本轮用户输入和还原后的正文再做一次保守纯规则判断；明确 `nsfw/climax` 才生成保守英文 tags，普通剧情只从可见正文选择最强视觉段并交给当前 Profile，禁止误添成人 tags，也禁止整条 ComfyUI 请求静默消失。

### 模块（单一属主）— ✅=已落地 / ⏳=规划

| 模块 | 状态 | 拥有什么 | 边界 |
|------|------|---------|------|
| `character_state` | ✅ | 动态状态单一属主：按 `repo_id` 存可变状态（`数值`+`叙事`字段，每字段带 provenance 证据+turn+source）、`parse_deltas`/`apply_deltas`（带 from→to 审计历史，封顶 200）、`render_state_block`（紧凑 kv+内联证据）。**手改/回滚(缺口6)**：`current_turn`/`set_fields`(设精确值非累加，数值 clamp，标 `source=user`)/`rollback_last`(还原到审计 `from` + 弹历史)。落盘 `<base>/<repo_id>/state.json` 物理隔离 | core 字段永不被本模块写；纯逻辑无 I/O 可单测，load/save 是唯一 I/O；base 由调用方注入不读 config；`state.py` 路由(GET/PATCH/rollback)只做 HTTP 适配，前端 StatePanel 消费 |
| `agency` | ✅ | 能动性**纯逻辑（0 I/O 0 LLM 全单测）**：`judge`(core依据→好感度门槛→掷骰) + `classify_roll(roll,chance)` **六档**(大成功/极难/困难/普通成功/失败/大失败，对齐正文 `<roll>` 语汇，骰≥96 恒大失败) + `should_consult_world`(廉价门控) + `tier_index`/`crossed_tier`(插画跨档)。裁判是确定性规则不是模型 | 吃好感度**快照**，**不 import `character_state`/`agent_graph`**；裁判不得做成 LLM 调用。正文 `<roll>` 由剧情推进 Agent 的 `rollInstruction` 提示词驱动(主模型打)，与本模块隐形裁判(管 NPC 自主行动)同一套六档语汇 |
| `scene_illustration` | ✅ | 剧情插画**纯逻辑（0 I/O 0 LLM 全单测）**：`decide_trigger`(优先级 显式>失控>**场景nsfw\|climax**>跨档>每N段，跨档复用 `agency.crossed_tier`) + `build_scene_request`(从「段落动作+core外观+state衣着/场景」拼 prompt，非空过滤，出图管线的裸拼接降级档) + `fallback_illustration_anchor`（只在 `<content>` 正文评分，忽略 think/控制块，物体放置等可见动作优先；`_anchor_score` 对写下/对折/化作/飞出等状态变化动作单独加权，靠回/嘴角/浅笑/余韵等静态收束词降权）+ `resolve_illustration_anchor`（只在模型锚定静态收束且正文另有状态变化动作链时纠正锚点；写回接缝同步废弃同源错误内联 Profile，并按纠正后的 `scene_spec` 本地重建，禁止肖像提示词继续提交或二次调用文本模型）+ `illustration_anchor_offset`（高潮锚点逐字/破甲还原优先，轻微改写按段落相似度映射；指定锚点完全无效则失败关闭，禁止回退消息末尾）+ renderer 注册表(纯 dict) | 触发是规则不是导演 Agent；吃标量快照，**不 import `character_state`/`agent_graph`/`workflow_submission`/`image_gen`**（scene-illustration-purity 合同强制）；本模块不拥有出图管线 |
| `scene_classify` | ✅ | 场景分类**纯逻辑（0 I/O 0 LLM）**：`normalize_scene`(模型给的场景字段→合法标签，中文别名+子串匹配+兜底空串)。标签 `dialogue/action/emotion/conflict/nsfw/climax`。信号复用 supervisor 路由那次 LLM（零额外往返），此处只解析规整 | **不 import `llm`/`agent_graph`**（scene-classify-purity 合同强制）；只规整不调模型 |
| `image_prompt_extract` | ✅ | 自动插画的纯逻辑接缝：主 Roleplay 在正文后附隐藏 `<illustration>`，同时保存 anchor、镜头、艺术决策、subjects、画幅、动作草稿与当前 Profile 完整成稿。模块在 JSON 解析前允许调用方把块内容经过与正文相同的 `AI_OUTPUT` 正则，以还原预设改变的结构；随后剥块并校验 `anchor/camera/composition/subjects.weight/prompt/profile_prompt/motion/aspect_ratio`。anchor 必须取所描绘高潮段末句，禁止选余韵/收束/尾句；视觉命题必须保留造成状态变化的动作链，静态肖像只能作次级信息。截断或坏控制块整体剥离并返回空计划。`visible_narrative_text` 只读取 `<content>`，排除思考、状态、表格和插画控制块，故隐藏成稿不显示、不入正文长度。比例只允许 `1:1/2:3/3:2/3:4/4:3/9:16/16:9`；主计划缺失时由 `scene_illustration.infer_aspect_ratio` 纯规则选择 | **不 import `llm`/`agent_graph`/`roleplay_agency`/`character_state`**；Profile 格式归 `image_prompt_profiles`，本模块只持有隐藏块协议；LoRA 触发词只按本次最终生效 LoRA 的完整文件名精确读取，空触发词不注入 |
| renderer 接口 | ✅ | 渲染器接口 `Renderer=Callable[[SceneRequest],str]` 在 `scene_illustration` 定义（register/get/available，纯 dict） | 新增图像格式=注册一个 renderer，不改 `scene_illustration` 触发逻辑 |
| `scene_renderers` | ✅ | renderer concrete 适配器（有 I/O，8 测试）：`cloud_renderer`(云→按 `req.actors` 命中 `CloudConfig.character_base_images` 取底图,有则 `image_gen.generate_with_images` 图生图锁角色一致性,否则 `generate` 纯文生图；未命中回退 `style_base_image`) + `comfy_renderer`(本地→`submit_template` 提交 + `fetch_result` 轮询取图 → 拼 `/view` 直链，sleep/now 注入可测)。工厂绑定运行期 config 产出 Renderer 闭包 | import `scene_illustration`+既有出图管线；`scene_illustration` 不反向 import（纯度合同保证无环）；只返回图片地址，不回灌图像 token |
| `narrative_memory` | ✅ | 纪要记忆**纯逻辑（0 I/O 0 LLM 全单测）**：默认每 3 个完整会话中的 assistant 回合新建一条独立 `overview/chronicle/dialogue/characters/keywords` 丰富纪要；召回把 FTS 命中与最近条目合并，优先当前出场人物，主 Roleplay 最多只注入 10 条短概览；旧分层压缩逻辑不得进入自动流程或删除频率索引 | **不 import `narrative_store`/`character_state`/`agent_graph`/`roleplay_agency`**（narrative-memory-purity 合同）；详细纪要与对白不回灌主上下文 |
| `narrative_store` | ✅ | 纪要落盘+召回（SQLite FTS5 trigram，按 repo_id 物理隔离 `<base>/<repo_id>/chronicle.db`）：丰富字段往返并自动迁移旧 schema；自动流程 append-only，历史进度落后超过一个频率区间时停止生成跨区间大卡并交给手动补表；用户编辑与手动重填可更新/删除指定 rowid 或仅删除相交消息范围；保留 `get/set_last_turn` 和 FTS 重建 | import `narrative_memory` 取类型+查询构造；base 由调用方注入不读 config（同 character_state）；局部覆盖禁止清空整库 |
| `manual_table_fill` | ✅ | 基于 `chat_snapshot` 文本历史的手动补表工作流：统计每表频率/未记录/上次回合，按所选表、最近 N 层和批次调用填表 Agent；范围重叠先返回确认，覆盖只替换相交消息范围，不覆盖则逐表跳过已处理消息 | 图片/媒体槽经 `chat_snapshot.to_prompt_history` 排除；确认前不得调用模型或改数据；进度按 repo_id 落 `table_progress.json` |
| `roleplay_agency` | ✅ | 能动性子图编排。World 每轮从在场 NPC core 推导「长期目标→阶段目标→本轮动作」，提案 intent/goal 穿过裁判进入主叙事；多角色按各自 `角色名·好感度` 仲裁。**Recall 是零 LLM 检索接缝**，候选与 GrayWill、世界书、历史和本轮 user 合成一次主 Roleplay。Chronicle 保留周期抽取；Curator 默认 `gate=1` 写 RAG，并受控增改当前小仓库世界书快照，`gate=0` 可显式关闭 | import 接缝模块+llm，**不 import agent_graph**；Recall 不得恢复成独立生成；World/Curator 使用当前对话模型的独立代理选择 |

> 当前默认调用链：**有卡纯文本零 LLM 直达 → World 对在场 NPC 做目标/行动提案并由规则裁判 → 小仓库世界书快照/RAG/Chronicle 候选机械组装（当前人物相关的最近 10 条概览）→ GrayWill + 状态/只读表格 + 可见历史 + 本轮 user + 已裁定 NPC 行动一次主 Roleplay（自动插画开启时同轮生成独立达标的 `<content>` 与隐藏高潮计划/完整 Profile）→ 隐藏块按正文正则还原、剥离、校验，失败则本地事实编译 → 快速状态写回 → 立即发正文与锚点插画 → 后台提交 ComfyUI → 独立表格维护 + Chronicle（每 3 轮，丰富纪要）+ Curator（默认开，写 RAG 并完善小仓库世界书）**。表格维护只返回 JSON 数组并写数据库/Trace，不进入对话；主计划缺失时写回接缝仍从可见正文选取最强视觉段，并纯规则恢复锚点、角色、画幅与 Profile 后发请求。GrayWill 可继续使用 `{{lastUserMessage}}` 防正则误删；Claude 发送边界会与末轮真实 user 去重。

> **代理合同**：保留一个全局代理地址。每个对话/生图/视频/远程嵌入模型有 `proxyMode=on|off|inherit`，旧配置与新模型默认 `on`；非本地端点中，`on` 使用全局地址，`off` 直连，`inherit` 跟随全局开关。localhost/127.0.0.1/::1 回环端点始终直连，前端不得透传代理，后端 Adapter 必须再次忽略显式代理。前端解析为 `chat_proxy_url/gen_proxy_url/video_proxy_url/embed_proxy_url`，后端按模型类型隔离；`proxy_url` 仍只表示联网搜索代理。

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

> **异步出图闭环（多元数据插入）**：主 Roleplay 在一次响应中先完成独立满足篇幅的 `<content>`，再在隐藏 `<illustration>` 写视觉高潮的原文锚点、镜头、构图、主体权重、画幅、艺术决策与当前 Profile 完整成稿。隐藏块不显示、不进入正文长度；显式 Roleplay `maxTokens` 会追加 800–1000 token 的 Profile 预算。隐藏 JSON 在解析前复用正文 `AI_OUTPUT` 正则，成稿随后走 `IMAGE_PROMPT` 清洗与四 Profile 格式/视觉事实门禁；`I won't generate/create/...` 等整段拒答、漏块、坏 JSON 或错误格式均从同轮 `scene_spec` 确定性降级，禁止提交拒答或触发前端第二次文本调用。四 Profile 最终均为纯英文；Anima 最终严格两行，负面词只写独立 `negative_prompt`。Profile 完成后才查询实际 LoRA 元数据并机械注入精确触发词/作者质量词去重。世界书模式人物只来自角色 LoRA 绑定名；计划缺失且正文只有代词时，从状态快照 `[在场]` 恢复角色，仍无角色才允许回退风格 LoRA。计划缺失时画幅按人数、特写与横纵动作纯规则选择；多人物 encounter 固定 `4:3`。采样参数保留工作流原值；刷新恢复时没有 `prompt_id` 的预提交槽必须移除并记录 `resume_unsubmitted`。

多元数据插入顶部的“角色外貌来源”是互斥合同：`条目模式(worldbook)` 只读取当前小仓库世界书中本轮命中的 `角色卡·` 视觉条目，并允许按角色配置 LoRA/底图与兜底图片；`角色卡模式(character_card)` 只读取当前小仓库绑定角色卡的 `description`，按本轮出现卡名筛选，未点名时回退开场卡，同时禁止消费角色 LoRA、角色底图和兜底图片，旧预设残留也必须忽略，仅保留可选全局风格 LoRA。新请求不得在两种来源间隐式降级；缺字段的旧调用保留旧兼容回退。LoRA 选择器以 `/loras/available` 的磁盘扫描为真源；请求加载、瞬时失败或后端重启期间必须继续显示并保留作品已保存的绑定，自动重试后仍失败才展示错误与手动重试，禁止把“列表暂不可用”伪装成“无角色 LoRA”。

自动插画 LoRA 采用三态合同，旧预设缺字段时迁移为 `single`：`none` 只消费角色/风格底图，提交前把工作流内标准 LoRA 加载器权重归零；`single` 只在 `illustrate_request.actors` 中按顺序找首个已配置角色 LoRA，找不到才使用风格 LoRA，角色命中时严禁注入风格 LoRA 及其触发词；`multi` 固定先加载默认风格 LoRA，再按在场角色顺序叠加每个角色 LoRA，完整文件名去重。冷倾雪＋虞妙玥的多角色栈必须包含 Ogipote 默认风格、柳世熙与蔡秀晶。角色配置全集只用于校验主模型输出和从高潮正文精确补漏，禁止整体并入 `actors`；即使主模型已给出一名合法角色，也必须继续补齐高潮正文或本轮外貌资料中明确出现的其余已配置角色。主 Roleplay 的 `<illustration>.subjects` 必须列出高潮画面中每一名实际可见角色并使用配置中的精确名称；后端再以已配置名称和高潮正文交叉校验。`<status>` 整体仍是不透明显示快照，但角色补漏必须确定性读取其中精确的 `[在场]` 单行；不能只读 `character_state.叙事[在场]`，因为真实预设通常只维护快照而不生成同名叙事 delta。相关回归必须走真实 `extract_status_snapshot → writeback → load_state`，禁止 mock `_narr` 伪造在场人物。`illustration.request` Trace 必须同时记录 `actor_candidates/status_actors/actors`，便于区分配置丢失、状态恢复失败和后续协议丢失。多人 LoRA 不新增 Agent 调用：`workflow_injector` 复用模板已标注的 `LoraLoaderModelOnly` 或 `LoraLoader` 为链首，动态复制后续加载器，串联 MODEL（完整加载器同时串联 CLIP），并把原下游统一重接到链尾。重生成快照必须保存 LoRA 栈和模式。Anima 使用风格 LoRA 时，内容行还必须机械加入手绘二次元媒介锁定词，避免只有触发词但基础模型仍落入真人域。

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
7. **异步媒体**：Agent 从固定比例集合选画幅；计划缺失时由纯规则按人物数量、特写与横纵动作选择，不固定单一比例。用户只选 Latent 最长边，工作流保有采样参数。SSE 在锚点处建立稳定槽，ComfyUI 与正文及记忆维护并行；最终持久化 output 按 `messageId + slotId` 原位回填，重新生图覆盖原图，失败只写 Trace。
8. **故障边界**：ComfyUI `execution_error`、提交失败和轮询失败都是终态，不能永久 pending 或污染对话。模型 `/models` 成功不代表推理健康；502 必须以最小推理和跨模型对照区分项目故障与供应商线路故障。

完整变更索引见 `docs/memory/stabilization-baseline-2026-08-05.md`。

禁止反向：`agency` 不知道存储；`scene_illustration` 不拥有出图；renderer 不拥有触发；角色卡直达只处理纯文本和强执行命令，带附件/模糊工具请求仍归 Supervisor。

### 插件化三插槽（复用既有 store，新增=注册项不改编排）

- **renderer**（图像格式）：ComfyUI workflow / gpt-image / 其它。ComfyUI 工作流就是一种 renderer = 你要的「节点管理」。
- **skills**（可下载 SKILLS 库）：`skills_store` 已在，提炼提示词/风格/角色行为片段做成可下载包。
- **MCP**（外部工具）：`mcp_store` / `tool_agent_node` 已在。

## 明确不做的（别反复提议）

- 3 个空 router（runs/loras/assets）+ `list_ai` 桩：已挂载=在用端点，删了缩 API 面。（`characters` 已从空桩转为角色卡导入/列表/删除/导出真实端点。）
- 角色卡「一张卡=一个文件夹」的落盘格式（`character_store` 布局）是单一属主：卡本体、内嵌世界书、正则、原图、对话记录同处一个文件夹。Phase 2 世界书检索、Phase 3 正则/提示词都从这里读，别在别处另立卡的存储。
- 偏置预设注入已升级为**多消息通道**：`preset_store.assemble_messages` 按各片段自身 role 组装多条消息（`_llm.chat_messages` 收消息数组发模型），`chatHistory` marker 处原位插历史（还原 ST 深度注入语义）。`assemble_system` 单串档保留作降级。**思维链**（`thinking_chains`）经 `select_chains` 按真状态 scene/affinity/turn 选，尾部链作独立 system 落历史后·本轮 user 前（离生成点最近，遵守最严）——这比 ST 的字符串宏变量更准（真状态判断，非文本插值）。改注入结构 → 这里，别退回单 system 串。
- Claude 兼容中转的消息合同由 `llm.prepare_messages` 单独拥有：发送前把所有 system 按原顺序合并、连续 user/assistant 合并为严格交替，并仅对“倒数包装 user 已含末轮真实 user”的明确形态去重；非 Claude 消息结构不变。Roleplay Trace 记录该规范化后的实际发送结构。
- 当前小仓库世界书快照落在 `<output_dir>/<repo_id>/worldbook.json`：首次从卡快照/绑定独立书复制并合并，之后读取和 Curator 写回都只作用于该文件，不回写源卡或独立世界书，也不串到兄弟小仓库。主 Roleplay 的本轮实际注入 index 会进入 Trace，并成为 Curator 唯一可见、可更新集合；未注入 index 即使模型提交也由服务端拒绝并追踪。角色长期进展写入唯一动态区，基础设定不被覆盖；机制类默认只读；即时外观状态仍归状态表。`worldbook_add` 不受允许集合影响，禁止自动删除。
- 正则「后端跑存储/发送档 + 前端跑显示档」是两个 runtime 的同一逻辑（`regex_engine.py` / `lib/regexEngine.ts`）：显示层 markdownOnly 必须在前端渲染时跑（原生 JS 正则、不落库），改存储/发送在后端。别强行合并成一处。显示档来源按 GLOBAL→PRESET→CARD 合并：全局(`/regex/`)+当前激活预设(`/preset/regex`)+当前卡(`/characters/regex`)；编辑视图仍显示原始存储文本，不应应用 markdownOnly。
- 卡即作品=「大仓库(卡名) + 子仓库(对话记录)」两层（`repos.addCardWork` 返回 `{parentId,childId}`）：对话线挂**子仓库**(`repo.parentId` 有值)，资产库下钻双击子仓库进对话、能返回。别把卡建成顶层单仓库(会导致下钻进空子列表无法回对话)。
- 检查点/分支是**纯前端**：检查点=`localStorage laf_ckpt_<threadId>` 存到某条为止的消息切片、可回滚(不新建仓库)；分支=`repos.addBranch` 在同大仓库下建兄弟子仓库、拷贝消息切片进新线(localStorage+后端快照)后跳转。都不新增后端端点。
- 仓库三样绑定(卡/独立世界书/人设)是**前端存字段 `cardName`/`worldbookName`/`personaId` + 后端解析**：`resolveBinding` 自身优先缺则继承父仓库（子继承父，非合并），`App.activeWork` 把解析结果合并进 repo(id 不变)。绑定只保证「该给的资料都喂进上下文」，不改模型行为——别把"绑定后剧情稳"理解成模型质量保证。**世界书/人设注入只在 `roleplay_node`(需 `_has_card`)内跑**：故独立世界书/人设绑定要「同时绑了卡(自身或继承父)」才生效，单绑世界书不绑卡不进扮演流程——别在通用对话节点另接世界书注入。显式绑人设标 `persona_bound`，`_apply_work_persona` 见之不用作品快照 `persona.json` 覆盖（别去掉这个门，会让绑定失效）。
- ComfyUI 界面模式画布是**页级单例，靠 iframe `ready`→`post("load")` 一次性推工作流**：切模板必须让 iframe 重挂(key 含 `sourcePath`)才会重新 `ready` 载新图——换整张不同工作流用整帧重挂(语义=刷新重启)，别学起源项目单节点卡的"软重发 load"(那是换同图节点用的，见 [[../d--tool-ComfyUI-ComfyUI-Wrapping-paper/memory/orchestration-route-and-nodecard-race]])。画布空白另有跨标签共享 store 争用根因(看 `N:x [0]` + 关旁标签能好)，只提示不代码根治。
- 数据表渲染：通用表/纪要/角色状态统一用**卡片式**（每行或每角色一卡、字段带标签两列网格、长文本占整行、`.card-grid` 多列流式）。角色状态必须按人物聚合，把该人物的好感度、状态、来源、依据放在同卡；长状态用 textarea 完整换行，禁止退回固定列宽 HTML 表格。
- AI 消息 HTML 渲染是**有意的、消毒后**：卡内正则把 `<status>`/`<roll>` 等标签换成带内联样式的 HTML 状态卡，`ChatMessages.looksLikeHtml` 检测块标签→`sanitizeHtml`(DOMPurify，留 style、禁 script/iframe/事件)→`dangerouslySetInnerHTML`。别改回纯文本(卡的状态栏设计就废了)，也别去掉消毒(XSS)。开场白 first_mes 的 HTML 同链路。
- 删仓库连带删卡文件夹：卡即作品下删大仓库=对 `target+子仓库` 的 cardName 集合调 `deleteCharacter` 再 `deleteRepo`，否则卡残留磁盘。别只删 localStorage 记录。
- `rag_backend._norm_url` 与 `image_gen._norm_url` 同名不同义：前者归一嵌入接口，后者归一图像接口，行为不同，别合并。
- 后端 `generation_store` 与前端 `useChatSession` 两条留存管线：运行环境不同，强合并增险。
- `ai_common` 的 `build_chat_model`/`chat` 薄封装：错误映射跨 8 端点复用，是深的。
- 模型可用性以真实最小推理为准，`/models` 目录成功只证明连接、鉴权和目录接口。剧情出现 `502 upstream_error` 时先按 `run_trace` 定位调用阶段，再用当前模型极短请求、同家族模型、跨家族模型逐级对照；短请求也失败时禁止误归因于上下文、Claude 消息规范化、RAG、表格或插画后处理。
- 剧情插画生成的图**只存 `url + 一句 caption`，绝不回灌为图像 token 进对话历史**：否则自动配图每回合翻倍烧 token。插画 prompt 从已跟踪的 state 构建，不额外拉上下文（见「剧情能动性引擎」支柱 3）。主 Roleplay 完成并剥离控制块后，最终正文 `replace` 与 `illustrate_request` 立即发出；前端异步启动 ComfyUI，同时后端继续 Chronicle/Curator/RAG 维护，三者互不等待。
- 多元数据插入（ComfyUI 异步出图/视频）**不新加同步 renderer 分支**：后端按高潮锚点发稳定槽事件，前端后台原位回填。多人 LoRA 的唯一图变换入口是 `workflow_injector.inject_lora_stack`，不得在前端或其他服务复制节点接线逻辑。`template_store` 仅在模板恰好有一个 `EmptyLatentImage` 且没有手动 Latent 语义时自动暴露宽高；多 Latent 工作流必须人工指定。智能模态使用主 Roleplay 同轮 `subjects/actors` 与本地 `infer_motion`，别另调 LLM 判断角色或图/视频。
- 后台活动点击返回作品必须同时更新 App 的父仓库 `repoId` 与小仓库 `workId`；只写 `#/chat/<threadId>` 不会驱动当前 App 状态。导航离开不是取消，显式停止才允许 abort。
- 从资产管理下钻打开小仓库时只切作品，不得把当前 `workMode` 强制改为剧情模式；hash 必须保持为当前模式。
- 多角色状态字段用 `角色名·字段` 明确归属；无分隔符的旧字段只兼容识别“身体/精神”等类别，展示时归回同一角色卡，禁止把状态类别当作角色名。同一字段已有任一明确归属键时，无归属旧副本必须淘汰，禁止回退到作品卡名形成重复角色卡。
- 裁判（`agency`）是**纯规则引擎不是 LLM**：好感度档位阈值 + 掷骰 + core 一致性驳回。别改成「导演/裁判 Agent 每回合判断」——那与省 token 初衷冲突，且不可复现。创造力只留给「世界 Agent 生成动作」这一处。
- 角色 `core`（人设/外观/死穴/机制）**永不自动更**，只有用户能改；只有 `state`（好感度+态度/心情/所在）随剧情走 StateDelta。人为乱改若不带证据 → `source:user, 证据:空`，AI 据此识别为「设定注入」而非剧情，不强行自圆其说到卡死。别把 core 做成可自动生成（人设会崩）。
