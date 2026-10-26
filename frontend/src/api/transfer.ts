// M5 换机迁移：作品包导出/导入。
// 导出**故意用 GET 附件 URL**（不是 fetch blob）：WebView 的 DownloadListener/
// DownloadManager 接不住 blob:，真实同源 http URL + cookie 才能原生落盘（后端
// routers/transfer.py 同口径）。导入走 multipart（<input type=file> 由壳层
// onShowFileChooser 桥供料）。
import { apiUrl } from "./client";

export function repoExportUrl(repoId: string): string {
  return apiUrl(`/transfer/export?repo_id=${encodeURIComponent(repoId)}`);
}

export interface ImportedRepo {
  repo_id: string;
  folder: string;
  name: string;
}

export async function importRepoPackage(file: File): Promise<ImportedRepo> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(apiUrl("/transfer/import"), { method: "POST", body: form });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch { /* 保留状态码文案 */ }
    throw new Error(detail);
  }
  return response.json() as Promise<ImportedRepo>;
}
