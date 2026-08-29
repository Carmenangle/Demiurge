# 开放执行面长线落实文档（Autopilot：能力清单 × 意图计划 × 队列执行）

方向合同见 `ARCHITECTURE.md`（实施时同步对应模块行）；本文件是施工图：每一步讲清目标、改动位置、实现要点、验收标准和**预排问题**（动手前先读对应条目）。

## 定位（用户拍板 2026-08-28）

用户原话：「只要是这个工具能实现的就能配合 harness 去执行，实现用户只要想，也能通过设置的模型去实现改造这个工具，让这个工具自动去工作。」

- **不是**为每个场景预写一条管线（那仍是封闭枚举），**而是**把 Demiurge 全部能力面暴露为 agent 可编排的开放接口：用户意图 → agent（用所配模型）编译成计划文档 → 校验 → 队列机械执行 → 产物回填。
- 能力上限 = Demiurge 现有功能面（manifest 有什么，agent 才能编排什么）；agent 不得变出软件没有的功能。
- 「改造这个工具」= 指挥工具干活（操作功能、写作品态文件/配置/资产），**不含**改 Demiurge 源码（那是编辑模式六专家合同）。
- 此前讨论的 FactKind（appearance_matrix / worldbook_spec / scene_plan）退化为「常用计划配方」，不是系统边界；配方 = 固化的计划模板，agent 可用配方也可现场写新计划。

## 核心类比

Demiurge 已经在对他者做这件事：拉 ComfyUI `object_info` 得知节点能力面 → `workflowFieldBinding` 机械填充 → 队列执行。本工程是**自指版**：Demiurge 导出自己的能力清单，agent 读它来编排 Demiurge 自己。

## Harness 设计原则对照（参照 D:\Study\Harness Engineering 笔记，2026-08-29 引入）

本工程的 harness 部分按以下原则设计；各阶段的落地位置已写进对应 P0–P4 条目。

**评估三问**（验收自检）：出错时怎么自动恢复——看反馈循环是否完整；上下文满了怎么压缩——看熵管理是否到位；代码质量谁把关——看架构约束是否有效。

| Agent 典型失败模式 | 本工程的 harness 对策 |
|---|---|
| One-shotting（一步到位、上下文耗尽留半成品） | budgets.max_steps 硬上限，超限必须拆多计划；计划落盘后重放不靠回忆 |
| Premature Completion（有可见产出就宣布完成） | 计划终态判定：全部步骤显式 done/blocked/skipped 才允许 completed（P2） |
| False Completion（写完不测就标记完成） | 验收只认端到端真实路径（真实 ComfyUI 批量出图），编译成功不算完成（P2） |
| Technical Debt（忠实复制坏模式、架构漂移） | 步骤失败/循环模式经 trace 分析回写 validator 规则与注册表描述（P4），对齐 AGENTS.md「每次重复错误应固化为测试」 |

其余原则与落点：

- **约束落为确定性检查，不写提示词**：manifest `--check` 门禁、plan_validator 纯函数、capability_sandbox 租约闸门——违反即拒绝，不靠 agent 自觉。
- **稳定入口 + 按需拉取上下文**：manifest 不全量塞进 agent 上下文，按意图相关 category 注入子集（P0/P1），控制 token 熵增。
- **Doom Loop 检测**：同一步骤同参数连续失败达阈值即 blocked + 通知，禁止执行器无限重试（P2）。
- **意图违背防线**：审批卡期间计划只读，只许批准/跳过/取消，不许就地改 params（P3）；计划落盘后编译侧不得静默改写（P1）。
- **过度工程化防线**：微小意图编译出巨型计划属 scope creep，validator 按 budgets 与意图规模拦（P1）；每个机制必须有消费端，无消费端不建（P4 hash 只因提交去重而存在）。
- **工具不是越多越好**：manifest 是精心筛选的子集，agent 内部协作用的中间服务不进清单（P0）。
- **自进化闭环**：任务完成 → 抓取 trace → 分析失败模式 → 修正 harness（validator 规则/注册表描述/配方参数），对应 Hermes 的「记忆→复用→优化」循环；配方固化即 Demiurge 版的 Skill Generation（P4）。

