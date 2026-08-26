# V1.5/V1.6 首尾帧视频链路 —— 实现计划与风险审计

> 状态：规划审计稿（2026-08-26，glm-5.3 规划）
> 上游：`docs/ROADMAP-MULTIMODAL.md` V1.5/V1.6（方案定稿）
> 已有代码：`backend/app/services/video_prompt.py`（两套提示词编译 + 参数组装 dry-run，纯函数）
> 本文目标：把「未来要构建的内容」拆成可独立验证的分期，并对每块做隐患审计，
> 让后续实现**不猜接口、不破坏纯函数边界、不引入跨楼层状态污染**。

---

## 0. 红线（实现前必读，违反即返工）

1. **不猜接口字段名**：`image[]` 双图的「首帧/尾帧」语义未经真实 API 实测前，只能当
   「职责描述绑定」（prompt 里写「图片1=首帧」），**不得**对外声称 API 层已支持首尾帧。
   —— 这是项目既有教训（`/models` 成功 ≠ 推理可用；参考图字段名没有事实不对齐）。
2. **纯函数边界**：`video_prompt.py` 不 import `agent_graph` / `image_gen` / LLM / 网络，
   对标 `scene_illustration` 的可独立单测边界。锚点提取、表格读取也各自做成纯函数。
3. **旧预设不迁移不报错**：`videoMode` 缺省 = `climax`；新增可选协议字段旧前端必须能忽略。
4. **climax 禁止时间分镜、firstlast 必须有时间分镜**：两套模板语义不可混，代码层分开（已分开），
   前端选择逻辑也必须分开（见风险 R4）。

---

## 1. 现状盘点（已落地 vs 待建）

| 能力 | 状态 | 落点 |
|------|------|------|
| 两套提示词编译（climax 精简 / firstlast 七段式） | ✅ 已做 | `video_prompt.compile_*` |
| 参数组装 dry-run（职责描述 / 图地址两层分离） | ✅ 已做 | `video_prompt.build_video_request` |
| 首尾帧双锚点提取（开头画面 + 结尾画面） | ❌ 待建 P1 | 楼层文本 → `{opening, closing}` |
| 表格素材读取（重要角色表 / 全局表 / 任务表） | ❌ 待建 P2 | `table_store` 列名容错读取 |
| `videoMode` preset 二选一 + 事件协议扩展 | ❌ 待建 P3 | `MediaInsertPreset` + `illustrate_request` |
| 尾帧链式状态（上楼层尾帧） | ❌ 待建 P4 | 推荐「反查」而非新增持久化 |
| 前端双图提交链（先首尾帧出图再提视频） | ❌ 待建 P5 | 复用 `claimIllustrationSubmission` 认领 |
| 真实 API 对齐（双图语义 / size 白名单） | ❌ 待建 P6 | 实测后回填 |

---

## 2. 分期计划（每期可独立验证、可独立提交）

### P1 首尾帧双锚点提取（纯函数）
- 新模块 `story_frames.py`（纯函数）：输入楼层可见文本（`restore_jailbreak` 之后），
  输出 `{opening: str, closing: str, evidence: str}`。
- 复用 `scene_illustration._anchor_score` 的段落打分思路，但**取首、尾两处**而非单一高潮段；
  段落边界按空行/换行切，无边界则整段既当开头又当结尾。
- 验证：单测（空楼层 / 纯对白 / 单段 / 多段 / jailbreak 包裹）。

### P2 表格素材读取（纯函数 + 静默降级）
- 新纯函数读 `table_store.load`：重要角色表（外貌特征/穿着打扮/所在地点/在场状态）、
  全局表（地点/世界状态）、任务表（地点）。
- **按列名存在才读**，缺列/表空 → 静默跳过，回退 `scene_spec` 现有字段，不阻塞主链。

### P3 `videoMode` + 事件协议（向后兼容）✅ 已完成（B1 提交）
- `MediaInsertPreset.videoMode?: "climax" | "firstlast"`，缺省 `climax`。
- `illustrate_request` 扩展**可选**字段：`videoMode`、`firstFrameDesc`、`lastFrameDesc`、
  `prevTailDesc`、`lastFrameUrl`（若有）。TS `chatStreamProtocol` 解码保持宽松（未知字段忽略）。
- 落地：
  - 后端 `agent_graph._ordered/_streamed_illustration_events` 透传 rec 里这 5 个字段（有值才带）。
  - 前端 `chatStreamProtocol` 解码 `video_mode/first_frame_desc/last_frame_desc/prev_tail_desc/
    last_frame_url` → 驼峰字段，宽松忽略未知值。
  - 前端 `resolveVideoMode`（illustrationMedia.ts）：事件 videoMode 优先 → preset.videoMode →
    缺省 climax。
  - `illustrationTemplateValues` 新增 binding：`video_mode/first_frame_desc/last_frame_desc/
    prev_tail_desc/last_frame_url`（模板 exposed 有对应语义才注入）。
  - `useChatSession.submitIllustration` 收 5 个可选参数，仅 `useVideo` 时透传。
- 验证：后端 3 个透传用例 + 前端 7 个（协议解码 4 + resolveVideoMode 3/4）全绿。

