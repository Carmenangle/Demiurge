// webMaterialTagging.ts — 上网素材的反推编排（2026-11-07）
// 素材详情此前只有「生成视觉描述」一条链；这里把资产库那套五档反推
// （vlm 两段式 / wd14 / gbc / ai / desc）复用到素材：素材同样有 assetId，
// 落在合成仓库 `web-materials`，三个后端打标端点都只认 asset_id + repo_id。
// GBC 复用资产库同一条链：英文场景描述缓存 + 打标后自动生成官方布局 JSON。
import {
  describeImage, ensureWebMaterialIndex, gbcTagAsset, generateMossLayout, getGbcGraph,
  listTagSnapshots, setGenerationDescription, tagFromText, tagGeneration,
  WEB_MATERIALS_REPO_ID, type Embed, type MossLayoutPayload, type SavedGbc, type WebMaterial,
} from "../api/ai";
import type { GbcEngine } from "../stores/settings";
import { imageDataUri } from "./imageData";
import { AI_TAG_PROMPT, VISION_DESC_PROMPT, VLM_TWO_PARA_PROMPT } from "./repoGalleryShared";

export type VlmChat = { baseUrl: string; apiKey: string; modelName: string; proxyUrl?: string };

export interface MaterialTagDeps {
  vlm: VlmChat | null;
  gbc?: GbcEngine;
  embed: Embed;
  /** 素材根目录（<outputDir>/_web_materials）；补登记历史素材用 */
  outputDir: string;
}

export interface MaterialTagOutcome {
  ok: boolean;
  summary: string;
  /** 反推后的当前标签（desc 模式不改标签，不返回） */
  tags?: string[];
  /** 写回的视觉描述（仅 desc / vlm 两段式会写） */
  description?: string;
  /** GBC 场景图（含官方布局；布局失败时只有 outline 与 mossError） */
  gbc?: SavedGbc;
  mossError?: string;
}

const GBC_NOT_CONFIGURED =
  "GBC 引擎未配置：请到「设置 → 路径 → GBC 场景图引擎」填 gbc.exe 与两份权重目录。";

