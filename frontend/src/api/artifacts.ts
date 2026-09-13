/** 合集卡交付产物 API：预览 / 下载 / 在资源管理器中定位（产物卡三个操作）+ 版本历史。
 *
 * `repoId`（2026-09-10 B3/A）：后端产物域 = 作品域（仓库/小仓库文件夹）∪ 本作品拥有的卡目录
 * （卡目录名可与作品文件夹名不同）。带上它，读取才被收窄到**当前作品**。
 * **2026-09-10 用户定案「缺值返回空结果」**：不带 `repoId` 时后端**不给任何域**——`/list`
 * 回空 `items`、其余端点 403（**不再**回退作品库根看别作品产物）。**调用方必须显式传值**。
 */

import type { ArtifactMeta } from "../types/chat";
import { apiGet, apiPost, apiUrl } from "./client";

/** 作品域参数后缀。**注意**：`repoId` 为空时不加该参数 = 触发后端「缺值返回空结果」
 * （列表回空 / 单文件 403），**不是**回退作品库根——调用方务必显式传当前作品 id。 */
function repoQuery(repoId = ""): string {
  return repoId ? `&repo_id=${encodeURIComponent(repoId)}` : "";
}

export interface ArtifactPreview {
  ok: boolean;
  name: string;
  size: number;
  kind: "card" | "worldbook" | "doc" | "file";
  text: string;
  truncated: boolean;
}

/** 列出当前作品的交付产物（历史消息补卡：无实时 artifacts 时按作品域拉取）。 */
export function listArtifacts(outputDir: string, repoId = "") {
  const q = `/artifacts/list?output_dir=${encodeURIComponent(outputDir)}${repoQuery(repoId)}`;
  return apiGet<{ ok: boolean; items: ArtifactMeta[] }>(q, 20_000);
}

/** 读取产物文本用于预览（后端截断超大文件并标记 truncated）。 */
export function previewArtifact(
  outputDir: string, path: string, repoId = "",
): Promise<ArtifactPreview> {
  return apiGet<ArtifactPreview>(
    `/artifacts/preview?output_dir=${encodeURIComponent(outputDir)}&path=${encodeURIComponent(path)}${repoQuery(repoId)}`,
    30_000,
  );
}

/** 下载产物（浏览器另存为；直接作为 <a href> 使用，或 fetch 后保存）。 */
export function artifactDownloadUrl(outputDir: string, path: string, repoId = ""): string {
  return apiUrl(
    `/artifacts/download?output_dir=${encodeURIComponent(outputDir)}&path=${encodeURIComponent(path)}${repoQuery(repoId)}`,
  );
}

/** 作品域内素材的**内联** URL（文档插图 `<img src>` 用）。
 * 与下载端点分开：下载带 `Content-Disposition: attachment`、media_type 为
 * octet-stream，浏览器不会把它当图片渲染。 */
export function artifactAssetUrl(outputDir: string, path: string, repoId = ""): string {
  return apiUrl(
    `/artifacts/asset?output_dir=${encodeURIComponent(outputDir)}&path=${encodeURIComponent(path)}${repoQuery(repoId)}`,
  );
}

/** 在系统资源管理器中定位产物文件（或打开其所在目录）。返回是否已唤起。 */
export function openArtifactFolder(outputDir: string, path: string, select = true, repoId = "") {
  return apiPost<{ ok: boolean; path: string }>("/artifacts/open-folder", {
    output_dir: outputDir, path, select, repo_id: repoId,
  });
}

/** 产物版本历史：每次智能编造完成自动存档（_versions/<序号>-<时间戳>/）。 */
export interface ArtifactVersionFile {
  rel: string;      // 相对作品根的路径，如 玫瑰与繁花/card.json
  name: string;     // card.json | worldbook.json（与产物白名单一致，可直接预览）
  size: number;
  mtime: number;
  path: string;     // 绝对路径（wire 附带，预览/下载直接回传）
}

export interface ArtifactVersion {
  version_id: string;  // 版本目录名，如 001-20260909103000
  seq: number;
  ts: string;          // ISO 时间
  trigger: string;     // done / pre_restore / …
  summary: string;     // 触发摘要（fabric 完成回复前 200 字）
  files: ArtifactVersionFile[];
  total_size: number;
}

/** 列出产物版本历史（新→旧）。 */
export function listVersions(outputDir: string, repoId = "") {
  const q = `/artifacts/versions?output_dir=${encodeURIComponent(outputDir)}${repoQuery(repoId)}`;
  return apiGet<{ ok: boolean; versions: ArtifactVersion[] }>(q, 20_000);
}

/** 读取**版本副本**的文本用于预览（版本历史「查看」用）。
 *
 * 与 `previewArtifact` 的区别：`/versions` 每条 `files[]` 附带的 `path` 是
 * **当前活文件**路径——改完之后再看旧版本会显示当前文件内容（当前文件被删还会
 * 404）。本接口按 `version_id + rel` 读**版本目录里的副本**，是版本历史的唯一正确来源。 */
export function previewVersionFile(
  outputDir: string,
  versionId: string,
  rel: string,
  repoId = "",
): Promise<ArtifactPreview> {
  return apiGet<ArtifactPreview>(
    `/artifacts/versions/file?output_dir=${encodeURIComponent(outputDir)}&version_id=${encodeURIComponent(versionId)}&rel=${encodeURIComponent(rel)}${repoQuery(repoId)}`,
    30_000,
  );
}

/** 回档到指定版本：后端先给当前状态自动存档（pre_restore），再覆盖回写。 */
export function restoreVersion(outputDir: string, versionId: string, repoId = "") {
  return apiPost<{ ok: boolean; restored: string[] }>("/artifacts/versions/restore", {
    output_dir: outputDir, version_id: versionId, repo_id: repoId,
  });
}

/** 删除指定版本（仅版本目录，不动当前产物）。 */
export function deleteVersion(outputDir: string, versionId: string, repoId = "") {
  return apiPost<{ ok: boolean }>("/artifacts/versions/delete", {
    output_dir: outputDir, version_id: versionId, repo_id: repoId,
  });
}

/** 手动同步：把卡目录的 worldbook.json 同步到独立世界书（资产库 worlds/<卡名>.json）。
 * 旧版自动备份为 <卡名>.json.bak-<时间戳>。仅世界书，不动主卡。 */
export function syncWorldbook(outputDir: string, path: string, repoId = "") {
  return apiPost<{ ok: boolean; dest: string; entries: number }>("/artifacts/sync-worldbook", {
    output_dir: outputDir, path, repo_id: repoId,
  });
}