### P4 尾帧链式状态（推荐「反查」，零新增持久化）✅ 已完成（B2）
- 不做 thread 级新 kv。前端已落地：
  - `MsgPart.lastFrameDesc`：illustrate_request 事件携带 lastFrameDesc → 槽位创建时存储，
    视频完成 resolve 为 video 时保留（chatSessionEvents.appendMediaSlot/resolveMediaSlot）。
  - `resolvePrevTailDesc(messages)`（chatGeneration.ts）：倒序扫描最近一条已完成 video 槽
    （type==="video" && status==="ready"），取尾帧描述；该槽无描述 → undefined 不跳过
    （避免跨楼层取到过时尾帧，R8）。
  - `submitIllustration` 兜底：事件 prevTailDesc 为空时用 `resolvePrevTailDesc` 反查结果。
- 与前端 `resolveVideoBaseImageRef` 同思路，天然随 `chat_snapshot` 走，**分叉/重生成不污染**。
- 转场素材兜底：`preset.transitionImage` 延后（用户决策 #2），不在本批。

### P5 前端提交链（异步闭环复用）
- ✅ 已落地（可验证核心，B3）：
  - R4 闸门：`resolveVideoTemplateChoice`（illustrationMedia.ts）——firstlast 楼层触发不看
    motion/smartVideo；climax 维持 smartVideo && motion>=2（旧预设缺省 climax 行为不变）。
  - 双帧图 binding：`illustrationTemplateValues` 新增 `first_frame_image`/`last_frame_image`。
  - `submitIllustration` firstlast 路由：事件 lastFrameUrl → 上传为 last_frame_image（失败降级
   首帧单图，不挂死槽）；首帧底图 → first_frame_image。尾帧图缺省不进 values（无悬空引用，R2）。
- ✅ 已落地（接线核心，2026-08-26）：**「先异步出首帧图+尾帧图（两次 ComfyUI），双图
  ready 后再提视频」**顺序链：
  - 等待桥复用现有 `pollWorkflowResult`（Promise 化，complete/failed/still_running，已有测试）。
  - 决策 A（用户拍板）：首尾帧生图复用现有图片模板（`preset.templateId`），prompt=事件
    `firstFrameDesc`/`lastFrameDesc`；reuse 免首帧生图（W2 已把上尾帧图复用为底图）；
    尾帧有事件图直接用。纯函数 `planFirstlastFrameTasks`/`firstlastFrameValues`
    （illustrationMedia.ts）+ `moveComfyOutputToInput`（产出图转 input，comfyui.ts）。
  - 接线：`submitIllustration` firstlast 路由——按计划逐帧 `submitWorkflow(图片模板)` →
    `pollWorkflowResult` → `moveComfyOutputToInput` → 双图就绪后再提视频任务。
    首帧生图失败 → 明确失败（视频必有首帧）；尾帧生图失败 → 降级首帧单图（不挂死，R2）。
  - 验证：前端 illustrationMedia +7、comfyui +2 单测，全量 570 passed；真实 ComfyUI
    双图视频模板验证顺延 P6（R1：不猜接口、不做无法验证的接线）。
- 复用 `claimIllustrationSubmission` 幂等认领不变（已有）。

### P6 真实 API 对齐（最后做，需用户提供可实测端点）
- ⏸ 实测顺延（2026-08-26 用户拍板）：真实双图视频端点未提供（`ai.t8star.org/v2/videos/
  generations` 不可用已拍板）→ 待用户提供可实测端点后：实测 `image[]` 是否「首帧/尾帧」
  语义；不是 → 只在 prompt 层职责绑定，回填文档。
- ✅ 已落地的代码契约（不依赖端点）：`build_video_request` 双图职责绑定（图片1=首帧/图片2=尾帧）；
  视频 size 默认 1280x720（R9）；`transition` 三态 wire；首尾帧顺序链（P5）。

---

## 3. 风险审计表（隐患 → 对策 → 落点）

