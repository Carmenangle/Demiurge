# 剧情生成全流程（含生图 / 音频 / 视频）

> 本文从**当前代码**梳理剧情模式的完整数据流，不涉及未来计划。
> 计划/路线见 `ROADMAP-MULTIMODAL.md` 与 `PLAN-VIDEO-FIRSTLAST.md`。
> 核心代码：`backend/app/services/agent_graph.py`（图 + roleplay 节点）、
> `roleplay_agency.py`（能动性子图）、`roleplay_turn.py`（回合事务）。

---

## 0. 一句话总览

```
前端 → POST /api/ai/multi-agent
     → agent_runner.run_multi_stream(RunContext)
     → agent_graph.stream_multi_agent  (LangGraph)
     → supervisor_node 判路由 → roleplay_node 剧情扮演
     → 正文生成后 writeback 同步产出「生图请求 / 音频请求 / 视频提示词」
     → SSE 事件下发 → 前端走 ComfyUI 异步闭环
     → 正文发出后做记忆维护（表格/认知/纪要/知识库）
```

---

## 0.5 端到端时序图（一次剧情轮 + 三功能异步闭环）

```
时间 →   前端(React)                    后端 agent_graph                  LLM / ComfyUI

   │  POST /api/ai/multi-agent
   ├───────────────────────────────►│
   │                                │ supervisor_node 判路由 (1 次快模型)
   │                                ├─────────────────────────────►│  route=roleplay + scene
   │                                │◄─────────────────────────────┤
   │                                │ roleplay_node 组装 system
   │                                │  (世界书 + 角色卡 + state 块 + 记忆召回 + 搭车指令)
   │                                ├─────────────────────────────►│ 主生成 (1 次 LLM)
   │                                │◄─────────────────────────────┤ 正文
   │                                │                               + <illustration> JSON
   │                                │                               + <audio> JSON
   │                                │                               + <状态更新> JSON
   │                                │ _agency_writeback 剥离/校验/组装 ↓
   │◄── trace ──────────────────────┤
   │◄── delta / replace (正文) ──────┤  emit_ready 先发正文
   │◄── illustrate_request ──────────┤  {prompt,motion,actors,anchor,
   │                                │    scene_spec,
   │                                │    video_prompt, video_params}   ← 高潮点生图+视频提示词
   │◄── audio_request ───────────────┤  {lines:[{speaker,text,emotion}]} ← 对白配音
   │                                │ _agency_maintenance 记忆维护
   │                                │  (表格/认知/纪要/知识库, 不阻塞)
   │                                │
   │  ── 前端异步 ComfyUI 闭环（与正文并行）───┤
   │  POST illustration-claim      ├───────────────────────────────►│ 防重认领插画槽
   │  POST illustration-submission ├───────────────────────────────►│ 记录实际出图参数
   │  POST audio-submission        ├───────────────────────────────►│ 逐角色记录配音参数
   │  (可选) illustration-failure  ├───────────────────────────────►│ 失败移除槽
   │◄── done ───────────────────────┤
```

要点：
- 正文与 `illustrate_request`/`audio_request` 同属**即时通道**，先到前端；记忆维护在正文之后，不阻塞。
- 视频不是独立请求：climax 提示词**寄生**在 `illustrate_request` 事件里下发（`video_prompt` + `video_params`）。
- 前端提交（claim/submission/failure）只是**追踪记录**，不回头改正文；ComfyUI 生成结果走独立媒体通道回填。

---

## 1. 入口与路由

### 1.1 入口

- `POST /api/ai/multi-agent`（`routers/ai_agent.py`）
- 请求体 `MultiAgentRequest`，关键字段见文末「开关速查表」
- `agent_request_context.from_payload()` 把 payload 组装成 `RunContext`（dataclass）
- `agent_runner.run_multi_stream(context)` 后台线程运行，`drain(q)` 转 SSE

### 1.2 图结构（`_build_graph`）

LangGraph `StateGraph`：入口 `supervisor` → 条件边按 `route` 分派到单个专家节点 → `END`。

| route | 专家节点 | 说明 |
|-------|----------|------|
| `roleplay` | `roleplay_node` | 剧情扮演（有角色卡默认走这） |
| `answer` | `answer_node` | 普通对话 |
| `generate` | `generate_node` | 文生图 |
| `video` | `video_node` | 独立「生成视频」 |
| `img2img` / `analyze` / `inspire` / `tool_agent` / `edit` | 各专家 | 图生图 / 反推 / 灵感 / 工具 / 编辑 |