## 四件套

```text
用户意图（自然语言）
   │  agent_graph supervisor 路由
   ▼
① Capability Manifest（能力清单）── agent 上下文注入，agent 由此知道「工具能干什么」
   │  plan_compiler 专家：structured_output 编译
   ▼
② Plan Document（计划文档，落作品文件夹，可人审/手改/重放）
   │  ③ plan_validator 纯函数校验（能力存在/参数合 schema/依赖无环/模型缺口/副作用分级）
   ▼
用户审批卡（durable/expensive 步骤需确认 → capability_sandbox.grant 租约）
   │
   ▼
④ Plan Executor（plan_tasks 队列，仿 workflow_build_tasks 租约式 FIFO）
   │  步骤 handler 调既有 services → 产物落作品文件夹 / 媒体走现有回填链
   ▼
进度 SSE + trace 审计（run_trace 全程）
```

## 路由界限：委派 vs 剧情与现有专家（先分明，再施工，保 UX）

P1 的「supervisor 识别委派意图」与现行硬规则直接冲突：`agent_graph.py` supervisor_node 里
有卡无图时只有 `_explicit_card_route` 的前缀强命令（画一张/生成视频/找灵感）会分派出去，
**其余全部文本默认掉进 roleplay 被当剧情处理**——「帮我批量出 20 张变体图」今天会被剧情
专家吞掉。界限不先定，委派入口要么抢走剧情默认（伤沉浸），要么永远进不去（功能空转）。

**三条判别轴**（supervisor 候选集、强命令表、文档三处共用同一口径）：

1. **对象轴**：操作对象是工具/资产/作品态（模板、卡、世界书、仓库、批量产物、配置）→ 委派；
   操作对象是剧情世界内的人/事 → roleplay。剧情内「她提笔画了一幅像」是剧情内容，
   由原位插画锚处理，与委派出图无关——插画锚语义不变。
2. **规模轴（最硬的一条）**：单能力单步、当场要结果 → 现有专家直调（generate/video/inspire…）；
   多步、批量、跨能力、带预算约束（N 个变体、先搜素材再生图再配文、整理全部世界书）→ plan_compiler。
   「画一张图」永远到不了 plan_compiler；「20 张变体」永远不该进 roleplay。
3. **时点轴**：进本轮对话流、原位回填 `messageId+slotId` → 现有通道；后台计划任务、
   审批后执行、不追加对话轮 → 委派。计划编译结果以**卡片形式回复**（类 clarify 卡），
   不是剧情正文，不进剧情历史。

**路由优先级（现行顺序不动，只插一层）**：

1. 编辑模式强制 edit（不变）。
2. `forced_route` 前端显式选择（不变；P4 后独立委派面板走这里直达 plan_compiler）。
3. **新增零 LLM 委派强命令层**：与 `_explicit_card_route` 同层扩展——只收高置信确定性模式
   （批量/规模词「批量、所有、每张、N 个变体、全部」+ 管理动词「导入、整理、建仓、编排、
   做个计划」+ 资产对象），命中 → plan_compiler，不调模型。词表与既有强命令同文件维护。
4. 有卡无图其余文本仍 roleplay（剧情默认不动；模糊表达按剧情处理的既有原则延续）。
5. 无卡/带图时 supervisor 候选集加 plan_compiler；低置信度 → clarify。

**误判倾向（fail-safe 决定方向）**：plan_compiler 误入是**安全失败**——只编译计划落盘+弹审批卡，
不批准不执行（P3 前），取消零成本；roleplay 误入是**脏失败**——污染剧情上下文与记忆。
所以方向定为：确定的委派信号 → 委派；模糊 → clarify 一次问清；**绝不为「可能像委派」
牺牲剧情默认**。频繁 clarify 伤体验，故零 LLM 层只收高置信模式，把 clarify 压到最少。