| # | 隐患（对照现有代码） | 对策 | 落点 |
|---|---------------------|------|------|
| R1 | **双图 `image[]` 语义未实测**：`build_video_request` 输出 `images=[首,尾]` + prompt「图片1=首帧」，但 API 是否把 `image[0]/image[1]` 当首尾帧是未知的（OpenAI 兼容 `image[]` 只是「多参考图」）。 | 一期只声明「职责绑定」，不声明 API 语义；P6 实测后再定是否换字段名（`first_frame_image`/`last_frame_image`）。 | P6 |
| R2 | **文生退化不连贯**：firstlast 缺图时 `images=[]`、`content_type` 自动转 json，但 prompt 仍写「图片1/图片2」→ 引用不存在的图。 | `compile_firstlast` 前加守卫：两帧图地址/描述缺一时，要么报错要么降级 climax 形态，不得发「引用了不存在的图」的提示词。 | P3/P5 |
| R3 | **元信息缺三件套**：`_meta` 只输出「时长 + 运镜」，缺 H3 要求的「模型名 + 画幅(16:9)」。 | 从 `video_config.model`（可选）与 `size`（`1280x720→16:9`）派生画幅，补进元信息。 | P3（改 `_meta`） |
| R4 | **前端触发闸门错位**：现有 `smartVideo` 用 `motion>=2` 选视频模板，但 firstlast 是「楼层触发」不看 motion；复用同一闸门会导致 firstlast 在低 motion 楼层不触发。 | firstlast 单独闸门（`isStoryNode` 思路），与 climax 的 `motion>=2` 分离。 | P3（前端） |
| R5 | **narrative 直接复述整段**：`_time_segments` 把整段 `narrative` 塞进「主体演变」，楼层文本可能含 jailbreak 残留/冗长对白，且后续可能再过 `_apply_regex(IMAGE_PROMPT)` 被改写。 | 用 P1 提取的 `opening/closing` 锚点 + 精简动作描述，而非整段 narrative；明确 video_prompt 输出**不再过**正则层。 | P1 |
| R6 | **音频占位 vs 真实对白**：`_audio_hint` 输出「台词=逐字+声线(actors)」但无实际台词；H3「真会照做」音频，占位可能诱导幻觉，且与 comfy_audio 独立配音可能双份。 | 无实际对白行时 ⑥ 只写纯音乐结构（不写「台词=逐字」）；有 `audio_lines` 才列逐字台词。 | P3（改 `_audio_hint`） |
| R7 | **负面提示词重复**：`_negative` 用「；」拼 preset + spec，可能重复项。 | 去重（按分隔词去重）或信任 preset 单源。 | P3 |
| R8 | **尾帧跨楼层状态污染**：若用新增持久化存尾帧，快照恢复/`scenario` 分叉/重生成会指向错链。 | 采用 P4「反查」方案，零新增状态，天然随快照走。 | P4 |
| R9 | **视频 size 默认沿用图片的 1024x1024**：`build_video_request` 默认 `size="1024x1024"`，对视频是 1:1，不合理。 | 视频默认改 `1280x720`（或由画幅派生），并校验 provider 白名单。 | P6 |
| R10 | **表格列名脆弱**：重要角色表列「外貌特征/穿着打扮/所在地点」是用户模板可改的，硬编码列名会读空或读错列。 | 按列名存在才读，缺列静默降级；列名做成常量便于对齐。 | P2 |

---

## 4. 测试策略

- **纯函数单测**（`test_video_prompt.py` 已有 11 个，续补）：
  - R2：firstlast 缺图守卫（不产出「图片1/图片2」引用不存在的图）。
  - R3：元信息含画幅（`1280x720 → 16:9`）。
  - R6：无对白行时 ⑥ 不含「台词=逐字」。
- **新纯函数单测**：`story_frames`（P1）边界、`resolvePrevTailDesc`（P4，含分叉/重生成场景）、
  表格列名容错（P2，缺列/空表降级）。
- **协议**：`chatStreamProtocol` 解码忽略未知字段用例（旧前端兼容）。

---

## 5. 已拍板决策（2026-08-26，用户答复）

1. **R1 保持红线**：现 `https://ai.t8star.org/v2/videos/generations` **不可**用于双图首尾帧实测。
   → 一期只在 prompt 层做职责绑定，不声明 API 语义；P6 顺延，待用户提供可实测端点。
2. **转场素材延后**：`preset.transitionImage` 不在当前目标内。当前核心目的三点：
   - **准确提取首尾帧**（P1 锚点提取）；
   - **首帧复用判断**（2026-08-26 定调修正：N+1 首段 ≈ N 尾端 → 复用上尾帧；否则独立生成
     首帧图 + 尾帧图）——**取代**原「首帧带上下文、由模型自然判断」的模糊衔接决策；
   - **高潮图片动作化延伸**（climax 精简版打磨，语义 = 定格图 → 剧情描述的完整动作）。
3. **模型名不硬编码**：Minimax H3 只是视频模型之一，实际还有别的。元信息里的模型名
   从 `video_config.model` 透传，**不得**写死「Minimax H3」。
4. **防拦截机制对齐图像生成（2026-08-26 新增）**：视频提示词与图像生成一样，采用
   **两层防拦截**。已确认图像生成的两层是：① `restore_jailbreak` 还原 `@()@` 破甲标记
   （纯函数，`image_prompt_extract`）；② `_apply_regex(IMAGE_PROMPT, is_prompt=True)`
   用户正则清洗（接线层，需 ctx）。
   - 第一层已落地：`video_prompt.build_video_request` 入口 `_clean_spec` 对 spec 文本字段
     统一 `restore_jailbreak` 兜底（端到端 dry-run 已验证：带 `@(色)@` 的 appearance 不再
     残留进 prompt）。
   - **第二层已定（2026-08-26 用户拍板修正）**：**不新增 VIDEO_PROMPT placement**。清洗规则
     从 IMAGE_PROMPT 抽出为**单一共享清洗规则文档**（`docs/PROMPT-CLEANING-RULES.md`，按用途
     命名「通用提示词清洗规则」），图像、视频及未来一切提示词都用同一份规则
     （破甲还原 → 客观提取 → 按目标语言拼装），节省上下文。视频提示词不单独配清洗 placement，
     直接用 IMAGE_PROMPT 同套机制。
   - 机制真相（代码核实）：@() 还原是共享模块 `prompt_clean.restore_jailbreak`（硬编码
     _MARKER_RE），不是 IMAGE_PROMPT 的能力；IMAGE_PROMPT placement 走 `regex_engine.run_scripts`
     的**任意正则**（findRegex/replaceString/trimStrings，全局库/预设/卡内嵌三层来源），
     能洗任意内容。
   - 独立性保障（2026-08-26 落地）：`image_prompt_extract` 的破甲还原已改为 re-export 共享
     模块 `prompt_clean`（本模块内 `_MARKER_RE` 与 `restore_jailbreak*` 实现已删除），
     即使删掉 IMAGE_PROMPT 清洗规则，图像生成仍由共享规则庇护防拦截
     （回归测试 `test_prompt_clean.py` 保证）。
