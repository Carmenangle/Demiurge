export type ClipboardPasteIntent =
  | { kind: "text" }
  | { kind: "image-file" }
  | { kind: "media-url"; url: string }
  | { kind: "html-image"; url: string };

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
  if (hasMeaningfulText) return { kind: "text" };
  if (payload.hasImageFile) return { kind: "image-file" };
  if (htmlImage) return { kind: "html-image", url: htmlImage };
  return { kind: "text" };
}
