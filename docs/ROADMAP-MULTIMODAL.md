# 多模态创作长线落实文档（剧情×图×视频×联网灵感）

方向合同见 `ARCHITECTURE.md`「多模态创作路线」章节（红线以彼处为准）；本文件是施工图：每一步讲清目标、改动位置、实现要点、验收标准和**预排问题**（动手前先读对应条目，别踩了再修）。

每步完成即勾选进度表并同步 `ARCHITECTURE.md` 对应模块行；测试门禁照 AGENTS.md（后端 Ruff/mypy/import-linter/pytest，前端定向测试+build，动 wire 协议必须跑 `check:wire` 与双端测试）。

## 阶段依赖与进度

```text
V1.1 首帧底图注入 ──→ V1.2 视频最小事实合同 ──→ V1.3 视频槽全链路验证 ──→ (V1.4 云端图生视频,可选)
M1.1 搜索源Adapter ──→ M1.2 图片搜索预览 ──→ M1.3 受控下载入库 ──→ M1.5 素材卡联动创作
                                              └──→ M1.4 灵感卡资产库化 ──┘
V1.3 + M1.3 ──→ M2.1 derived_from 元数据 ──→ M2.2 资产库多模态筛选
A1.1 三开关重构 ──→ A1.2 音轨配置 ──→ A1.3 音频语义绑定 ──→ A1.4 台词筛分+情感 ──→ A1.5 逐角色生成+排序合并 ──→ A1.6 楼层聚合展示
(V1.5 视频首尾帧参考图 / V1.6 视频独特提示词方法：后续目标，见 V1 章节尾部)
```

| 步骤 | 状态 | 一句话 |
|------|------|--------|
| V1.1 | ✅ 已完成 | 视频模板首帧底图注入（图生视频本地链路） |
| V1.2 | ✅ 已完成 | 视频 scene_spec 最小事实合同（零新增 LLM） |
| V1.3 | ✅ 已完成 | 视频槽 SSE/快照/恢复/瘦身全链路验证 |
| V1.4 | ✅ 已完成 | 云端视频端点通用化 + 首帧图生视频（参考图参数） |
| V1.5 | ✅ 已完成 | 首尾帧生图独立模式 + 首帧复用两级判断 + 转场视频任务编排（P6 真实 API 对齐顺延，待可实测端点） |
| V1.6 | ✅ 已完成 | 视频独特提示词方法（climax 精简动作延伸 + firstlast 七段式 + action_sequence 动作序列） |
| M1.1 | ✅ 已完成 | 搜索源 Adapter 注册表 |
| M1.2 | ✅ 已完成 | 图片素材搜索与预览 |
| M1.3 | ✅ 已完成 | 受控下载入资产库（候选校验/域名白名单/provenance/魔数/SSRF/原子写全链；后台任务化列后续） |
| M1.4 | ✅ 已完成 | 灵感卡资产库化（归入上网素材：封面=图1:1或文本预览，双击看文本内容，批量+发送对话框/画布，删图留文本） |
| M1.5 | ✅ 已完成 | 灵感卡→输入框联动创作（插对话/画布，图文拆分 + 编辑回填还原） |
| M2.1 | ✅ 已完成 | derived_from 派生元数据（视频入库记首帧底图槽弱引用，资产库只读展示） |
| M2.2 | ✅ 已完成 | 资产库多模态筛选（图/视频）与视频资产展示（占位封面 + 详情播放） |
| A1.1 | ✅ 已完成 | 多元数据插入三开关重构（图片/视频/音频分区显隐） |
| A1.2 | ✅ 已完成 | 按角色参考音轨配置（characterVoices） |
| A1.3 | ✅ 已完成 | 音频语义绑定（voice_text/voice_reference/voice_emotion_<key>） |
| A1.4 | ✅ 已完成 | 台词筛分 + 8 维情感向量（同轮内嵌 audio 块 + 降级） |
| A1.5 | ✅ 已完成 | 逐角色独立生成 + 按台词顺序排序；「合并」一期按 2026-08-24 决策做分段聚合展示（ffmpeg 混音列为后续） |
| A1.6 | ✅ 已完成 | 产出聚合到剧情楼层：对话按角色分条音频气泡 + 画布楼层逐条播放器（含生成中占位进度与刷新恢复） |

V1 与 M1 前半（M1.1–M1.3）互不依赖，可并行推进。

---

## V1 视频联动深化（剧情→图→视频）

### V1.1 视频模板首帧底图注入

**目标**：smartVideo 切到视频模板时，把已完成的插画作为首帧底图注入视频模板的图像输入节点，角色一致性由底图锁定。

**改哪里**：
- `frontend/src/lib/useChatSession.ts` `submitIllustration`——视频分支增加底图解析与注入。
- `frontend/src/lib/chatGeneration.ts`——复用 `needsImageInput`/`hasImageProvided` 图像门（已存在，服务 `/s` 图生图拦截），为视频模板增加底图来源解析纯函数（可单测）。
- `backend/app/services/comfyui_client.py`——上传底图到 ComfyUI input 目录的通道已存在，只做复用确认。

**实现要点**：
1. 底图来源优先级：**本回合同槽已完成插画 > 最近一次已完成插画 > 用户手动指定 > 模板未声明图像口时纯文生视频**。优先级解析写成纯函数放 `chatGeneration.ts`。
2. 视频模板声明图像输入口（`image_node_id` 或 exposed `control=image`）且解析不到底图时，按现有图像门语义拦截并失败关闭，不得空图提交图生视频工作流。
3. 注入走模板 exposed 隐藏 binding 的 image 字段（提交 key 用原工作流字段名，同图片模板合同）；底图先上传 ComfyUI 取回文件名再填值。
4. LoRA 注入对视频模板**不自动接线**：`inject_lora_stack` 只认标准 loader，视频工作流 loader 结构各异；视频模板的 LoRA 由模板自身暴露字段配置，不复制节点接线逻辑（红线：唯一图变换入口合同）。

**验收**：纯函数单测（来源优先级/无底图拦截）；真实 ComfyUI 提交一次带首帧的视频模板并确认节点收到图；无底图时按模板声明拦截。

**预排问题**：
- **时序**：插画还在生成、视频就要提交 → 首版合同：底图只取**已完成**的插画（同槽 pending 不等待），取不到就按模板声明拦截或降级文生视频（模板无图像口）。禁止为等底图阻塞对话通道。
- **尺寸不匹配**：底图比例与视频 Latent 不一致 → 由视频工作流内部的 resize/裁剪节点负责，注入只传图不改尺寸；模板文档注明要求。
- **上传失败**：底图上传 ComfyUI 失败是终态，失败槽按现有 `discardFailedIllustration` 路径走，不重试乘法。

