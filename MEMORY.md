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
