export type ClipboardPasteIntent =
  | { kind: "text" }
  | { kind: "image-file" }
  | { kind: "media-url"; url: string }
  | { kind: "html-image"; url: string }
  // 2026-09-01 修复：粘贴混合内容时，图片进图片栏 + 文本进输入框，不再丢弃图片。
  // 原「text 优先」决策已被翻转（richPaste.test.ts L14-20 用例更新同步）。
  | { kind: "text-with-image-file" };

interface ClipboardPastePayload {
  text: string;
  html: string;
  hasImageFile: boolean;
}


const MEDIA_EXTENSION = /\.(png|jpe?g|gif|webp|bmp|mp4|webm|mov|mkv)$/i;
const GENERIC_IMAGE_TEXT = /^(图片|图像|image|photo|photograph)$/i;


function isStandaloneMediaReference(value: string): boolean {
  if (!value || /\s/.test(value)) return false;
  if (/^data:(image|video)\//i.test(value)) return true;

  const absoluteHttp = /^https?:\/\//i.test(value);
  const appRelative = value.startsWith("/");
  if (!absoluteHttp && !appRelative) return false;
  try {
    const parsed = new URL(value, "http://localhost");
    if (/\/comfyui\/(local-)?view\b/i.test(parsed.pathname)) return true;
    return absoluteHttp && MEDIA_EXTENSION.test(parsed.pathname);
  } catch {
    return false;
  }
}


export function classifyClipboardPaste(payload: ClipboardPastePayload): ClipboardPasteIntent {
  const text = payload.text.trim();
  if (isStandaloneMediaReference(text)) return { kind: "media-url", url: text };

  const htmlImage = payload.html.match(/<img[^>]+src=["']([^"']+)["']/i)?.[1];
  const hasMeaningfulText = Boolean(text) && !GENERIC_IMAGE_TEXT.test(text);
  // 2026-09-01 修复：文本+图片混合时**两者都保留**——图片进图片栏，文本插入输入框。
  // 原逻辑直接返回 text（丢弃图片），用户在外部 AI 产品复制含图片引用的内容时常踩。
  if (hasMeaningfulText && payload.hasImageFile) return { kind: "text-with-image-file" };
  if (hasMeaningfulText) return { kind: "text" };
  if (payload.hasImageFile) return { kind: "image-file" };
  if (htmlImage) return { kind: "html-image", url: htmlImage };
  return { kind: "text" };
}