### V1.2 视频 scene_spec 最小事实合同

**目标**：视频提交携带最小事实（动作意图=motion、时长、镜头运动），全部来自已有同轮数据与预设，**零新增 LLM 调用**。

**改哪里**：
- `backend/app/services/image_prompt_extract.py`——隐藏 `<illustration>` 块已有 `motion` 字段，只做透传确认。
- `backend/app/services/scene_illustration.py`——`infer_motion` 已有；镜头运动/时长**不从模型读**，由预设与模板 binding 配置。
- wire 协议：`chat_stream_protocol` 的 `illustrate_request` 若需新字段（如 `duration_hint`），按双端合同五处同步（协议、前端解码、reducer、schema、双端测试）。

**实现要点**：
1. `motion`（0–3）继续作为动作强度事实；镜头运动（pan/zoom/static）与时长（秒）是**用户预设值**，映射到视频模板 exposed 字段（隐藏 binding）。
2. 禁止从模型输出读 camera 字段——模型自由发挥的镜头描述与视频模板参数体系不兼容，会污染提交。
3. 红线对照：不建四 Profile 级字段账本，视频事实就三样——首帧底图（V1.1）、motion、预设参数。

**验收**：wire 字段双端测试；提交 payload 中 motion/时长/镜头值来源于预设的 Trace 核对。

**预排问题**：
- **协议膨胀诱惑**：想给视频加主体权重、构图、色板等字段 → 拒绝；视频模型能力有限，账本过严只会产生重试失败（排雷红线 2）。
- **旧预设兼容**：未配置视频参数的旧预设 → 全部走默认值（模板原值），不迁移不报错。

### V1.3 视频槽全链路验证

**目标**：视频原位回填、刷新恢复、快照瘦身、删除、重新生成全路径有真实测试覆盖。

**改哪里**：
- `backend/tests/`（`chat_snapshot` 视频路径补测）+ `frontend` Playwright E2E（媒体槽视频形态）。
- `frontend/src/lib/useChatSession.ts` 快照瘦身与 `workflowGenerationRuntime` 超时——排查图片假设。

**实现要点**：
1. `resolve_media_slot` 已支持视频；逐条核对瘦身（体积假设）、持久化（mime/扩展名白名单）、恢复（pending 轮询）路径里是否有 `image` 硬编码。
2. 视频超时上限独立于图片：视频生成普遍数分钟到十分钟级，`workflowGenerationRuntime` 的超时合同对视频槽放宽（可配置），禁止 5 分钟掐掉 10 分钟任务。
3. E2E 至少覆盖：视频槽原位回填、刷新后 pending 恢复、目标消息删除后结果丢弃。

**验收**：新增测试全绿；真实 ComfyUI 视频模板一次完整回合（提交→后台→回填→刷新恢复）。

**预排问题**：
- **快照体积**：视频 url+caption 进会话，但本地缓存/下载的视频文件按红线不进回合快照（外置资产库+引用）——本步只验证引用路径正确。
- **后台活动面板**：视频 pending 的进度显示复用现有徽记；确认面板不因媒体类型是视频而漏显示。

### V1.4 云端图生视频对齐

**目标**：`video_gen.py` 支持参考图参数——**仅当 Provider 明确提供该能力**。

> **进度（2026-08-25）**：已落地。真实接口形态 `<站点根>/v2/videos/generations`（v2 + 复数）确认后：
> - **URL 由用户决定，代码不猜**：`_norm_url`/`_norm_task_url` 原样使用用户填的地址（不拼版本/单复数），
>   适用于任意 OpenAI 兼容站（t8star 的 /v2/videos/generations、seedance 的 /v1 根形态等）；报错时提示填完整接口地址。
> - **发送参数参照图像模型**：文生视频 `generate` → JSON payload（{model, prompt, size}）；
>   图生视频 `generate_with_images` → **multipart/form-data，image[] 同名多图**（复用 `image_gen.load_image_bytes`
>   读图上传，与图生图完全一致，不猜字段名）。Agent 视频工具接入用户消息图片（`video_node` 取 `state["images"]`），
>   有图 → 图生视频，无图 → 文生视频。
> - **首尾帧未做**：尾帧来源（插画/用户指定/剧情目标帧）与字段名未定，并入 V1.5 一并设计。

**预排问题**：
- **猜接口是大坑**：OpenAI 兼容 video/generations 各家字段不一，现 `_pick_video_url` 已在做兼容妥协；参考图字段名没有事实前不对齐（红线：`/models` 成功≠推理可用的同类教训——目录在≠能力在）。
- **字段名差异**：`image` 是常见形态但非唯一；实测若 Provider 要求 `first_frame_image`/`init_image` 等，改 `generate` payload 一处键名。

### V1.5 首尾帧生图 + 首尾帧视频（分层定调 2026-08-26 修正）

