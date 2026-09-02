// 新人引导的链接语法（纯逻辑，无 React/无 DOM，便于单测）。
//
// 引导正文（newcomerGuide.ts 的 step.text）里可写三类链接，语法沿用 Markdown 行内链接：
//   [去看「AI 搭工作流」](guide:workflow)            切到别的章节
//   [定位到第 3 步](guide:workflow#3)                切章并滚动定位到该步（序号从 1 起）
//   [工作流模板导入详解](doc:docs/guide/x.md)         打开仓库里的独立教学文档
//   [ComfyUI 官网](https://www.comfy.org)            外链，新标签页打开
//
// 渲染层只认这三类；其它协议（file:、ftp: 等）不解析成链接，按原文显示，避免出现死链。
// 这是「链接只有一个解析属主」的落点：新增协议只改本文件，不要在各组件里各写一份正则。

export type GuideLinkKind = "guide" | "doc" | "external";

export interface GuideLink {
  kind: GuideLinkKind;
  /** 链接文案（方括号里的部分） */
  label: string;
  /** guide: 章节 id；doc: 相对仓库根的 md 路径；external: 完整 URL */
  target: string;
  /** guide 链接带的步骤序号（1 起）；无则只切章不定位 */
  stepNumber?: number;
}

export interface GuideTextSegment {
  type: "text" | "link";
  /** 文本片段原文；链接片段为链接文案 */
  text: string;
  link?: GuideLink;
}

// [文案](target) —— target 内不允许空白与右括号，避免把普通行文里的括号误吃进来。
const LINK_RE = /\[([^\]\n]+)\]\(([^)\s]+)\)/g;

/** 解析一个链接 target；不认识的协议返回 null（调用方按原文渲染）。 */
export function parseGuideLink(target: string, label: string): GuideLink | null {
  const raw = (target || "").trim();
  const text = (label || "").trim() || raw;
  if (!raw) return null;

  if (raw.startsWith("guide:")) {
    const body = raw.slice("guide:".length).trim();
    if (!body) return null;
    const [sectionId, stepRaw] = body.split("#");
    if (!sectionId) return null;
    const stepNumber = Number.parseInt((stepRaw || "").trim(), 10);
    return {
      kind: "guide",
      label: text,
      target: sectionId,
      ...(Number.isInteger(stepNumber) && stepNumber > 0 ? { stepNumber } : {}),
    };
  }

  if (raw.startsWith("doc:")) {
    const docPath = raw.slice("doc:".length).trim();
    if (!docPath) return null;
    return { kind: "doc", label: text, target: docPath };
  }

  if (raw.startsWith("https://") || raw.startsWith("http://")) {
    return { kind: "external", label: text, target: raw };
  }

  return null;
}

/** 把一段正文切成「纯文本 / 链接」片段序列，供渲染层逐段输出。 */
export function splitGuideLinks(text: string): GuideTextSegment[] {
  const source = text || "";
  const segments: GuideTextSegment[] = [];
  let cursor = 0;
  LINK_RE.lastIndex = 0;
  let match = LINK_RE.exec(source);
  while (match) {
    const [full, label, target] = match;
    const link = parseGuideLink(target, label);
    if (link) {
      if (match.index > cursor) {
        segments.push({ type: "text", text: source.slice(cursor, match.index) });
      }
      segments.push({ type: "link", text: link.label, link });
      cursor = match.index + full.length;
    }
    match = LINK_RE.exec(source);
  }
  if (cursor < source.length) segments.push({ type: "text", text: source.slice(cursor) });
  return segments;
}

/** 步骤锚点 id：切章后按它滚动定位。序号与界面显示的「第 N 步」对齐，从 1 起。 */
export function guideStepAnchorId(sectionId: string, stepNumber: number): string {
  return `guide-step-${sectionId}-${stepNumber}`;
}

/** 判断 href 是不是「需要按仓库路径解析」的相对地址（外链/锚点/协议一律 false）。 */
function isRelativeHref(href: string): boolean {
  const raw = (href || "").trim();
  return !!raw && !raw.startsWith("#") && !/^[a-z][a-z0-9+.-]*:/i.test(raw);
}

