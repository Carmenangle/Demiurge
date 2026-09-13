// 新人引导内容单一真源：文案与插图都在这里逐段补充（不需要改组件）。
// 使用方法：
//   1. 插图文件放 `frontend/public/onboarding/`（PNG/JPG/WebP 均可）；
//   2. 在对应 step 的 image 填相对 public 的路径，如 "onboarding/quick-start-1.png"；
//   3. text 支持空行分段，渲染时按段落拆开。
// 新增一节 = 在 NEWCOMER_GUIDE_SECTIONS 里加一个 GuideSection（id 唯一）。

export interface GuideStep {
  title: string;
  text: string;
  /** 相对 frontend/public 的图片路径；空串 = 显示“插图待补充”占位 */
  image?: string;
}

/** 页内分组：左侧栏仍只占一项，页内按流程分节讲解（2026-09-09 固化流程合并）。 */
export interface GuideGroup {
  /** 分组标题（如「01：批量生图」） */
  title: string;
  /** 分组导语（可选） */
  summary?: string;
  steps: GuideStep[];
}

export interface GuideSection {
  /** 唯一 id（hash 锚点用） */
  id: string;
  title: string;
  summary?: string;
  steps: GuideStep[];
  /** 可选：页内再分组（左侧栏不拆项）。有 groups 时 steps 由 groups 机械展开，勿两处维护 */
  groups?: GuideGroup[];
}

/** 章节的步骤序列（分组章节按 groups 展开，保证锚点序号与渲染顺序一致）。 */
export function guideSteps(section: GuideSection): GuideStep[] {
  return section.groups?.length ? section.groups.flatMap((g) => g.steps) : section.steps;
}