> **进度（2026-08-26）**：已落地。首尾帧双锚点提取（`story_frames.extract_story_frames`）、
> 首帧复用两级判断（L0 场景连续性 `judge_frame_reuse` + L1 `<transition>` 搭车，`merge_frame_reuse`
> 合并）、转场视频任务编排（`build_video_request(mode="transition")` + 前端 2 任务排队）、
> 尾帧链式反查（`resolvePrevTailDesc`，零新增持久化）、`videoMode` 二选一协议透传均已实现并测试覆盖。
> 剩余：P6 真实 API 对齐（双图 `image[]` 语义实测）待用户提供可实测端点。详见
> `docs/PLAN-VIDEO-FIRSTLAST.md`。
>
> **进度（2026-08-28 用户拍板 + 修复）**：用户报告选首尾帧模式实际仍出高潮格式——根因是首尾帧
> 生图编排绑死 `useVideo`（未配视频模板时选项完全失效；trace 实证 383 条 illustration.request
> 无一 firstlast、最新 video_prompt_chars=0）。拍板：**首尾帧是独立图片模式，视频模式跟随该选项
> 推导**。修复（`useChatSession.submitIllustration` + `illustrationMedia.firstlastSlotLayout`
> + `chatSessionEvents.appendImageSlot`）：只配图片模板即生效——`useFirstlastImages` 解耦
> `useVideo`，双帧图经 pollResult 走既有回填+入库全链；楼层主槽=本楼层新画面（regenerate=首帧、
> reuse=尾帧/画面延续），尾帧新图进 `:last` 副槽；视频开启时双帧图走 `:first`/`:last` 副槽
> （主槽留正片，避免同槽双 pollResult 竞态）；独立图片模式跳过高潮 Profile 渲染与高潮主图提交。
>
> **进度（2026-08-28 验收反馈三修）**：①帧图 LoRA 未生效——帧提示词缺触发词前置，LoRA 查表/
> 校验/前置重构为 `withLoraTriggers` 公共路径（主图与帧图共用，查表失败仍硬失败）；②取段位置
> 无需改——`extract_story_frames` 本就取首段/末段（纯对白就近借位），「情节对不上图」是 ③ 的症状；
> ③帧提示词「瞎写」——帧描述原文直喂生图模型，首版 `compileFramePrompt` 降级明显。
>
> **进度（2026-08-29 帧提示词同构重构，用户拍板「用高潮点那一套方案」）**：高潮点的 action/
> visual_facts 等结构化画面事实是主生成同轮提取的产物；首尾帧没有同轮载体，帧描述只有叙事句、
> 缺帧时点结构化字段 → 渲染校验（field_ledger/primary_focus/visual_hook）不过 → deterministic_fallback
> 兜底垃圾。重构为与高潮点**完全同构**：新增后端 `/ai/prompt/profile/frames` 端点——
> `generate_frame_prompts` 先做一次「时点提取」（LLM 从楼层首段/尾段分别提取该时点英文结构化
> action/visual_facts，帧描述走 `@(…)@` 防拦截标记保护，解析失败带因重试一次），再逐帧走**同一
> `generate` 编译器**（同一校验/带因重写/deterministic_fallback 兜底/field_ledger），一次调用出两帧
> 成品。前端 `genFramePrompts` 一次取两帧缓存，`compileFramePrompt` 退化为缓存查询+触发词前置
> （`withLoraTriggers`），编译失败降级帧描述原文不挂死。trace `illustration.profile` 带 frame 标记。
> 回归测试：提取成功/提取失败重试降级/单帧三例。
>
> **进度（2026-08-29 验收三修：首帧位置 / think 污染 / LoRA 触发词复发）**：trace 实证三案——
> ①首帧图落中央/末尾：firstlast 主槽沿用了主图的「高潮纠偏/末段兜底」锚点，新增
> `scene_illustration.first_frame_anchor_offset`（正文第一段末），hook 对 video_mode=firstlast 特判不走高潮纠偏；
> ②编译产物整段 `<think>` 提交 ComfyUI、提取 JSON 混 think 解析必败→尾帧降级中文原文：
> `image_prompt_profiles.generate` 两轮输出与 `_parse_frame_extract` 入口统一剥 think（对齐
> image_prompt_extract 正则）；③角色 LoRA「没生效」：触发词表空 manual 记录（登记页空保存=确认通用）
> → 注入静默跳过——保留既有语义，改为绑定 UI 角色行醒目警告（未登记触发词·画面可能不像角色），
> 新仓库绑定当场可见，不再等生图后发现。
>
> **进度（2026-08-29 提交阶段卡死自愈，验收「新对话没有触发生图」）**：trace 实证——后端
> illustration.request 已 emitted、前端帧编译已完成，但 ComfyUI history 无对应任务且无
> illustration.submitted：提交请求静默挂起（submitWorkflow 无超时），直到用户刷新页面触发
> 孤儿清理。修复：submitWorkflow/submitGraph 加 60s 超时（后端 urlopen 30s + 模型卸载余量）；
> 帧循环提交失败自动中断残留任务并重提一次（对齐 stalled 自愈，上限 1 次）；再失败进失败槽
> （可见+可重新生成）。卡死自愈从此覆盖「提交」与「轮询」两个阶段。
>
> **进度（2026-08-29 二轮验收：响应丢失救回 + 思考折叠保态）**：①重发对话后 ComfyUI 已出图
> 但对话无回填——后端 submit_prompt 的 urlopen 超时抛错时**任务实际已被 ComfyUI 接收**（响应
> 在返回 prompt_id 前被掐断），前端放弃了一个正在生成的任务。修复：客户端预生成 prompt_id
> 随请求提交，超时后查 history+queue 确认任务已接收则**救回** prompt_id；②对话/画布正文里
> 预设正则折叠出的 `<details>思考过程` 在任何消息更新（插画回填/流式/状态块）重写 innerHTML
> 后都会回到默认收起——新增 lib/stableDetailsOpen（纯逻辑+stub DOM 回归测试）与
> useStableDetailsOpen，details 的 toggle 经 capture 原生委托记录展开键，重渲染后恢复。
>
> **进度（2026-08-29 队列卡死自愈 + 失败槽重新生成，用户需求）**：①同步轮询 `pollWorkflowResult`
> 补停顿守卫（对齐 workflowRuntime 5 分钟 stall 窗口，`seenRunning` 后不误杀；哨兵用 -1——
> 0 是合法时间值会让第二段 pending 守卫失效，测试抓出）→ 返回 `kind:"stalled"`；调用侧
> （帧循环/画布工具卡/CanvasStageFlow）stalled 时 `interruptComfy`（清队列+中断+释放租约）
> **自动重新提交一次**（上限 1 次防循环），再卡死才失败。②插画失败/手动停止**不再删槽**——
> `markMediaSlotFailed` 保留槽为 failed 态（错误原因+`retryArgs` 参数快照），楼层显示
> 「重新生成」按钮 → `retryIllustration` 从快照重调 `submitIllustration`（source 翻转为 manual
> 跳过 claim，提交/入库/trace 三者同一编译后提示词源）。

**架构分层（图片层 / 视频层）**：
- **图片层两个模式**：
  - **高潮片段生图**（已完善）——高潮锚点 → 1 张高潮动作图。
  - **首尾帧生图**（本步完善）——楼层首尾双锚点 → 首帧图 + 尾帧图；**首帧复用判断**（决策点②）
    决定首帧图是「复用上尾帧」还是「独立生成」。产出首帧图 + 尾帧图入库资产，是**独立的图片模式**，
    不只是视频的参考图。
- **视频层两个模式**（基于图片 + 剧情）：
  - **climax（高潮点·动作代入）**：高潮图 → 单图生视频。语义是**高潮图片的动作延伸**（决策见 V1.6）。
  - **firstlast（首尾帧·剧情影片）**：首帧图 + 尾帧图 → 双图生视频；任务编排含**转场视频排队**
    （决策点④）+ **时长分档**（决策点⑤）。