/** 按当前文档所在目录解析相对地址，返回相对仓库根的路径（GitHub 相对链接同款语义）。 */
function resolveRelativePath(currentDocPath: string, href: string): string | null {
  const raw = (href || "").trim();
  if (!isRelativeHref(raw)) return null;
  const cleaned = raw.split("#")[0].split("?")[0].replace(/\\/g, "/");
  const baseDir = (currentDocPath || "").replace(/\\/g, "/").split("/").slice(0, -1);
  const segments = cleaned.startsWith("/") ? cleaned.slice(1).split("/") : [...baseDir, ...cleaned.split("/")];
  const resolved: string[] = [];
  for (const seg of segments) {
    if (!seg || seg === ".") continue;
    if (seg === "..") { resolved.pop(); continue; }
    resolved.push(seg);
  }
  return resolved.join("/") || null;
}

/**
 * 把文档正文里的相对 md 链接解析成「相对仓库根」的文档路径（GitHub 相对链接同款语义）。
 * 返回 null 表示该链接不该进文档阅读态（外链、页内锚点、非 md），调用方保持默认跳转。
 * 白名单（必须在 docs/ 下）由后端 doc_library 唯一裁决，这里不做二次门禁。
 */
export function resolveDocLink(currentDocPath: string, href: string): string | null {
  const raw = (href || "").trim();
  if (!isRelativeHref(raw)) return null;
  const resolved = resolveRelativePath(currentDocPath, raw);
  if (!resolved || !resolved.toLowerCase().endsWith(".md")) return null;
  return resolved;
}

// 文档正文里的图片相对路径：docs/assets 已被后端挂成 /docs-assets 静态目录。
const DOC_ASSET_PREFIX = "docs/assets/";
export const DOC_ASSET_URL_PREFIX = "/docs-assets/";

/**
 * 文档正文 img 的相对 src → 可直接请求的 URL。
 * 只有落在 docs/assets 下才有对应静态挂载；其余返回 null（调用方保持原样，浏览器按页面 URL 解析）。
 */
export function docAssetUrl(currentDocPath: string, src: string): string | null {
  const raw = (src || "").trim();
  if (!raw || raw.startsWith("data:")) return null;
  if (/^(https?:)?\/\//i.test(raw)) return null;
  if (raw.startsWith(DOC_ASSET_URL_PREFIX)) return raw;
  const resolved = resolveRelativePath(currentDocPath, raw);
  if (!resolved) return null;
  if (!resolved.startsWith(DOC_ASSET_PREFIX)) return null;
  return DOC_ASSET_URL_PREFIX + resolved.slice(DOC_ASSET_PREFIX.length);
}

/** 去掉正文首个一级标题：页面标题栏已显示同一标题，避免正文重复一行大字。 */
export function stripLeadingDocTitle(content: string): string {
  const lines = (content || "").split(/\r?\n/);
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i += 1;
  if (i < lines.length && /^#\s+\S/.test(lines[i])) lines.splice(i, 1);
  return lines.join("\n");
}

// ── hash 合同 ──────────────────────────────────────────────────────────────
// 引导区 hash 形如 #/guide/<章节id>[/doc/<文档路径>]。
// App.tsx 的刷新恢复只读前两段（区/子项），第三段起由本模块自己解析——
// 所以文档路径里的 "/" 不会干扰导航恢复，也不能把文档塞进第二段（会被当无效子项回退）。
export const GUIDE_DOC_SEGMENT = "doc";

export function buildGuideHash(sectionId: string, docPath?: string | null): string {
  const base = `#/guide/${sectionId}`;
  const doc = (docPath || "").trim();
  return doc ? `${base}/${GUIDE_DOC_SEGMENT}/${doc}` : base;
}

export function parseGuideHash(hash: string): { sectionId: string | null; docPath: string | null } {
  let raw = (hash || "").replace(/^#\/?/, "");
  if (!raw) return { sectionId: null, docPath: null };
  try { raw = decodeURIComponent(raw); } catch { /* 非法转义则按原文解析 */ }
  const parts = raw.split("/").filter((p) => p !== "");
  if (parts[0] !== "guide") return { sectionId: null, docPath: null };
  const sectionId = parts[1] || null;
  const docPath = parts[2] === GUIDE_DOC_SEGMENT ? (parts.slice(3).join("/") || null) : null;
  return { sectionId, docPath };
}
