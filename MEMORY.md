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

**模型分工（用户指定，2026-08-28 重新拍板，取代 2026-08-26 三档分工）**：
- glm-5.3（tier:c3）= 代码审计 + 修复审计出的漏洞 bug + 整体方向引导 + 架构文档创建
  + 正常对话 + 文档读取与创建 + 记忆更新。
- deepseek-v4-pro（用户口述 0831；当前路由表实际为 deepseek-v4-pro-0813，tier:c2）=
  代码编程（新功能/常规实际编码；审计发现的 bug 修复归 glm-5.3，不归 deepseek）。
- glm-5.3-flash（tier:c0/c1）= 本轮未点名；原「记忆/文档/对话/简单编程」分工中对话、
  文档、记忆已划归 glm-5.3，flash 仅余轻量兑底。

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
5. **默认开放 climax 视频提示词 + 视频参数 dry-run（用户指示测试点 ✅）**：视频模型/工作流
   都不用准备，剧情推进高湖点即用 `video_prompt.build_video_request` 完整组装
   「上交视频模型的参数」（`agent_graph._video_request_for`；scene_spec+motion 喂
   compile_climax_video_prompt；video_config 随 illustrate_req → rec 白名单透传）。
   随事件下发 `video_prompt`（提示词）+ `video_params`（model/size/endpoint/images/
   reference_binding/warnings，可核对「参数有没有上传」）；前端解码存槽位。探针
   `backend/scripts/b2_video_prompt_probe.py` 三档 motion 输出供人工核对。
   验证：后端 1652 / 前端 551 + tsc 0 错（+ video_config 透传 / video_params 线编码 /
   roleplay 白名单用例）。
   🆕 trace 可核对：produce 层编译 video_request 存进 illustrate_req → rec 白名单透传，
   事件层直接复用；`illustration.request` trace 新增 video_prompt 全文，agent-trace.jsonl
   可核对视频提示词（后端 1653）。注意：非高潮轮（如 <status> 状态轮）不触发插画 → 无
   illustration.request → 无 video_prompt，属预期。
   🆕 画面级要素优先（2026-08-26 用户反馈「角色不对/提示词对不上剧情」）：climax 的
   [动作] 桥段原先直接用中文 narrative（围绕 anchor 截取，anchor 陈旧时会截取到错误桥段），
   改为优先用主模型同轮提炼的画面级要素（subjects/visual_facts/composition，与图片提示词
   同源），camera 优先、motion 兜底运镜；[参考绑定] 图职责描述与 build_video_request 的
   desc 同样用画面级动作瞬间兜底（不再硬传 `first_frame_desc or "高潮动作画面"`）；produce
   层/`_video_request_for` 的 first_frame_desc 留空。test_video_prompt +3 用例，全量 后端
   1656 / 前端契约 14。注意：角色不对（问题1）根因在插画角色识别层（`_mentioned_bound_names`
   纯子串匹配 + `illustration_actor_names` 可能混入作品名/道具），非视频提示词桥段，本轮未改。

下一步：用探针输出人工核对 climax 提示词质量 + 视频参数（model 空=未配视频模型、
images 空=参考图待上游补、warnings 缺图守卫）；之后按反馈逐步排查内容编写。P6 真实
API 对齐待用户提供可实测端点；配好视频工作流后改回真正执行 submit。

## W3 转场视频任务编排（已完成，2026-08-26，deepseek-v4-pro-0813 编码）

P5 首尾帧顺序链解除阻塞后，W3「2 任务排队」前端接线落地：

- **前端→后端 video_mode**：`AgentInvocation.videoMode`（`climax|firstlast`）新增 wire 字段
  （`agent-invocation.schema.json` → `agentInvocationBody` → `RunContext.video_mode`），
  produce 层据此编译正片 video_request（缺省 climax，旧预设兼容）。
- **后端转场编译**：firstlast 且 `transition≠reuse` 时额外 `build_video_request(mode="transition")`
  → `transition_video_request` 随事件下发 `transition_video_prompt` + `transition_video_params`
  （图片1=上尾帧/起点、图片2=当前首帧/终点；坑G 转场时长走 `preset.transitionDurationHint`，
  缺省交模型默认，绝不兑底正片时长）。
