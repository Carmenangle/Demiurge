"""按目标模型协议把剧情场景渲染为最终生图提示词。"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping

from app.services import image_prompt_extract, prompt_clean


ANIMA_QUALITY_TAGS = (
    "masterpiece, best quality, score_7, score_9, very aesthetic, ultra detailed, "
    "fair skin, high contrast, amazing quality, newest, absurdres, 8k, high resolution, "
    "refined details, good anatomy, good shading, sharp focus, anime coloring"
)
ANIMA_NEGATIVE_TAGS = (
    "worst quality, low quality, score_1, score_2, score_3, artist name, lowres, jpeg, "
    "cropped, bad composition, artifacts, bad proportions, error, inaccurate limb, "
    "bad anatomy, bad, ugly, terrible, extra fingers, fewer fingers, missing fingers, "
    "extra arms, extra legs, inaccurate eyes, extra digit, fewer digits, trademark, "
    "username, watermark, signature, text, words"
)

PROFILE_IDS = ("anima_tags", "krea2", "natural_language", "niji_sections")


def _diag_list(diag: dict[str, object], key: str) -> list[object]:
    """取诊断字典里的列表字段；缺失/类型不符回退空列表（mypy 可收窄）。"""
    value = diag.get(key)
    return value if isinstance(value, list) else []


# 同轮成稿剥离后，内联插画义务的提示词合同（inline_generation_instruction /
# near_generation_contract）与输出预留（inline_output_token_reserve）一并移除；
# 画像成稿由 generate() 独立链编译，格式合同在 _system() 内单一属主。


_ART_DIRECTION = (
    "所有模式都必须先在内部完成同一套艺术决策，再转换成目标格式，但不要输出分析过程："
    "第一，从高潮事实中选出唯一视觉命题，即一眼能记住且最能代表本段转折的动作、道具、接触点、"
    "空间关系或视觉反差；禁止用‘唯美画面’‘戏剧性场景’等空泛主题。"
    "第二，确定主体层级，只设一个第一视觉中心和必要的次级引导；最高细节、最强对比和最清晰边缘"
    "集中在剧情关键主体或关键部位，其余人物、服装、头发和背景按层级逐渐概括，禁止全画面等密度。"
    "人物互动高潮中，第一视觉中心必须是人物的面部、目光、动作、接触点或人物关系；"
    "物件只能作为辅助视觉装置，通过反射、遮挡、引导线、色彩重复或材质呼应把视线导回人物。"
    "只有发现、开启、争夺或取得物件本身就是剧情高潮时，物件才允许成为第一视觉中心，"
    "且仍须用人物反应或动作建立剧情关系。"
    "第三，统一色彩与材质母题，从场景事实中选择受控的主色、辅色和少量强调色，选择一至两种关键材质，"
    "让服装、道具和环境互相呼应；禁止堆叠互不相关的颜色、材质、画风和装饰词。"
    "第四，设计可解释的光影因果，明确光源、方向、光质、照亮对象、材质反应、阴影去向及其如何强化"
    "视觉中心和情绪；禁止只罗列 cinematic lighting、rim light 等孤立术语。"
    "第五，再选能服务上述命题的镜头、视角、景深和构图，引导视线经过次级元素回到视觉中心。"
    "转换格式不得削弱上述色彩材质母题、光影因果、镜头构图与主体层级。"
)

_VISUAL_BLUEPRINT = (
    _ART_DIRECTION
    + "采用‘硬事实锁＋开放视觉槽’。硬事实锁包括在场人物、世界书稳定外貌、当前外观变化、正文明确的"
    "服装状态、实际动作、人物关系、地点和剧情结果；不得因艺术化而改写事实。开放视觉槽包括正文未明确规定的"
    "微动作、姿态、视线、镜头、构图、光影、天气表现、背景活动和材质细节；必须根据当前时间、地点、天气、"
    "情绪、人物处境与视觉因果进行合理联想，使画面成为具体瞬间，而不是用通用模板填空。执行‘缺失硬事实补全’："
    "生成完整画面所需的当前服装、具体动作或姿态、地点环境、人物位置、镜头、构图、光影与背景中，任何未明确项"
    "都必须补出一个具体答案，不得留空、不得使用占位语或通用模板。开放视觉槽不得"
    "引入重要新人物、关键道具或新事件，不得改变硬事实与剧情结果。只展开服务唯一视觉命题的发型、妆容、"
    "服装、背景和装饰细节，无关项简写或省略。若输入确实没有稳定外貌，可根据明确的年龄、性别、职业、"
    "身份、阵营和名称调性，保守补足不改变剧情事实的典型外貌与服装；不得据此改写年龄、关系、动作或阵营设定。"
    "人物视觉事实不足时，可改以当前环境为第一视觉中心，让人物退居次级叙事位置。"
    "人物、动作、镜头、光影、背景和构图必须形成同一视觉系统。"
)

_COMMON = (
    "输入是当前剧情高潮画面的 JSON。先锁定输入明确给出的剧情事实，再主动设计输入未规定的开放视觉槽；"
    "开放设计必须能由当前时间、地点、天气、情绪、人物处境或视觉因果解释，"
    "不得借用其他会话、历史图片或固定成人模板，不得创造未出场人物。"
    "角色姓名只用于把剧情人物关联到对应外貌条目，不是生图语义；转换时必须按人物的空间角色改写为"
    "primary adult character、second adult character 等中性指代，最终提示词禁止出现原姓名、音译姓名或"
    "用姓名充当外貌。多角色必须用各自具体外貌、服装、动作和位置区分。"
    "角色 LoRA 只能辅助视觉身份，不能代替文字外貌；所有模式都必须把条目中与当前画面有关的发色、"
    "发型、发饰、五官、体型、当前服装和鞋袜翻译成具体英文，禁止用 preserve identity、"
    "established appearance、stable appearance 或 current clothing condition 等空泛身份锁代替。"
    "角色条目的基础穿着只作默认值，剧情正文或 wardrobe 中的脱下、破损、凌乱、沾污等当前状态优先。"
    "任何剧情关键物件若没有可靠英文通用名，必须用材质、几何外形与尺度、可见结构或运动方式、当前功能及"
    "与人物/环境的交互来建立可画身份；只采用输入证据支持的维度，不猜内部原理，也不得因缺少术语而省略。"
    "人物、服装、动作、镜头、光影、背景和构图必须彼此一致。"
    # 【画面自洽】（2026-09-21 第 38 刀 · 用户真机实锤）：这段原先**只写在【多人分述】块内**、
    # 以「画面里有两名以上主体时」为条件 ⇒ 单人帧（`1girl, solo`）根本不受约束。
    # 真机 L3·anima 单人帧同时写出 `face down ass up` + `first-person view` + `close-up on face`
    # + `looking at viewer`：后向专属信息（翘臀／阴部／背后绑缚／脊线）配了**无方向**的机位
    # ＋**正面专属**构图 ⇒ 模型按正视图渲染 ⇒ 结构崩坏（用户原话「小穴张在了背上」）。
    # ⚠⚠ 因果方向（用户 2026-09-21 **当场纠正**）：**内容优先，机位是因变量** —— 先列全并
    # **必保**剧情拿到的画面信息，再据此反推「最能呈现它」的观察机位；**不是**反过来用机位
    # 去删改／回避内容。（本条首版写成「机位写明了方向就得看得到所写信息 ⇒ 不得同时写正面
    # 构图」＝把因果写反了，已被用户否掉。）
    # ⇒ 拆成**无条件**条款（不再挂多人条件），并把原多人块里的姿态自洽句一并收编到这里，
    # 使「单主体 / 多主体」共用**同一份判据**（单一真源，不在两处各写一份）。
    # 【两侧都有 · 偏侧面分档】（2026-09-21 第 39 刀 · 用户细化）：原句把「内容两侧都有」判成
    # 一句话「取能**同时**看见两者的机位（背后视角 ＋ 回头）」——太粗。用户口径：
    # 「两侧都有的情况就分为**正面偏侧面**和**背面偏侧面**，要区分偏向于正面还是背面就**看细节动作**，
    # 就比如双手掰开臀部，这就是背面偏侧面，双手托胸就是正面偏侧面……**不只是手部细节动作，
    # 可能还有别的细节动作**」⇒ 偏向由**动作自身的接触面**判定（不限于手），不是按默认机位。
    # ⚠ 与【视角·第一人称】块的「身体朝向按画面事实」不冲突，但要在本条里点明，避免模型把
    # 已写明的背向式姿势改写成正面来「迎合」正面偏侧面。
    "【画面自洽·机位由内容反推】**剧情画面信息是必保内容，镜头机位是它的因变量**："
    "先把你已经拿到的画面事实（人物、动作、身体部位、服装当前状态、接触点）**列全并全部保留**，"
    "再据此选择**最能呈现这些信息**的观察方向与景别；不得反过来用一个默认机位去删改、回避"
    "或改写已经拿到的内容。只可能从背后看到的部位与状态（翘起的臀部、臀缝、肛门、阴部、"
    "脊线与背部、背后的绑缚、身后的交合处）⇒ 机位取**从背后看**（view from behind／"
    "from behind／rear view）；只可能从正面看到的（面部表情、胸前、正面服装）⇒ 才取正面机位；"
    "内容两侧都有时（既写了臀部又写了面部表情）⇒ 机位取**偏侧面**，偏向哪一侧由**细节动作的"
    "接触面**决定：动作的接触点或受力部位落在**背面可及处**（臀部、臀缝、肛门、阴部、脊背、"
    "身后的绑缚或交合处）⇒ **背面偏侧面**（three-quarter back view／from behind）；落在"
    "**正面可及处**（胸部、乳房、面孔、正面服装与饰物）⇒ **正面偏侧面**"
    "（three-quarter front view／from the front）。判断看的是**动作自身的接触面，不限于手**——"
    "双手掰开臀部＝背面偏侧面、双手托胸＝正面偏侧面；吮吸与舔舐、抵靠与磨蹭、穿插、牵引等"
    "任何带明确接触点或受力方向的细节动作同理。两侧动作方向相反时**只取一侧**"
    "（取接触点更具体、动作更明确的那一侧），另一侧只留 head turned back over shoulder 回头；"
    "**身体朝向仍以本轮画面事实为准**（不得把已写明的背向式姿势改写成正面，此时应取背面偏侧面）。"
    "**同一主体不得被写成互相矛盾的姿态或视线**（不得既写「背对镜头」又写「正对镜头」，"
    "也不得既写「站」又写「跪」）；构图以小说原文为准，不得自创与原文冲突的机位。"
    # 【画面自洽·物理可达性】同刀同源（用户 2026-09-21 口径）：「要考虑下物理逻辑关系」——
    # 用户原话：「凌若冰身材丰满，让她跪着翘起屁股、手要够着掰开小穴，手的长度不一定足够；
    # 如果要够着，手要将肥臀挤压着有压痕才能够到才是」。⇒ 同样**内容优先**：动作保留，
    # 把让动作成立的**中间物理事实**补写出来（挤压／压痕／绷紧），不得写成互不接触地叠放。
    # 另一实证：`arms bound behind back, wrists tied` 与 `fingers spreading labia` 同稿共存且
    # 画面只有 `1girl, solo` ⇒ **同一主体物理互斥**；按既有「出处闸」口径只保留有本轮正文／
    # 画面事实出处的一项（本例锚点写了「探手到身后」⇒ 留动作、去模型自加的绑缚）。
    "【画面自洽·物理可达性】**内容里的动作优先保留**，但只写主体**做得到**的：肢体长度、"
    "关节活动度与身体朝向决定手能不能够到目标；够到目标必然伴随挤压与形变，必须把让该动作"
    "成立的**中间物理事实**一并写出（如手指陷进丰臀、臀部被压出凹陷、皮肤绷紧），"
    "不得写成两个物体互不接触地叠在一起。**同一主体物理互斥的两项，只保留本轮正文或画面"
    "事实有出处的那一项**（如「双手反绑在背后」与「用手指掰开」互斥；锚点只写了"
    "「探手到身后」⇒ 保留掰开，不写绑缚）。"
    + _VISUAL_BLUEPRINT
)

_ANIMA_SYSTEM = _COMMON + (
    "你在为 Anima 系动漫模型生成正向内容提示词。必须先把剧情高潮转换为一个具体可见的图像概念，"
    "而不是把高潮中出现的人物、道具、车辆和建筑全部并列画出。为此设计一个视觉装置，例如反射、框架、"
    "遮挡、尺度反差、前景引导、重复色彩、材质呼应或负空间，让剧情关系能直接被看见；不要照搬示例中的"
    "眼镜、沙漏、飞蛾或其他具体物件。普通剧情清单、标准中景群像和多个竞争中心都视为失败。"
    "只输出 JSON 对象，字段必须为 visual_hook、primary_focus、supporting_elements、content。"
    "visual_hook 用英文写清具体可见的视觉装置；primary_focus 只能是一个第一视觉中心；"
    "supporting_elements 是英文字符串数组，最多两个，且每个都必须直接服务第一视觉中心。"
    "次要人物与背景应降为轮廓、倒影、遮挡、模糊层或负空间，不得与第一视觉中心争夺清晰度和对比。"
    "content 是最终英文内容段：先写英文 Danbooru tags，"
    "再紧接一至三句英文自然语言画面描述。tags 用英文逗号分隔，负责人数与角色、稳定外貌与当前变化、"
    "发型、妆容、表情、服装、姿态、镜头、视角、景深、构图、背景、光影和风格等硬约束。"
    "自然语言不得机械复述 tags，而要落实已经选定的唯一视觉命题，补足主体与环境的空间关系、材质与反射、"
    "光线传播、细节主次和叙事情绪；最高细节留给剧情关键主体或关键部位，其余内容按层级逐渐概括。"
    "自然语言必须写成一至三句连续英文描述，不得写 Her body:、Bound:、Position:、Appearance:、Action:"
    "等标题式小段或任何冒号标签；同一句内的可见属性只用英文逗号连接。"
    # 【事实边界】（2026-09-20 用户口径）：三档在**同一份窗口**下会各自脑补出互斥的场景——
    # L1 真机实锤：anima 写 `teacher office / wooden desk`、natural 与 niji 写
    # `bed / mattress / bedside lamp / dark bedroom`，而原文是**教室**（窗口 59 字里没有地点）。
    # anima 档的「视觉装置」是**处理方式**（反射/遮挡/框景/重复），不是引入新道具的许可，
    # 故这里显式把装置也约束在输入已有元素上。
    "【事实边界】画面里的人、物、服装、场景与配色只能来自输入画面事实；视觉装置必须由输入已有元素构成"
    "【多人分述】画面里有两名以上主体时，**必须逐人分述、各自点名**：每人写明**画面位置**（镜头左侧／镜头右侧／前景／后景）＋**该人自己的动作与可见身体部位**。**即使两人动作完全相同，也要分别写**（例：「镜头左侧的A翘起臀部、掰开臀瓣；镜头右侧的B同样翘起臀部、掰开臀瓣」），不得只写一次动作让读者以为是同一人；不得只给人物名而不给动作，也不得只给动作而不给位置。"
    # 第 42 刀·D（2026-09-22 用户定案）：anima 首行是**扁平逗号序列**（用户明确否决分块／分隔符
    # 方案，原话「不是分块，而是下方的文段不够详细」）⇒ 归属只能靠「**首行相邻聚拢 ＋ 文段写全**」
    # 编码。真机事故：衣物 tag 只有一份，模型拿到一个多余的归属者就把女主服装绑到主人公身上
    # （成稿含 `adult man`，女主变裸）。
    # ⚠ **只加在 anima 档**：natural／niji 的输出本身就是逐人成段的散文，归属天然可辨
    #    （用户原话「自然语言和 niji …都是一个文段了，所以说很容易区分从属关系」）。
    # ⚠ 共享的【多人分述】块措辞**逐字节不动**（`test_三档多人分述条款措辞一致` 的切片锁；
    #    本块追加在其切片终点**之后**，故该锁不受影响）。
    "【多人分述·anima 从属绑定】本档第一行是**扁平逗号序列**、没有分块符，"
    "所以每个画面主体的**衣着与身体部位只能靠位置聚拢与文段点明来绑定**，额外要求："
    "①文段里每名主体除位置与动作外，**还必须写明该人自己的可辨识身份**"
    "（发色／发型／瞳色／体型等稳定外貌中至少一项，用来把两名主体区分开，不得两人共用同一个泛称）"
    "**与该人当前的衣着及状态**（穿着什么，以及裸露、镂空、半褪、破损、凌乱、沾污到什么程度）"
    "——**衣着一律写在它所属那个人的分句里**，不得把服装、配饰、鞋袜单独列成一条让人猜归属。"
    "②**朝向**（面向谁／背对谁／是否回头）与**接触点**（谁的手碰到谁身上的哪个部位）"
    "同样只能写在所属主体的分句里。"
    "③⚠ 同一件服装、同一个身体部位、同一处接触**只能归属到一个主体**，"
    "分句里必须写明「谁的什么正处于什么状态」；"
    "不得把所有服装 tag 集中提到两人之前或之后让它归属不明。"
    "④第一行 tags 按主体**相邻聚拢**：把同一主体的身份、稳定外貌、当前服装与状态、姿态动作 tag "
    "紧挨着写在一起，各主体按画面顺序依次排列——**不加任何分隔符**，仍是英文逗号分隔的扁平序列。"
    "【具体化必须有出处】画面事实分两级，**具体化一律要有出处**：①**可直接写**——输入画面事实里出现过的可见项（人、动作、身体部位、服装、物件、地点、光线）；②**只能中性化**——只给了类别、没给细节的（有「墙」无材质、有「床」无床品、有「衣服」无款式）只写类别，**不得补具体材质、颜色、款式**；③**地点、道具、光源、场景类型只能来自输入**，不得新增或升级——「背靠墙壁」不是「窄巷」「走廊」，「坐着」不是「床边」，输入没写灯就不写台灯、蜡烛、床头灯；④人物外观（发色、瞳色、体型、服装款式）只能来自「外观」栏与输入画面事实，两边都没有的项**留白不写**。"
    "（反射、遮挡、框景、重复、负空间是处理方式），不得新增输入未提及的新道具、新场景或新配色；"
    "宁可少写也不编造。"
    # 【最高优先级·语言】（2026-09-20 真机 L3·anima raw#1 实锤）：本档原先**没有**这条声明，
    # 而 system_with_preset 会把防拦截预设整段前置（预设正文含「正文简体中文」等明文要求）。
    # 真机 raw#1 是一次 **265 字纯中文叙事**（221 CJK / 1 行、连 JSON 都没输出）⇒ 首轮即判死。
    # 与 natural/niji 两档同一句话（有测试锁三档措辞一致），只是把「四字段 + content」列全。
    "【最高优先级·语言】无论上文（若存在角色扮演设定或世界观规则）如何规定正文语言，"
    "你本次 JSON 的 visual_hook、primary_focus、supporting_elements 与 content 都必须是纯英文，"
    "不得以任何理由改写成中文或其他语言；不要使用任何中文字符（含标点）。"
    "content 中不要输出质量词、LoRA 触发词、LoRA 权重、参数、标题、Markdown 或负面提示词；质量行由程序添加。"
)

_KREA_SYSTEM = _COMMON + (
    "你在为大参数自然语言图像模型 Krea2 生成剧情插画提示词。"
    "输入是已经确定的剧情高潮画面 JSON。将输入中的高潮内容准确转换成一段可直接用于 Krea2 的英文自然语言提示词。"
    "不得续写剧情，不得改变人物、动作、服装、关系、地点或剧情结果等硬事实锁；"
    "允许为开放视觉槽补足与时间、地点、天气、情绪和人物处境一致的微动作、镜头、构图、光影与背景细节。"
    "生成提示词前，在内部严格按以下六个维度依次解析，但不要输出分析过程、标题、编号或分段标签。"
    "第一，构图与留白占比。根据高潮动作、人物数量和空间关系确定主体位置、画面占比、视觉中心、"
    "前后景关系、视线引导和适合当前场景的负空间比例；留白必须服务人物关系、动作方向或环境纵深，"
    "不得机械套用固定比例。"
    "第二，角色外貌与服装。完整保留实际人物、稳定外貌、当前外观变化、发型、体型、表情、服装款式、"
    "服装当前状态、配饰、鞋袜和可见动作；角色 LoRA 已经确定人物身份时不得重新设计五官，"
    "但 LoRA 不能代替文字描述，仍须把条目中的每项可见外貌翻译成具体英文。角色条目的基础穿着只作"
    "默认值，剧情正文或 wardrobe 中的脱下、破损、凌乱、沾污等当前状态优先。不得添加未出场人物或"
    "擅自改变服装。禁止用 preserve identity、established facial structure、defined by the bound model、"
    "stable appearance、current clothing condition 等占位句代替实际的发色、发型、五官、体型和服装。"
    "第三，摄影风格、镜头视角与透视表现。根据高潮内容选择景别、焦段、相机距离、机位高度、俯仰角度、"
    "观察方向、景深和透视压缩程度；镜头必须清楚表现动作主体、人物关系和空间方向，"
    "不得为了复杂构图而使用与高潮无关的夸张机位。"
    "第四，有机材质与画面质感。描述肌肤、头发、衣料、汗水、液体、植物和其他有机表面的质感、湿度、"
    "柔软度、褶皱、拉伸、压力与受光反应；材质表现必须服从实际动作、服装状态和环境条件，"
    "不得凭空增加身体变化或不存在的物质，也不得做皮肤纹理特写式的照片写实描述。"
    "第五，光影、层次与色彩设定。明确主光源的位置、方向、软硬、色温和强度，说明受光主体、材质高光或透光、"
    "阴影落向与空气透视；使用受控的主色、辅色和少量强调色形成前景、人物与背景层次，"
    "把最高对比和清晰度集中在高潮动作或人物关系上。"
    "第六，画质质量与完成度。保证人体结构、肢体承重、接触关系、服装受力、物体遮挡、透视、光照和材质反应"
    "彼此一致；强调结构一致、主体清晰、轮廓稳定、细节克制、边缘干净。"
    "媒介与画风完全交给已加载的 LoRA 决定：禁止照片写实向描述——不得使用 photorealistic、photograph、"
    "live-action photography、realistic skin、skin texture、visible veins/pores 等真人媒介与皮肤特写词汇，"
    "有机材质只描述受光与柔软度等可见结果，不做照相机式的纹理写实。"
    "优先使用输入已有的 visual_thesis、hierarchy、palette_material、lighting_logic、camera 和 composition；"
    "只有这些信息缺失时，才补足使空间、透视、解剖、承重、遮挡、光照和材质成立的必要内容。"
    "实际加载的角色 LoRA 和风格 LoRA 决定人物与媒介风格；不得添加 LoRA 触发词、LoRA 权重，也不得使用"
    "anime、manga、3D render 等词擅自锁定媒介。"
    "最终输出且仅输出两行，用一个换行分隔，两行都不得夹杂中文。"
    "第一行：英文内容 tags 与构图 tags，用英文逗号分隔——覆盖人数与角色、稳定外貌与当前变化、"
    "发型、表情、服装与当前状态、姿态动作，以及镜头景别、视角、光影和构图要素；"
    "不写质量词（质量 tags 由程序前置，重复会被去重）。"
    "第二行：内容文段，一至三句连贯英文自然语言，按上述六维顺序落实唯一视觉命题、"
    "人物关系与全部空间关系；不得输出 JSON、Markdown、解释、自检、拒答、参数、"
    "LoRA 名称/权重或负面提示词。"
)

_NATURAL_SYSTEM = _COMMON + (
    "你在为 GPT Image、Banana 等自然语言图像模型生成提示词。输出一段连贯、具体的自然语言画面描述，"
    "以唯一视觉命题开场，而不是从人物属性清单开场；用自然语言清楚说明主体稳定外貌与当前变化、"
    "气质、发型、妆容、表情、服装款式和材质受光、动作、镜头视角与景深、构图、光影、背景和画风。"
    "必须明确第一视觉中心、次级引导、受控色彩与材质母题，以及光从何处照到何物并产生何种材质和阴影效果；"
    "不服务视觉命题的细节应省略。"
    # 【定格·静态一帧】（2026-09-20 用户口径，L1·natural 真机成稿）：原提示词只说「连贯、具体的
    # 自然语言画面描述」，模型于是把剧情**动态过程**照搬过来（脱下内裤→面料滑过大腿→在脚踝处堆叠→
    # 等待下一步），还写了图像表现不出的气味与台词（「薰衣草洗衣液的气味」「the word 'teacher'
    # 仍悬在空气里」）⇒ 出图拿到的是「一串动作」而不是「一帧」。本条把这一档明确成**定格帧**。
    # ⚠ 动态版不是废弃：视频线（首尾帧）需要动作过程，届时另开档，不得回头放宽本档。
    "【定格·静态一帧】把这一刻**定格**成一张静止画面：只写这一帧里**已经呈现**的状态，用静态陈述句；"
    "不写动作的开始、过程、先后或结果，不写时间推进（begins to / starts to / then / before / after / "
    "continues to / slowly / gradually / is about to 这类），也不写「即将发生」的动作。"
    # 【只写画得出来的】同源（用户口径）：气味 / 声音 / 台词 / 心理活动都落不成像素；
    # 它们挤占篇幅还会把画面的焦点从「可见的一帧」带到「剧情解释」。
    "【只写画得出来的】禁止写图像无法表现的内容：气味、味道、声音、台词或对话、内心想法与心理活动、"
    "比喻性感受、剧情因果解释；人物说过的话只能用可见的嘴形、表情或姿态间接体现。"
    # 【事实边界】同源（真机 L2·natural 整段幻觉：20 字窗口被写成「废墟大厅 + 断裂立柱 + 红围巾」，
    # 而 L1 三档在同一窗口下分别脑补出「教室办公桌」与「卧室床铺」两套互斥场景）。
    "【事实边界】只描述输入画面事实里出现过的人、物、服装、场景与色彩；"
    "【多人分述】画面里有两名以上主体时，**必须逐人分述、各自点名**：每人写明**画面位置**（镜头左侧／镜头右侧／前景／后景）＋**该人自己的动作与可见身体部位**。**即使两人动作完全相同，也要分别写**（例：「镜头左侧的A翘起臀部、掰开臀瓣；镜头右侧的B同样翘起臀部、掰开臀瓣」），不得只写一次动作让读者以为是同一人；不得只给人物名而不给动作，也不得只给动作而不给位置。"
    "【具体化必须有出处】画面事实分两级，**具体化一律要有出处**：①**可直接写**——输入画面事实里出现过的可见项（人、动作、身体部位、服装、物件、地点、光线）；②**只能中性化**——只给了类别、没给细节的（有「墙」无材质、有「床」无床品、有「衣服」无款式）只写类别，**不得补具体材质、颜色、款式**；③**地点、道具、光源、场景类型只能来自输入**，不得新增或升级——「背靠墙壁」不是「窄巷」「走廊」，「坐着」不是「床边」，输入没写灯就不写台灯、蜡烛、床头灯；④人物外观（发色、瞳色、体型、服装款式）只能来自「外观」栏与输入画面事实，两边都没有的项**留白不写**。"
    "不得新增输入未提及的道具、服装、场景或配色，宁可少写也不编造。"
    # 「覆盖式」语言指令（2026-09-19 优化项②）：此前这里只有一句位于**块末**的
    # 「最终结果必须全部使用英文。」，而 system_with_preset 会把当前防拦截预设**拼在前面**
    # ——预设正文里有「正文简体中文」「推演当然使用中文啦」等明文要求（真机取证：真源预设
    # 193,412 字里 65,822 CJK，拼接后 system 前 8,737 字是预设段、模板只占末 1,962 字）。
    # 上游越权指令对「语言」这个话题拥有 2.4 倍篇幅优势 ⇒ 模型约 1/10 次整段写中文叙事，
    # 被 _errors 的纯英文硬判据判死掉兜底（§2.8 形态：282/311 字 CJK＝90.7%）。
    # 语言是本任务唯一必须压过剧情预设的约束，故写成**不可被上游指令覆盖**的独立声明，
    # 并前置到主体描述之前——不只结尾一句顺带叮嘱。
    "【最高优先级·语言】无论上文（若存在角色扮演设定或世界观规则）如何规定正文语言，"
    "你本次输出的提示词都必须是纯英文。这是本任务的格式要求，与该设定不冲突，"
    "不得以任何理由改写成中文或其他语言；不要使用任何中文字符（含标点）。"
    "不要使用标签堆砌、质量咒语、权重语法、参数后缀、标题、解释或Markdown。"
)

_NIJI_SYSTEM = _COMMON + (
    "你在为 Niji 生成四段提示词。只输出 JSON 对象，字段必须是 subject、style、additions、suffix。"
    "把共同视觉骨架映射到四段：subject 承载稳定外貌与当前变化、气质、发型、妆容、表情、服装材质和姿态；"
    "style 承载画风、媒介、材质表现和审美；additions 承载镜头类型、非常规视角、景深、光源方向、"
    "色调氛围、背景、构图、人物位置和视觉引导；"
    "四段必须围绕同一个视觉命题：subject 给出第一视觉中心和关键关系，style 只保留统一色材母题，"
    "additions 用镜头与光影因果建立层级，禁止三段各写一套互不相干的审美。"
    "suffix 只放参数指令，可用 --ar、--chaos、--iw、--stylize、--seed、--no、--sref、--sw、--weird、--niji。"
    "subject、style、additions 必须全部使用英文。不得输出 JSON 之外的内容，不得把参数混入前三段。"
    # 同 _NATURAL_SYSTEM：Niji 档同样挂在防拦截预设之后，语言约束必须是覆盖式前置声明。
    "【最高优先级·语言】无论上文（若存在角色扮演设定或世界观规则）如何规定正文语言，"
    "你本次 JSON 的 subject、style、additions 三段都必须是纯英文，"
    "不得以任何理由改写成中文或其他语言；不要使用任何中文字符（含标点），suffix 除外。"
    # 【事实边界】同 _NATURAL_SYSTEM（2026-09-20）：L1 真机成稿把 59 字窗口写成
    # `bed / mattress / bedside lamp / dark bedroom`，而原文是教室。
    "【事实边界】四段里出现的人、物、服装、场景与配色只能来自输入画面事实；"
    "【多人分述】画面里有两名以上主体时，**必须逐人分述、各自点名**：每人写明**画面位置**（镜头左侧／镜头右侧／前景／后景）＋**该人自己的动作与可见身体部位**。**即使两人动作完全相同，也要分别写**（例：「镜头左侧的A翘起臀部、掰开臀瓣；镜头右侧的B同样翘起臀部、掰开臀瓣」），不得只写一次动作让读者以为是同一人；不得只给人物名而不给动作，也不得只给动作而不给位置。"
    "【具体化必须有出处】画面事实分两级，**具体化一律要有出处**：①**可直接写**——输入画面事实里出现过的可见项（人、动作、身体部位、服装、物件、地点、光线）；②**只能中性化**——只给了类别、没给细节的（有「墙」无材质、有「床」无床品、有「衣服」无款式）只写类别，**不得补具体材质、颜色、款式**；③**地点、道具、光源、场景类型只能来自输入**，不得新增或升级——「背靠墙壁」不是「窄巷」「走廊」，「坐着」不是「床边」，输入没写灯就不写台灯、蜡烛、床头灯；④人物外观（发色、瞳色、体型、服装款式）只能来自「外观」栏与输入画面事实，两边都没有的项**留白不写**。"
    "不得新增输入未提及的道具、服装、场景或配色，宁可少写也不编造。"
)

_SYSTEMS = {
    "anima_tags": _ANIMA_SYSTEM,
    "natural_language": _NATURAL_SYSTEM,
    "niji_sections": _NIJI_SYSTEM,
}

def _strip_wrapping(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _krea_prompt(raw: str, scene: Mapping[str, object] | None = None) -> str:
    _ = scene
    text = _strip_wrapping(raw).split("——自检——", 1)[0].strip()
    # 两段式（2026-08-30 用户定稿）：第一段内容/构图 tags、第二段内容文段。
    # 旧行为把所有行合并成单行——那正是格式被破坏的源头。
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) >= 2:
        return _normalize_krea_style(lines[0]) + "\n" + _normalize_krea_style(
            " ".join(lines[1:]))
    if lines:
        return _normalize_krea_style(lines[0])
    return ""


_STRAY_PAREN_RE = re.compile(r"\(([^()]{1,48}?)\)")

# 悬空事实片段（2026-09-20 第 21 刀）：`looking at` 这类**只带动词+介词、没有宾语**的片段，
# 补进散文会产出 `The two adult characters are depicted with looking at.` 这种病句。
# 只杀这一形状，**不按词数过滤**——`bedchamber`（1 词地点）是合法事实，必须补。
_DANGLING_FACT_RE = re.compile(
    r"^\s*\w+ing\s+(?:at|to|into|on|onto|toward|towards|for|from|with|over|under)\s*$",
    re.I,
)


def _is_dangling_fact(detail: str) -> bool:
    return bool(_DANGLING_FACT_RE.match(detail or ""))


def _unwrap_stray_parentheses(text: str) -> str:
    """拆掉**碎片式圆括号**（2026-09-20 用户实锤）。

    L1·niji 真机成稿：`a (torn) white shirt … exposing a strip of bare lower back (above her)
    (soaked-through) (thin) (panties) pulled taut` —— 渲染模型把补语/并列词塞进括号，
    读起来是**碎裂的短语**而不是画面描述。用户口径：「出现 () 包住单词的情况要修复」。

    只拆**短碎片**（≤48 字、无冒号）⇒ 不碰权重语法 `(word:1.2)` 这类带冒号的形式。
    """
    def _swap(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if not inner or ":" in inner:
            return match.group(0)
        return inner

    return _STRAY_PAREN_RE.sub(_swap, text or "")


def _normalize_krea_style(prompt: str) -> str:
    """清除会锁死真人材质的皮肤微观词，不代替 LoRA 决定画风。"""
    text = prompt
    replacements: tuple[tuple[str, str], ...] = (
        (r"(?:整体)?(?:必须)?(?:采用|呈现|为)?(?:精致)?(?:超写实)?"
         r"(?:真人摄影|真实照片|写实摄影|二次元插画|动漫插画)(?:风格|美学)?[，,]?", ""),
        (r"皮肤呈(?:现)?半透明(?:的)?质感", "肌肤受光自然"),
        (r"隐约透出(?:底层)?微血管", "以细腻明暗表现暖色反光"),
        (r"(?:脸上|面部|皮肤)?毛孔(?:清晰)?(?:可见)?", "面部明暗过渡柔和"),
    )
    replacements += (
        (r"\b(?:photorealistic\s+live-action|live-action\s+photorealistic|"
             r"realistic\s+photography|detailed\s+anime\s+illustration|anime\s+illustration|"
             r"manga\s+illustration|3D\s+render(?:ing)?)\s*(?:imagery|style|aesthetic)?[.,;:]?\s*", ""),
        (r"\bphotorealistic\b[.,;:]?\s*", ""),
        (r"\b(?:live-action photography|anime(?: style)?|manga(?: style)?|3D render(?:ing)?)\b[.,;:]?\s*", ""),
        (r"\brealistic human skin\b", "natural skin tones"),
        (r"\btranslucent skin\b", "natural tonal transitions across the skin"),
        (r"\b(?:microscopic|visible)\s+(?:facial\s+)?(?:veins?|pores?)\b", "subtle tonal detail"),
        (r"\b(?:faint|visible)\s+veins?\s+visible\b", "subtle tonal detail"),
        (r"\bfaint\s+vein\b", "subtle shading"),
        (r"\bskin\s+texture\b", "skin shading"),
        (r"\brealistic\s+(?:softness|skin|detail|rendering)\b", "clean shading"),
        (r"\bhigh\s+image\s+fidelity\b", "clean presentation"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    # 两段式：逐行清洗但保留换行（旧行为合并成单行会破坏第一段 tags/第二段文段结构）
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _escape_bare_control_chars(payload: str) -> str:
    """把 **JSON 字符串字面量内部**的裸控制字符转义（`_json_object` 的补救通道，第 31 刀）。

    只做这一件事：`"` 进出字符串（认 `\\` 转义），字符串内的裸 `\\n` / `\\r` / `\\t` 换成对应的
    转义序列；字符串之外原样透传（那里的换行与缩进本来就是合法 JSON 空白）。
    ⚠ 刻意**不做**其他容错（尾逗号、单引号、中文引号……）：那些形状一旦放手写容错，等于放宽
    结构约束，会把「模型没按协议写」这类真问题掩盖掉（与本项目 fail-closed 的口径相反）。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for char in payload:
        if in_string:
            if escaped:
                out.append(char)
                escaped = False
                continue
            if char == "\\":
                out.append(char)
                escaped = True
                continue
            if char == '"':
                out.append(char)
                in_string = False
                continue
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(char, char))
            continue
        if char == '"':
            in_string = True
        out.append(char)
    return "".join(out)


def _json_object(raw: str) -> dict[str, object]:
    text = _strip_wrapping(raw)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    payload = match.group(0)
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        # 2026-09-21 第 31 刀（anima 兜底波动归因的副产物，零 LLM 复现）：真机 `L2·单人锚点[63]`
        # 首轮＋repair 两轮的 raw 都是**合法形状的 JSON**，唯一毛病是 `content` 字符串里带了
        # **未转义的真实换行**（`…refuses to heed.\n"`）⇒ `json.loads` 直接抛 ⇒ `_json_object`
        # 返回 {} ⇒ 掉进「非 JSON」分支（首行变成 `{`）⇒ 两行结构判据全红 ⇒ 白烧一次 repair
        # 再掉兜底。它不是采样波动，是**解析接缝**。
        # 定案：只对**字符串字面量内部的裸控制字符**做一次转义重试，不放宽任何结构约束
        # ⇒ 合法 JSON 走原路径逐字节不变，其他残破形状照样返回 {}（fail-closed 不变）。
        try:
            value = json.loads(_escape_bare_control_chars(payload))
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


_ANIMA_PROSE_LABELS: tuple[tuple[str, str], ...] = (
    (r"her body|body", "Her body is shown with "),
    (r"bound|restraints?", "She is bound with "),
    (r"position|pose", "She is positioned "),
    (r"appearance", "She appears with "),
    (r"clothing|wardrobe|costume", "She wears "),
    (r"action", "She is shown "),
    (r"expression", "Her expression shows "),
    (r"setting|location|background", "The scene takes place in "),
    (r"camera|composition", "The composition uses "),
    (r"lighting", "The lighting uses "),
)

# 画幅声明：`3:4` 与 `3:4 aspect ratio` 都归一成恰好一份 `N by M aspect ratio`。
_ASPECT_RATIO_RE = re.compile(
    r"\b(\d+)\s*:\s*(\d+)\b(\s*[Aa]spect\s+[Rr]atio)?",
)

# 只服务视觉命题的通用艺术词：是**独立 tag** 时才剔除（见 `_drop_generic_anima_tags`）。
_ANIMA_GENERIC_ART_TAGS = frozenset({
    "dramatic scene", "climactic moment", "dynamic composition", "action pose",
    "cinematic lighting",
})


def _normalize_aspect_ratio(text: str) -> str:
    """`3:4` / `3:4 aspect ratio` → `3 by 4 aspect ratio`，**恰好一份** `aspect ratio`。

    2026-09-20 真机实锤（L2/L3·anima 成稿）：原实现 `\\b(\\d+)\\s*:\\s*(\\d+)\\b` →
    `\\1 by \\2 aspect ratio` 不认模型**已经写过** `aspect ratio` 的情况 ⇒
    `3:4 aspect ratio` 被追加成 `3 by 4 aspect ratio aspect ratio`（画幅声明重复一份）。
    改为**连后缀一起吞掉再补一份**，两种输入都得同一结果。
    """
    return _ASPECT_RATIO_RE.sub(
        lambda m: f"{m.group(1)} by {m.group(2)}{m.group(3) or ' aspect ratio'}",
        text,
    )


def _anima_naturalize_prose(value: str) -> str:
    """把模型偶发的 `Label: fragment` 改成连续英文画面句。"""
    text = " ".join((value or "").splitlines()).strip()
    for label, replacement in _ANIMA_PROSE_LABELS:
        text = re.sub(
            rf"(^|[.!?]\s+)(?:{label})\s*:\s*",
            rf"\1{replacement}",
            text,
            flags=re.I,
        )
    text = _normalize_aspect_ratio(text)
    text = re.sub(r"\s*:\s*", ", ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _anima_clean_tag_head(value: str) -> str:
    """Anima 首行只能是逗号序列，不能携带标题式冒号。"""
    text = re.sub(r"\s*;\s*", ", ", value or "")
    labels = "|".join(label for label, _replacement in _ANIMA_PROSE_LABELS)
    text = re.sub(rf"\b(?:{labels})\s*:\s*", "", text, flags=re.I)
    text = _normalize_aspect_ratio(text)
    return re.sub(r"\s*:\s*", ", ", text)


def _drop_generic_anima_tags(tag_head: str) -> str:
    """按 **tag 粒度**剔除通用艺术词，保留原有逗号分隔。

    2026-09-20 真机实锤（L2/L3·anima 成稿）：原实现用一条正则
    `(?:^|,\\s*)(...)(?=,|\\.|$)\\s*,?\\s*` 就地删除，**前后两个分隔符一起被吃掉** ⇒
    `volumetric steam, cinematic lighting, depth of field` 粘成 `volumetric steamdepth of field`。
    改成先 split 再 join，分隔符由 join 重建，结构上不可能粘连；判定与旧正则等价
    （整 tag 命中才删，`dynamic action pose` 这类只含子串的 tag 不动）。
    """
    kept = [
        tag.strip()
        for tag in (tag_head or "").split(",")
        if tag.strip() and not re.fullmatch(r"[*_#`~\s]+", tag.strip())
        and tag.strip().rstrip(".").strip().lower() not in _ANIMA_GENERIC_ART_TAGS
    ]
    return ", ".join(kept)


# ── 首行 tag 归一（2026-09-21 第 31 刀，用户实锤：「重复词条会导致某个 tag 的权重过高」）─────
# 现场（L3·NSFW多人 · anima_tags 真机首行）：`shy … shy`、`wet pussy … wet pussy`，
# 且 `masterpiece, best quality, high quality, absurdres` 与**程序前置的质量行整段重复**。
# 根因：`_ANIMA_SYSTEM` 虽写明「质量行由程序添加」，但拼装处（`_normalize` 的 anima 分支）把
# 质量行直接前置，全链**没有任何同名去重**；`_drop_generic_anima_tags` 只按黑名单剔通用艺术词。
# 判据：**tag 粒度全等**（大小写与内部空白归一后完全相同）才折叠，保留**首次**出现的拼写与
# 位置 —— 不改任何非重复 tag，**不做同义合并**（同义合并＝改画面事实，越权）。
def _anima_dedupe_tags(tag_line: str) -> str:
    kept: list[str] = []
    seen: set[str] = set()
    for tag in (tag_line or "").split(","):
        cleaned = tag.strip()
        if not cleaned:
            continue
        key = " ".join(cleaned.split()).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(cleaned)
    return ", ".join(kept)


# 人数词（Danbooru 计数族）：同一帧只允许一个「画面主体数」口径。
# 第 31 刀真机实锤：L3·anima 首行 `1girl, 2girls` 并存 —— `1girl` 是 krea2/solo 时代的习惯
# 残留，与多人帧自相矛盾（没有闸禁止低位计数词）。用户定案：**保留多人计数词**，
# 剔除冲突的低位词与 `solo`。
# ⚠ 单人帧（各族最高位都 <2，或压根没有计数词）**逐字返回原串** ⇒ 零回归。
#
# 第 46 刀（2026-09-22 用户口径）：**族内归一从「只认 girls」改成认两族**。
# 用户原话：「第三人称时按剧情的画面人物判断，男主主人公+2女主角就是 `1boy, 2girls`……
# 女主人公+2男主角 `1girl, 2boys`，也就是说**根据剧情判断在场的内容，不是锁死女角色**」。
# 旧口径只认 girls（`^(\d+)\s*girls?$`）⇒ 纯男性帧 `2boys, solo` 整段跳过归一
# ⇒ 首行同时出现 `2boys` 与 `solo`（自相矛盾；零 LLM 走查实锤）。
# ⚠ 跨族**不相消**：`1girl, 2boys` 是合法阵容（2 人帧＝1 女 2 男不是 3 人帧），两族各自留最高位。


def _resolve_subject_counts(tag_line: str) -> str:
    """族内归一：**逐族**只留最高位计数词；有任一族最高位 ≥2 时统一剔除 `solo`（第 46 刀）。"""
    tags = [tag.strip() for tag in (tag_line or "").split(",")]
    family_max: dict[str, int] = {}
    for tag in tags:
        match = _SUBJECT_FAMILY_RE.match(tag)
        if not match:
            continue
        family = "boys" if match.group(2).lower().startswith("boy") else "girls"
        family_max[family] = max(family_max.get(family, 0), int(match.group(1)))
    if not family_max or max(family_max.values()) < 2:
        return _anima_dedupe_tags(tag_line)
    kept: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            continue
        match = _SUBJECT_FAMILY_RE.match(cleaned)
        if match:
            family = "boys" if match.group(2).lower().startswith("boy") else "girls"
            if int(match.group(1)) != family_max[family]:
                continue
        if cleaned.lower() == "solo":
            continue
        kept.append(cleaned)
    return _anima_dedupe_tags(", ".join(kept))


# 跨族人数词（第 40 刀 · 2026-09-21 用户实报）：girls 族**已把画面主体名额占满**时，
# 多出来的 boys 族计数词是**孤儿**——没有任何画面主体可以对应它。
#
# 真机现场（`backend/data/logs/agent-trace.jsonl` · `illustration.submitted` ·
# 2026-09-21T21:48:20 · slot 43）：`illustration.request` 明写 `actors: ["舞姬恋", "凌若冰"]`、
# `actor_count: 2`（**叙事者根本不在 `actors` 里**），契约只要求 `2girls`；但交付首行是
# `…, anime coloring, 2girls, 1boy, male viewer pov, first-person view, no viewer face, …`
# —— `1boy` 与 `male viewer pov` 同现 ⇒ 模型把**不入画的叙事者**（男性）写成了第 3 个人。
# 用户口径：「**除非是第三人称，主角相关的 tag 才可能进入提示词中**」。
#
# ⚠ **不是禁词表**（技能 `demiurge-viewpoint-subject-scope` §2 红线：第一人称下画面里
#   完全可能真有别的男性，禁词闸会误杀）：
#   · `1girl, 1boy`（girls 族只占 1 个名额、主体数 2）⇒ **原样保留**；
#   · 纯男性帧 `2boys`（girls 族 0）⇒ **不动**；
#   · `2boys, 1girl`（主体数 2）⇒ girls 族未占满 ⇒ **不动**。
# ⚠ 40 刀当时**只能剔 boys**（整条计数设施按 girls 建），故写「girls 族永远不动」。
# ⚠ **第 46 刀（2026-09-22）对称化**：用户口径原话「**根据剧情判断在场的内容，不是锁死
#   女角色**」——女主人公＋2 男主角就是 `1girl, 2boys`，纯男帧写 `1girl` 同样是孤儿。
#   有了**逐主体性别**（`scene["subject_genders"]`）后，判据改成「**某族真值为 0 ⇒ 整族剔**」；
#   拿不到性别（老调用方 / 解析不全）⇒ **退回 40 刀口径**（只剔 boys），老测试逐格不变。
_SUBJECT_FAMILY_RE = re.compile(r"^(\d+)\s*(girls?|boys?)$", re.I)


def _is_boy_count(tag: str) -> bool:
    match = _SUBJECT_FAMILY_RE.match(tag.strip())
    return bool(match and match.group(2).lower().startswith("boy"))


def _drop_orphan_subject_counts(
    tag_line: str, subject_count: int, genders: list[str] | None = None,
) -> str:
    """跨族人数词孤儿闸：**没有对应画面主体的计数词族整族剔除**（第 40 刀／第 46 刀）。

    · 有逐主体性别（`genders` 与画面主体名单**同长同序**、且全部可解析）⇒ **对称**：
      某族真值为 0 而行内写了该族计数词 ⇒ 剔该族（两女帧 `2girls, 1boy` ⇒ 剔 `1boy`；
      纯男帧 `2boys, 1girl` ⇒ 剔 `1girl`）。
    · 无性别（老调用方 / 解析不全）⇒ **退回第 40 刀口径**：`girls 族计数 ≥ 画面主体数`
      且行内存在 boys 族 ⇒ 剔 boys 族（girls 族不动）—— 该支逐字节等价旧实现。

    ⚠ 零回归锁：`subject_count < 1` ⇒ **逐字节返回原串**；不触发 ⇒ 同样逐字节返回原串。
    ⚠ 只在 `_resolve_subject_counts` **之后**调用（那时整行已归一成 `", "` 连接的非空 tag 串，
    重连接才是字节安全的）。
    """
    if subject_count < 1:
        return tag_line
    tags = [tag.strip() for tag in (tag_line or "").split(",")]
    resolved = (
        genders if genders is not None and len(genders) == subject_count
        and all(str(gender).strip() for gender in genders) else None
    )
    if resolved is not None:
        girls_true = sum(1 for gender in resolved if "女" in str(gender))
        boys_true = sum(1 for gender in resolved if "男" in str(gender))
        drop_girls, drop_boys = girls_true == 0, boys_true == 0
    else:
        girls = 0
        for tag in tags:
            match = _SUBJECT_FAMILY_RE.match(tag)
            if match and not match.group(2).lower().startswith("boy"):
                girls += int(match.group(1))
        if girls < subject_count or not any(_is_boy_count(tag) for tag in tags):
            return tag_line
        drop_girls, drop_boys = False, True
    if not drop_girls and not drop_boys:
        return tag_line
    kept: list[str] = []
    for tag in tags:
        if not tag:
            continue
        match = _SUBJECT_FAMILY_RE.match(tag)
        if match:
            is_boy = match.group(2).lower().startswith("boy")
            if (is_boy and drop_boys) or (not is_boy and drop_girls):
                continue
        kept.append(tag)
    if len(kept) == len([tag for tag in tags if tag]):
        return tag_line
    return ", ".join(kept)


def _contract_subject_genders(scene: Mapping[str, object]) -> list[str]:
    """画面主体的**逐主体性别**，与 `_contract_subject_names(scene)` **同长同序**（第 46 刀）。

    数据源＝`scene["subject_genders"]`（`{角色名: "女"/"男"}`；由接线侧按**世界书条目正文优先、
    通用表「性别」列兜底**的零 LLM 解析后随 `scene_spec` 下传）。
    ⚠ 取不到的名 ⇒ `""`（**宁缺勿猜**：调用方据此退回中性措辞，不写具体性别族）。
    ⚠ 缺键 / 类型不符 ⇒ 全 `""`（老调用方零回归）。
    """
    names = _contract_subject_names(scene)
    table = scene.get("subject_genders")
    if not names or not isinstance(table, Mapping):
        return ["" for _ in names]
    return [str(table.get(name) or "").strip() for name in names]


def _subject_count_words(genders: list[str]) -> str:
    """按画面主体的**实际性别组成**给出人数词（第 46 刀，纯函数、零 LLM）。

    全可解析 ⇒ `2girls` / `2girls, 1boy` / `1girl, 2boys`（**girls 族在前**＝Danbooru 习惯，
    也是真机既有形态 `2girls, 1boy` 的顺序；计数为 0 的族不写）。
    任一体性别解析不出 ⇒ `""`（调用方**退回中性措辞**，不写具体性别族——用户定案「不写」）。
    """
    if not genders:
        return ""
    girls = 0
    boys = 0
    for gender in genders:
        text = str(gender).strip()
        if "女" in text:
            girls += 1
        elif "男" in text:
            boys += 1
        else:
            return ""
    parts: list[str] = []
    if girls:
        parts.append(f"{girls}girl" + ("" if girls == 1 else "s"))
    if boys:
        parts.append(f"{boys}boy" + ("" if boys == 1 else "s"))
    return ", ".join(parts)


# 第 41 刀（2026-09-21 用户口径）：多人契约的人数词**不再硬编码 `2girls`**。
# 用户口径：「第三人称就按剧情正常来就行，无需像第一人称一样删去剧情里出现的主人公的 tag」；
# 「主人公是男的 ⇒ `2girls, 1boy`；主人公是女的 ⇒ `3girls`」。
# 旧判据 `\b(?:2girls|two girls)\b` 把「画面主体**恒为 2** 且全女」写死 ⇒ 三人帧里模型
# **写对的 `3girls` 反被判红**（零 LLM 复现 `_tmp/i41_probe.py` A2／C2 格）⇒ 逼出修复轮，
# 极端情况掉通用兜底模板、把模型真实画面全丢。
# 新判据＝只给 **girls 族**一个上/下界：`2 ≤ girls ≤ 画面主体数`。
# ⚠ **boys 族刻意不进契约**（40 刀真机格 `2girls, 1boy` 于 2 主体帧，旧判据放行；
#   若把 boys 计入总数会判红 ⇒ **给已知真机形态新增判红面**）。
#   boys 族的归属是**归一化侧**的「剔孤儿」（40 刀），契约只负责判、不重复治。
# ⚠ 本刀**不需要**逐主体性别：只约束 girls 族区间，`2girls, 1boy` 与 `3girls` 都合法；
#   性别由模型按注入的 `【性别】X` 画面事实自己写（23 刀定案：不机械拼硬标签）。
# ⚠ 必须与 `_resolve_subject_counts` **同口径**（同族并存只认最高位）：否则
#   `1girl, 2girls` 会被数成 3 ⇒ 给 31 刀的真机残留**新增判红面**。
_COUNT_FAMILY_RE = re.compile(r"(\d+)\s*(girls?|boys?)\b", re.I)


def _subject_count_totals(prompt: str) -> tuple[int, int]:
    """数出提示词里 girls / boys 族的**有效**计数（第 41 刀，纯函数）。

    口径与 `_resolve_subject_counts` 一致：**同族并存只认最高位**
    （`1girl, 2girls` 的有效值是 2，不是 3 —— 那是 31 刀实锤的习惯残留）。
    兼容旧判据的 `two girls` 词形；`two-shot` 这类不带计数的 `shot` 词不算。
    """
    girls = 0
    boys = 0
    text = prompt or ""
    for match in _COUNT_FAMILY_RE.finditer(text):
        value = int(match.group(1))
        if match.group(2).lower().startswith("boy"):
            boys = max(boys, value)
        else:
            girls = max(girls, value)
    if re.search(r"\btwo girls\b", text, re.I):
        girls = max(girls, 2)
    return girls, boys


def anima_quality_tags(rating: str = "sfw") -> str:
    if rating == "nsfw":
        return ANIMA_QUALITY_TAGS.replace(
            "score_9,", "score_9, sensitive, explicit,", 1,
        )
    return ANIMA_QUALITY_TAGS


def negative_prompt(profile: str, _scene: Mapping[str, object] | None = None) -> str:
    return ANIMA_NEGATIVE_TAGS if profile == "anima_tags" else ""


def profile_defaults(profile: str, rating: str = "nsfw") -> dict[str, str]:
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    return {
        "quality_prompt": anima_quality_tags(rating) if profile == "anima_tags" else "",
        "negative_prompt": negative_prompt(profile),
    }


def system_with_preset(
    system: str,
    scene: Mapping[str, object],
    *,
    preset_dir: str = "",
    preset_name: str = "",
    user_name: str = "",
    task_label: str = "内部生图提示词任务",
    head_only: bool = True,
) -> str:
    """把当前偏置/防拦截预设用于独立提示词调用，不携带剧情历史。

    head_only=False：携带完整预设（含 <Order> 剧情机制段）——token 更贵，但防拦截
    强度与剧情主生成同级（2026-09-01 用户要求：生图提示词要像剧情一样稳定）。

    ⚠ 拼接边界（2026-09-19 优化项②取证）：预设段**整段前置**于任务框定与 Profile system，
    而预设正文自带「正文简体中文」类语言要求 ⇒ 对「输出语言」这个话题，上游指令在篇幅与
    位置上双压模板。**语言约束的压制放在各 Profile 的 system 文本里**（覆盖式声明，
    见 _NATURAL_SYSTEM / _NIJI_SYSTEM），本函数**不**在这里再插一层——同一条约束两处写
    就是两处漂移，且模板段离生成位置更近（近因）。此处只负责拼接与顺序保持。
    """
    if not (preset_dir.strip() and preset_name.strip()):
        return system
    try:
        from app.services import preset_store

        preset = preset_store.read_preset(preset_dir, preset_name)
        if not preset:
            return system
        model_scene = _scene_for_model(scene)
        actors = model_scene.get("actors")
        names = [str(name).strip() for name in actors] if isinstance(actors, list) else []
        source = json.dumps(model_scene, ensure_ascii=False, separators=(",", ":"))
        markers = {
            "char_name": "、".join(name for name in names if name),
            "char_description": str(model_scene.get("appearance") or ""),
            "char_personality": "",
            "scenario": str(model_scene.get("narrative") or ""),
            "dialogue_examples": "",
            "worldbook": str(model_scene.get("appearance") or ""),
            "worldbook_after": "",
            "persona": "",
            "user_name": user_name.strip(),
            "last_user_message": source,
            "last_char_message": "",
        }
        # head_only=False 时携带完整预设（含 <Order> 剧情机制段），防拦截强度与
        # 剧情主生成同级——2026-09-01 用户定案：生图提示词的稳定性优先于 ~8k token。
        guard = preset_store.assemble_system(preset, markers, head_only=head_only).strip()
        if not guard:
            return system
        return (
            guard
            + f"\n\n【{task_label}】以下任务独立于剧情正文，只输出目标协议要求的提示词，"
              "不得续写剧情、解释、道歉或附加拒答说明。\n"
            + system
        )
    except Exception:  # noqa: BLE001 预设读取失败时仍允许按 Profile 自身协议生成
        return system


_ANIMA_SCHEMA_FIELDS = ("visual_hook", "primary_focus", "supporting_elements", "content")


def _extra_field_prose(value: Mapping[str, object]) -> str:
    """`content` 缺画面句时，从对象里的**非规格**字符串字段取回（唯一调用点：`_normalize` JSON 分支）。

    真机 2026-09-19 实锤（`probe_prompt_delivery.py`）：模型把 tags 写进规格字段 `content`，
    却把画面句写进**非规格字段** `content_note`（`content` 里既无换行也无句点）⇒
    `partition(".")` 的 `prose` 为空 ⇒ 输出退化 1 行 ⇒ 三连判死（白烧一次 repair 调用）。
    口径：**只看非规格字段**；候选必须同时满足「含逗号」+「≥100 字符」+「含完整英文句」
    （与 `_errors` 的 `^[A-Za-z]…[.?!]` 同源）——真实画面句远超该阈值，而
    「Here is the JSON you requested.」这类附带说明既无逗号也过短 ⇒ 不入选，避免把说明当画面句；
    多个候选取最长。拒答文本即使被选入，也会被 `_errors` 的 `_REFUSAL_RE` 兜住。
    ⚠ 只在 `prose` 本已为空时被调用 —— 那个状态**必然**产出死稿，故单向放宽、零回归。
    """
    candidates: list[str] = []
    for key, raw_value in value.items():
        if key in _ANIMA_SCHEMA_FIELDS or not isinstance(raw_value, str):
            continue
        text = " ".join(raw_value.split()).strip()
        if len(text) < 100 or "," not in text:
            continue
        if re.search(r"^[A-Za-z][^.?!]{24,}[.?!](?:\s|$)", text):
            candidates.append(text)
    return max(candidates, key=len) if candidates else ""


_ANIMA_FIELD_LABEL_RE = re.compile(
    r"^\s*(visual_hook|primary_focus|supporting_elements|content)\s*[:：]\s*(.*)$",
    re.I,
)


def _labelled_fields_object(raw: str) -> dict[str, object]:
    """**字段式回复** → 等价对象（模型用 `字段: 值` 写全四个规格字段，只是没加花括号）。

    真机实锤（2026-09-21 第 33 刀 · `D:\\pytest-tmp\\i33\\tp` 的 `L3·NSFW多人` repair 轮）：
    模型写的是
        visual_hook: …\\n\\nprimary_focus: …\\n\\nsupporting_elements: [...]\\n\\ncontent: "…"
    ——四个字段**全在、值全非空**，只是外层没有 `{}`。这种文本 `_json_object` 必然返回 `{}`
    ⇒ 掉进「非 JSON」分支 ⇒ 那一支把 `", ".join((lines[0], body))` 当 tags ⇒ **整段原文进首行**
    （该格首行 2702 字），而结构判据只看「两行 + 首行 ≥6 个 tag」⇒ **一条格式红都不报** ⇒
    `_patch_failed_prompt` 照旧放行 ⇒ **交付 4833 字的半 JSON 残留**。

    ⚠ **准入刻意很窄**（不是 `_json_object` 的放宽容错，而是另一种**合法序列化形态**的识别）：
    ① 必须命中全部四个规格字段；② 每个值必须非空；③ 只认行首的 `字段:` 标签（续行并入前一字段）。
    任一条不满足 ⇒ 返回 `{}` ⇒ 与旧行为**逐字节相同**（fail-closed 不变）。
    """
    fields: dict[str, str] = {}
    current = ""
    for line in _strip_wrapping(raw).splitlines():
        match = _ANIMA_FIELD_LABEL_RE.match(line)
        if match:
            current = match.group(1).lower()
            fields[current] = match.group(2).strip()
            continue
        if current and line.strip():
            fields[current] = f"{fields[current]} {line.strip()}".strip()
    if not all(name in fields and fields[name] for name in _ANIMA_SCHEMA_FIELDS):
        return {}
    cleaned: dict[str, object] = {
        name: _strip_field_quotes(value) for name, value in fields.items()
    }
    # `supporting_elements` 在 `{}` 形态里是 **JSON 数组**，字段式回复只是把同一段文本照抄过来
    # （带外层中括号）⇒ 这里解回 list，输出才能与 `_json_object` **结构等价**：
    # `_anima_contract_errors` 要用 `isinstance(supporting, list)` 判它（否则白白多出一条契约红）。
    supporting = str(cleaned.get("supporting_elements") or "")
    if supporting.startswith("["):
        try:
            parsed = json.loads(supporting)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(parsed, list):
            return {}
        cleaned["supporting_elements"] = parsed
    return cleaned


def _strip_field_quotes(value: str) -> str:
    """剥掉字段值外层的成对引号。

    真机实锤（2026-09-21 第 33 刀 · `D:\\pytest-tmp\\i33\\tp` 的 `L3·NSFW多人` repair 轮）：字段式
    回复里 `content: "…"` 是**照抄 JSON 写法**的，引号留在值上。带着尾引号进散文行会让
    「第二行须是英文自然语言画面描述」的判据失配（`…completes."` 后面的 `.` 不落在句末）⇒ 白红。
    ⚠ 只剥**成对**的外层引号，值内部的引号一律不动。
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def _niji_sections_join(sections: list[str]) -> str:
    """niji 四段拼回：**内容段末尾统一以 `", "` 收尾**（2026-09-21 第 33 刀用户口径）。

    用户原话（复核第 32 刀后真机跑批）：「niji_sections 合格，但是**段与段的末尾建议加上 `", "`**，
    保持良好的习惯」。理由＝段是逗号列表粒度，任何后续拼接／截取都不该缺分隔符。
    ⚠ **参数段（以 `--` 开头的那一段，如 `--ar 4:3 --niji 6`）不加** —— 它是 niji 参数串，
    不是关键词列表；加了会把参数语法写坏。空段保持空（`_errors` 会照旧判「四段」）。
    """
    out: list[str] = []
    for section in sections:
        if not section:
            out.append("")
            continue
        if section.lstrip().startswith("--"):
            out.append(section)
            continue
        out.append(section.rstrip().rstrip(",").rstrip() + ", ")
    return "\n".join(out)


def _looks_like_tag_run(text: str) -> bool:
    """像不像一串**逗号分隔的 tag**（第 33 刀，`content` 内部切分用）。

    判据刻意松而稳：至少 4 个逗号段、且**不含句末标点**（有句点就不是 tag 串，是句子）。
    """
    body = text.strip()
    return "," in body and not re.search(r"[.!?]", body) and len([
        token for token in body.split(",") if token.strip()
    ]) >= 4


def _looks_like_prose_sentence(text: str) -> bool:
    """像不像**一句完整英文画面句**（第 33 刀，`content` 内部切分用）。

    与「第二行须是英文自然语言画面描述」（`^[A-Za-z][^.?!]{24,}[.?!]`）同口径：大写字母开头、
    24 字以上、以句末标点收尾，且**句内**不再出现句末标点（多句就不该走这条切分）。
    """
    body = text.strip()
    return bool(re.fullmatch(r"[A-Z][^.?!]{24,}[.!?]", body))


def _looks_like_scene_description(text: str) -> bool:
    """这段 `content` 像**画面描述**还是像一串**裸 tag**？（第 33 刀 · 只服务「要不要拿它当 prose」）

    ⚠ 这条判据是为**保住既有红线**而存在的，不是新放宽：`test_animaJSONcontent只有tags且无额外
    句子字段时仍判死` 锁死「裸 tag 串不得被拿来冒充第二行」。判据＝**至少有一个逗号段长到足以成句**
    （≥ 6 个词）——真机对照：
      · `L1·NSFW单人` 的 content：`the white fluid running down her inner thigh`（8 词）、
        `her head tilted back with her mouth open in exhaustion`（10 词）⇒ 是**描述**（短语里带
        谓语/分词），可以当第二行；
      · 裸 tag 串 `1girl, long black hair, amber eyes, white shirt, black pencil skirt,
        legs together, medium shot, dramatic side light, notice board background`（最长 3 词）
        ⇒ 不是描述，照旧判死。
    """
    return any(len(segment.split()) >= 6 for segment in text.split(","))


def _split_comma_phrase(content: str) -> tuple[str, str]:
    """把「整段纯逗号串」按**段长**切成 (tag 行, 散文行) —— 纯机械切分，**不新增任何字**。

    第 72 刀（2026-09-29）：本函数服务的场景原本是 **`tag_head` 与 `prose` 同串**
    （见 `_looks_like_scene_description` 那条分支），而交付时那是**两行**
    ⇒ 整段逐字出现两遍（真机 [38] 第 1 跑 2340 字里约 1100 字重复；
       零 LLM 复现：同一串出现 2 次、首行以逗号收尾、次行以句号收尾）。

    切分门槛与 `_looks_like_scene_description` **共用同一个 6 词判据**，不另立阈值
    （两处口径必须恒等，否则会出现「判定它是描述、却切不出描述段」的空转）。
    切不动（tag 段不足 6 条 / 没有成句段）⇒ 返回 `(原串, "")`，调用方**保持旧行为**。
    """
    segments = [seg.strip() for seg in content.split(",") if seg.strip()]
    long_parts = [seg for seg in segments if len(seg.split()) >= 6]
    tags = [seg for seg in segments if seg not in long_parts]
    if not long_parts or len(tags) < 6:
        return content.strip(), ""
    return ", ".join(tags), ", ".join(long_parts)


def _normalize(
    profile: str, raw: str, scene: Mapping[str, object] | None = None,
) -> str:
    if profile == "krea2":
        # 第 64 刀：krea2 与 anima **同构**（首行 tags ＋ 散文）⇒ 首行也走第一人称收口
        # （剔旁观机位词 ＋ 补 POV 声明）；散文行逐字节不动。
        return _anonymize_prompt_names(
            _first_person_krea_guards(_krea_prompt(raw, scene), scene or {}), scene or {})
    if profile == "anima_tags":
        # 第 33 刀（2026-09-21）：`{}` 形式的 JSON 之外，再认一种**合法序列化变体** ——
        # 模型用 `visual_hook: … / primary_focus: … / supporting_elements: … / content: …`
        # 把四个规格字段写全（只是外层没有花括号）。不认它的话，那种 raw 会掉进下面的
        # 「非 JSON」分支、被整段吞进 tags 行且**一条格式红都不报** ⇒ `patched` 直接把
        # 半 JSON 残留交付出去。准入很窄（四字段全在且值非空），见 `_labelled_fields_object`。
        value = _json_object(raw) or _labelled_fields_object(raw)
        explicit_tags = ""
        explicit_prose = ""
        if value:
            source = str(value.get("content") or "")
        else:
            lines = [line.strip() for line in _strip_wrapping(raw).splitlines() if line.strip()]
            if len(lines) >= 2:
                body = " ".join(lines[1:])
                if re.search(
                    r"\b(?:masterpiece|best quality|score_[1-9]|anime coloring)\b",
                    lines[0], re.I,
                ):
                    # 已编译结果再次经过 normalize_inline 时，只剥掉质量前缀，保留同一行内容 tags。
                    explicit_tags = re.sub(
                        r"^[\s\S]*?\banime coloring\b\s*,?", "", lines[0],
                        count=1, flags=re.I,
                    ).strip(" ,")
                    if not explicit_tags:
                        legacy_tags, dot, legacy_prose = body.partition(".")
                        explicit_tags = legacy_tags.strip(" ,")
                        if dot and legacy_prose.strip():
                            body = legacy_prose.strip()
                else:
                    # 主模型偶尔把环境 tags 放首行、人物和动作写成第二行分号描述；
                    # 确定性复制具体描述到 tags 行，不能把 Agent 已提取的画面事实丢掉。
                    explicit_tags = ", ".join((lines[0].rstrip(" ,"), body))
                    explicit_tags = re.sub(
                        r"\b(?:(?:primary|second) adult character|the visual center)\s*:\s*",
                        "", explicit_tags, flags=re.I,
                    )
                    explicit_tags = re.sub(r"\s*[.;]\s*", ", ", explicit_tags)
                    explicit_tags = explicit_tags.encode("ascii", "ignore").decode("ascii")
                explicit_prose = re.sub(
                    r"\bprimary adult character\s*:\s*",
                    "The primary adult character is ", body, flags=re.I,
                )
                explicit_prose = re.sub(
                    r"\bsecond adult character\s*:\s*",
                    "The second adult character is ", explicit_prose, flags=re.I,
                )
                explicit_prose = re.sub(r"\s*;\s*", ". ", explicit_prose)
                explicit_prose = explicit_prose.encode("ascii", "ignore").decode("ascii").strip()
                if explicit_prose and explicit_prose[-1] not in ".!?”":
                    explicit_prose += "."
                source = ""
            else:
                source = " ".join(lines)
        content_lines = [
            line.strip() for line in _strip_wrapping(source).splitlines() if line.strip()
        ]
        content = " ".join(content_lines).strip()
        # 第一行是质量+内容 tags；第二行才是解释文段。
        if explicit_tags or explicit_prose:
            tag_head = explicit_tags
            prose = explicit_prose
        elif len(content_lines) >= 2:
            # 2026-09-19 修（真源：`docs/illustration-prompt-delivery-2026-09-19.md` §2.6）：
            # JSON 分支原先把 content 的换行**先压平**再 `partition(".")`。模型若用换行（而非句点）
            # 分隔 tags 与 prose（真机 L1/L3 实锤），整段会被塞进 tag_head、prose 落空 ⇒ 输出退化
            # 1 行 ⇒「必须正好两行」连带「第一行须含质量词与≥6 tags」「第二行须含英文画面描述」
            # 三连判死（模型两次都写了完整内容，第一稿是被本地归一化判死的）。
            # 改为与上方非 JSON 质量前缀分支同口径：首行 tags、其余并入 prose。
            # 单行 content 仍走下方首个句点切分（设计口径，行为逐字不变）。
            tag_head = content_lines[0]
            prose = " ".join(content_lines[1:])
        else:
            tag_head, dot, prose_tail = content.partition(".")
            prose = prose_tail.strip() if dot else ""
        if value and not prose.strip():
            # 2026-09-19 真机实锤（`%TEMP%\demiurge_pytest\after_normfix\_prompt_delivery_raw.json`）：
            # 模型把 tags 写进 `content`、把画面句写进**非规格字段** `content_note`
            # ⇒ 上面三条切分路径都拿不到 prose（`content` 内既无换行也无句点）⇒ 输出退化 1 行。
            # ⚠ 这条只在 prose 本已为空时触发（那个状态必然死稿）⇒ 单向放宽、零回归。
            prose = _extra_field_prose(value)
        if value and not prose.strip() and content.strip():
            # 第 33 刀（2026-09-21 真机 `D:\pytest-tmp\i33` 实锤，命中 2 格）：模型把 `content`
            # 写成**没有内部句点的形态**，上面三条切分路径全拿不到 prose ⇒ 输出退化成 **1 行**
            # ⇒「必须正好两行」三连判死 ⇒ 掉通用词表模板，把模型 1715／1826 字的真实画面全数丢掉。
            # 两种形态分别处理（都只在 `_errors` 判死前进来，**单向放宽**）：
            #   ① `tags ; 英文画面句`——真机 `L3·单人锚点[386]` 实锤：`;` 之前是逗号 tag 串、
            #      之后是完整英文句。按 `;` 切开，两行各得其所（tag 行 ＋ 散文行）。
            #   ② 纯逗号短语——真机 `L1·NSFW单人` 实锤：整段就是画面短语。此时它**本身就是画面
            #      描述**（非 JSON 分支对同形态也是「同一段文字既进 tags 又进 prose」，见
            #      `explicit_tags = ", ".join((lines[0].strip(" ,"), body))`）⇒ 直接当 prose。
            # 两条都**不新增任何模板引导句**（红线 28 ②），只在末尾补一个句点满足「英文自然语言
            # 画面描述」的形状判据。
            tag_part, sep, prose_part = content.partition(";")
            if sep and _looks_like_tag_run(tag_part) and _looks_like_prose_sentence(prose_part):
                tag_head = tag_part.strip(" ,")
                prose = prose_part.strip()
            elif _looks_like_scene_description(content):
                # 第 72 刀：旧行为把 `tag_head` 与 `prose` 设成**同一个串** ⇒ 交付两遍。
                # 先试机械切分（tag 形状的短段留首行、成句段给次行）；切不动才退回旧法。
                split_tags, split_prose = _split_comma_phrase(content)
                if split_prose:
                    tag_head, prose = split_tags, split_prose
                else:
                    prose = content.strip()
            if prose and prose[-1] not in ".!?":
                prose += "."
        tag_head = _anima_clean_tag_head(tag_head)
        tag_head = _drop_generic_anima_tags(tag_head)
        prose = _unwrap_stray_parentheses(_anima_naturalize_prose(prose))
        # 第 68 刀：第一人称下剔掉散文行的元语言镜头句
        # （`the camera is the narrator's eyes` 这类；零命中即逐字节返回）。
        prose = _first_person_prose_meta_guard(prose, scene or {})
        rating = str((scene or {}).get("rating") or "sfw")
        # 第 31 刀①②：**先拼质量行、再整行归一**——去重必须横跨「程序前置的质量行」与
        # 「模型自带的内容 tags」两个来源（真机首行这两块整段重复），人数词归一同样要看全行。
        head = _resolve_subject_counts(f"{anima_quality_tags(rating)}, {tag_head}")
        # 第 40 刀：族内归一之后再查**跨族**——girls 族已占满画面主体名额时，多出来的 boys 族
        # 计数词是孤儿（真机：第一人称下模型把**不入画的叙事者**写成了 `1boy`）。
        # 画面主体按 37 刀口径（已剔叙事者）⇒ 第三人称下叙事者占一个名额，`1boy` 自然保留。
        # 第 46 刀：有逐主体性别时口径**对称**（某族真值为 0 ⇒ 整族剔；纯男帧的 `1girl` 也剔）。
        head = _drop_orphan_subject_counts(
            head,
            len(_contract_subject_names(scene or {})),
            _contract_subject_genders(scene or {}),
        )
        # 第 60 刀（2026-09-28）：第一人称下剔掉**旁观/取景矛盾机位词**。
        # `_VIEWPOINT_FORBIDDEN_CAMERA` 原先是一张**空转的禁表**——只被抄进
        # `_viewpoint_clause` 的中文指令，**从未被任何代码消费** ⇒ 模型不听话时
        # `from behind` / `three-quarter back view` / `looking down at face` 直接提交
        # 给 ComfyUI（真机 slot `illo-req-…-53` 四次重生成里命中两次）。
        # ⚠ 零回归锁：非第一人称 / 无收束依据 / 行内无命中 ⇒ 都逐字节返回原串。
        # 第 73 刀（2026-09-29）：先削掉首行里**指向镜头的方位短语**
        # （上游「禁用机位」条款曾亲手给出 `her back arching toward the camera`
        #   范例，探针 [38] 首行因此出现 `back facing the camera`。）
        head = _first_person_strip_camera_direction_tags(head, scene or {})
        head = _strip_bystander_camera_tags(head, scene or {})
        # 第 65 刀（2026-09-28）：给「叙事者部位事实」补**从画面下缘进入**的呈现限定
        # （**追加**原串、不改写 ⇒ 账本 `expected` 判据不受影响、43／47 刀 12 处锁全绿）。
        # ⚠ 必须夹在「剔禁词」与「补 POV」之间（与 `_first_person_head_guards` 同序）。
        head = _first_person_limb_entry_phrasing(head, scene or {})
        # 2026-10-23（用户定案）：画面事实写明对方比叙事者高 ⇒ 机械补**轻微仰拍**，
        # 并剔掉 `from above`/`high angle` 这类会把身高差画反的高机位词。
        head = _ensure_first_person_height_angle(head, scene or {})
        # 第 61 刀（2026-09-28 · 用户定案「机械补」）：第一人称下**确保首行有 POV 正向声明**。
        # 该词形在本仓原先**没有任何注入点**（只出现在注释里）⇒ 全靠 profile LLM 自觉；
        # 真机四次重生成里它只在第 1 次自觉写了 ⇒ 后三次按默认第三人称构图。
        # ⚠ 幂等：已有 `first person view`/`pov`/`male viewer pov` ⇒ 逐字节返回原串。
        head = _ensure_first_person_view_tag(head, scene or {})
        # 第 67 刀（用户真机审查）：JSON 残片一律不得交付（见 `_json_residue_hits`）
        # ⇒ 返回空串，走既有的「空稿」兜底通道。
        if _json_residue_hits(f"{head},\n{prose}"):
            return ""
        return _anonymize_prompt_names(
            f"{head},\n{prose}", scene or {},
        )
    if profile == "natural_language":
        return _anonymize_prompt_names(
            _unwrap_stray_parentheses(_strip_wrapping(raw)), scene or {})
    if profile == "niji_sections":
        value = _json_object(raw)
        # 第 64 刀：只给 `subject` 段做第一人称收口（风格段／附加段／参数段一律不动）。
        sections = _first_person_niji_guards(
            [str(value.get(key) or "").strip()
             for key in ("subject", "style", "additions", "suffix")],
            scene or {},
        )
        return _anonymize_prompt_names(
            _unwrap_stray_parentheses(_niji_sections_join(sections)),
            scene or {},
        )
    raise ValueError(f"未知提示词模式：{profile}")


def _restore_scene_value(value: object) -> object:
    if isinstance(value, str):
        return image_prompt_extract.restore_jailbreak(value)
    if isinstance(value, list):
        return [_restore_scene_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _restore_scene_value(item) for key, item in value.items()}
    return value


def _scene_for_facts(scene: Mapping[str, object]) -> dict[str, object]:
    """本地校验使用还原后的事实；防拦截标记不得污染格式和事实检查。"""
    restored = _restore_scene_value(scene)
    assert isinstance(restored, dict)
    protected = scene.get("protected_narrative")
    if isinstance(protected, str) and protected.strip():
        restored["narrative"] = image_prompt_extract.restore_jailbreak(protected)
    restored.pop("protected_narrative", None)
    return restored


def _first_person_narrator(scene: Mapping[str, object]) -> str:
    """第一人称且给了叙事者名 ⇒ 返回该名；否则空串（＝调用方一律不做删改，零回归）。

    第 37 刀（2026-09-21 用户实锤）统一口径：**「叙事者不入画」的唯一判据**。
    `viewpoint` 缺失／非法、或 `narrator` 为空 ⇒ 空串。
    """
    narrator = str(scene.get("narrator") or "").strip()
    if not narrator:
        return ""
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    return narrator


def _strip_narrator_from_model_view(scene: Mapping[str, object]) -> dict[str, object]:
    """第一人称下，从**模型可见副本**里摘掉叙事者（`actors` 名单 ＋ `appearance` 段落）。

    第 37 刀（2026-09-21 用户实锤）：手选第一人称，成图 `2girls` 正确却多写 `1boy`。
    画面主体名单（计划 `subjects` / 判据 `frame_subjects`）是对的 ⇒ 主人公是从
    **模型载荷**漏进去的：`actors` 把他算进 `char_name`，`appearance` 把他的段落塞进
    `char_description`／`worldbook` ⇒ 模型据此把叙事者写进画面。

    ⚠ 只改**载荷副本**，原 `scene` 一字不动 ⇒ `_actor_visual_details` 的句级归因
    （仍按 `actors` 全集，32 刀红线）、字段账本与兜底路径全部逐字节不变。
    """
    result = dict(scene)
    narrator = _first_person_narrator(scene)
    if not narrator:
        return result
    actors = result.get("actors")
    names: list[str] = []
    if isinstance(actors, list):
        names = [str(name).strip() for name in actors if str(name).strip()]
        result["actors"] = [name for name in names if name != narrator]
    appearance = result.get("appearance")
    if isinstance(appearance, str) and appearance.strip():
        # 段落头＝行首「名字[：:]」，只认**原名单**里的名字（叙事者已从 actors 移除，
        # 故用未过滤的那份做 marker），叙事者整段丢弃直到下一个段落头。
        marker = re.compile(
            rf"^\s*({'|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))})"
            r"\s*[：:]",
        ) if names else None
        kept: list[str] = []
        dropping = False
        for line in appearance.splitlines():
            hit = marker.match(line) if marker else None
            if hit:
                dropping = hit.group(1) == narrator
            if not dropping:
                kept.append(line)
        result["appearance"] = "\n".join(kept)
    return result


def _scene_for_model(scene: Mapping[str, object]) -> dict[str, object]:
    """模型输入保留防拦截正文并匿名化角色名；姓名只用于本地关联外貌。

    第 37 刀：第一人称下**先摘掉叙事者**（见 `_strip_narrator_from_model_view`），再匿名化。
    第 43 刀：第一人称下把「叙事者局部肢体入画」写成**显式事实行**（见 `_narrator_limb_facts`）
    ——只靠正文叙事，模型读不出「这是必须画进画面的东西」（`_viewpoint_clause` 那句
    「有出处就按事实写具体」举的例子全是手/臀，器官类没有先例）。
    """
    result = _strip_narrator_from_model_view(scene)
    protected = result.pop("protected_narrative", None)
    if isinstance(protected, str) and protected.strip():
        result["narrative"] = protected
    limb_facts = _narrator_limb_facts(result)
    if limb_facts:
        # ⚠ 只改**载荷副本**，原 `scene` 一字不动 ⇒ 字段账本与词表兜底走自己的注入点
        #   （`prompt_field_ledger` / `_concrete_scene_facts` / `_english_scene_details`）。
        result["narrative"] = (
            f"{str(result.get('narrative') or '')}\n"
            "【第一人称·叙事者局部入画事实（只写这些局部，不得写他的脸或全身）；"
            "**一律写成第一人称的呈现形态**——局部**从画面下缘进入**"
            "（`entering from the lower frame edge`／`cropped at the bottom edge`），"
            "**不写**它的根部、躯干、腿】"
            + "；".join(limb_facts)
        )
    return _anonymize_scene_characters(result)


def _character_names(scene: Mapping[str, object]) -> list[str]:
    names: list[str] = []
    actors = scene.get("actors")
    if isinstance(actors, list):
        names.extend(str(name).strip() for name in actors if str(name).strip())
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        names.extend(
            str(item.get("name") or "").strip() for item in subjects
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        )
    return list(dict.fromkeys(names))


def _replace_character_name(text: str, name: str, label: str) -> str:
    if not name:
        return text
    pattern = (
        rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        if name.isascii() else re.escape(name)
    )

    def replacement(match: re.Match[str]) -> str:
        prefix = text[:match.start()]
        if not prefix.strip() or re.search(r"[.!?]\s*$", prefix):
            return label[:1].upper() + label[1:]
        return label

    return re.sub(pattern, replacement, text, flags=re.I if name.isascii() else 0)


_CHARACTER_ORDINALS = (
    "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
)


def _character_labels(count: int) -> list[str]:
    """按出现顺序给角色分配**中性指代**。

    2026-09-20 用户实锤：第 3 名起原为 `adult character 3`——**裸编号直接进最终提示词**，
    生图模型读不懂，看着像程序编号泄漏。改成自然序数词（`the third adult character`），
    仍满足多人契约的「互斥指称 + 角色名词绑定」（`_ACTOR_NOUN_PATTERN` 只认 character 词根）。
    """
    labels = ["the primary adult character", "the second adult character"]
    for index in range(3, count + 1):
        offset = index - 3
        word = _CHARACTER_ORDINALS[offset] if offset < len(_CHARACTER_ORDINALS) else "additional"
        labels.append(f"the {word} adult character")
    return labels


def _anonymize_prompt_names(prompt: str, scene: Mapping[str, object]) -> str:
    names = _character_names(scene)
    for name, label in zip(names, _character_labels(len(names)), strict=False):
        prompt = _replace_character_name(prompt, name, label)
    return prompt


def _anonymize_scene_characters(scene: Mapping[str, object]) -> dict[str, object]:
    names = _character_names(scene)
    mapping = dict(zip(names, _character_labels(len(names)), strict=False))

    def rewrite(value: object) -> object:
        if isinstance(value, str):
            for name, label in mapping.items():
                value = _replace_character_name(value, name, label)
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): rewrite(item) for key, item in value.items()}
        return value

    result = rewrite(scene)
    assert isinstance(result, dict)
    return result


# 拒答句式识别/清洗收口到 prompt_clean（生图与视频提示词链共用同一套规则）。
_REFUSAL_RE = prompt_clean.REFUSAL_RE
_strip_refusal_suffix = prompt_clean.strip_refusal_suffix

# 渲染模型可能输出 <think>…</think> 推理段（新仓库/新大脑模型 CoT 行为不稳定，
# 2026-08-29 trace 实证：编译产物整段 think 混进提示词直接提交 ComfyUI）。
# 正则对齐 image_prompt_extract._THINK_RE/_THINK_OPEN_TAIL_RE。
_THINK_RE = re.compile(r"\s*<think\b[^>]*>[\s\S]*?</think>\s*", re.I)
_THINK_OPEN_TAIL_RE = re.compile(r"\s*<think\b[^>]*>[\s\S]*\Z", re.I)


def _strip_think(text: str) -> str:
    """剥掉 think 推理段（含未闭合直达结尾的尾部）；think 后的正文才是提示词。"""
    return _THINK_OPEN_TAIL_RE.sub("", _THINK_RE.sub("", text or ""))

# anima `visual_hook` 的「必须命中」清单（第 22 刀，2026-09-20 生产链实锤 ★1）。
#
# ⚠ 这张表**决定模型的动机构成**：凡在这张表里出现的词，模型为了过校验就会去写它。
# 原表把两类词混列——**处理方式词**（构图/光学处理，只用已有元素即可写）
# 与**需要新反光面/材质的词**（`mirror` / `reflection` / `refraction` / `translucent`）。
# 后者的「合规解」在窗口里没有镜子时**只能是编一面镜子**：L1 窗口 44 字、原文没有任何
# 镜面，成稿仍写出 tag `wall mirror reflection` ＋ 文段 `a wall mirror catches a blurred
# reflection of her face`——模型不是「坏了」，是在**按必须命中的清单交作业**。
# ⇒ 本表只保留「不引入新物件即可满足」的构图与处理方式词；具体道具/材质词一律移出。
# 与修法 ②（`_unsourced_visual_device_errors` 出处闸）配套：一个消除动机，一个机械兜底。
#
# ⚠ 这一列只判「hook 有没有写到**怎么成像**」，不当词表考试（2026-09-20 真机实测两处误报）：
#   ① L3·anima 首轮写 `mirroring each other's posture … in a symmetrical V-shaped display`
#      ——语义完全合格，却因表里只有 `symmetry`（无 `symmetrical`）被判「没写装置」；
#   ② 收窄成「构图名词小表」后，L1/L2·anima 两格仍各白跑一次 repair（hook 措辞没落进小表）。
#   ⇒ 判据改成**词干 + `\w*` + 宽面**（光/镜头/构图/焦点/角度/色调任意一个即算），
#      只保留下限：必须谈「怎么拍」，不能只罗列「画面里有什么」（那由 `_ANIMA_VAGUE_HOOKS` 挡）。
#   道具词不在此列——编装置由出处闸（`_unsourced_visual_device_errors`）管。
_ANIMA_VISUAL_DEVICE_RE = re.compile(
    r"\b(?:fram\w*|occlu\w*|silhouette\w*|negative space|space|foreground\w*|background\w*|"
    r"depth|focus\w*|repet\w*|rhym\w*|contrast\w*|scale|proportion\w*|perspectiv\w*|"
    r"diagonal\w*|symmetr\w*|asymmetr\w*|shadow\w*|highlight\w*|light\w*|backlit|backlight\w*|"
    r"layer\w*|line of sight|leading line|gaze line|contact point|motion trail|"
    r"angle\w*|shot|composition\w*|palette\w*|tone\w*)\b",
    re.I,
)
_ANIMA_VAGUE_HOOKS = {
    "dramatic scene", "dramatic departure scene", "beautiful scene", "cinematic scene",
    "emotional scene", "climax scene", "character portrait", "group scene",
}

_ANIMA_ACTION_RE = re.compile(
    r"\b(?:reaching|gripping|holding|pulling|pushing|walking|running|turning|looking|"
    r"raising|lowering|standing|sitting|kneeling|leaning|bending|lying|embracing|kissing|"
    r"fighting|attacking|swinging|drawing|opening|handing|climbing|falling|jumping|"
    r"carrying|lifting|unloading)\b",
    re.I,
)
# T2（2026-09-23 深夜）：「扣后颈」曾被误升格为「掐喉」。原式 `(?:掐|扣|卡)…(?:脖子|颈部?|喉咙)`
# 里 `颈部?` 会匹配**后颈 / 颈侧 / 颈背 / 颈椎**，且 `扣` 语义本可只是「按住」——
# 真机正文写的是「一手撑住榻底，一手**扣她后颈**」（＝按住后颈，画面里根本没有掐喉），
# 却被确定性层升格成 `one hand gripping the partner's throat`；一旦与 800 字内的插入动词同现，
# 还会连带触发 `_COMPOUND_THROAT_INTERCOURSE`（**删除** `penetrative intercourse` 等真实动作、
# 换成一条编排出来的复合动作）⇒ 属第 22 刀「无出处不得入画」同族。
# 修法＝**解剖位限定**：`(?<!后)颈(?:部)?(?!侧|背|椎)` —— 真正的「掐脖子 / 卡喉咙 / 掐颈」逐字不变。
_THROAT_GRIP_SOURCE = (
    r"(?:掐|扣|卡)(?:着|住|紧)?.{0,8}(?:脖子|喉咙|(?<!后)颈(?:部)?(?!侧|背|椎))|"
    r"(?:脖子|喉咙|(?<!后)颈(?:部)?(?!侧|背|椎)).{0,8}(?:掐|扣|卡)(?:着|住|紧)?"
)
_PENETRATION_SOURCE = r"(?:重新.{0,6}(?:推进|进入)|推进去|推入|插入|抽插|交媾|做爱|性交)"
_COMPOUND_THROAT_INTERCOURSE = (
    "penetrative intercourse while the secondary adult partner grips the primary woman's throat"
)
_SCENE_ACTION_RULES = (
    (
        rf"(?:{_THROAT_GRIP_SOURCE})[\s\S]{{0,800}}(?:{_PENETRATION_SOURCE})|"
        rf"(?:{_PENETRATION_SOURCE})[\s\S]{{0,800}}(?:{_THROAT_GRIP_SOURCE})",
        _COMPOUND_THROAT_INTERCOURSE,
    ),
    (_THROAT_GRIP_SOURCE, "one hand gripping the partner's throat"),
    (_PENETRATION_SOURCE, "penetrative intercourse"),
    (r"传教士|(?:仰卧|躺)[\s\S]{0,80}面对面|面对面[\s\S]{0,80}(?:仰卧|躺)", "face-to-face missionary position"),
    (r"(?:双腿|大腿|腿).{0,8}(?:压|架|搭).{0,10}(?:对方|他的|她的)?肩", "legs raised over the partner's shoulders"),
    (r"火车便当|抱离地面.{0,12}(?:悬空|贴住)|悬空.{0,12}(?:抱|贴住)", "standing suspended carry position"),
    (r"(?:^|[^0-9])69(?:式|姿势|体位)|六九式|上下交叠", "mutual oral 69 position"),
    (r"伸手", "reaching out"),
    (r"抓住.{0,4}手腕|握住.{0,4}手腕", "gripping wrist"),
    (r"拉向|拽向|拖向", "pulling another person"),
    (r"推开|推向|推动", "pushing"),
    (r"走向|走进|步入|行走", "walking"),
    (r"奔跑|跑向|冲向", "running"),
    (r"转身", "turning"),
    (r"回头", "looking back"),
    (r"注视|凝视|看向|望向", "looking at"),
    # 2026-09-21 第 31 刀（用户实锤：「回头妩媚勾引的看向镜头的神情没有在文段环节表示出来」）：
    # 原表只有上面两条**无神态**的动作串，且都以动名词开头 ⇒ 归动作槽、**结构上进不了文段**
    # （第 27 刀定案「动作事实不进散文尾巴」）。用户在源文里能指到出处
    # （`两人带着还没满足的眼神回头看向我。` / `回头望来的世熙和宥夏同时屁股一颤。`）
    # ⇒ 是「有出处没落实」，不是编造。
    # 本支产出一条**名词短语**事实（`a … look …`）⇒ 走 `depicted with` 状态槽，能真正落进文段。
    # ⚠ 触发必须**回眸＋媚态**同现（或「媚眼/媚态」＋看向）：孤立「妩媚」可能只是舞姿或衣着，
    #   不得凭空补神情（第 22 刀「无出处不得入画」同族）。
    (r"(?:回头|回眸|扭头)[^。！？!?\n]{0,12}(?:媚|勾引|撩人|挑逗|风情|娇媚|妖媚)|"
     r"(?:媚眼|媚态|勾魂)[^。！？!?\n]{0,10}(?:看|望|瞥|回头|回眸)",
     "a coquettish come-hither look directed back over her shoulder"),
    # 2026-09-20 第 27 刀（用户实锤「第二个角色根本没有描述内容」）：具名角色只剩「旁观」一句时，
    # 旧表**一条都咬不住** ⇒ 逐人分述退化成 `is present in frame`，该角色等于从画面消失
    # （情况①原文：`宥夏在旁边看着。`）。两条都只认**同句**句式，跨句不触发。
    (r"(?:在旁|在一旁|在旁边|站在一旁|站在一边)[^。！？!?\n]{0,8}(?:看|望|观|注视|观察|瞧)",
     "watching from the sidelines"),
    (r"(?:看着|瞧着|望着|观看)[^。！？!?\n]{0,8}(?:她|他|她们|他们|两人|二人|这一幕|这一切)",
     "watching them"),
    (r"抬头|仰头", "raising head"),
    (r"低头|垂首", "lowering head"),
    (r"站起|站立|站在", "standing"),
    (r"坐下|坐在", "sitting"),
    (r"跪下|跪在", "kneeling"),
    (r"靠在|倚在", "leaning"),
    (r"俯身|弯腰", "bending forward"),
    (r"躺下|躺在|卧在", "lying down"),
    (r"拥抱|抱住", "embracing"),
    (r"亲吻|吻住", "kissing"),
    (r"攻击|交战|战斗", "fighting"),
    (r"挥剑|挥刀|劈砍", "swinging a weapon"),
    (r"拔剑|拔刀", "drawing a weapon"),
    (r"打开|开启", "opening"),
    (r"递给|交给", "handing over"),
    # 2026-09-19 第 7 项：原为 `取出|拔出|抽出|移除 → removing the visible object`（一条粗规则）。
    # 实测（854 段真机语料）四源词只命中 3 段，且**没有一段是「取出容器里的物件」**：
    #   L1「要拔出来了。」/ L3「我悄悄抽出了正在抚弄阴阜的手指。」/ L3「我猛地抽出了正扒开世熙臀部的手指。」
    # ⇒ 产出「把可见物体拿开」这个**与画面无关**的串（在 NSFW 语境更会被读成「移除遮挡物」）。
    # 规则表是**并列全扫描、无优先级** ⇒ 粗串会与更具体的串**同时**进 tags，不会被后者压掉；
    # 优化项⑤ 的补丁降级又把它经 `complete_field_coverage` **嫁接进模型稿**（同轮追加
    # 「In this instant, the concrete action is …」）⇒ 须按词义拆开（每支＝一个可见动作）。
    (r"取出", "taking an object out of a container"),
    # 2026-09-20 用户实锤：原串 `withdrawing what was sheathed`（抽出鞘中之物）在真机语料里
    # **全是「抽出手指」**（「我悄悄抽出了正在抚弄阴阜的手指。」「我猛地抽出了正扒开世熙臀部的手指。」），
    # 读起来与画面无关。规则表是**并列全扫描、无优先级** ⇒ 用负向先行断言让粗串在手指语境下不触发。
    (r"(?:抽出|拔出)(?=.{0,10}(?:手指|手|胳膊|手臂))", "withdrawing the fingers"),
    (r"(?:拔出|抽出)(?!.{0,10}(?:手指|手|胳膊|手臂))", "pulling out"),
    (r"移除|移开", "moving an obstruction aside"),
    (r"摩擦|磨着|磨动|揉动", "rubbing the contact point"),
    (r"撤开|撤掉|抽回|收回", "withdrawing just before completion"),
    (r"(?:腰|身体).{0,12}(?:追|拱|顶)(?:向|过去|上去)?", "her body following the withdrawn contact"),
    (r"(?:膝盖|双腿|大腿).{0,8}(?:分开|打开)", "knees spread apart"),
    (r"压住|按住", "pressing down"),
    # 2026-09-01 用户实锤：高潮动作未映射，确定性兜底只剩通用 2girls 模板。
    # 补齐本作高频 NSFW 动作词汇，至少让兜底提示词咬住高潮时间点的动作。
    (r"(?:阳具|肉棒|肉柱|柱身).{0,12}(?:掐|握|攥|抓|按)|(?:掐|握|攥|抓|按).{0,12}(?:阳具|肉棒|肉柱|柱身)", "gripping the shaft"),
    (r"(?:拇指|指尖).{0,12}(?:压|按|碾).{0,12}(?:系带|龟头|马眼)|(?:系带|龟头|马眼).{0,12}(?:被|遭).{0,12}(?:压|按|碾)", "thumb pressing the frenulum"),
    (r"(?:顶弄|顶动|挺动|抽送|抽插)", "thrusting"),
    (r"(?:口交|口含|含住|吞入|舔舐|舔净).{0,12}(?:阳具|肉棒|柱身|龟头)|(?:阳具|肉棒|柱身|龟头).{0,12}(?:含住|吞入|舔舐|舔净)", "fellatio with the shaft in the mouth"),
    (r"(?:指尖|手指).{0,12}(?:挑开|挑逗|爱抚|抚摸)", "fingertips teasing"),
    (r"(?:冰凤|灵力|阳元).{0,12}(?:灌入|灌顶|注入)", "channeling spiritual energy into the body"),
    (r"(?:搬|卸).{0,12}木箱|木箱.{0,12}(?:搬|卸)|搬下|搬到|卸下|卸货",
     "unloading a wooden medicine crate"),
    (r"扛着|背着|搬着", "carrying"),
    # 2026-09-20 第 27 刀（用户实锤：「只说了一个角色跪下掰开臀部，但本应该是弯腰翘起屁股掰开臀部」）
    # 体态缺一层：跪姿把臀部朝向镜头必然是**躬身抬臀**，但旧表只认「跪」「掰开」两个词。
    # ⚠ 必须排在下面「掰开」支**之前**：产出顺序＝本表顺序，先「弯腰抬臀」再「掰开」才读得通。
    # 两条都**同句**锚定（`[^。！？!?\n]` 不许跨句），跨句一律不触发（第 21 刀「跨句串味」同族）。
    (r"跪[^。！？!?\n]{0,20}(?:掰开|扒开|撑开|翘起|撅起|献上|亮出|敞开|展露|露出)"
     r"[^。！？!?\n]{0,10}(?:臀|屁股|臀瓣|臀肉|臀部)",
     "bending forward with her buttocks raised"),
    # 「抬高/献上臀部」独立成支（两个语序都要认：「翘起屁股」与「臀部高高翘起」）。
    (r"(?:翘起|撅起|抬高|抬起|挺起)[^。！？!?\n]{0,6}(?:臀|屁股|臀部)|"
     r"(?:臀|屁股|臀部)[^。！？!?\n]{0,6}(?:翘起|撅起|抬高|抬起|挺起)",
     "arching her back with her buttocks raised"),
    # 2026-09-20 第 24 刀（用户实锤）：L3 兜底**丢掉了剧情里的体位动作** ——「两名女主角
    # 翘起屁股、掰开臀瓣」在成稿里一条都没有，只剩 `looking back / looking at`。
    # 根因＝这张表里没有本作高频体位词。下面五支各自**只认明确出处**（反向锁见
    # `test_兜底动作表不凭空触发`），且限定在**同一句内**（`[^。！？!?\n]` 不允许跨句），
    # 避免第 21 刀「跨句动作片段串味」那类事故。
    # ⚠ 「掰开」支带负向断言：`…正扒开世熙臀部的手指` 这类**定语形式**不算画面主体的体位展示
    #   （焦点是「手指被抽出」，与第 17 刀同族）⇒ 保住既有精确断言，只认真正的展示句。
    # 2026-09-20 第 27 刀：触发词去掉 `撅起|翘起`。它们是「**抬高/献上**」而不是「**掰开**」，
    # 旧表会把「臀部高高翘起」误映射成 `spreading her own buttocks apart`；抬高另立一支（见下）。
    # 2026-09-21 第 31 刀（用户实锤）：「把双手贴在臀肉上掰开屁股」**一条源句同时命中两条规则**
    # ⇒ 首行出现 `spreading her own buttocks apart` + `both palms pressed against her own
    # buttocks` 两条**语义重叠**的串。用户原话：「说的词多还不够详细，不如直接说双手扒开臀部」。
    # 定案（用户选定「合并成一条」）：本支改写为 `both hands spreading her buttocks apart`
    # （把「双手」这一层并进同一条），并**删除**原「双掌贴臀」支 ⇒ 同一动作永不产出两条。
    # ⚠ 新串以**施动部位**开头、不是动名词 ⇒ 必须走 `_is_action_fact` 的动作槽，否则会被
    #   拼成 `The two adult characters are depicted with both hands spreading her buttocks
    #   apart.` 这种「复数主语＋单数物主」病句（第 29 刀同族，见 `_ACTION_FACT_LEAD_RE`）。
    (r"(?:掰开|扒开|撑开|分开)(?![^。！？!?\n]{0,8}的(?:手指|手))"
     r"[^。！？!?\n]{0,10}(?:臀瓣|臀肉|屁股|臀部|臀)|"
     r"(?:臀瓣|臀肉|屁股|臀部)[^。！？!?\n]{0,12}(?:掰开|扒开|撑开|分开)",
     "both hands spreading her buttocks apart"),
    (r"(?:臀|屁股|臀部|翘臀)[^。！？!?\n]{0,12}(?:晃|摇|摆动|摇曳)|"
     r"(?:晃|摇)(?:着|动)?[^。！？!?\n]{0,10}(?:臀|屁股)",
     "swaying her hips"),
    (r"(?:献上|亮出|露出|暴露|敞开|展露)[^。！？!?\n]{0,12}(?:小穴|后庭|肛门|穴)|"
     r"(?:小穴|后庭|肛门)[^。！？!?\n]{0,12}(?:露出|裸露|暴露|敞开|展露)",
     "exposing her pussy and anus"),
)


def _scene_action_tags(scene: Mapping[str, object]) -> list[str]:
    """从事实底座保守映射可见动作；只识别明确动词，不为纯肖像编造姿态。"""
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("draft_prompt") or ""),
    ))
    tags = [tag for pattern, tag in _SCENE_ACTION_RULES if re.search(pattern, source)]
    if _COMPOUND_THROAT_INTERCOURSE in tags:
        tags = [tag for tag in tags if tag not in {
            "one hand gripping the partner's throat", "penetrative intercourse", "bending forward",
        }]
    # 英文动作底座逐段判定（2026-09-18 贴合剧情修复）：narrative 是中文正文、draft_prompt
    # 是英文动作底座（同轮 <illustration> 成稿或 build_fallback_content_tags）。旧实现用
    # 整段 source 判 isascii，中文叙事一在场就整体跳过此分支——同一条英文动作事实在
    # 「无叙事」时被采纳、「有中文叙事」时被丢弃，连带 PromptFieldLedger 的 action 塌成
    # 非必需，动作字段退出验收。改为逐段判定：中文段不参与，英文段各自提取。
    for segment in (str(scene.get("draft_prompt") or ""), str(scene.get("narrative") or "")):
        if segment.strip() and segment.isascii():
            tags.extend(match.group(0).lower() for match in _ANIMA_ACTION_RE.finditer(segment))
    return list(dict.fromkeys(tags))


# ── 动作事实的「同部位已写过动作」冲突闸（2026-09-21 第 29 刀，用户定案口径） ──
# 真机实锤（i28 · L3·NSFW多人 | niji_sections / anima_tags）：模型已写
# `sits back with one hand touching a thigh`、`hands gripping and pulling apart their own
# buttocks`，账本仍把 `withdrawing the fingers` / `swaying her hips` /
# `both palms pressed against her own buttocks` 追加到同一句尾 ⇒
#   ① **语义互斥**：同一只手既「触摸大腿」又「抽出手指」；
#   ② **归属缺失**：裸动作串写死 `her`、不带主体，三名角色在场时读者分不清是谁的动作
#      （anima 文段还会产出 `The two adult characters are depicted with both palms pressed
#      against her own buttocks.` 这种复数主语＋单数物主的病句）。
# 用户定案：**同部位已有动作表述 ⇒ 该条动作事实不追加**（事实仍可走 tags 行 / 其他档）。
# ⚠ 零回归前提：**抽不出部位的动作事实一律不跳过**（判不了就不动），故
#   `penetrative intercourse` / `looking at` 这类无部位词的事实逐字节不变。
_ACTION_PART_GROUPS: tuple[tuple[str, str], ...] = (
    # （事实里出现的词干 / 该组在**成稿文本**里的部位写法）
    (r"\bhip|\bbuttock|\bass\b|\bcheek|\brear\b",
     r"hips?|buttocks?|ass|cheeks?|rear"),
    (r"\bfinger|\bhand|\bpalm|\bthumb|\bwrist",
     r"fingers?|hands?|palms?|thumbs?|wrists?|fingertips?"),
    (r"\bleg|\bthigh|\bknee|\bankle|\bfeet\b|\bfoot\b",
     r"legs?|thighs?|knees?|ankles?|feet"),
    (r"\bpussy|\bcunt|\blabia|\bvulva|\banus|\banal\b|\bnipple|\bbreast|\bshaft\b|\bcock\b|"
     r"\bpenis|\bsemen|\bcum\b",
     r"pussy|cunt|labia|vulva|anus|nipples?|breasts?|shaft|cock|penis|semen|cum"),
    (r"\bshoulder|\bneck\b|\bthroat", r"shoulders?|neck|throat"),
    (r"\bhead|\bface|\bmouth|\blip\b|\beye|\bgaze\b",
     r"heads?|faces?|mouths?|lips?|eyes?|gaze"),
    (r"\bwaist\b|\bback\b|\bspine|\bbody\b|\btorso\b", r"waist|back|spine|body|torso"),
    (r"\barm\b|\barms\b|\belbow|\bforearm", r"arms?|elbows?|forearms?"),
)

# 接触 / 位移 / 姿态动作词干：与部位**同时**命中才算「该部位已经写过动作」。
_ACTION_VERB_RE = re.compile(
    r"\b(?:touch\w*|hold\w*|grip\w*|grasp\w*|clutch\w*|press\w*|rest\w*|reach\w*|spread\w*|"
    r"pull\w*|push\w*|slide\w*|slip\w*|insert\w*|penetrat\w*|thrust\w*|withdraw\w*|strok\w*|"
    r"rub\w*|squeez\w*|massag\w*|pinch\w*|part(?:s|ed|ing)?|cup\w*|cradl\w*|caress\w*|"
    r"teas\w*|fondl\w*|brac\w*|hook\w*|wrapp\w*|dangl\w*|drip\w*|kneel\w*|lean\w*|"
    r"bend\w*|arch\w*|sway\w*|rock\w*|grind\w*|glid\w*|trac\w*|trail\w*|hang\w*|expos\w*)\b",
    re.I,
)

# 姿态 / 全身类动作事实：tag 自身没有具体部位词，用**部位覆盖表**兜底。
# 只登记真机成稿里实际出现过的条目；未登记 ⇒ 不参与冲突判定。
_ACTION_FACT_PART_OVERRIDES: dict[str, str] = {
    "leaning": r"\b(?:back|spine|body|shoulders?|weight|elbow|forearm|arm)\b",
    "bending forward": r"\b(?:back|spine|waist|body|torso)\b",
    "bending forward with her buttocks raised": r"\b(?:back|spine|buttocks?)\b",
    "arching her back with her buttocks raised": r"\b(?:back|spine|buttocks?)\b",
    "turning": r"\b(?:body|torso|shoulders?|waist|head)\b",
    "lying down": r"\b(?:bed|mattress|floor|sheets?|surface)\b",
}

# 神态事实**豁免**同部位冲突闸（2026-09-21 第 31 刀）：
# 本条的触发词里带 `shoulder`（回眸必然是「过肩」），按部位表会被划进「头/肩」组 ⇒
# 模型只要别处写过 `in her downward gaze` 之类的视线词 ＋ 任意动词，这条神态事实就会被
# 判成「同部位已写过动作」而整条丢弃 —— 而「写了视线」不等于「写了妩媚勾引」，
# 用户要的正是这一层。故显式豁免：**判不了就当不判**（与第 29 刀「抽不出部位的事实
# 一律不跳过」同一条口径），不改闸的判定逻辑本身。
_ACTION_FACT_NO_PART = frozenset({
    "a coquettish come-hither look directed back over her shoulder",
})


def _action_fact_part_re(fact: str) -> re.Pattern[str] | None:
    """动作事实的**部位模式**；判不出部位 ⇒ `None`（不参与冲突判定 ⇒ 不跳过）。

    部位按**同义组**归并：「臀部轻轻晃着」这条事实写成 `swaying her hips`，而模型稿里
    可能写的是 `spreading her own buttocks` —— 只比字面 `hips` 会漏判，故同组词一并认。
    """
    key = " ".join(str(fact or "").split()).lower()
    if key in _ACTION_FACT_NO_PART or key in _NARRATOR_LIMB_FACT_TEXTS:
        return None
    override = _ACTION_FACT_PART_OVERRIDES.get(key)
    if override:
        return re.compile(override, re.I)
    groups = [
        written for stem, written in _ACTION_PART_GROUPS if re.search(stem, key)
    ]
    if not groups:
        return None
    return re.compile(r"\b(?:" + "|".join(groups) + r")\b", re.I)


def _action_fact_conflicts(text: str, fact: str) -> bool:
    """既有文本是否已在**同一部位**上写过动作 ⇒ 该动作事实视为已覆盖（不追加）。

    真机实锤与定案见上（`_ACTION_PART_GROUPS` 注释块）。判据只做**词面**判断：
    「事实的部位词（同义组）」与「一段接触/位移/姿态动词」在**同一份文本**内共现即算冲突。
    ⚠ 本判据只删「账本候选」，不改任何既有文本、不动合同锁要求的**逐字**落地：
    事实若已由模型写在正文里，逐字锁本来就已经满足。
    """
    part = _action_fact_part_re(fact)
    if part is None:
        return False
    body = text or ""
    return bool(part.search(body)) and bool(_ACTION_VERB_RE.search(body))


def _tag_already_actions_part(tag_line: str, fact: str) -> bool:
    """**同一条 tag 内**该部位已带动作 ⇒ 这条动作事实在兜底模板里不再追加。

    与 `_action_fact_conflicts` 的分工：那条判的是**模型正文**（一句话里「部位＋动词」共现即
    算已写过）；兜底模板面对的却是一串**逗号 tags**，照搬整串共现判据会把 `kneeling,
    soft surface` 这种不相干组合误判成「已写过该部位的动作」⇒ 整批体位词被吞掉。
    故兜底路径改用**逐段（tag）内**共现：只有 `hands gripping and pulling apart their own
    buttocks` 这种**同一个 tag 里部位与动词都在**的串才算已写过。

    2026-09-21 第 31 刀（用户选定「合并成一条」时一并要求）：把第 29 刀的同部位闸接到
    兜底路径 —— 规则表从中文叙事推出的 action tag 不得与 `draft_prompt` 已写出的同部位动作
    重复入画。
    """
    part = _action_fact_part_re(fact)
    if part is None:
        return False
    return any(
        part.search(segment) and _ACTION_VERB_RE.search(segment)
        for segment in (tag_line or "").split(",")
    )


def _anima_contract_errors(raw: str) -> list[str]:
    # 第 33 刀（2026-09-21）：与 `_normalize` 同口径——`{}` 之外再认**字段式**（`字段: 值`）这一种
    # 合法序列化变体，否则 repair 轮明明按规格写全四字段、只是没加花括号，仍被判「必须输出包含艺术
    # 决策字段的JSON对象」而在结构层面白红（真机 `L3·NSFW多人` repair 轮实锤）。
    value = _json_object(raw) or _labelled_fields_object(raw)
    if not value:
        return ["必须输出包含艺术决策字段的JSON对象"]
    errors: list[str] = []
    hook = str(value.get("visual_hook") or "").strip()
    focus = str(value.get("primary_focus") or "").strip()
    supporting = value.get("supporting_elements")
    content = str(value.get("content") or "").strip()
    if (len(hook) < 18 or hook.lower() in _ANIMA_VAGUE_HOOKS
            or not _ANIMA_VISUAL_DEVICE_RE.search(hook)):
        errors.append("visual_hook必须写出具体可见的光学或构图装置")
    if (not focus or len(focus) > 300
            or re.search(r"[.;]\s*[A-Z]", focus)):
        # A2（2026-09-04 用户定案）：NSFW 高潮场景复合焦点是常态，不再按 160 字
        # 一刀切判死。primary_focus 只是模型中间思考字段，不直接进最终两行提示词；
        # content 合格时 focus 措辞过宽由 generate 分级保留（kept_content），仅对
        # 空 / 失控超长 / 分号句点罗列多个中心判错引导重写。
        errors.append("primary_focus必须且只能描述一个第一视觉中心")
    if not isinstance(supporting, list) or len(supporting) > 2:
        errors.append("supporting_elements辅助元素最多两个")
    elif any(not isinstance(item, str) or not item.strip() for item in supporting):
        errors.append("supporting_elements必须是非空英文字符串")
    if not content:
        errors.append("content不能为空")
    return errors


def _anima_scene_fact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """只校验剧情硬事实，不约束镜头、光影、色材或视觉装置。"""
    encounter = scene.get("encounter")
    encounter_values = encounter.values() if isinstance(encounter, Mapping) else ()
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("appearance") or ""),
        str(scene.get("wardrobe") or ""),
        str(scene.get("locale") or ""),
        " ".join(str(value) for value in encounter_values),
    ))
    content = prompt.lower()
    requirements = (
        (
            r"(?:搬|卸).{0,16}(?:木箱|药箱)|(?:木箱|药箱).{0,16}(?:搬|卸)",
            r"(?:unload|carry|lift|move)\w*.{0,80}(?:crate|box)|"
            r"(?:crate|box).{0,80}(?:unload|carry|lift|move)\w*",
            "正文明确搬运药材木箱，content必须保留搬箱动作",
        ),
        (r"药材|黄芪|艾草|干姜", r"medicinal herbs?|herbal medicine|astragalus|mugwort|ginger",
         "正文明确出现药材，content必须保留药材"),
        (r"宽肩", r"broad shoulders?", "人物宽肩特征不得丢失"),
        (r"方脸", r"square face", "人物方脸特征不得丢失"),
        (r"浓眉", r"thick eyebrows?", "人物浓眉特征不得丢失"),
        (r"虎牙", r"(?:prominent|visible|small) canine tooth|fang", "人物虎牙特征不得丢失"),
        (r"厚茧|老茧", r"calloused hands?", "人物掌心厚茧特征不得丢失"),
        (r"褐色短褂", r"brown .{0,20}(?:jacket|workwear|shirt)", "人物褐色短褂不得丢失"),
        (r"袖口挽|挽起袖|挽到肘", r"rolled(?:-up)? sleeves?", "人物挽袖状态不得丢失"),
        (r"孤儿院.{0,8}门|门.{0,8}孤儿院", r"orphanage (?:entrance|gate|doorway)",
         "孤儿院门口地点不得丢失"),
        (r"骡子.{0,12}(?:车|平板车)|骡车", r"mule(?:-drawn)? cart|mule cart",
         "骡车环境事实不得丢失"),
    )
    errors = [message for source_pattern, prompt_pattern, message in requirements
              if re.search(source_pattern, source) and not re.search(prompt_pattern, content)]
    has_matron_interaction = bool(
        re.search(r"院长", source) and re.search(r"招呼|查看|接受|迟疑|不安|惶然|伸手", source)
    )
    if has_matron_interaction:
        if not re.search(r"\b(?:matron|orphanage director|caretaker|headmistress)\b", content):
            errors.append("正文存在院长互动，content必须保留院长作为次级人物")
        if re.search(r"\b(?:solo|1girl)\b", content):
            errors.append("正文存在院长互动，禁止使用solo或1girl把关系场景退化为单人肖像")
    adult_occupation = bool(re.search(r"药材商贩|药材贩子|女商贩|女商人", source))
    if adult_occupation and not re.search(r"\b(?:adult woman|woman|2women)\b", content):
        errors.append("成年商贩身份不得退化为未标明成年身份的少女")
    unsupported_mule_action = re.search(
        r"\b(?:slap|slapping|pat|patting|hit|hitting|strike|striking)\b.{0,40}\b(?:mule|horse)\b",
        content,
    )
    source_mule_action = re.search(r"(?:拍|打|抚摸|抽|击).{0,12}(?:骡|马)", source)
    if unsupported_mule_action and not source_mule_action:
        errors.append("正文没有拍打骡子的动作，禁止凭空添加该动作")
    return errors


_CJK_RE = re.compile(r"[\u3400-\u9fff]")

# 「图像表现不出来」的高精度词面（2026-09-20 用户口径）：气味 / 声音 / 台词。只做**观测**，
# 不参与判死 —— 用于在真机 trace 里判读「定格化提示词是否真的减少了不可绘制内容」。
# 刻意只收高精度词：`moan` / `silence` / `breath` 这类既是声音又可能是可见姿态的词**不收**。
_UNPAINTABLE_RE = re.compile(
    r"\b(?:scent|smell|fragrance|aroma|odor|rustle|echo(?:es)?|whisper(?:s|ed|ing)?|"
    r"murmur(?:s|ed|ing)?|the word|dialogue|monologue)\b|[“”\"]",
    re.I,
)

_VAGUE_IDENTITY_RE = re.compile(
    r"\b(?:identified character|bound character|stable appearance|current clothing condition|"
    r"illustrated identity|replacement face|redesign (?:her|his|their) identity|"
    r"defined by the (?:bound )?(?:character )?model|established (?:facial structure|eye shape|"
    r"hairstyle silhouette|body proportions|appearance|identity))\b",
    re.I,
)

# 中文角色条目的高频可视事实：同时用于语义门禁和无模型兜底。规则只翻译视觉属性，
# 不含角色专名；当前 wardrobe/正文中的服装变化会覆盖条目里的基础【穿着】段。
_VISUAL_FACT_RULES: tuple[tuple[str, str, str], ...] = (
    (r"漆黑墨发|华贵墨发|墨发|乌黑(?:长)?发|黑发", "glossy jet-black hair", r"\b(?:jet-black|raven|ebony|black) hair\b"),
    (r"发团|发髻|盘发", "hair gathered into a rounded bun", r"\b(?:hair )?(?:bun|updo|chignon)\b"),
    (r"紫玉金髻|紫玉.{0,4}(?:簪|髻|钗)", "a purple jade and gold hair ornament", r"purple jade.{0,35}(?:hair|ornament|pin)|(?:hair|ornament|pin).{0,35}purple jade"),
    (r"朱唇|红唇", "full crimson lips", r"\b(?:crimson|red|scarlet|vermilion) lips\b"),
    (r"脸颊红润|红润.{0,3}脸颊", "rosy cheeks", r"\b(?:rosy|flushed|warm) cheeks\b"),
    (r"晶亮.{0,8}美目|美目.{0,8}晶亮", "luminous rounded eyes", r"\b(?:luminous|bright|clear|glossy|shining).{0,12}eyes\b"),
    (r"暗红.{0,5}(?:美眸|眼|眸)|(?:美眸|眼|眸).{0,5}暗红", "narrow dark-red eyes", r"\b(?:narrow )?dark-red eyes\b"),
    (r"熟厚双唇|丰厚.{0,3}(?:双唇|嘴唇)", "full mature lips", r"\bfull mature lips\b"),
    (r"成熟风韵|成熟.{0,4}(?:干练|韵味)|久经历练", "a mature and composed gaze", r"\b(?:mature|seasoned|composed|experienced).{0,18}(?:gaze|eyes|expression)\b"),
    (r"前凸后翘|曲线优美|丰腴熟躯|丰腴肥熟|丰腴.{0,4}(?:身材|曲线)", "a voluptuous curvy figure", r"\b(?:voluptuous|curvy|full-figured)\b"),
    (r"爆硕巨乳|丰满胸部|巨乳", "a very full bust", r"\b(?:very |large |ample |full )?(?:bust|breasts)\b"),
    (r"媚软纤腰|纤腰", "a narrow waist", r"\b(?:narrow|slender|slim).{0,10}waist\b"),
    (r"宽厚圆硕.{0,5}(?:臀|屁股)|圆硕.{0,3}(?:臀|屁股)|肥臀", "broad rounded hips", r"\b(?:broad|wide|full|rounded).{0,12}(?:hips|buttocks)\b"),
    (r"蚕丝白袜|白丝|白袜", "white silk stockings", r"\bwhite (?:silk )?(?:stockings|socks)\b"),
    (r"修长.{0,6}(?:美腿|双腿)|厚嫩美腿", "long legs", r"\blong.{0,12}(?:legs|thighs)\b"),
    (r"素紫色?薄纱法衣|紫色?薄纱法衣", "a light purple gauze robe", r"\b(?:light |pale )?purple.{0,15}gauze robe\b"),
    (r"道门徽记|灵符", "Taoist emblems and talisman motifs", r"\b(?:Taoist|Daoist).{0,18}(?:emblem|talisman)|talisman motif"),
    (r"胸口半敞|领口半敞", "a partly open neckline", r"\bpartly open (?:neckline|front|chest)\b"),
    (r"碎花紫长裙|紫色?碎花长裙", "a floral purple long skirt", r"\b(?:floral purple|purple floral).{0,12}(?:long )?(?:skirt|dress)\b"),
    (r"红色?长裙|红裙", "a red long dress", r"\bred (?:long )?(?:dress|skirt)\b"),
    (r"红莲.{0,8}(?:华袍|长袍|袍)[\s\S]{0,400}(?:华袍|衣袍|袍服)?残片|红莲.{0,8}(?:华袍|长袍|袍).{0,12}(?:撕开|毁损|残片)|(?:撕开|毁损|残片).{0,12}红莲.{0,8}(?:华袍|长袍|袍)|red lotus robe.{0,20}(?:torn|remnants)|(?:torn|remnants).{0,20}red lotus robe", "torn remnants of a red lotus patterned robe", r"\btorn remnants of a red lotus patterned robe\b"),
    (r"红莲纹饰华袍|红莲.{0,5}(?:华袍|长袍|袍)", "a red lotus patterned robe", r"\bred lotus.{0,20}(?:patterned )?robe\b"),
    (r"高叉开衩|高开叉|高叉", "a high side slit", r"\bhigh (?:side )?slit\b"),
    (r"(?:衣|裙|袍).{0,4}(?:破碎|破损|撕裂|扯破)|(?:破碎|破损|撕裂|扯破).{0,4}(?:衣|裙|袍)", "torn clothing with stressed folds", r"\btorn.{0,20}(?:clothing|robe|dress|skirt|fabric)\b"),
    (r"赤裸|全裸|一丝不挂", "a nude body with no remaining garments", r"\b(?:nude|naked).{0,18}(?:body|woman|character)?\b"),
    (r"媚药.{0,8}(?:香薰|盘香|香)|(?:香薰|盘香).{0,8}媚药", "aphrodisiac incense releasing sweet dense smoke", r"\baphrodisiac incense\b|\bincense.{0,35}(?:aphrodisiac|sweet dense smoke)"),
    (r"锁链|镣铐", "iron chains restraining the wrists", r"\b(?:iron )?(?:chains?|shackles?).{0,30}(?:wrists?|arms?)|\b(?:wrists?|arms?).{0,30}(?:chains?|shackles?)"),
    (r"石板|石台|石床", "a cold stone slab", r"\bcold stone (?:slab|platform)\b"),
    (r"囚衣", "a pale prisoner's robe", r"\bpale prisoner's robe\b|\bprison (?:robe|garment)\b"),
)


# ── 本轮事件性必现事实（T1，2026-09-23 深夜 · 用户实锤）──────────────────────
# 与 `_VISUAL_FACT_RULES` 的**分工**（两者同层、互补，源不同）：
#   · `_VISUAL_FACT_RULES` ＝**长驻外观/服饰**（发色/唇/胸/袜/袍…），源＝
#     `_current_visual_source`（appearance/wardrobe，且 narrative **只在衣物变化时**才并入）。
#   · 本表 ＝**本轮事件才产生的客观画面事实**（体液 / 睡眠状态），源**恒为 narrative
#     （取材窗口）**，与 `_current_visual_source` 解耦。
#
# ⚠ 为什么必须解耦（真机 2026-09-23 23:26 实锤，用户判定「提取错误」）：
#   取材窗口 1676 字里「第一股射在鼻梁和眼罩边沿／第二股歪了糊在她左颊／第三股落在唇上、
#   下巴」「精液挂在眼罩角上」「睡着的狐不认人」「她还没醒」**全都在**，成稿却
#   `cum/semen/facial/on face/closed eyes/sleeping/asleep` **一个都没有** ⇒ 画面变成
#   「阴茎抵在舌头上」而不是「睡着被颜射的脸」。而该帧 `wardrobe` 为空 ⇒
#   `clothing_changed=False` ⇒ narrative **根本不进** `_current_visual_source`
#   ⇒ 旧通道**结构性扫不到**这两类事实（实测：wardrobe="" 时源仅 25 字）。
#   ⇒ 只把规则加进 `_VISUAL_FACT_RULES` 是**无效**的，必须自带 narrative 源。
#
# 命中面已量（改前，全库所有存档）：体液族仅本作 1 处、睡眠族仅本作 8 处，其余存档全 0
# ⇒ 误伤风险极低。负向断言刻意排掉「不像睡着／睡意全无」这类否定写法。
_EVENT_FACT_RULES: tuple[tuple[str, str, str], ...] = (
    # 体液：颜射/精液在脸上——画面必须有体液（用户原话「师傅脸上应该有精液才是」）。
    # ① 直接写体液名词（最强、零歧义）；② 「射在/射满/射了」＋常见身体部位词
    # （真机窗口写的是「第一股射在鼻梁和眼罩边沿」，无「脸」字 ⇒ 部位词表须含鼻/眼罩等）。
    (r"精液|白浊|颜射|射满|射在[^。\n]{0,12}(?:脸|面|颊|腮|唇|嘴|口|鼻|眼|眉|颈|脖|肩|胸|乳|腹|肚|背|臀|腿|身)"
     r"|射了[^。\n]{0,8}(?:脸|面|颊|唇|嘴|口|胸|身)",
     "cum on face",
     r"\b(?:facial|cum|semen|ejaculat\w*|spunk)\b"),
    # 睡眠状态：睡中/未醒——画面必须闭眼（用户原话「师傅的睡觉…丢失了」）。
    # `(?!不着|不醒|意)` 排掉「睡不着／睡不醒／睡意」；`没醒` 仍算睡中故保留。
    (r"(?:睡(?:着|梦|沉|熟)|沉睡|阖眼|未醒|没醒)(?!不着|不醒|意)",
     "closed eyes",
     r"\bclosed eyes\b|\beyes (?:shut|closed)\b|\bsleeping\b|\basleep\b"),
)


def _event_fact_source(scene: Mapping[str, object]) -> str:
    """事件性必现事实的源＝**取材窗口**（`narrative`），恒扫、与衣物变化解耦。"""
    return image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))


def _required_event_facts(scene: Mapping[str, object]) -> list[str]:
    """窗口里出现的事件性事实（体液/睡眠）⇒ 画面必须体现，返回英文短语列表。"""
    source = _event_fact_source(scene)
    if not source.strip():
        return []
    return list(dict.fromkeys(
        phrase for pattern, phrase, _ in _EVENT_FACT_RULES if re.search(pattern, source)
    ))


def _missing_event_facts(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """窗口有、成稿漏的事件性事实（供报错与补写）；窗口无 ⇒ 空表（零回归）。"""
    source = _event_fact_source(scene)
    if not source.strip():
        return []
    return list(dict.fromkeys(
        phrase for pattern, phrase, output_pattern in _EVENT_FACT_RULES
        if re.search(pattern, source) and not re.search(output_pattern, prompt, re.I)
    ))


def _current_visual_source(scene: Mapping[str, object]) -> str:
    appearance = str(scene.get("appearance") or "")
    wardrobe = str(scene.get("wardrobe") or "")
    narrative = str(scene.get("narrative") or "")
    clothing_changed = bool(wardrobe.strip() or re.search(
        r"赤裸|全裸|一丝不挂|衣.{0,6}(?:破|裂|脱|褪)|裙.{0,6}(?:破|裂|脱|褪)|袜.{0,6}(?:破|裂|脱|扯)",
        narrative,
    ))
    if clothing_changed:
        appearance = appearance.split("【穿着】", 1)[0]
    subjects = scene.get("subjects")
    actors = scene.get("actors")
    actor_names = {
        str(name).strip() for name in actors if str(name).strip()
    } if isinstance(actors, list) else set()
    subject_details = "\n".join(
        str(item.get("description") or "") for item in subjects
        if isinstance(item, Mapping) and (
            not actor_names
            or not str(item.get("name") or "").strip()
            or str(item.get("name") or "").strip() in actor_names
        )
    ) if isinstance(subjects, list) else ""
    return "\n".join(filter(None, (
        appearance, wardrobe, narrative if clothing_changed else "", subject_details,
    )))


def _mapped_visual_facts(scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    facts = list(dict.fromkeys(
        phrase for source_pattern, phrase, _ in _VISUAL_FACT_RULES
        if re.search(source_pattern, source)
    ))
    if "torn remnants of a red lotus patterned robe" in facts:
        facts = [fact for fact in facts if fact != "a red lotus patterned robe"]
    return facts


# 句级归属的两个正则（2026-09-20 第 24 刀，第 25 刀修订口径）：
#   · 第一人称 ⇒ **按句内是否点名角色分两种**：点名的（「我紧捏**世熙**的臀部」）按名归到
#     被触及的角色名下（用户 2026-09-20 第 25 刀口径：主角**也有动作**，要判、不能一律丢）；
#     没点名的（「我悄悄抽出了…手指」）才跳过——归属对象答不出来就不写，宁缺勿猜。
#   · 回指代词 ⇒ 只在**有最近点名者**时按就近原则归属，绝不广播给全部角色。
_NARRATOR_SELF_RE = re.compile(r"我|咱|俺")
_ACTOR_PRONOUN_RE = re.compile(r"她|他|对方|二人|两人|彼此|她们|他们")

# ---------------------------------------------------------------------------
# 主角**对某个角色**的接触动作（2026-09-20 第 25 刀 · 用户原话）
#
# 「并不是主人公不吃体位词，而是要判断主角有没有所谓的动作 —— 如紧捏 xxx 角色的臀部，
#   然后你就可以说『（一只手）放在哪个角色的臀部上』；如果没有，那也不需要强调第一或第三人称。」
#
# 三条设计约束（缺一条就会退化成上一刀的「一律丢掉」或「凭空编造」）：
#   ① 与 `_SCENE_ACTION_RULES` **分开成表**：接触是「接触关系」，措辞走 `has …` 而不是
#      `performs …`；也**不得**污染 `_scene_action_tags` 的既有精确断言（第 17／24 刀都锁着它）。
#   ② **只从含第一人称的句子提取** ⇒ 「**她**把双手贴到臀肉上」（自己的手）不产接触 tag，
#      与既有的 `both palms pressed against her own buttocks` 分工明确、互不抢。
#   ③ **必须有被点名的角色**才归属（靠 `_actor_visual_details` 的按名归属天然保证）；
#      只有部位、没有角色 ⇒ 答不出「哪个角色」就**不写**（宁缺勿猜）。
#   ⚠ 词表刻意**窄**（只收臀部/腰/私处这类明确体位词），不收「肩/手/头」等通用部位——
#      否则「我看向世熙，揉了揉自己的肩膀」会被误判成对世熙的接触。
# ---------------------------------------------------------------------------
_SCENE_CONTACT_RULES = (
    (r"(?:捏|掐|揉|抚弄|抚摸|摩挲|按住|捂住|抓住|攥住|握住|摸着|扶着)"
     r"[^。！？!?\n]{0,10}(?:臀部|屁股|臀肉|臀瓣|臀|腰肢|腰)",
     "a hand resting on her buttocks"),
    (r"(?:抚弄|抚摸|揉|按|挑弄|摩挲|插入)"
     r"[^。！？!?\n]{0,10}(?:阴阜|私处|小穴|下体|花穴|腿间|股间)",
     "a hand touching her crotch"),
)
_CONTACT_TAGS = frozenset(tag for _pattern, tag in _SCENE_CONTACT_RULES)


def _scene_contact_tags(clauses: list[str]) -> list[str]:
    """提取「**主角的手**放在哪个角色身上」的可见事实（只认含第一人称的句子）。"""
    tags: list[str] = []
    for clause in clauses:
        if not _NARRATOR_SELF_RE.search(clause):
            continue
        tags.extend(
            tag for pattern, tag in _SCENE_CONTACT_RULES if re.search(pattern, clause)
        )
    return list(dict.fromkeys(tags))


# ---------------------------------------------------------------------------
# 第一人称「叙事者的局部肢体按剧情入画」（2026-09-22 用户实锤 · 第 43 刀）
#
# 用户原话：「第一人称时如果画面有描述肢体是能进入画面的……这个画面中第一人称视角本应该在
#   镜头下方会出现主人公的肉棒拍在画面女主角的脸上挤压的动作，但是却没有这个提示词，可能是
#   前面为了防止主人公完整入镜杀的太狠，导致之前协议的部分肢体可以根据剧情的主人公动作描述
#   进入画面的功能被杀掉了」。
#
# 归因（两条通道同时断，缺一都复现不了该帧）：
#   ① `_scene_contact_tags` 的**唯一调用点**在 `_actor_visual_details`，而后者在
#      `len(scope) < 2` 时早退；第一人称下画面主体只剩女主 ⇒ **单主体帧没有这条通道**；
#      唯一消费方 `_multi_actor_sentences` 同样只在 ≥2 主体时产出。
#      ⇒ 「局部入画」只剩 `_viewpoint_clause` 的 system 条款在喊，**没有任何确定性事实源**，
#        模型不主动写就是一条都没有。
#   ② 既有 `_SCENE_CONTACT_RULES` 只收「手 × 臀/腰/私处」，且要求句内出现第一人称
#      （`我/咱/俺`）——真机正文用的是「那条肉棒从单衣下摆里挣出来…命中她鼻梁以下的整张脸」
#      （主语是器官、叙事者是名字）⇒ 连句级门槛都过不去。
#
# 设计（**按类管、不是禁词表**）：
#   · 只认**部位本身决定归属**的类：男性器官（肉棒/阳具/阴茎/龟头…）不可能是画面里女性主体的
#     部位 ⇒ 归属无歧义，绝不会把女主自己的动作误配给叙事者（这是本表敢放宽句级门槛的前提）；
#   · 只在**同句**出现「器官 + 接触动作 + 被触部位」三元组时产出（第 22 刀同族：无出处不入画）；
#   · 只在 `first_person` 生效 ⇒ 第三人称下叙事者本来就在画面里、走常规事实链，逐字节零回归。
#   ⚠ 输出写成**名词短语**：既进得了 anima 的 `depicted with` 状态槽，也进得了散文通道；
#     动名词片段会被 `_is_dangling_fact` 剔掉，正是第 27 刀踩过的坑。
#   ⚠ 目标侧代词沿用既有 `_SCENE_CONTACT_RULES` 的 `her` 口径（画面主体为女性角色）。
# ---------------------------------------------------------------------------
_PENIS_NOUN = r"(?:肉棒|阳具|阴茎|玉茎|巨根|龟头|柱身)"
_FACE_NOUN = r"(?:脸|面|脸颊|颊|鼻|唇|嘴|口)"
_MOUTH_NOUN = r"(?:口|嘴|唇|口腔|喉咙?)"
_BUTT_NOUN = r"(?:臀|屁股|臀肉|后庭|股沟|腰)"
# ⚠ **必须限定「器官 → 接触动词 → 被触部位」的邻接距离**（2026-09-22 零 LLM 走查实锤）：
#   只判「同句三元组共现」会在真机正文上**过度命中** —— 原句
#   `…龟头顶端撞过眼罩下沿，留下一道黏腻的阳元浊痕，从她脸颊一直挂到嘴角。`
#   里的 `顶`（＝「顶端」的一半）与 `唇/脸` 同句 ⇒ 一次点着「压脸」与「口交」两条规则，
#   产出**正文里根本没有**的动作（＝凭空装置，踩第 22 刀红线）。
#   ⇒ 判据形同既有的 `_SCENE_CONTACT_RULES`：**动词与其宾语部位必须邻近**（≤10 字且不跨句）。
#   ⚠ 宁可漏（少写一条局部）绝不编造（多写一条没发生的动作）。
_LIMB_GAP = r"[^。！？!?\n]{0,10}"
_NARRATOR_LIMB_RULES: tuple[tuple[str, str], ...] = (
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:拍|甩|弹|抽打|击|撞|扇){_LIMB_GAP}{_FACE_NOUN}",
     "an erect penis slapping her face"),
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:贴|抵|压|蹭|摩|擦|挤压|顶){_LIMB_GAP}{_FACE_NOUN}",
     "an erect penis pressing against her face"),
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:插|顶|戳|捅|送入|推进){_LIMB_GAP}{_MOUTH_NOUN}",
     "an erect penis thrusting into her mouth"),
    (rf"{_PENIS_NOUN}{_LIMB_GAP}(?:顶|抵|蹭|摩|擦|贴|压){_LIMB_GAP}{_BUTT_NOUN}",
     "an erect penis pressing against her buttocks"),
)

# ---------------------------------------------------------------------------
# 第 47 刀 · 手/臂类「叙事者局部肢体入画」（2026-09-22 用户口径纠正）
#
# 用户原话：「肢体例外**不是说凌渊的手这种**，而是**主人公对于画面内容的干涉参与到画面内容**
#   才会显现，比如说**用双手抓住画面角色的巨乳**，或者是**用肉棒拍打画面角色的脸颊**。」
# ⇒ 判据＝「**叙事者（主人公）的身体部位作为施动者、主动作用于画面主体**」，
#   **与文本怎么称呼该部位无关**（喊「我」／喊名字／以部位直接作主语都算）。
#
# 43 刀只建了**器官类**：因为「器官本身决定归属」（男性器官不可能是画面里女性主体的部位）
#   ⇒ 无须句级门槛也判不错。**手/臂类没有这个性质**（「手」既可能是叙事者的、也可能是女主的）
#   ⇒ 本表用三重限定换回确定性（缺一就可能把别人的手算给叙事者）：
#     ① 施动者**不接受第三人称领有**：`她的双手…`／`他伸手…` 这种不认（`_HAND_NO_THIRD_OWNER`）；
#     ② 施动者与目标之间的间隙里不得出现 `自己的／自身`（女主抓自己 ⇒ 不是叙事者的手）；
#     ③ 句级门槛（见 `_hand_facts`）：分句里**有第一人称`我/咱/俺`**，或施动者**之前**没出现过
#        第三人称（第一人称叙事里，未标主的施动者就是叙事者）⇒ 才认。
#   ⚠ 三重限定**只作用于手/臂类**；器官类规则逐条不动（零回归）。
#   ⚠ 目标部位**刻意只收 胸/乳 与 脸** —— 25 刀 `_SCENE_CONTACT_RULES` 已管 臀/腰/私处
#     （多人帧走 `has a hand resting on her buttocks`），此处再收会与之**重复出两条事实**。
#   ⚠ 同 43 刀「宁漏不编」：只在**同一分句内**、**邻接且不跨句**（≤10 字）时产出。
# ---------------------------------------------------------------------------
_HAND_NOUN = r"(?:双手|两只手|一只手|手掌|巴掌|手指|指尖|手臂|双臂|手)"
_BREAST_NOUN = r"(?:巨乳|双乳|乳房|胸部|胸|奶子)"
_HAND_NO_THIRD_OWNER = r"(?<![她他])(?<!她的)(?<!他的)"
_HAND_SELF_DENY = r"(?!自己的|自身)"
_HAND_GAP = rf"(?:{_HAND_SELF_DENY}[^。！？!?\n]){{0,10}}"
# ⚠⚠ 措辞**必须以 `hands` 开头**（不可写 `a hand …`）：`_ACTION_FACT_LEAD_RE`（`:2997`）刻意
#   只认 `both hands?`／`two hands?`／`hands?` 与**男根一族**（认冠词的放宽只给器官类），
#   写成 `a hand grabbing her breasts` 会被判成**状态事实** ⇒ 塞进 `depicted with` 槽 ⇒
#   复述成 `The primary adult woman is depicted with a hand grabbing her breasts.`
#   —— 主语是女性主体、手却属于叙事者（第 29／31／43 刀同族的「主语/物主错配」病句）。
#   单/复数按 tag 惯用形归一（同既有的 `both hands spreading her buttocks apart` 口径）。
_NARRATOR_HAND_RULES: tuple[tuple[str, str], ...] = (
    (rf"{_HAND_NO_THIRD_OWNER}{_HAND_NOUN}{_HAND_GAP}"
     rf"(?:抓|握住|攥|捏|托|捧|揉|揉捏|掐){_HAND_GAP}{_BREAST_NOUN}",
     "hands grabbing her breasts"),
    (rf"{_HAND_NO_THIRD_OWNER}{_HAND_NOUN}{_HAND_GAP}"
     rf"(?:抚摸|抚弄|摩挲|按|压){_HAND_GAP}{_BREAST_NOUN}",
     "hands caressing her breasts"),
    (rf"{_HAND_NO_THIRD_OWNER}{_HAND_NOUN}{_HAND_GAP}"
     rf"(?:拍|扇|抚摸|捧住|托起|托|捧|捏|掐|按){_HAND_GAP}{_FACE_NOUN}",
     "hands touching her face"),
)
_THIRD_PARTY_RE = re.compile(r"她|他")

# ⚠ 这些事实**豁免第 29 刀的同部位冲突闸**（`_action_fact_part_re` 返回 None）：判据会把
#   「her face」并进「头/面」组 ⇒ 模型只要写了 `close-up on lower face` ＋ 任意姿态动词，
#   本事实就被判成「同部位已写过动作」而整条丢弃 —— 可「写了脸的特写」绝不等于「写了器官与
#   脸的接触」。同第 31 刀神态事实口径：**判不了部位就当不判**。
_NARRATOR_LIMB_FACT_TEXTS = frozenset(
    phrase for *_rest, phrase in (*_NARRATOR_LIMB_RULES, *_NARRATOR_HAND_RULES)
)


def _hand_facts(clause: str) -> list[str]:
    """手/臂类「叙事者局部肢体入画」事实（第 47 刀；判据见上表注释）。

    句级门槛（器官类**不设**，见上）：① 分句里有第一人称`我/咱/俺`；或
    ② 施动者**之前**没出现过第三人称（第一人称叙事里，未标主的施动者就是叙事者）。
    ⚠ 已知边界（宁漏不编的代价）：同一逗号分句里若先出现别的施动者
      （`我抱住她，她伸手拍她的脸`），① 仍会放行 ⇒ 手/臂类可能多认一条；器官类无此问题。
    """
    self_narrated = bool(_NARRATOR_SELF_RE.search(clause))
    found: list[str] = []
    for pattern, phrase in _NARRATOR_HAND_RULES:
        for match in re.finditer(pattern, clause):
            if self_narrated or not _THIRD_PARTY_RE.search(clause[: match.start()]):
                found.append(phrase)
    return found


# 第 62 刀（2026-09-28 · 用户定性）：第一人称「叙事者部位事实」的**取舍**。
#
# 用户原话：「2 的问题在于第一人称的**视觉下的取舍**，即第三人称是保证内容提示词选择合适
#   视角**全面展示信息**，那第一人称就是在内容的基础上**用第一人称去进行取舍或修改使其
#   符合第一人称情况下的特色**」。
#
# 真机现场（第 60 刀同一批取证 · 第 4 次重生成）：该帧是**上半身近身帧**（face 命中），
# 首行却**同时**写着 `an erect penis pressing against her buttocks`（下半身）与
# `… her face`（上半身）—— 两条**物理上不可能同现**，且前者根本装不进上半身取景。
# ⇒ 57 刀 ③ 的取景筛分**只覆盖 `appearance`/`wardrobe`**，**不覆盖**本函数产出的动作事实。
#
# ⚠ 判据**复用** 57 刀 ③ 的口径（同一个 4 键 blob ＋ `_VIEW_FACE_CONTACT_RE` 面优先）；
#   刻意**不改** `first_person_frame_filter` / `_first_person_framing` 的既有实现
#   （它们各自保留原 blob 构建 ⇒ 零回归），本函数只服务「叙事者部位事实的取舍」。
# ⚠ 只做**一个方向**（上半身帧剔下半身），反向不猜（与 57 刀 ③ 一致）；带**剔空保护**。
_FP_LOWER_LIMB_RE = re.compile(
    r"\b(?:buttocks?|butt|hips?|thighs?|legs?|waist|groin|lower body|lower frame)\b",
    re.I,
)


def _first_person_upper_frame(scene: Mapping[str, object]) -> bool:
    """本帧是否「上半身近身帧」（第 62 刀）：与 57 刀 ③ **同口径**（4 键 blob ＋ 面优先）。

    ⚠ 只读、纯函数；`viewpoint` 非 `first_person` ⇒ `False`（零回归锁）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return False
    sources: list[str] = []
    for key in ("narrative", "appearance", "wardrobe", "draft_prompt"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(image_prompt_extract.restore_jailbreak(value))
    blob = "\n".join(sources)
    return _view_face_frame(blob)


def _narrator_limb_facts(scene: Mapping[str, object]) -> list[str]:
    """第一人称下「叙事者局部肢体入画」的**确定性事实源**（第 43 ＋ 47 ＋ 62 刀）。

    ⚠ 零回归锁：`viewpoint` 缺失／非 `first_person`、或画面主体为空 ⇒ 返回空表
      ⇒ 所有下游（模型载荷／字段账本／词表兜底）逐字节等于旧行为。
    ⚠ 第 62 刀（用户定性「第一人称＝在内容基础上用第一人称取舍」）：产出**先按本帧取景族
      取舍** —— 上半身近身帧里**装不下**下半身部位事实（真机第 4 次重生成里
      `… against her buttocks` 与 `… her face` 同现、物理上不可能）。
      判据与 57 刀 ③ 同口径（4 键 blob ＋ 面优先）、只做一个方向、剔空保护。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return []
    if not _contract_subject_names(scene):
        return []
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    facts: list[str] = []
    for clause in re.split(r"[。！？!?；;\n]", narrative):
        if not clause.strip():
            continue
        facts.extend(
            phrase for pattern, phrase in _NARRATOR_LIMB_RULES
            if re.search(pattern, clause)
        )
        facts.extend(_hand_facts(clause))
    facts = list(dict.fromkeys(facts))
    if facts and _first_person_upper_frame(scene):
        # 第 62 刀：上半身帧 ⇒ 剔下半身部位事实；**剔空保护**（全被剔 ⇒ 保留原文，
        # 宁缺勿猜 —— 清空会让「叙事者局部入画」整条消失，与 57 刀 ③ 同族）。
        kept = [fact for fact in facts if not _FP_LOWER_LIMB_RE.search(fact)]
        facts = kept or facts
    return facts


def _actor_visual_details(scene: Mapping[str, object]) -> list[tuple[str, list[str], list[str]]]:
    """按高潮人物拆开外貌与动作，供所有 Profile 的多人确定性编译。

    ⚠ **两条名单必须分开**（第 32 刀，2026-09-21 用户实锤）：
      · **归因名单** ＝ `scene["actors"]`（取材窗口解析出的**在场**全集）——「认得出点名句」的依据。
        缩小它会让**点名第三人**的句子变成无名句，紧跟其后的代词句被就近误配给在场角色；
      · **输出名单** ＝ `_contract_subject_names(scene)`（**画面主体**；判据 `frame_subjects` /
        同轮计划 `subjects` 的正向证据优先）—— 画面里有几个人就写几个人。
    真机现场（L3·NSFW多人）：在场 3 人（含叙事者泰宇老师）、画面主体 2 人 ⇒ 原先多写一条
    `An additional adult character at the center of the frame is present in frame.`，
    ①与同稿的人数措辞 `The two adult characters' …` **自相矛盾**（写了 3 个人却说 two）；
    ②把叙事者摆到**正中央**、遮挡两名主体的翘臀掰臀动作（原文他自己也是 `behind them`）。
    无正向证据时 `_contract_subject_names` 退回 `actors` ⇒ 逐字节等于旧行为（零回归前提）。
    """
    actor_values = scene.get("actors")
    actors = [
        str(name).strip() for name in actor_values
        if str(name).strip()
    ] if isinstance(actor_values, list) else []
    scope = [str(name).strip() for name in _contract_subject_names(scene) if str(name).strip()]
    if len(scope) < 2:
        return []
    sources: dict[str, list[str]] = {name: [] for name in actors}
    appearance = str(scene.get("appearance") or "")
    marker = re.compile(
        rf"^\s*({'|'.join(re.escape(name) for name in sorted(actors, key=len, reverse=True))})\s*[：:]",
    )
    current = ""
    for line in appearance.splitlines():
        match = marker.match(line)
        if match:
            current = match.group(1)
        if current:
            sources[current].append(line)
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    # 2026-09-20 第 24 刀：句级归属必须**按出现顺序**做，不能「谁的名字出现在这句里就归谁」。
    # 真机 L3 窗口里「她把双手贴到臀肉上，小心翼翼地掰开屁股。」**没有名字**，只有回指代词
    # ⇒ 旧实现把它整句丢掉（探针实锤：逐人 facts/actions 全空 ⇒ 兜底退化成 `is present in frame`）。
    # 新口径三步：
    #   ① 含角色名的句子按名归属（多个名字同句 ⇒ 同句共享）；
    #   ② 无名的**就近前指代词句**归「最近一次被点名的人」（顺序敏感：必须在①之后判）；
    #   ③ **无名的**第一人称句不归任何角色，也**不改写**「最近点名者」——否则
    #      「我扒开世熙臀部，她轻轻晃着」里的后半句会被错记到上一句的角色名下。
    #      ⚠ 但**点了名的**第一人称句（「我紧捏着世熙的臀部」）必须先走 ① 按名归属
    #      （第 25 刀用户口径：主角也有动作，要判不要一律丢）——所以 ① 必须排在 ③ 之前。
    last_named = ""
    for clause in re.split(r"[。！？!?；;\n]", narrative):
        clause = clause.strip()
        if not clause:
            continue
        named = [actor for actor in actors if actor in clause]
        if named:
            for actor in named:
                sources[actor].append(clause)
            last_named = named[-1]
            continue
        if _NARRATOR_SELF_RE.search(clause):
            continue
        if _COLLECTIVE_SUBJECT_RE.search(clause):
            # 集体句（主语是「两侧/两人/双方…」）不参与就近代词归属：句里的**宾语**代词
            # （「两侧同时献上小穴和后庭，争着要**他**插进来」）会把整句引到某个具体角色名下，
            # 把女性体位动作塞给他（真机 L3 实锤：男主吃到了 swaying hips / exposing pussy）。
            continue
        if last_named and _ACTOR_PRONOUN_RE.search(clause):
            sources[last_named].append(clause)
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        for subject in subjects:
            if not isinstance(subject, Mapping):
                continue
            name = str(subject.get("name") or "").strip()
            description = str(subject.get("description") or "").strip()
            if name in sources and description:
                sources[name].append(description)
    details: list[tuple[str, list[str], list[str]]] = []
    # 输出只列**画面主体**（`scope`）：窗口里被提及但不在画面里的角色（旁观者 / 叙事者）不写进画面。
    # ⚠ 上面那段句级归因仍按 `actors` 全集跑 —— 否则点名第三人的句子会被判成无名句，
    #   紧跟的代词句被就近误配给在场角色（见函数 docstring）。
    for actor in scope:
        source = "\n".join(sources.get(actor, []))
        facts = _mapped_facts_from(source)
        english = source.encode("ascii", "ignore").decode("ascii")
        english = " ".join(english.split()).strip(" :,.;-")
        if english and re.search(r"[A-Za-z]{3}", english):
            facts.append(english)
        actions = _scene_action_tags({"narrative": source})
        # 第 25 刀：主角**对这个角色**的接触动作归到这里——该角色 source 里能出现的第一人称句，
        # 必然是「点了这个角色的名」才被按名归入的（无名的第一人称句在上面的循环里已被跳过）。
        contacts = _scene_contact_tags(source.splitlines())
        details.append((
            actor,
            list(dict.fromkeys(facts)),
            list(dict.fromkeys([*actions, *contacts])),
        ))
    return details


# 多人分述的**位置锚**（2026-09-20 用户实锤）：L3·anima 原成稿「tag 没把两名角色的动作表示出来、
# 没区分人物」。用户口径：「就算她们的动作都一样，你也要说这名角色在镜头左侧翘起臀部掰开屁股，
# 另一名角色在镜头右侧翘起臀部掰开屁股」⇒ 每人一条，**位置 + 该人自己的动作/可见事实**。
_MULTI_ACTOR_POSITIONS = (
    "on the left side of the frame",
    "on the right side of the frame",
    "at the center of the frame",
    "in the foreground of the frame",
    "in the background of the frame",
)

# 集体句（无点名主语、无第一人称，但主语是「两个人」）的指称词（2026-09-20 第 24 刀）。
# ⚠ 故意**不收**单字「都」「两个」以外的宽词：宁可不补齐，也不要把某一个人的动作广播给全员。
_COLLECTIVE_SUBJECT_RE = re.compile(r"两侧|两人|二人|双方|彼此|一起|同时|两个|她们|他们|全都")


def _collective_action_tags(scene: Mapping[str, object]) -> list[str]:
    """只从**集体句**提取动作，供「无独立证据的主体」补齐（2026-09-20 第 24 刀）。

    用户口径：动作一样，也要在各自位置各写一遍。但直接拿**整段**动作 tags 广播会踩
    第 21 刀跨句串味的坑（叙事者「我」的动作、别人的动作都会被塞进来）。这里只认
    **主语为集体**的句子——句中不出现点名的角色、也不出现第一人称，却出现集体指称词
    （`两侧同时献上小穴和后庭…屁股轻轻晃着。`）。
    """
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    actors = scene.get("actors")
    names = [str(name).strip() for name in actors if str(name).strip()] \
        if isinstance(actors, list) else []
    clauses = [
        clause.strip() for clause in re.split(r"[。！？!?；;\n]", narrative)
        if clause.strip()
        and _COLLECTIVE_SUBJECT_RE.search(clause)
        and not _NARRATOR_SELF_RE.search(clause)
        and not any(name in clause for name in names)
    ]
    if not clauses:
        return []
    return _scene_action_tags({"narrative": "\n".join(clauses)})


def _multi_actor_sentences(scene: Mapping[str, object]) -> list[str]:
    """多人逐人分述：每人一条，**位置锚 + 该人自己的可见事实与动作**。

    ⚠ 动作即使相同也**分别写**——两条句子各自带位置，读者不会误认为同一人。
    自己取不到独立证据的主体（真机 L3 常见：窗口里只有集体句）用**集体句动作**补齐，
    而不是丢成 `is present in frame`（用户实锤：兜底成稿里这两名角色等于消失）。
    有独立证据的主体**不许**再吃集体动作——否则会把两人的动作误配给第三人。
    """
    sentences: list[str] = []
    details = _actor_visual_details(scene)
    # 集体句动作只在**全员都取不到独立证据**时才启用。真机 L3 实锤：集体句的语义主体是
    # 两名女主角，宾语「他」指男主 ⇒ 按人补齐会把女性体位动作塞给男主。全员无证据才说明
    # 窗口里只剩集体句，此时逐人各写一遍＝用户口径（「动作一样也要各写一遍」）。
    collective = (
        _collective_action_tags(scene)
        if all(not actions for _actor, _facts, actions in details) else []
    )
    for index, (_actor, facts, actions) in enumerate(details):
        position = _MULTI_ACTOR_POSITIONS[
            index if index < len(_MULTI_ACTOR_POSITIONS) else -1
        ]
        label = f"The primary adult character {position}" if index == 0 else (
            f"The second adult character {position}" if index == 1
            else f"An additional adult character {position}"
        )
        parts: list[str] = []
        if facts:
            parts.append("is visibly defined by " + ", ".join(facts))
        effective_actions = actions or collective
        # 第 25 刀：接触类（「主角的手放在她臀部上」）用 `has …`，动作类用 `performs …`。
        # 无接触 tag 时输出与第 24 刀**逐字节一致**（零回归）。
        contact = [tag for tag in effective_actions if tag in _CONTACT_TAGS]
        performed = [tag for tag in effective_actions if tag not in _CONTACT_TAGS]
        if contact:
            # 接触类是**状态**，不是动作 ⇒ `has A and B`（不用 `then` 串，读起来才是「她身上有一只手」）
            parts.append("has " + " and ".join(contact))
        if performed:
            parts.append("performs " + ", then ".join(performed))
        if not parts:
            # 无事实可写也必须保留**位置锚**，否则这一名主体在画面里等于消失
            parts.append("is present in frame")
        sentences.append(label + " " + " and ".join(parts) + ".")
    return sentences


# 画面主体数的英文措辞（2026-09-20 第 24 刀）。真机 L3 是**三主体帧**，兜底却仍写
# `two adult women` / `two-character`（双口径残留）。2 人帧**逐字节保留**旧措辞＝零回归。
_ACTOR_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _multi_actor_count(scene: Mapping[str, object]) -> tuple[int, str]:
    """返回 (画面主体数, 英文数词)；数词最多到 five，再多一律 several。"""
    count = len(_contract_subject_names(scene))
    return count, _ACTOR_COUNT_WORDS.get(count, "several")


def _fallback_multi_actor_sentences(scene: Mapping[str, object]) -> list[str]:
    """兜底模板的多人分支内容——与多人契约共用同一条「画面主体数」口径。

    `_multi_actor_sentences` 只看 `actors`（取材窗口解析出的**在场**角色）。优化项③
    把**契约**改成按画面主体数判之后，若兜底仍走 actors，就出现自相矛盾的**双口径**：
    契约放行单人定格成稿，兜底却硬写 `two adult women` / `2girls, two-shot` /
    `The two adult characters is …`（2026-09-19 真机实锤：单人帧被观众席式地写成双人）。

    这里统一到 `_contract_subject_names`：一旦有正向证据（同轮计划 `subjects` /
    判据 `frame_subjects`）说这帧只一个主体，返回空表 ⇒ 四个兜底模板全部落到单人分支。

    无正向证据时 `_contract_subject_names` 退回 `actors`，本函数逐字节等于
    `_multi_actor_sentences` —— 这是零回归前提。
    """
    if len(_contract_subject_names(scene)) < 2:
        return []
    return _multi_actor_sentences(scene)


def _mapped_facts_from(source: str) -> list[str]:
    facts = list(dict.fromkeys(
        phrase for source_pattern, phrase, _ in _VISUAL_FACT_RULES
        if re.search(source_pattern, source or "")
    ))
    if "torn remnants of a red lotus patterned robe" in facts:
        facts = [fact for fact in facts if fact != "a red lotus patterned robe"]
    return facts


def _mapped_wardrobe_facts_from(source: str) -> list[str]:
    wardrobe_words = re.compile(
        r"\b(?:robe|dress|skirt|stockings?|socks?|clothing|garments?|neckline|slit|nude|naked|emblems?|talisman)\b",
        re.I,
    )
    return [fact for fact in _mapped_facts_from(source) if wardrobe_words.search(fact)]


def _missing_visual_facts(prompt: str, scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    lowered = prompt.lower()
    missing = list(dict.fromkeys(
        phrase for source_pattern, phrase, output_pattern in _VISUAL_FACT_RULES
        if re.search(source_pattern, source) and not re.search(output_pattern, lowered, re.I)
    ))
    # T1：事件性必现事实（体液/睡眠）走自己的 narrative 源，与外观类共用「漏」的报错出口
    # ⇒ `_visual_fact_errors` 报「未落实」、触发修补重试；窗口无该事实时返回空表（零回归）。
    missing.extend(
        phrase for phrase in _missing_event_facts(prompt, scene) if phrase not in missing
    )
    return missing


def _visual_fact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    # T1：事件性必现事实（体液/睡眠）走自己的 narrative 源，**不受 `source` 是否为空影响**——
    # 真机该帧 `wardrobe` 为空 ⇒ 旧实现在此早退 ⇒ 事件事实连报错机会都没有。
    event_missing = _missing_event_facts(prompt, scene)
    if not source.strip() and not event_missing:
        return []
    errors: list[str] = []
    if source.strip() and _VAGUE_IDENTITY_RE.search(prompt):
        errors.append("角色段落使用了空泛身份锁，必须改写为条目中的具体可视外貌与当前服装")
    errors.extend(
        f"角色条目中的可视事实未落实：{phrase}"
        for phrase in _missing_visual_facts(prompt, scene)
    )
    for name in _character_names(scene):
        if name.isascii() and _replace_character_name(prompt, name, "") != prompt:
            errors.append("最终提示词包含角色姓名；姓名只能用于关联外貌条目")
            break
    return errors


# ---------------------------------------------------------------------------
# 无出处视觉装置闸（第 22 刀，2026-09-20 生产链实锤 ★1）
#
# 现场：L1·anima 取材窗口 44 字、原文没有任何镜面，成稿却写 `wall mirror reflection`
# ＋ 文段 `Behind her, a wall mirror catches a blurred reflection of her face`。
# 与 `_visual_fact_errors` 的分工（两者同层、互补，**方向相反**）：
#   `_visual_fact_errors` 管**漏**——条目里有的可视事实必须落实；
#   本闸管**加**——画面事实里没有的装置不得入画。
# 此前只有「漏」有机械兜底，「加」只写在 system 提示里（【事实边界】），模型忽略后无人拦。
#
# 闸口只按**类**管（反光面 / 发光体 / 显示面 / 画框 / 水面），不列禁词表：
#   ① 出处判定看**输入画面事实**（外貌/服装/正文/地点）⇒ 原文写了镜子就放行，闸门管
#      「无出处」，不管词本身；
#   ② 比喻式提及（`the black lacquer lid acts as a dark mirror`）不引入新物件 ⇒ 放行；
#   ③ 三档共用同一份表（与 `_visual_fact_errors` 同层调用），避免只治一档。
# ⚠ 与 `_body_errors` 的关系：`_body_errors` 用空 scene 调用 ⇒ 出处来源为空 ⇒ 本闸必然
# 产出空表 ⇒ 优化项⑤ 的补丁门槛（判「稿子本体能不能用」）逐字节不受影响。
# ---------------------------------------------------------------------------
_UNSOURCED_DEVICE_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "镜面或其他反光面",
        # ⚠ 必须**带限定词的名词短语**（2026-09-20 真机实测误报）：L3·niji 写
        # `the primary adult character mirrors the same pose`——`mirrors` 是**动词**（同一姿势），
        # 不是新镜子。裸 `\bmirrors?\b` 会把它判红并白跑一次 repair ⇒ 用限定词锚住「作为物件被引入」。
        # ⚠ 第 30 刀（2026-09-21 真机实锤：第 22 刀那层限定词**没打穿**）：限定词与 `mirrors?` 之间
        #   允许 `(?:\w+\s+){0,2}` ⇒ 真机原句 `…on the right mirrors the same pose` 恰好**夹一个名词**
        #   （`right`／`left`），旧式照样命中 ⇒ L3·natural_language 首轮＋repair **各红一次**
        #   （零 LLM 复跑实证：`the right mirrors` / `the left mirrors` 双命中）。
        #   ⇒ 再补一层**宾语侧否定前瞻**：`mirror(s)` 作**动词**时必然接宾语
        #   （`mirrors the same pose` / `mirror each other`）；作**名词**时后面接谓语/介词/句读
        #   （`a wall mirror catches` / `the mirror on the wall` / `… the mirror.`）。
        #   `each other` **单列**：`the mirror each morning` 里的 `each` 只是状语，不能连坐（保召回）。
        #   ⚠ `this|that` **刻意不进宾语清单**：它们是**关系从句**的开头（`a wall mirror that catches …`
        #     是名词式，必须判红），而 `mirrors that pose`（动词＋指示词宾语）在成稿里几乎不出现
        #     ⇒ 两害相权保**召回**（放走编造镜面＝★1 原病，比白跑一次 repair 严重）。
        # 2026-09-21 第 31 刀（用户实锤：两处漏判 + 一处误报，零 LLM 已复现）：
        #   漏判① **裸名词 tag**（`wall mirror reflection`）没有限定词 ⇒ 旧式 `det + …` 整条
        #         不入闸。定案：限定词**改为可选** —— 裸名词短语同样是「把镜面引入了画面」，
        #         按「漏＝编造」优先于「多＝白跑一轮」的红线口径，必须收进来。
        #   漏判② `a full-length mirror catches …`：`full-length` 带连字符，旧式 `(?:\w+\s+)`
        #         被 `-length` 卡住 ⇒ 跨不过去、整条不入闸。定案：词形放宽为 `[\w-]+`。
        #   ⚠ 误报 `a mirror pose`（`mirror` 作**定语**）本轮**刻意不治**：它在词面上与
        #     「限定词 + mirror + 名词」的裸名词真串（`a wall mirror reflection`）**完全同形**，
        #     要治只能二选一——给 `mirror` 的接续名词开槽（＝禁列禁词表，踩红线），或要求后接
        #     谓词/介词（＝掉召回）。按红线「宁可白跑一轮 repair，不可放走编造镜面」⇒ 保召回。
        r"\b(?:(?:a|an|the|his|her|their|its|this|that|another|one|two|three)\s+)?"
        r"(?:[\w-]+\s+){0,2}"
        r"mirrors?\b(?!-like)"
        r"(?!\s+(?:a|an|the|another|his|her|their|its|my|your|our|him|them|it|us|me|you"
        r"|each\s+other|one\s+another)\b)|\bmirror image\b|"
        r"\breflecting (?:surface|glass|pool|water)\b",
        r"镜|倒影|映出|映着|反光|mirror|reflect",
    ),
    (
        "灯具或烛火光源",
        r"\b(?:lamps?|bulbs?|lanterns?|chandeliers?|sconces?|candles?|candlesticks?|torches?|"
        r"streetlights?|neon sign|fluorescent (?:light|lamp|tube))\b",
        r"灯|烛|火把|火炬|焰|萤|灯泡|lamp|bulb|lantern|candle|torch",
    ),
    (
        "屏幕或显示设备",
        r"\b(?:monitors?|screens?|televisions?|tv)\b",
        r"屏|显示器|电视|电脑|监控|monitor|screen|display",
    ),
    (
        "画框或画像",
        r"\b(?:(?:picture|photo|portrait|gilded|ornate) frame|hanging portrait|"
        r"painted portrait)\b",
        r"画框|相框|画像|画作|遗像|挂画|肖像",
    ),
    (
        "水面倒影",
        r"\b(?:reflecting pool|puddle|still water|water mirror)\b",
        r"水面|水洼|积水|水塘|池塘|湖|河|pond|lake|river",
    ),
)

# 比喻/借代标记：装置名词落在这种从句里时，说的是**已有物件的一种读法**，不是新增道具。
_DEVICE_SIMILE_RE = re.compile(
    r"\b(?:acts? as|reads as|serves as|functions as|doubles as|becomes|turns into|"
    r"like|as if|as though|almost|resembles)\b",
    re.I,
)


def _device_source(scene: Mapping[str, object]) -> str:
    """装置的**出处来源**＝输入画面事实 ＋ 地点。

    刻意复用 `_current_visual_source`（同一份事实口径，不另抄一份正则），只额外补
    `narrative` 与 `locale`：`_current_visual_source` 只在服装变化时才带正文，而装置
    出处必须**无条件**看正文（「她站在镜子前」正是这种事实），故这里显式再并一次
    （重复拼接只是冗余、不影响判定）。`locale` 是房间/街巷事实的真源，装置常写在那里。
    """
    return "\n".join(filter(None, (
        _current_visual_source(scene),
        str(scene.get("narrative") or ""),
        str(scene.get("locale") or ""),
    )))


def _device_hits_are_simile(prompt: str, pattern: re.Pattern[str]) -> bool:
    """逐处判定装置提及是否全为比喻式：只要有一处是实指，就按实指处理。"""
    hits = list(pattern.finditer(prompt))
    for match in hits:
        start = max(prompt.rfind(sep, 0, match.start()) for sep in ".;!?,\n")
        if not _DEVICE_SIMILE_RE.search(prompt[start + 1:match.start()]):
            return False
    return bool(hits)


# ═══════════════════════════════════════════════════════════════════════════════
# 第 70 刀（2026-09-29 · 与第 69 刀配套）：**隔着布料的器官接触**出处闸
#
# 第 69 刀放开了「隔着衣物接触」的写法（用户原话：「肉棒贴在女角色的小穴口隔着内裤布料
# 磨蹭、女角色内裤湿透透明」）。**放开即须配闸**：这类描述最容易被写到没有出处的帧上，
# 而既有的编造闸（`_UNSOURCED_DEVICE_RULES`）**只管道具、不含身体部位与接触形态**
# ⇒ 本刀按道具闸**同构**补上。
# ⚠ 与道具闸同一条红线：**宁可白跑一轮 repair，不可放走编造**。
# ⚠ 出处侧用**两个前瞻的合取**（「有隔绝/透湿/磨蹭」**且**「有布料」），
#   避免「只要有衣服词就放行」的空转（那样等于没有闸）。
# ⚠ 正则**刻意零反斜杠**（`(?<![A-Za-z])` 替 `\b`、`(?s)` 替 `[\s\S]`）。
_UNSOURCED_CONTACT_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "隔着布料的器官接触",
        r"(?<![A-Za-z])(?:bulge|bulging|outline|erection|erect|penis|shaft|groin|crotch|manhood)"
        r"(?![A-Za-z])[^.]{0,80}?(?<![A-Za-z])(?:through|under|beneath|against|behind)"
        r"(?![A-Za-z])[^.]{0,40}?"
        r"(?<![A-Za-z])(?:cloth|fabric|garment|clothes|clothing|underwear|underpants|"
        r"loincloth|robe|dress|skirt|trousers|pants)(?![A-Za-z])"
        r"|"
        r"(?<![A-Za-z])(?:through|under|beneath|against)(?![A-Za-z])[^.]{0,40}?"
        r"(?<![A-Za-z])(?:cloth|fabric|garment|underwear|robe|loincloth)(?![A-Za-z])"
        r"[^.]{0,60}?(?<![A-Za-z])(?:bulge|outline|erection|penis|shaft|groin|crotch)"
        r"(?![A-Za-z])",
        r"(?s)(?=.*(?:隔|透|湿|渗|洇|蹭|磨|贴|压|碾|rub|grind|press|wet|through))"
        r"(?=.*(?:布|衣|裤|袍|裙|cloth|fabric|robe|underwear|loincloth|garment))",
    ),
)

_UNSOURCED_CONTACT_MESSAGE = (
    "提示词写入了画面事实中没有出处的「隔着布料的器官接触」：{label}；"
    "这类接触只能在画面事实写明时才写（宁可少写也不编造）"
)


def _unsourced_contact_hits(
    prompt: str, scene: Mapping[str, object],
) -> list[tuple[str, re.Pattern[str]]]:
    """列出成稿里**没有出处**的「隔着布料的器官接触」（第 70 刀，与道具闸同构）。

    出处来源复用 `_device_source`（同一份事实口径，不另抄一份正则）。
    """
    source = _device_source(scene)
    if not source.strip():
        return []
    hits: list[tuple[str, re.Pattern[str]]] = []
    for label, prompt_pattern, source_pattern in _UNSOURCED_CONTACT_RULES:
        pattern = re.compile(prompt_pattern, re.I)
        if not pattern.search(prompt):
            continue
        if re.search(source_pattern, source, re.I):
            continue
        hits.append((label, pattern))
    return hits


def _unsourced_contact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """画面事实里没有出处的「隔着布料的器官接触」（第 70 刀）。"""
    return [
        _UNSOURCED_CONTACT_MESSAGE.format(label=label)
        for label, _pattern in _unsourced_contact_hits(prompt, scene)
    ]


_UNSOURCED_DEVICE_MESSAGE = (
    "提示词添加了画面事实中没有出处的视觉装置：{label}；"
    "画面里没有的装置不得入画（宁可少写也不编造）"
)


def _unsourced_device_hits(
    prompt: str, scene: Mapping[str, object],
) -> list[tuple[str, re.Pattern[str]]]:
    """列出成稿里**没有出处**的视觉装置（第 33 刀抽出：判错与「摘掉装置」共用同一份判定）。

    返回 `(类名, 已编译正则)`，判定口径与旧实现逐字相同（**出处放行**／**比喻放行**两个豁免都在）。
    """
    source = _device_source(scene)
    if not source.strip():
        return []
    hits: list[tuple[str, re.Pattern[str]]] = []
    for label, prompt_pattern, source_pattern in _UNSOURCED_DEVICE_RULES:
        pattern = re.compile(prompt_pattern, re.I)
        if not pattern.search(prompt):
            continue
        if re.search(source_pattern, source, re.I):
            continue
        if _device_hits_are_simile(prompt, pattern):
            continue
        hits.append((label, pattern))
    return hits


def _unsourced_visual_device_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """画面事实里没有出处的视觉装置：与 `_visual_fact_errors` 同层、方向互补。"""
    return [
        _UNSOURCED_DEVICE_MESSAGE.format(label=label)
        for label, _pattern in _unsourced_device_hits(prompt, scene)
    ]


def _device_prune_is_token_list(profile: str, index: int) -> bool:
    """这一行是**逗号 tag 串**（可按 token 摘）还是**英文散文**（只能按句摘）？"""
    if profile in ("anima_tags", "krea2"):
        return index == 0
    # niji 前三段都是逗号 tag 串；natural_language 整篇是散文。
    return profile == "niji_sections"


def _drop_unsourced_device_mentions(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> str:
    """把「画面事实里没有出处」的视觉装置从成稿里**摘掉**（第 33 刀 · 第 14 项补救）。

    为什么不做「整稿作废」：`_patch_failed_prompt` 的立意就是**别把模型的真实画面换成词表模板**
    （第 11／12 项用户实锤）；可凭空装置又绝不能随补丁交付（红线 22「宁可白跑一轮 repair，
    不可放走编造镜面」）。两头都要占 ⇒ **只摘装置提及**：
      · tag 串（anima／krea2 首行、niji 前三段）⇒ 按**逗号 token** 摘（tag 是可枚举粒度）；
      · 散文行（anima／krea2 第二行、natural_language 全文）⇒ 按**句子**摘（整句原文保留/丢弃，
        不做词级手术，免得把句子改到语法破碎）。
    摘完仍判红、或成稿已不成立（`_body_errors` 再红）⇒ 调用方照旧作废 ⇒ 失败方向仍是 fail-closed。
    """
    hits = _unsourced_device_hits(prompt, scene)
    if not hits:
        return prompt
    patterns = [pattern for _label, pattern in hits]
    kept_lines: list[str] = []
    for index, line in enumerate(prompt.split("\n")):
        if not line.strip() or line.lstrip().startswith("--"):
            kept_lines.append(line)
            continue
        if _device_prune_is_token_list(profile, index):
            tokens = [token.strip() for token in line.split(",") if token.strip()]
            kept = [token for token in tokens if not any(p.search(token) for p in patterns)]
            suffix = "," if line.rstrip().endswith(",") else ""
            kept_lines.append(", ".join(kept) + suffix)
            continue
        sentences = [seg for seg in re.split(r"(?<=[.!?])\s+", line) if seg.strip()]
        kept = [seg for seg in sentences if not any(p.search(seg) for p in patterns)]
        kept_lines.append(" ".join(seg.strip() for seg in kept))
    return "\n".join(kept_lines)


def _krea_contract_errors(
    prompt: str, scene: Mapping[str, object], *, check_length: bool,
) -> list[str]:
    errors: list[str] = []
    if not prompt.strip():
        return ["提示词为空"]
    if _REFUSAL_RE.search(prompt):
        errors.append("模型返回拒答")
    # 两段式（2026-08-30 用户定稿）：第一段内容/构图 tags，第二段英文内容文段
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if len(lines) != 2:
        return errors + ["Krea2必须输出两段：第一段为内容与构图tags，第二段为英文内容文段"]
    tags_line, prose = lines
    if _CJK_RE.search(tags_line):
        errors.append("第一段tags必须为纯英文")
    tags = [t.strip() for t in tags_line.split(",") if t.strip()]
    if len(tags) < 6:
        errors.append("第一段必须包含至少六个英文内容/构图tags")
    if _CJK_RE.search(prose):
        errors.append("第二段内容文段必须为纯英文，禁止夹杂中文")
    if re.search(r"\([^)]*:\s*\d+(?:\.\d+)?\)", prose):
        errors.append("第二段禁止权重语法（构图权重只能写在第一段tags）")
    if re.search(r"\b(?:masterpiece|best quality)\b", prose, re.I):
        errors.append("质量词只能由程序前置，不得写进文段")
    if check_length:
        words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", prose)
        if not 80 <= len(words) <= 500:
            errors.append("英文单词数必须为80到500")
    errors.extend(_multi_actor_contract_errors("krea2", prompt, scene))
    errors.extend(_visual_fact_errors(prompt, scene))
    return errors


def _inline_krea_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    return _krea_contract_errors(prompt, scene, check_length=False)


def _complete_inline_krea_facts(prompt: str, scene: Mapping[str, object]) -> str:
    """只补同轮成稿遗漏的可视事实，保留模型已经完成的高潮与艺术决策。"""
    errors = _inline_krea_errors(prompt, scene)
    fact_errors = _visual_fact_errors(prompt, scene)
    missing = _missing_visual_facts(prompt, scene)
    recoverable = bool(missing) and set(errors) == set(fact_errors) and all(
        error.startswith("角色条目中的可视事实未落实：") for error in fact_errors
    )
    if not recoverable:
        return ""
    subject = "The visible adult characters are specifically distinguished by "
    if len(_character_names(scene)) <= 1:
        subject = "The primary adult character is specifically defined by "
    addition = subject + ", ".join(missing) + "."
    first_sentence = re.search(r"[.!?](?:\s|$)", prompt)
    if first_sentence:
        end = first_sentence.end()
        repaired = prompt[:end].rstrip() + " " + addition + " " + prompt[end:].lstrip()
    else:
        repaired = prompt.rstrip(" .") + ". " + addition
    return "" if _inline_krea_errors(repaired, scene) else repaired


def normalize_inline(profile: str, raw: str, scene: Mapping[str, object] | None = None) -> str:
    """归一已有提示词或本地编译结果；不调用第二个模型。"""
    if profile not in PROFILE_IDS:
        profile = "anima_tags"  # 2026-08-31 默认协议切 Anima（krea2 模版不再默认）
    raw = _strip_refusal_suffix(raw)
    if profile == "niji_sections":
        text = _strip_wrapping(raw)
        prompt = _normalize(profile, text, scene) if text.lstrip().startswith("{") else text
    else:
        prompt = _normalize(profile, raw, scene)
    if profile == "krea2":
        if not _inline_krea_errors(prompt, scene or {}):
            return prompt
        return _complete_inline_krea_facts(prompt, scene or {})
    errors = _errors(profile, prompt, scene or {})
    if profile == "anima_tags" and errors and all(
        error.startswith("角色条目中的可视事实未落实：") for error in errors
    ):
        # 同义表述不应让整份 Agent 画面退化；字段账本会把精确稳定外貌补进两行。
        return prompt
    return "" if errors else prompt


def _contract_subject_names(scene: Mapping[str, object]) -> list[str]:
    """多人契约的「画面主体名集合」——决定要不要按多人成稿。

    用户口径（2026-09-19 定案）：一律按**画面主体数**判，**不按角色表人数、也不按在场人数**。
    优先级：
      ① 同轮计划 `subjects`（模型已声明的本帧主体，身份真源）；
      ② 判据档下传的 `frame_subjects`（高潮点定位判据判出的本帧主体名集合）；
      ③ 两者都缺 ⇒ **无正向证据**，退回 `actors`（取材窗口解析出的在场角色）。

    ③ 是**零回归前提**：旧行为即「只看 actors」，没有正向证据时逐字节不变。
    反过来，一旦有正向证据（判据/计划说这帧只一个人），契约就必须听它——否则取材窗口里
    被提及的旁观者会把单人定格帧推成多人帧，模型写对的单人成稿反被判死，甚至被硬塞
    「second adult character」污染画面（2026-09-19 真机实锤）。
    """
    narrator = _first_person_narrator(scene)

    def _drop_narrator(names: list[str]) -> list[str]:
        if not narrator:
            return names
        return [name for name in names if name != narrator]

    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        planned = [
            str(item.get("name") or "").strip() for item in subjects
            if isinstance(item, Mapping)
        ]
        planned = [name for name in planned if name]
        if planned:
            # 第 37 刀：① 计划 `subjects` 原本**早返回、不过人称滤** ⇒ 第一人称下叙事者
            # 仍被算成本帧主体（② `frame_subjects` 同理）。判据与 ③ 完全一致。
            return list(dict.fromkeys(_drop_narrator(planned)))
    frame = scene.get("frame_subjects")
    if isinstance(frame, list):
        named = [str(name).strip() for name in frame if str(name).strip()]
        if named:
            return list(dict.fromkeys(_drop_narrator(named)))
    actors = scene.get("actors")
    if not isinstance(actors, list):
        return []
    names = [str(name).strip() for name in actors if str(name).strip()]
    # 2026-09-20 用户定案：第一人称下叙事者不占画面主体数。③ 这条回落路径原本只看 actors，
    # 叙事者若在角色表里就会被算回来 ⇒ 收紧**只在一半路径生效**（第 37 刀把 ①/② 一并补齐）。
    # 只在 `narrator` 键**存在**时剔 ⇒ 老调用方（无该键）逐字节等于旧行为。
    return list(dict.fromkeys(_drop_narrator(names)))


# 多人契约的「语义分述」判据（优化项⑨ 句式放宽，2026-09-19）：
# 契约只要求「两名画面主体被**分开描述**」，不强制模板字样 `primary/second adult character`。
# 只认**词面**句式：命中一对互斥指称，且每个指称都与角色名词绑定（不做语义理解）。
_ACTOR_NOUN_PATTERN = (
    r"(?:woman|women|man|men|girl|girls|boy|boys|character|characters|"
    r"figure|figures|person|people|adult|adults)"
)

# 互斥指称对：同一对的两侧都命中，才说明两个主体被分开指称。
_ACTOR_CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    ("left", "right"),
    ("leftmost", "rightmost"),
    ("first", "second"),
    ("one", "other"),
    ("foreground", "background"),
    ("front", "back"),
    ("nearer", "farther"),
    ("closer", "farther"),
    ("upper", "lower"),
    ("former", "latter"),
)


def _actor_designated_by(prompt: str, marker: str) -> bool:
    """提示词是否用 `marker` 单独指称了一个画面主体（词面判据，不做语义理解）。

    只认三种通用句式，且都必须与角色名词直接绑定（避免把「台灯在左边」当成角色）：
      ① 序数/差集前缀：`first woman` / `the other man` / `one of the women`；
      ② 角色名词 + 方位介词短语：`the woman on the left` / `the man in the background`；
      ③ 方位前缀：`the left woman` / `the nearer figure`。
    """
    noun = _ACTOR_NOUN_PATTERN
    patterns = (
        rf"\b{marker}\b\s+(?:of\s+the\s+)?{noun}\b",
        rf"\b{noun}\b(?:\s+\w+){{0,1}}\s+(?:on|to|at|in)\s+"
        rf"(?:the\s+|his\s+|her\s+|their\s+)?{marker}\b",
        rf"\bthe\s+{marker}\s+{noun}\b",
    )
    return any(re.search(pattern, prompt or "", re.I) for pattern in patterns)


# 外貌／衣着区分式指称（第 44 刀 · 2026-09-22 用户定案「分述判据只认位置词，按你的修复来」）：
# 真机 B2（2026-09-22）成稿按 42 刀·D 的引导写成
#   `The blonde woman in the hollowed-out white wedding dress …`
#   `the silver-haired woman in the black bunny suit …`
# 两名主体**确实被分开指称**（blonde vs silver-haired），却因**一个位置词都没有**
# ⇒ `_ACTOR_CONTRAST_PAIRS` 一对都命中不了 ⇒ 判红 2 条 ⇒ 白烧一次 repair（2/2 复现）。
# ⚠ 与既有位置锚判据同族：**只判词面、不引词表**，靠「定语是否绑在角色名词上」这一句式特征，
#   不去列任何外貌词。
# ⚠ 与角色形象来源完全无关：本组判据只读 `prompt` 字符串，**不从这里反推／回写任何外貌事实**，
#   角色外貌仍一律来自 `scene_illustration` 的**三源融合（剧情表现 > 表格补充 > 角色条目，
#   第 36 刀）**。
_MODIFIER_TOKEN = r"[A-Za-z][A-Za-z'-]+"
# ⚠ ② 的介词是**功能词白名单**（不是外貌词表）：只收「绑定在身上之物」的介词短语，
#   刻意**排除** `in/at/on` —— `in the corridor`、`in the room` 这类是**地点**，
#   纳入就会把场景方位当成角色分述（保精度）。
_APPEARANCE_PREP = r"(?:with|wearing|dressed\s+in|clad\s+in|carrying|holding)"
_APPEARANCE_DESIGNATION_RES: tuple[re.Pattern[str], ...] = (
    # ① 定语在名词前：`the blonde woman` / `a silver-haired girl`
    re.compile(rf"\b(?:the|a|an)\s+({_MODIFIER_TOKEN})\s+{_ACTOR_NOUN_PATTERN}\b", re.I),
    # ② 定语在名词后（衣着／携带／发色）：`the woman with glossy jet-black hair` /
    #   `a girl wearing a black bunny suit` / `the blonde woman wearing a white wedding dress`
    #   （名词前可再带一个定语；短语本体最多 3 个实词，避免把后面的动词吞进来）
    re.compile(
        rf"\b(?:the|a|an)\s+(?:{_MODIFIER_TOKEN}\s+)?{_ACTOR_NOUN_PATTERN}\s+"
        rf"{_APPEARANCE_PREP}\s+"
        rf"(?:the\s+|a\s+|an\s+|his\s+|her\s+|their\s+)?"
        rf"((?:{_MODIFIER_TOKEN})(?:\s+{_MODIFIER_TOKEN}){{0,2}})",
        re.I,
    ),
)


def _appearance_designations(prompt: str) -> set[str]:
    """提取「定语 + 角色名词」式指称（**纯词面、无词表**）；只留**彼此无共享实词**的指称。

    只做**句式**判定：
      · ① 前置定语：取紧贴角色名词前的那**一个** token（`silver-haired woman` → `silver-haired`）；
      · ② 后置短语：取介词短语本体（`with glossy jet-black hair` → `glossy jet-black hair`，≤3 词）。
    ⚠ 两道保精度闸（都靠**结构**、不靠词表）：
      ① ② 的实词数必须 **≥2**（`the woman with a scar` 这种单词短语不算外貌指称）；
      ② 只保留**与其它指称无共享实词**的指称 —— `the woman with blonde hair` 与
         `the girl with blonde hair`（同一个人写了两遍）共享 `blonde/hair` ⇒ 判为同一指称。
    ⚠ 与角色形象来源无关：结果只由 `prompt` 决定，绝不反推／回写任何外貌事实。
    """
    entries: list[tuple[str, frozenset[str]]] = []
    for index, pattern in enumerate(_APPEARANCE_DESIGNATION_RES):
        for match in pattern.finditer(prompt or ""):
            phrase = " ".join(match.group(1).split()).lower()
            tokens = frozenset(token for token in re.split(r"\W+", phrase) if token)
            if not tokens:
                continue
            if index == 1 and len(tokens) < 2:
                continue
            entries.append((phrase, tokens))
    return {
        phrase for position, (phrase, tokens) in enumerate(entries)
        if all(
            tokens.isdisjoint(other_tokens)
            for other_position, (_other, other_tokens) in enumerate(entries)
            if other_position != position
        )
    }


def _has_separate_actor_designations(prompt: str) -> bool:
    """多人契约的「语义分述」等价物（优化项⑨ 句式放宽 2026-09-19；第 44 刀扩外貌锚 2026-09-22）。

    旧口径只认模板字样 `primary/second adult character|woman`；真机 L3·natural 成稿用
    `the woman on the left … the woman on the right …` 已把两名主体**分开描述**，却因不认
    模板句式被判红 2 条（`docs/illustration-prompt-delivery-2026-09-19.md` §3 第 3 项）
    ⇒ 契约验收的是**措辞**而不是**分述**。

    这里把「命中一对互斥指称、且各自绑定角色名词」视为已单独描述每个主体。
    **只放宽句式、不放松人数**：是否走多人契约仍由 `_contract_subject_names` 决定；
    指称对必须互斥（只写「左边」不算），且必须绑定角色名词（纯场景方位不算）。

    **第 44 刀（2026-09-22 用户定案）**：再加一路**外貌／衣着区分式指称** —— 出现
    **≥2 个互不相同**的「定语 + 角色名词」式指称（`_appearance_designations`）即视为已分述。
    理由：42 刀·D 明确**要求**模型用「可辨识身份（发色／发型／瞳色／体型）」区分两名主体，
    而位置锚词表认不出这种写法 ⇒ **我们要求的写法判不过我们自己的契约**。
    ⚠ 三条边界不动：① 仍**只判词面、不引词表**；② **不放松人数**（画面主体 <2 时
    `_multi_actor_contract_errors` 整段不跑；人多不多、人称口径全按 `_contract_subject_names`）；
    ③ **不碰角色形象来源** —— 本函数只读 `prompt` 字符串，角色外貌仍一律来自
    `scene_illustration` 的**三源融合（剧情表现 > 表格补充 > 角色条目，第 36 刀）**，
    绝不从提示词反推或回写外貌事实。
    """
    if any(
        _actor_designated_by(prompt, first) and _actor_designated_by(prompt, second)
        for first, second in _ACTOR_CONTRAST_PAIRS
    ):
        return True
    return len(_appearance_designations(prompt)) >= 2


def _multi_actor_contract_errors(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> list[str]:
    subject_count = len(_contract_subject_names(scene))
    if subject_count < 2:
        return []
    errors: list[str] = []
    if not _has_separate_actor_designations(prompt):
        if not re.search(r"\bprimary adult (?:character|woman)\b", prompt, re.I):
            errors.append("多人提示词必须单独描述第一角色")
        if not re.search(r"\bsecond adult (?:character|woman)\b", prompt, re.I):
            errors.append("多人提示词必须单独描述第二角色")
    if profile == "anima_tags":
        # 第 45 刀（2026-09-22 用户定案）：**删掉「必须包含 2girls」下限**。
        # 用户口径原话：「性别的判断是根据剧情来的」—— 人数与性别由**剧情实际**决定
        # （剧情里几人、什么性别，模型照着写），不得用「画面主体 ≥2 ⇒ 必须 `2girls`」
        # 这种机械规则反推性别。
        # 真机事故（2026-09-22 12:28，slot -47）：第一人称单女帧（师尊在榻边掖被子、
        # 视线落到主人公脸上）被这条下限逼出 `2girls` ⇒ 模型凭空多画一个女人；
        # 且 2 主体帧的区间 `2 ≤ girls ≤ 2` 会把写对的 `1girl, 1boy`（男主＋1 女）判死。
        # 保留**上界**：人数词不得超过画面主体数（仍与 `_contract_subject_names` 同口径）。
        # ⚠ boys 族刻意不进契约（见 `_subject_count_totals` 上方注释）：它的归属是
        # 归一化侧 40 刀的「剔孤儿」。
        girls, _boys = _subject_count_totals(prompt)
        if girls > subject_count:
            errors.append("Anima 提示词人数词不得超过画面主体数")
    return errors


_LENGTH_WARNING = "英文单词数必须为80到500"


def _split_length_warnings(errors: list[str]) -> tuple[list[str], list[str]]:
    """把词数校验从「触发重写」降级为「警告放行」（2026-08-30 成本实锤：一次 repair
    = 一次携带完整防拦截预设的调用 ≈$0.2-0.3；词数偏短不影响提示词合法性，
    长度只用于引导，硬卡它只会多烧一次调用）。返回（需修复错误, 警告）。"""
    warnings = [e for e in errors if e == _LENGTH_WARNING]
    return [e for e in errors if e != _LENGTH_WARNING], warnings


def _errors(profile: str, prompt: str, scene: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    if profile != "krea2" and _REFUSAL_RE.search(prompt):
        errors.append("模型返回拒答")
    if profile == "krea2":
        errors.extend(_krea_contract_errors(prompt, scene, check_length=True))
    elif profile == "anima_tags":
        lines = prompt.splitlines()
        tags = lines[0] if len(lines) == 2 else ""
        prose = lines[1] if len(lines) == 2 else ""
        if len(lines) != 2:
            errors.append("必须正好两行")
        if not tags.isascii() or len([tag for tag in tags.split(",") if tag.strip()]) < 6:
            errors.append("第一行必须包含质量词与至少六个英文内容tags")
        if not re.search(r"^[A-Za-z][^.?!]{24,}[.?!](?:\s|$)", prose):
            errors.append("第二行必须包含英文自然语言画面描述")
        if _scene_action_tags(scene) and not _ANIMA_ACTION_RE.search(prompt):
            errors.append("剧情存在明确动作，提示词必须保留具体动作或姿态")
    elif profile == "natural_language":
        if len(prompt) < 20:
            errors.append("自然语言描述过短")
        if _CJK_RE.search(prompt):
            errors.append("自然语言最终提示词必须为纯英文")
        if prompt.startswith("```") or not re.search(r"[。.!?]", prompt):
            errors.append("必须是完整自然语言段落")
    elif profile == "niji_sections":
        lines = prompt.splitlines()
        if len(lines) != 4 or any(not line.strip() for line in lines):
            errors.append("必须包含主体、风格、附加提示词、后缀指令四段")
        elif not lines[3].startswith("--"):
            errors.append("第四段必须只包含后缀指令")
        if _CJK_RE.search(prompt):
            errors.append("Niji 最终提示词必须为纯英文")
    if profile != "krea2":
        errors.extend(_multi_actor_contract_errors(profile, prompt, scene))
    if profile != "krea2":
        errors.extend(_visual_fact_errors(prompt, scene))
    # 第 22 刀 ★1：与 `_visual_fact_errors` 同层、方向相反（管「加」不管「漏」）。
    # 放在 `if profile != "krea2"` 之外＝四档都过同一份闸（krea2 的 `_krea_contract_errors`
    # 只并了「漏」那一半，这里补齐「加」那一半）。
    errors.extend(_unsourced_visual_device_errors(prompt, scene))
    # 第 70 刀：与道具闸**同层同向** —— 身体部位与接触形态的编造同样不得放行。
    errors.extend(_unsourced_contact_errors(prompt, scene))
    return errors


def _body_errors(profile: str, prompt: str) -> list[str]:
    """成稿「本体」违例：只判**结构（行/段）与语言**，不含任何依赖 SceneSpec 的事实/契约缺口。

    用途是优化项⑤ 的补丁门槛（「稿子能不能用」而不是「稿子答不答得全」）。实现上把 scene
    换成空表：`_multi_actor_contract_errors` / `_visual_fact_errors` / `_scene_action_tags`
    的入口都先判来源为空，空 scene 下必然产出空表 ⇒ `_errors` 只剩与 scene 无关的格式判定。
    这样判定口径仍是**单一真源**，不必把正则抄第二份（抄一份就是一份漂移风险）。
    """
    errors, _warnings = _split_length_warnings(_errors(profile, prompt, {}))
    return errors


# ═══════════════════════════════════════════════════════════════════════════════
# 第一人称「取景收束」（2026-09-24 用户定案 · 第 55 刀）
#
# 用户口径（原话）：「按照我之前要求的，**先获取画面内容，再寻找合适视角**，所以说为了
#   全面展示提示词的内容，自然会像第三方视角一样展示所有，然后自然会类似将女主角的全身
#   涵盖在画面，而在这情况下，主人公的动作想实现极有可能暴露全身，也就是说**没有收束
#   第一人称主角视角的画面**。比如说该画面应该**仅局限于上半身**，就是以第一人称视角压在
#   师傅的上半身，肉棒从**镜头下半区域**露出压在师傅嘴上。」
# 用户随后给出修法（原话）：「就是把当前情况的**内容优先、机位是因变量**划分给**第三人称**；
#   如果是**第一人称**，那就是**先内容优先，再判断第一人称的数据视野朝向，最后筛分镜头
#   里面应该保留的东西**。」
#
# ⇒ 三档因果方向（本条＝第 38 刀口径的**人称分支**，不是推翻）：
#   · 第三人称：内容 ⇒ 机位（远到能容纳全部内容）—— **保持第 38 刀原样**。
#   · 第一人称：内容 ⇒ **数据视野朝向**（镜头已固定在叙事者眼睛上、视野是**有限**的）
#     ⇒ **取景筛分**（装不下的内容按层级省略）。**镜头不再是因变量，而是已给定的约束**。
#
# 真机实锤（零 LLM，`agent-trace.jsonl` 三轮同病）：
#   · turn 52（`line 291`）：交付含 `close-up` ＋ `three-quarter back view` ＋ `from behind`，
#     却同时要求「男手捏下巴＋器官贴唇＋女全身侧躺＋抬腿＋白袜」⇒ **物理装不下**。
#   · turn 51（`line 230`）：`close-up` 却写「臀部压进被褥、**床边叠着外袍、门边倚着剑**」
#     ⇒ 全景信息塞进特写。
#   · turn 50（`line 172`）：写「薄被推开半掌、**另一半床空着且冷**」
#     ⇒ 拿叙事者的空位当构图元素 ＝ 上帝视角。
#   ⇒ `close-up` 在这个链路里**形同虚设**：模型一边写特写、一边描述只有退到远处才看得见的
#     东西（床、门、地上的外袍、臀部曲线）。
#
# ⚠ 判据的 `composition` 字段是**死数据**：判据每次都自报了景别（turn 52/51 报「特写」、
#   turn 50/48 报「中景」），但 `scene_spec["composition"]` 只从**主计划** `plan["composition"]`
#   取（`agent_agency.py:1524`），**判据那份从未被消费** ⇒ 本函数不依赖它，改用确定性推导。
# ═══════════════════════════════════════════════════════════════════════════════

# 「数据视野朝向」的确定性数据源：画面事实里**接触点落在哪个高度带**。
# 用户示例「压在师傅的上半身，肉棒从镜头下半区域露出压在师傅嘴上」⇒ 接触点在**口/面**
# ⇒ 视野上限＝**上半身**（不是全身）。
_VIEW_FACE_CONTACT_RE = re.compile(
    # 第 71 刀 ②（2026-09-29 真机 + 探针双实锤）：本表只收**不可与下身复合**的上半身部位写法。
    # 旧式含裸 `口`／`唇`／`面` 三个**子串**，真机把下列词误当面部接触：
    #   `穴口`／`缝口` 的「口」、`阴唇` 的「唇」、`布面`／`褥面` 的「面」
    # ⇒ `face=True` ⇒ 取景上限锁成「上半身」——而那一帧的画面事实全是下半身动作
    # ⇒ 模型为了把器官接触塞进上半身，**把接触点挪到了脸上**
    #   （探针复现：事实「肉棒贴着阴唇碾」写成 `penis on face` / `glans pressing her cheek`）。
    # ⚠ 裸「面」一并去掉（改 `面部`／`面颊`）：`布面`／`褥面` 在真机窗口里是高频词。
    #   代价＝「压在面上」这类写法不再命中 ⇒ 退回「不给上限」（宁缺勿猜），可接受。
    # ⚠ `口`／`唇` **保留但加负向后顾**（首版把裸字整个删掉 ⇒ 真机/既有锁双双回归：
    #   「压在她**唇**上」「朱**唇**饱满」是**真上半身接触**，删掉就丢召回）。
    #   要排除的只是**下身复合词**：穴口／缝口／伤口／创口／裂口／门口／洞口、阴唇。
    r"(?:嘴|嘴角|嘴唇|唇角|口腔|口交|口水|"
    r"(?<!穴)(?<!缝)(?<!伤)(?<!创)(?<!裂)(?<!门)(?<!洞)口|"
    r"(?<!阴)唇|"
    r"舌|脸颊|脸|面部|面容|面颊|"
    r"鼻|下颌|下巴|颈|脖子|锁骨|胸|乳|肩|咽喉|喉)",
)
# 接触点落在**腰部以下** ⇒ 视野下移但不是推远（仍保持近距离）。
# ⚠ 第 71 刀 ②：裸 `股` 改为复合词 —— `一股接一股`／`那股` 会把上半身帧误判成下半身。
_VIEW_LOWER_CONTACT_RE = re.compile(
    # ⚠ **不变式**：`_VIEW_LOWER_STRICT_RE` ⊆ 本表 —— 否则「下身专有词压过面命中」之后
    #   本表却认不出它 ⇒ 取景上限掉成「不给上限」（第 71 刀自测抓到 `缝口` 这个洞）。
    r"(?:下体|私处|阴|穴|臀|屁股|股间|股沟|胯|裆|腿|脚|足|膝|大腿|小腿|腰腹|"
    r"腿根|膝弯|会阴|缝口|龟头|肉棒)",
)
# 第 71 刀 ③：**下身专有词** —— 命中即**压过**上面的面命中。
# 口径：只收「接触点不可能落在上半身」的词（`阴唇`／`穴口`／`缝口`／`胯`／`裆` …），
# **刻意不收** `臀`／`大腿`／`腿` 这类**可与面部接触同现**的部位
# （反例：「她的大腿搭在他肩上」＋「肉棒压在她脸上」两条同真 ⇒ 不该被压成下半身）。
_VIEW_LOWER_STRICT_RE = re.compile(
    r"(?:阴唇|阴部|阴户|阴道|阴蒂|阴阜|穴口|穴内|缝口|会阴|股间|腿根|胯|裆)",
)


def _view_face_frame(blob: str) -> bool:
    """本帧是否「**上半身**近身帧」（第 57 刀 ③ 口径 ＋ 第 71 刀 ③ 例外）。

    **三处消费者共用同一真源**（`_first_person_framing`／`_first_person_frame_filter`／
    `_first_person_upper_frame`）—— 口径分叉就会出现「提示词声明上半身、筛分却按下半身」
    的自相矛盾（第 57 刀 ③ 的注释里记着这条）。
    """
    if not blob.strip():
        return False
    if _VIEW_LOWER_STRICT_RE.search(blob):
        # 下身专有词 ⇒ 压过面命中（第 71 刀 ③）。
        return False
    return bool(_VIEW_FACE_CONTACT_RE.search(blob))

# 第三方全景机位措辞：**仅在「近身接触帧」**下禁用（它们隐含「有一个站在旁边看的观察者」，
# 与「镜头＝叙事者眼睛」自相矛盾）。
# ⚠ 2026-09-24 第 56 刀（用户第二次纠正）：这些措辞**不得在纯风景/环境帧**里禁用 ——
#   用户原话：「不行，**你不能限制死特写中景**，因为我开着第一人称，**有时候如果只是描写
#   风景**，那被限制成近景/中景**基本彰显不了风景全貌**」。
#   ⇒ 风景帧**没有接触锚点** ⇒ 本来就不该进近身收束态。
_VIEWPOINT_FORBIDDEN_CAMERA = (
    "from behind", "three-quarter back view", "rear view", "back view",
    "full body", "wide shot", "long shot", "full shot", "medium wide shot",
    "environmental composition", "god's eye view", "bird's eye view",
    "looking down at face", "from above",
)

# ⚠ 上述禁表里**只有「俯视/背后」这一半**是身份矛盾（上帝视角 ＝ 旁观者，恒禁）；
#   「full body / wide shot / long shot」这一半是**取景矛盾**，只在近身帧成立。
#   ⇒ 风景帧只用「恒禁那一半」（见 `_first_person_framing`）。
_VIEWPOINT_BYSTANDER_ONLY = (
    "from behind", "three-quarter back view", "rear view", "back view",
    "god's eye view", "bird's eye view", "looking down at face", "from above",
    "environmental composition",
)
_VIEWPOINT_WIDE_ONLY = (
    "full body", "wide shot", "long shot", "full shot", "medium wide shot",
)

# 「纯风景 / 环境帧」的确定性判据（第 56 刀）：**没有人体接触**但**有环境陈设词**。
# 用户口径「有时候如果只是描写风景……基本彰显不了风景全貌」——
#   这类帧必须**放行全景**，不得收成近景/中景。
# ⚠ 与 `_VIEW_FACE_CONTACT_RE` / `_VIEW_LOWER_CONTACT_RE` 互斥使用：
#   两者任一命中 ⇒ 是**近身帧** ⇒ 走取景收束；两者都不命中且本表命中 ⇒ 是**风景帧** ⇒ 放行远景。
_VIEW_ENVIRONMENT_RE = re.compile(
    r"(?:山|峰|岭|崖|谷|林|树|竹|松|枫|槐|柳|梅|湖|河|江|溪|池|潭|海|浪|潮|"
    r"云|霞|雾|霜|雪|风|雨|露|月|星|天|日|阳|霞光|晨光|曦|暮|黄昏|夜|"
    r"宫|殿|楼|阁|亭|台|院|廊|桥|街|巷|道|路|阶|墙|瓦|檐|窗|门|灯|烛|"
    r"屋|房|室|堂|案|桌|榻|床|椅|柜|架|屏|帘|帐|书|剑|琴|棋|画|茶|酒|炭盆|香炉|"
    r"远处|远方|山脚|天际|全貌|风景|景致|四下|四周|屋外|院外|窗外|院中)",
)


def _first_person_framing(scene: Mapping[str, object]) -> str:
    """第一人称「数据视野朝向 ＋ 取景筛分」的确定性推导（第 55／56 刀）。

    **三态**（第 56 刀据用户第二次纠正扩为三态，原来只有「近身」与「不设限」两态）：
      · **近身接触帧**（命中 `_VIEW_FACE_CONTACT_RE`／`_VIEW_LOWER_CONTACT_RE`）
        ⇒ 给**取景上限**（上半身 / 下半身）＋ 筛分口径；
      · **纯风景 / 环境帧**（两边接触都是空、但命中 `_VIEW_ENVIRONMENT_RE`）
        ⇒ **明确放行全景**（用户原话：风景被限成近景/中景「彰显不了风景全貌」）
        —— 只禁**身份矛盾那一半**（俯视/背后 ＝ 上帝视角），**不禁** `full body`/`wide shot`；
      · **其他**（无接触、也无环境词）⇒ 返回空串、**不回上限**（宁缺勿猜，
        调用方跳过整个收束块 ⇒ 逐字节等于旧行为）。
    `viewpoint` 非 `first_person` ⇒ 返回空串（零回归锁）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    sources: list[str] = []
    for key in ("narrative", "appearance", "wardrobe", "draft_prompt"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(image_prompt_extract.restore_jailbreak(value))
    blob = "\n".join(sources)
    if not blob.strip():
        return ""
    face = _view_face_frame(blob)
    # 即使两张表哪天漂移，strict 命中**必然**蕴含 lower（第 71 刀不变式的运行期兜底）。
    lower = (bool(_VIEW_LOWER_CONTACT_RE.search(blob))
             or bool(_VIEW_LOWER_STRICT_RE.search(blob)))
    if not face and not lower:
        if _VIEW_ENVIRONMENT_RE.search(blob):
            # ── 第 56 刀：风景帧——**放行全景**（不得限制死特写/中景）────────────────
            bystander = "、".join(f"`{item}`" for item in _VIEWPOINT_BYSTANDER_ONLY)
            return (
                "【视角·第一人称·本帧为环境/风景镜（不设近景上限）】本帧画面事实里"
                "**没有近身人体接触**、只有环境与陈设 ⇒ **不得**把它收成特写/近景："
                "那样**彰显不了风景全貌**（用户定案）。本帧**允许**远景/全景机位"
                "（`wide shot`／`long shot`／`full shot`／`medium wide shot` 均可），"
                "按画面事实把环境与陈设写全。⚠ 唯有一条仍然成立：本帧**仍是从叙事者的"
                "位置看出去**（不是旁边站着的观察者）⇒ **仍禁止**"
                f"{bystander}这类**俯视/背后/第三旁观**措辞——远景与旁观是两回事。"
                + _FP_PROSE_NO_META_CLAUSE
            )
        # ⚠ 两边都没命中 ⇒ **不给取景上限**（宁缺勿猜：没有接触证据就没有收束依据，
        #   此时按常规景别走）。返回空串 ⇒ 调用方跳过整个收束块、**逐字节等于旧行为**。
        return ""
    if face:
        limit, limit_en = "上半身", "upper body (waist-up, bust shot)"
    else:
        limit, limit_en = "下半身", "waist-down shot"
    return (
        f"【视角·第一人称·数据视野朝向（本帧为{limit}）】本帧取景上限＝**{limit}**"
        f"（{limit_en}）：**先保留**剧情拿到的画面事实，再按第一人称的**有限视野**"
        f"筛分——**只有真正看得见的**才写进画面，装不下的内容按层级**省略**"
        f"（可写进区域内的局部；不得为了「展示全部内容」把视角拉开）。"
        f"⚠ 本上限**只适用于近身接触帧**：若本帧只是描写环境/风景而无人体接触，"
        f"不应套用（那种帧要放行全景）。"
        + _FP_PROSE_NO_META_CLAUSE
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 第一人称「画面事实筛分」（2026-09-24 · 第 57 刀 ③）
#
# 用户口径（原话，第二次重申）：「第三人称是**内容产出再找视角展示完整内容**，而第一人称则是
#   **先内容再核对主人公主视角内容进行筛分**。」
#
# 第 55 刀只把「筛分」写成了**一句提示词指令**（`_first_person_framing`：「只有这个视野里
#   真正看得见的才写进画面，装不下的内容按层级省略」）——**靠模型自觉**。
# 真机实锤（turn `9bb03d04`，第一人称 + 上半身近身帧）提示词**自相矛盾**：
#   `upper body, close-up, from above, first-person view`（取景已收）
#   **同时**要求 `broad rounded hips` ＋ `white silk stockings … on her legs`（**装不下**）。
# ⇒ 只靠指令赢不了：`appearance` / `wardrobe` 在字段账本里是 **`required`**
#   （`complete_field_coverage` 必须补齐）⇒ 编译器**不能省略**它们 ⇒ 模型只能把镜头推远。
# ⇒ 本函数把「筛分」**从提示词指令下沉到数据层**：进编译器之前，先按取景上限把
#   **装不下的部位/衣物从 `appearance` / `wardrobe` 里剔掉**，让账本不再强制它们出场。
#
# 设计（全部服从「宁缺勿猜」）：
#   · 只在 **`first_person` ＋ 上半身近身帧**（命中 `_VIEW_FACE_CONTACT_RE` 且未命中
#     下半身接触）时生效 —— 只做**一个方向**（上半身帧剔下半身），反向不猜；
#   · 风景帧（第 56 刀三态）**豁免**：用户定案「风景帧要放行全景」；
#   · 下半身帧 / 其他 / 第三人称 / 无 `viewpoint` ⇒ **原样返回**（零回归锁）；
#   · **剔空保护**：若某字段整条都被剔（说明该字段本就只写下半身），**保留原文**，
#     绝不把字段清空（宁缺勿猜 —— 清空会让编译器彻底编造）。
# ═══════════════════════════════════════════════════════════════════════════════

# 下半身部位／裤袜类（保守：以多字词为主；裸「臀／腿／胯／袜」保留 —— 本表只作用于
# **外貌/服装这类短名词短语**，不是自由叙事，量词陷阱（第 56 刀「一股」）不适用；
# 而真机 `appearance` 正是「宽厚圆硕的臀」「修长白皙的双腿」这种写法，漏掉裸字就整条失效）。
# ⚠ 与 `_TP_LOWER_RE`（scene_illustration，判**接触高度带**）**不同源**：那张表服务
#   「时点截断」，本表服务「取景筛分」，两者判的不是同一件事 ⇒ 刻意各留一份、不互相 import。
_FP_LOWER_BODY_RE = re.compile(
    r"(?:臀|屁股|股间|股沟|胯|裆|"
    r"大腿|小腿|膝弯|膝窝|腿|足尖|脚踝|脚背|脚趾|脚心|脚掌|双足|脚|"
    r"腰腹|小腹|腹部|腹股沟|腰侧|腰肢|腰间|腰|髋|"
    r"白袜|白丝|裤袜|丝袜|长袜|袜)",
)

# 字段内的**片段分隔符**（外貌/服装都是顿号/逗号/分号串起来的中文短语）。
_FP_SEGMENT_SPLIT_RE = re.compile(r"[、，,；;\n]+")


def _strip_lower_body(text: str) -> tuple[str, list[str]]:
    """按**片段**剔掉含下半身部位/裤袜的短语。返回 `(保留文本, 被剔片段)`。

    只按分隔符切、只丢整条命中片段 ⇒ **不改写、不重组**保留下来的文字（逐字不变）。
    被剔片段非空但保留文本为空 ⇒ 放弃本次剔（宁缺勿猜，见模块注释的「剔空保护」）。
    """
    raw = text or ""
    if not raw.strip():
        return raw, []
    segments = _FP_SEGMENT_SPLIT_RE.split(raw)
    kept: list[str] = []
    removed: list[str] = []
    for segment in segments:
        if not segment.strip():
            continue
        if _FP_LOWER_BODY_RE.search(segment):
            removed.append(segment.strip())
        else:
            kept.append(segment)
    if not removed or not kept:
        return raw, []
    return "、".join(kept), removed


def first_person_frame_filter(scene: Mapping[str, object]) -> dict:
    """第一人称「画面事实筛分」：按取景上限剔掉装不下的外观/服装事实（第 57 刀 ③）。

    返回 `{"applied": bool, "scope": str, "appearance": str, "wardrobe": str,
    "removed": [str, ...]}`；`applied=False` 时 `appearance`/`wardrobe` **原样返回调用方的值**
    （调用方据此判断「没动过」⇒ 逐字节等于旧行为）。
    """
    appearance = str(scene.get("appearance") or "")
    wardrobe = str(scene.get("wardrobe") or "")
    # `dict`（不带参数）⇒ 值按 Any 处理，免得为一次内部返回值引入 TypedDict。
    untouched: dict = {
        "applied": False, "scope": "", "removed": [],
        "appearance": appearance, "wardrobe": wardrobe,
    }
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return untouched
    sources: list[str] = []
    for key in ("narrative", "appearance", "wardrobe", "draft_prompt"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(image_prompt_extract.restore_jailbreak(value))
    blob = "\n".join(sources)
    if not blob.strip():
        return untouched
    # 取景上限的判定必须与 `_first_person_framing` **同口径**（同一个 4 键 blob、同一条
    # 「**面优先**」规则）—— 否则会出现「提示词声明上半身、本函数却按下半身不筛」的
    # 自相矛盾。⚠ 曾经踩过：把闸写成「命中下半身就不筛」，而 `appearance` 里本就有
    # 「臀／双腿」⇒ **被筛字段反过来关掉了筛分**（循环）。面优先之后，
    # `appearance` 里的唇/胸与 narrative 里的口/面共同得出「上半身」，与本函数目的自洽。
    face = _view_face_frame(blob)
    if not face:
        # 未命中「面/颈/胸/肩」⇒ 不是上半身近身帧（下半身帧不做反向筛；风景帧/无接触按
        # 第 56 刀三态要放行全景）⇒ 一律不筛（宁缺勿猜）。
        # ⚠ 刻意**不**把 `_VIEW_LOWER_CONTACT_RE` 也纳入本闸：`_first_person_framing` 用的是
        #   「**面优先**」（face 命中即给上半身上限，lower 只在其后兜底）⇒ 本函数跟着只认 face，
        #   才与提示词声明的取景上限恒一致。
        return untouched
    new_appearance, removed_appearance = _strip_lower_body(appearance)
    new_wardrobe, removed_wardrobe = _strip_lower_body(wardrobe)
    if not removed_appearance and not removed_wardrobe:
        return untouched
    return {
        "applied": True, "scope": "upper_body",
        "appearance": new_appearance, "wardrobe": new_wardrobe,
        "removed": [*removed_appearance, *removed_wardrobe],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 第一人称「旁观/取景矛盾机位词」机械闸（2026-09-28 · 第 60 刀）
#
# 真机实锤（slot `illo-req-09f79e6f-…-53` **同一槽位四次重生成**）：
#   `illustration.request` 四次的 `viewpoint_detail` 全是
#   `{viewpoint: first_person, auto: third_person, source: override, conflict: true}`、
#   `frame_subjects=["妲千沁"]` ⇒ **人称判定层健康**（37 刀摘叙事者、`override` 正确压住自动档）；
#   但 `illustration.submitted` 的最终提示词：第 1 次 `first person view, pov,
#   looking down at face`、第 4 次 `from behind, three-quarter back view, … penis against
#   rear, hand on hip, hand on knee` ⇒ `_VIEWPOINT_FORBIDDEN_CAMERA`（上方）**明列**这些词，
#   却**从未被任何代码消费** —— 它只被抄进了 `_viewpoint_clause` 的中文指令。
#   ⇒ 约束停在提示词层 ⇒ 模型不听话时禁词直接提交给 ComfyUI ⇒ 用户实报
#     「手选第一人称，出来的却是**第三人称人物在场**的画面」。
#
# 本刀把这张**已建好却空转**的禁表接成机械闸（纯剔除、宁缺勿猜，与 31/40/46/57 刀同族）。
# ⚠ 三态**复用** `_first_person_framing`（近身帧＝全表／风景帧＝恒禁那一半／
#   无接触无环境＝不动），绝不另写一份判据 —— 否则会出现「提示词声明风景帧、
#   闸却按近身帧剔掉全景词」的自相矛盾。
# ⚠ 修订闸时**只剔整条命中的 tag**，不改写、不重组、不合并（改写＝猜测，违反宁缺勿猜）。
# ═══════════════════════════════════════════════════════════════════════════════


def _first_person_camera_ban(scene: Mapping[str, object]) -> tuple[str, ...]:
    """第一人称本帧**禁用的机位词表**（第 60 刀）；无收束依据／非第一人称 ⇒ 空表（零回归锁）。

    三态判据**直接复用** `_first_person_framing` 的返回值（同一个 4 键 blob、同一条「面优先」）：
      · 空串（第三人称／无接触无环境）⇒ `()` ⇒ 调用方逐字节返回原串；
      · 含「不设近景上限」（风景/环境帧）⇒ 只禁**恒禁那一半**
        （`_VIEWPOINT_BYSTANDER_ONLY`：俯视/背后/第三旁观＝身份矛盾，风景帧也成立）；
      · 其余＝近身接触帧 ⇒ 禁**全表** `_VIEWPOINT_FORBIDDEN_CAMERA`
        （旁观 ＋ `full body`/`wide shot` 这一半取景矛盾）。
    """
    framing = _first_person_framing(scene)
    if not framing:
        return ()
    if "不设近景上限" in framing:
        return _VIEWPOINT_BYSTANDER_ONLY
    return _VIEWPOINT_FORBIDDEN_CAMERA


def _strip_camera_tags(tag_line: str, banned: tuple[str, ...]) -> str:
    """从首行 tag 串里剔掉命中 `banned` 的整条 tag（纯剔除，不改写）。

    剔空保护（与 57 刀 `_strip_lower_body` 同族）：整行都是禁词 ⇒ 返回原串，
    避免清空首行后下游质量行与内容 tag 全数丢失。
    """
    if not banned:
        return tag_line
    tags = [tag.strip() for tag in (tag_line or "").split(",")]
    kept: list[str] = []
    dropped = 0
    for tag in tags:
        if not tag:
            continue
        if any(re.search(rf"\b{re.escape(item)}\b", tag, re.I) for item in banned):
            dropped += 1
            continue
        kept.append(tag)
    if not dropped or not kept:
        return tag_line
    return ", ".join(kept)


def _strip_bystander_camera_tags(tag_line: str, scene: Mapping[str, object]) -> str:
    """第一人称下从**首行 tag 串**里剔掉旁观/取景矛盾机位词（第 60 刀）。

    ⚠ 零回归锁（任一成立即**逐字节返回原串**、且不改动空格的既有形态）：
      ① `viewpoint` 非 `first_person`（`_first_person_camera_ban` 回空表）；
      ② 本帧无收束依据（既无近身接触、也无环境词）；
      ③ 行内无命中项。
    ⚠ 剔空保护（与 57 刀 `_strip_lower_body` 同族）：整行都是禁词 ⇒ **放弃本次剔**、
      返回原串 —— 清空首行会让下游质量行与内容 tag 全数丢失（宁缺勿猜）。
    ⚠ 只作用于**首行 tags**：第二行是自由散文，机械剔词必然误伤，刻意不碰。
    """
    return _strip_camera_tags(tag_line, _first_person_camera_ban(scene))


# 第 73 刀（2026-09-29 探针 [38] / 真机 09-29 批次）：第一人称首行 tags 的
# **指向镜头的方位短语**去机位化。
#
# 根因：`_first_person_framing` 的「禁用机位」条款一边禁 `from behind`／`rear view`，
#   **一边亲手给出** `her back arching toward the camera` 作范例——而同一段提示词里的
#   `_FP_PROSE_NO_META_CLAUSE` 又明禁散文行出现 `camera`／`viewer` ⇒ 指令自相矛盾。
#   后果：真机 slot `-54-5` 首行 `back turned toward the camera, back facing viewer`、
#   探针 [38] 首行 `back facing the camera`。第 60 刀是**整条字面禁表**，
#   扫不到「身体部位 ＋ 机位方位短语」这种组合（`back view` 有边界词，`back faced` 没有）。
#
# 口径：**只削方位短语、部位与姿势逐字保留**（`back turned toward the camera` →
#   `back turned`），不发明新词、不改姿势语义 ⇒ 与第 60 刀「不得把整个镜头搬到她背后」互补。
#   与 `_strip_bystander_camera_tags` 分工：那条**剔整条**禁词 tag，本函数**只削短语**。
_FP_TAG_CAMERA_DIRECTION_RE = re.compile(
    r"\s+(?:toward|towards|to|at|into)\s+the\s+camera\b", re.I,
)


def _first_person_strip_camera_direction_tags(
    tag_line: str, scene: Mapping[str, object],
) -> str:
    """第一人称下削掉**首行 tags 里指向镜头的方位短语**（第 73 刀）。

    ⚠ 零回归锁（任一成立即**逐字节返回原串**）：① `viewpoint` 非 `first_person`；
      ② 行内无命中；③ 命中 tag 削完**不足两个词** ⇒ 放弃该条（防悬空动词：
      `looking at the camera` → `looking`）。
    ⚠ 只作用于**首行 tags**：第二行是自由散文，由 `_first_person_prose_meta_guard`
      （第 68 刀）另行收口。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return tag_line
    segments = (tag_line or "").split(",")
    changed = False
    kept: list[str] = []
    for segment in segments:
        body = segment.strip()
        if not body or not _FP_TAG_CAMERA_DIRECTION_RE.search(body):
            kept.append(segment)
            continue
        candidate = re.sub(r"\s{2,}", " ", _FP_TAG_CAMERA_DIRECTION_RE.sub("", body))
        candidate = candidate.strip(" ,;")
        if len(candidate.split()) < 2:
            kept.append(segment)          # 防悬空动词
            continue
        changed = True
        indent = segment[: len(segment) - len(segment.lstrip())]
        kept.append(indent + candidate)
    if not changed:
        return tag_line
    return ",".join(kept)


# ═══════════════════════════════════════════════════════════════════════════════
# 第一人称「POV 正向声明」机械补（2026-09-28 · 第 61 刀 · 用户定案「机械补」）
#
# 真机背景（与第 60 刀同一批取证）：同一 slot 四次重生成里 `first person view`／`pov`
# **只在第 1 次出现**（profile LLM 自觉写的），第 2／3／4 次**全缺** ⇒ 生图模型按默认
# **第三人称**构图、把叙事者的躯体当第三方画进画面。全仓 grep 证明该词形**没有任何注入点**
# （只出现在注释里）。
#
# ⚠ 与红线 23（「不机械拼硬标签」）的关系：红线 23 管的是**角色性别**那类「注入事实、
#   让模型自己措辞」的场景；本刀补的是**镜头身份声明** —— 它不是事实、是**视角口径**，
#   与 `_viewpoint_clause` 的中文条款同源 ⇒ 用户已就本项单独拍板，不构成对红线 23 的推翻。
# ⚠ 只**追加**、不重排、不删除 ⇒ 下游所有按位置的归一（人数词／孤儿计数词／第 60 刀闸）
#   都不受影响；已有等价声明时逐字节返回原串（**幂等**）。
# ═══════════════════════════════════════════════════════════════════════════════

# ⚠ 识别面**刻意保守**（第 63 刀）：只认**无歧义的第一人称**写法。
#   `from her/his/the man's point of view` 这类**不认** —— 它同样可能是第三人称下
#   「从某角色视角拍」的描述；认错 ⇒ **不补** ⇒ 缺陷反向复发。
#   **方向原则：宁可重复追加（冗余、无害），不可误认为已有（漏补、致命）。**
_FP_VIEW_TAG_RE = re.compile(
    r"\b(?:first[-\s]?person\s+(?:view|pov|perspective)"
    r"|from\s+my\s+point\s+of\s+view"
    r"|male\s+viewer\s+pov|viewer\s+pov|pov)\b",
    re.I,
)
_FP_VIEW_TAGS = "first person view, pov"
# 风景帧／无收束依据帧只补这一条（见 `_ensure_first_person_view_tag` 的三态说明）。
_FP_VIEW_TAG_MIN = "first person view"


def _first_person_frame_kind(scene: Mapping[str, object]) -> str:
    """第一人称本帧的**取景三态**（第 63 刀统一收口）：`"near"`／`"scenery"`／`""`。

    ⚠ 与 `_first_person_camera_ban` **同源**（都从 `_first_person_framing` 派生：同一个返回串、
      同一条「不含『不设近景上限』即近身帧」的判据）—— 下游不得各自重判。
    ⚠ `viewpoint` 非 `first_person` ⇒ `""`（零回归锁）。
    """
    framing = _first_person_framing(scene)
    if not framing:
        return ""
    return "scenery" if "不设近景上限" in framing else "near"


def _ensure_first_person_view_tag(tag_line: str, scene: Mapping[str, object]) -> str:
    """第一人称下确保首行含 **POV 正向声明**（第 61 刀；第 63 刀按三态分级）。

    ⚠ 零回归锁：`viewpoint` 非 `first_person` ⇒ **逐字节返回原串**；
      行内已有等价声明（见 `_FP_VIEW_TAG_RE`）⇒ 同样逐字节返回（**幂等**，不重复追加）。
    ⚠ 空串／纯空白 ⇒ 原样返回（宁缺勿猜：没有位置语义，不补一条孤零零的 tag）。

    第 63 刀（三态分级）—— `pov` 在 Danbooru 里隐含「**视角人物的部分身体可见**」：
      · **近身接触帧**（`kind == "near"`）⇒ `first person view, pov`；
      · **风景帧／无收束依据帧** ⇒ 只补 `first person view`。理由是风景帧的既有定案
        （红线 76）是「**放行全景**、彰显风景全貌」——声明 `pov` 与该帧意图打架
        （可能诱导模型在风景里画进一只手）；而无收束依据帧**没有**「有局部入画」的正向证据
        ⇒ 不声明（宁缺勿猜）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return tag_line
    if not (tag_line or "").strip():
        return tag_line
    if _FP_VIEW_TAG_RE.search(tag_line):
        return tag_line
    tags = _FP_VIEW_TAGS if _first_person_frame_kind(scene) == "near" else _FP_VIEW_TAG_MIN
    return f"{tag_line.rstrip(' ,')}, {tags}"


# ═══════════════════════════════════════════════════════════════════════════════
# 第一人称「身高差 ⇒ 低机位仰拍」（2026-10-23 · 用户定案）
#
# 用户口径（原话）：「她近两米、比男主高一头，低机位能同时解决身高差和第一人称」。
# 第一人称的镜头固定在叙事者眼睛上 ⇒ 镜头高度本来就是叙事者眼高；当画面事实明确
# 写了对方**比叙事者更高**时，正确表现是**轻微仰拍**（slight low angle / looking up），
# 而不是把镜头抬到对方眼高（抹平身高差）或俯视。
#
# ⚠ 2026-10-23 用户纠正：「不是说 100% 高身高就要仰视的，比如说主角被对方抱起来不就是
#   平视了吗……身高只能说是一个参考情况，而不是说一定是这个情况」⇒ 身高差**只是参考**：
#   · 先取内容（含动作与相对位置），再选**最能呈现内容**的机位；
#   · 第一人称只画叙事者在该位置**真正看得见**的东西；
#   · 画面事实出现**改变相对高度**的动作/姿态（抱起、坐腿上、躺卧、俯身、跪蹲……）⇒
#     **不机械套仰拍、也不剔高机位**（此时平视甚至俯视才对），改由条款按实际相对位置取景。
# ⚠ 数据驱动、宁缺勿猜：只认**明确的身高差证据**（「比 {{user}} 高」「高出一个头」
#   「a head taller than the user」这类），不按「身高近两米」单独猜——叙事者也可能同样高。
# ⚠ 只作用于**非风景帧**：风景帧的既有定案是放行全景，不强行套人物仰拍。
# ⚠ 零回归锁：第三人称／无证据／风景帧／有相对高度变化 ⇒ 逐字节返回原串。
# ═══════════════════════════════════════════════════════════════════════════════

_FP_TALLER_THAN_NARRATOR_RE = re.compile(
    r"比\s*(?:\{\{\s*user\s*\}\}|user|你|我|男主(?:角|人公)?|主人公|主角|玩家|叙事者)"
    r"\s*(?:还|仍|要)?\s*高"
    r"|高(?:出|过)\s*(?:\{\{\s*user\s*\}\}|user|你|我|男主(?:角|人公)?|主人公|主角|玩家|叙事者)?"
    r"\s*(?:一个头|一头|半个头|半头|一截)"
    r"|\b(?:a\s+head|one\s+head|half\s+a\s+head)\s+taller\s+than\s+"
    r"(?:me|you|the\s+(?:user|protagonist|narrator|player))\b"
    r"|\btaller\s+than\s+(?:me|you|the\s+(?:user|protagonist|narrator|player))\b",
    re.I,
)
_FP_LOW_ANGLE_TAG = "slight low angle, looking up"
_FP_LOW_ANGLE_RE = re.compile(
    r"\b(?:slight\s+)?(?:low[-\s]?angle|upward(?:\s+camera)?\s+angle|"
    r"from\s+below|looking\s+up|worm'?s[-\s]?eye)\b",
    re.I,
)
# 与「仰拍」直接冲突的高机位词：只在身高差成立且相对高度未变时剔（不可动普通 `looking down` 这类动作）。
_FP_HIGH_ANGLE_ONLY = (
    "from above", "looking down at face", "bird's eye view", "god's eye view", "high angle",
)
# 「改变相对高度」的画面事实（2026-10-23 用户纠正）：出现这些动作/姿态时，双方当下的
# 实际相对高度可能与「身高差」相反或相等（被对方抱起＝平视甚至俯视；对方俯身/跪蹲＝平视），
# ⇒ 身高差退为**参考**：不机械补仰拍，也不剔高机位，机位按实际相对位置选最佳呈现。
_FP_RELATIVE_LEVEL_RE = re.compile(
    # 中文：抱起/扛起/打横/公主抱；⚠「抱起双臂」不是位移，用负向断言挡掉。
    r"抱(?:起|起来)(?!(?:双)?(?:臂|手|胳膊))"
    r"|打横|横抱|公主抱|扛(?:在|到)?(?:肩上|肩头)|扛起"
    r"|(?:把|将|被)[^。；;，,\n]{0,10}(?:抱起|扛起|举起|托起|捧起)"
    r"|(?:抱|搂|拥)(?:在|进|入)(?:怀里|怀中)"
    r"|(?:坐|跨坐|骑)(?:在|到|上|回)"
    r"|(?:躺|卧|趴|伏)(?:在|于|到|下)|仰卧|仰面|俯卧|侧卧"
    r"|(?:跪|蹲)(?:在|下|着|到)|俯身|弯腰|弓腰|俯下|屈膝|半跪|单膝(?:跪|着)"
    r"|(?:压|按)在[^。；;，,\n]{0,10}(?:身下|床上|榻上|地上)"
    # 英文
    r"|\b(?:carried|lifted|picked\s+up|hoisted)\b[^.;,\n]{0,30}\b(?:in|on)\s+"
    r"(?:his|her|their)\s+(?:arms|lap)\b"
    r"|\bon\s+(?:his|her|their)\s+lap\b|\bstraddl\w*"
    r"|\b(?:lying|laid)\s+(?:on|in|across)\b"
    r"|\bkneel\w*|\bcrouch\w*|\bbent\s+over\b|\bbend(?:s|ing)?\s+(?:over|down)\b"
    r"|\blean(?:s|ing)?\s+(?:in|down|over|closer)\b"
    r"|\b(?:pinned|pressed)\s+(?:under|beneath|down)\b"
    r"|\b(?:sitting|sat|seated)\s+(?:on|in)\b",
    re.I,
)


def _first_person_match(
    scene: Mapping[str, object], pattern: re.Pattern[str],
) -> str:
    """第一人称下在画面事实里搜一条匹配（非第一人称 ⇒ 空串）。"""
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    for key in ("appearance", "wardrobe", "narrative"):
        value = scene.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        match = pattern.search(image_prompt_extract.restore_jailbreak(value))
        if match:
            return match.group(0)
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        for item in subjects:
            if not isinstance(item, Mapping):
                continue
            match = pattern.search(str(item.get("description") or ""))
            if match:
                return match.group(0)
    return ""


def _first_person_height_evidence(scene: Mapping[str, object]) -> str:
    """本帧有没有「对方比叙事者高」的**明确**证据（第一人称限定）。"""
    return _first_person_match(scene, _FP_TALLER_THAN_NARRATOR_RE)


def _first_person_relative_level(scene: Mapping[str, object]) -> str:
    """本帧有没有**改变相对高度**的动作/姿态（抱起、坐腿上、躺卧、俯身、跪蹲……）。

    命中 ⇒ 身高差只是参考，实际机位应由画面事实决定（可能平视甚至俯视）。
    """
    return _first_person_match(scene, _FP_RELATIVE_LEVEL_RE)


def _first_person_height_angle(scene: Mapping[str, object]) -> bool:
    """是否应给本帧补「叙事者眼高 ＋ 轻微仰拍」（风景帧／相对高度已变 ⇒ 不套）。"""
    if not _first_person_height_evidence(scene):
        return False
    if _first_person_relative_level(scene):
        return False
    return _first_person_frame_kind(scene) != "scenery"


def _ensure_first_person_height_angle(
    head: str, scene: Mapping[str, object],
) -> str:
    """身高差成立且**相对高度未变**时，确保首行含**低机位仰拍** tag，并剔掉冲突的高机位词。

    ⚠ 零回归锁：第三人称／无证据／风景帧／**相对高度已变**／空串 ⇒ 逐字节返回原串。
    ⚠ 幂等：已有 `low angle`／`looking up` 等写法 ⇒ 不重复追加。
    ⚠ 这里**允许**在「整行都是高机位词」时剔空后只留仰拍 tag：身高差已由画面事实确证，
      继续保留俯视会把「她比叙事者高」直接画反（与第 60 刀的剔空保护场景不同）。
    ⚠ 相对高度已变（抱起/坐腿上/躺卧/俯身/跪蹲）⇒ 高机位可能才是 POV 真实所见 ⇒ 不剔。
    """
    if not _first_person_height_angle(scene):
        return head
    raw = head or ""
    if not raw.strip():
        return head
    tags = [tag.strip() for tag in raw.split(",") if tag.strip()]
    kept = [
        tag for tag in tags
        if not any(re.search(rf"\b{re.escape(item)}\b", tag, re.I)
                   for item in _FP_HIGH_ANGLE_ONLY)
    ]
    changed = len(kept) != len(tags)
    if any(_FP_LOW_ANGLE_RE.search(tag) for tag in kept):
        return ", ".join(kept) if changed else head
    kept.append(_FP_LOW_ANGLE_TAG)
    return ", ".join(kept)


def _first_person_height_clause(scene: Mapping[str, object], who: str) -> str:
    """身高差条款（无证据／风景帧 ⇒ 空串）。

    2026-10-23 用户纠正：身高差**只是参考**。画面事实出现改变相对高度的动作/姿态时，
    不写「仰拍」硬规则，改写成「按实际相对位置选最佳机位、只画 POV 看得见的」。
    """
    if not _first_person_height_evidence(scene):
        return ""
    if _first_person_frame_kind(scene) == "scenery":
        return ""
    cue = _first_person_relative_level(scene)
    if cue:
        return (
            f"【视角·第一人称·相对位置优先（身高差只是参考）】画面事实写了对方比{who}更高，"
            f"但本帧还有**改变相对高度**的动作/姿态（「{cue[:40]}」）⇒ **不得**机械套用仰拍，"
            f"也**不得**为了保住身高差而改写动作。**先按画面事实确定双方当下的实际相对高度，"
            f"再选最能呈现画面内容的机位**：平视、俯视、仰拍都可能成立；"
            f"只画{who}在这个位置**真正看得见**的内容。"
        )
    return (
        f"【视角·第一人称·身高差与仰拍】画面事实明确写了对方**比{who}更高**"
        f"（如「高出一个头」这类**明确的身高差**），且本帧**没有改变相对高度**的动作/姿态"
        f"（抱起、坐腿上、躺卧、俯身、跪蹲等）⇒ 此时保持**{who}的眼高**、"
        f"取**轻微仰拍**（`slight low angle`／`looking up`），让对方在画面中显得高于{who}；"
        f"**不得**用俯视、高机位或把镜头抬到对方眼高来抹平身高差。"
        f"⚠ 身高差只是**参考**：一旦画面出现上述改变相对高度的动作，本条不适用，"
        f"机位按实际相对位置取景。仰拍只改变机位高度，**不得**因此新增任何画面事实；"
        f"第一人称只画{who}在该位置**真正看得见**的内容。"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 第一人称收口扩到 krea2／niji_sections（2026-09-28 · 第 64 刀）
#
# 第 63 刀实测登记：第 60／61／63 刀的机制**只在 `_normalize` 的 `anima_tags` 分支接线**
# ⇒ 换 profile 出图等于回到缺陷态。本刀**按输出结构决定落点**（不硬套 anima 的做法）：
#   · `krea2` —— 两段式（首行 tags ＋ 散文），与 anima **同构** ⇒ 首行照 anima 收口；
#   · `niji_sections` —— 四段，**只动 `subject` 段**：POV 声明与机位词都属内容语义；
#     `style`／`additions` 是画风与附加词（塞 POV 会污染风格语义）、`suffix` 是 niji 参数串
#     （动了会写坏参数语法）⇒ 一律不动；
#   · `natural_language` —— **单段散文**：机械剔词会伤语法/语义，追加声明句又会违反红线 28
#     「交付文本只写画面」（不得出现机制自述）⇒ **刻意不动**，只登记为已知边界。
# ═══════════════════════════════════════════════════════════════════════════════


# ═══ 第 65 刀：叙事者部位事实的「呈现形态」（追加式）═══════════════════════════
# 用户定性（原话）：「第一人称就是在内容的基础上**用第一人称去进行取舍或修改**使其符合
#   第一人称情况下的特色」。第 62 刀＝「取舍」；本刀＝「修改」的**机械保证**。
# ⚠ 只**追加**、不改写 —— 理由见 `_first_person_limb_entry_phrasing` 的 docstring
#   （改写会新增账本假红面 ＋ 打红 43／47 刀 12 处锁）。
_FP_LIMB_ENTRY_TAG = "entering from the lower frame edge"
_FP_LIMB_ENTRY_MARK = "lower frame edge"


def _first_person_limb_entry_phrasing(head: str, scene: Mapping[str, object]) -> str:
    """第一人称下给「叙事者部位事实」补一条**从画面下缘进入**的呈现限定（第 65 刀 · **追加式**）。

    用户定性：「第一人称就是在内容的基础上**用第一人称去进行取舍或修改**使其符合第一人称
    情况下的特色」。第 62 刀做了「取舍」；本刀补「修改」的**机械保证**（不再只靠指令自觉）。

    ⚠⚠ **刻意不做「改写」，只做「追加」**（复核后定案）：
      · **改写**（插进原有短语中间）会让字段账本的 `expected` 与成稿形态**不一致** ——
        模型写短形态就判不 `covered` ⇒ **新增假红面**（红线 72 的「永不消失的假红」）；
        且会打红 43／47 刀 **12 处**特征锁（那些锁断言的正是这些精确串）。
      · **追加**（原串**保留**并补一条限定）⇒ 原串**仍是成稿的连续子串** ⇒ 账本判据零影响、
        既有锁全绿，而画面语义完全达成（`entering from the lower frame edge` 就是
        `_viewpoint_clause` 既有的「局部从画面下缘进入」措辞）。

    ⚠ 判据＝**精确匹配 `_NARRATOR_LIMB_FACT_TEXTS`**（43／47 刀的固定产出集）⇒ 零误伤；
    ⚠ 幂等：已有下缘进入字样 ⇒ 逐字节返回原串；非第一人称／无部位事实 ⇒ 逐字节返回原串。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return head
    raw = head or ""
    if not raw.strip():
        return head
    tags = [tag.strip() for tag in raw.split(",")]
    if not any(tag in _NARRATOR_LIMB_FACT_TEXTS for tag in tags):
        return head
    if any(_FP_LIMB_ENTRY_MARK in tag.lower() for tag in tags):
        return head
    kept = [tag for tag in tags if tag]
    kept.append(_FP_LIMB_ENTRY_TAG)
    return ", ".join(kept)


# ═══════════════════════════════════════════════════════════════════════════════
# 第 67 刀（2026-09-29 用户真机审查）：**JSON 残留机械闸**
#
# 真机实锤（turn `d3d32fda` · 槽 `illo-req-…-54-1`）：模型／repair 轮返回**被截断的半 JSON**，
# `_json_object` 与 `_labelled_fields_object` **双双失败** ⇒ 掉进 `_normalize` 的**非 JSON 分支**。
# 该分支 `explicit_tags = ", ".join((lines[0], body))` **无条件吞并**原文各行 ⇒
#   · 首行混进 `{, … "supporting_elements": [, "Her flattened hand on the mattress, ], …`（残片当 tag）；
#   · 第二行变成 `content": "1girl, solo, … anime style.`（**JSON 的 key 标签**进了散文行）；
#   · 而散文判据 `^[A-Za-z][^.?!]{24,}[.?!]` 被 `content": "…` **意外满足**（`c` 是字母、
#     中间无句点、末尾有句点）⇒ **一条格式红都不报** ⇒ `_patch_failed_prompt` 补上通用构图
#     词表后**交付 976 字半 JSON 残留**，而**真正的第二行内容描述整段丢失**。
# 用户口径（原话）：「正常来说这里上半段应该还是 tag」「槽 1 的下一段的内容描述文段还丢失了」。
#
# ⇒ 口径：**结构化残片一律不得当提示词交付**。命中即返回空串 ⇒ 调用方照旧判死并走 repair
#   （与「空稿／拒答」同一条兜底通道），**不新增任何交付路径**。
# ⚠ 只查**组装后的成稿**、不查 raw：raw 若是**合法 JSON**，花括号与引号本就是协议的一部分
#   （那种情况早已被 `_json_object` 解析成 `value`）⇒ 组装结果里不带符号 ⇒ 零误报。
_JSON_RESIDUE_SCHEMA_RE = re.compile(
    r"(?:visual_hook|primary_focus|supporting_elements|content)\s*[\"']?\s*:\s*[\"'\[{]",
    re.I,
)
# 引号**紧邻结构标点**才算残留（`the "crimson eye"` 这种正常引号不算）。
_JSON_RESIDUE_NOISE_RE = re.compile(r"[\"']\s*[,:\]\[]|[,:\[\{]\s*[\"']")


def _json_residue_hits(text: str) -> list[str]:
    """成稿里的 **JSON 残留**特征（空列表＝干净）。第 67 刀，只服务「判死」这一个用途。"""
    body = text or ""
    hits: list[str] = []
    if "{" in body or "}" in body:
        hits.append("花括号")
    match = _JSON_RESIDUE_SCHEMA_RE.search(body)
    if match:
        hits.append(f"规格字段裸标签 {match.group(0).strip()[:40]}")
    if _JSON_RESIDUE_NOISE_RE.search(body):
        hits.append("引号紧邻结构标点")
    return hits


# ═══════════════════════════════════════════════════════════════════════════════
# 第 68 刀（2026-09-29 用户真机审查）：**散文行元语言机械剔**
#
# 用户口径（原话）：「内容描述不是说要明说**镜头即叙述者的双眼**、所以叙述者从不出现在画面里，
# **而是你的内容描述能透露出这一点**，因为**人家模型哪里知道叙述者是谁**，真要说你上面的
# 第一人称视角描述就够用了」。
# ⇒ `narrator`／`camera` 是 **Agent 侧概念**，ComfyUI 侧读不懂；第一人称由**首行 tag** 声明
#   （第 61 刀 `_ensure_first_person_view_tag` 已保证），散文行只写**画面里看得见的东西**。
# 根因（第 55／56 刀指令原文）：中文指令写着「镜头**已经固定**在**叙事者的眼睛位置上**」⇒
#   模型把它**照抄**成英文散文（turn `d3d32fda` 7 份成稿里 **4 份**中招）。
# ⚠ 零回归锁：`viewpoint` 非第一人称 ⇒ **逐字节返回原串**；无命中 ⇒ 同样逐字节返回。
# 第 68 刀：这段**逐字进中文指令**（近身帧与风景帧共用），模型照抄元语言正是本次真机缺陷的根因。
# 口径＝**只禁止，不引导造句**（红线 28 ②：不新增模板引导句），且把「叙事者入画」正确形态
# 说清为「**具体部位在画面里的位置**」——与第 43／65 刀 `_narrator_limb_facts` 同源。
_FP_PROSE_NO_META_CLAUSE = (
    "⚠ **散文行（第二行）不得出现 `camera`／`narrator`／`viewer`／`point of view` "
    "这类元语言**——生图模型不认识「叙事者」是谁，第一人称由首行 tag 的 "
    "`first person view` 声明即可。叙事者入画时只写**具体部位在画面里的位置**"
    "（如 `a hand prying her leg open`／`a single hand entering from the lower frame edge`），"
    "**不得**写「镜头即叙述者的双眼」「叙述者不出现在画面里」这类解释句。"
)


_FP_PROSE_META_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    # ① 「镜头＝叙述者双眼 ＋ 叙述者不入画」整块前缀 → 只保留「谁可见」那半句
    (re.compile(
        r"the\s+camera\s+is\s+the\s+narrator'?s\s+eyes\s*,?\s*"
        r"(?:so\s+)?the\s+narrator\s+never\s+appears\s+in\s+frame\s*;?\s*",
        re.I), ""),
    # ①b 兜底：「镜头＝叙述者双眼」**裸前缀**（真机 slot `-54-4`：「…, looking down at
    #     the woman's sleeping face … from a first-person perspective.」——前半句
    #     没有 `so the narrator never appears…`，① 匹配不到）。必须先于 ④ 执行。
    (re.compile(r"the\s+camera\s+is\s+the\s+narrator'?s\s+eyes\s*,?\s*", re.I), ""),
    # ② 「镜头固定在叙述者眼高／位置」从句
    (re.compile(
        r"the\s+camera\s+(?:holds?|sits?|stays?|rests?)\s+(?:at|to)?\s*the\s+narrator'?s\s+"
        r"(?:eye\s+level|eyes|position)\s*,?\s*", re.I), ""),
    (re.compile(
        r"the\s+camera\s+holds?\s+the\s+narrator'?s\s+position\s+", re.I), ""),
    (re.compile(
        r"the\s+camera\s+(?:is\s+)?(?:fixed|locked)\s+(?:at|on|to)\s+[^,.;]*[,;]?\s*",
        re.I), ""),
    # ③ 指向镜头的方位短语
    (re.compile(r"\s+(?:toward|towards|into|to|at)\s+the\s+camera", re.I), ""),
    # ④ 「从第一人称视角」这类自指视角短语（首行 tag 已声明，散文行不必复述）
    (re.compile(
        r"\s+from\s+a\s+(?:first[-\s]?person|pov|the\s+narrator'?s)\s+"
        r"(?:perspective|view|angle|eyes)", re.I), ""),
    (re.compile(r"\s+in\s+the\s+first[-\s]?person\s+(?:perspective|view)", re.I), ""),
    # ⑤ 「叙述者身体只作为局部可见」→ 只留局部事实本身
    (re.compile(
        r"the\s+narrator'?s\s+(?:body|arm|hand|leg)s?\s+is\s+only\s+visible\s+as\s+",
        re.I), "only "),
    (re.compile(r"the\s+narrator\s+never\s+appears\s+in\s+frame\s*;?\s*", re.I), ""),
    # ⑥ 兜底：任何残留的「叙述者的 X」→ 去主语化成「a X」。真机 slot `-54-3`：
    #     `while the narrator's lower body enters only as a cropped segment from the
    #     bottom edge` —— 语义是对的（局部入画），但 `narrator` 一词必须去掉。
    #     ⚠ 必须排在 ①②⑤ 之后：那几条要连同整个从句一起删，不能被本条的 "a X" 抢走。
    (re.compile(r"the\s+narrator'?s\s+", re.I), "a "),
    (re.compile(r"the\s+narrator\s+", re.I), ""),
)


def _first_person_prose_meta_guard(prose: str, scene: Mapping[str, object]) -> str:
    """第一人称下把**散文行里的元语言镜头句**机械剔掉（第 68 刀）。

    ⚠ 只动**指向镜头/叙述者的元语言**，**不动任何画面事实**：命中短语被删后重排标点与大小写，
    句子其余部分逐字保留。删完若不再像「英文画面句」，下游既有判据会照旧判死 ⇒ 走 repair，
    不会交付残句（fail-closed，不新增交付路径）。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return prose
    body = prose or ""
    if not body.strip():
        return prose
    guarded = body
    for pattern, replacement in _FP_PROSE_META_REWRITES:
        guarded = pattern.sub(replacement, guarded)
    if guarded == body:
        return prose          # 零命中 ⇒ 逐字节返回（幂等锁）
    # 重排：压空白 → 修标点 → 修大小写
    guarded = re.sub(r"\s{2,}", " ", guarded)
    guarded = re.sub(r"\s+([,;.])", r"", guarded)
    guarded = re.sub(r"[,;]\s*([,;.])", r"", guarded)
    guarded = re.sub(r"([,;])\s*$", "", guarded).strip()
    guarded = re.sub(r"^[,;.\s]+", "", guarded).strip()
    if guarded:
        guarded = guarded[0].upper() + guarded[1:]
    if guarded and guarded[-1] not in ".!?":
        guarded += "."
    if not guarded.strip():
        return prose          # 全被剔空 ⇒ 交回原串（下游判据接手，不做二次发明）
    return guarded


def _first_person_head_guards(head: str, scene: Mapping[str, object]) -> str:
    """第一人称「首行 tags」统一收口：**剔旁观机位词 → 补下缘进入限定 → 补身高差仰拍
    → 补 POV 声明**（第 60／61／63／65 刀 ＋ 2026-10-23 身高差仰拍）。

    ⚠ 与 `_normalize` 的 anima 分支**同序同语义**（那几行因接线锁必须显式留在 `_normalize` 里
      —— 见 `test_第60刀_闸已接进anima归一化收口`／`test_第61刀_接线锁_闸与补都接在归一化收口`）。
    ⚠ 每一步各自带零回归锁与非第一人称短路 ⇒ 本函数对第三人称**逐字节返回原串**。
    """
    head = _first_person_strip_camera_direction_tags(head, scene)
    head = _strip_bystander_camera_tags(head, scene)
    head = _first_person_limb_entry_phrasing(head, scene)
    head = _ensure_first_person_height_angle(head, scene)
    return _ensure_first_person_view_tag(head, scene)


def _first_person_krea_guards(prompt: str, scene: Mapping[str, object]) -> str:
    """krea2 的两段式也走第一人称收口（第 64 刀）：**只动首行**，散文行逐字节不动。"""
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return prompt
    lines = prompt.splitlines()
    if not lines:
        return prompt
    return "\n".join([_first_person_head_guards(lines[0], scene), *lines[1:]])


def _first_person_niji_guards(sections: list[str], scene: Mapping[str, object]) -> list[str]:
    """niji 四段的第一人称收口（第 64 刀）：**只动 `subject` 段**，其余段逐字节不动。

    ⚠ `subject` 段本身以 `--` 开头时（畸形输入）同样不动 —— 防写坏参数语法。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return sections
    if not sections or sections[0].lstrip().startswith("--"):
        return sections
    return [_first_person_head_guards(sections[0], scene), *sections[1:]]


# 第 60～65 刀已把 POV 保证与禁词闸覆盖到 `anima_tags`／`krea2`／`niji_sections`
# （按各自输出结构落点）。下面这张表是**已知未覆盖**的 profile —— **白名单式**：
# 只对它告警，不给空值/未知 profile 制造噪音。
# ⚠ 将来新增 profile 时必须回来评估：做了机械层就不必登记，没做就在这里加一项。
_FP_UNCOVERED_PROFILES = frozenset({"natural_language"})


def viewpoint_coverage_warning(profile: str, scene: Mapping[str, object]) -> str:
    """第一人称下**该 profile 有没有 POV 机械保证**的诊断告警（第 66 刀 · 防呆登记）。

    ⚠ 只**返回诊断串**、**不改任何输出**：非第一人称／不在未覆盖表里 ⇒ 空串（零回归锁）。
    ⚠ 语义是「**防呆**」而不是「修复」：第 64 刀刻意不给 `natural_language` 做机械层
      （单段散文：机械剔词伤语法/语义，追加声明句违反红线 28「交付文本只写画面」）。
      本函数让这件事**可观测** —— 第一人称下若真的用了它，trace 里能看到告警，
      而不是静默回到「**第三人称人物在场**」的缺陷态。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return ""
    if str(profile or "").strip() not in _FP_UNCOVERED_PROFILES:
        return ""
    return (
        f"profile `{profile}` 在第一人称下**没有 POV 机械保证**"
        "（无 POV 声明注入、无旁观机位词剔除）；详见技术手册 "
        "`A-roleplay-illustration/03` §5 红线 79 ④"
    )


def _viewpoint_clause(scene: Mapping[str, object]) -> str:
    """视角口径（生图模式，2026-09-20 用户定案）。

    第一人称：镜头＝叙事者的眼睛，叙事者本人不入画，但**身体局部要入画**（NSFW 桥段必然有
    身体部位）；第三人称：叙事者是画面里的一个人物，正常参与主体数。

    ⚠ **零回归锁**：`viewpoint` 键缺失/非法 ⇒ 返回空串 ⇒ 三档提示词**逐字节等于旧行为**。
    """
    mode = str(scene.get("viewpoint") or "").strip()
    if mode not in ("first_person", "third_person"):
        return ""
    narrator = str(scene.get("narrator") or "").strip()
    if mode == "third_person":
        return (
            "【视角·第三人称】叙事者是画面里的一个人物，正常参与画面主体数；该出现在画面里时，"
            "按与其他角色**完全相同**的规格写全（外貌、当前服装、动作与可见接触点），"
            "不得因为他是叙事者而省略、含糊或降格成背景。"
            # 2026-09-24 第 55 刀（用户定案「把内容优先、机位是因变量**划分给第三人称**」）：
            # 第 38 刀那条因果**只在第三人称成立** ⇒ 在此**显式绑定到第三人称**，
            # 免得它被误当成全局通则而在第一人称下把镜头推远（本刀的真机事故成因）。
            # ⚠ 措辞只是把既有限定补明，不改第 38 刀的判定内容 ⇒ 第三人称侧零行为变化。
            "【视角·第三人称·机位是自变量】本帧机位**因变量**成立的前提是「观察者可以自由"
            "选择站位」——第三人称下观察者在画面之外，**可以**退远到容纳全部画面事实，"
            "故：**先列全并保留剧情拿到的画面事实，再据此选择最能呈现它们的机位与景别**。"
            # 2026-09-21 第 31 刀（用户实锤）：本条款原先只规定「谁入画」，**没有视线与朝向的
            # 方向口径** ⇒ 真机 L3·natural 在第三人称下写 `exposes … directly toward the
            # camera`，而主人公就站在画面里；当时落成「第三人称＝看向主人公」。
            # ⚠ 2026-09-22 用户纠正：**不得强制**视线指向叙事者 —— 视线方向**按剧情**，
            #   只有剧情明写「看向/注视/回望叙事者」时才写；人称只决定**翻译方向**。
            "【视角·视线与朝向（第三人称）】**视线方向按本轮画面事实写**——只有当剧情/画面事实明确写到"
            "某角色看向、注视、回望叙事者时，才把该角色的视线写成指向叙事者本人；"
            "**不得默认或强制**让角色看向叙事者（没写就不写）。**身体朝向按画面事实写**——"
            "事实写明「撅起臀部、掰开臀瓣呈现给某人」这类**背向式**姿势时，身体是"
            "**背对叙事者本人**的（臀部朝事实指明的那一方）；头与视线也按画面事实写"
            "（事实写回望才回望）；"
            # 2026-09-21 第 33 刀（用户实锤 · L3·NSFW多人 natural_language）：第 31 刀把「身体朝向」
            # 也一刀切钉成「必须指向叙事者本人」，真机据此把两名**背向**撅臀的角色写成
            # 「both facing toward him / 面向 + 跪着」⇒ 空间关系被读成面对面，与「背对主角、只回头」
            # 的剧情矛盾。定案：**身体朝向**改由画面事实决定（背向式姿势＝背对）；
            # ⚠ 2026-09-22 再纠正：**视线也一并交回画面事实**（不得强制指向叙事者）。
            "不得把这写成「面向叙事者」「面向镜头」「跪在他面前」等**正对式**措辞——那会把空间"
            "关系写成面对面。也不得凭空用 toward the camera、at the viewer 之类把方向交给镜头"
            "（只有本轮事实明写看向镜头时才可写）。"
        )
    who = f"叙事者（{narrator}）" if narrator else "叙事者"
    # 第 42 刀·C（2026-09-22 用户实锤「手选第一人称，成图仍含主人公」）：第一人称的**数据层**
    # 保护（`filter_anchor_subjects`／`_strip_narrator_from_model_view`／`_contract_subject_names`）
    # 全部挂在 `narrator` 上，而它来自用户设置的人设名 ⇒ 为空时四条判据集体空转，只剩本条款在挡。
    # 原条款只说「叙事者不入画」，**说不出谁是叙事者** ⇒ 模型读到正文里被角色点名的男主人公
    # （真机：`凌渊`），照样把他画成画面里的人（成稿含 `adult man`，女主服装被绑到他身上）。
    # ⇒ 当时把身份判据从**名字**换成**叙述人称**：以第一人称「我」叙述／行动的那个人就是叙事者。
    #   ⚠ **2026-09-22 再纠正**（真机复现：第三人称叙述的正文点名主人公「凌渊」10 次、
    #   「他」8 次，一个「我」都没有 ⇒ 该规则**整体失效**、主人公被画进画面）：
    #   **文段级判断机械做不了，语境理解得靠 LLM** ⇒ 条款改为「人称由用户手选确定、
    #   不得据正文叙述人称推翻」＋「第三人称点名主人公仍是第一人称、主人公＝镜头」
    #   ＋「请按语境认出他」。⚠ 零回归锁不变（`viewpoint` 缺失/非法 ⇒ 空串）。
    framing = _first_person_framing(scene)
    # ⚠ 三态分流（第 56 刀）：
    #   · `framing` 为空（无接触、也无环境词）⇒ 整个取景块不写，避免出现
    #     「取景上限以上一条为准」这种**指向空条款**的悬空引用（零回归锁）。
    #   · `framing` 是**环境/风景镜**条款（含「不设近景上限」）⇒ **只写它**、
    #     **不写**「近身三步顺序／禁用机位／叙事者从画面下缘进入」三块——
    #     那三块全都以「近身接触」为前提（真机踩过：风景帧被套上禁用
    #     `full body`/`wide shot` 的取景矛盾条款，与「放行风景全貌」直接对撞）。
    #   · 否则＝近身接触帧 ⇒ 写全部四块（第 55 刀原样）。
    if not framing:
        scope = ""
    elif "不设近景上限" in framing:
        scope = framing
    else:
        scope = framing + (
        f"【视角·第一人称·三步顺序（不可颠倒）】① **先内容优先**——先把剧情拿到的画面事实"
        f"（人物、动作、接触点、服装当前状态）**读全**，这一步与第三人称相同；"
        f"② **再判数据视野朝向**——但本帧镜头**已固定**在{who}的眼睛上，**不是**可以自由退远的"
        f"旁观机位 ⇒ **不得**用「内容装不下就把镜头推远」来解决（那是第三人称的做法）；"
        f"③ **最后筛分镜头里该保留的东西**——按上一条给出的取景上限，**只有真正看得见的**"
        f"才写进画面，凡超出该视野的画面事实一律**不进画面**（不是删剧情，是**这一帧看不见**）。"
        f"【视角·第一人称·禁用机位】本帧**禁止**第三方全景机位与旁观视角措辞——包括 "
        f"`from behind`、`three-quarter back view`、`rear view`、`full body`、`wide shot`、"
        f"`long shot`、`environmental composition`、`bird's eye view`，以及"
        f"**`looking down at face`/`from above` 这类俯视**（俯视隐含「有一个站在上方看的人」"
        f"＝旁观者，与「镜头＝{who}的眼睛」矛盾）。若剧情确需表现某个背向部位，"
        f"改用**该部位本身＋当前姿态**的措辞（如 `her bare back arched, facing away, "
        f"spine and shoulder blades visible`），"
        f"**不得**把整个镜头搬到她背后。"
        f"【视角·第一人称·叙事者从画面下缘进入】{who}的身体局部**从画面下缘/下侧边缘进入**"
        f"（`enters from the lower frame edge`／`cropped at the bottom edge`），"
        f"**只看见接触所需的那一小段**——例如「压在{who}的面上」写成"
        f"「器官从画面下缘进入、压在她唇上」，**不写**它的根部、躯干、腿，"
        f"**不写**{who}的脸、肩、视线或任何可以反推出全身的部位。"
        f"⚠ 局部入画**不是**把{who}画到镜头里来——他是**镜头本身**，"
        f"故**永远不出现**他的躯干与面部。"
        )
    height_clause = _first_person_height_clause(scene, who)
    return (
        f"【视角·第一人称】镜头就是{who}的眼睛，{who}本人**不入画**。"
        # ═══ 2026-09-24 第 55 刀：第一人称的**三步因果**（用户定案原话）═══════════════
        # 「如果是第一人称，那就是**先内容优先，再判断第一人称的数据视野朝向，
        #   最后筛分镜头里面应该保留的东西**。」
        # ⇒ 三步必须**按此顺序**写进条款（顺序本身是口径的一部分，不可重排）：
        #   ① 内容优先（与第三人称共有的前提，但要明说它**不推导机位**）；
        #   ② 数据视野朝向（镜头已固定 ⇒ 视野有限，见 `_first_person_framing`）；
        #   ③ 取景筛分（装不下的按层级省略）。
        # ⚠ 与第 38 刀的关系：第 38 刀「内容优先、机位是因变量」已在上方**限定给第三人称**
        #   （用户原话「划分给第三人称」）⇒ 第一人称走本条，不再复用那条的「机位自由」含义。
        #   **不是推翻第 38 刀**，而是补它的**人称分支**。
        # ⚠ `scope` 为空（无接触证据）⇒ 只保留「不入画」底款 ⇒ 零回归锁。
        + scope
        # 2026-10-23：画面事实写明对方比叙事者高 ⇒ 眼高 ＋ 轻微仰拍（无证据 ⇒ 空串）。
        + height_clause
        + f"【视角·第一人称·谁是{who}】本帧人称由**用户手选**确定＝第一人称，"
        f"**不得据正文的叙述人称推翻它**：正文**不一定**以「我」叙述——它可能用**第三人称**"
        f"点名{who}、或只用「他」指代他，**这仍然是第一人称**：正文里的主人公就是{who}"
        f"＝**镜头本身**。请**按语境**认出他（他可能被其他角色点名、也可能只以「他」"
        f"出现在动作里），**无论他叫什么、正文用第几人称写他、输入画面事实里是否写了他的"
        f"外貌与服装，都不得把他算作画面主体**。"
        f"{who}的**身体局部**"
        f"（手、手臂、腿、腰、臀、**下体/男根**等）**只在输入画面事实写明时才入画**"
        f"（输入画面事实＝本帧摘录正文，以及其中以「叙事者局部入画事实」标注的行）——"
        f"有出处就按事实写具体"
        f"（窗口写了「抓住她的臀部」⇒ 写可见的双手；写了「肉棒拍在她脸上」⇒ 就要把这段器官"
        f"写进画面、**从画面下缘进入**压在她脸部接触），**没写就一律不写**；"
        f"⚠ 「有出处就写」**仍受本帧取景上限约束**（上一条）：有出处 ≠ 可以因此把镜头推远、"
        f"把{who}的躯干或面部带进画面；超出取景上限的部分**只写接触点那一小段、其余省略**。"
        f"不得为了体现第一人称而凭空"
        f"补一只手或任何身体部位。"
        # ═══ 第 69 刀（2026-09-29 用户口径）：**接触形态的具体写法** ═══════════════
        # 用户原话：「你的描述应该是一个腿被手掰开、肉棒贴在女角色的小穴口隔着内裤布料磨蹭、
        #   女角色内裤湿透透明。这种描述就没有说主人公完整入场，只是**描述有器官在对应的位置**」。
        # 动机：第 68 刀只做了「**剔掉元语言**」这一半（减法），剔完会出现**描述真空**
        #   （真机 slot `-54-2` 剔完只剩「Only the woman is visible, her lower face …
        #   draw the eye downward」）⇒ 必须补上「**该怎么写**」这一半（加法）。
        # ⚠ 三条自我约束（与第 68 刀同一批口径）：
        #   ① 例子**一律条件式**：只有画面事实写明才写，承接上一条「没写就一律不写」；
        #   ② **只引身体部位与接触词，不引任何机位词**（否则会诱发第 60 刀的禁用机位）；
        #   ③ 无出处的编造由第 70 刀的出处闸机械兜底。
        f"【视角·第一人称·接触形态的具体写法】{who}的部位入画时**只写器官在对应位置**——"
        f"写清**哪一段器官、在谁身上的哪个位置、是隔着衣物还是直接接触**；"
        f"本帧取景上限与「只写接触点那一小段」的约束**仍然全部适用**。"
        f"两种形态（**仅在输入画面事实写明时才写**，没写一律不写）："
        f"①**撬开／推动肢体**——事实写了把她的腿拨开、架起或压向某处 ⇒ 写成"
        f"`a hand prying her leg open`／`her leg lifted and pressed aside by a hand`"
        f"（只写手与腿，**不写这只手的主人**，也不写它从哪来）；"
        f"②**隔着衣物的接触**——事实写了器官贴上去隔着布磨蹭、布面洇湿 ⇒ 写成"
        f"`the shaft pressed against her through the damp fabric`／"
        f"`the wet cloth clinging translucent to the skin`，"
        f"⚠ **只写衣物本身可见的状态**（湿透、透出底色、被顶起、贴出形状），"
        f"**不得**补写衣物之下的器官形态或内部构造。"
        f"不得写{who}的脸或全身，也不得把{who}写成画面里的第二个人；画面主体数只算其他角色。"
        # 2026-09-21 第 31 刀（当时定案「第一人称＝看向镜头」）：镜头就是叙事者的眼睛 ⇒
        # 剧情写「看向叙事者」时翻译成「看向镜头」；把叙事者另写成画面里的第三方人物
        # （真机 L3·natural 曾写 `a dominant third figure who stands just beyond the frame`）
        # 与「叙事者不入画」自相矛盾，必须一并禁掉。
        # ⚠ 2026-09-22 用户纠正：**不得强制**视线指向镜头 —— 视线方向**按剧情**。
        # 2026-09-21 第 33 刀（与第三人称同口径）：**身体朝向**按画面事实——
        # 背向式姿势（撅臀、掰开臀瓣呈现给某人）身体是**背对镜头**的。
        f"【视角·视线与朝向（第一人称）】**视线方向按本轮画面事实写**——只有当剧情/画面事实明确写到"
        f"某角色看向、注视、回望{who}（＝镜头）时，才写成看向镜头；**不得默认或强制**让角色看向镜头"
        f"（没写就不写）。**身体朝向按画面事实写**——「撅起臀部、掰开臀瓣」这类**背向式**姿势，"
        f"身体是**背对镜头**的（臀部朝事实指明的那一方）；头与视线也按画面事实写"
        f"（事实写回望才回望）；"
        f"不得写成「面向镜头」的正对式措辞。不得把{who}另写成画面里的第三方人物、旁观者或"
        f"「画面之外的某个人」，也不得写他站在画面里或占据画面位置。"
        # 第 51 刀 · FP6（2026-09-22 登记）：「看向/亲吻/接触主人公」＝「看向/亲吻/接触镜头」。
        f"【视角·接触镜头（第一人称）】正文里其他角色「看向/亲吻/接触{who}」"
        f"＝「看向/亲吻/接触**镜头**」——因为镜头就是{who}的眼睛；不得把这种接触写成"
        f"「接触画面里的另一个人」或「接触第三方」，也不得因此把主人公画进画面。"
    )


def _system(profile: str, scene: Mapping[str, object]) -> str:
    if profile == "krea2":
        if scene.get("character_lora"):
            return _KREA_SYSTEM + (
                "当前画面已绑定角色 LoRA，但 LoRA 只是视觉辅助，不能替代角色条目。仍须逐项把条目中的"
                "发色、发型、发饰、五官、体型和当前服装写成具体英文；不得输出角色姓名、模型名称、权重、"
                "触发词或任何‘由模型保持身份’的空泛句。"
            ) + _viewpoint_clause(scene)
        return _KREA_SYSTEM + _viewpoint_clause(scene)
    try:
        return _SYSTEMS[profile] + _viewpoint_clause(scene)
    except KeyError as exc:
        raise ValueError(f"未知提示词模式：{profile}") from exc


def _english_scene_details(scene: Mapping[str, object]) -> list[str]:
    """从混合语言 SceneSpec 中保留可直接使用的英文高潮、外貌与艺术事实。"""
    english_details: list[str] = []
    art_direction = scene.get("art_direction")
    candidates = [
        _current_visual_source(scene), scene.get("locale"),
        scene.get("camera"), scene.get("composition"), scene.get("draft_prompt"),
    ]
    subjects = scene.get("subjects")
    actors = scene.get("actors")
    actor_names = {
        str(name).strip() for name in actors if str(name).strip()
    } if isinstance(actors, list) else set()
    if isinstance(subjects, list):
        candidates.extend(
            item.get("description") for item in subjects
            if isinstance(item, Mapping)
            and (
                not actor_names
                or not str(item.get("name") or "").strip()
                or str(item.get("name") or "").strip() in actor_names
            )
        )
    if isinstance(art_direction, Mapping):
        candidates.extend(art_direction.values())
    encounter = scene.get("encounter")
    if isinstance(encounter, Mapping):
        candidates.extend(encounter.values())
    for value in candidates:
        detail = " ".join(str(value or "").split()).strip(" ,.;")
        # 角色外貌常是“中文名: English tags”；兜底不能因中文身份前缀丢掉后面的精确英文特征。
        if _CJK_RE.search(detail):
            detail = detail.encode("ascii", "ignore").decode("ascii")
            detail = " ".join(detail.split()).strip(" :,.;-")
        if detail and re.search(r"[A-Za-z]{3}", detail) and not _REFUSAL_RE.search(detail):
            english_details.append(detail)
    return list(dict.fromkeys([
        *english_details, *_mapped_visual_facts(scene), *_evidenced_visual_facts(scene),
        *_required_event_facts(scene), *_narrator_limb_facts(scene),
    ]))


def _concrete_scene_facts(
    scene: Mapping[str, object], *, include_draft_prompt: bool = True,
) -> list[str]:
    """统一事实底座：只收人物、当前服装、动作、道具、地点与空间关系。"""
    direct_facts: list[str] = []
    for key in ("appearance", "wardrobe"):
        detail = " ".join(str(scene.get(key) or "").split()).strip(" ,.;")
        if detail and detail.isascii() and re.search(r"[A-Za-z]{3}", detail):
            direct_facts.append(detail)
    facts = [*direct_facts, *_mapped_visual_facts(scene), *_scene_action_tags(scene),
             *_evidenced_visual_facts(scene), *_required_event_facts(scene),
             *_narrator_limb_facts(scene)]
    locations = [
        phrase for pattern, phrase in _LOCATION_RULES
        if re.search(pattern, str(scene.get("locale") or ""))
    ]
    facts.extend(locations)
    subjects = scene.get("subjects")
    actors = set(_character_names({"actors": scene.get("actors", [])}))
    if isinstance(subjects, list):
        for item in subjects:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if actors and name and name not in actors:
                continue
            detail = " ".join(str(item.get("description") or "").split()).strip(" ,.;")
            if detail and detail.isascii() and detail not in facts:
                facts.append(detail)
    keys = ["appearance", "camera", "composition"]
    if include_draft_prompt:
        keys.insert(1, "draft_prompt")
    for key in keys:
        raw_detail = str(scene.get(key) or "")
        if key == "draft_prompt":
            # 同轮草稿常包含独立质量行；事实底座只取内容行，不能把质量词重复塞进正文。
            detail_lines = [line.strip() for line in raw_detail.splitlines() if line.strip()]
            raw_detail = detail_lines[-1] if detail_lines else ""
        detail = " ".join(raw_detail.split()).strip(" ,.;")
        if _CJK_RE.search(detail):
            detail = " ".join(detail.encode("ascii", "ignore").decode("ascii").split()).strip(" ,.;:-")
        if detail and re.search(r"[A-Za-z]{3}", detail) and detail not in facts:
            facts.append(detail)
    return list(dict.fromkeys(fact for fact in facts if fact.strip()))


def _scene_camera_tag(scene: Mapping[str, object]) -> str:
    """景别服从要展示的内容，不固定套 medium shot。

    ⚠ 2026-09-24 第 55 刀（用户定案）：第一人称下**镜头已固定在叙事者眼睛上** ⇒ 景别不再
    「服从要展示的内容」（那是第三人称的因变量逻辑），而受**取景上限**约束
    （`_first_person_framing`）。真机事故：turn 52 写 `close-up` 却同时要求
    `three-quarter back view` + 女主全身侧躺 + 抬腿 + 白袜 ⇒ 自相矛盾、物理装不下。
    ⇒ 第一人称下：先取判据/计划的显式景别，**再按取景上限收窄**（只收不放——
    显式要求的更近景别保留，更远景别一律收窄到上限）。
    `viewpoint` 非 `first_person`（含缺失）⇒ **逐字节等于旧行为**（零回归锁）。
    """
    source = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    explicit = str(scene.get("camera") or "").strip()
    tag = ""
    if explicit and explicit.isascii():
        match = re.search(
            r"\b(?:extreme close-up|close-up|close shot|medium close-up|medium shot|"
            r"medium wide shot|wide shot|long shot|full shot)\b", explicit, re.I,
        )
        if match:
            tag = match.group(0).lower()
    if not tag:
        if re.search(r"亲吻|眼睛|瞳|嘴唇|内裤|局部|特写|脸部", source):
            tag = "close-up"
        elif re.search(r"远景|全景|城市全貌|群山|天际线|辽阔|远处风景", source):
            tag = "wide shot"
        else:
            tag = "medium shot"
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return tag
    # ── 第 55／56 刀：第一人称取景收束（只收不放）───────────────────────────
    # ⚠ 第 56 刀（用户第二次纠正）：**只有近身接触帧才收**。风景/环境帧
    #   （`framing` 给出的是「放行全景」条款）**绝不能被收成 medium close-up**——
    #   用户原话「你不能限制死特写中景……只是描写风景……基本彰显不了风景全貌」。
    framing = _first_person_framing(scene)
    if not framing or "不设近景上限" in framing:
        # 无接触证据 / 风景帧 ⇒ 不收景别（逐字节等于旧行为，或按画面事实放行全景）。
        return tag
    cap = "medium close-up"
    _ORDER = (
        "extreme close-up", "close-up", "close shot", "medium close-up",
        "medium shot", "medium wide shot", "wide shot", "long shot", "full shot",
    )
    try:
        if _ORDER.index(tag) > _ORDER.index(cap):
            return cap
    except ValueError:
        pass
    return tag


# 动名词事实（2026-09-20 第 27 刀）：`withdrawing the fingers` / `swaying her hips` 这类**动作片段**
# 只能出现在动作槽；`depicted with` 是名词槽（名词短语 / 独立主格），接动名词即病句。
_GERUND_FACT_RE = re.compile(r"^[A-Za-z]+ing\b", re.I)

# 动作事实的**施动者先行**形状（2026-09-21 第 31 刀）：`both hands spreading her buttocks
# apart` 这类以**施动部位**开头的串，句法上是**动作**（该落 tags 行 / 动作槽），不是
# `depicted with` 槽要的名词短语。旧口径只认动名词（`_GERUND_FACT_RE`）⇒ 新串会被误判成状态，
# 被拼成 `The two adult characters are depicted with both hands spreading her buttocks apart.`
# 这种「复数主语＋单数物主」病句（第 29 刀同族）。
# 2026-09-22 第 43 刀：并入**男根一族**（`an erect penis slapping her face`，第一人称「叙事者
# 局部入画」的措辞）。同一条理由——它是本帧的**动作**，若留在状态槽会被复述成
# `The primary adult woman is depicted with an erect penis slapping her face.`
# （主语是女性主体、器官却属于画外的叙事者 ⇒ 与第 29 刀同族的「主语/物主错配」病句）。
# ⚠ 认冠词 `a/an/the` 的放宽**只作用在男根一族**：hand 一族维持原样 ⇒ 既有事实零影响
#   （`a hand resting on her buttocks` 这类接触措辞仍不进动作槽）。
_ACTION_FACT_LEAD_RE = re.compile(
    r"^\s*(?:both\s+hands?|two\s+hands?|hands?|"
    r"(?:(?:a|an|the)\s+)?(?:erect\s+)?(?:penis|shaft|cock))\b",
    re.I,
)


def _is_action_fact(fact: str) -> bool:
    """动作事实＝动名词开头（既有口径）**或**施动部位先行（第 31 刀补，第 43 刀并入男根一族）。"""
    value = fact or ""
    return bool(_GERUND_FACT_RE.match(value) or _ACTION_FACT_LEAD_RE.match(value))


def _anima_content_description(
    scene: Mapping[str, object], facts: list[str], *, include_actions: bool = True,
) -> str:
    """把同一批具体 tags 复述为画面文段，不输出给下游的元指令。"""
    concrete = list(dict.fromkeys(fact.strip(" ,.;") for fact in facts if fact.strip(" ,.;")))
    actions = _scene_action_tags(scene) if include_actions else []
    # 主语同样按画面主体数选：`_character_names` 会把在场角色全算进来，
    # 单人定格帧会被写成 `The two adult characters is …`（2026-09-19 实锤）。
    # ⚠ 复数主语配 `is` 是语法破损（2026-09-20 真机 L1/L3 成稿实锤）⇒ 动词跟着数走。
    # 2026-09-20 第 24 刀：三人以上不得再写 `The two adult characters`（0 人措辞也按同一函数）。
    actor_count, count_word = _multi_actor_count(scene)
    plural = actor_count >= 2
    subject = "The primary adult woman" if not plural else (
        "The two adult characters" if actor_count == 2 else f"The {count_word} adult characters"
    )
    sentences: list[str] = []
    # 2026-09-20 第 27 刀（用户实锤病句）：`depicted with` 后面只能接**名词短语 / 独立主格**
    # （`both palms pressed against her own buttocks` 是独立主格，合法），接动名词会产出
    # `The three adult characters are depicted with withdrawing the fingers.` ⇒ 动名词事实改走
    # **动作槽**（与动作表 tag 合并去重），其余留在 `depicted with` 槽。
    states = [fact for fact in concrete if not _is_action_fact(fact)]
    # ⚠ 2026-09-20 第 28 刀（探针实锤）：动作事实也必须受 `include_actions` 管——旧实现只清空了
    # `_scene_action_tags(scene)`，从 `facts` 参数传进来的**动作事实会绕过开关** ⇒
    # `_anima_content_description(s, ['withdrawing the fingers'], include_actions=False)`
    # 仍输出 `In this instant, the concrete action is withdrawing the fingers.`。
    # 第 31 刀：判据由 `_GERUND_FACT_RE` 换成 `_is_action_fact`（并入「施动部位先行」的形状，
    # 使 `both hands spreading her buttocks apart` 仍归动作槽、不进 `depicted with` 名词槽）。
    if include_actions:
        action_facts = [fact for fact in concrete if _is_action_fact(fact)]
        actions = list(dict.fromkeys([*actions, *action_facts]))
    if states:
        sentences.append(
            f"{subject} {'are' if plural else 'is'} depicted with {', '.join(states)}."
        )
    if actions:
        sentences.append(f"In this instant, the concrete action is {', '.join(actions)}.")
    return " ".join(sentences) or f"{subject} occupies the depicted scene."


def _safe_anima_draft_tags(value: object) -> list[str]:
    """只接纳短英文内容草稿；拒绝艺术分析、权重主体和完整规划串。"""
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    candidates = [
        line for line in lines
        if not re.search(r"\b(?:masterpiece|best quality|score_\d+)\b", line, re.I)
    ]
    content = candidates[-1] if candidates else ""
    if (
        not content or not content.isascii() or _REFUSAL_RE.search(content)
        or ";" in content
        or re.search(r"\b(?:visual thesis|primary|secondary|tertiary|hierarchy)\s*:", content, re.I)
    ):
        return []
    content = re.sub(r"\([^()]*:\s*\d+(?:\.\d+)?\)", "", content)
    tags = [tag.strip(" ,.;") for tag in content.split(",") if tag.strip(" ,.;")]
    return list(dict.fromkeys(
        tag for tag in tags
        if len(tag) <= 120 and not re.search(r"\b(?:masterpiece|best quality|score_\d+)\b", tag, re.I)
    ))


def _krea_scene_fallback(scene: Mapping[str, object]) -> str:
    """连续硬失败时给出两段式纯英文剧情插画描述（第一段 tags、第二段内容文段）。"""
    multi_sentences = _fallback_multi_actor_sentences(scene)
    concrete_facts = _concrete_scene_facts(scene)
    fact_sentence = (
        " The visible subjects and scene are concretely defined by "
        + ", ".join(concrete_facts) + "."
        if concrete_facts and not multi_sentences else ""
    )
    camera = _scene_camera_tag(scene)
    # 第一段：内容/构图 tags（质量 tags 由程序前置）
    draft_tags = [
        tag.strip(" ,.;") for tag in str(scene.get("draft_prompt") or "").split(",")
        if tag.strip(" ,.;") and tag.isascii()  # 第一段 tags 必须纯英文
    ]
    tags_line = ", ".join(list(dict.fromkeys([
        *draft_tags[:10], camera, "dramatic composition", "clear focal hierarchy",
        "coherent perspective", "layered background depth"])))
    actor_count, count_word = _multi_actor_count(scene)
    environment = _environment_phrase(scene, default="a layered background")
    if not multi_sentences:
        subject_opening = (
            f"A {camera} depicts the primary adult woman in one continuous narrative moment, with her visible action "
            "as the decisive action and visual focus."
        )
    elif actor_count == 2:
        # 第 46 刀：两人帧旧实现写死 `two adult women`（一男一女帧同样如此）⇒ 改按画面主体
        # 的**实际性别组成**；性别拿不到 ⇒ 中性 `two adult characters`（与 anima/natural 同口径）。
        subject_opening = (
            "A balanced two-character environmental composition gives "
            f"{_two_subject_phrase(scene)} distinct silhouettes, "
            f"readable positions, and one shared visual focus against {environment}."
        )
    else:
        subject_opening = (
            f"A balanced {count_word}-character environmental composition gives {count_word} adult characters "
            f"distinct silhouettes, readable positions, and one shared visual focus against {environment}."
        )
    character_sentences = (" " + " ".join(multi_sentences)) if multi_sentences else ""
    relationship_sentence = (
        " Their visible actions, spacing, gaze directions, and contact relationship remain readable as one coherent scene."
        if multi_sentences else ""
    )
    prose = (
        subject_opening
        + character_sentences
        + relationship_sentence
        + fact_sentence
        + " The composition makes every stated body position, contact point, current garment, prop, and location "
          "simultaneously readable, using coherent perspective, directional light, consistent material shading, and controlled "
          "background depth. Maintain consistent anatomy, stable contours, clean edges, restrained detail, and even "
          "tonal transitions."
    )
    return tags_line + "\n" + prose


def _anima_scene_fallback(scene: Mapping[str, object]) -> str:
    """独立 Profile 拒答时复用主剧情链已生成的英文高潮内容。"""
    encounter = scene.get("encounter")
    encounter_values = encounter.values() if isinstance(encounter, Mapping) else ()
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("appearance") or ""),
        str(scene.get("wardrobe") or ""),
        str(scene.get("locale") or ""),
        " ".join(str(value) for value in encounter_values),
    ))
    fact_rules = (
        (r"女人|女性|妇人|女子|她|三十出头", "adult woman"),
        (r"三十出头", "early thirties"),
        (r"药材商贩|药材贩子|药商", "herb merchant"),
        (r"宽肩", "broad shoulders"),
        (r"厚背|结实|壮实", "sturdy build"),
        (r"方脸", "square face"),
        (r"浓眉", "thick eyebrows"),
        (r"虎牙", "prominent canine tooth"),
        (r"厚茧|老茧", "calloused hands"),
        (r"结实.{0,4}小臂", "strong forearms"),
        (r"褐色短褂", "brown work jacket"),
        (r"袖口挽|挽到肘", "rolled sleeves"),
        (r"靴底.{0,8}泥|泥.{0,8}靴", "muddy boots"),
        (r"风尘仆仆", "travel-worn"),
        (r"爽朗|热络", "open cheerful expression"),
        (r"骡子.{0,8}平板车|骡车", "mule-drawn cart"),
        (r"药材|黄芪|艾草|干姜", "bundled medicinal herbs"),
        (r"孤儿院.{0,6}门|门.{0,6}孤儿院", "orphanage entrance"),
        (r"院长.{0,12}(?:不安|迟疑|惶然|恐惧)|(?:不安|迟疑|惶然).{0,12}院长",
         "anxious matron"),
        (r"(?:搬|卸).{0,12}木箱|木箱.{0,12}(?:搬|卸)|搬下|搬到|卸下|卸货",
         "unloading a wooden medicine crate"),
        (r"跳下", "jumping down from the cart"),
        (r"尘土|扬尘", "dust in the air"),
    )
    facts = list(dict.fromkeys([
        *(tag for pattern, tag in fact_rules if re.search(pattern, source)),
        *_safe_anima_draft_tags(scene.get("draft_prompt")),
        *_concrete_scene_facts(scene, include_draft_prompt=False),
    ]))
    # 具体角色/动作/场景 tags 必须先于抽象镜头词；有具体外貌时去掉无信息量的 adult woman。
    direct_appearance = " ".join(str(scene.get("appearance") or "").split()).strip(" ,.;")
    has_specific_english_appearance = bool(
        direct_appearance and direct_appearance.isascii()
        and re.search(r"[A-Za-z]{3}", direct_appearance)
    )
    concrete_role_facts = [
        fact for fact in facts
        if (not has_specific_english_appearance or fact.lower() != "adult woman")
        and fact.lower() not in {"dramatic scene", "dramatic composition", "climactic moment",
                                "dynamic composition", "action pose", "cinematic lighting"}
    ]
    if concrete_role_facts:
        facts = concrete_role_facts
    if has_specific_english_appearance:
        facts = [direct_appearance, *[fact for fact in facts if fact != direct_appearance]]
    base = facts or ["visible decisive action", "environmental narrative composition"]
    content_tags = list(dict.fromkeys([
        *base, _scene_camera_tag(scene), "three-quarter view", "environmental composition",
        "directional light", "layered background", "detailed anime illustration",
    ]))
    content = ", ".join(content_tags)
    # 第 25 刀（发现即修）：追加动作 tag 前必须**判重**。第 24 刀新增的体位词（spreading / swaying /
    # both palms… / exposing…）不在 `_ANIMA_ACTION_RE` 里，于是 `content` 明明已含这些 tag
    # （由 `_concrete_scene_facts` 带入），却仍被追加一次 ⇒ 首行尾部出现重复串（真机 demo 实锤）。
    # 第 31 刀：**同部位闸接入兜底路径** —— `draft_prompt` 已写出的同部位动作
    # （如 `hands gripping and pulling apart their own buttocks`）不得再被规则表推出的
    # 第二条同义串（`both hands spreading her buttocks apart`）叠成两条。判据是**逐 tag
    # 内**「部位＋动词」共现（`_tag_already_actions_part`），不是整串共现，避免误吞体位词。
    action_tags = [
        tag for tag in _scene_action_tags(scene)
        if tag and tag not in content and not _tag_already_actions_part(content, tag)
    ]
    if action_tags and not _ANIMA_ACTION_RE.search(content):
        head, dot, tail = content.partition(".")
        content = f"{head.rstrip(',. ')}, {', '.join(action_tags)}{dot}{tail}"
    multi_sentences = _fallback_multi_actor_sentences(scene)
    if multi_sentences:
        # 2026-09-01 用户实锤：多角色分支把上面已组装的具体动作/事实 tags 整段
        # 覆盖成 2girls 通用模板，高潮动作全丢。改为：首行保留具体 tags（含动作），
        # 多角色分工句只作为正文补充。
        # 2026-09-20 第 24 刀：人数措辞改按**画面主体数**（三主体帧仍写 `2girls, two-shot` /
        # `The two adult characters' …` 是双口径残留）。2 人帧逐字节不变。
        # 第 46 刀（2026-09-22 用户定案）：人数词**不再硬写 `2girls`**，改按
        # `scene["subject_genders"]` 里**画面主体的实际性别组成**写（`2girls` / `2girls, 1boy` /
        # `1girl, 2boys`）。用户原话：「第三人称时按剧情的画面人物判断，男主主人公＋2 女主角
        # 就是 `1boy, 2girls`……**根据剧情判断在场的内容，不是锁死女角色**」
        # ＋「男主＋1 女兜底写 `2girls` 很明显有问题，正常应该是 `1boy, 1girl`」。
        # ⚠ 任一体性别解析不出 ⇒ 只留中性数词（`two-shot`），**不写具体性别族**（用户定案「不写」）。
        # ⚠ 全女二人帧仍得到 `2girls, two-shot, …` ⇒ 与旧措辞逐字节一致（零回归）。
        _actor_count, count_word = _multi_actor_count(scene)
        count_words = _subject_count_words(_contract_subject_genders(scene))
        gender_prefix = f"{count_words}, " if count_words else ""
        count_tags = (
            f"{gender_prefix}{count_word}-shot, balanced composition, clear spatial separation, "
        )
        count_tail = (
            f" The {count_word} adult characters' distinct appearances and actions form one readable relationship, "
        )
        content = (
            count_tags
            + content
            + ". "
            + " ".join(multi_sentences)
            + count_tail
            + "while the background, light direction, consistent material shading, and depth remain coherent."
        )
    if not re.search(r"(?:^|\.\s+)[A-Z][^.?!]{24,}[.?!](?:\s|$)", content):
        if "herb merchant" in facts and "unloading a wooden medicine crate" in facts:
            description = (
                "A sturdy adult herb merchant unloads a wooden medicine crate at the orphanage "
                "entrance, with her calloused hands, square face, and prominent canine tooth receiving "
                "the sharpest detail. Late-afternoon light catches road dust, rough wood, and bundled "
                "herbs while the anxious matron and mule cart recede into softer layers."
            )
        else:
            description = _anima_content_description(scene, content_tags)
        content = f"{content.rstrip(',. ')}. {description}"
    return _normalize("anima_tags", content, scene)


def _environment_phrase(
    scene: Mapping[str, object], *, default: str = "a layered environmental background",
) -> str:
    """环境短语：**有地点事实就用地点**，没有才落回分层背景（2026-09-20 用户口径）。

    地点来源的优先级由调用方保证（本轮正文/状态 → 数据表的上一轮旧值）。这里只做两件事：
    ① 中文地点按 `_LOCATION_RULES` 映射成英文（映射不到 → 退回默认值，不硬塞中文）；
    ② 过长或带分隔符的地点串不塞进句子（会读成病句）→ 退回默认值。
    """
    locale = " ".join(str(scene.get("locale") or "").split()).strip(" ,.;")
    if locale and not locale.isascii():
        mapped = [
            phrase for pattern, phrase in _LOCATION_RULES if re.search(pattern, locale)
        ]
        locale = mapped[0] if mapped else ""
    if not locale or len(locale) > 48 or "," in locale or ";" in locale:
        return default
    return f"a layered {locale} environment"


def _two_subject_phrase(scene: Mapping[str, object]) -> str:
    """两人帧的**主体名词短语**，按画面主体的实际性别组成（第 46 刀，纯函数）。

    · 全女 ⇒ `two adult women`（＝第 24 刀旧措辞**逐字节一致**，零回归）；
    · 一男一女 ⇒ `one adult woman and one adult man`；
    · 全男 ⇒ `two adult men`；
    · 性别解析不全（缺键 / 有空 / 长度不符）⇒ 中性 `two adult characters`
      （用户定案「**不写**具体性别族」，宁缺勿猜）。
    """
    genders = _contract_subject_genders(scene)
    if len(genders) != 2 or any(not str(gender).strip() for gender in genders):
        return "two adult characters"
    girls = sum(1 for gender in genders if "女" in str(gender))
    boys = sum(1 for gender in genders if "男" in str(gender))
    if girls + boys != 2:
        return "two adult characters"
    if girls == 2:
        return "two adult women"
    if boys == 2:
        return "two adult men"
    return "one adult woman and one adult man"


def _natural_scene_fallback(scene: Mapping[str, object]) -> str:
    multi_sentences = _fallback_multi_actor_sentences(scene)
    if multi_sentences:
        actor_count, count_word = _multi_actor_count(scene)
        environment = _environment_phrase(scene)
        if actor_count == 2:
            # 第 46 刀：旧实现把两人帧写死成 `two adult women`（一男一女帧同样如此）⇒
            # 改按画面主体的**实际性别组成**；性别拿不到时用中性 `two adult characters`。
            opening = (
                "A balanced two-character composition places "
                f"{_two_subject_phrase(scene)} in clearly separated positions against "
                f"{environment}. "
            )
        else:
            opening = (
                f"A balanced {count_word}-character composition places {count_word} adult characters in clearly "
                f"separated positions against {environment}. "
            )
        return (
            opening + " ".join(multi_sentences) + " Their visible relationship, "
            "spacing, gaze directions, and contact point remain the overall visual focus. Directional light, material "
            "response, cast shadows, controlled color hierarchy, and a clean high-fidelity finish preserve one coherent scene."
        )
    details = "; ".join(_english_scene_details(scene))
    facts = details or "the visible adult characters performing the supplied decisive action"
    return (
        f"The image preserves {facts}. The decisive story action is the single visual focus within an "
        "environmental narrative composition. The highest detail remains on the characters' faces, hands, "
        "clothing state, and visible contact point while secondary figures and the background recede in clarity. "
        "A coherent directional light source, consistent material shading, cast shadows, controlled color hierarchy, and "
        "clean high-fidelity finish reinforce the visible character relationship without changing any scene fact."
    )


def _niji_scene_fallback(scene: Mapping[str, object]) -> str:
    multi_sentences = _fallback_multi_actor_sentences(scene)
    details = "; ".join(_english_scene_details(scene))
    subject = " ".join(multi_sentences) if multi_sentences else (
        details or "visible adult characters performing the supplied decisive story action"
    )
    ratio = str(scene.get("aspect_ratio") or "2:3")
    if ratio not in ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"):
        ratio = "2:3"
    # 2026-09-21 第 33 刀（③ 同一口径 · 真机 i34 实测）：**词表兜底**也是 niji 四段之一——
    # 真机 `L3·单人锚点[379]` 掉兜底时，兜底三段末尾没有 `", "`（仅 LLM 路径走 `_niji_sections_join`）
    # ⇒ 同一档「合格稿有、兜底稿没有」自相矛盾。收尾习惯必须一致 ⇒ 兜底也走同一处 join；
    # `--ar` 参数段照旧不动（由 `_niji_sections_join` 内部按 `--` 前缀跳过）。
    return _niji_sections_join([
        subject,
        "refined two-dimensional narrative illustration, cohesive linework, controlled color and material motif",
        "environmental composition, directional light, highest detail on faces hands and contact point, layered soft background",
        f"--ar {ratio} --niji 6",
    ])


def deterministic_fallback(profile: str, scene: Mapping[str, object]) -> str:
    """仅用主生成已给出的场景事实组装提示词，不再调用文本模型。"""
    if profile == "krea2":
        prompt = _krea_scene_fallback(scene)
    elif profile == "anima_tags":
        prompt = _anima_scene_fallback(scene)
    elif profile == "natural_language":
        prompt = _natural_scene_fallback(scene)
    elif profile == "niji_sections":
        prompt = _niji_scene_fallback(scene)
    else:
        raise ValueError(f"未知提示词模式：{profile}")
    return _anonymize_prompt_names(prompt, scene)


_FIELD_PATTERNS = {
    "appearance": re.compile(r"\b(?:hair|eyes?|gaze|face|lips?|cheeks?|figure|bust|waist|hips?|legs?)\b", re.I),
    "wardrobe": re.compile(r"\b(?:dress|robe|skirt|shirt|jacket|coat|stockings?|socks?|fabric|garment|clothing)\b", re.I),
    "location": re.compile(r"\b(?:bedchamber|chamber|room|cell|corridor|railing|gate|entrance|forest|street|hall|enclosure|village|outdoors?)\b", re.I),
    "camera": re.compile(r"\b(?:shot|view|angle|lens|focal|perspective|depth of field|camera)\b", re.I),
    "composition": re.compile(r"\b(?:composition|foreground|background|negative space|visual focus|frame|thirds?|diagonal)\b", re.I),
    "lighting": re.compile(r"\b(?:light|lighting|shadow|highlight|backlit|rim light|color temperature)\b", re.I),
    "material": re.compile(r"\b(?:material|texture|fabric|silk|gauze|folds?|skin|hair|wood|metal|wet|moisture)\b", re.I),
    "quality": re.compile(r"\b(?:anatomy|contours?|clean edges?|fine detail|fidelity|polished|best quality|masterpiece)\b", re.I),
}

_LOCATION_RULES = (
    (r"寝殿|卧室", "bedchamber"),
    (r"天牢|牢房|囚室|禁闭室", "confinement cell"),
    (r"长廊|走廊", "corridor"),
    (r"栏杆", "railing"),
    (r"山门", "mountain gate"),
    (r"孤儿院.{0,8}门|门.{0,8}孤儿院", "orphanage entrance"),
    (r"森林|树林", "forest"),
)

def _evidenced_visual_facts(scene: Mapping[str, object]) -> list[str]:
    """开放类型视觉事实：只信任带本轮正文证据且为英文成稿的条目。"""
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    values = scene.get("visual_facts")
    if not isinstance(values, list):
        return []
    facts: list[str] = []
    for item in values[:12]:
        if not isinstance(item, Mapping):
            continue
        fact = str(item.get("fact") or "").strip()
        evidence = image_prompt_extract.restore_jailbreak(
            str(item.get("evidence") or ""),
        ).strip()
        if fact and fact.isascii() and evidence and evidence in narrative:
            facts.append(fact)
    return list(dict.fromkeys(facts))


def _expected_present(prompt: str, phrase: str) -> bool:
    # T1：事件性必现事实（体液/睡眠）也在这张配对表里找自己的 output_pattern，
    # 否则会退化成按词拆的粗判（`cum on face` 要求 cum+face 至少 2 词同时命中）。
    for _source_pattern, known_phrase, output_pattern in (*_VISUAL_FACT_RULES, *_EVENT_FACT_RULES):
        if known_phrase == phrase:
            return bool(re.search(output_pattern, prompt, re.I))
    words = [word for word in re.findall(r"[a-z]{3,}", phrase.lower())
             if word not in {"with", "into", "from", "that", "this", "very", "adult"}]
    if not words:
        return True
    def matches(word: str) -> bool:
        variants = [word]
        if word.endswith("ing") and len(word) > 5:
            stem = word[:-3]
            if len(stem) > 2 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            variants.append(stem)
        return any(re.search(rf"\b{re.escape(value)}\w*\b", prompt, re.I) for value in variants)

    present = sum(1 for word in dict.fromkeys(words) if matches(word))
    return present >= max(1, (len(set(words)) + 1) // 2)


def prompt_field_ledger(prompt: str, scene: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """逐字段验收最终成稿；只记录可由 SceneSpec 客观验证的事实。"""
    fact_scene = _scene_for_facts(scene)
    appearance_source = str(fact_scene.get("appearance") or "")
    subjects = fact_scene.get("subjects")
    subject_visuals = "\n".join(
        str(item.get("description") or "") for item in subjects
        if isinstance(item, Mapping)
    ) if isinstance(subjects, list) else ""
    wardrobe_source = "\n".join((
        appearance_source,
        str(fact_scene.get("wardrobe") or ""),
        str(fact_scene.get("narrative") or "") if re.search(
            r"衣|裙|袍|袜|dress|robe|skirt|stocking", str(fact_scene.get("narrative") or ""), re.I,
        ) else "",
        subject_visuals,
    ))
    appearance_expected = [
        fact for fact in _mapped_facts_from(appearance_source)
        if fact not in _mapped_wardrobe_facts_from(appearance_source)
    ]
    wardrobe_expected = _mapped_wardrobe_facts_from(wardrobe_source)
    if appearance_source.isascii():
        appearance_expected.extend(
            part.strip(" ,.;") for part in appearance_source.split(",")
            if _FIELD_PATTERNS["appearance"].search(part)
            and not _FIELD_PATTERNS["wardrobe"].search(part)
        )
    direct_wardrobe = str(fact_scene.get("wardrobe") or "")
    if direct_wardrobe.isascii():
        wardrobe_expected.extend(
            part.strip(" ,.;") for part in direct_wardrobe.split(",")
            if part.strip(" ,.;") and _FIELD_PATTERNS["wardrobe"].search(part)
        )
    appearance_expected = list(dict.fromkeys(filter(None, appearance_expected)))
    wardrobe_expected = list(dict.fromkeys(filter(None, wardrobe_expected)))
    # 第 43 刀：第一人称「叙事者局部入画」事实并入**账本可验证的动作期望** ⇒ 模型漏写时由
    # `complete_field_coverage` 补进成稿（anima/niji 进 tags 行、散文档进正文尾巴），
    # 不再只靠模型自觉。归**动作槽**而非状态槽：它是本帧的决定性动作，且状态槽会被
    # `_anima_content_description` 复述成 `The primary adult woman is depicted with … penis …`
    # 这种「主语不是器官持有者」的病句（第 27/29 刀同族）。
    actions = list(dict.fromkeys([
        *_scene_action_tags(fact_scene),
        *_narrator_limb_facts(fact_scene),
    ]))
    visual_facts = list(dict.fromkeys([
        *_evidenced_visual_facts(fact_scene),
        # T1：事件性必现事实（体液/睡眠）——提取器常漏写 `scene.visual_facts`，
        # 若只认它，`required` 会恒为 False ⇒ 这类事实零校验零补充（真机实锤）。
        # 并进同一槽位 ⇒ 既进 `required`、又进 `expected`，由 `complete_field_coverage` 补写。
        *_required_event_facts(fact_scene),
    ]))
    locale = str(fact_scene.get("locale") or "")
    locations = [phrase for pattern, phrase in _LOCATION_RULES if re.search(pattern, locale)]
    if locale.isascii() and re.search(r"[A-Za-z]{3}", locale):
        locations.append(locale.strip(" ,.;"))
    requirements = {
        "appearance": bool(appearance_source.strip()),
        "wardrobe": bool(str(fact_scene.get("wardrobe") or "").strip() or wardrobe_expected),
        "action": bool(actions),
        "visual_facts": bool(visual_facts),
        "location": bool(locale.strip()),
        "camera": True,
        "composition": True,
        "lighting": True,
        "material": True,
        "quality": True,
    }
    expected = {
        "appearance": appearance_expected,
        "wardrobe": wardrobe_expected,
        "action": actions,
        "visual_facts": visual_facts,
        "location": locations,
    }
    ledger: dict[str, dict[str, object]] = {}
    for field, required in requirements.items():
        phrases = expected.get(field, [])
        # T3（2026-09-23 真机实锤）：`covered` 必须与**修补层同判据**。
        # `complete_field_coverage` 会把「同部位已写过动作」的候选交给第 29 刀的冲突闸剔除
        # （`_action_fact_conflicts`）；若这里仍按**未过滤**的候选做 `all()`，被剔的那条
        # **永远不会命中**（模型本来就不该再写它）⇒ `action.covered` 变成**永不消失的假红**，
        # `missing_fields` 里恒挂 `action`、把 trace 观测变成噪声（真机：6 条候选里 3 条被豁免、
        # 余 3 条全已命中，却仍报 `covered=False`）。
        # ⚠ 适用范围限定：**只对 action 生效**，其余字段（含 appearance/location 的
        # 「无可验证期望 ⇒ 不判覆盖」设计）逐字不动。
        applicable = phrases
        if field == "action" and phrases:
            applicable = [item for item in phrases if not _action_fact_conflicts(prompt, item)]
        if applicable:
            covered = all(_expected_present(prompt, phrase) for phrase in applicable)
            if field == "location" and not covered:
                covered = bool(_FIELD_PATTERNS["location"].search(prompt))
        elif phrases and field == "action":
            # 候选**全部**被第 29 刀的冲突闸豁免 ⇒ 名单为空，但事实源仍在：本帧没有
            # 「该补而未补」的动作 ⇒ 视为已覆盖（不能落进下面的 `covered = False`）。
            covered = True
        elif required and field in {
            "appearance", "wardrobe", "action", "location", "visual_facts",
        }:
            # 有事实来源却没有可验证的英文期望时，通用 hair/room/action 等词不能
            # 冒充已覆盖；否则本地词典漏项会把空泛模板放行到 ComfyUI。
            covered = False
        elif field in _FIELD_PATTERNS:
            covered = bool(_FIELD_PATTERNS[field].search(prompt))
        else:
            covered = not required
        if field == "appearance" and _VAGUE_IDENTITY_RE.search(prompt):
            covered = False
        ledger[field] = {
            "required": required,
            "covered": bool(covered or not required),
            "expected": phrases,
        }
    return ledger


def complete_field_coverage(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> tuple[str, dict[str, dict[str, object]]]:
    """只补缺字段，不替换已经合格的高潮与艺术决策。"""
    ledger = prompt_field_ledger(prompt, scene)
    missing = [field for field, item in ledger.items() if item["required"] and not item["covered"]]
    factual_missing = [field for field in missing if field in {
        "appearance", "wardrobe", "action", "location", "visual_facts",
    }]
    if not factual_missing:
        return prompt, ledger
    factual_details: list[str] = []
    for field in (
        "appearance", "wardrobe", "action", "visual_facts", "location",
    ):
        if field in factual_missing:
            expected = ledger[field]["expected"]
            if isinstance(expected, list):
                factual_details.extend(str(value) for value in expected if str(value).strip())
    details: list[str] = list(factual_details)
    # 第 29 刀（2026-09-21 用户定案）：**同部位已写过动作的动作事实不追加**
    # （成因与词表见 `_action_fact_conflicts`）。只掐「动作」事实，形态默认值照旧补。
    action_expected = ledger.get("action", {}).get("expected")
    conflicted = {
        str(fact) for fact in (action_expected if isinstance(action_expected, list) else [])
        if str(fact).strip() and _action_fact_conflicts(prompt, str(fact))
    }
    if conflicted:
        factual_details = [fact for fact in factual_details if fact not in conflicted]
        details = [detail for detail in details if detail not in conflicted]
        ledger["__action_conflict__"] = {"skipped": sorted(conflicted)}
    defaults = {
        "camera": "a scene-appropriate medium shot with coherent perspective and readable depth of field",
        "composition": "a clear primary visual focus, foreground guidance, controlled negative space, and layered background depth",
        "lighting": "one coherent directional light source with consistent highlights and cast shadows",
        "material": "consistent hair, fabric and surface rendering with believable light response",
        "quality": "consistent anatomy, stable contours, clean edges, and restrained detail",
    }
    details.extend(defaults[field] for field in missing if field in defaults)
    details = list(dict.fromkeys(detail for detail in details if detail and _expected_present(prompt, detail) is False))
    if not details:
        # 第 33 刀（2026-09-21 用户口径）：niji **内容段末尾统一 `", "`**。这条不变式要在**交付口**
        # 成立——`_normalize` 补过一次，但下面补全分支会先 `rstrip` 再拼；无缺口时也得保证，
        # 否则「有时带、有时不带」比不补更难判读。
        if profile == "niji_sections":
            return _niji_sections_join(prompt.splitlines()), ledger
        return prompt, ledger
    ledger["__field_repair__"] = {
        "count": len(details),
        "fields": sorted({field for field in missing if field in defaults}),
    }
    # 2026-09-20 用户实锤（L3·natural 真机）：原实现把**内部账本标签**原样拼进交付文本——
    # `… focusing all texture on the stretched, eager flesh. Required visible facts: looking at;
    # withdrawing the fingers; consistent anatomy, …` ⇒ 交付稿里出现一句「机制自述」。
    # 用户口径：交付文本只写画面。⇒ 去掉标签；补全是否发生改由 `ledger["__field_repair__"]` 传递。
    # ⚠ 第 27 刀试过「散文通道排除动作片段」⇒ **7 例红灯**：krea2 / natural_language **没有 tags 行**，
    #   散文段是动作事实的**唯一**通道（`test_所有模板把连续高潮编译为复合动作…` 靠它）。
    #   ⇒ 事实一条不删，只**换语法外壳**；anima 的 `depicted with` 名词槽另修（见下）。
    # ⚠ 第 28 刀①（2026-09-20 真机实锤）：旧实现 `"; ".join(片段) + "."` 直接拼在模型稿后
    #   ⇒ `… stands still in the room. withdrawing the fingers; a clear primary visual focus, …`
    #   —— 句号后接**小写片段**。krea2 改为**只改标点**（句号→逗号），理由见 krea2 分支。
    # ⚠ 第二层实锤（2026-09-20 第 21 刀）：**只删标签不够**——`looking at; consistent anatomy,
    # stable contours, clean edges, and restrained detail.` 这类**悬空片段**还挂在文末，
    # 且 anima 文段被拼成病句 `The two adult characters are depicted with looking at.`。
    # 根因：账本里的**事实片段**（`looking at` / `withdrawing the fingers`）被当成句子用。
    # 定案：**事实片段不进任何散文通道**（它们本就来自叙事，模型没写就是没写）；
    # 只有**形态默认值**可以补，且必须补成语法合格的一句英文。
    # 事实**该补**（契约要求 `bedchamber` / `legs raised over the partner's shoulders` 这类
    # 事实必须出现在成稿里），但**悬空动词片段**不能补——`looking at` 单独挂着不成话。
    details = [detail for detail in details if not _is_dangling_fact(detail)]
    natural_sentence = (
        "The image also keeps " + "; ".join(details) + "." if details else ""
    )
    # anima 首行与 niji 分段是**逗号 tag 序列**：长句默认值进去会被下游当 tag。真机 L1/L2/L3 实锤
    # `..., dramatic tension., withdrawing what was sheathed, a scene-appropriate medium shot
    #  with coherent perspective and readable depth of field,`（L2·niji 同病：质量长句进了 subject 段）
    # ⇒ 逗号序列只收 tag 形状；长句默认值只配进自然语言正文。
    tag_defaults = {
        defaults["camera"]: "medium shot, coherent perspective, readable depth of field",
        defaults["composition"]: (
            "clear primary visual focus, foreground guidance, controlled negative space, "
            "layered background depth"
        ),
        defaults["lighting"]: "one directional light source, consistent highlights, cast shadows",
        defaults["material"]: (
            "consistent hair fabric and surface rendering, believable light response"
        ),
        defaults["quality"]: "consistent anatomy, stable contours, clean edges, restrained detail",
    }
    tag_details = [tag_defaults.get(detail, detail) for detail in details]
    # 解释文段只复述**真事实**，不复述通用模板长句（真机实锤：L3·anima 文段被接上
    # `… a clear primary visual focus, foreground guidance, … one coherent directional light source …`）。
    fact_set = set(factual_details)
    prose_facts = [detail for detail in details if detail in fact_set]
    if profile == "krea2":
        # 第 28 刀①（2026-09-20 真机实锤）：旧实现 `prompt.rstrip(" .") + ". " + "; ".join(details)
        # + "."` ⇒ `… stands still in the room. withdrawing the fingers; a clear primary visual
        # focus, …` —— 模型句已收尾，后面却接一个**小写动词片段**，读起来就是断句。
        # ⚠ 两条被否掉的修法（都试过/推演过，留档防回归）：
        #   ① 「片段首字母大写 / 套 `The visible action is …`」⇒ **破合同锁**：字段验收要求事实
        #      **逐字**出现（`a glowing spiral object …` / 单字地点 `bedchamber`），大写即失配，
        #      实测 3 例红灯（`test_所有模板逐条验收…` / `test_krea2词数偏短…` / `test_krea2补丁降级…`）；
        #   ② 「加引导句（`Also in the frame:` 之类）」⇒ 与用户口径相反：用户要的是**砍掉模板尾巴**
        #      （`In this instant, the concrete action is …` / `The image also keeps …` 已因同理被削）。
        # ⇒ 定案＝**只改标点、不改字**：句号改逗号，让账本片段成为模型句的**分词状语 / 同位语续列**。
        repaired = prompt.rstrip(" .") + ", " + "; ".join(details) + "."
    elif profile == "natural_language":
        if not natural_sentence:
            # 事实片段补齐对散文无意义 ⇒ 不算补全（trace 也不再报 field_repair）
            ledger.pop("__field_repair__", None)
            return prompt, ledger
        repaired = prompt.rstrip() + " " + natural_sentence
    elif profile == "anima_tags":
        lines = prompt.splitlines()
        if len(lines) != 2:
            return prompt, ledger
        # 可见事实属于 tags 行；解释文段是**模型自己的**自然语言，只允许追加真事实的复述句
        # （既有锁：`test_anima同义外貌不丢弃Agent动作成稿而由字段账本精确补齐` 要求事实也进第二行）。
        # 悬空 tag（`looking at`）接进文段会产出病句 ⇒ 剔除悬空片段（判据同散文通道）。
        prose_facts = [fact for fact in prose_facts if not _is_dangling_fact(fact)]
        # 2026-09-20 第 27 刀：文段只复述**非动作**真事实（动作片段归 tags 行）。全部是动作片段时
        # **不追加**任何尾巴——否则模型自己写好的文段会被接上 `The three adult characters are
        # depicted with withdrawing the fingers.` 这种病句（用户实锤）。
        prose_states = [fact for fact in prose_facts if not _is_action_fact(fact)]
        tail = (
            f" {_anima_content_description(scene, prose_states, include_actions=False)}"
            if prose_states else ""
        )
        repaired = (
            lines[0].rstrip(" ,") + ", " + ", ".join(tag_details) + ",\n"
            + lines[1].rstrip() + tail
        )
    elif profile == "niji_sections":
        lines = prompt.splitlines()
        if len(lines) != 4:
            return prompt, ledger
        lines[0] = lines[0].rstrip(" ,.;") + ", " + ", ".join(tag_details[:4])
        if len(tag_details) > 4:
            lines[2] = lines[2].rstrip(" ,.;") + ", " + ", ".join(tag_details[4:])
        # 上面两处 `rstrip` 会吃掉内容段末尾的 `", "` ⇒ 交付前统一按第 33 刀口径重拼一次
        # （`--` 参数段保持原样，见 `_niji_sections_join`）。
        repaired = _niji_sections_join(lines)
    else:
        return prompt, ledger
    repaired = _anonymize_prompt_names(repaired, scene)
    return repaired, prompt_field_ledger(repaired, scene)


def _anima_keep_content(raw: str, fact_scene: Mapping[str, object]) -> str:
    """A1（2026-09-04 用户定案）：JSON 中间契约字段（visual_hook/primary_focus/
    supporting_elements）措辞违例，只说明模型没按中间协议组织艺术决策，不代表
    content（最终两行提示词的真源）不合格。NSFW 高潮场景复合焦点是常态，此前
    repair 后仍违例会把整稿作废 → deterministic 词表通用动作兜底：两次画像 LLM
    调用白跑、高潮动作失真。⚠ 2026-09-19 归因更正：事故串 `removing the visible object` 的**来源**
    是旧粗规则 `取出|拔出|抽出|移除 → removing the visible object`（第 7 项已按词义拆开），
    **不是** A1 造成的——A1 本身正是当初为「不再退词表」而立的；沿用旧串字样只为对上既有文档口径。
    这里剥离中间字段，仅用 content 重建两行；重建结果通过正文/事实校验才返回，
    否则空串由调用方继续词表兜底。"""
    if not raw or not str(raw).strip():
        return ""
    content_prompt = str(_normalize("anima_tags", raw, fact_scene)).strip()
    if not content_prompt:
        return ""
    errors = [
        *_errors("anima_tags", content_prompt, fact_scene),
        *_anima_scene_fact_errors(content_prompt, fact_scene),
    ]
    if errors:
        return ""
    return content_prompt


def _patch_failed_prompt(
    profile: str, prompt: str, scene: Mapping[str, object],
    report: dict[str, object] | None = None,
) -> str:
    """补丁降级（优化项⑤）：宁可给「模型稿 + 本地补丁」，不给「词表通用模板」。

    旧行为：稿子只要还有一条错误就**整稿作废** ⇒ `deterministic_fallback` 词表模板。
    真机实测代价（2026-09-19 §2.14）：2453 字 raw、带着真实高潮事实的成稿被换成 777 字模板，
    连带词表误映射出 `removing the visible object` 事故串；补丁接手后该格 777 → 1993 字。

    这里把「作废」收窄成「缺口补丁」：稿子**本体**（结构与语言）可用时保留模型稿，
    缺的客观字段（外貌/着装/动作/地点/可视事实）交给 `complete_field_coverage` 补一句。
    与 A1（`_anima_keep_content`，2026-09-04）同一取向，只是门槛更贴事实——A1 要求
    content 自身**全项通过**，本函数只要求本体可用；A1 在前，故 A1 的行为逐字不变。

    **单向放宽**：只在调用方**本来就要掉兜底**的分支上接手 ⇒ 对已合格稿零影响。
    本体不合格（空稿/拒答/行数或语言不合规）时返回 ""，调用方照旧词表兜底。

    事实与契约的残红**不阻断**保留：词表模板本身也未必满足它们，而模型稿带着真实画面。

    ⚠ 唯一例外＝**凭空装置**（第 33 刀 · 第 14 项）：那不是「缺一项事实」，是**编造**。这类稿要么
    摘掉装置提及后交付，要么作废退词表——绝不原样放行（红线 22「不可放走编造镜面」）。
    """
    if not str(prompt or "").strip():
        return ""
    if _body_errors(profile, prompt):
        return ""
    patched, _ledger = complete_field_coverage(profile, prompt, scene)
    if not str(patched or "").strip() or _body_errors(profile, patched):
        return ""
    # 第 70 刀：编造的「隔着布料的器官接触」**不做机械摘除**（改句子会伤语法/语义，
    # 与道具闸「摘掉装置」不同）⇒ 直接作废，交回调用方走既有兜底通道。
    if _unsourced_contact_hits(patched, scene):
        return ""
    device_hits = _unsourced_device_hits(patched, scene)
    if device_hits:
        pruned = _drop_unsourced_device_mentions(profile, patched, scene)
        if (not pruned.strip() or pruned == patched
                or _body_errors(profile, pruned)
                or _unsourced_visual_device_errors(pruned, scene)):
            if report is not None:
                report["patched_device_dropped"] = [label for label, _ in device_hits]
            return ""
        if report is not None:
            report["patched_device_stripped"] = [label for label, _ in device_hits]
        return pruned
    return patched


def _anima_two_line_rescue(raw: str, fact_scene: Mapping[str, object]) -> str:
    """第 58 刀（2026-09-24 用户口径）：两行格式合同判死时的**本地归一**。

    真机实锤：25 次编译里 3 次死于「第二行必须包含英文自然语言画面描述」——模型稿
    1400–1900 字内容合格（非空/非拒答/无 CJK）、只是没排成「第一行 tags ＋ 第二行
    英文画面描述」的两行结构；repair 重写仍不合格；而确定性兜底只有 662–794 字
    （`adult woman` 开头、**无角色身份**）。
    ⇒ 内容/英文/分级/禁词全过、只剩**格式**时，本地重排成两行，不整稿作废。
    组装不出来（tags < 6 或没有合格英文句）⇒ 返回 ""（调用方照旧走词表兜底，不劣化）。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"```[a-zA-Z]*", "", text)
    restored = image_prompt_extract.restore_jailbreak(text)
    # ① 英文画面描述行：取最长的一条「字母开头、≥25 字符、句读收尾」的句子
    #    （正则与合同同款 ⇒ 提出来的句子必然过「第二行」那关；⚠ 必须排除换行，
    #    否则 tags 行（无句读）会被连同下一句一起吞成一个「跨行 prose」）
    prose = ""
    for match in re.finditer(r"[A-Za-z][^.?!\n]{24,}[.?!]", restored):
        cand = match.group(0).strip()
        if len(cand) > len(prose):
            prose = cand
    if not prose:
        return ""
    # ② tags 行：prose 之外的剩余文本里的 ASCII 逗号段（丢中英文混杂段/代码围栏）。
    #    ⚠ prose 是从 `restored`（restore_jailbreak 还原后）提取的，residue 也必须用
    #    `restored` 去 replace，否则含破甲标记时匹配不到 ⇒ prose 残留进 tags（2026-09-24 审计）。
    residue = restored.replace(prose, " ")
    tags: list[str] = []
    for seg in re.split(r"[\n;。！？]", residue):
        seg = seg.strip()
        if not seg or not seg.isascii():
            continue
        for tag in seg.split(","):
            tag = tag.strip().strip("-. ")
            if tag and tag.isascii() and len(tag) <= 40:
                tags.append(tag)
    tags = list(dict.fromkeys(tags))[:40]
    if len(tags) < 6:
        return ""
    rebuilt = f"{', '.join(tags)}\n{prose}"
    errors, _warnings = _split_length_warnings(_errors("anima_tags", rebuilt, fact_scene))
    if errors:
        return ""
    return rebuilt


def generate(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
    diagnostics: dict[str, object] | None = None,
    *,
    allow_repair: bool = True,
) -> str:
    """调用文本模型并验证目标协议；语义格式错误时携带原因重写一次。

    allow_repair=False（插画链同轮兜底用）：第一次不通过直接本地确定性兜底，
    不再花第二次 LLM 调用——正文已生成完，用户不该为提示词重写干等。"""
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    report = diagnostics if diagnostics is not None else {}
    report.clear()
    report.update({"strategy": "direct", "first_errors": [], "repair_errors": []})
    fact_scene = _scene_for_facts(scene)
    model_scene = _scene_for_model(scene)
    system = _system(profile, fact_scene)
    source = json.dumps(model_scene, ensure_ascii=False, separators=(",", ":"))
    first_raw = generate_text(system, source)
    raw = _strip_refusal_suffix(_strip_think(first_raw))
    # 插桩（2026-09-18）：把「上游返回空」「模型明确拒答」「内容被剥离成空」三种失败
    # 分开记账——此前 trace 只有 strategy=fallback，分不清 NSFW 拦截发生在哪一层。
    report["first_raw_chars"] = len(first_raw or "")
    report["first_empty"] = not raw.strip()
    report["first_refusal"] = bool(
        _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(first_raw or ""))
    )
    prompt = _normalize(profile, raw, fact_scene)
    errors, length_warnings = _split_length_warnings(_errors(profile, prompt, fact_scene))
    if length_warnings:
        report["warnings"] = length_warnings
    # 语言违规的**观测事实**（2026-09-19 优化项②）：本档「中文」违例是已知的采样事件，
    # 只记错误串无法区分「偶发一句中文残留」与「整篇中文叙事」——两者的修法完全不同。
    # 这里把根因形态压成一个比率，trace 里与 first_errors 并排即可判读（先观测后改法）。
    if errors and _CJK_RE.search(prompt):
        report["first_cjk_chars"] = len(_CJK_RE.findall(prompt))
        report["first_cjk_ratio"] = round(
            len(_CJK_RE.findall(prompt)) / max(1, len(prompt)), 3)
    # 画面不可绘制内容的**观测事实**（2026-09-20 用户口径：自然语言档必须「定格」，不得写
    # 气味 / 声音 / 台词 / 心理）。与 CJK 观测同源——**只记事实、不判死**：这类措辞偏了也
    # 比掉词表兜底好，把它升级成错误会让每一格都更容易掉兜底。留待真机 trace 判读效果。
    unpaintable = sorted({match.group(0).lower() for match in _UNPAINTABLE_RE.finditer(prompt)})
    if unpaintable:
        report["first_unpaintable_terms"] = unpaintable
    if not raw.strip() and _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(first_raw)):
        errors.insert(0, "模型返回拒答")
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(raw))
        errors.extend(_anima_scene_fact_errors(prompt, fact_scene))
    if not errors:
        completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
        report["field_ledger"] = ledger
        return completed
    report["first_errors"] = list(dict.fromkeys(errors))
    if not allow_repair:
        # A1：即使不允许第二次调用，content 合格时也不该被中间字段措辞拖进词表。
        kept = _anima_keep_content(raw, fact_scene) if profile == "anima_tags" else ""
        if kept:
            report["strategy"] = "kept_content"
            completed, ledger = complete_field_coverage(profile, kept, fact_scene)
            report["field_ledger"] = ledger
            return completed
        # ⑤ 补丁降级：稿子本体可用就留住模型稿、只补缺口，不再整稿作废退词表。
        patched = _patch_failed_prompt(profile, prompt, fact_scene, report)
        if patched:
            report["strategy"] = "patched"
            completed, ledger = complete_field_coverage(profile, patched, fact_scene)
            report["field_ledger"] = ledger
            return completed
        # 第 58 刀：两行格式判死 ⇒ 本地归一（内容合格、只剩格式时不整稿作废）。
        rescued = _anima_two_line_rescue(raw, fact_scene)
        if rescued:
            report["strategy"] = "normalized_two_line"
            completed, ledger = complete_field_coverage(profile, rescued, fact_scene)
            report["field_ledger"] = ledger
            return completed
        report["strategy"] = "fallback"
        fallback = deterministic_fallback(profile, fact_scene)
        completed, ledger = complete_field_coverage(profile, fallback, fact_scene)
        report["field_ledger"] = ledger
        return completed
    repair = (
        f"{source}\n\n上次输出未通过：{'；'.join(errors)}。请严格按系统协议重写。"
        f"\n上次输出：{raw}"
    )
    second_raw = generate_text(system, repair)
    repaired_raw = _strip_refusal_suffix(_strip_think(second_raw))
    report["repair_raw_chars"] = len(second_raw or "")
    report["repair_empty"] = not repaired_raw.strip()
    report["repair_refusal"] = bool(
        _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(second_raw or ""))
    )
    prompt = _normalize(profile, repaired_raw, fact_scene)
    errors, _extra_warnings = _split_length_warnings(_errors(profile, prompt, fact_scene))
    prior_warnings = report.get("warnings")
    if _extra_warnings and isinstance(prior_warnings, list):
        report["warnings"] = list(dict.fromkeys([*prior_warnings, *_extra_warnings]))
    if (not repaired_raw.strip()
            and _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(second_raw))):
        errors.insert(0, "模型返回拒答")
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(repaired_raw))
        errors.extend(_anima_scene_fact_errors(prompt, fact_scene))
    if errors:
        report["repair_errors"] = list(dict.fromkeys(errors))
        # 第 58 刀：repair 后仍只剩两行格式问题 ⇒ 本地归一，不整稿作废退词表。
        rescued = _anima_two_line_rescue(repaired_raw, fact_scene)
        if rescued:
            report["strategy"] = "normalized_two_line"
            completed, ledger = complete_field_coverage(profile, rescued, fact_scene)
            report["field_ledger"] = ledger
            return completed
        if profile == "anima_tags" and set(errors) <= {
            "必须正好两行",
            "第一行必须包含质量词与至少六个英文内容tags",
            "第二行必须包含英文自然语言画面描述",
        }:
            # 第 59 刀：归一救不回且**只剩两行格式错** ⇒ 定向重试一次——把两行模板
            # 写死进纠错指令（比「请严格按系统协议重写」具体得多），输出再走本地归一；
            # 首次输出也纳入归一候选。兜底稿是无角色身份的降级，能救一张是一张。
            # ⚠ 只对格式错重试：焦点/事实/JSON 结构违例是模板修不了的，不烧这次调用。
            retry_prompt = (
                f"{source}\n\n上次输出未通过：{'；'.join(errors)}。"
                "重写必须**严格两行**：第一行是英文逗号分隔的内容 tags（至少 6 个）；"
                "第二行是一句英文画面描述（字母开头、至少 25 个字符、以句号/问号/感叹号收尾）。"
                f"\n上次输出：{repaired_raw or raw}"
            )
            third_raw = _strip_refusal_suffix(_strip_think(generate_text(system, retry_prompt)))
            report["retry_raw_chars"] = len(third_raw or "")
            for candidate in (third_raw, raw):
                third = _anima_two_line_rescue(candidate, fact_scene)
                if third:
                    report["strategy"] = "normalized_two_line+retry"
                    completed, ledger = complete_field_coverage(profile, third, fact_scene)
                    report["field_ledger"] = ledger
                    return completed
        report["strategy"] = "fallback"
        # Krea2 的长度只用于引导纠错；英文、段落、分级和禁词仍是硬合同。
        if profile == "krea2" and not _inline_krea_errors(prompt, fact_scene):
            completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
            report["field_ledger"] = ledger
            return completed
        if profile == "anima_tags":
            # A1：重写后仍只余中间契约字段违例 → 保留合格 content（真实高潮动作），
            # 不再整稿作废退词表通用动作（2026-09-04 removing the visible object 事故）。
            kept = _anima_keep_content(repaired_raw, fact_scene)
            if kept:
                report["strategy"] = "kept_content"
                completed, ledger = complete_field_coverage(profile, kept, fact_scene)
                report["field_ledger"] = ledger
                return completed
        # ⑤ 补丁降级：重写后仍红，但稿子本体可用 ⇒ 留住模型稿、只补缺口，
        # 不再整稿作废退词表模板（A1 之上再放宽一层：不再要求 content 全项通过）。
        patched = _patch_failed_prompt(profile, prompt, fact_scene, report)
        if patched:
            report["strategy"] = "patched"
            completed, ledger = complete_field_coverage(profile, patched, fact_scene)
            report["field_ledger"] = ledger
            return completed
        fallback = deterministic_fallback(profile, fact_scene)
        completed, ledger = complete_field_coverage(profile, fallback, fact_scene)
        report["field_ledger"] = ledger
        return completed
    report["strategy"] = "repaired"
    completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
    report["field_ledger"] = ledger
    return completed


def generate_result(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    prompt = generate(profile, scene, generate_text, diagnostics)
    first_errors = diagnostics.get("first_errors")
    repair_errors = diagnostics.get("repair_errors")
    first_items = first_errors if isinstance(first_errors, list) else []
    repair_items = repair_errors if isinstance(repair_errors, list) else []
    result: dict[str, object] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt(profile, scene),
        "strategy": str(diagnostics.get("strategy") or "direct"),
        "validation_errors": list(dict.fromkeys([
            *[str(item) for item in first_items],
            *[str(item) for item in repair_items],
        ])),
        "field_ledger": diagnostics.get("field_ledger") or {},
    }
    return result


_FRAME_EXTRACT_SYSTEM = (
    "你是首尾帧画面事实提取器。输入包含两段画面描述（first=楼层开头画面、last=楼层结尾画面）"
    "与角色/场景结构化事实。分别针对每个时点提取英文结构化画面事实，只输出 JSON 对象："
    '{"first": {"action": "...", "visual_facts": ["...", "..."], "subjects": ["..."]}, '
    '"last": {"action": "...", "visual_facts": ["...", "..."], "subjects": ["..."]}}。'
    "action：该时点人物的身体动作与姿态，一条具体可见的英文短语，不含心理与叙事总结。"
    "visual_facts：该时点可见的视觉事实列表（3-6 条英文短语：肢体位置、衣物状态、道具、"
    "环境互动），必须与该时点的画面描述一致，不得引入画面描述之外的事件。"
    "subjects：该时点**画面里实际可见的人物**列表，按出现顺序，逐条抄输入中给出的指代"
    "（`The primary adult character` 等）或该时点描述里明确出现的人名；"
    "只被台词提及、并未出现在这个画面里的人**不要写**；判断不了就留空数组。"
)


def _parse_frame_extract(raw: str) -> dict[str, dict[str, object]] | None:
    """解析提取输出；剥 think 与代码围栏，字段非法视为整体失败。

    提取模型带 CoT 时 JSON 常混在 think 之后，不剥离则解析必败 → 两次重试全废 →
    帧编译失去提取事实（2026-08-29 新仓库 trace：尾帧降级为中文叙事原文）。
    """
    import json as _json

    text = _strip_think(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        value = _json.loads(text)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    parsed: dict[str, dict[str, object]] = {}
    for frame in ("first", "last"):
        item = value.get(frame)
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        facts = item.get("visual_facts")
        visual_facts = [
            str(entry).strip()
            for entry in (facts if isinstance(facts, list) else [])
            if str(entry).strip()
        ]
        raw_subjects = item.get("subjects")
        subjects = [
            str(entry).strip()
            for entry in (raw_subjects if isinstance(raw_subjects, list) else [])
            if str(entry).strip()
        ]
        if action or visual_facts:
            parsed[frame] = {
                "action": action,
                "visual_facts": visual_facts,
                "subjects": subjects,
            }
    return parsed or None


# 第 34 刀：帧场景**不得继承高潮锚点的画面要素**（用户规格 2026-09-21「首尾帧无需高潮点
# 判断流程，只提取剧情前面部分 / 尾部部分」）。`scene` 是随请求恒定下发的高潮锚点
# `scene_spec`（`agent_agency._wb_frames_and_video`），其中
# narrative / draft_prompt / subjects / frame_subjects / composition / camera /
# action / visual_facts / action_sequence 全按**高潮锚点**算出来 —— 旧实现
# `frame_scene = dict(scene)` 只覆盖 action/visual_facts/protected_narrative，
# 于是首/尾帧被画成高潮画面。真机实锤（探针 i36）：L3 首帧描述只有两人对白，
# 产物却同时列出 primary/second/third adult character（三人）。
#
# ⚠ `frame_subjects` 在**剔除之列**（高潮那份不能用），但第 35 刀把它**按本帧重建**填回来
# ——见 `_build_frame_scene`。剔了不填会让画面主体退回 `actors`（在场全集），
# 契约便按在场人数成稿（真机 i38 实锤：单人的尾帧被硬套多人契约、好成稿被判死）。
_FRAME_SCENE_EXCLUDE = frozenset({
    "narrative", "protected_narrative", "draft_prompt",
    "subjects", "frame_subjects", "secondary_characters",
    "composition", "camera", "action", "visual_facts", "action_sequence", "motion",
})


def _resolve_frame_subjects(
    scene: Mapping[str, object], tokens: object, label_map: Mapping[str, str] | None = None,
) -> list[str]:
    """把时点提取器报出的「本帧可见人物」映射回本名（第 35 刀）。

    为什么要映射：提取器看到的是**匿名后的指代**（`the primary adult character`，见
    `_anonymize_scene_characters`），而本地多人契约（`_actor_visual_details` 的 facts
    归属）按**本名**建键 ⇒ 不映射回去，逐人描述会退化成 `is present in frame` 占位符。

    ⚠ **宁缺勿猜**：一条都对不上（提取器吐中文散文式主体）⇒ 返回空表 ⇒ 调用方**不写**
    `frame_subjects` ⇒ 退回 `actors`，逐字节等于第 34 刀行为（零回归）。
    """
    if not isinstance(tokens, list):
        return []
    base = [str(name).strip() for name in _character_names(scene) if str(name).strip()]
    mapping = dict(label_map) if label_map else dict(
        zip(_character_labels(len(base)), base, strict=False)
    )
    lowered = {str(label).strip().lower(): str(name).strip() for label, name in mapping.items()}
    known_values = scene.get("known")
    candidates = [*base]
    if isinstance(known_values, list):
        candidates.extend(str(name).strip() for name in known_values if str(name).strip())
    candidates = list(dict.fromkeys(candidates))
    resolved: list[str] = []
    for token in tokens:
        text = str(token).strip()
        if not text:
            continue
        name = lowered.get(text.lower()) or next(
            (item for item in candidates if item == text or item.lower() == text.lower()), ""
        )
        if name and name not in resolved:
            resolved.append(name)
    return resolved


def _build_frame_scene(
    scene: Mapping[str, object], desc: str, facts: Mapping[str, object] | None,
    label_map: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """按帧时点重建帧场景。

    事实底座沿用（外貌 / 服装 / 地点 / 在场 / 评级 / 画幅 / 视角 / LoRA），
    画面要素一律取自**本帧自己的描述**：`narrative`/`protected_narrative` 换成帧描述、
    `draft_prompt` 由帧描述重建（与高潮场景从自己那段正文重建同口径）、
    `action`/`visual_facts` 由时点提取器回填（缺失则留空，交 `generate()` 从帧描述推）。

    第 35 刀：`frame_subjects`（**画面主体**）同样只认本帧——取提取器报出的本帧可见人物
    （`_resolve_frame_subjects` 映射回本名）。提取器没报 / 对不上 ⇒ **不写**该键 ⇒
    `_contract_subject_names` 退回 `actors`（零回归）。⚠ 只剔高潮锚点那一份，绝不接管
    `actors`：`actors` 是事实底座（在场全集），第 32 刀「缩输出、不缩归因」的红线。
    """
    frame_scene: dict[str, object] = {
        key: value for key, value in scene.items() if key not in _FRAME_SCENE_EXCLUDE
    }
    frame_scene["narrative"] = desc
    frame_scene["protected_narrative"] = f"@({desc})@"
    frame_scene["draft_prompt"] = image_prompt_extract.build_fallback_content_tags(desc)
    if facts:
        if facts.get("action"):
            frame_scene["action"] = facts["action"]
        if facts.get("visual_facts"):
            frame_scene["visual_facts"] = facts["visual_facts"]
        subjects = _resolve_frame_subjects(scene, facts.get("subjects"), label_map)
        if subjects:
            frame_scene["frame_subjects"] = subjects
    return frame_scene


def generate_frame_prompts(
    profile: str,
    scene: Mapping[str, object],
    frame_descs: Mapping[str, str],
    generate_text: Callable[[str, str], str],
    diagnostics: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    """首尾帧提示词：与高潮点同构——先提取帧时点结构化事实，再走同一编译器。

    高潮点的 action/visual_facts 是主生成同轮搭车提取的结构化产物；首尾帧没有同轮
    载体，这里补一步「时点提取」：一次 LLM 从楼层前/尾部分分别提取该时点的
    action/visual_facts/subjects（英文结构化，字段名与高潮链 scene 对齐），再逐帧
    generate() 编译（同一校验、带因重写、deterministic_fallback 兜底）。
    帧描述是剧情可见正文，模型侧以 @(...)@ 防拦截标记保护（对齐 protected_narrative）。

    ⚠ 传入的 `scene` 是**高潮锚点**的 scene_spec；每帧实际编译用的是 `_build_frame_scene()`
    重建出的帧场景（事实底座沿用、画面要素只取本帧自己的描述、画面主体取本帧提取结果）
    ——见 `_FRAME_SCENE_EXCLUDE` 与 `_resolve_frame_subjects`。
    """
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    report = diagnostics if diagnostics is not None else {}
    report.clear()
    wanted = {
        frame: str(desc or "").strip()
        for frame, desc in frame_descs.items()
        if str(desc or "").strip()
    }
    if not wanted:
        return {}
    # 提取输入：事实字段子集（不含整段 narrative）+ 标记保护的帧描述。
    fact_keys = (
        "appearance", "wardrobe", "location", "visual_facts", "subjects",
        "secondary_characters", "actors", "rating",
    )
    extract_scene = {
        key: scene[key] for key in fact_keys if key in scene
    }
    extract_model_input = {
        **extract_scene,
        "frames": {
            frame: f"@({desc})@" for frame, desc in wanted.items()
        },
    }
    # 第 35 刀：提取器读到的是**匿名后的指代**（`the primary adult character`）⇒ 先按
    # `_anonymize_scene_characters` 的同一份名单与同一份序数词表记下「指代 ↔ 本名」，
    # 提取回来后用它映射回本名（否则多人契约的 facts 归属按键对不上）。
    extract_names = _character_names(extract_model_input)
    label_map = dict(
        zip(_character_labels(len(extract_names)), extract_names, strict=False)
    )
    extract_source = _scene_for_model(extract_model_input)
    extract_payload = json.dumps(
        extract_source, ensure_ascii=False, separators=(",", ":"),
    )
    extracted: dict[str, dict[str, object]] | None = None
    raw = generate_text(_FRAME_EXTRACT_SYSTEM, extract_payload)
    extracted = _parse_frame_extract(raw)
    if extracted is None:
        retry = (
            f"{extract_payload}\n\n上次输出无法解析为约定 JSON，请只输出约定 JSON 对象。\n"
            f"上次输出：{raw}"
        )
        extracted = _parse_frame_extract(generate_text(_FRAME_EXTRACT_SYSTEM, retry))
    report["frame_extract_ok"] = extracted is not None
    # 观测（2026-09-23 甲）：此前 trace 只记 frame_extract_ok 一个布尔 ⇒ 真机出现
    # 「首尾帧成品几乎逐字相同」时，无法判断是**提取器把两帧抹平**还是**编译器稀释**。
    # 这里把提取器的输入/原始输出/解析结果原样带出（纯记录，不参与任何判定）。
    report["frame_extract_input"] = extract_payload
    report["frame_extract_raw"] = raw
    report["frame_extract_parsed"] = extracted
    results: dict[str, dict[str, object]] = {}
    for frame, desc in wanted.items():
        facts = (extracted or {}).get(frame)
        if not facts:
            report[f"{frame}_extract_missing"] = True
        # 帧场景按本帧时点重建（不继承高潮锚点的画面要素），帧描述以 protected_narrative
        # 进链：模型侧带防拦截标记，本地校验用还原正文。
        frame_scene = _build_frame_scene(scene, desc, facts, label_map)
        # 诊断（第 35 刀）：本帧的**画面主体**（多人契约的判据）。模型侧逐格方差大
        # （真机实测：同输入同代码，高潮基线也会 direct↔fallback 翻转），唯这一项是
        # 确定性的 ⇒ 改帧逻辑时只认它做 A/B 归因。
        report[f"{frame}_subjects"] = list(_contract_subject_names(frame_scene))
        frame_diag: dict[str, object] = {}
        # ⚠ 2026-10-20 回退：曾在此传 allow_repair=False 省一轮 LLM 往返（该链最多 6 次调用，
        # 240s 前端超时易打满），但 `03-backend-illustration-pipeline.md:343-345` 记着
        # **2026-09-01 用户确认**：单次不重写会让提示词明显简略。用户口径「只要结果好就没问题」
        # ⇒ 质量优先，帧编译同样保留这次带因重写。
        prompt = generate(profile, frame_scene, generate_text, frame_diag)
        # 观测（2026-09-23 甲）：本帧实际喂进编译器的字段账本 + 场景键 + 成品。
        # 关键怀疑点：两帧的 camera/composition 不是继承来的（scene_spec 本就没有这两个键），
        # 而是编译器从各自 ≤500 字的帧描述里推的 ⇒ 需要账本与成品来判「谁抹平的」。
        report[f"{frame}_field_ledger"] = frame_diag.get("field_ledger") or {}
        report[f"{frame}_scene_keys"] = sorted(frame_scene.keys())
        report[f"{frame}_prompt"] = prompt
        report[f"{frame}_prompt_chars"] = len(prompt)
        results[frame] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt(profile, frame_scene),
            "strategy": str(frame_diag.get("strategy") or "direct"),
            "validation_errors": list(dict.fromkeys([
                *[str(item) for item in _diag_list(frame_diag, "first_errors")],
                *[str(item) for item in _diag_list(frame_diag, "repair_errors")],
            ])),
            "field_ledger": frame_diag.get("field_ledger") or {},
        }
    return results
