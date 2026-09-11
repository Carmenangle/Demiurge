import { apiGet, apiPost, apiPut } from "./client";

// 图片一律以 data URI 传，对齐 RichInput 里图片的既有形态（前端本来就拿 data URI）。
// 后端有 16MB 上限并回可读错误，见 services/image_payload。

export interface DecodedGif {
  frames: string[];      // 逐帧 PNG data URI
  durations: number[];   // 每帧毫秒
  width: number;
  height: number;
}

export function decodeGif(image: string) {
  return apiPost<DecodedGif>("/gif-sprite/decode", { image });
}

export function composeSheet(
  frames: string[], cols: number, padding: number, background = "",
) {
  return apiPost<{ image: string; width: number; height: number }>(
    "/gif-sprite/compose", { frames, cols, padding, background },
  );
}

export function sliceSheet(
  image: string, cols: number, rows: number, padding: number, dropEmpty = true,
) {
  return apiPost<{ frames: string[]; count: number }>(
    "/gif-sprite/slice", { image, cols, rows, padding, drop_empty: dropEmpty },
  );
}

export function encodeGif(frames: string[], durationMs: number, transparent: boolean) {
  // frames=成品真实帧数（连续相同帧会被合并），input_frames=送进去的帧数
  return apiPost<{ image: string; frames: number; input_frames: number }>(
    "/gif-sprite/encode", { frames, duration_ms: durationMs, transparent },
  );
}

export interface PaletteResult {
  colors: string[];      // #rrggbb，按占比降序
  preview: string;       // 量化后预览图 data URI
  count: number;
}

export function extractPalette(
  image: string, maxColors: number, bitDepth: string, method: string,
) {
  return apiPost<PaletteResult>("/palette/extract", {
    image, max_colors: maxColors, bit_depth: bitDepth, method,
  });
}

export interface PaletteConstraint {
  colors: string[];
  name: string;
}

// 设为当前色彩约束；colors 传空数组即清除
export function setPaletteConstraint(repoId: string, colors: string[], name = "") {
  return apiPut<PaletteConstraint>("/palette/constraint", {
    repo_id: repoId, colors, name,
  });
}

export function getPaletteConstraint(repoId: string) {
  return apiGet<PaletteConstraint>(
    `/palette/constraint?repo_id=${encodeURIComponent(repoId)}`,
  );
}

export function probeImage(image: string) {
  return apiPost<{ width: number; height: number }>("/image-resize/probe", { image });
}

export interface ResizeOptions {
  targetW?: number;
  targetH?: number;
  scale?: number;
  keepAspect?: boolean;
  filterName?: string;
  sharpen?: number;
  format?: string;
  quality?: number;
}

export function resizeImage(image: string, o: ResizeOptions) {
  return apiPost<{ image: string; width: number; height: number; bytes: number }>(
    "/image-resize/resize", {
      image,
      target_w: o.targetW || 0,
      target_h: o.targetH || 0,
      scale: o.scale || 0,
      keep_aspect: o.keepAspect !== false,
      filter_name: o.filterName || "lanczos",
      sharpen: o.sharpen || 0,
      format: o.format || "png",
      quality: o.quality ?? 92,
    },
  );
}

// 把 File/Blob 读成 data URI，各工具的上传口共用
export function fileToDataUri(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result || ""));
    fr.onerror = () => reject(new Error("读取文件失败"));
    fr.readAsDataURL(file);
  });
}

// 对话里的图片是同源 /api/... 代理地址，可直接取回再交给工具（无跨源污染问题）
export async function urlToDataUri(url: string): Promise<string> {
  if (url.startsWith("data:")) return url;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`取图失败：${r.status}`);
  return fileToDataUri(await r.blob());
}