- **前端 2 任务排队**：`uploadRemoteImageToInput` 取上尾帧图（localViewUrl）回传 ComfyUI input
  （坑F 缺图 → 降级文字转场不拦截）；`appendTransitionSlot` 追加独立槽（slotId=`<id>:transition`）；
  `transitionVideoValues` 图片1/2 绑定；先提转场视频、正片随后进队列顺序执行，转场失败仅标
  槽位失败不挂死正片。`VideoParams.mode` 类型扩为 `climax|firstlast|transition`。
- 尾帧图地址走前端 `resolvePrevTailDesc` 反查（lastFrameUrl，R8），后端不赋值 `last_frame_url`。

验证：后端 1699 passed / ruff ✅ / mypy ✅；前端 vitest 578 passed / tsc ✅ / 生产构建 ✅；
`check:wire` 同步 ✅。文档同步：`PLAN-VIDEO-FIRSTLAST.md` W3 标 ✅、`STORYGEN-FLOW.md` 视频四形态。
剩余（P6）：真实双图视频模板字段名 + 真实 API 对齐，待用户提供可实测端点。

## 视频提示词绑定点 + 生图生命周期加固（2026-08-27，用户拍板）

1. **climax 参考绑定只声明角色，不说「高潮动作画面」**：`video_prompt._reference_binding_climax`
   由「图片1=X的高潮动作画面（唯一参考画面，作为准确起始帧）」改为「图片1角色为 A、B」——
   「高潮动作画面」这类画面职责描述会作为干扰词回流，画面细节只在 [动作] 桥段承载；无在场角色
   才退回 `_climax_frame_role` 职责占位描述。
2. **角色提取漏人守卫**：新增 `_unbound_person_refs` / `_action_source_text`（纯函数）——动作/叙事
   里出现未绑定人称（Man/He/the woman/他/她…）时，climax 绑定补「画面另含未绑定角色」提示，
   `build_video_request` 同时落 `warnings`。根因是男方角色无角色卡/LoRA，不进 actors。
3. **ComfyUI 轮询区分「有动弹/无动弹」**：后端 `comfyui_client.fetch_result` 队列查询时
   `queue_running`→`status=running`（节点已开始运转）、`queue_pending`→`status=pending`（仍在排队）。
   前端 `WorkflowGenerationRuntime` 加停顿守卫（`DEFAULT_STALL_TIMEOUT_MS=5min`）：一直 pending、
   从未观察到 running 就早停（`observer.stalled`），不再死等到 20/60min 硬超时。
4. **图片生成位置下方「停止」键**：`useChatSession.stopSlotGeneration`（中断 ComfyUI + cancel 守望 +
   `failMediaSlot` 原位标 failed），`ChatMessages` media-slot pending 态渲染停止键。
5. **重新生图原位替换**：`regenerateResult` 的 `ai-image` 分支由 `upsertMessages` 新发消息改为按
   messageId 原位替换 `image/regeneration`，不再往剧情对话里插一条新消息。

关键文件：`video_prompt.py`、`comfyui_client.py`、`workflowGenerationRuntime.ts`、
`useChatSession.ts`、`chatSessionEvents.ts`、`ChatMessages.tsx`、`ChatView.tsx`。
门禁：后端 1719 passed / ruff ✅；前端 vitest 580 passed / tsc ✅。

## 视频提示词七段式 + 参考绑定措辞（2026-08-28，用户拍板，覆盖旧「climax 精简版」决策）

用户对 climax 视频提示词做结构评审后拍板三条，全部落地 `video_prompt.py`：

1. **climax 也走完整七段式**（推翻 2026-08-25 定稿「climax 精简版、禁止时间分镜」）：
   ① 元信息 ② 风格声明 ③ 参考绑定 ④ 主体/场景 ⑤ 时间分镜 ⑥ 音频 ⑦ 负面约束。
   顺带统一三模板段标签：首行 `[元信息]：…`、`[风格]→[风格声明]`；climax 补 `[音频]`。
2. **`[动作]`（定格起点/延伸/收尾长句）→ `[时间分镜]`**：`0–Xs / X–Ys / …` 逐拍切段，
   每段 = 节奏名 + 运镜 + 主体动作 + 特效 + 节拍同步 + 身份锁；按 `action_sequence` beat 数
   均分，无序列则单段「主体动作」。新增 `_climax_time_segments`/`_climax_beat_sync`，
   `_climax_fx` 改为纯特效。
3. **参考绑定「图片1角色为 X」→「图片1中心的角色为 X」**；删除「画面另含未绑定角色…」
   提示 + `_unbound_person_refs`/`_action_source_text` 漏人守卫与 `build_video_request` 的
   unbound warning（推翻 2026-08-27 漏人守卫拍板）——只需声明「图片X中心的角色为 X」，
   无名配角由模型按画面自行区分。