### 1.3 路由决策（`supervisor_node`）

1. `workspace_mode == "edit"` → 强制 `edit`。
2. 有关联角色卡（`_has_card`：card_name + character_dir 非空）且无图附件 → 默认 `roleplay`
   （显式强命令如「生成视频」走零 LLM 分派）。
3. 否则交 Supervisor 模型（`_supervisor_route`）做唯一语义判断，产出 `route + scene + confidence`；
   低置信度且有多个候选 → `clarify`（让用户选）。
4. 有关卡的作品里 `answer` 统一并入 `roleplay`（保持人设）。

> 每个普通轮次 Supervisor 都可能额外调一次（快）模型判路由；`scene` 标签复用同一次调用产出，
> 后续驱动「条件选链 + 是否配图」。

---

## 2. 剧情扮演主链路（`roleplay_node`）

`roleplay_node` 内部顺序：

1. **输入清洗**：用户输入走 `Placement.USER_INPUT` 正则。
2. **世界书解析** `_resolve_worldbook`（+ `Placement.WORLD_INFO` 正则）。
3. **角色卡 persona 解析** `_resolve_personas`（首轮只注开场卡，后续按命中角色）。
4. **能动性子图准备** `_agency_prelude`：算 turn、读好感度、组装 state 注入块、
   构建出图 renderer（无卡或无 output_dir → `deps=None`，整条子图静默跳过）。
5. **阶段 A 世界提案** `_agency_propose`（门控，通常关 → 塌回单次 LLM 零额外成本）。
6. **组装 system prompt**：preset（或内置扮演提示）+ 历史多轮 + state 块 + 记忆召回
   （`recall_chronicle` / RAG / 数据表）+ 裁定自主行动 + 搭车状态指令 `<状态更新>`；
   若开插画/音频，追加 inline 计划指令 + 近生成契约 + 音频指令（见 §4）。
7. **调 LLM 生成** `_chat_with_optional_stream`（流式/非流式）。
8. **回合事务** `roleplay_turn.execute_turn` → `finalize_turn`：
   - `writeback`（`_agency_writeback`）→ 产出正文 + 生图/音频/视频请求
   - `apply_output`（`Placement.AI_OUTPUT` 正则）
   - `emit_ready`（`_emit_roleplay_ready`）→ **先发正文 + 媒体任务**
   - `maintain`（`_agency_maintenance`）→ 记忆维护（见 §6）

> 「先发正文再做维护」：正文和插画请求先到前端，维护属于本轮完成边界，
> 避免下一轮读到旧表格/纪要；ComfyUI 走独立通道，不阻塞这里。

---

## 3. writeback：生图/音频/视频请求的组装（`_agency_writeback`）

这是三大生成功能的**唯一组装点**，按顺序：

1. `extract_illustration_plan(reply)`：剥离 `<illustration>` JSON（画面要素），
   解析出 `subjects / visual_facts / composition / camera / art_direction / prompt / motion / actors / aspect_ratio`。
2. 若 `comfy_audio`：`extract_audio_dialogue` 剥离 `<audio>` 块，解析台词 + 8 维情感向量。
3. 抽 `<status>` 快照 + 剥 `<状态更新>` JSON → `roleplay_agency.writeback` 写回角色状态。
4. `narrative_ci.evaluate` 剧情一致性诊断（只评估，不阻断）。
5. 场景判定 `at_climax`（是否高潮点，见 §4.1 触发条件）。
6. 组装 `scene_spec`（narrative/appearance/wardrobe/locale/actors/subjects/visual_facts/
   composition/camera/art_direction/aspect_ratio/profile/negative_prompt…）。
7. 若 `comfy_illustrate`：组装 `illustrate_req`（prompt/motion/actors/anchor/scene_spec/video_config），
   并 **dry-run 组装 climax 视频提示词** `video_prompt.build_video_request(mode="climax")`。
8. 若 `comfy_audio`：组装 `audio_req`（lines）。
9. 返回 `(clean 正文, image_recs, illustrate_req, audio_req)`。

---

## 4. 三大生成功能

### 4.1 生图（插画）

两条路径（互斥，见 `_build_renderer`）：

| 路径 | 开关 | 机制 |
|------|------|------|
| 同步 renderer | `illustrate=true` | 云端模型同步付费出图（需 gen_base+gen_model），走能动性 D 阶段 `maybe_illustrate` |
| 异步 ComfyUI | `comfy_illustrate=true` | 高潮点发 `illustrate_request` 事件，前端按本地预设模板走 ComfyUI 闭环（**推荐，后端不再同步 render**） |