/** 单张素材跑一次反推。调用方负责 busy/错误态；这里只做编排。 */
export async function runMaterialTag(
  item: WebMaterial,
  mode: string,
  deps: MaterialTagDeps,
): Promise<MaterialTagOutcome> {
  const id = item.assetId || "";
  if (!id) {
    return { ok: false, summary: "该素材还没有资产记录：重启后端后新下载的素材会自动补，旧素材需回填" };
  }
  if (!deps.outputDir) return { ok: false, summary: "未配置输出图片路径，无法确认素材资产记录" };
  // 历史素材（P2-1 之前落盘）只有文件 + provenance，资产库里查不到 ⇒ 打标端点回
  // 「资产不存在」，VLM/GBC 档还会先把视觉模型调用花掉。反推前先按需补登记
  // （幂等：已登记的零成本），补不上就当场止损并说清原因。
  const ensured = await ensureWebMaterialIndex(deps.outputDir, [id]);
  if (![...ensured.registered, ...ensured.already].includes(id)) {
    const why = ensured.failed.includes(id)
      ? "补登记失败（嵌入模型没配好或不可用）"
      : "磁盘上找不到这张素材的文件";
    return { ok: false, summary: `这条素材没登记进资产库：${why}` };
  }
  const repoId = WEB_MATERIALS_REPO_ID;
  const { vlm, embed } = deps;

  if (mode === "wd14") {
    // model 传空 = 后端默认打标器（与资产库「WD14 本地」同一档）
    const result = await tagGeneration("", id, repoId, embed);
    return { ok: true, summary: `${result.total} 个标签`, tags: result.tags };
  }

  if (mode === "gbc") {
    const gbc = deps.gbc;
    if (!gbc?.exePath?.trim() || !gbc.sam3Dir?.trim() || !gbc.embedDir?.trim()) {
      return { ok: false, summary: GBC_NOT_CONFIGURED };
    }
    if (!vlm) return { ok: false, summary: "未配置视觉模型" };
    // GBC 的种子描述由后端用 WD14 标签生成（物体/服装/配饰只认标签）；前端不再
    // 花一次视觉模型调用写整图描述，也不再缓存
    const result = await gbcTagAsset(id, repoId, gbc, vlm, embed);
    const base = `${result.total} 个标签 · ${result.outline.entities.length} 物体 / `
      + `${result.outline.relations.length} 条关系（${Math.round(result.elapsed_s)}s）`
      + (result.warning ? `；${result.warning}` : "");
    const saved: SavedGbc = { ok: true, outline: result.outline, engine: { vlm_model: vlm.modelName } };
    try {
      const layout = await generateMossLayout(id, vlm);
      return {
        ok: true,
        summary: `${base}；官方布局已生成（${Math.round(layout.elapsed_s || 0)}s）`,
        tags: result.tags,
        gbc: {
          ...saved,
          moss_layout: {
            layout: layout.layout,
            parts: layout.parts,
            model: layout.model,
            elapsed_s: layout.elapsed_s,
          },
        },
      };
    } catch (e) {
      const msg = (e as Error).message;
      return {
        ok: false,
        summary: `${base}；官方布局生成失败：${msg}（重新打开详情会自动重试）`,
        tags: result.tags,
        gbc: saved,
        mossError: msg,
      };
    }
  }

  if (!vlm) return { ok: false, summary: "未配置视觉模型" };
  const imgForModel = await imageDataUri(item.url);

  // 纯描述模式：只写描述，不产标签、不动快照
  if (mode === "desc") {
    const r = await describeImage([imgForModel], vlm, VISION_DESC_PROMPT);
    const desc = (r.prompt || "").trim();
    if (!desc) return { ok: false, summary: "模型未返回描述（可能不支持视觉）" };
    await setGenerationDescription(id, repoId, desc, embed);
    return { ok: true, summary: "已写回视觉描述", description: desc };
  }

  const prompt = mode === "vlm" ? VLM_TWO_PARA_PROMPT : AI_TAG_PROMPT;
  const r = await describeImage([imgForModel], vlm, prompt);
  const text = (r.prompt || "").trim();
  if (!text) return { ok: false, summary: "模型未返回内容（可能不支持视觉）" };
  const saved = await tagFromText(id, repoId, embed, text, {
    model: mode,
    label: mode === "vlm" ? "两段式" : "视觉打标",
    saveDescription: mode === "vlm",
  });
  return {
    ok: true,
    summary: saved.saved_description ? `${saved.total} 个标签＋描述` : `${saved.total} 个标签`,
    tags: saved.tags,
    description: saved.description || undefined,
  };
}

/** 打开素材详情时读已存的反推标签与 GBC 场景图（都没跑过就返回空）。 */
export async function loadMaterialTagView(
  assetId: string,
  embed: Embed,
): Promise<{ tags: { tags: string[]; label: string } | null; gbc: SavedGbc | null }> {
  const [snaps, gbc] = await Promise.all([
    listTagSnapshots(assetId, WEB_MATERIALS_REPO_ID, embed).catch(() => null),
    getGbcGraph(assetId).catch(() => null),
  ]);
  let tags: { tags: string[]; label: string } | null = null;
  if (snaps?.snapshots?.length) {
    const snap = snaps.snapshots.find((s) => s.id === snaps.active)
      || snaps.snapshots[snaps.snapshots.length - 1];
    if (snap) tags = { tags: snap.tags, label: snap.label || snap.model };
  }
  return { tags, gbc: gbc?.ok ? gbc : null };
}

/** 老场景图补生成官方布局（GBC 跑成功后那次失败时，重开详情自动重试）。 */
export async function generateMaterialMossLayout(
  assetId: string,
  vlm: VlmChat,
): Promise<{ mossLayout?: Partial<MossLayoutPayload>; error?: string }> {
  try {
    const layout = await generateMossLayout(assetId, vlm);
    return {
      mossLayout: {
        layout: layout.layout,
        parts: layout.parts,
        model: layout.model,
        elapsed_s: layout.elapsed_s,
      },
    };
  } catch (e) {
    return { error: (e as Error).message };
  }
}
