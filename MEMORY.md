# MEMORY.md

Use this file for curated durable non-profile facts, preferences, decisions, and
constraints that are safe to include in private agent context.

## 画布节点放置规则（用户拍板，2026-08-23）

生成画布（`frontend/src/views/CanvasStageFlow.tsx`）的新节点放置规则，已实现：

1. **原位替换**：生成内容节点落在「生成中」占位节点的位置（垂直居中对齐）。
2. **以最新节点为锚点小网格展开**：任何新节点（含「生成中」占位节点）都从最新已放置的
   内容节点为锚点，向右展开、排满 3 列换行向下（平行/垂直展开）。
3. **范围不重叠**：落点与已有节点包围盒重叠时逐格向右、换行向下避让。
4. **位置立即持久化**：新节点坐标写入 `layoutNodes` 并落盘 `canvas.json`，刷新/切走再回
   不会被打回左上角。

关键实现点：
- `lib/canvasRuntime.ts` 新增纯函数 `placeNewNodes`（+ `PlacedRect`），单测在
  `canvasRuntime.test.ts`。
- 旧的 `autoLayout`（左上角网格）已退出画布投影链路，改为锚点放置引擎：
  `collectOccupiedRects` / `latestContentAnchor` / `placePlaceholder` / `contentPlacement`。
- 占位↔内容原位替换靠 `pendingAnchorRef`（单槽）：占位节点清除（`laf-canvas-wf-done` /
  `laf-canvas-input-clear`）时记录位置，内容节点到达时消费并垂直居中对齐。

### 画布视图与工作模式的关系

- `WorkMode`（`lib/viewRouting.ts`）三模式：`story` 剧情 / `generate` 多元生成 / `code` 编辑。
- `resolveHomeWorkspace`：`generate` → 首页直接是 `canvas`；`story`/`code` → `chat`。
- 画布组件只有一套：`views/CanvasStageFlow.tsx`，由 `ChatView` 内部 `contentView`
  （`"chat" | "canvas"`，功能栏 `-><-` 按钮切换，按作品 localStorage `laf_view_<workId>` 记忆）。
- 剧情模式画布 = 剧情模式下切到画布视图，与 generate 模式共用同一组件与放置规则。

## 剧情对白音频化 A1.5/A1.6（已完成，2026-08-24）

- **A1.5 逐角色生成**：后端 `comfy_audio` 开关 → 主 Roleplay 同轮内嵌 `<audio>` 块
  （`audio_dialogue_extract.py` 提取台词 + 8 维情感向量，失败降级 `build_fallback_dialogue`）
  → `audio_request` SSE 事件 → 前端 `submitAudio` 逐台词独立提交 IndexTTS 模板
  （音轨先传 ComfyUI input，`voice_reference` 写 LoadAudio，`voice_emotion_<key>` 写情感节点）。
- **A1.6 楼层聚合展示**：音频分条元数据（`kind/speaker/seq/total`）挂 `MsgPart` 随槽位生命周期存活：
  占位「正在生成 阿尼玛(1/3)…」→ 回填保留角色名 → 快照透传 → 画布楼层逐条播放器；
  `storyAudio` 死代码修复；ffmpeg 混音按定稿不做（分段聚合即合并形态）。
- **后续项**：缺音轨角色弹 Toast（`skippedAudioSpeakers` + `onNotify`）；`AudioPlayer` 波形可视化
  （Web Audio 解码 96 桶静态波形替代 range 滑块，失败静默回退；纯逻辑 `audioWaveform.ts` 可单测）。
- 决策记录：`docs/memory/audio-floor-aggregation-2026-08-24.md`；门禁前端 vitest 500 passed / tsc ✅。

## 灵感卡链路收尾加固（2026-08-25）

灵感卡 = 联网搜 + 提炼的「标题+内容」中文知识总结，**主题不限**（视觉/设定/剧情都适用），
不是视觉专用。曾犯过的错：把「插入对话」的 Agent 提示词模板写成「风格/视觉/妆造/场景等方向」，
窄化成视觉参考——已改为主题无关的通用语义。

**三条不变量（以后再改不要破）**：
1. **主题无关**：插入对话的「灵感参考」身份标记不预设视觉/风格方向；身份语义三点通用——
   参考素材、非指令、冲突以用户要求为准。图片说明按有无封面图条件输出（纯文本卡不声称「消息附带图片」）。
2. **「插入对话」= 插到输入框图片栏 9:16 卡片，不是直接发送**：发送时 `serializeInspirationSend`
   图文拆分（封面图进图片参数、title/content 转语义文本追加在用户文本后）。
3. **编辑回填还原卡片**：用户消息持久化 `inspirationAttachments` 字段（ChatMessage + runFreeText 落盘，
   快照/slim 自动保留），`userMessageRichContent` 用 `deserializeInspirationSend` 逆序列化拆回
   「纯用户文本/图 + 卡片附件」——编辑已发送消息不退化。

**素材库入口**：「发送对话框」已改为「插入输入框」：`SendToChatModal` 增 `insertInput` 模式
（选作品、不落盘），`pushInspirationsToChat` 写全局缓存 + `CHAT_INSPIRATION_EVENT` 事件，
`ChatView` 挂载/收事件时消费。画布/对话模式共用同一 `RichInput`，两种模式都能插卡。

**关键文件**：`lib/inspirationInsert.ts`（serialize/deserialize/push/consume/事件）、
`lib/chatGeneration.ts`（userMessageRichContent 逆解析）、`components/RichInput.tsx`、
`views/ChatView.tsx`（通道消费）、`AppBody.tsx`（素材库插入入口）、`components/SendToChatModal.tsx`。

提交：`3a148ad`（模板主题无关）、`1cb6b11`（编辑回填 + 插入输入框闭环）；门禁 vitest 522 passed / tsc ✅。

