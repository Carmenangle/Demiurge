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

## 当前状态（2026-08-10）
- 仓库 v0.1 Full RAG 便携版本；终端用户只发 Full RAG Edition（含本地 Embedding/Reranker 依赖，不含模型权重）。
- 已做大量稳定化：扮演、World/Curator、世界书/RAG、状态/纪要/七类表、后台原位插画、四类提示词 Profile、LoRA 数据、ComfyUI 与界面修复。
- Krea2 Profile 只有一份剧情高潮英文转译模板，不做场景或 SFW/NSFW 分类；按六维顺序输出单段纯英文。角色姓名只作剧情人物→外貌条目→LoRA 的本地关联键，发送 Profile 前匿名化，四 Profile 最终正文再移除原姓名；提示词必须写实际发色/发型/发饰/五官/体型和剧情当前服装，禁止用 identity lock 或“由绑定模型保持身份”的空话。条目基础穿着服从 wardrobe/正文当前状态；四 Profile 共用具体视觉事实门禁和完整兜底，兜底不得固定时段/色板覆盖剧情。详见 `krea2-prompt-profile.md`。
- 自动插画 LoRA 合同：`single` 角色命中时只加载角色 LoRA，无命中才回退风格；`multi` 固定加载默认风格并叠加全部在场角色 LoRA。冷倾雪＋虞妙玥对应 Ogipote＋柳世熙＋蔡秀晶。角色补漏必须从真实 `<status>` 快照精确读取 `[在场]`，不能假设 `character_state.叙事` 另有同名字段；回归测试不得 mock `_narr` 伪造生产状态。需要英文 tags + 英文描述和二次元媒介约束时使用 `anima_tags`；运行态设置必须从界面保存，禁止手改 `user_state.json` 后假定已生效。
- 生图提示词的原始合同是两阶段三来源：Profile 先融合「剧情高潮提炼＋角色条目稳定外貌」；完成后再查实际加载 LoRA 的元数据，把精确触发词和筛选后的作者质量建议机械前置，与成稿大小写不敏感去重。LoRA 元数据不得前移给 Profile 改写，也不得为空记录猜测触发词；查表失败必须显式失败而不是静默提交。
- ComfyUI 自动插画开启时，每个真实 Roleplay 回复在同一次主生成中先完成独立达标的可见 `<content>`，再输出隐藏高潮计划与当前 Profile 完整成稿；隐藏块不显示、不计正文篇幅。Roleplay 优先采用预设 `openai_max_tokens` 作为正文额度，其外追加 4000 token 分析/状态/骰点及 800–1000 token Profile 预算。隐藏 JSON 解析前复用正文正则，成稿校验失败从同轮 `scene_spec` 本地编译，自动链路不再补调第二个文本模型。主模型漏计划时仍以 `missing_plan_fallback` 发请求；误选低强度结尾钩子时废弃同源成稿并按纠正高潮重建。从状态 `[在场]` 恢复角色及刷新恢复合同不变。
- 2026-08-11 修复长剧情后角色稳定外貌丢失：视觉条目查询除短历史与本轮输入外，必须从最近 `<status>` 精确追加 `[在场]` 角色，避免用户只说“继续”时世界书角色条目未命中。四 Profile 因而能恢复条目已有的发色、发型、发饰、唇颊、眼部形态与目光；条目未声明的瞳色等属性不得推断。
- 2026-08-11 多元数据插入 LoRA 下拉必须区分“磁盘没有文件”和“列表请求暂不可用”：后端重启或瞬时失败时保留并显示作品现有角色/风格 LoRA 绑定，自动重试三次，仍失败再显示错误与手动重试；禁止因缺少匹配 option 把已有绑定显示成“无角色 LoRA”。
- 许可证尚未选定（源码公开≠自动授权）。
- 2026-08-10 实测审计：Structured Runtime 的统一校验与离线 Replay 已在真实 Trace 生效，但生产仍走 `legacy_text`，Replay 不是整轮重执行；generation 文本 Hybrid 已返回真实结果，VLM 描述仍为 0，Qwen3-VL 权重校验通过但视觉索引尚未建立；时序账本已接 Chronicle/API，但尚无真实数据库且未回灌主剧情。详见 `structured-assets-temporal-2026-08-10.md`。
- 2026-08-11 四种插画 Profile 共用 `protected_narrative` 防拦截输入与还原解析，拒答尾缀/整段拒答不会进入 ComfyUI；Profile 仍先融合高潮事实＋角色稳定外貌，完成后才按实际加载 LoRA 精确元数据机械注入触发词/作者质量词并去重。
- 七项联动已形成可运行纵切：Narrative CI 只诊断不改写；时序事实与本轮选中角色认知进入权威分层上下文；资产显式偏好用 Elo 重排且不改 LoRA；模型租约让 ComfyUI 抢占本进程 VLM/Reranker/Embedding；全状态快照覆盖作品文件、SQLite、仓库 Chroma，旧消息分支必须匹配同回合；Procedure Skill 必须审核、dry-run 和能力租约后执行；Smithery Skill/MCP 默认禁用。
- 反事实的“完整快照”不是旧的消息 checkpoint：完成回合按 `turn:N` 去重落全状态，媒体优先硬链接，恢复只允许空目标。历史旧回合若从未有全状态快照，机械拒绝创建状态错位分支，不能凭当前状态伪造过去。
- 四 Profile 的正常路径与兜底最终都必须纯英文；混合语言场景要保留其中具体英文高潮和角色外貌，禁止抽象成通用 identity lock。真实 Krea2 单角色任务约 80 秒完成，Comfy 历史确认柳世熙 LoRA@1.0 接入两个采样器、704×1024、无 Ogipote，产物为手绘二次元。
- 当前真实边界：Qwen3-VL 未建索引时仍只走文本资产搜索；能力租约不是操作系统容器；角色认知写入、CI 处置、反事实候选选择与 Procedure 审核仍是显式操作。2026-08-11 门禁为后端 1202、前端 335、Playwright 3、Ruff/13 条 import-linter/mypy 39 文件/硬编码/wire/生产构建全通过。
- 2026-08-11 架构性能深化：Structured Runtime 的统一调用已接入 Supervisor；Scenario v2 使用 staging 原子发布、增量哈希和失败回滚；RAG/纪要写事务下沉；前端分支编排进入独立 Runtime；依赖合同增至 17 条。最新门禁为后端 1212、前端 338、Playwright 3，全绿；综合架构 9.1。详见 `architecture-performance-2026-08-11.md`。
- 2026-08-11 自动插画提交耐久性：自动路径调用 ComfyUI 前必须原子认领 `messageId + slotId`；重复、迟到、已完成或已删除槽失败关闭，旧前端快照不得清掉同槽认领/promptId/完成结果，也不得复活用户删除。后台活动逐项显示真实任务和父仓库路径，两个浮标保持 56px。详见 `automatic-illustration-submission-and-pc-ux-2026-08-11.md`。
- 2026-08-14 Roleplay 连续两轮被 Provider 默认输出上限截在 `<content>` 第二段。当前预设 `openai_max_tokens` 是正文额度真源，其外固定追加 4000 token 分析/状态/骰点与 800–1000 token Profile 预算；未闭合 `<content>` 在状态、纪要、Curator、插画写回前失败关闭，Trace 记录最终 `max_tokens`。详见 `docs/memory/roleplay-output-budget-and-truncation-2026-08-14.md`。
- 2026-08-14 Anima 第二行禁止 `Her body:`、`Bound:`、`Position:` 等标题式冒号小段；同轮合同要求一至三句连续英文描述，最终归一器即使收到标签式小段也会机械改写为完整句子。第一行仍为质量 tags＋内容 tags 的单一逗号序列并以逗号收尾。
- 2026-08-11 剧情正文与插画槽即时交付后，表格、纪要、Curator/世界书维护必须转入 `post_turn_maintenance` 按作品串行执行；维护模型再慢也不得继续占用前台 Agent 准入，否则“正文和图片已完成却仍提示任务在跑”，并阻止对话重新生成。
- 2026-08-11 最终 Prompt 编译深化：显式 Provider Profile 决定历史后规则位置，Trace Replay 可区分本地漏注入/模型不遵从/上游拒答；世界书限最近窗口，表格读写与检索预算分离；纪要用稳定 `T1-1/T1-2` 多卡编号并召回最多 10 条角色相关概览；四 Profile 用字段账本局部补齐事实。详见 `prompt-compiler-retrieval-ledger-2026-08-11.md`。
- 2026-08-14 Full RAG 分层 Runtime 的外置 Torch 曾被 PyInstaller 冻结 Finder 混合解析，Linux/macOS 自检均在 `torch.autograd` 部分初始化失败；现由专用 MetaPath Finder 统一拥有 Torch/Transformers/SentenceTransformers/Scipy/Sklearn 的外置子模块解析，Base 显式携带外置 Torch 所需 `unittest.mock`，并保持真实冻结 Torch 自检。详见 `docs/memory/runtime-portable-release.md`。

## Agent 工作约束（AGENTS.md）
- 只做用户明确要求；先读 ARCHITECTURE.md 与 docs/memory 相关部分再开工。
- 禁止 `git reset --hard`/`git checkout --` 等破坏性操作；搜索优先 rg。
- 改代码后同步修正文档漂移；跨层任务拆成可独立验证的小步。
- 完成前重读用户要求、检查 diff、核对验收条件。
