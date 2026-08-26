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
   **两模式语义本质不同、提示词截然不同**：
   - climax = 高潮「动作瞬间」单图生视频，目的加代入感（动作图动态化，微运镜/特效/节拍），
     **不是**整个桥段叙事，不做完整时间轴。
   - firstlast = 剧情楼层（roleplay 路由）→ 首帧图+尾帧图 → 双图生视频，才是「剧情对话对应的完整影片」，
     覆盖整个桥段起承转合，走完整七段式时间轴。
2. **转场判断不单独做**：首帧提示词生成时带「上楼层尾帧描述 + 本楼层开头」上下文，生成模型自然判断是否转场；
   接不上 → 转场素材开局兜底。不引入独立「能否衔接」判定。
3. **利用上传表格素材**：通用数据表 `table.py`（重要角色表外貌/穿着/所在地点、全局表地点/世界状态、任务表地点）
   作为首尾帧场景/角色素材；首尾帧双锚点提取复用 `scene_illustration` 段落打分/锚点纠正思路（取首尾两处而非单一高潮段）。

视频提示词方法（V1.6）：参照 H3 七段式骨架（`D:\video\寻味电台\H3视频生成\H3-提示词模版规律.md`），
原料 `scene_spec` 已含大部分字段，本地编译不重调模型；**七段式只用于 firstlast**（核心新增第⑤块「时间分镜」
= 视觉事实+camera+motion 按时间轴切 3-5 段）；**climax 走精简版**（元信息+风格+单图绑定+主体+动作微动态+负面约束），
禁止套时间分镜。产出 `video_prompt` 随 illustrate_request 下发。

关键约束：preset 二选一；旧预设不迁移不报错（videoMode 缺省=climax）。

## V1.5/V1.6 实现计划 + 风险审计（glm-5.3 规划 2026-08-26）

已产出 `docs/PLAN-VIDEO-FIRSTLAST.md`，将剩余实现拆成 P1-P6 分期并做隐患审计（R1-R10）。
核心红线与隐患：
- **R1（最大红线）**：双图 `image[]` 首尾帧语义未实测——OpenAI 兼容 `image[]` 只是「多参考图」，
  不是「首帧/尾帧」。一期只在 prompt 层做「职责绑定」（图片1=首帧），不声明 API 语义；P6 实测后定字段名。
- R2：firstlast 缺图时 prompt 仍写「图片1/图片2」→ 引用不存在的图，需守卫。
- R3：`_meta` 缺 H3 三件套里的「模型名+画幅」；R6：`_audio_hint` 无对白时仍写「台词=逐字」会诱导幻觉。
- R4：前端 firstlast 触发不能复用 climax 的 `motion>=2` 闸门，要按楼层单独闸门。
- R8：尾帧跨楼层状态用「反查」（倒序找最近 ready video 槽）而非新增持久化，避免快照/分叉/重生成污染。
- 已落地：`services/video_prompt.py`（两套编译 + build_video_request dry-run，纯函数），
  `tests/test_video_prompt.py` 11 passed。提交 `2f276c7`。
- 建议下一步：P3（videoMode/协议/守卫，含 R2/R3/R6/R7 修正）——它是 P1/P2 产出的出口契约。

## V1.5/V1.6 实现进度（deepseek-v4-pro-0813 编码）

**模型分工（用户指定，三档路由，2026-08-26 修正）**：
- glm-5.3（tier:c3）= 极度困难的代码编程方向引导 + 架构文档编写 + 代码审计。
- deepseek-v4-pro-0813（tier:c2）= 实际编码 + 审计后修复。
- deepseek-v4-flash-0731（tier:c0/c1）= 记入记忆 + 读取文档 + 普通对话 + 极其简单的代码编程。

已完成：
- A1 首尾帧双锚点提取 `services/story_frames.py`（纯函数，提交 407fac7）：楼层文本 → 段首+段尾
  双锚点 opening/closing（非单一高潮段）；纯对白段就近借画面段；单段退化静止；空正文降级。
- A2 视频提示词打磨（提交 50e810e）：R2/R3/R6/R7/R9 + 衔接感 + 高潮动作化。
- **防拦截第一层对齐**：`video_prompt.build_video_request` 入口 `_clean_spec` 对 spec 文本字段
  统一 `restore_jailbreak` 兜底。端到端 dry-run 验证：带 `@(色)@` 破甲标记的 appearance
  不再残留进 prompt（修复前会残留）。