5. **A3 表格读取取消（2026-08-26 新增）**：表格在剧情推进时已自动发送/自动填表，
   生成视频提示词时场景、角色等信息**本就是已知内容**（已在 `scene_spec` 的
   appearance/wardrobe/locale 里），无需单独读 `table_store`。P2 从实现计划移除。
6. **视频提示词不靠模板敲定（2026-08-26 用户拍板）**：镜头语言映射（情境→角度+运动）
   只是骨架，A（镜头编排）+ B（动态提取：对白逐字/动作序列/情绪）是提取方向，
   但具体提示词必须结合剧情动态逐段编写，**尚未拍案实现细节**。

---

## 6. 实现顺序（按核心三目标重排）

```
第一批（本轮核心目标，纯函数，不依赖前端协议）：
  A1 首尾帧双锚点提取  story_frames.py（P1）✅ 完成
  A2 提示词打磨         video_prompt.py ✅ 完成——
                        · 衔接感：firstlast 首帧强化「上楼层尾帧→本楼层开场」衔接描述
                        · 高潮动作化延伸：climax 动作段加强运镜/特效/节拍
                        · 修 R2/R3/R6/R7/R9 + 防拦截第一层（_clean_spec → 共享 prompt_clean）
  A3 表格素材读取      ❌ 取消（表格在剧情推进时已自动发送，场景信息 scene_spec 已含）

第二批（接线，需前端协议）：
  B1 videoMode + 事件协议（P3）✅ 完成——
                        · MediaInsertPreset.videoMode（缺省 climax，旧预设兼容）
                        · 后端事件透传 video_mode/first_frame_desc/last_frame_desc/prev_tail_desc/last_frame_url
                        · 前端宽松解码 + resolveVideoMode 决策 + 模板 binding 注入
                        · 线编码器 encode_event 透传修复 + 跨语言契约测试（b1_emit_wire.py + b1Contract.test.ts）
  B2 尾帧反查（P4）✅ 完成——
                        · MsgPart.lastFrameDesc + 槽位创建/完成保留
                        · resolvePrevTailDesc 纯函数（零持久化，随 chat_snapshot 走）
                        · submitIllustration prevTailDesc 兜底
  B3 前端双图提交链（P5）✅ 可验证核心完成（顺序链待 P6）——
                        · R4 闸门：firstlast 楼层触发不看 motion（resolveVideoTemplateChoice）
                        · 双帧图 binding：first_frame_image/last_frame_image + lastFrameUrl 上传路由
                        · 尾帧图缺省降级首帧单图（无悬空引用，R2）
                        · ⏸「先出双图再提视频」顺序链：阻塞于无等待桥 + 无真实双图模板（P6）

第三批（延后）：
  C1 真实 API 对齐（P6，待用户提供可实测端点）  C2 转场素材（延后）
```

A1+A2+B1+B2+B3（可验证核心）已完成并验证（端到端 dry-run 串通 + 防拦截共享清洗 +
videoMode 协议透传 + 尾帧反查 + 双帧路由）。P5 剩余的「先双图后视频」顺序提交流程
受限于无等待桥与无真实双图视频模板，按红线原则延到 P6（真实 API 对齐）一并落地。
视频提示词内容编写（镜头语言 + 动态提取）按用户指示留到后续测试环节逐步排查。

## 7. 补充测试点：默认开放 climax 视频提示词生成（2026-08-26 用户指示）✅ 完成

- 背景：视频模型和工作流现在都不需要准备。剧情推进时**默认开放视频提示词生成环节**，
  先测试高潮模式（climax）生成的视频提示词是否符合要求，再逐步排查内容编写。
- 后端：`agent_graph._video_request_for(rec)` —— 有 scene_spec 即用
  `video_prompt.build_video_request` 完整组装「上交给视频模型的参数」（dry-run）：
  spec+motion 喂 compile_climax_video_prompt（first_frame_desc 用已还原 narrative），
  video_config 从 rec 透传（组装 illustrate_req 时带 ctx 的 vid_base/vid_model/vid_proxy
  + 视频默认 1280x720，roleplay_turn 白名单透传进 rec）。随事件下发 `video_prompt`
  （提示词）+ `video_params`（结构化参数：model/size/endpoint/images/reference_binding/
  warnings）；失败静默降级，不阻断出图/出视频。线编码器透传两字段。
- 前端：`chatStreamProtocol` 宽松解码 `video_prompt` + `videoParams` → 槽位存储
  （appendMediaSlot / resolveMediaSlot 保留），**无视频模板/模型也展示**（测试核对）；
  `illustrationTemplateValues` 新增 `video_prompt` binding（仅视频分支注入）。
