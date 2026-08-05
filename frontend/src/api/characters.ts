import { apiGet, apiPost, apiUrl } from "./client";

export interface CardSummary {
  name: string;
  folder: string;
  has_worldbook: boolean;
  has_regex: boolean;
  has_chat: boolean;
  has_avatar: boolean;
}

export interface ImportPreview {
  name: string;
  spec: string;
  has_worldbook: boolean;
  has_regex: boolean;
  worldbook_entries: number;
  regex_count: number;
}

export function listCharacters(base: string) {
  return apiGet<{ items: CardSummary[] }>(`/characters/?base=${encodeURIComponent(base)}`);
}

export interface ScanResult { imported: string[]; skipped: string[]; failed: string[]; }

// 扫描角色卡文件夹根目录下手动放入的散装卡文件(.json/.png)，解析入库后删源。
// 传 worldbookDir 则把卡内嵌世界书外拆成独立世界书并从卡剥离（含存量卡迁移）。
export function scanLooseCards(base: string, worldbookDir = "") {
  return apiPost<ScanResult>("/characters/scan", { base, worldbook_dir: worldbookDir });
}

// 新建作品时把源库卡+当时选中的用户人设快照进作品仓库文件夹（卡+世界书+正则+头像+persona.json），
// 运行时优先读快照 → 改源卡/设置里的人设不回灌已建作品。
export function snapshotToWork(
  characterDir: string, cardName: string, outputDir: string,
  persona?: { name: string; content: string },
) {
  return apiPost<{ ok: boolean; created: boolean; persona_created: boolean }>(
    "/characters/snapshot-to-work",
    {
      character_dir: characterDir, card_name: cardName, output_dir: outputDir,
      user_name: persona?.name || "", user_persona: persona?.content || "",
    },
  );
}

// 只解析不落盘：拿卡名、是否带世界书/正则，用于 bundle/覆盖确认弹窗
export async function previewCharacter(file: File): Promise<ImportPreview> {
  const fd = new FormData();
  fd.append("file", file);
  const resp = await fetch(apiUrl("/characters/preview"), { method: "POST", body: fd });
  if (!resp.ok) throw new Error(await extractDetail(resp));
  return resp.json();
}

export interface ImportResult extends CardSummary { ok: boolean; }
export interface ImportConflict { reason: "exists"; name: string; has_chat: boolean; }

// 导入。同名且 overwrite=false → 抛 ImportConflict（前端据此弹覆盖确认）
// 传 worldbookDir 则把卡内嵌世界书外拆成独立世界书（名=卡名）并从卡剥离。
export async function importCharacter(
  file: File, base: string, overwrite = false, worldbookDir = "",
): Promise<ImportResult> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("base", base);
  fd.append("overwrite", String(overwrite));
  if (worldbookDir) fd.append("worldbook_dir", worldbookDir);
  const resp = await fetch(apiUrl("/characters/import"), { method: "POST", body: fd });
  if (resp.status === 409) {
    const body = await resp.json().catch(() => ({}));
    const conflict = (body?.detail ?? body) as ImportConflict;
    throw Object.assign(new Error(`已存在同名角色卡「${conflict.name}」`), { conflict });
  }
  if (!resp.ok) throw new Error(await extractDetail(resp));
  return resp.json();
}

export function exportChat(base: string, name: string) {
  return apiGet<{ name: string; chat: unknown }>(
    `/characters/export-chat?base=${encodeURIComponent(base)}&name=${encodeURIComponent(name)}`,
  );
}

export function deleteCharacter(base: string, name: string) {
  return apiPost<{ ok: boolean }>("/characters/delete", { base, name });
}

export function characterDetail(base: string, name: string) {
  return apiGet<Record<string, unknown>>(
    `/characters/detail?base=${encodeURIComponent(base)}&name=${encodeURIComponent(name)}`,
  );
}

// 该卡内嵌正则（regex.json，ST 格式数组）；显示层脚本渲染时用
export function characterRegex(base: string, name: string) {
  return apiGet<{ items: Record<string, unknown>[] }>(
    `/characters/regex?base=${encodeURIComponent(base)}&name=${encodeURIComponent(name)}`,
  );
}

// PNG 卡原图 URL（走 local-view 读盘），无原图返回空
export function avatarUrl(base: string, folder: string): string {
  return apiUrl(`/local-view?path=${encodeURIComponent(`${base}\\${folder}\\avatar.png`)}`);
}

async function extractDetail(resp: Response): Promise<string> {
  try {
    const data = await resp.json();
    return typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? data);
  } catch {
    return `导入失败: ${resp.status}`;
  }
}
