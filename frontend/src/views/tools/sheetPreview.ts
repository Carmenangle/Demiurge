// 精灵图的前端即时预览：改「每行帧数/间距/透明背景」时马上看到排布，不用先请求后端。
//
// 布局必须跟 services/gif_sprite.compose_sheet 逐像素对齐，否则预览跟导出的图不是一回事：
//   - cols<=0 表示全铺一行
//   - padding 含外缘：宽 = per*cw + pad*(per+1)，不是 pad*(per-1)
//   - 格子尺寸取所有帧的最大宽高，小帧在格内居中（GIF 逐帧尺寸可能不一致）

const cache = new Map<string, HTMLImageElement>();

function load(uri: string): Promise<HTMLImageElement> {
  const hit = cache.get(uri);
  if (hit) return Promise.resolve(hit);
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => { cache.set(uri, img); resolve(img); };
    img.onerror = () => reject(new Error("预览解码失败"));
    img.src = uri;
  });
}

export interface SheetPreview {
  image: string;
  width: number;
  height: number;
  cols: number;
  rows: number;
}

export interface SheetLayout {
  cols: number;
  rows: number;
  width: number;
  height: number;
}

/** 网格尺寸计算。单独抽出来是为了能不依赖 canvas 直接测，跟后端公式对齐。 */
export function sheetLayout(
  count: number, frameW: number, frameH: number, cols: number, padding: number,
): SheetLayout {
  const c = cols > 0 ? Math.max(1, cols) : Math.max(1, count);
  const rows = Math.max(1, Math.ceil(count / c));
  const pad = Math.max(0, padding);
  return {
    cols: c,
    rows,
    width: frameW * c + pad * (c + 1),
    height: frameH * rows + pad * (rows + 1),
  };
}

/** 把帧按网格拼成一张预览图。frames 为逐帧 data URI，顺序即排布顺序。 */
export async function composeSheetPreview(
  frames: string[], cols: number, padding: number, transparent: boolean,
): Promise<SheetPreview | null> {
  if (!frames.length) return null;
  const imgs = await Promise.all(frames.map(load));
  // 帧尺寸取最大值：GIF 的帧理论上同尺寸，但坏文件里会有偏差，取最大不裁切
  const fw = Math.max(...imgs.map((i) => i.naturalWidth || 1));
  const fh = Math.max(...imgs.map((i) => i.naturalHeight || 1));
  const { cols: c, rows, width: w, height: h } =
    sheetLayout(imgs.length, fw, fh, cols, padding);
  const pad = Math.max(0, padding);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  // 像素画放大时别糊；缩小交给 CSS
  ctx.imageSmoothingEnabled = false;
  if (!transparent) {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);
  }
  imgs.forEach((img, i) => {
    const col = i % c;
    const row = Math.floor(i / c);
    const iw = img.naturalWidth || fw;
    const ih = img.naturalHeight || fh;
    // 小帧在格内居中，跟后端 (cw - fr.width) // 2 一致
    const x = pad + col * (fw + pad) + Math.floor((fw - iw) / 2);
    const y = pad + row * (fh + pad) + Math.floor((fh - ih) / 2);
    ctx.drawImage(img, x, y);
  });
  return { image: canvas.toDataURL("image/png"), width: w, height: h, cols: c, rows };
}