**验收（进 P1，先于编译功能本身）**：路由单测矩阵——有卡+「批量出 20 张变体图」→ plan_compiler；
有卡+剧情内画像请求 → roleplay（插画锚不受影响）；无卡+「整理全部世界书」→ plan_compiler；
模糊管理语 → clarify；既有路由行为逐条不变（回归）。`_explicit_card_route` 强命令表与
supervisor 候选集一致性单测。

## 与既有地基的对照（不重复造轮子）

| 既有件 | 现状 | 在本工程中的角色 |
|---|---|---|
| `capability_sandbox.py` | 短期能力租约 `grant(subject, capabilities[{operation,path,domain}], ttl, approved_by)`，93 行 | 审批闸门直接复用：批准计划 = 对 durable 步骤授一次性租约 |
| `procedure_skills.py` | `_EVENT_ACTIONS` 已有操作动词先例（illustration.generate / workflow.submit / rag.index / scenario.snapshot） | manifest 的 `operation` 动词命名沿用此风格 |
| `workflow_build_tasks.py` | 租约式 FIFO worker 完整实现（lease 60s、心跳 10s、唤醒条件变量、取消控制器、保留 7 天/200 条），332 行 | P2 执行器的模式模板（新建 `plan_tasks` 仿其骨架，不动现有任务语义） |
| `generation_approval.py` | 生成审批生命周期 + 审批卡 | P3 计划确认卡复用其模式 |
| `structured_output.py` | 统一结构化输出 Runtime（native schema → fallback → Pydantic 校验 → Trace） | plan_compiler 的编译与校验重试 |
| `mcp_client.py` / `mcp_store.py` | MCP 外部工具已接入 agent | 后续扩展：manifest 可纳入 MCP 工具条目（P4 后） |
| `agent_graph.py` | LangGraph StateGraph 多专家，supervisor 语义路由 | 意图入口：supervisor 识别「委派类意图」→ route 到 plan_compiler 专家 |
| `run_trace.py` + agent-trace.jsonl | 全链路 trace | 计划执行的审计面 |

## 能力条目 schema（manifest 单元）

```json
{
  "operation": "workflow.submit_batch",
  "category": "comfyui",
  "description": "把变体值批量注入模板并提交 ComfyUI 队列（中文，写给人看）",
  "params_schema": { "type": "object", "properties": {} },
  "needs_model": null,
  "side_effect_level": "expensive",
  "channel": "queue",
  "handler": "app.services.workflow_submission:submit_batch"
}
```

字段说明：`operation` 动词.宾语（对齐 `_EVENT_ACTIONS` 风格，全局唯一）；`category` ∈ comfyui/worldbook/character/repo/rag/media/asset；`needs_model` ∈ null/chat/image/video/audio/embed；`side_effect_level` 分级见下；`channel` ∈ sync（当场返回）/queue（进 plan_tasks）；`handler` 执行适配器「模块:函数」。

**side_effect_level 分级（默认方案，待用户确认）**：

- `readonly` 只读（列模板、查资产、读状态）→ 直跑。
- `reversible` 可逆低危（写计划文档、写草稿文件、写入临时区）→ 直跑。
- `durable` 持久变更（建仓、导入卡/世界书、改绑定、删资产）→ 需审批（批准计划时 `capability_sandbox.grant` 一次性授权）。
- `expensive` 烧 GPU/token（提交 ComfyUI、调生成模型）→ 需审批 + 每计划配额（步数/张数/秒数上限）。

## 阶段进度