**两种视频模式（preset 二选一，决策点①）——语义本质不同，提示词截然不同**：
- `videoMode: "climax" | "firstlast"`，默认 `climax`（现有高潮点视频，旧预设兼容）。
- **climax（高潮点·动作代入）**：高潮**动作瞬间** → 1 张动作画面 → 单图生视频。目的不是叙事，
  而是给高潮桥段加「代入感」——把高潮图的定格动作**延伸**为剧情描述的完整动作（见 V1.6）。
  **不覆盖整个桥段**，提示词 = 单一动作画面 + 动作延伸，不做完整时间轴。
- **firstlast（首尾帧·剧情影片）**：剧情楼层（roleplay 路由产出，参考 `isStoryNode` 同思路）→
  生首帧图 + 尾帧图 → 双图生视频。这才是「剧情对话对应的完整影片」，首帧到尾帧覆盖整个桥段的
  起承转合。提示词 = 完整叙事时间轴（七段式，见 V1.6）。

**首尾帧锚点提取**：从楼层文本提取「开头画面」+「结尾画面」两个锚点（复用现有高潮锚点提取
`scene_illustration` 的段落打分/锚点纠正思路，取首、尾两处而非单一高潮段）。

**首帧复用判断（决策点②，2026-08-26 用户定调修正）**：
首尾帧生图需有**明确判定**：判断「N+1 次对话的首段」与「N 次对话的尾端」是否构成
「一张图可涵盖」的关系——
- **能涵盖**（构图/场景/站位无显著变化）→ 首帧图**复用** N 次对话的尾帧图，无需重新生成；
- **不能涵盖**（构图、场景位置等发生较明显变化）→ 独立生成「首帧图 + 尾帧图」。
判据（构图/场景位置变化）采用**两级判断（2026-08-26 用户定调修正）**：
- **L0 纯启发式（0 LLM，先跑）**：先排除简单情况——地点/场景切换词（换地点/换场景 → 明显
  变化，直接独立生成）；段落画面特征对比（复用 `story_frames._VISUAL_TERMS` / `_dialogue_ratio`，
  明显同场景连续 → 直接复用）。启发式能确定的不再进 LLM。
- **L1 LLM 确认（搭车主生成，非后发）**：启发式判断「可以复用」但属模糊地带时由 LLM 确认。
  **LLM 判断不得后发单独调用**——正文/块是防拦截结果，再次读取可能被拦截、读不准；正确做法
  是**打从主生成时就默认输出转场判定**：主 Roleplay 生成正文的同一次调用里搭车输出结构化
  转场标记（对齐 `<状态更新>`/`<illustration>` 块，如 `<transition>reuse|regenerate</transition>`），
  生成时即决定是否触发转场，正文落地后不再读文本复核。

**首尾帧视频的任务编排（决策点④，2026-08-26 用户定调新增）**：视频任务数取决于首帧复用判断——
- **无需生成首帧图**（首帧复用上尾帧）→ 只需 **1 个视频任务**：当前剧情的首尾帧视频
  （首帧图 → 尾帧图）。
- **需要生成首帧图**（构图/场景明显变化）→ **2 个视频任务排队**：
  ① 转场视频（上一对话尾帧 → 当前对话首帧）；② 当前剧情的首尾帧视频（当前首帧 → 当前尾帧）。

**时长分档（决策点⑤，2026-08-26 用户定调修正）**：转场视频是短桥段，**不得**套用正片时长
（如 15s）；正片（首尾帧/高潮视频）按剧情长度 + preset `videoDurationHint`。
**转场时长不预设死值**：转场内容随机性太高（触发时机/场景差无法预估），不在提示词编译层
硬控时长；由前端视频模板/生成侧按实际转场内容决定（模板 duration 输入或视频模型默认）。

**转场素材（思路B，决策点③）**：preset 配置「转场开局图」（用户自备：片头/空镜/logo 等），
作为接不上时的首帧兜底来源；转场不单独生成视频，仅作为首帧图。

**素材来源（决策点③）**：首尾帧描述融合通用数据表结构化信息（`table.py` 重要角色表的
外貌特征/穿着打扮/所在地点、全局表的「地点/世界状态」、任务表的「地点」），与角色卡/世界书
`appearance_source` 一致——场景、角色信息不靠重新提取，直接从已上传表格读。

**改哪里（待实现）**：
- 前端 `MediaInsertPreset` 加 `videoMode` 字段 + 转场开局图配置。
- `scene_illustration` 加首/尾双锚点提取（对照 `resolve_illustration_anchor`）。
- 尾帧图入库 + 链式引用：本楼层尾帧图存资产库，下一楼层作为「上楼层尾帧」上下文。
- 首尾帧描述生成融合表格素材 + 上文尾帧。
- 视频提示词按 H3 七段式骨架本地编译（见 V1.6）。

### V1.6 视频独特提示词方法（方案定稿 2026-08-25）

> **进度（2026-08-26）**：已落地。climax 精简版（动作延伸）与 firstlast 七段式两套提示词
> 已分开建模（`video_prompt.compile_climax_video_prompt` / `compile_firstlast_video_prompt` /
> `compile_transition_video_prompt`）；第⑤块时间分镜为 firstlast 专属；climax 动作延伸通过
> `<illustration>` 块可选字段 `action_sequence` 落地（`_climax_action_beat` 优先消费，
> 缺失回退 subjects/visual_facts/composition）；`video_prompt` 随 `illustrate_request` 下发。
> 防拦截对齐图像两层机制（共享 `prompt_clean` 破甲还原 + IMAGE_PROMPT 清洗规则）。
> 提示词内容编写（镜头语言 + 动态提取逐段打磨）按用户指示留到测试环节逐步排查。

**目标**：视频提示词从「高潮动作概括」升级为「视频专属生成法」，不套用生图静态描述框架。
两种模式（V1.5）的提示词**截然不同**，分别建模，不共用一套模板。

**方法**：参照 MiniMax H3 提示词模板规律（`D:\video\寻味电台\H3视频生成\H3-提示词模版规律.md`）——
H3 高服从字面执行，本质是「把成片逐秒逐镜头规定死」。七段式骨架的原料 `scene_spec` 事实合同
（V1.2）已含大部分字段，**本地编译而非重新调模型**。七段式**只用于 firstlast 模式**：

| H3 七段 | 来源 |
|---------|------|
| ① 元信息（时长/比例/FPS） | preset `videoDurationHint` + 模板 |
| ② 风格声明（类型+美学+配色） | preset 风格前缀 |
| ③ 参考绑定（图=角色/镜头职责） | actors → `characterLoras[角色].baseImage`（V1.1）|
| ④ 主体/场景（可还原细节） | `scene_spec.appearance/wardrobe/locale` + 通用表格 |
| ⑤ 时间分镜（0-Xs 节奏名+运镜+动作+节拍） | **visual_facts + camera/composition + motion 本地编译**（核心新增）|
| ⑥ 音频（音乐/音效/台词） | comfy_audio 对白 + preset 音乐风格 |
| ⑦ 负面约束 | preset `negativePrompt` + 模板禁区 |