另：climax 元信息在 `has_frame`（有参考图）时加「使用输入图片作为准确起始帧」（I2V 必加）。

验证：`test_video_prompt.py` 48 passed；全量后端 1719 passed；前端契约 6 passed；
`b2_climax_video_prompt.json` fixture 由 probe 脚本重生成（七段式）。

## 代码审计：SSRF 重定向三处修复（2026-08-28，glm-5.3 审计+修复）

审计范围：后端全部路由/服务（subprocess 均为列表参数无 shell 注入；无 eval/exec/pickle；
project_files 路径围栏、node_update requirements 来源审查、install_target 域名+目录双重校验、
能力租约 capability_sandbox 均完好）。确认并修复「后端主动 fetch 用户 URL」家族同一类漏洞：
**首跳校验通过后下载器自动跟随重定向且重定向目标不校验 → 外部 URL 可 302 跳私网/metadata
地址把内网响应拉回本地**。

1. `image_store._from_src`（高危）：validate 后用默认 urlopen 下载（自动跟随重定向）。
   新增 `_download_external_url`：httpx `follow_redirects=False` + 逐跳 `validate_media_url`
   （校验通过才发下一跳，上限 5 跳），loopback local-view 豁免保留。
2. `image_proxy.fetch_remote_image`（中危，TOCTOU）：`follow_redirects=True` 先请求后审计
   history——对内网请求已发出。改为逐跳校验同款合同。
3. `visual_ci._to_data_uri`（中危）：http(s) 直拉完全不校验。补 `validate_media_url`
   （local-view 豁免）+ 单跳重定向校验。

不变量（以后再改不要破）：**「校验通过才发下一跳请求」**——任何新的后端取外链代码必须
`follow_redirects=False` + 每跳校验，禁止「先请求后审计 history」。

残余风险（记录在案，未改）：`model_downloader`/`workflow_downloader` 的下载 URL 已白名单
huggingface.co/civitai.com，但重定向未逐跳校验（需这两个域名存在 open redirect 才可利用，
且响应仅落盘不回显，影响有限）；`local-view` 按设计可读任意媒体扩展名文件（loopback 门禁
+ 扩展名白名单是边界）；SVG 在 `_MEDIA_EXTS` 白名单内，直接导航可能执行内嵌脚本
（单机 loopback 场景风险低）。

回归：新建 `tests/test_ssrf_guard.py`（7 用例：私网/环回重定向拒第二跳、公网链放行、
循环超限、visual_ci 直连拒绝）；`test_image_proxy.py` 重定向用例改为 TOCTOU 断言。
门禁：后端全量 1730 passed / ruff ✅ / mypy ✅。

## 视频提示词链四项修复 + 三模态独立开关（2026-08-28 晚，glm-5.3 审计+修复）

本轮四连修（根因均在 video/agent_graph 链），全部有回归测试：

1. **防拦截对齐生图链（两层）**：`_extract_video_action_plan`（agent_graph）此前裸调 LLM——
   system 不挂预设、输入用还原正文、输出无过滤，拒答句「我不能协助这项请求」直接流进
   [时间分镜]。修复：① 输入层用 `protected_narrative`（防拦截原文）+ `system_with_preset`
   挂当前防拦截预设（task_label=内部视频提示词任务）；② 输出层 `video_prompt.parse_video_plan`
   逐字段拒答过滤（desc/subject_scene/music/sfx/sync 丢弃，**lines 台词原文不过滤**——
   「我不能满足你」这类正常对白必须保留）；③ 整体拒答带原因重试一次，仍败回 {} 纯函数兜底；
   ④ `image_prompt_extract` 内联 <illustration> 的 action_sequence 同款过滤；
   ⑤ `prompt_clean` 收口 REFUSAL_RE/strip_refusal_suffix 单一来源。
   实测 19:00:33 新生成：8 拍分镜 + 7 条具体音效 + 3 句当下台词，strategy=same_turn。