const BASE_SECTIONS: GuideSection[] = [
  {
    id: "quick-start",
    title: "快速开始",
    summary: "从配置到第一张图的完整流程",
    steps: [
      {
        title: "1. 启动应用",
        text: "开发环境双击 start-dev.bat，便携版运行包内启动脚本。浏览器打开 http://127.0.0.1:8010（开发环境 Vite 端口 5173）。",
        image: "onboarding/quick-start-1.png",
      },
      {
        title: "2. 配置模型（设置 → 模型）",
        text: "在「⚙ 设置 → 模型」添加模型卡：填 API URL（如 https://api.openai.com/v1）、API Key、API 模型名称（可点「读取模型列表」直接选择）；按接口协议选 Provider Profile（OpenAI 兼容 / Claude 兼容，按协议选、不按模型名选），最后点「测试模型」确认连通。\n\n对话模型必配；生图、视频、嵌入模型按需分别添加（本地嵌入/GGUF 也可在对应卡片切 local 模式）。",
        image: "onboarding/quick-start-2.png",
      },
      {
        title: "3. 配置路径（设置 → 路径）",
        text: "「⚙ 设置 → 路径」：填 ComfyUI 目录（含 main.py）与 ComfyUI 访问地址；再设仓库文件夹（生成图片与会话记录都落这里）、角色卡文件夹、世界书文件夹、偏置预设文件夹、工作流默认读取路径。目录决定素材从哪读、产物存到哪。",
        image: "onboarding/quick-start-3.png",
      },
      {
        title: "4. ComfyUI 自动启动",
        text: "「⚙ 设置 → 路径」里填好 ComfyUI 目录（含 main.py）与 ComfyUI 访问地址并保存后，start-dev 启动时会按保存的配置自动拉起 ComfyUI，无需手动启动（已在运行则自动跳过）。首次初始化较慢（约 30–60 秒），可在「系统管理 → 节点管理」查看状态，● 运行中 即可。\n\n两个例外需要手动：刚填完路径还没重启过后端、或自动拉起失败时，在「节点管理」右上角点「启动」手动拉起。只用云端生图模型可跳过本步（不填 ComfyUI 目录就不会自动启动）。",
        image: "onboarding/quick-start-4.png",
      },
      {
        title: "5. 导入工作流模板",
        text: "「工作流管理 → 工作流模板」：点「选择文件导入」单个导入，或「扫描默认目录」批量导入。\n\n导入之后怎么选暴露参数、怎么填能力描述并保存，见 [导入工作流模板详解](doc:docs/guide/workflow-template-import.md)。",
        image: "onboarding/quick-start-5.png",
      },
{
        title: "6. 选择暴露的节点参数",
        text: "解析后进入模板编辑，两种方式选暴露参数：参数清单模式——勾选要暴露的参数（常暴露提示词、宽高、LoRA、图片输入口；已连线字段不可暴露），系统自动推断语义绑定（prompt / lora / latent / 负面词等）；ComfyUI 界面模式（推荐）——在内嵌画布上长按节点选择，顺序即对话填参顺序，画布载错可点「重新载入画布」。",
        image: "onboarding/quick-start-6.png",
      },
{
        title: "7. 编写能力描述与保存模板",
        text: "「编写能力描述」里可指定替换输入 / 输出节点（即 AI 可干涉的范围）与主输出节点（默认不选，结果不合预期时再手动指定）。填「模板名称」后点「保存模板」。",
        image: "onboarding/quick-start-7.png",
      },
{
        title: "8. LoRA 数据保存（智能同步模型权重）",
        text: "「系统管理 → LoRA 数据保存」自动扫描本机 ComfyUI 的 loras 目录（默认 `D:\\tool\\ComfyUI\\ComfyUI\\models\\loras`），把每个 `.safetensors` 的触发词、建议提示词、建议权重、来源与作者签名提取进表格——方便工作流生成时辅助导入模型权重、自动插入触发词。\n\n首次进入点「智能同步」建立索引（只扫描本机文件、不调用 API），之后装了新权重再点一次同步更新即可。「触发词 / 建议提示词 / 建议权重」可手动编辑并保存，未填的项会显示「未确认」。需要把整批 LoRA 删除重建时用「全量重建」（仅在索引异常时使用）。\n\n跑工作流时若模板标注了 LoRA 字段，系统会按当前对话的角色、风格与 LoRA 列表自动挑匹配项注入参数，省去每次手动填触发词。",
        image: "onboarding/quick-start-8.png",
      },
{
        title: "9. 对话输入 /w 调用模板",
        text: "回到作品对话，直接输入「/w」即可弹出模板选择提示，选定要使用的模板。\n\n还没有可用模板时，可以让 AI 从零搭一个，见 [AI 搭工作流第 3 步](guide:workflow#3)。",
        image: "onboarding/quick-start-9.png",
      },
{
        title: "10. 按节点顺序填参",
        text: "选定模板后，按模板选择的节点顺序展示各节点暴露的参数，逐项填写提示词、宽高、LoRA、图片输入口等。",
        image: "onboarding/quick-start-10.png",
      },
{
        title: "11. 运转工作流",
        text: "提交后工作流在后台运转，生成中的媒体槽在对话中占位，完成后图片 / 视频 / 音频原位回填、不新增对话轮。\n\n要让剧情高潮自动出图，可在媒体设置里把模板绑为插画 / 视频 / 音频模板。",
        image: "onboarding/quick-start-11.png",
      },
    ],
  },
  {
      id: "story",
      title: "剧情扮演",
      summary: "绑定角色卡与世界书，开始沉浸式剧情创作",
      steps: [
        {
          title: "创建作品并绑定角色卡",
          text: "首页集中了仓库、角色卡、世界书入口；点击左上角就能新建仓库和小仓库，其中新建小仓库（作品）时会弹出「仓库绑定」：选择角色卡即可（卡内嵌世界书导入时已外拆为同名独立世界书，绑卡后自动加载）；也可从首页「角色卡」入口卡或资产管理一键建作品。",
          image: "onboarding/story-1.png",
        },
        {
          title: "挂世界书与偏置预设（可选）",
          text: "每个仓库下方都有三个按键：最左侧用于绑定角色卡和世界书（打开「仓库绑定」弹窗），中间是编辑仓库名称，右侧是删除按钮。在「仓库绑定」弹窗里：独立世界书可另挂别的书（不绑则自动用与卡同名的世界书）；偏置预设选本作品专属预设，留空则用全局激活预设。ST 格式预设从首页「预设」入口卡导入：片段可开关、可拖动排序，越靠后越接近生成点、遵守越强。",
          image: "onboarding/story-2.png",
        },
        {
          title: "开始剧情对话",
          text: "绑定成功后进入对话界面，刚进入就会有初始对话，直接输入即可继续（开场卡决定第一句）。剧情模式默认启用：只带上一次剧情轮的正文作为历史；角色会自主行动，尝试失败也会写成未遂或受挫，而不是静默消失。上方功能栏的功能变多了，就说明世界书与角色卡导入成功。",
          image: "onboarding/story-3.png",
        },
        {
          title: "角色状态与回合维护",
          text: "正文中模型会输出 <status> 绿框战报（在场、所在等），系统随回合写回角色状态，并自动维护表格与纪要；值得长期保留的新知识由 Curator 沉淀进世界书与知识库。对话上方功能栏第 5 个开关控制是否开启剧情生成多元数据，第 6 个是其中的具体参数调整按钮。",
          image: "onboarding/story-4.png",
        },
        {
          title: "多元数据插入面板",
          text: "点对话上方功能栏的「多元数据插入」按钮打开面板：最上方是生成图片 / 生成视频 / 生成音频三个开关，支持多选（至少勾选一项）。保存预设会自动开启「剧情自动生成」，剧情高潮点按勾选类型分别生成。注意视频没有独立素材来源：它用图片分区里每个角色的底图作首帧，所以想出视频要先配好图片分区。\n\n角色外貌来源二选一：合集卡（主要角色写在世界书条目中）用「条目模式」，从当前小仓库世界书命中的角色视觉条目读取外貌；多角色卡 + 一本世界书的玩法用「角色卡模式」，从本作品绑定的角色卡描述读取外貌，适合纯机制世界书。\n\nLoRA 模式三档：无 LoRA——只用角色底图，不加载角色或风格 LoRA，此时提交的工作流模板也不需要标注 LoRA 字段；单 LoRA——为每个角色绑定一个 LoRA，生成时自动添加 LoRA 节点完成多人物生成（支持多角色多 LoRA），某个角色没有专属 LoRA 就落到「兜底风格 LoRA」；多 LoRA——角色要 LoRA、风格也要 LoRA 的情况，固定加载默认风格 LoRA 并叠加全部在场角色 LoRA。\n\n提示词模式看生图模型是谁：用 ComfyUI 本地生成建议选 Anima（质量行 + 内容 tags / 英文描述），可固定质量提示词与负面提示词；用 GPT Image、Banana 之类 API 生图选自然语言；用 Niji 出图就选 Niji（主体 / 风格 / 附加 / 后缀）。\n\n生图时点二选一：高潮点模式出单张定格图；首尾帧模式按剧情首帧 + 尾帧生成、全程覆盖剧情与对白（此时视频自动变为首尾帧剧情影片，含本段全部对白）。\n\n图片工作流模板的节点要求：必须标注「提示词」字段，否则无法注入剧情提示词、保存不了；建议再标注——负面提示词（用固定负面时才参与）、Latent 宽度与高度（不标则 1K / 2K / 4K 尺寸不注入）、LoRA 字段（单 / 多 LoRA 模式必须，否则角色 LoRA 不生效）、角色底图槽位（图生图锁定角色一致性，纯文生图可不标）。\n\n视频模板没有额外必标字段，每名角色的参考图自动取图片分区该角色的底图作首帧；勾「智能模态」后，剧情动作剧烈（奔跑、律动）时自动改用视频模板，静态画面仍出图。音频模板（IndexTTS 系语音合成）必须标注「角色台词」（voice_text），否则无法注入台词、保存不了；建议再标注「参考音轨」（voice_reference）启用音色克隆——每个角色准备一条参考音轨，台词按角色筛分逐角色合成，旁白 / 叙述句不配音。\n\n按角色配置区逐行填：角色名（与剧情中一致）+ 角色 LoRA 与权重 + 底图（用于角色一致性）。若某角色既无 LoRA 也无底图且无兜底风格，面板会红字提示该角色出图缺少一致性锚点。",
          image: "onboarding/story-5.png",
        },
      ],
    },
  {
    id: "canvas",
    title: "画布创作",
    summary: "把生成内容铺在画布上编排",
    steps: [
        {
          title: "进入画布",
          text: "进入作品后点功能栏的对话/画布切换按钮（⇄ 图标）即可在对话与画布视图之间切换。（旧版「顶部工作模式三选一：剧情模式/多元数据生成/编辑模式」的切换器已废弃，入口合并进顶栏功能栏；旧链接 #/story、#/generate、#/code 仍作兼容解析。）",
          image: "onboarding/canvas-1.png",
        },
        {
          title: "画布里照常对话",
          text: "画布视图的对话框与对话模式完全同源：同一发送链路，普通消息、/w 选模板、灵感卡插入都可用。输入栏默认折叠为悬浮小球（把空间留给画布），点小球展开，再点左下角按钮收起。",
          image: "onboarding/canvas-2.png",
        },
        {
          title: "工作流模板节点",
          text: "对话里输入「/w」选定模板后，画布自动投影出「工作流工具」节点：未确认时逐节点显示迷你 ComfyUI 画布预览（超过 8 个只显示前 8 个）；双击节点打开编辑器填参数或 AI 编排，点「选择完毕」锁定参数，封面徽标同步显示已选节点数；点「运转工作流」提交 ComfyUI 后台运转，「生成中」占位节点完成后被生成内容节点原位替换。",
          image: "onboarding/canvas-3.png",
        },
    ],
  },
  {
    id: "workflow",
    title: "AI 搭工作流",
    summary: "用自然语言生成 ComfyUI 工作流",
    steps: [
        {
          title: "1. 前置：配好嵌入模型（设置 → 模型）",
          text: "AI 搭工作流靠节点知识库做语义检索（RAG），检索质量取决于嵌入模型。先到「⚙ 设置 → 模型」最下方的 Embedding 区块配好嵌入模型：云端可选智谱 embedding-3、OpenAI text-embedding-3，本地可选 Ollama 的 qwen3-embedding（都有快捷预设可一键填入），填完点「测试嵌入模型」确认连通。\n\n强烈建议配置：不配嵌入模型的话，节点知识库没有向量化能力，RAG 检索会大打折扣，AI 搭工作流时就找不到（或找错）可用节点。",
          image: "onboarding/workflow-1.png",
        },
        {
          title: "2. 节点知识库：增量同步就行",
          text: "确认「⚙ 设置 → 路径」里 ComfyUI 访问地址已填、ComfyUI 已启动，然后到「工作流管理 → 节点知识库」点「增量同步」建立节点索引：它只抓取本机已装节点自带的说明信息入库，不调用大模型、不花费 tokens；之后装 / 卸了节点，再点一次增量同步更新可用节点集即可。\n\n别随手点「全量重建」：它会走 api_key 把全部节点包重新嵌入一遍，要花钱（云端嵌入按量计费），除非索引异常否则用不上。",
          image: "onboarding/workflow-2.png",
        },
        {
          title: "3. AI 搭工作流：推荐这样设置",
          text: "进入「工作流管理 → AI 搭工作流」，输入框用大白话描述想要的效果（搭建走「对话模型」，强模型一次到位的成功率明显更高）。推荐组合：不用先选骨架底座（选了会自动切到增量模式，那是在现有工作流上小步修改的玩法），勾上「精简直连」+「顾问模式」——精简直连信任强模型一次到位、只调 1 次模型、最快不超时；顾问模式先用大白话讲清方案，确认后再动工。两个都开，从零一步到位。",
          image: "onboarding/workflow-3.png",
        },
        {
          title: "4. 方案确认：点「同意执行」",
          text: "顾问模式下 AI 会先发一条方案消息，用大白话讲清打算用哪些节点、怎么搭（如图）。检查没问题点「同意执行」，AI 才真正动画布生成工作流；想改需求就点「编辑」在方案上直接改，改完「保存并执行」；不想要就「取消」。方案里提示缺节点时：点「去安装」装对应节点，或点「用本机平替重搭」改用你已装的同类节点重新出方案。",
          image: "onboarding/workflow-4.png",
        },
        {
          title: "5. 最终结果与复用",
          text: "执行完成后，搭好的工作流就铺在右侧 ComfyUI 画布上（如图），可以直接运转出图验证效果；想调整就继续对话描述（例如「把采样步数改成 30」），搭建进度会自动存进会话、下次进入可恢复。满意后可以把它收进「工作流管理 → 工作流模板库」（选择文件导入 / 扫描默认目录），之后对话里输入「/w」随时调用（见「快速开始」）。",
          image: "onboarding/workflow-5.png",
        },
    ],
  },
];