**两种模式的提示词差异**：

- **firstlast（剧情影片）**：完整七段式。③ 参考绑定为首帧+尾帧两张图，⑤ 时间分镜覆盖
  「首帧画面 → 演变 → 尾帧画面」全程（起承转合）；首帧描述带「上楼层尾帧」上下文（V1.5 决策②）。
- **climax（动作代入）**：**精简版**，不做完整时间轴。只保留 ① 元信息 + ② 风格 + ③ 单图参考绑定
  + ④ 主体 + 一个「动作瞬间 + 运镜/特效/节拍」短描述 + ⑦ 负面约束。
  语义是**高潮图片的动作延伸**（非简单「动态化」）：高潮图是动作的**定格起点帧**（如「勺子挖出
  一勺奶油」），视频要延伸演出剧情里描述的**完整动作**（如「从挖出到吃下去」/「从挖出到把勺子
  喂向镜头」）——延伸动作**必须来自剧情文本**（剧情写了「吃下去」才有吃下去，写了「喂给主角」
  才有喂向镜头的流程），不得凭空补动作。禁止套七段式时间分镜（那会让高潮点误生成整段叙事）。

**核心工作**：
- **第⑤块「时间分镜」（firstlast 专属）**：把剧情动作窗口按时间轴切 3-5 段，每段
  「节奏名 + 运镜 + 主体动作 + 特效/节拍」，这是图片提示词没有的。
- **climax 动作延伸**：`_climax_action_beat` 需补「剧情动作目标/序列」来源（决策③，
  定格动作 → 剧情描述的完整动作），不再只拼 subjects/visual_facts/composition
  的空间要素——见 `PLAN-VIDEO-FIRSTLAST.md` 坑 E（10.5）。
- 产出 `video_prompt` 随 `illustrate_request` 下发，前端视频分支优先用。

> 实现计划 + 风险审计（R1-R10，含双图 image[] 语义实测红线、firstlast 缺图守卫、
> 元信息三件套、前端触发闸门分离等）见 `docs/PLAN-VIDEO-FIRSTLAST.md`（glm-5.3 规划 2026-08-26）。

---

## M1 灵感素材化（文字卡→素材卡）

### M1.1 搜索源 Adapter 注册表

**目标**：`web_search` 收编为可插拔搜索源注册表，单一属主，路由与 Agent 不感知具体源。

**改哪里**：
- `backend/app/services/web_search.py`——重构为 `SearchAdapter` 协议 + 注册表 dict（形态对照 `scene_illustration` 的 renderer 注册表）+ DDG 首个实现。
- `backend/app/services/inspiration.py`——调用侧改为 `search(query, provider=...)`，行为不变。
- `user_state` settings——可选 `searchProvider` 字段（默认 ddg），密钥若需要只进 user_state.json。

**实现要点**：
1. Adapter 接口固定：`search(query, max_results, proxy) -> [{title, snippet, url}]`；单源失败返回空列表（现有语义），`inspiration` 抛 `NoResults` 兜底。
2. DDG 实现原样迁移（HTML 正则解析、显式 proxy、trust_env=False），零行为变化。
3. 候选扩展源排序：SearXNG（自托管可控、有稳定 API）> Bing（需密钥）> 商业聚合 API。

**验收**：注册表单测（注册/查找/默认源）；DDG 行为迁移前后一致（现有测试不动全绿）。

**预排问题**：
- **DDG 反爬/改版**：HTML 正则解析脆弱是已知债务；adapter 化后故障域隔离，加源即可恢复，不改调用方。
- **密钥泄漏**：adapter 日志与 Trace 禁止输出密钥；配置读取只在服务层。

### M1.2 图片素材搜索与预览

**目标**：灵感搜索支持图片结果，前端灵感卡区域渲染缩略图网格供预览选择。

**改哪里**：
- 新图片搜索 adapter（DDG Images 或 Bing Images，按 M1.1 接口）。
- `backend/app/services/inspiration.py`——返回体加 `images: [{thumb_url, full_url, source_url, width?, height?}]`（仅远程 URL，不自动下载）。
- 前端灵感卡渲染组件——缩略图网格 + 点选；选中项进入 M1.3 下载流程。

**实现要点**：
1. 图片结果只存 URL 不落盘；`persist_inspiration` 会话快照只记选中项的 URL（避免快照膨胀）。
2. 缩略图显示走**后端图片代理端点**中转（新路由，形态对照 `local_media` 的 Adapter+Response 分层），原因见预排问题。
3. 来源信息保留：每张图带 source_url（来源网页）与 full_url（图直链），版权追溯链不断。

**验收**：搜索→预览→选中的 E2E；无图结果时降级为纯文字卡（现有形态）不报错。

**预排问题**：
- **前端直连外网图床**：浏览器 `<img src=外网>` 不走后端代理、可能被墙/防盗链拦截 → 必须后端代理中转；代理端点只允许 http(s) 且限响应大小（如 5MB），防被当开放代理滥用。
- **搜索结果污染**：图片搜索结果质量参差 → 保持用户选择权（预览点选），不做自动下载、不做自动首选。
- **NSFW 边界**：本地单用户工具，内容边界由用户自查；系统只保证不自动落盘未选中内容。

### M1.3 受控下载入资产库

> **进度（2026-08-25）**：受控下载安全链已补齐——候选列表校验（只接受搜索结果登记过的 URL，快照内灵感卡图片兼容）、域名白名单（默认仅 https，`WEB_MATERIAL_ALLOWED_DOMAINS` 可放行）、provenance 落盘（source_url/搜索词/搜索源/时间/大小/格式）、魔数校验、SSRF/私网拒绝、大小上限、原子写均已就位并单测覆盖；前端灵感卡增加「保存到素材库」入口，画布拖放改 data URI 上传。**后台任务化（task_progress_store）列为后续增强**（同步下载 20MB 上限内可接受）。

**目标**：用户选中的图经校验链下载进作品资产库，成为可管理资产。

**改哪里**：
- 新 `backend/app/services/asset_ingest.py`——下载事务单一属主（形态对照 `model_downloader`：临时文件→校验→原子落盘）。
- `generation_store` / `rag_store`——资产登记与 RAG 入库（显式创建路径）。
- 路由：资产库域新端点（收参→调服务→包异常，薄）。

