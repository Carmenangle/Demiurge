// 上网素材 + 灵感卡资产 + 参考图（原 api/ai.ts 素材段，2026-10-24 按资源域拆分）：
// 图站市场搜索/收藏/代理取图 + 受控下载落盘 + 灵感卡资产库（M1.4）+ 聊天参考图列表。
import { apiGet, apiPost, apiUrl } from "../client";

export interface WebMaterial {
  path: string;
  url: string;         // local-view URL
  /** 素材在资产主表里的 RAG 文档 id（反推/视觉描述落库用；2026-10-18） */
  assetId?: string;
  /** 已生成的视觉描述（素材大多数只有原站标签，没有描述） */
  description?: string;
  source_url: string;  // 来源网页
  title: string;
  filename: string;
  content?: string;    // 灵感卡内容（M1.4 统一展示用；图片素材为空）
  // 2026-10-16 扩充（详情面板「左图右信息」用；来自落盘时的 provenance）
  query?: string;
  provider?: string;
  downloaded_at?: string;
  tags?: string[];
  author?: string;
  rating?: string;
  size?: number;
}

export function listWebMaterials(outputDir: string) {
  return apiPost<{ items: WebMaterial[] }>("/comfyui/web-materials/list", {
    output_dir: outputDir,
  });
}

// P2-1：图站素材市场（pixiv / danbooru）搜索——结果由后端登记受控下载候选，
// 勾选后走 saveWebMaterial 落盘（安全模型不变）
export interface WebMaterialSearchItem {
  full_url: string;
  /** danbooru 的 1920px 版（「查看大图」用它：原图常 20MB+，large 约 1MB 秒开） */
  large_url?: string;
  thumb_url: string;
  source_url: string;
  title: string;
  tags: string[];
  author: string;
  provider: string;
  rating?: string;
  /** 「编辑/详情」面板：danbooru post 分组标签与元数据 */
  post_id?: number;
  tags_general?: string[];
  tags_artist?: string[];
  tags_character?: string[];
  tags_copyright?: string[];
  tags_meta?: string[];
  width?: number;
  height?: number;
  file_size?: number;
  created_at?: string;
  score?: number;
  fav_count?: number;
  is_favorited?: boolean;
  /** pixiv 多页作品：总页数 + 每页 large URL（查看大图翻页 / 下载全部页用） */
  page_count?: number;
  pages?: string[];
}

export function searchWebMaterials(
  source: "pixiv" | "danbooru", query: string, limit = 20, proxy = "", page = 1,
  mode = "search", modeParam = "",
) {
  return apiPost<{ ok: boolean; items: WebMaterialSearchItem[]; error: string; provider: string; next_cursor?: string }>(
    "/comfyui/web-materials/search",
    { source, query, limit, proxy, page, mode, mode_param: modeParam },
  );
}

// pixiv 关注作者列表（user_following）
export interface PixivUser {
  id: number;
  name: string;
  account: string;
  avatar_url: string;
}

export function pixivFollowing(proxy = "", page = 1) {
  const q = new URLSearchParams({ page: String(page) });
  if (proxy) q.set("proxy", proxy);
  return apiGet<{ ok: boolean; users: PixivUser[]; error: string; provider: string }>(
    `/comfyui/web-materials/pixiv/following?${q.toString()}`,
  );
}

// 多页作品全页 URL（illust_detail；feed 对大作品只给 page_count，查看/下载前按需补全）
export function pixivPages(illustId: number, proxy = "") {
  return apiPost<{ ok: boolean; pages: string[]; originals?: string[]; page_count: number; error: string }>(
    "/comfyui/web-materials/pixiv/pages",
    { illust_id: illustId, proxy },
  );
}

// 收藏 / 取消收藏到账号收藏夹（danbooru 需用户名+API Key；pixiv 用 refresh token）
export function favoriteWebMaterial(postId: number, proxy = "", source = "danbooru", remove = false) {
  return apiPost<{ ok: boolean; already: boolean; error: string }>(
    "/comfyui/web-materials/favorite",
    { post_id: postId, proxy, source, remove },
  );
}