- **共享清洗规则落地**：新建 `services/prompt_clean.py`（纯函数：restore_jailbreak/
  restore_jailbreak_with_offsets/clean_spec_text_fields）+ `docs/PROMPT-CLEANING-RULES.md`
  （按用途命名「通用提示词清洗规则」，单一事实来源）。`image_prompt_extract` 与 `video_prompt`
  均改为复用共享模块（image_prompt_extract 内 _MARKER_RE 与 restore_jailbreak* 实现已删除，
  改为 re-export）。独立性保障：删掉 IMAGE_PROMPT 清洗规则，图像生成仍受共享规则庇护
  （test_prompt_clean.py 回归保证）。全量 1640 passed。
- **B1 videoMode + 事件协议（本轮，P3）**：
  - 前端 `MediaInsertPreset.videoMode?: "climax" | "firstlast"`（缺省 climax，旧预设兼容）。
  - 后端 `agent_graph._ordered/_streamed_illustration_events` 透传 rec 里 5 个可选字段
    （video_mode/first_frame_desc/last_frame_desc/prev_tail_desc/last_frame_url，有值才带）。
  - 前端 `chatStreamProtocol` 宽松解码（未知字段忽略）+ `resolveVideoMode` 决策纯函数
    （事件优先→preset→climax）+ `illustrationTemplateValues` 新增 binding
    （video_mode/first_frame_desc/last_frame_desc/prev_tail_desc/last_frame_url）。
  - `useChatSession.submitIllustration` 收 5 个可选参数，仅 useVideo 时透传。
  - 验证：后端 3 透传用例 + 前端 7 用例；全量 后端 1643 / 前端 532 + tsc 0 错。

关键结论（用户拍板）：
1. **防拦截两层机制**（与图像生成一致）：① 破甲还原 @()@→正常文字（纯函数，已落地）；
   ② _apply_regex(placement) 用户正则清洗（接线层）。
   - **第②层决策（用户拍板 2026-08-26 修正）**：**不新增 VIDEO_PROMPT placement**。清洗规则
     从 IMAGE_PROMPT 抽出为单一共享清洗规则文档 `docs/PROMPT-CLEANING-RULES.md`（「通用提示词
     清洗规则」），图像/视频/未来一切提示词共用一份，节省上下文。
   - **机制真相（代码核实）**：@() 还原是共享模块 `prompt_clean.restore_jailbreak`（硬编码
     _MARKER_RE），不是 IMAGE_PROMPT 的能力；IMAGE_PROMPT placement 走 `regex_engine.run_scripts`
     的**任意正则**（findRegex/replaceString/trimStrings，全局库/预设/卡内嵌三层来源），
     能洗任意内容，绝不只是 @()。
   - **独立性保障（落地）**：image_prompt_extract 破甲还原改为 re-export 共享模块，删掉
     IMAGE_PROMPT 清洗规则图像生成仍受共享规则庇护（test_prompt_clean.py 回归）。
   - 视频两套提示词已分开建模：firstlast=剧情完整桥段（七段式时间分镜）、climax=高潮段落
     扩展（精简版动作瞬间），各自独立 compile_*。
2. **A3 表格读取取消**：表格在剧情推进时已自动发送/自动填表，场景角色信息 scene_spec
   已含（appearance/wardrobe/locale），无需单独读 table_store。P2 移除。
3. **B2 尾帧反查（P4）✅**：MsgPart.lastFrameDesc（槽位创建/完成保留）+
   `resolvePrevTailDesc` 纯函数（零持久化，随 chat_snapshot 走，分叉/重生成不污染，R8）；
   submitIllustration 事件无 prevTailDesc 时反查兜底。
4. **B3 前端提交链（P5）可验证核心 ✅**：R4 闸门 `resolveVideoTemplateChoice`（firstlast
   楼层触发不看 motion；climax 维持 smartVideo+motion≥2）+ 双帧图 binding
   （first_frame_image/last_frame_image）+ lastFrameUrl 上传路由 + 尾帧缺省降级首帧单图。
   ⏸「先出双图再提视频」顺序链阻塞：无等待桥（pollResult 是 fire-and-forget）+ 无真实双图
   模板（红线：不猜接口字段名）→ 延 P6。

下一步：P6 真实 API 对齐（需用户提供可实测端点）——落地后可补 P5 顺序链。视频提示词内容
编写（镜头语言 + A/B 动态提取）按用户指示留到后续测试环节逐步排查，不靠模板敲定。