**实现要点**：
1. 校验链五步：扩展名 ∈ {jpg,png,webp} → Content-Type 匹配 → **魔数校验**（JPEG `FF D8 FF`/PNG `89 50 4E 47`/WebP `RIFF....WEBP`）→ 大小上限（首版 20MB）→ 域名策略（默认全 https 可配白名单）。
2. 下载写 `.part` 临时文件，全部通过后原子改名落盘；失败清理临时文件（对照 `model_downloader` 合同）。
3. provenance 落资产 metadata：source_url、query、搜索源、下载时间。
4. 后台任务化：走 `task_progress_store` 进度合同（重启中断归一），大图慢图不阻塞请求。

**验收**：校验链每条失败路径的单测（伪造魔数、超大、私网 URL）；真实下载一张图入库并出现在资产库。

**预排问题**：
- **SSRF**：恶意图 URL 指向内网元数据端点（169.254.169.254、10.x、127.x）→ 下载前解析目标 IP 并拒绝私网/环回/链路本地段；重定向逐跳复查。
- **任意 URL 落盘**：端点只接受**本会话搜索结果登记过的 URL**（服务端缓存候选列表、带 TTL），不接受客户端任意提交 URL——这是"受控"的核心语义。
- **同名覆盖**：资产命名内容寻址（hash 前缀）或时间戳，禁止按原文件名直接覆盖。

### M1.4 灵感卡资产库化

**目标**：灵感卡从会话快照升级为资产库可管理成员，可跨会话检索复用。

**改哪里**：
- `generation_store.persist_inspiration`——保留会话快照写入（对话内展示），新增资产库登记（卡 identity = query+tags 摘要 hash）。
- 资产库索引——灵感卡作为资产类型成员入库（RAG 文本链，无需视觉索引）。

**实现要点**：
1. **资产库是唯一持久真源**，会话快照只是展示缓存；灵感卡不可两处编辑。
2. 遵守 generation RAG 合同：禁止磁盘自动补录、删除资产保留本地文件。
3. 「一键插对话」现有行为不变。

**验收**：新会话能检索到旧会话保存的灵感卡；删除资产不删会话内已展示的卡。

**预排问题**：
- **双真源**（最大坑）：若会话快照与资产库都可改，改哪边另一边就脏 → 写路径只有资产库，会话内卡片是只读投影。
- **刷库**：每次搜索都自动入库会灌满资产库 → 入库是显式动作（用户点保存）或卡被实际用于生成时才登记。

> **进度（2026-08-25）**：已落地。资产库形态按用户定调细化——灵感卡归入「上网素材」域（`_web_materials/inspiration/<id>.json`），**不**并入生成内容资产库（避免与"生成参数/标签"详情语义混淆）：
> - **后端** `inspiration_store.py`：save/list/get/update/delete；图片走 M1.3 受控下载安全链落盘 `_web_materials/`，卡 JSON 记录本地引用；删卡只删 JSON（图片保留可作独立素材）、删图只改 JSON（图文件保留）。入库是显式动作（前端"保存到素材库"），不自动刷库。
> - **前端 WebMaterialsView** 加「灵感卡」tab：封面=首图(1:1)或文本预览（放部分内容）；双击→详情弹窗显示**文本内容**（非生成参数），可逐图删除只留文本；批量选择→删除/发送对话框/发送画布。
> - **发送对话框**：有图=图片+文本、无图=纯文本（chatAppend）；**发送画布**：`laf-inspiration-to-canvas` 事件→画布创建灵感卡节点（带封面图）。
> - 对话框灵感卡「保存到素材库」改为存**整卡**（标题+内容+选中图片）。
> - 测试：后端 9 例（保存/幂等/本地引用/受控下载/未登记拒绝/删图留文本/删卡留图/非法 id）；前端 tsc 0 错 + chat 组件测试通过。

### M1.5 灵感卡→输入框联动创作

> **定调（2026-08-25）**：原设想「素材图一键设为角色底图/参考图/工作流底图」被更贴合创作流的方案取代——**灵感卡「插入对话」改为插到输入框图片栏的 9:16 卡片，发送时图文拆分**，闭环「找参考 → 带进对话 → 编辑 → 再发」。原「设为底图/参考图」若后续需要，作为独立能力另立项（不并入 M1.5）。

**目标**：灵感卡（联网搜 + 提炼的「标题+内容」知识总结，**主题不限**）作为可编辑的卡片附件进输入框，发送时图文拆分，编辑已发送消息时能还原回卡片形态。

**改哪里**：
- `frontend/src/lib/inspirationInsert.ts`——序列化/逆序列化（`serializeInspirationSend`/`deserializeInspirationSend`）+ 全局缓存通道（`pushInspirationsToChat`/`consumePendingInspirationAttachments`/`CHAT_INSPIRATION_EVENT`）。
- `frontend/src/components/RichInput.tsx`——输入框图片栏 9:16 灵感卡附件（`inspCards`）+ `insertInspirationCard`。
- `frontend/src/views/ChatView.tsx`——通道消费（挂载 + 收事件时插入）；画布/对话共用同一 RichInput。
- `frontend/src/lib/chatGeneration.ts` + `types/chat.ts`——`inspirationAttachments` 持久化 + 编辑回填逆解析。

**实现要点**：
1. **三条不变量**（再改不破）：
   - 主题无关：插入对话的「灵感参考」身份标记不预设视觉/风格方向；身份语义三点通用（参考素材、非指令、冲突以用户要求为准）。
   - 「插入对话」= 插到输入框图片栏卡片，**非直接发送**；发送时 `serializeInspirationSend` 图文拆分（封面图进图片参数、title/content 转语义文本追加在用户文本后）。
   - 编辑回填还原卡片：用户消息持久化 `inspirationAttachments` 字段，`userMessageRichContent` 用 `deserializeInspirationSend` 逆序列化拆回「纯用户文本/图 + 卡片附件」。
2. 素材库「发送对话框」改为「插入输入框」（`SendToChatModal` 增 `insertInput` 模式：选作品、不落盘、push 到输入框）。
3. 图片说明按有无封面图条件输出（纯文本卡不声称「消息附带图片」）。

**验收**：对话模式 /find → 插入 → 9:16 卡片 → 发送图文拆分 → 编辑还原卡片；素材库「插入输入框」选作品后画布/对话模式均插卡；vitest 522 passed。

