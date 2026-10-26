// 离线资源同步：可移植设置 + 素材/作品仓库/正则/上网素材。
// 导出在手机端走**绝对 GET 附件 URL**（WebView 的 DownloadListener 认不出相对
// 地址的 host，会静默丢下载；真实同源 http URL 才能原生落盘）；PC 桌面壳没有
// 下载处理器，改由后端 /export/save 直接写进系统下载目录，再用 /reveal 定位。
// 导入走 multipart（<input type=file> 由壳层 onShowFileChooser 桥供料）。
import { apiGet, apiPost, apiUpload, apiUrl } from "./client";

export function configBundleExportUrl(includeSecrets: boolean): string {
  const path = apiUrl(`/config/export?include_secrets=${includeSecrets ? "true" : "false"}`);
  if (typeof window === "undefined") return path;
  return new URL(path, window.location.origin).toString();
}

export interface SavedConfigBundle {
  path: string;
  filename: string;
  bytes: number;
}

export function saveConfigBundle(includeSecrets: boolean): Promise<SavedConfigBundle> {
  return apiPost<SavedConfigBundle>(
    `/config/export/save?include_secrets=${includeSecrets ? "true" : "false"}`, {});
}

export function revealConfigBundle(path: string): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>("/config/reveal", { path });
}

export interface ConfigInventory {
  counts: Record<string, number>;
}

/** 本机现有资源计数：口径与各资源页列表一致（面板用来证明「资源确实进来了」）。 */
export function getConfigInventory(): Promise<ConfigInventory> {
  return apiGet<ConfigInventory>("/config/inventory");
}

export interface ConfigImportCount {
  imported: number;
  skipped: number;
}

export interface ConfigImportResult {
  settings_updated: string[];
  secrets_included: boolean;
  assets: Record<string, ConfigImportCount>;
  repos: ConfigImportCount & { items?: { repo_id: string; name: string; folder: string }[] };
  regex: ConfigImportCount;
  web_materials: ConfigImportCount;
  meta: Record<string, unknown>;
}

export async function importConfigBundle(
  file: File,
  overwrite: boolean,
): Promise<ConfigImportResult> {
  const form = new FormData();
  form.append("file", file);
  return apiUpload<ConfigImportResult>(
    `/config/import?overwrite=${overwrite ? "true" : "false"}`, form);
}
