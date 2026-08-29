# 剧情正文去 AI 味（四方法落进扮演链路）

> 来源：D:\Study\Harness Engineering\去AI味.md 的四个方法；本文件是施工图。
> 参照代码：`backend/app/services/agent_graph.py`（roleplay_node / writeback）、
> `narrative_ci.py`（非阻断诊断）、`routers/narrative.py` + `NarrativeCiPanel.tsx`（诊断展示与处置）。
> 文风问题属**剧情正文域**，与 `prompt_clean`（出图/视频提示词清洗）无关，不得混用属主。

## 定位

正文文风的 AI 味（套路句式、讨好腔、空洞大词、破折号连击、自问自答、三段式命题作文）是
剧情沉浸感的第一杀手。四方法按「确定性优先、检测不涂改」落进现有链路：

| 方法（笔记原文） | 落点 | 性质 |
|---|---|---|
| ① 硬性规则（扫描禁用词/套路句型/违规标点） | `prose_style.py` 纯函数 lint → 并入 Narrative CI 诊断流 | 确定性，逐轮 |
| ② 风格一致性检查（开头具体性/节奏节拍器/口语化） | lint 确定性指标（句长方差、跨轮开场趋同）+ 口语化归 ④ | 确定性为主 |
| ③ 内容质量审查（观点有细节支撑、不幻想） | 已有 Narrative CI 事实/认知/世界规则诊断覆盖「不幻想」；「细节空洞」归 ④ 通审清单 | 复用+归并 |
| ④ 活人感通审（通读，看像不像真人写的） | LLM 通审走**正文后维护通道**，产出综合文风诊断 | LLM，非阻断 |

两条铁律贯穿全工程：

- **检测不涂改**：任何诊断（含 ④）不得自动改写正文——`ARCHITECTURE.md` Narrative CI 合同
  （「不得自动改写或净化正文」）原样适用；采纳与否由用户决定（重生成/续写时自然规避）。
- **词表单一属主、一份两用**：禁用词/套路句式表只有一个属主文件，lint 用它检测，
  生成侧预防 prompt 也从它编译——两处绝不允许各自维护一份而漂移。

## 四件套

```text
roleplay_node 组装 system
   │  风格约束段（从禁用词表编译，可开关）→ 生成侧预防
   ▼
主生成产出正文
   │  writeback：narrative_ci.evaluate 并入 prose_style lint 诊断（①②）
   ▼
正文发出（真源不动）→ 后台维护通道
   │  LLM 活人感通审（④，含③的细节支撑清单）→ 综合文风诊断入同一诊断流
   ▼
NarrativeCiPanel 展示 → 用户处置（open/fixed/accepted 等既有生命周期）
```

## S0 禁用词表与确定性 lint（最小可信产物）——✅ 已落地（2026-08-29）

**落地情况**：`prose_style.py`（词表属主 + 6 类 lint：固定搭配/标点密度/句式模板/自问自答/
节拍器/跨轮开场趋同）+ `narrative_ci.evaluate` 并流（`recent_openings` 由 agent_graph
writeback 处从最近 3 层 assistant 楼层传入）+ 30 项单测。S1 的 `style_prompt_segment()`
编译函数已就位但未注入 roleplay system；词表用户增删（用户态文件）待 S1 一并做。

**目标**：正文每轮过一遍确定性文风 lint，AI 味套路被逐条抓出并展示。

**改哪里**：
- 新建 `backend/app/services/prose_style.py`：纯函数、0 I/O、0 LLM。内含：
  - **词表数据（单一属主）**：套路句式（「不是A，而是B」滥用、自问自答「难道…吗？不，…」）、
    空洞大词（赋能/闭环/抓手/底层逻辑等）、讨好腔、AI 味破折号与省略号密度阈值、
    命题作文三段式信号。词表先小而准（误报优先级最低），用户可在设置里增删（落用户态文件，不进源码）。
  - `lint(text, *, turn) -> list[diagnostic]`：返回与 `_diagnostic` 同构的诊断 dict
    （code/severity/message/evidence），错误信息中文、带原句证据。
- `narrative_ci.py`：新增文风诊断码（`CODE_STYLE_BANNED_PHRASE` / `CODE_STYLE_PUNCT_DENSITY` /
  `CODE_STYLE_RHYTHM_METRONOME` 等），`evaluate()` 内调用 `prose_style.lint` 并入返回流——
  诊断渠道仍只有 Narrative CI 一个，前端 `NarrativeCiPanel` 无需新面板。
- 节奏指标：按句切分算句长分布，方差过低（节拍器感）报 `CODE_STYLE_RHYTHM_METRONOME`；
  跨轮开场趋同（最近 N 层开头 15 字高度相似/同构）报 `CODE_STYLE_OPENING_CUE`（②的确定性部分）。

**验收**：lint 各词表分支单测；对含套路句式/破折号连击的真实样文逐条命中、对干净样文零误报；
Narrative CI 诊断流端到端可见（evaluate→save→Panel 展示）。

**预排问题**：
- **误报伤正文**：文学创作里「不是A，而是B」本身合法。策略：低危 severity（info/warning），
  只在密度超标或同模式重复出现时报；词表按「单轮出现即报」和「密度阈值」两档分级。
