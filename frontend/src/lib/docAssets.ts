/** 文档插图路径解析（2026-09-10 链路③）：把 Markdown 里相对文档的图片路径
 *  （如 `assets/设定-1.png` / `../assets/x.png`）解析成可访问 URL。
 *
 * 为什么要有这一层：后端 `doc.attach_material` 把素材复制进 `<作品>/docs/assets/`
 * 并回传**相对路径**（相对目标文档，便于用户整目录迁移不裂图）；预览渲染时必须
 * 把这个相对路径换算成后端可访问的 URL（`/api/artifacts/asset?...`）。
 * 纯函数、无 DOM 依赖，便于单测。
 */

/** 外部/锚点路径：原样保留（http(s)、data、blob、mailto、#anchor）。
 * 要求 scheme 至少 2 个字符——否则 Windows 盘符 `D:` 会被误判成 scheme。 */
const EXTERNAL_RE = /^(?:[a-z][a-z0-9+.-]+:|#)/i;
/** 已是绝对路径（Windows 盘符 / UNC / POSIX 根）。 */
const ABSOLUTE_RE = /^(?:[A-Za-z]:[\\/]|[\\/])/;

const MD_IMAGE_RE = /(!\[[^\]]*\]\()([^)\s]+)((?:\s+"[^"]*")?\))/g;
const HTML_IMAGE_RE = /(<img\b[^>]*?\bsrc=")([^"]+)(")/g;

function normalizeSlashes(path: string): string {
  return (path || "").replace(/\\/g, "/");
}

/** 路径的目录部分（无目录返回空串）。 */
export function dirOf(path: string): string {
  const normalized = normalizeSlashes(path);
  const cut = normalized.lastIndexOf("/");
  return cut > 0 ? normalized.slice(0, cut) : "";
}

/** 拼接并规范化路径（处理 `.` / `..`，保留盘符/根前缀）。
 *  `relative` 已是绝对路径时直接采用（baseDir 仅作为相对路径的基准）。 */
export function joinPath(baseDir: string, relative: string): string {
  const base = normalizeSlashes(baseDir).replace(/\/+$/, "");
  const rel = normalizeSlashes(relative);
  const combined = !base || ABSOLUTE_RE.test(rel) ? rel : `${base}/${rel}`;
  const drive = /^[A-Za-z]:\//.exec(combined);
  const prefix = drive ? drive[0] : combined.startsWith("/") ? "/" : "";
  const body = prefix ? combined.slice(prefix.length) : combined;
  const stack: string[] = [];
  for (const part of body.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (stack.length) stack.pop();
      continue;
    }
    stack.push(part);
  }
  return prefix + stack.join("/");
}

export interface DocAssetOptions {
  /** 文档自身路径（绝对或相对，用于解析相对图片路径的基准目录）。 */
  docPath: string;
  /** 绝对路径 → 可访问 URL。 */
  toUrl: (absolutePath: string) => string;
}

/** 把 Markdown 里指向文档同级的相对图片路径解析成 `toUrl(绝对路径)`。
 *
 * - 只处理 Markdown 图片语法与 HTML `<img src="...">`；
 * - http(s)/data/blob/#anchor 原样保留；已是绝对路径的也过 `toUrl`（保证可访问）；
 * - 文档没有目录（无基准）时相对路径原样保留，不猜测。
 */
export function resolveDocImageUrls(markdown: string, options: DocAssetOptions): string {
  if (!markdown) return markdown;
  const baseDir = dirOf(options.docPath);
  const resolve = (raw: string): string => {
    const src = (raw || "").trim();
    if (!src || EXTERNAL_RE.test(src)) return raw;
    if (ABSOLUTE_RE.test(src)) return options.toUrl(joinPath("", src));
    if (!baseDir) return raw;
    return options.toUrl(joinPath(baseDir, src));
  };
  return markdown
    .replace(MD_IMAGE_RE, (_m, pre: string, src: string, post: string) => pre + resolve(src) + post)
    .replace(HTML_IMAGE_RE, (_m, pre: string, src: string, post: string) => pre + resolve(src) + post);
}