**触发条件（`at_climax`，comfy_illustrate 路径）**：存在 `<illustration>` 计划，或
（正文非空且命中以下之一）：
- 配角强得手导致用户失控（`lost`）
- scene 判定为 `nsfw/climax`
- 本地场景兜底 `local_scene_fallback`
- 主模型漏计划兜底 `missing_plan_fallback`
- 首轮回复 `first_story_reply`
- 角色初登场 `character_encounter`

**一致性机制**：图片提示词用主模型**同轮**提炼的画面级要素（subjects/visual_facts/composition/camera），
`visual_facts` 的 `evidence` 必须逐字命中当前正文才保留——保证画面锚定当前剧情。

### 4.2 音频配音（IndexTTS）

开关：`comfy_audio=true`。

1. roleplay_node 注入 `build_inline_audio_instruction()`，要求模型输出 `<audio>` 块（台词 + 情感向量）。
2. writeback 里 `extract_audio_dialogue` 解析；解析失败/无块 → `build_fallback_dialogue`（正文机械抽取兜底）。
3. 组装 `audio_req = {lines:[{speaker,text,emotion}]}`。
4. 下发 `audio_request` 事件 → 前端逐角色提交 IndexTTS。

> 音频与插画正交：可只开配音不开图。

### 4.3 视频

四种形态：

| 形态 | 触发 | 状态 |
|------|------|------|
| 独立「生成视频」 | 用户明确说生成视频 → `video_node`（可带首帧图） | 已接线，走 `generation_approval.execute_generation` |
| 剧情高潮点 climax | 高潮点随 `illustrate_request` 事件下发 `video_prompt + video_params` | **已 dry-run 接线**（`build_video_request(mode="climax")`），前端用视频模板生成 |
| firstlast 首尾帧剧情影片 | 剧情楼层首帧+尾帧双图 | 已接线：produce 层按 `video_mode` 编译（`compile_firstlast_video_prompt`），随事件下发 |
| transition 转场视频 | firstlast 且首帧需独立生成（transition≠reuse） | 已接线：produce 层编译 `transition_video_request`（图片1=上尾帧、图片2=当前首帧），随事件下发 `transition_video_prompt/params`，前端 2 任务排队先提转场再提正片 |

climax 视频提示词区块：`元信息 / 风格 / 参考绑定 / 主体场景 / 动作 / 负面约束`，
动作桥段优先用画面级要素（subjects/visual_facts/composition），缺失回退 narrative。

### 4.4 三条支线信息流：输入 → 处理 → 输出

#### 生图（comfy_illustrate 路径）

- **输入**：
  - 主模型同轮 `<illustration>` JSON：`anchor / subjects / visual_facts / composition / camera / art_direction / prompt / motion / aspect_ratio`
  - 角色上下文：`appearance`（世界书/角色卡）、`wardrobe`、`locale`（数据表）、角色全集 `_known`、出场角色 `present`
- **处理**：
  1. `extract_illustration_plan`：剥离 JSON，校验 `visual_facts.evidence` 必须逐字命中正文（否则丢弃）→ 保证画面锚定当前剧情
  2. `scene_classify` 场景分类 + `narrative_ci` 一致性诊断（只评估不阻断）
  3. 判定 `at_climax`（是否出图）
  4. `_resolve_illustration_request_actors`：planned + 用户输入 + narrative + 出场 + 初登场 合并去重
  5. 组装 `request_prompt`（画面级要素拼装）+ `scene_spec`
- **输出**：`illustrate_request` 事件 `{prompt, motion, actors, anchor, scene_spec, video_prompt, video_params, id}`
  → 前端拿 `prompt + actors` 选 LoRA/底图，填 ComfyUI 工作流出图。

#### 音频（comfy_audio 路径）

- **输入**：主模型同轮 `<audio>` JSON `{lines:[{speaker,text,emotion:{8维}}]}`；兜底用 `card_names`
- **处理**：
  1. `build_inline_audio_instruction` 注入指令，要求模型输出 `<audio>` 块
  2. `extract_audio_dialogue`：剥离 JSON，`speaker/text` 还原防拦截标记，`emotion` 8 维归一化到 0~1，全 0/无效 → 回退 `Neutral=1`
  3. 漏块/解析失败 → `build_fallback_dialogue`（从正文机械抽取「角色名：台词」兜底）
- **输出**：`audio_request` 事件 `{lines:[{speaker,text,emotion}], id}`
  → 前端逐角色提交 IndexTTS（speaker→音色 reference，emotion→情感向量）。