// 固化流程 01/02/03：内容各自独立成节，但**左侧栏只占一项**
// （curingFlows，页内按流程分组讲解）——左栏不再被固化章节撑满。
// 「对话自动创建流程」（自定义流程）2026-09-11 用户定案暂缓展示：
// 旧 id create-curing-process 的别名映射保留（旧 hash 不失效），内容交后续版本再上。
const CURING_FLOW_SECTIONS: GuideSection[] = [
  {
    id: "curing-process",
    title: "固化流程 01：批量生图",
    summary: "智能编造一次性批量生图并固化为可重放预设",
    steps: [
      {
        title: "1. 让智能编造跑批量生图",
        text: "在「智能编造」对话里用大白话讲清楚「按哪份文档、分几批、跑哪个模板」。Agent 会自动拼出计划：读取文档 → workflow.read_exposed_fields → workflow.submit_batch 批提交 → media.collect_comfy_outputs 回收产物，配额与步数按文档规模算好（steps=3 / GPU≤14 / LLM≤0）。确认没问题点「批准执行」，Agent 就把整批送进队列。\n\n意图写得越具体越好——把文档路径、模板、LoRA、套数一起交代。出现「离审批」时点「批准执行」即可，CPU 配额与域校验照旧生效。",
        image: "onboarding/curing-process-1.png",
      },
      {
        title: "2. 产物原位回填并自动固化为预设",
        text: "批提交运转完成后，全部图按顺序原位回填到对话里，每张卡片都有「查看原图 / 下载 / 重新生图 / 蒙化修改 / 发送至对话 / 设为封面」。整套流程自动固化成「草稿」配方——对话里点「保留」（或在 ⚙ 设置 → 智能体 → 固化流程预设 点保留）即列入清单；之后对话里出现同类目标时，Agent 优先整条重放，省去逐步探索 token。durable / expensive 步骤照常走审批与配额。\n\n提示：固化流程预设与「固化知识库」（⚙ 设置 → 智能体 → 固化知识库，`data/agent_knowledge/` 下的流程规范）互不替代——固化知识决定 Agent 怎么想，固化预设决定 Agent 怎么快跑。",
        image: "onboarding/curing-process-2.png",
      },
    ],
  },
  {
    id: "novel-to-collection-card",
    title: "固化流程 02：小说转合集卡",
    summary: "提供小说 + 一句话描述，Agent 自动生成合集卡；一路批准后即可拿到产物",
    steps: [
      {
        title: "1. 提供小说，一句话说清要什么",
        text: "在「智能编造」对话里把小说原文粘进输入框，或直接把 .md / .txt 拖进去（给文件路径也可以）。\n\n然后用一句话说清目标——**从头创建合集卡**、或**更新已有的合集卡**都可以：\n· 「这是《XX》小说，做成合集卡」\n· 「这是《XX》的更新版小说，更新已有合集卡」\n\n不需要你指定工具、路径或流程：Agent 会自动按章节感知阅读、切素材、先列条目清单给你确认，再逐批写条目。",
        image: "onboarding/curing-process-3.png",
      },
      {
        title: "2. 一路批准，拿到产物",
        text: "接下来只需在弹审批时点「批准执行」（durable 步骤逐个确认）。条目数不足 40、密度不达标（角色条目 ≥1800 / 机制 ≥800 / 编号 ≥600 / NSFW ≥400 字）会自动继续补写，中断也会自动续跑——不用反复催。\n\n完成后对话给出汇总（条目总数 / 达标 / 豁免 / 密度验收），消息下方就是**交付产物卡**（主卡 + 世界书：可预览 / 打开位置 / 下载）。要进资产库点「**同步到资产库**」即可（旧版自动备份）；每次完成自动存一版**版本快照**，可回档。",
        image: "onboarding/curing-process-4.png",
      },
    ],
  },
  {
    id: "card-worldbook-convert",
    title: "固化流程 03：合集卡转化（ST 卡）",
    summary: "把 ST（SillyTavern）卡转成作品内可用的合集卡：机械转写、正文逐字保留、卡目录三文件落盘",
    steps: [
      {
        title: "把 ST 卡交给智能编造，一步拿到合集卡",
        text: "把 ST 的 PNG / JSON 卡拖进「智能编造」对话，说一句「把这份 ST 卡转成作品内能用的合集卡」就行——不用指定工具、路径或流程。\n\nAgent 先跑 character.migrate_scan 体检（逐条列出 ST 注入位字段、渲染宏、好感度表格、空 keys 等待转写点），再用 character.migrate_mechanical 一步机械转写——零 LLM、不让模型逐条改写：① 删掉 ST 注入位字段，条目只留 content/comment/keys/constant/enabled 五字段；② 渲染宏转纯文本标记（<status>→【状态栏】、<roll>→【检定】、<encounter>→【登场】、<fate>→【命定预警】）；③ 好感度表格转【好感度 ≤-30 / 区间】档位文本；④ keys 空则补、超 6 裁短；⑤ entries 统一 list。转完立刻做无损验证：对源正文重放同一套规则逐字比对，一致才算过——原卡设定一个字都不会丢。\n\n落盘是卡目录三文件：card.json（B 态，内嵌全部条目）、worldbook.json（运行时真源）、regex.json（原卡正则原样保留），完成后自动存一版版本快照，可回档。\n\n点「批准执行」后，计划卡下方直接出现交付产物卡（角色主卡 + 世界书：可预览 / 打开位置 / 下载），不用去翻执行面板；计划任务是异步跑的，完成后这条消息会自动补上产物卡，刷新也还在。产物默认留在作品内，要进资产库（独立世界书）点产物卡右侧的「同步到资产库」即可（旧版自动备份）。",
        image: "onboarding/curing-process-5.png",
      },
    ],
  },
];