- 验证：dry-run 探针 `backend/scripts/b2_video_prompt_probe.py`（三档 motion 输出
  提示词 + 视频参数 + wire 导出到前端 fixture）；后端 7 用例（默认携带/区块完整/motion
  运镜/无 scene_spec 不生成/video_config 透传/video_params 线编码/roleplay 白名单）
  + 前端契约（wire→解码→binding/槽位）。全量 后端 1652 / 前端 551 + tsc 0 错。
- 🆕 日志可核对（2026-08-26 补充）：produce 层（agent_graph）即用 build_video_request
  编译 video_request 存进 illustrate_req → rec 白名单透传（roleplay_turn），事件层直接复用
  （不重复编译）；`illustration.request` 的 trace 记录新增 `video_prompt` 全文 +
  `video_prompt_chars`——推进剧情到高潮点后，`backend/data/logs/agent-trace.jsonl`
  里可直接核对视频生成提示词（测试模式关键能力）。全量 后端 1653。
- 测试点：**视频参数有没有上传** → video_params 里可直接核对 model（空=未配视频模型）、
  size（1280x720）、endpoint（空=未配视频工作流）、images（空=参考图待上游补）、
  warnings（缺图守卫）。后续配好视频工作流后，改回真正执行 submit。
- 下一步：用探针输出人工核对提示词质量；之后按反馈逐步排查内容编写（对白逐字/动作序列/情绪）。

## 8. 高潮视频提示词桥段：画面级要素优先（2026-08-26 用户反馈「角色不对/提示词对不上剧情」）✅ 完成

- 背景：用户测试发现高潮视频提示词两个问题——①角色不对；②提示词对不上剧情。定位根因：
  climax 的 `[动作]` 桥段直接用中文 `narrative`（围绕 anchor 截取的一段叙事，anchor 陈旧
  时会截取到错误桥段），而非图片提示词所用的、主模型同轮提炼的画面级要素。
- 图片提示词的一致性机制：主模型同轮输出 `<illustration>` JSON，`extract_illustration_plan`
  校验并保留 subjects（英文主体描述）/visual_facts（evidence 必须命中 visible_story 才保留）
  /composition/camera/art_direction，图片 prompt = 这些画面要素拼装；scene_spec 全量透传。
- 修改（`video_prompt.py`）：`_climax_action` 拆为 `_climax_action_beat`（subjects+
  visual_facts+composition 优先，narrative 兜底）+ `_climax_camera`（camera 优先，motion
  兜底）+ `_climax_fx`（motion 强度）；`_reference_binding_climax` 的图职责描述用 action_beat
  兜底；`build_video_request` climax 分支的 desc 同样用 action_beat 兜底（原硬传
  `first_frame_desc or "高潮动作画面"` 会覆盖留空值）。
- 接线（`agent_graph.py`）：produce 层 / `_video_request_for` 的 first_frame_desc 留空，
  图职责描述交给 video_prompt 用画面级动作瞬间兜底，与 [动作] 桥段同源。
- 验证：后端 test_video_prompt 新增 3 用例（画面级要素优先/无要素回退 narrative/camera
  优先）+ 全量 后端 1656 / 前端契约 14（fixture 无画面级要素场景行为不变，无需重新导出）。
- 角色不对（问题1）根因在插画角色识别层（`_mentioned_bound_names` 纯子串匹配 +
  `illustration_actor_names` 角色全集可能混入作品名/道具等非角色实体），非视频提示词桥段；
  本次未改动，留待单独排查。

## 9. 2026-08-26 用户定调：首尾帧生图独立模式 + 视频两模式完整语义（glm-5.3 修订）

> 背景：用户对「剧情自动插入的首尾帧生成」做了一次方向性定调，与旧规划有三处根本修正
> （见下 ①—⑤）。本节为新的目标文档，`ROADMAP-MULTIMODAL.md` V1.5/V1.6 已同步。

### 9.1 架构分层（图片层 / 视频层）

```
图片层（两个模式，产出图片资产）：
  · 高潮片段生图（已完善）── 高潮锚点 → 1 张高潮动作图
  · 首尾帧生图（本步完善）── 首尾双锚点 → 首帧图 + 尾帧图
                              ├─ 首帧复用判断（②）
视频层（两个模式，基于图片 + 剧情，产出视频）：
  · 高潮片段视频（climax）── 高潮图的动作延伸（③）
  · 首尾帧视频（firstlast）── 首帧图 + 尾帧图 + 剧情
                              ├─ 转场任务排队（④）
                              └─ 时长分档（⑤）
```

关键认知修正：**首尾帧生图是图片层的独立模式**（与高潮片段生图并列），产出首帧图 + 尾帧图
入库资产；视频只是它的下游消费者。旧规划把首尾帧图降为「视频的参考图」是层级错位。

### 9.2 首帧复用判断（决策点②，取代旧「不单独判定、融入生成」）

**问题**：旧决策点②“不单独判定、融入生成”——把首帧衔接推给生成模型自然判断，不可解释、
不可控、无法省成本。

**修正**：首尾帧生图需有**明确判定**——

| 判断结果 | 触发条件 | 动作 |
|---------|---------|------|
| **首帧复用** | N+1 首段与 N 尾端可用一张图涵盖（构图/场景/站位无显著变化） | 首帧图 = N 次对话的尾帧图，**零生图** |
| **独立生成** | 构图、场景位置等发生较明显变化 | 独立生成首帧图 + 尾帧图 |

