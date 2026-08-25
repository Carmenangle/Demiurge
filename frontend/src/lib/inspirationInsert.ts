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
  id?: string;
  title?: string;
  content?: string;
  images?: Array<{ url?: string; full_url?: string }>;
  selected?: string[];
}

const hasImage = (u?: string) => Boolean(u && u.trim());

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

// 画布送达缓存（M1.5）：对话灵感卡 / 素材库灵感卡「发送画布」的统一通道。
// 画布组件（CanvasStageFlow）可能未挂载（不在画布模式/未切视图），派发的事件会丢失。
// 方案：发送方先写入模块级缓存再派发空通知；画布挂载时先消费缓存，之后每收到通知
// 增量消费。消费即清空 → 无论发送时画布是否挂载，切到画布后节点必定出现且不重复。

export interface CanvasInspirationPayload {
  id?: string;
  title?: string;
  content?: string;
  imageUrl?: string;
}

const pendingCanvasInspirations: CanvasInspirationPayload[] = [];
export const CANVAS_INSPIRATION_EVENT = "laf-inspiration-to-canvas";

/** 发送灵感卡到画布：写缓存 + 派发通知（detail 不带数据，画布从缓存取）。 */
export function pushInspirationsToCanvas(items: CanvasInspirationPayload[]): void {
  if (!items || items.length === 0) return;
  pendingCanvasInspirations.push(...items);
  window.dispatchEvent(new CustomEvent(CANVAS_INSPIRATION_EVENT));
}

/** 取走并清空待送达缓存（画布挂载时先消费，之后每次收到通知再消费）。 */
export function consumePendingInspirations(): CanvasInspirationPayload[] {
  if (pendingCanvasInspirations.length === 0) return [];
  return pendingCanvasInspirations.splice(0, pendingCanvasInspirations.length);
}

/** 灵感卡 → 画布节点数据（对话灵感卡消息用：稳定 id + 封面图）。 */
export function inspirationToCanvasPayload(card: InspirationInsertCard & { messageId?: string }): CanvasInspirationPayload {
  const images = inspirationInsertImages(card);
  return {
    id: card.id || (card.messageId ? `insp-${card.messageId}` : undefined),
    title: card.title || "",
    content: card.content || "",
    imageUrl: images[0] || "",
  };
}

/** 有图片时的发送画布文案（无图提示用）。 */
export function inspirationCanvasLabel(card: InspirationInsertCard): string {
  const imgs = inspirationInsertImages(card);
  return imgs.length > 0 ? `发送画布（${imgs.length} 图）` : "发送画布";
}