const curingFlows: GuideSection = {
  id: "curing-flows",
  title: "固化流程",
  summary: "三条固化流程：批量生图 / 小说转合集卡 / ST 卡转合集卡",
  groups: CURING_FLOW_SECTIONS.map((s) => ({
    // 组标题去掉「固化流程 」前缀，页内已在大标题下，避免重复
    title: s.title.replace(/^固化流程[：:\s]*/, ""),
    summary: s.summary,
    steps: s.steps,
  })),
  steps: CURING_FLOW_SECTIONS.flatMap((s) => s.steps),
};

/** 旧章节 id → 合并后章节 id：旧的 hash / 收藏链接（#/guide/card-worldbook-convert）不失效。 */
export const GUIDE_SECTION_ALIASES: Record<string, string> = {
  "curing-process": "curing-flows",
  "novel-to-collection-card": "curing-flows",
  "card-worldbook-convert": "curing-flows",
  "create-curing-process": "curing-flows",
};

/** 解析章节（含旧 id 别名）；未知 id → undefined（调用方回退第一节）。 */
export function resolveGuideSection(id?: string | null): GuideSection | undefined {
  const key = GUIDE_SECTION_ALIASES[String(id ?? "")] ?? String(id ?? "");
  return NEWCOMER_GUIDE_SECTIONS.find((s) => s.id === key);
}