**判据（两级判断，2026-08-26 用户定调修正）**：
- **L0 纯启发式（0 LLM，先跑）**：先排除简单情况——地点/场景切换词（换地点/换场景 → 明显变化，
  直接独立生成）；段落画面特征对比（复用 `story_frames._VISUAL_TERMS` / `_dialogue_ratio`，
  明显同场景连续 → 直接复用）。启发式能确定的不再进 LLM。
- **L1 LLM 确认（搭车主生成，非后发）**：启发式判断「可以复用」但属模糊地带时由 LLM 确认。
  **LLM 判断不得后发单独调用**——正文/块是防拦截结果，再次读取可能被拦截、读不准；正确做法
  是**打从主生成时就默认输出转场判定**：主 Roleplay 生成正文的同一次调用里搭车输出结构化
  转场标记（对齐 `<状态更新>`/`<illustration>`/`<audio>` 块，如
  `<transition>reuse|regenerate</transition>`），生成时即决定是否触发转场，正文落地后
  不再读文本复核。

### 9.3 高潮视频的动作延伸（决策点③，语义升级）

**问题**：旧规划把 climax 定义为「动作图的动态化」（微运镜/特效/节拍）——只动镜头不动动作，
未覆盖「剧情里的完整动作」。

**修正**：climax 视频 = **高潮图片的动作延伸**——
- 高潮图是动作的**定格起点帧**；
- 视频要延伸演出**剧情里描述的完整动作**；
- 延伸动作**必须来自剧情文本**（不凭空补）：
  - 剧情写「吃下去」→ 视频演「挖出一勺 → 送入口中吃下」；
  - 剧情写「喂给主角」→ 视频演「挖出一勺 → 将勺子喂向镜头」。

**落点**：需从剧情文本提取「定格动作 → 后续动作完成」的动作序列/目标（对白逐字 + 动作序列
提取方向之一，`video_prompt._climax_action_beat` 需补充动作目标来源）。

### 9.4 转场任务排队（决策点④）

**问题**：旧规划只有「首帧带上下文自然转场」，无「两个视频任务排队」的编排。

**修正**：首尾帧视频的任务数取决于 9.2 的首帧复用判断——

| 场景 | 视频任务 |
|------|---------|
| 无需生成首帧图（首帧复用上尾帧） | **1 个**：当前剧情的首尾帧视频（首帧图 → 尾帧图） |
| 需要生成首帧图（构图/场景明显变化） | **2 个排队**：① 转场视频（上一对话尾帧 → 当前对话首帧）；② 当前剧情的首尾帧视频（当前首帧 → 当前尾帧） |

### 9.5 时长分档（决策点⑤，2026-08-26 用户定调修正）

- **转场视频**：短桥段，**不得**套用正片时长（如 15s）。
- **正片**（首尾帧/高潮视频）：按剧情长度 + preset `videoDurationHint`。
- **转场时长不预设死值**：转场内容随机性太高（触发时机/场景差无法预估），不在提示词编译层
  硬控时长；由前端视频模板/生成侧按实际转场内容决定（模板 duration 输入或视频模型默认）。

### 9.6 对现有分期的影响

- 新增**首帧复用判断**（两级）—— L0 纯启发式纯函数：输入 N 尾帧画面描述 + N+1 首帧画面描述，
  输出 `reuse | regenerate | ambiguous`（可并入 `story_frames.py` 或独立模块）；
  L1 LLM 确认**搭车主生成**：主 Roleplay 注入转场判定契约块（对齐 `build_inline_audio_instruction`
  的 `<audio>` 块做法），生成正文时同步输出 `<transition>reuse|regenerate</transition>`，
  正文落地后不再后发读文本复核。
- 新增**转场视频任务编排**（④）+ **时长分档**（⑤）—— 落到 `build_video_request` / 前端
  firstlast 提交链（P5/P6 顺序链一并考虑）。
- `video_prompt._climax_action_beat` 需补「剧情动作目标」来源（③），不再只拼 subjects/
  visual_facts/composition。
- 旧 P4「上楼层尾帧反查」仍是首帧复用判断的输入源之一（取 N 尾帧描述），语义不变。

---

## 10. 代码构筑坑位排查（glm-5.3 分析，2026-08-26）

> 本节是「首尾帧生图独立模式 + 首帧复用两级判断 + 高潮动作延伸 + 转场编排」四块
> **动手编码前必须读**的坑位清单。每坑给「坑 → 根因 → 规避/落点」。
> 目的：让后续 deepseek-v4-pro-0813 编码**不猜、不重复踩**，接口/语义先定清。

### 10.1 坑 A：`<transition>` 搭车块与既有块的剥离顺序 & 漏块降级

- **坑**：`<transition>` 是新增搭车块，但 writeback 已有固定剥离顺序
  （`extract_illustration_plan` → `extract_audio_dialogue` → `extract_status_snapshot`
  → `parse_state_block`）。乱插位置会吃块或漏剥。
- **根因**：`<transition>` 内容只是枚举 `reuse|regenerate`（不含防拦截标记），但它的
  剥离时机和块间干扰没定。