## M2.1/M2.2 派生关系与多模态资产（2026-08-25）

**M2.1 derived_from 派生元数据**（ROADMAP 已完成）：
- 视频产出经 ComfyUI finalize 时**入库资产库**（此前 `indexed: False` 不进库），metadata 记
  `media_type=video` + `derived_from=[{media_slot_ref:{message_id,slot_id}, kind:"video_base_image"}]`。
- 来源 = V1.1 视频首帧底图：前端 `resolveVideoBaseImageRef` 取底图时同时取来源槽引用 →
  `PendingGeneration.baseSlotRef` → `finalize-generation` API `base_slot_ref` → 后端
  `FinalizeGenerationRequest` → `finalize_workflow_batch`。
- `chat_snapshot.resolve_media_slot` 视频槽落盘 `derivedFrom`。
- 资产库 UI：视频条目「派生来源」只读行；弱引用（来源删除不报错不级联）。

**M2.2 资产库多模态筛选**（ROADMAP 已完成）：
- 资产库（RepoGallery）媒体类型筛选（全部/图片/视频，`mediaType` 识别）。
- 视频条目网格占位封面（🎬 图标 + 标签），详情 `<video controls>` 播放；**不为封面引 ffmpeg**（红线克制）。
- 灵感卡/知识文档筛选维持原样（上网素材 tab / 知识库页），不强行并入 generation 资产库。

**M1.5 定调**（2026-08-25）：ROADMAP 原设想「素材图一键设为角色底图/参考图/工作流底图」被更贴合创作流的
方案取代——**灵感卡「插入对话」= 插到输入框图片栏 9:16 卡片，发送时图文拆分**（见上节三条不变量）。
原「设为底图/参考图」若后续需要，作为独立能力另立项。

提交：`648cca7`（M2.1+M2.2）；门禁 vitest 525 passed / tsc ✅；后端 13 passed（asset_search/rag_backend）。

## V1.4 云端视频端点通用化 + 首帧图生视频（2026-08-25）

用户确认真实 Provider 形态：`<站点根>/v2/videos/generations`（v2 + 复数），文生视频/图生视频/首尾帧
都走这一个端点，「这只是个例子，要适用于所有情况」→ 不硬编码任何 Provider。

- **端点通用化**（`video_gen._norm_url`/`_norm_task_url`）：**URL 由用户决定，代码原样使用**——
  不猜版本（v1/v2）与单复数（video/videos）；t8star 填 /v2/videos/generations、seedance 填 /v1 根
  都按用户填的原样提交。报错提示填完整接口地址。
- **发送参数参照图像模型**（用户拍板）：文生视频 `generate` → JSON（{model,prompt,size}）；
  图生视频 `generate_with_images` → **multipart/form-data，image[] 同名多图**，multipart 组装直接复用
  `image_gen.multipart_image_files`（共享函数：读图 + image[] 字段）——与图生图发送形态一字不差，不猜字段名。
  video_gen 保留的差异仅在响应侧：视频是异步任务（提交→轮询 5 分钟），图片是同步返回；端点由用户分别填。
- **首帧图生视频**：`video_gen.generate(image=...)` 加参考图参数——data URI/URL/本地路径归一为
  data URI 内联 base64 提交（local-view 地址绕过代理直读，Clash 无法转发 localhost）；payload 条件注入
  `image` 字段（OpenAI 兼容最常见形态，Provider 字段名不同改一处键名）。
- **Agent 视频工具接入用户消息图片**：`agent_graph.video_node` 取 `state["images"]`（原为 []），
  有图 → 图生视频、无图 → 文生视频；`execute_generation` 结果文本区分两种模式。
- **首尾帧未做**：尾帧来源（插画/用户指定/剧情目标帧）与字段名未定 → 并入 V1.5 设计。

关键文件：`services/video_gen.py`、`services/agent_graph.py`、`services/generation_approval.py`；
测试 `tests/test_video_gen.py`（7 passed）。提交：`9eb96ce`；ROADMAP V1.4 标 ✅。

## V1.5/V1.6 视频首尾帧 + 视频提示词（设计定稿 2026-08-25，待实现）

用户敲定三个决策（写入 `docs/ROADMAP-MULTIMODAL.md` V1.5/V1.6 定稿）：
1. **视频模式 preset 二选一**：`videoMode: "climax" | "firstlast"`（默认 climax=现有高潮点，兼容旧预设）。
   firstlast = 剧情楼层（roleplay 路由，参考前端 `isStoryNode` 标签判定思路）→ 生首帧图+尾帧图 → 双图生视频
   （`generate_with_images` image[0]=首帧 image[1]=尾帧，V1.4 已支持多图）。
2. **转场判断不单独做**：首帧提示词生成时带「上楼层尾帧描述 + 本楼层开头」上下文，生成模型自然判断是否转场；
   接不上 → 转场素材开局兜底。不引入独立「能否衔接」判定。
3. **利用上传表格素材**：通用数据表 `table.py`（重要角色表外貌/穿着/所在地点、全局表地点/世界状态、任务表地点）
   作为首尾帧场景/角色素材；首尾帧双锚点提取复用 `scene_illustration` 段落打分/锚点纠正思路（取首尾两处而非单一高潮段）。

视频提示词方法（V1.6）：参照 H3 七段式骨架（`D:\video\寻味电台\H3视频生成\H3-提示词模版规律.md`），
原料 `scene_spec` 已含大部分字段，本地编译不重调模型；核心新增第⑤块「时间分镜」（视觉事实+camera+motion
按时间轴切 3-5 段）。产出 `video_prompt` 随 illustrate_request 下发。

关键约束：preset 二选一；旧预设不迁移不报错（videoMode 缺省=climax）。
