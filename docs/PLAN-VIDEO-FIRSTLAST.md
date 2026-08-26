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
- ⏸ 未落地（阻塞，需 P6）：**「先异步出首帧图+尾帧图（两次 ComfyUI），双图 ready 后再提视频」**
  的顺序链。原因：当前 `pollResult` 是 fire-and-forget（后台轮询 resolve 槽位），无
  「等待工作流完成」Promise 桥；且无真实双图视频模板/API 可验证（红线 R1：不猜接口字段名、
  不做无法验证的接线）。待 P6 提供可实测端点/双图模板后，补「等待桥 + 双帧顺序提交」。
- 复用 `claimIllustrationSubmission` 幂等认领不变（已有）。

### P6 真实 API 对齐（最后做，需用户提供可实测端点）
- 实测 `image[]` 双图：确认是否「首帧/尾帧」语义；不是 → 只在 prompt 层职责绑定，回填文档。
- 视频 `size` 白名单与默认（见 R9）。

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
   - **前后剧情镜头衔接感**——提示词写好（首帧带「上楼层尾帧」上下文，衔接描述要到位）；
   - **高潮图片动作化延伸**（climax 精简版打磨）。
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