- **规避**：
  1. `<transition>` 剥离放**最前**（与 `<illustration>`/`<audio>` 同为「生成时搭车」块，
     在正文净化前先抽）；正则对齐 `audio_dialogue_extract._AUDIO_RE` 的双闭合 + 开尾截断
     模式（`_AUDIO_OPEN_TAIL_RE`），防模型只开不闭把正文吞掉。
  2. **漏块降级**：主模型漏输出 `<transition>` 是常态（对齐 `<audio>` 的
     `build_fallback_dialogue`）——`<transition>` 是**增强不是必需**，L0 启发式永远能兜底
     （见坑 D）；L1 结果缺失时回退 L0 结论，不得抛错。

### 10.2 坑 B：L0 与 L1 的时间线错位（后发判断）

- **坑**：L0 启发式跑在「正文落地后」（拿 N 尾帧 + N+1 首段文本对比），而 L1 若要
  「LLM 确认」会发生在 LLM 已跑完之后——这就是用户点破的「后发判断」。
- **根因**：把 L0/L1 当「先 L0 再按需调 L1」的顺序链，时间上 L1 永远追不上。
- **规避（用户定调）**：`<transition>` 块**每次主生成都默认搭车输出**（对齐 `<audio>` 的
  「不得省略」），生成时即决定，正文落地后不再读文本复核。合并规则：
  - L0 确定（reuse / regenerate）→ 用 L0，忽略 `<transition>`；
  - L0 ambiguous → 消费已搭车产出的 `<transition>` 结果。
  - 即 `<transition>` 是「提前准备好的 L1」，不是「事后补判」。

### 10.3 坑 C：`<transition>` 语义与「可复用尾帧图」的前提脱节

- **坑**：`reuse` 语义 = 「首帧复用上一楼层尾帧图」，但上一楼层**未必**产出了尾帧图
  （可能是高潮片段生图模式，或上楼层是纯对白没触发首尾帧生图）。此时 `reuse` 无从谈起。
- **根因**：`<transition>` 由主模型输出，主模型不知道「上楼层有没有尾帧图」这个**资产事实**。
- **规避**：「是否有可复用尾帧图」是纯规则（反查资产库/消息槽 `resolvePrevTailDesc`），
  不是 LLM 判断。合并顺序：
  1. 先规则判断「上楼层有无尾帧图」——无 → 直接 `regenerate`（`<transition>` 的 `reuse` 作废）；
  2. 有 → 再按 10.2 的 L0/L1 合并决定 `reuse|regenerate`。

### 10.4 坑 D：L0 需要「两段场景对比」能力，现有 story_frames 只有「单段画面感」

- **坑**：文档写 L0 复用 `story_frames._VISUAL_TERMS` / `_dialogue_ratio`，但这两个是
  「判断一段有没有画面」（单段），不是「判断两段是否同一场景」（跨段对比）。
- **根因**：首帧复用判断的输入是**两段**（N 尾帧描述 vs N+1 首帧描述），现有能力不匹配。
- **规避**：新增「场景连续性对比」纯函数（独立模块或 `story_frames` 扩展）：
  输入两段画面描述，输出 `reuse | regenerate | ambiguous`。判据：
  - 地点/场景词重合度（两段共享同一地点词 → 倾向 reuse）；
  - 场景切换词（换地点/换场景/时间跳跃词 → 直接 regenerate）；
  - 均不明显 → ambiguous。
  `_VISUAL_TERMS` 可复用做词表，但**对比逻辑是新的**，不要指望现有 `_has_visual` 直接顶。

### 10.5 坑 E：高潮「动作延伸」缺「动作目标/序列」字段 —— ✅ 已定稿（方案1，2026-08-26）

- **坑**：9.3 定调 climax = 高潮图的**动作延伸**（定格 → 剧情描述的完整动作），但现有
  `scene_spec` 字段（subjects/visual_facts/composition/camera/motion）全是**空间/画面**要素，
  `_climax_action_beat` 拼不出「从挖出到吃下去」的**时间维度动作演进**。
- **根因**：动作延伸需要「定格动作 → 后续动作目标/序列」，这是 `<illustration>` 块契约
  里**没有的字段**；`visual_facts` 的 `fact` 是「视觉事实」（空间），不是「动作演进」（时间）。

**定稿契约（方案1：同轮搭车输出，与正文同源保证一致性）**：

`<illustration>` 块新增**可选**字段 `action_sequence`（旧前端/旧数据宽松忽略）：

```json
<illustration>{
  "anchor": "...", "subjects": [...], "composition": "...",
  "action_sequence": [
    {"beat": "定格起点", "desc": "勺子挖出一勺奶油（对应高潮图）"},
    {"beat": "延伸", "desc": "勺子送向嘴边 → 吃下（仅当剧情写了吃下去）"}
  ]
}</illustration>
```

- `action_sequence`：可选数组；每项 `{beat: str, desc: str}`——`beat` 节奏名
  （定格起点/延伸/收尾），`desc` 动作描述。
- 语义：`desc[0]` 对应高潮图的**定格动作**（与插画画面一致），`desc[1..]` 是**延伸动作**
  （剧情描述的完整流程，如「挖出 → 吃下去」/「挖出 → 喂向镜头」）。