export const NEWCOMER_GUIDE_SECTIONS: GuideSection[] = [
  ...BASE_SECTIONS,
  curingFlows,
  {
    id: "tools",
    title: "多功能工具",
    summary: "入口在「系统管理 → 多功能工具」：十个小工具逐个说明",
    steps: [
      {
        title: "GIF 转精灵图",
        text: "拆开 GIF 逐帧，剔掉不要的帧后按网格拼成一张精灵图——做表情包、逐帧立绘素材时用，帧剔除与动画预览即时生效。",
        image: "onboarding/tools-1.png",
      },
      {
        title: "精灵图转 GIF",
        text: "按行列切开精灵图，挑帧、调帧率后合成 GIF——和「GIF 转精灵图」互为反向工具，精灵图与动图随取随换。",
        image: "onboarding/tools-2.png",
      },
      {
        title: "调色盘",
        text: "从图片提取主色，可一键设为当前配色，让 AI 生图自动沿用这套色调——想固定作品整体画风时很好用。",
        image: "onboarding/tools-3.png",
      },
      {
        title: "分辨率缩放",
        text: "2K 转 1K 这类等比缩放，Lanczos 重采样尽量不糊；档位按长边给、短边按原图比例自动算，不会把图拉变形。",
        image: "onboarding/tools-4.png",
      },
      {
        title: "文本清理",
        text: "移除 Markdown 标记、空行并保留可见正文——从别处复制来的带格式文本，清洗后再进提示词或正文。",
        image: "onboarding/tools-5.png",
      },
      {
        title: "文本拼接",
        text: "按自定义分隔符拼接多行文本——把一堆触发词、标签合并成一行时用。",
        image: "onboarding/tools-6.png",
      },
      {
        title: "文本加料",
        text: "在每两个字符之间插入指定字符串——比如逐字插换行、加分隔符做特殊排版。",
        image: "onboarding/tools-7.png",
      },
      {
        title: "字数统计",
        text: "实时统计中日文字数、英文单词、标点和字符——卡字数写正文、提示词时用。",
        image: "onboarding/tools-8.png",
      },
      {
        title: "文本转义",
        text: "UTF-8 字符串、Python 字节、Hex 与 JSON 互转——处理转义串、调试数据格式时用。",
        image: "onboarding/tools-9.png",
      },
      {
        title: "简繁切换",
        text: "繁简中文双向转换与引号替换——角色卡、世界书简繁体统一时用。",
        image: "onboarding/tools-10.png",
      },
    ],
  },
];