2. **镜头语言词汇表（用户文档驱动）**：`D:\Study\镜头语言学习\`（镜头运动.md 26 种 +
   镜头角度.md 9 种，每种带标准「提示词：」句式）。video_prompt 自造词「极缓推进」「低机位
   快速丝滑运镜」全删，改 `_CAMERA_ESTABLISH/_MIDDLE/_SOLO/_CLOSE` 常量（句式=文档原文），
   `_beat_camera` 按拍位分配：开场定场（固定/缓推）→中段循环换镜→收尾拉远/骤停；
   剧情句含场景切换 cues（另一边/来到/走进/片刻后/此时/镜头一转/回到/转场）自动改
   遮挡揭示/甩镜头转场。**不变量：新增运镜必须来自该文档句式，不自造**。

3. **身份锁去重**：「人物身份和五官不能发生变化」从每拍尾部移除，`_negative` 统一追加进
   [负面约束] 仅一次（精确匹配 _IDENTITY_LOCK，勿用含「五官」的宽匹配——会撞上用户自定义
   「禁止五官漂移」而漏加）。旧测试 test_firstlast_identity_reasserted_per_segment 已改语义。

4. **firstlast 逐句成拍**：`_time_segments` 从「固定 3 段均分」改为剧情逐句切拍
   （`_split_narrative_beats`：。！？；\n 切句 + 引号句合并归属；首帧衔接+尾帧收束；
   引语拍标「台词随口型同步」；每拍独立 `_beat_camera` 运镜）。climax 侧 `_climax_time_segments`
   每拍也改 `_beat_camera`（原单句 cam 全程复用）。

5. **高潮台词时点约束**：用户发现 [音频]台词 是「已过去」的对白。根因：提取 prompt 无时点
   约束，LLM 把高潮前文对白也摘进来。修复：`_extract_video_action_plan` system 规则 3 加
   「只列高潮片段当下（0–15s 画面此刻）亲口说出的台词；此前对话/回忆/旁白转述一律不列」。
   治本是事实先行管线（offset_s 取数），P1 待做。

6. **三模态独立开关 comfy_video（用户要求「关=零 token」）**：查实图链 comfy_illustrate/
   音链 comfy_audio 本就条件 gate 零成本；视频链寄生图链——illustrate_req 一存在就编译
   video_request + 每次高潮调一次提取 LLM，干烧 token。修复：
   - schema `comfy_video: boolean`（agent-invocation.schema.json，保留原紧凑格式！勿整文件
     json.dumps 重写——会展开成 300 行 diff）→ `scripts/generate_wire_contracts.py` 再生成
     前后端契约；
   - 后端 agent_contracts（字段+legacy dict）/agent_request_context（payload 解析）/
     agent_runner（trace）/ai_agent router/agent_graph（`if illustrate_req and ctx.get("comfy_video")`
     gate 在视频编译整块外）；
   - 前端 useChatSession：`comfyVideo = !!(settings.illustrate && mediaPreset?.videoTemplateId)`
     （没配视频模板自动关）；ai.ts 接口+body；
   - **事件层 `_video_request_for` 兜底编译保留**（rec 没带 video_request 时现场编译）——纯函数
     零 LLM token，只影响 trace 观测；token 大头在 produce 层已被 gate。
   门禁：后端 178（runner/dispatch/protocol/roleplay_turn）+ gate 2 用例 / ruff ✅ /
   wire --check in sync；前端 tsc ✅ / vitest 契约 3 passed。

**事实先行管线规划（用户思路，P1-P3 待做）**：正文前先出 story_facts JSON（scene/climax
anchor/present[服装外貌]/beats[offset_s+action+camera+transition]/dialogue[offset_s]/
audio[offset_s]），注入正文 system 当硬约束，下游从表编译而非从正文抽取。挂点：
`_draft_story_facts`（复用 _extract_video_action_plan 的二段调用模式）；visual_facts 的
fact+evidence 就是雏形；thinking_chains 可加「导演推演」head 链（预设层唯一值得改的）。
_INLINE_PLAN_INSTRUCTION 4000 字合同四活一身（正文+艺术决策+导演决策+英文翻译）是
local_fallback 占比高/正文缩水的结构性根因，P0 瘦身方向已与用户对齐。

**遗留（下会话可直接接手）**：
- 18:44–18:47 trace 批次是旧缓存字节级重放（28/28 identical 16:45–16:47），重放路径
  直接重发修复前 video_prompt 缓存，未重走新链——是否强制重新生成未拍板。
- [音频] 音效具体化依赖提取 LLM 成功；strategy 大量 local_fallback（25/31），生图链
  LLM 兜底触发率待查。
- 19:00 生成后 visual.ci warn + agent.error（manual_backfill_required）未排查。
- 本轮全部改动（防拦截+镜头表+身份锁+firstlast+时点+comfy_video）**尚未 commit**，
  工作区还混着 SSRF/前端协议等更早改动，整理 commit 时需分组。

**交接验收（2026-08-28 晚续，glm-5.3）**：

门禁复跑全绿：后端 pytest 全量 1740 passed / ruff 通过 / wire --check in sync；
前端 tsc 通过 / 契约 vitest 9 passed。开关语义两路验证：① gate 2 用例
（关=零提取调用+零 video_request+trace 空；开=恢复编译）；② trace 文件里同一测试
19:36（gate 前，video_prompt 227 字符被编译）vs 20:24（gate 后，0）。
镜头表/身份锁用 19:00:33 同款 spec 在当前代码下 in-process 编译核验：
8 拍运镜 5 种轮换（定场→摇/拉焦/手持循环→拉远收束）、「极缓推进」绝迹、
身份锁全文仅 1 次且在 [负面约束]。

**19:00:33 trace 实为修复中段产物，验收结论需修正**：该生成里运镜仍是
「极缓推进」全程复用、身份锁每拍重申——即镜头表/身份锁修复落地**之前**
的代码所编译（提取 LLM 与音频时点已是新链，8 拍+7 音效+3 句当下台词属实）。
19:00:33 之后至 20:24 的 trace 全是 pytest 批次（「继续剧情→最终正文」x3 +
20:23:59 全量门禁），**没有任何修复后代码的真实生成**——#2/#3/#4 的
运行时验收只能靠 in-process 编译 + 单测，真实生成待下次使用时自然覆盖。

本轮验收发现并已修（各带回归测试）：
1. **firstlast 引号句合并归属此前未实现**（本段原文写的 _split_narrative_beats
   函数并不存在）：「她低声说「开饭了，都过来！」沈糯放下筷子。」被 ！ 切碎成
   「…都过来；台词随口型同步」+ 孤儿「」沈糯放下筷子」两拍。现补
   video_prompt._split_narrative_beats：引号内分隔符不切拍、闭引号后紧跟
   正文（字母/汉字）即断拍、剔除整句裸拒答（引号内对白不动，守「台词不过滤」）。
   _time_segments 与 _climax_fallback_beats 两处切分收口到该函数。
2. **纯函数兜底路径拒答泄漏**：narrative 本身是拒答句（主模型拒答当正文，
   提取 LLM 失败/关闭回退纯函数时）会原样编进 [时间分镜]
   （trace 19:36 实证「我不能协助这项请求。」进 [时间分镜]）。现
   _split_narrative_beats 剔除裸拒答句后回退「主体动作按剧情自然演变」。
   注：19:36 批次本身是测试运行（合成数据），但泄漏路径真实可达。
3. **台词时点（用户审查指出）**：[音频] 台词只有「谁说了什么」没有「什么时候说」——
   提取协议 lines 增加 at_s（提取 LLM 按剧情位置推算该句在本段窗口内的说出时刻，秒；
   推算不了才省略、禁止全标 0），parse_video_plan 数字/数字串透传（bool/乱串丢弃），
   _audio_hint 渲染成「{t}s｜说话人：台词」；缺失诚实省略（comfy_audio 兜底台词不受影响）。
   回归：test_parse_video_plan_at_s_only_numeric_passes + test_audio_design_lines_carry_plot_timing
   + 提取 system 断言（at_s/按剧情位置推算）。

**台词时点语义再定稿（用户审查，2026-08-28 深夜）**：at_s 只属于 firstlast——
高潮定格时刻角色对白通常已说完，climax 视频动作窗口（0–15s）内**根本没有对白**；
只有首尾帧影片从头到尾覆盖剧情，才需要含本段**全部**对白并按剧情位置标 at_s。
落地：① 提取协议按 video_mode 分支（climax：lines 一律留空数组，声音只进 sfx；
firstlast：列出从头到尾全部亲口台词 + at_s 按剧情位置推算）——_video_mode 判定
提前到提取之前并传入 _extract_video_action_plan(ctx, spec, video_mode)；
② compile_climax_video_prompt 强制丢台词（_audio_hint include_lines=False，
audio_design.lines 与 comfy_audio 兜底双来源一律不列）；③ firstlast 渲染不变
（{t}s｜说话人：台词，缺 at_s 诚实省略）。
**多元数据插入 UI 同步定稿**：图片生成功能加「首尾帧生成」选项（mediaPreset.firstlast，
旧预设按 videoMode==="firstlast" 回填）+ 既有「生成视频」开关，视频模式由选项推导：
firstlast 开 → firstlast 剧情影片，否则 climax 高潮点动作代入。resolveVideoMode
优先级：事件 > preset.firstlast > 旧 videoMode > climax；useChatSession 发送
video_mode 按推导值。回归：climax 丢台词（双来源）、firstlast 全对白带时点、
提取两分支规则断言、resolveVideoMode 推导 contract 测试；后端 193 定向 + 前端
584 + tsc + build 绿（全量后端随门禁跑）。

**仍未修（待拍板）**：镜头表里「镜头快速推近主体后骤停」是自造词
（「推近」「骤停」均不见于两文档；_CAMERA_MIDDLE[3] 与 _CAMERA_CLOSE[3] 在用），
违反「不自造」不变量；候选替换：文档「甩镜头」句式或「低弧绕行」快速档。
「低角度仰拍，摄像机以快速弧线围绕主体运动」属文档组合（仰拍+低弧绕行快速档），算合规。

**两个已知项排查结论（均非本轮回归）**：
- visual.ci warn（amber）：19:03 与更早 16:18/16:42/16:44 共 10 条同形——
  vlm_ok=null（未配 VLM，语义审计没跑）、similarity=0（无参考图）、
  failed=[character_identity,action,scene] 是英文关键词启发式对中文 narrative
  恒 miss + mechanical 里 checkpoint/sampler/steps 恒空（illustration.submitted
  未带这些字段，loras 字段名曾因此修过一轮）。amber 是设计内非阻断诊断，
  修复方向（英文启发式改中文/接 VLM）另立任务，不阻塞本轮。
- agent.skipped(manual_backfill_required)：chronicle 纪要代理的门控跳过
  （roleplay_agency.maybe_summarize：turn-last>cadence 即要求手动补纪要；
  本仓 last_turn=0、turn=24），8/12 起每 cadence 必跳，属积压欠账非新故障。
  19:06 的两条 agent.error 是 table_maintenance/curator 的 Connection error
  （上游模型瞬断，与视频链无关）。

## 资产库「清理裂图」失明修复（2026-08-28 深夜）

**现象**：生成内容网格能看到裂图，点「清理裂图」却提示「没有发现裂图记录」。

**根因**（直接查 chroma.sqlite3 逐条核对 198 条 generation 得证）：
- 196 条 local-view 条目磁盘文件全部存在、无 0 字节坏文件，prune 判定本身没错；
- 真裂图 = 2 条 **legacy remote-view 直链**（/api/comfyui/view?filename=...&url=<comfyui>，
  早期未落盘留存的生成），文件已被 ComfyUI 输出清理，直连 8188 均 404；
- rag_store._local_path_of 只认 local-view 形态，remote-view 被当「外链无法判定」永久跳过；
- 另：doPrune 把每仓库请求异常静默吞掉（total=0）——后端故障也会伪装成「没有发现裂图记录」；
  comfyui_client.fetch_view 把一切异常（含 404）映射成 502，代理状态码不可用于判定。

**修复**（不变量：无法判定存在性一律保留，不误删真源）：
- comfyui_client.probe_view：GET /view 三态探测——200=ok / 404=missing /
  其余状态、网络异常、非法 url（不过 validate_comfyui_url 白名单）=unreachable；
- rag_store.prune_missing_generations：local-view 仍按磁盘判定；remote-view 直链
  仅在 probe 明确 404 时删，unreachable 一律保留；
- 前端 RepoGallery.doPrune：请求失败的仓库单计 failed，报「N 个仓库请求失败」，
  不再伪装成「没有发现裂图记录」。
- 回归测试：test_prune_deletes_legacy_remote_view_only_when_comfyui_confirms_missing、
  test_prune_keeps_legacy_remote_view_when_file_exists_or_cannot_judge、
  test_probe_view_maps_status_and_never_raises（404 判定走 ComfyUI 本体真值，
  与浏览器展示同源）。
- 门禁：ruff / import-linter 17 kept / mypy 40 files / 硬编码门禁（顺手把
  image_gen.py:127、image_store.py:98 两行注释里的 127.0.0.1:8010 字面量改指
  config 的 BACKEND_BASE_URL，既有告警清零）/ 后端全量 **1751 passed**；
  前端 tsc + vitest + build 绿。
- ARCHITECTURE.md「裂图清理」约束句已同步更新语义。