**预排问题**：
- **通道未挂载**：素材库 push 时 ChatView 未挂载 → 走全局缓存，挂载/收事件时消费（`CHAT_INSPIRATION_EVENT`），不丢卡。
- **快照瘦身**：`inspirationAttachments` 含远程封面 URL，随消息 JSON 落盘，非 dataURI 大图，无体积隐患。

---

## M2 派生关系编排（多模态资产连续性）

### M2.1 derived_from 派生元数据

**目标**：视频（及未来的图→图衍生）资产记录派生来源，派生链可读不可自动执行。

> **进度（2026-08-25）**：已落地。视频产出经 ComfyUI 工作流 finalize 时入库资产库（此前 `indexed: False` 不进库），
> metadata 记 `media_type=video` + `derived_from=[{media_slot_ref:{message_id,slot_id}, kind:"video_base_image"}]`——
> 来源 = V1.1 视频首帧底图（前端 `resolveVideoBaseImageRef` 取底图时同时取来源槽引用，随 `base_slot_ref` 传后端）。
> 资产库 UI（RepoGallery）视频条目展示「派生来源」只读行（弱引用：来源删除不报错、不级联）。

**改哪里**：
- `generation_store`——资产 metadata 加 `derived_from: [{asset_id|media_slot_ref, turn_id, kind}]`。
- `chat_snapshot` 媒体槽元数据——视频槽记录底图槽引用。
- 资产库 UI——派生链展示（这张视频来自哪张图、哪个回合）。

**实现要点**：
1. 弱引用：来源被删不级联、不阻断展示，UI 显示「来源已删除」。
2. 红线：派生链**只读展示**，禁止「源头改了自动重跑下游」的级联重建。

**验收**：V1.1 产出的视频带正确 derived_from；删除底图后视频展示不报错。

**预排问题**：
- **引用膨胀**：派生链多层嵌套后 UI 递归渲染失控 → 首版只展示一跳（直接来源），深层链后续按需。
- **跨仓库引用**：底图与视频分属不同作品（理论上不该发生，路径上防御）→ 记录时校验同 repo，异常写 Trace 不入库。

### M2.2 资产库多模态筛选与视频封面

**目标**：资产库按媒体类型（文/图/视频/灵感卡）与来源（生成/检索下载）筛选；视频有封面可浏览。

> **进度（2026-08-25）**：已落地。资产库（generation）新增媒体类型筛选（全部/图片/视频，`mediaType` 识别）；
> 视频条目在网格中显示占位封面（🎬 图标 + 「视频」标签），点击进详情用 `<video controls>` 播放；
> 无封面用占位图标不阻塞列表（ComfyUI 侧未返回 thumbnail，按 ROADMAP 红线不为封面引 ffmpeg）。
> 「文/灵感卡」筛选维持原样：灵感卡在「上网素材」域已有 tab（WebMaterialsView），知识文档在知识库页——不强行并入 generation 资产库。

**改哪里**：
- 前端资产库——筛选器与视频卡片形态。
- 封面：优先取生成侧返回的 thumbnail（ComfyUI/云端若给）；**不为封面引入 ffmpeg/opencv 依赖**。

**实现要点**：
1. 视觉索引只对**封面帧**生效（若有），视频本体不入向量库。
2. 无封面视频用占位图标，不阻塞列表。

**验收**：混合资产（图+视频+灵感卡）筛选与分页正确；无封面视频正常展示。

**预排问题**：
- **依赖蠕变**：抽封面帧最省事的是引 ffmpeg → 拒绝；封面是增强项不是功能项，Provider 不给就没有（红线 2 同源：克制）。
- **索引体积**：视频封面入 `visual_asset_index` 前确认集合配额与 repo 隔离合同不破。

---

## A1 剧情智能音频化（对话念白配音 × IndexTTS）

> 长线决策（2026-08-24 定稿）：产出 = 剧情楼层对话内容念白配音（旁白/叙述忽略）；引擎 = 先做 ComfyUI（IndexTTS-2.5 情感向量节点），云端 TTS 后续；音色 = 每个作品专属、每名角色一个参考音轨（`<repo>/voices/<角色名>.wav`，类比 reference/）；逐角色提交（模型一次一个音色）；每段台词独立生成 + 排序 + 合并；产出聚合到剧情楼层、音频气泡按角色分条；情感向量兜底 Neutral=1。

### A1.1 多元数据插入三开关重构

**目标**：多元数据插入从「图片为主 + 可选视频」重构为「图片 / 视频 / 音频」三开关多选；未勾选的分区隐藏；开关「剧情插画」更名「剧情自动生成」，开启后按勾选类型随剧情自动生成。

**改哪里**：
- `frontend/src/components/MediaInsertModal.tsx`——顶部三开关（`enableImage`/`enableVideo`/`enableAudio`）+ 图片/视频/音频三块分区显隐；现有图片选项（模板/LoRA/底图/尺寸/提示词/按角色配置）整体移入「生成图片」分区。
- `frontend/src/stores/settings.ts` `MediaInsertPreset`——加 `enableImage/enableVideo/enableAudio` 三布尔 + `audioTemplateId` + `characterVoices`。
- 开关更名：`ChatView.tsx` 里「剧情插画」按钮文案与 tooltip 改「剧情自动生成」；触发分发按 enable 位走三条链路。

**实现要点**：
1. 三开关默认：图片开、视频/音频关（向后兼容旧预设，旧预设视为 enableImage=true）。
2. 未勾选图片时，图片专属字段（模板/LoRA/底图/尺寸/提示词）整块隐藏，但**不丢值**（勾回去还在）。
3. 至少勾选一项才能保存；全不勾 = 等价关闭「剧情自动生成」。

**验收**：三开关切换分区显隐；旧预设载入后 enableImage 默认开、行为不变。

**预排问题**：
- **旧预设兼容**：旧 `MediaInsertPreset` 无三布尔 → 载入时按 `templateId`/`videoTemplateId` 有无回填 enableImage/enableVideo；音频默认关。
- **保存联动**：勾选音频但未配音轨/模板 → 保存允许（运行时空角色回退 Neutral），还是拦截？首版允许、运行时缺音轨跳过该角色并 Toast。

### A1.2 按角色参考音轨配置

**目标**：每名角色配置一个参考音轨（音色），交互复刻「按角色配置（LoRA + 底图）」表格。

**改哪里**：
- `MediaInsertModal.tsx` 音频分区——按角色表格（角色名 + 选择/上传音轨文件），形态对齐现有 `rows` 表格。
- 后端上传通道——音轨落到 `<repo>/voices/<角色名>.<ext>`（复用 reference 上传通道或新增 voices 目录端点）。

