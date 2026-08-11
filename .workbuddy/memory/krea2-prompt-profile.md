# Krea2 剧情高潮英文转译合同

- Krea2 只把确定的剧情高潮画面转换成一个纯英文自然语言段落；不做场景分类，不含角色设定图/超巨型生物规则，也不按 SFW/NSFW 切换提示。
- 与 Anima 共用事实和艺术决策底座，但 Krea2 按固定六维顺序组织：构图与留白占比 → 角色外貌与服装 → 摄影风格/镜头/透视 → 有机材质与画面质感 → 光影/层次/色彩 → 画质质量与完成度。
- 第六维只描述解剖稳定、材质精度、干净边缘、高图像保真度和整体完成度，不代表真人写实；禁止 `photorealistic/live-action photography/realistic human skin` 锁定真人媒介。
- 不续写或改写剧情，不增加输入没有的人物、动作、服装、关系、地点和结果。主计划除 anchor 与 subjects.name 外使用简洁英文视觉事实；高潮重定向只替换错误动作、镜头和构图，稳定 subjects 外貌描述必须保留。
- 角色姓名只作“剧情人物→外貌条目→LoRA 配置”的本地关联键。发送 Profile 前递归匿名化 narrative/appearance/actors/subjects；四 Profile 最终正文再机械移除原姓名。多角色靠各自具体外貌、当前服装、动作和位置区分。
- LoRA 不能代替文字外貌。四种 Profile 都必须逐项翻译条目的发色、发型、发饰、五官、体型、配饰和鞋袜；条目 `【穿着】` 只作基线，wardrobe 或正文中的脱下、破损、凌乱、沾污状态优先。共用语义门禁会拒绝漏项和 identity lock，连续失败兜底仍保留高潮/外貌/当前服装/场景/镜头，禁止固定时段色板覆盖剧情。
- `identified character`、`preserve identity`、`established facial structure`、`defined by the bound model` 等占位句属于硬失败；缺少已识别视觉事实会重写一次。连续失败兜底仍输出具体外貌、当前服装、动作、背景和构图，不得退回 identity lock。
- Krea2 Profile 只处理「高潮画面＋角色条目外貌」；完成后才按实际加载 LoRA 完整文件名查元数据，机械注入精确触发词和兼容质量建议，并与成稿去重。这一步不可前移给模型，否则会增加 Token 且可能改写精确触发词。
- 2026-08-11 真实 HTTP 复测：冷倾雪破屋推门夹具在 Anima/Natural/Niji 最终结果中均保留完整具体外貌、紫色服装、转身推门、破屋与黎明山路，无姓名/identity lock；格式分别为两行/单段/四段。Niji repaired，Anima/Natural 漏项时走完整兜底；Anima 固定 afternoon/dust/warm palette 已删除。
- Profile 兜底只读取当前 `actors` 中仍在场主体的 description；锚点纠正后旧主体及其旧动作不得混回提示词。
- 最终只允许单段纯英文自然语言；中文、拒答、错误段落和禁词是硬失败；长度只作软纠错。
- 2026-08-11 真实端点用冷倾雪条目验证：首轮缺项触发 `repaired`，第二轮无姓名并保留黑发发团、紫玉金饰、朱唇红颊、成熟眼神、丰腴曲线、纤腰圆臀、白色蚕丝袜、紫色薄纱法衣、碎花高衩长裙，以及破屋出口、转身、晨光与纵向构图。后端全量 `1223 passed`。
- 2026-08-11 自动插画恢复同轮隐藏成稿：主 Roleplay 先让 `<content>` 独立满足篇幅，再在隐藏 `<illustration>.profile_prompt` 输出当前 Profile 完整英文提示词；隐藏块不显示、不计正文。显式 `maxTokens` 额外追加 800–1000 token。
- 隐藏 JSON 解析前复用正文 `AI_OUTPUT` 正则，成稿再走 `IMAGE_PROMPT` 清洗；漏块、坏结构、拒答或格式失败均从同轮 `scene_spec` 本地编译，不再依赖前端第二次文本调用。拒答识别已覆盖 `I won't generate/create/...`。
- Krea 同轮成稿若只漏具体视觉事实，机械补齐缺项并保留其高潮、构图与光影，不再因严格门禁整段退化为通用兜底。
- LoRA 顺序不变：Profile 同轮融合高潮＋具体外貌/当前服装，完成后才查询实际 LoRA 元数据并机械注入、去重。
- 最终门禁：后端 1251、前端 343、Ruff、17 条依赖合同、mypy 39、硬编码和生产构建全绿。
