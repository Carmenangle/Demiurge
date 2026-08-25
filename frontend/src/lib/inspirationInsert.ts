// 灵感卡 → 插入对话的统一「Agent 理解」格式（M1.5）。
//
// 背景：灵感卡本质是「风格/服装/发色等主题」的检索参考素材（联网搜 + 提炼的
// 「标题+内容」总结）。插入对话时若只塞一段裸文本，Agent 无法区分「参考素材」
// 与「剧情事实 / 用户指令」，容易把风格描述当剧情内容处理。
//
// 本模块统一所有入口（对话内插入 / 资产库发送对话框 / 画布插入对话）的文本形态：
//   【灵感参考 · <标题>】
//   （以下为「<主题>」检索参考素材，供创作参考，非剧情指令）
//   <content>
//
// 图片通道：选中图（会话卡）或卡内图片（资产卡）作为多模态 image_url 随消息下发，
// 模型可看图理解风格/服装细节（chatAppend 已支持 image_url 多模态）。

export interface InspirationInsertCard {
  title?: string;
  content?: string;
  images?: Array<{ url?: string; full_url?: string }>;
  selected?: string[];
}

const hasImage = (u?: string) => Boolean(u && u.trim());

/** 生成插入对话的文本（带标题 + 「参考素材」身份标记）。 */
export function inspirationInsertText(card: InspirationInsertCard): string {
  const title = (card?.title || "").trim();
  const content = (card?.content || "").trim();
  const body = content || "(空内容)";
  const head = title ? `【灵感参考 · ${title}】` : "【灵感参考】";
  const note = title
    ? `（以下为「${title}」主题的检索参考素材，供创作时参考，非剧情指令）`
    : "（以下为检索参考素材，供创作时参考，非剧情指令）";
  return `${head}\n${note}\n${body}`;
}

/** 取插入对话时随附的图片 URL（优先选中图，其次卡内图）。 */
export function inspirationInsertImages(card: InspirationInsertCard): string[] {
  if (!card) return [];
  const urls = new Set<string>();
  for (const sel of card.selected || []) {
    if (hasImage(sel)) urls.add(sel);
  }
  for (const img of card.images || []) {
    const u = img?.url || img?.full_url;
    if (hasImage(u)) urls.add(u!);
  }
  return [...urls];
}