| 阶段 | 状态 | 一句话 |
|------|------|--------|
| P0 | ✅ 已落地（2026-08-29） | `capability_registry.py` 注册期闸门 + `capability_handlers.py` 薄适配 + `scripts/generate_capability_manifest.py --check` 门禁；首批 4 条 comfyui 能力，manifest 生成物随源码发布 |
| P1 | ✅ 已落地（2026-08-29，真实模型端到端通过） | 路由界限先行✅（委派强命令层+路由矩阵）+ GenerationPlan 合同 + plan_validator + plan_compiler 专家 + plans/ 落盘；真实 LLM（gpt-5.6-terra@xtoken）一次编译出合法批量出图计划：读 exposed 字段→submit_batch 四变体，inputs_from 点引用链接，approval_required 正确汇总，json+md 落盘 |
| P2 | ✅ 已落地（2026-08-29） | `plan_tasks.py` 租约式 FIFO 执行器（失败隔离/Doom Loop blocked/终态判定 partial 防 premature completion/inputs_from 链式传参/main.py worker），plan_compiler 落盘后自动投递 |
| P3 | ✅ 已落地（2026-08-29） | capability_sandbox 一次性租约（ttl=预算×2，过期需重批）+ SupportWidget 计划卡批准/取消 + 配额计数器（失败尝试也计消耗） |
| P4 | ✅ 已落地（2026-08-29） | task_progress_store 进度快照 + SupportWidget 轮询面板 + 规范化 hash 幂等去重 + 配方固化/实例化 API；trace 分析闭环目前为 plan.terminal 事件留档+人工/Agent 分析 |

> P0 实施注记：manifest 为静态导出（提交时 available 恒缺省），运行时可用性由
> `capability_registry.with_availability(configured_models)` 按四类模型代理配置打
> `available` 标记注入 agent 上下文——静态生成物不随用户模型配置变化而漂移。

---

## P0 能力注册表与清单导出（最小可信产物）

**目标**：services 能力面变成机器可读清单，含首批能力，进 check 门禁。

**改哪里**：
- 新建 `backend/app/services/capability_registry.py`——单一属主：能力**显式注册**（装饰器/注册函数），不做 AST 自动扫描（描述是人写的，自动扫只产出垃圾描述）。
- 新建 `scripts/generate_capability_manifest.py`——从注册表导出 manifest JSON（随源码发布），`--check` 校验 manifest 与注册表一致（对标 `generate_wire_contracts.py` 模式）。

**实现要点**：
1. 第一批能力选「批量模板提交」路径（P2 首验）：列模板 / 读模板 exposed 字段 / 注入变体值 / 批量提交队列（operation 命名注册时定稿）。
2. `needs_model` 与四类模型三级代理配置对齐：未配置的模型在导出时打 `available:false`，agent 计划阶段即见缺口。
3. handler 只做薄适配：参数透传既有 services 函数，不藏业务。

**验收**：`--check` 门禁通过；注册表 operation 唯一性单测；manifest 首批能力 schema 字段完整。

**预排问题**：
- **清单膨胀**：只注册「计划可编排」的能力，agent 内部协作用的中间服务不进清单；按 category 分文件注册、导出合并。
- **description 质量**：中文、写清「做什么+影响什么」——这是 agent 编排准确性的第一决定因素。
- **上下文注入策略**：agent 上下文只放本次意图相关 category 的子集（如批量出图只注入 comfyui/media），全量清单落文件按需读取——工具不是越多越好，清单越长选错能力的概率越高。

## P1 计划编译与校验

**目标**：一条真实意图 → 合法计划文档落盘。

**改哪里**：
- **路由界限先行（验收先于编译功能）**：按「路由界限」一节改 `supervisor_node`——零 LLM 委派强命令层扩进 `_explicit_card_route` 同层，supervisor 候选集加 plan_compiler，跑通路由单测矩阵后才动 plan_compiler 本体。
- `structured_contracts.py` 加 `GenerationPlan` Pydantic：`{intent, repo_id, budgets{max_steps,max_gpu_tasks,max_llm_calls}, steps[{id, operation, params, inputs_from[], outputs}]}`。
- `agent_graph.py` 新专家节点 `plan_compiler`：supervisor 识别委派意图（新增路由分支）→ manifest 注入上下文 → `structured_output.invoke` 产出计划 → 校验 → 落盘。
- 新建 `backend/app/services/plan_validator.py` 纯函数：能力存在 / params 合 schema / inputs_from 引用存在且无环 / needs_model 已配 / 分级汇总审批需求 / 配额不超 / **params 中路径必须落在计划声明的作品域内**（防越权写，capability_sandbox path 精确租约的前置闸）。错误逐条中文可读。
- 落盘：`<作品>/plans/<ts>-<slug>.plan.json`（执行真源）+ 姊妹稿 `.plan.md`（人审视图，单向 json→md 渲染）。