**实现要点**：
1. 数据形态 `characterVoices: Record<角色名, { voiceRef: string }>`（对齐 `characterLoras` 先例）。
2. 音轨文件 `audio/*` 白名单（wav/mp3/flac）；`<repo>/voices/` 目录作品专属。

**验收**：配置音轨后保存/重载保留；音轨文件落到 `<repo>/voices/`。

**预排问题**：
- **同名角色**：多卡同名时按角色名 key 会互相覆盖 → 首版以角色名 key，冲突时后者覆盖 + 保存提示。

### A1.3 音频语义绑定

**目标**：模板 exposed 声明音频输入口，运行时注入 `voice_text`（台词）/`voice_reference`（参考音轨）/情感向量。

**改哪里**：
- `frontend/src/api/workflows.ts`——新增 `SEMANTIC_VOICE_TEXT = "voice_text"`、`SEMANTIC_VOICE_REFERENCE = "voice_reference"`（+ 可选 `voice_emotion`）。
- 后端 `inject_template_values` / `submit_template`——音频节点注入：`voice_text → IndexTTS25EmotionVectorNode.text`、`voice_reference → LoadAudio.audio`、情感向量 → `IndexTTS25EmotionVectorNode` 的 8 个 widget（happy/angry/sad/fear/hate/low/surprise/neutral）。

**实现要点**：
1. 参考 IndexTTS-2.5 工作流节点：`LoadAudio`（参考音轨）→ `IndexTTS25EmotionVectorNode`（text + 情感向量）→ `SaveAudio`（`filename_prefix` 按角色命名）。
2. 情感向量 0~1 混合权重（非 one-hot），未给时 Neutral=1 兜底。

**验收**：真实提交一次音频模板，节点收到 text + 参考音轨 + 情感向量。

**预排问题**：
- **节点字段名漂移**：IndexTTS 节点 widget 名以实际模板 JSON 为准，语义绑定常量与节点名解耦（binding 只声明语义，注入层按模板节点映射）。

### A1.4 台词筛分 + 情感向量

**目标**：剧情楼层正文产出后，LLM 切分出「谁说的哪句话」+ 每句 8 维情感向量；只取对话、旁白/叙述忽略。

**改哪里**：
- 新增后端台词分析模块（复用 supervisor 类 LLM 调用）：输入 = 楼层全文 + 上下文（前几轮剧情/角色状态/好感度）+ 在场角色名，输出 `[{speaker, text, emotion: {happy,angry,sad,fear,hate,low,surprise,neutral}}]`。

**实现要点**：
1. 情感判断**必须含上下文**——「你走开。」是愤怒/悲伤/冷漠要看上下文，只读单句不对味。
2. 每段台词独立成一条（对话场景：A 说一句、B 接一句），不合并相邻同角色——保证多人对话时序。
3. LLM 解析失败/拒答 → 情感向量回退 Neutral=1（不阻断生成）。

**验收**：真实剧情楼层切分出多角色有序台词；情感向量 8 维合法（0~1）。

**预排问题**：
- **角色名未对齐**：正文里角色名与 `card_names` 不一致 → 模糊匹配 + 兜底「未识别角色」跳过；LLM 输出需约束 speaker ∈ 在场角色集。

### A1.5 逐角色独立生成 + 排序 + 合并

**目标**：每段台词独立提交一次 IndexTTS 运转（注入该角色音轨 + 台词 + 情感向量），完成后按楼层时序排序。

**改哪里**：
- 复用 `submit_template` 逐条提交；每次注入不同 `voice_reference` + `voice_text` + 情感向量。
- 画布占位进度显示「正在生成 阿尼玛(1/3)…」逐角色推进（复用 streamingId 占位机制）。

**实现要点**：
1. 独立生成 = 每段一次运转，`filename_prefix` 带角色 + 序号（如 `IndexTTS25/阿尼玛_02`）。
2. 排序 = 按筛分结果的原始顺序（对话时序），不是按完成顺序。
3. 合并 = 一期只做「分段聚合展示」（不强制 ffmpeg 混音，音频气泡按角色分条），真正混音列为后续。

**验收**：多角色楼层产出多段音频，顺序正确、逐角色进度可见。

**预排问题**：
- **提交风暴**：N 句台词 = N 次运转，逐角色串行提交（ComfyUI FIFO），不并行轰炸；进度用 task_progress 合同跟踪。
- **失败隔离**：某句生成失败不阻断其余句，失败句占位标记 + 可重试。

### A1.6 产出聚合到剧情楼层 + 分条音频气泡

**目标**：音频聚合到对应剧情楼层，对话气泡与画布楼层节点内按角色分条展示播放器。

**改哪里**：
- 对话：`ChatMessage` 剧情消息扩展音频槽（按角色分条）——复用 `parts` 媒体槽或 `audio` 扩展为数组。
- 画布：剧情楼层节点展示多条音频播放器（角色名 + 播放条）。
- 落库：音频产出 `generation_store` 登记 + `chat_snapshot` 音频槽。

**实现要点**：
1. 音频气泡按角色分条（每角色一段播放器），带角色名标签。
2. 复用已有 audio 播放器组件（`<audio>` / 波形可视化为后续增强）。

**验收**：剧情楼层内多角色音频可逐条播放、顺序正确、刷新恢复。

**预排问题**：
- **快照体积**：音频文件本体按红线不进会话快照，只存引用 url + 角色名；文件外置资产库。
- **波形可视化**：音频节点波形条渲染为后续增强（Web Audio API 解码生成波形），一期纯播放器。

---

## 横切关注点（贯穿所有步骤）

1. **密钥**：搜索/视频 API 密钥只进被 Git 忽略的 user_state.json；adapter/路由/Trace/日志禁出。
2. **Trace**：新运行阶段复用 `run_trace.emit`；搜索与下载记录来源 URL 与决策依据（不含密钥）。
3. **不可信输入**：搜索摘要/网页文本/文件名一律视为外部内容，截断+来源标记后进模型（`instruction_provenance` 合同）。
4. **通道隔离**：本地视频走 ComfyUI FIFO；下载/搜索走后台任务；任何新等待不得进前台对话路径。
5. **Token 边界**：视频/素材只以 url+caption 进会话；灵感卡一句话说明不刷屏。
6. **文档同步**：每步落地后更新本文件进度表与 `ARCHITECTURE.md` 对应模块行；协议变更同步 wire schema 与双端测试。
7. **回滚预案**：V1.1/V1.2 是前端提交参数扩展，回滚=恢复旧预设行为；M1.3 下载端点独立，出问题禁用配置即可，不影响既有链路。