#### 视频（climax 路径）

- **输入**：`scene_spec`（narrative/appearance/subjects/visual_facts/composition/camera/actors/rating/aspect_ratio）+ `motion` + `video_config`（vid_base/vid_model/size=1280x720/proxy）
- **处理**：
  1. `_merged_spec = scene_spec + motion`
  2. `build_video_request(mode="climax")` → `compile_climax_video_prompt` 编译七区块：
     元信息（时长/画幅/模型）+ 风格 + 参考绑定（图片1 职责 + 锁身份）+ 主体场景 + 动作（画面级动作瞬间 + 运镜 + 特效）+ 负面约束（去重）
  3. 抽 `video_params`（model/size/endpoint/images/reference_binding/warnings）
- **输出**：随 `illustrate_request` 事件下发的 `video_prompt`（提示词全文）+ `video_params`
  → 前端用视频模板生成；缺图时 `warnings` 诚实标注「缺高潮参考图」。

---

## 5. 事件下发与前端闭环

### 5.1 后端 SSE 事件（`stream_multi_agent` yield）

| 事件 | 含义 |
|------|------|
| `trace` | 节点流转（🎭 剧情扮演中… 等） |
| `delta` / `replace` | 正文（流式/整体） |
| `route` / `route_choice` | 路由/用户选择卡 |
| `image` / `video` | 同步出图/视频结果 |
| `illustrate_request` | 高潮点出图请求（含 prompt/motion/actors/scene_spec/video_prompt/video_params） |
| `audio_request` | 对白配音请求（lines） |
| `rag_status` | 纪要/知识库创建状态 |
| `approval` / `error` / `done` / `interrupted` | 审批/异常/结束/打断 |

### 5.2 前端提交（`routers/ai_agent.py`）

- `POST /image-agent/illustration-claim`：提交前认领权威插画槽（防重复）
- `POST /image-agent/illustration-submission`：记录最终提交 ComfyUI 的插画参数
- `POST /image-agent/audio-submission`：记录音频配音参数（台词/音色/情感）
- `POST /image-agent/ensure-audio-slot`：音频对白槽补写快照
- `POST /image-agent/illustration-failure`：记录失败并移除槽

---

## 6. 记忆维护（正文发出后，`_agency_maintenance`）

失败只记 Trace，不阻断正文：

1. `_table_maintenance`：表格更新（只读注入 → 正文后独立维护）
2. `_belief_maintenance`：角色认知变化抽取（纯规则，0 额外 LLM）
3. `maybe_summarize`：每 N 轮抽一条独立纪要（layer0，超上限再压缩归并）
4. `maybe_curate`：门控从剧情抽「值得长期留存的新知识」写入 RAG 知识库 + 世界书（只增不改）

---

## 7. 开关速查表（`MultiAgentRequest` → `RunContext`）

| 字段 | 默认 | 作用 |
|------|------|------|
| `card_name` / `character_dir` | "" | 关联角色卡（有卡才走 roleplay） |
| `illustrate` | false | 剧情插画开关（同步云端 renderer 路径） |
| `comfy_illustrate` | false | 已预设 ComfyUI 图/视频模板：高潮点发 `illustrate_request` 异步闭环 |
| `comfy_audio` | false | 已预设音频模板（IndexTTS）：发 `audio_request` 逐角色配音 |
| `prompt_profile` | krea2 | 自动插画提示词模式（主 Roleplay 同轮成稿） |
| `appearance_source` | worldbook | 角色外貌来源：worldbook / character_card |
| `gen_base_url` / `gen_model` | "" | 生图模型（同步 renderer 路径用） |
| `video_base_url` / `video_model` | "" | 视频模型（`vid_base/vid_model`，climax 视频提示词 + 独立视频专家用） |
| `illustration_actor_names` | [] | 自动插画可识别的已配置角色名（LoRA/底图选择真源） |
| `preset_name` | "" | 激活偏置预设（空=内置扮演提示） |
| `worldbook_name` | "" | 绑定独立世界书（与卡内嵌世界书合并注入） |

**最少开启配置**（剧情 + 三功能）：
- 剧情：`card_name` + `character_dir`（+ `preset_dir` 可选）
- 生图：`comfy_illustrate=true`（前端已配好 ComfyUI 图模板）
- 音频：`comfy_audio=true`（前端已配好 IndexTTS 模板）
- 视频：`video_base_url` + `video_model`（高潮点自动出 climax 提示词；或前端视频模板）