- **禁分割槽**：正文带防拦截拆字标记（`@(x)@`），lint 前须先过 `restore_jailbreak`
  同款还原（复用 `prompt_clean.restore_jailbreak_with_offsets`，只读还原不落库）。

## S1 生成侧预防（同一词表编译进 system）——✅ 已落地（2026-08-29）

**落地情况**：roleplay_node 在两分支汇合处注入 `style_prompt_segment()`（默认开，
`data/prose_style.json` 的 `enabled:false` 时返回空串、system 逐字节不变）；
用户态配置（enabled/extra/removed）由 `load_config()` 每轮读一次、坏文件回退默认，
lint 与注入段共用 `effective_phrases()`（同一属主）。`mypy_files.txt` 已纳入
prose_style/capability_registry/capability_handlers 三个新契约模块。
**剩余**：无——词表增删 UI/API 已落地（`/narrative/prose-style` + 设置→智能体「剧情文风」分区，含 S2 通审频率）。

**目标**：减少产生 AI 味的源头，而不是只靠事后抓。

**改哪里**：
- `agent_graph.py` roleplay_node system 组装处：从 `prose_style` 词表编译一小段风格约束
  （禁用句式示例 + 「多用具体细节少用空洞概括」），追加进 system；**开关放预设**，默认开、可关。
- 词表改动 → 编译段自动跟随（同一属主），`--check` 类一致性由单测锁（编译函数对词表快照断言）。

**实现要点**：
1. 约束段控制在 ~200 字内：确定性检测是主力，prompt 只做预防；塞长清单吃正文额度还降低遵从。
2. 不用「禁止显得像 AI」这类模型无法执行的模糊指令，只给可核对的句式清单与正例。
3. trace 记 `prose_style.injected`（词表版本/条数），方便回归对照开关前后命中率。

**验收**：开关关闭时 system 与现状逐字节一致；开启后真实一轮正文 lint 命中数下降（对照记录进 trace）。

**预排问题**：
- **预设污染**：约束段是机制注入不是用户预设内容，须与预设正则/世界书装配点隔离，避免被
  用户预设覆盖或反覆盖——装配点只读词表编译产物。

## S2 LLM 活人感通审（后台维护通道）——✅ 已落地（2026-08-29）

**落地情况**：`style_review.py`（采样闸门 should_review + structured_output StyleReview 判定 →
`CODE_STYLE_LIVING_REVIEW` 诊断入 Narrative CI 诊断流，info 级）；接线在 `_agency_maintenance`
（Curator 之后，同维护队列）；采样由配置 `review_every` 控制（默认每 5 轮，0=关），
设置界面可调；失败静默降级只记 trace。

**目标**：①②抓不到的整体感（讨好腔通篇、节拍、活人感）由 LLM 通读兜底。

**改哪里**：
- 正文发出后的维护链（表格/认知/纪要/Curator 所在通道，`_remember` 附近）：新增可选
  文风通审调用——输入本轮正文（还原防拦截壳后），输出结构化判定
  `{alive_score, 开头具体性, 节奏, 口语化, 细节支撑(③), 综述}`，映射成一条综合文风诊断入流。
- 模型走四类模型代理的 chat 通道，额度独立于正文；**失败静默降级**（trace 记 status），永不阻断。

**实现要点**：
1. 频率：每轮通审太贵——默认采样（如每 N 轮或正文超阈值时跑），开关放预设。
2. 通审 prompt 用「通读并按清单核对」的结构化输出（复用 structured_output Runtime），不自由发挥。
3. 处置复用 RESOLUTIONS 生命周期；accepted（接受此风格）同时是词表调参信号——
  连续 accepted 的模式应从词表降级/移除（人工确认后改属主文件）。

**验收**：通审诊断端到端可见；关闭开关零调用；失败降级不占前台队列；采样频率生效。

**预排问题**：
- **通道隔离**：通审与纪要/Curator 同为后台维护，不得并发挤占维护串行队列——复用既有
  维护顺序位，不新开并行通道（generation-channel-isolation 合同）。
- **裁判同样有 AI 味**：通审模型自身的评判腔（三段式、空洞大词）需在通审 prompt 里自反约束，
  否则诊断综述本身没法看。

## 红线对照

- 检测与通审**永不阻断或改写正文**；正文真源只属于模型输出与用户编辑（Narrative CI 合同原样延伸）。
- 词表单一属主：lint 与生成侧注入共用一份，两处漂移即打回。
- `prompt_clean`（提示词域）与 `prose_style`（正文文风域）互不越界；显示层个性化替换仍走用户
  markdownOnly 正则，不归本工程管。
- 后台运行不阻塞前台聊天队列；文风诊断不计入正文额度。
- 落地时同步 `ARCHITECTURE.md`（Narrative CI seam 段落加文风诊断一句话）并双份记忆。

## 与 Autopilot / 多模态路线的关系

- 文风通审是「trace 分析闭环」在正文域的同构应用：诊断命中率与 accepted 率就是文风 harness
  的反馈信号，词表即正文域的 validator 规则属主。
- Autopilot 的能力清单若纳入「词表编辑」条目，属 reversible 级（写用户态配置），P2 扩展批再议。