// 「查看大图」链接：经后端代理抓取（浏览器不一定能直连 CDN / 有防盗链）
export function marketImageProxyUrl(url: string, proxy = "") {
  const q = new URLSearchParams({ url });
  if (proxy) q.set("proxy", proxy);
  return apiUrl(`/comfyui/web-materials/proxy-image?${q.toString()}`);
}

export function saveWebMaterial(outputDir: string, src: string, sourceUrl = "", title = "", threadId = "") {
  return apiPost<WebMaterial>("/comfyui/web-materials/save", {
    output_dir: outputDir,
    src,
    source_url: sourceUrl,
    title,
    thread_id: threadId,
  });
}

// pixiv 多页作品整册保存：后端一次性拉全页 + 逐页落盘（前端崩溃/断连也不影响完成）
export function saveWebSeries(
  outputDir: string, illustId: number, sourceUrl = "", title = "", proxy = "",
) {
  return apiPost<{ ok: boolean; saved: number; failed: number; page_count: number; error?: string }>(
    "/comfyui/web-materials/save-series",
    { output_dir: outputDir, illust_id: illustId, source_url: sourceUrl, title, proxy },
  );
}

export function deleteWebMaterial(outputDir: string, filename: string) {
  return apiPost<{ ok: boolean }>("/comfyui/web-materials/delete", {
    output_dir: outputDir,
    filename,
  });
}

// ===== 灵感卡资产库（M1.4）：会话灵感卡升级为资产库可管理成员 =====

export interface InspirationCardImage {
  full_url: string;
  source_url: string;
  title?: string;
}

export interface InspirationCardAsset {
  id: string;
  title: string;
  content: string;
  sources: { title: string; url: string }[];
  images: { url: string; source_url: string; title: string }[];
  cover_url: string;
  created_at: string;
}

export function saveInspirationCard(
  outputDir: string,
  data: {
    cardId?: string;
    title: string;
    content: string;
    sources?: { title: string; url: string }[];
    images?: InspirationCardImage[];
    threadId?: string;
  },
) {
  return apiPost<InspirationCardAsset>("/comfyui/web-materials/inspiration/save", {
    output_dir: outputDir,
    card_id: data.cardId || "",
    title: data.title,
    content: data.content,
    sources: data.sources || [],
    images: (data.images || []).map((img) => ({
      full_url: img.full_url,
      source_url: img.source_url,
      title: img.title || "",
    })),
    thread_id: data.threadId || "",
  });
}

export function listInspirationCards(outputDir: string) {
  return apiPost<{ items: InspirationCardAsset[] }>("/comfyui/web-materials/inspiration/list", {
    output_dir: outputDir,
  });
}

export function getInspirationCard(outputDir: string, cardId: string) {
  return apiPost<InspirationCardAsset>("/comfyui/web-materials/inspiration/get", {
    output_dir: outputDir,
    card_id: cardId,
  });
}

export function updateInspirationCard(
  outputDir: string,
  data: {
    cardId: string;
    title?: string;
    content?: string;
    removeImageUrls?: string[];
  },
) {
  return apiPost<InspirationCardAsset>("/comfyui/web-materials/inspiration/update", {
    output_dir: outputDir,
    card_id: data.cardId,
    title: data.title,
    content: data.content,
    remove_image_urls: data.removeImageUrls || [],
  });
}

export function deleteInspirationCard(outputDir: string, cardId: string) {
  return apiPost<{ ok: boolean }>("/comfyui/web-materials/inspiration/delete", {
    output_dir: outputDir,
    card_id: cardId,
  });
}

// ===== 参考图：聊天上传到 <repo>/reference/ 的图片，供画布自动导入 =====

export interface ReferenceImage {
  path: string;
  url: string;
  title: string;
  filename: string;
}

export function listReferenceImages(outputDir: string, repoId: string) {
  return apiPost<{ items: ReferenceImage[] }>("/comfyui/reference-images/list", {
    output_dir: outputDir,
    repo_id: repoId,
  });
}