**实现要点**：
1. 用户手改 md 不回灌（要改就改 json 或重新对话；md→json 反向 P4 后再议）。
2. 编译失败带校验错误重试一次（structured_output 现成模式），仍败如实回复「差什么」，不编造。
3. 意图含糊 → 产出 clarify 请求（复用 supervisor clarify 卡），禁止猜意图硬编。
4. trace 记 `plan.compiled` / `plan.validated`。
5. **意图违背防线**：计划落盘后编译侧不得静默改写；要改就重新编译出新版本文件，旧版本保留可追溯。
6. **防 scope creep**：微小意图编译出巨型计划（步骤数与意图复杂度明显失配）按 budgets 拦截，超限让 agent 拆多计划。

**验收**：plan_validator 各分支单测；真实意图一次编译出合法计划；非法计划（未知 operation / 缺参数 / 模型缺口 / 路径越出作品域）被拦且报错可读。

**预排问题**：
- **计划大小**：budgets.max_steps 默认 24，超限让 agent 拆多计划，禁止一步到位巨型计划。

## P2 计划执行器（端到端闭环）

**目标**：计划 → 队列 → 逐步执行 → 产物落位，全程 trace；真实跑通「N 变体批量出图」。

**改哪里**：
- 新建 `backend/app/services/plan_tasks.py`：仿 `workflow_build_tasks.py` 骨架（SQLite `plan_tasks` + `plan_task_steps` 两表、租约 FIFO、心跳、唤醒条件变量、取消控制器）。一个计划=一个任务，计划内步骤串行（有依赖），计划间并行受限，GPU 类步骤受 `model_lease` 约束。
- `main.py` 常驻 worker 注册。
- handler 分发：capability_registry 逐 operation 调用；文件产物落 `<作品>/plans/artifacts/` 或既有资产链；媒体提交走 `workflow_submission` 现有链，回填靠既有 `messageId+slotId` 机制。
- trace 记 `plan.step_started/done/failed`。

**实现要点**：
1. **步骤失败隔离**：单步失败不炸全计划——停在失败步，剩余标 blocked，可指令跳过/重试该步。
2. **Doom Loop 检测**：同一步骤同参数连续失败达阈值（默认 2，对齐 AGENTS.md「同一方案连续失败两次」）→ 步骤 blocked + 通知；执行器不得自动无限重试，重试必须由用户指令或修正后的新计划触发。
3. **终态判定防 premature completion**：全部步骤显式 done/blocked/skipped 才允许计划标 completed；部分有产出但仍有 pending 步骤时只能标 partial，进度面板如实显示，不得把可见产物当整体完成。
4. **执行器内不再调 LLM**（编译在 P1 完成）；expensive 步骤自身的模型消耗（如生图）属能力语义，按能力走。
5. 步骤间数据传递：inputs_from 按上一步 outputs 声明键传递 + artifacts 落盘引用；禁止隐式全局态。

**验收**：真实 ComfyUI 跑通 N 变体批量出图计划；杀进程后 lease 过期自动恢复；trace 完整可审计；同参数连续失败 2 次触发 blocked 而非死循环（单测）。

**预排问题**：
- **与 workflow_build_tasks 并存**：不改名不合并（现有 AI 搭工作流语义独立）；若后续发现骨架 90% 重复再评估泛化抽取，届时同步 `ARCHITECTURE.md`。

## P3 审批闸门与配额

**目标**：durable/expensive 步骤未经批准不执行；批准 = `capability_sandbox.grant` 一次性租约。

**改哪里**：
- `capability_sandbox.py` 复用：计划批准时 `grant(subject=plan_id, capabilities=[各 durable/expensive 步骤], ttl=预算估算×2)`。
- 前端计划确认卡（复用 `generation_approval` 审批卡模式）：列步骤/影响面/预算/模型缺口；批准→执行+授权；可编辑后批准（跳过步骤）。
- **审批只读（防意图违背）**：审批卡期间计划文档只读——只许批准 / 跳过步骤 / 取消，不允许就地改 params；要改就拒绝并回到 P1 重新编译。
- 配额执行：worker 执行 expensive 步骤前查预算计数器，超限 → 步骤 blocked + 通知。