- **一致性红线**：注入指令要求「延伸动作必须基于本轮剧情，剧情没写的动作不得补」。
- **降级**：字段缺失/空 → 回退现有 `subjects/visual_facts/composition`（旧行为不变，不报错）。

**落点**：
- `image_prompt_extract.extract_illustration_plan` 解析并校验 `action_sequence`
  （新增可选字段，归一化 beat/desc 字符串，空项丢弃）。
- `<illustration>` 块注入指令补「动作序列」说明（对齐现有 subjects/visual_facts 契约文案）。
- `video_prompt._climax_action_beat` 优先消费 `action_sequence`（拼进 `[动作]` 桥段），
  缺失回退现有空间要素。

### 10.6 坑 F：转场视频的「上尾帧图地址」获取

- **坑**：决策④转场视频 = 上尾帧图 → 当前首帧图，需要**图地址**（image 输入）而非描述；
  但上尾帧图是「首尾帧生图模式」才产出的，若上楼层走「高潮片段生图」或未触发，则无对应图。
- **根因**：转场视频的输入图依赖「上一楼层首尾帧生图模式产出的尾帧图资产」。
- **规避**：转场视频的触发**前置**「上楼层有可复用尾帧图」与 10.3 同源；
  图地址从资产库/消息槽反查（复用 P4 `resolvePrevTailDesc` 同思路的「图地址反查」），
  反查不到 → 转场视频降级为「文字转场」（首帧单图生视频）或跳过，不挂死。

### 10.7 坑 G：转场时长「不预设死值」与 preset 单一 duration 的冲突

- **坑**：决策⑤转场时长不预设死值，但 `build_video_request` 的 `duration_hint` 来自单一
  `preset.videoDurationHint`——转场视频和正片若同 preset，时长会一样。
- **根因**：当前编译层只有「一个时长」，没有「转场时长 vs 正片时长」区分。
- **规避**：编译层**不硬控转场时长**（用户定调），转场视频的 duration 由前端提交侧决定
  （转场任务单独给 duration 输入，或视频模型默认）；`build_video_request` 对转场形态
  不写死秒数。正片仍走 `videoDurationHint`。

### 10.8 坑 H：wire 协议扩展的连锁（五处同步）

- **坑**：首帧复用判定结果（`transition` 字段）要下发前端驱动「复用 or 生成」，这是 wire
  变更，按 AGENTS.md 必须 `check:wire` + 双端测试。
- **根因**：B1 已走过一遍 `videoMode` 五处同步（协议/前端解码/reducer/schema/双端测试），
  `transition` 字段同样五处，漏一处就断链。
- **规避**：`illustrate_request` 事件加**可选** `transition` 字段（`reuse|regenerate`），
  前端 `chatStreamProtocol` 宽松解码（未知字段忽略，旧前端兼容），线编码器透传。
  参照 B1 的 `video_mode/first_frame_desc/...` 同套路。

### 10.9 坑 I：`<transition>` 枚举二态 vs L0 三态，语义边界要统一

- **坑**：L0 输出 `reuse | regenerate | ambiguous`（三态），`<transition>` 块是
  `reuse|regenerate`（二态）。两套状态不一致会在合并处产生歧义。
- **规避**：明确定义——L0 三态是「内部决策态」，`<transition>` 二态是「LLM 搭车输出的
  最终复用结论」。合并时 `ambiguous` 才消费 `<transition>`；`<transition>` 永远只输出二态
  （reuse/regenerate），不输出 ambiguous。

### 10.10 实现顺序建议（给 deepseek 编码）

```
第一批（纯函数，无 wire，可独立单测）：
  F1 场景连续性对比纯函数（坑D）── 两段画面描述 → reuse|regenerate|ambiguous
  F2 <transition> 注入指令 + 剥离解析（坑A/B）── 对齐 build_inline_audio_instruction + _AUDIO_RE
  F3 上楼层尾帧图「反查」纯函数（坑C/F）── 是否有尾帧图 + 图地址（复用 P4 思路）
  F4 climax 动作延伸字段契约（坑E）── 先定契约，再改 _climax_action_beat

第二批（接线，需 wire）：
  W1 <transition> 结果并入 writeback + illustrate_request 事件透传（坑A/H）✅
  W2 首帧复用决策合并逻辑（坑B/C/I）── L0/L1 合并 + 有图前提 ✅
  W3 转场视频任务编排（坑F/G）── 2 任务排队 + 转场时长前端决定
     ✅ 已落地（后端可验证部分，2026-08-26）：build_video_request 新增 mode="transition"
        短桥段编译（图片1=上尾帧/起点、图片2=当前首帧/终点）+ 坑G 不硬控时长
        （preset.transitionDurationHint，缺省交视频模型默认，绝不兑底正片 5s）+
        缺图降级（坑F：缺上尾帧 → 文字转场）。单测 7 个全绿。
     ⏸ 未落地（阻塞，需 P5/P6）：前端 firstlast 提交链「2 任务排队」（转场视频 +
        正片顺序提交）依赖 P5 的「先出首尾帧图再提视频」顺序链（⏸ 阻塞，需 P6）与
        真实双图视频模板实测；尾帧图地址（last_frame_url）后端仍未赋值（同 prev_tail_desc
        前期状态）。待 P6 后与 P5 一并接线。
```
