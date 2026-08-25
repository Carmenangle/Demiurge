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