**验收**：durable 步骤无租约被拒（单测）；租约过期再执行被拒；配额超限停步。

**预排问题**：
- **租约 TTL 与长计划**：批量出图可能跑数小时——ttl 按预算留余量；过期未跑完的 expensive 步骤需重新批准（安全优先）。
- **审批疲劳**：readonly/reversible 已直跑；durable 按计划一次授权，不逐步弹卡。

## P4 进度、幂等与配方

**目标**：长任务可视、可中断、可恢复；常用计划固化为配方。

**改哪里**：
- 计划进度 SSE + 前端面板（复用 `task_progress_store` / 后台活动面板模式）。
- 幂等：计划内容 hash → 提交时查同 hash 已执行/执行中 → 拒绝重复并提示复用结果。
- 配方：执行成功且用户标记常用的计划 → 参数槽位化存为配方；agent 后续优先套配方只填参数。
- **trace 分析闭环**：计划终态（成功或失败）后从 run_trace 抓取该计划全程 trace，分析失败模式与循环模式，固化为 validator 新规则、注册表 description 修正或配方参数——「任务完成→抓取 Trace→分析→修改 Harness」循环，对应 Hermes 自进化闭环与 AGENTS.md「每次重复错误应固化为测试」。

**验收**：刷新后计划进度恢复；同 hash 重复提交被拦；一条配方从固化到复用跑通；一次真实失败计划的 trace 能产出到位的 validator/描述修正并留档。

**预排问题**：
- **hash 必须有消费端且语义稳定**：hash 只为提交去重而存在（无消费端不建）；对规范化序列化（键序固定、时间戳剔除）后的计划语义内容计算，避免等价计划因 JSON 键序或落盘时间戳不同而漏判/误判。

---

## 拍板项（默认方案已写入上文，实施前确认）

1. manifest 生成：**显式注册表+构建时校验**（默认✅）vs AST 自动扫描（否决：描述质量不可控）。
2. side_effect_level 四级划分与放行策略（上文默认方案）。
3. 入口：P1 起对话内直接说（supervisor 路由）；独立委派面板 P4 后可选。
4. 计划文档归属：作品文件夹 `plans/`（随快照走，默认✅）。
5. 路由界限：三轴判别（对象/规模/时点）+ 零 LLM 委派强命令层 + 误判方向「模糊不改剧情默认」（上文默认✅）。

## 红线对照（既有架构合同，违反即打回）

- agent 只改作品态；改 Demiurge 源码不在执行面（编辑模式六专家合同）。
- 计划执行全程后台，不阻塞对话通道（generation-channel-isolation）。
- 媒体产物 `messageId+slotId` 原位回填，不追加对话轮。
- 卡/世界书生成物进源库走既有导入链，作品用快照，改源不回灌（card-source-work-decouple）。
- 每计划必须带 budgets；无预算计划校验不通过。
- manifest `--check` 进门禁，防清单与注册表漂移；trace 全程审计。
- 执行面无任意 shell / MCP 兜底：handler 只能是注册表里的既有 services 函数（对齐 procedure_skills 合同），未知动作拒绝而不是降级执行。
- 失败与循环必须固化为 harness 修正（validator 规则/注册表描述/测试），不允许「再试一次就好」文化；同一方案连续失败两次即停止小修重查假设。

## 与多模态路线的关系

`ROADMAP-MULTIMODAL.md` 三链（图/视频/音频）是单回合被动提取；本工程的 scene_plan 类计划是**主动规划消费方**——三链能力注册进 manifest（P2 扩展批）后，agent 一次计划即可编排多幕×多资产。story_facts 事实先行（待办 P1）按配方形态融入：story_facts 是计划的事实来源文档，落地时直接按本文档设计，不另立管线。
