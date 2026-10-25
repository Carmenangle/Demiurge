// RAG 知识库与资产库（原 api/ai.ts RAG 段，2026-10-24 按资源域拆分）：
// 入库（生成/文档）+ 列表/检索/结构化搜索 + 标签与快照 + 缩略图/视觉索引/描述 + 回收站 + 仓库同步对账 + 记录删改。
import { apiPost } from "../client";
import { ragEmbed, type Embed, type VlmWire } from "./wire";

// 生图完成后把这次生成的提示词/标签/图片入全局 RAG 知识库
// template_name / model_name / lora_names：生成溯源元数据（可选，2026-10-16 补；
// 不传时后端存空串 ⇒ 资产库详情的「模板/模型/LoRA」不显示）
export function indexGeneration(
  threadId: string,
  data: {
    prompt?: string; tags?: string; image_url?: string;
    template_name?: string; model_name?: string; lora_names?: string;
  },
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ ok: boolean }>("/rag/index-generation", {
    thread_id: threadId,
    prompt: data.prompt || "",
    tags: data.tags || "",
    image_url: data.image_url || "",
    template_name: data.template_name || "",
    model_name: data.model_name || "",
    lora_names: data.lora_names || "",
    ...ragEmbed(embed),
  });
}

// 手动上传参考资料入全局 RAG 知识库
export function indexDocument(
  threadId: string,
  text: string,
  title: string,
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ ok: boolean; chunks: number }>("/rag/index-document", {
    thread_id: threadId,
    text,
    title,
    ...ragEmbed(embed),
  });
}

// ⑤ 批量导入参考资料（从导出 JSON 恢复 / 迁移仓库）
export function importDocuments(
  threadId: string,
  docs: { text: string; title: string }[],
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ ok: boolean; documents: number; chunks: number }>("/rag/import-documents", {
    thread_id: threadId,
    docs,
    ...ragEmbed(embed),
  });
}

export interface RagDoc {
  id: string;
  content: string;
  kind: string;       // system | document | generation
  title: string;
  locked: boolean;    // 系统指令条目，不可删改
  image_url: string;
}

// 列出「系统库 + 本仓库库」所有条目（顺带幂等播种系统指令）
export function listDocs(repoId: string, embed: Embed) {
  return apiPost<{ items: RagDoc[] }>("/rag/list", {
    repo_id: repoId,
    ...ragEmbed(embed),
  });
}

export interface Generation {
  id: string;
  repo_id?: string;
  prompt: string;
  description?: string;
  image_url: string;
  tags: string[];
  /** 工作流生成元数据：模板名、主模型、LoRA（逗号分隔）。
      ⚠ 后端两个返回分支键名不一致：默认路径给 snake（`template_name`/`lora_names` 字符串），
      keyset 分页路径给 camel（`templateName`/`loraNames` 数组）⇒ 消费侧两个都要认。 */
  template_name?: string;
  model_name?: string;
  lora_names?: string;
  templateName?: string;
  modelName?: string;
  loraNames?: string[];
  created_at?: number;   // 入库毫秒时间戳（权威排序键；历史记录可能为 0/缺失）
  /** 标签快照（版本化反推，2026-10-17）：JSON 字符串 [{id,tags,model,label,at}] + 当前版 id */
  tag_snapshots?: string;
  active_tag_snapshot?: string;
  /** M2.1/M2.2：媒体类型（image|video），视频资产按 video 渲染/播放 */
  mediaType?: "image" | "video" | "audio";
  /** P0-2：缩略图派生层 URL（.thumbs/ 长边 512）；空则回落 image_url 原图 */
  thumbUrl?: string;
  /** M2.1：派生链弱引用（视频→首帧底图槽），只读展示不级联 */
  derivedFrom?: Array<{
    media_slot_ref?: { message_id: string; slot_id: string };
    asset_id?: string;
    turn_id?: string;
    kind?: string;
  }>;
  /** P2-1：素材资产来源（上网素材「落盘即资产记录」；generation 链为空/缺失） */
  source?: string;
  sourceUrl?: string;
  sourceMeta?: {
    author?: string;
    rating?: string;
    query?: string;
    provider?: string;
    size?: number;
    downloaded_at?: string;
  };
}

/**
 * 上网素材合成仓库 id（P2-1）——与后端 `rag_store.WEB_MATERIALS_REPO_ID` 同值。
 * 素材在 `outputDir/_web_materials/` 全局共享、不属于任何真实仓库 ⇒ 资产记录统一落
 * 这个作用域；资产库（AssetsView）把它并入 repoIds 聚合展示，标签统计/语义搜索/
 * 视觉索引/WD14 反推全链路复用。⚠ 它不是真仓库，不进仓库注册表/选择器。
 */
export const WEB_MATERIALS_REPO_ID = "web-materials";

// 列出生成记录（图片+提示词+标签），供仓库详情页图片网格
export function listGenerations(
  repoId: string | string[],
  embed: Embed,
  opts: { limit?: number; beforeTs?: number; offset?: number; mediaType?: string } = {},
) {
  // P0-1 分页：limit>0 时后端按 created_at 倒序截断并回 has_more + total。
  // - beforeTs：上一页末条 created_at（keyset，新增图不打乱已有页）
  // - offset：按偏移取（2026-10-19：分页条要「共 N 页」并直达任意页；只靠游标算不出总页数）
  // - mediaType：媒体类型**下推 SQL**（此前只在前端过滤已加载项 ⇒ 聚合页翻页不取更多）
  // 三者缺省=全量（旧行为）。传数组 ⇒ 后端多仓库合并分页（资产库聚合页）。
  const multi = Array.isArray(repoId);
  return apiPost<{ items: Generation[]; has_more?: boolean; total?: number }>("/rag/generations", {
    ...(multi ? { repo_ids: repoId } : { repo_id: repoId }),
    limit: opts.limit ?? 0,
    before_ts: opts.beforeTs ?? 0,
    offset: opts.offset ?? 0,
    media_type: opts.mediaType ?? "",
    ...ragEmbed(embed),
  });
}

export function wd14Tag(id: string, repoId: string, embed: Embed) {
  // P0-3：WD14 本地反推打标（合并进 tags，不动原有）；模型未安装时后端 503 附下载指引
  return apiPost<{ ok: boolean; added: string[]; total: number }>("/rag/wd14-tag", {
    id,
    repo_id: repoId,
    ...ragEmbed(embed),
  });
}

// 可插拔打标器：列出可用模型（内置 WD14 家族 + 丢在 models/taggers/ 下的自定义）
export type TaggerInfo = { id: string; name: string; family: string; available: boolean; source: string };
export function listTaggers() {
  return apiPost<{ items: TaggerInfo[]; default: string }>("/rag/taggers", {});
}

// 按指定打标器反推（model 空＝默认）。结果已合并落库。
export function tagGeneration(model: string, id: string, repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean; model: string; snapshot_id: string; tags: string[]; total: number }>(
    "/rag/tag", {
      id,
      repo_id: repoId,
      model,
      ...ragEmbed(embed),
    },
  );
}

// 标签快照（版本化）：每次反推存一版，可切换/删除
export type TagSnapshot = { id: string; tags: string[]; model: string; label: string; at: number };
export function listTagSnapshots(id: string, repoId: string, embed: Embed) {
  return apiPost<{ snapshots: TagSnapshot[]; active: string }>("/rag/tag-snapshots", {
    id, repo_id: repoId, ...ragEmbed(embed),
  });
}
export function activateTagSnapshot(id: string, repoId: string, snapshotId: string, embed: Embed) {
  return apiPost<{ ok: boolean }>("/rag/tag-snapshot/activate", {
    id, repo_id: repoId, snapshot_id: snapshotId, ...ragEmbed(embed),
  });
}
export function deleteTagSnapshot(id: string, repoId: string, snapshotId: string, embed: Embed) {
  return apiPost<{ ok: boolean }>("/rag/tag-snapshot/delete", {
    id, repo_id: repoId, snapshot_id: snapshotId, ...ragEmbed(embed),
  });
}

export function generateThumbs(repoIds: string | string[], embed: Embed) {
  // P0-2：为仓库历史资产补生成缩略图（.thumbs/ 派生层），返回 generated/skipped。
  // 2026-10-18：支持多仓库一次跑（repo_ids）⇒ 后端聚合成一个后台任务总进度。
  return apiPost<{ generated: number; skipped: number; job_id: string }>("/rag/generate-thumbs", {
    repo_ids: Array.isArray(repoIds) ? repoIds : [repoIds],
    ...ragEmbed(embed),
  });
}

export function searchGenerations(
  repoIds: string[], query: string, embed: Embed, k = 64, outputDir = "",
) {
  return apiPost<{ items: Generation[] }>("/rag/search-generations", {
    repo_ids: repoIds, query, k, output_dir: outputDir, ...ragEmbed(embed),
  });
}

export type VisualPreferenceReason =
  "character" | "action" | "composition" | "lighting" | "color" | "quality" | "other";

export function recordVisualPreference(
  outputDir: string, repoId: string, winnerId: string, loserId: string,
  reason: VisualPreferenceReason,
) {
  return apiPost<{ ok: boolean; winner_score: number; loser_score: number }>(
    "/rag/visual-preference",
    { output_dir: outputDir, repo_id: repoId, winner_id: winnerId, loser_id: loserId, reason },
  );
}

export function setGenerationDescription(
  id: string, repoId: string, description: string, embed: Embed,
) {
  return apiPost<{ ok: boolean }>("/rag/set-generation-description", {
    id, repo_id: repoId, description, ...ragEmbed(embed),
  });
}

export function indexVisualGenerations(repoIds: string | string[], embed: Embed) {
  // 2026-10-18：支持多仓库一次跑（repo_ids），后端登记为一个后台任务、面板显示 1/157 进度
  return apiPost<{ ok: boolean; indexed: number; skipped: number; job_id: string }>(
    "/rag/index-visual-generations", {
      repo_ids: Array.isArray(repoIds) ? repoIds : [repoIds], ...ragEmbed(embed),
    },
  );
}

export function describeMissingAssets(
  repoIds: string[], vlm: VlmWire, embed: Embed, hint = "",
) {
  // 2026-10-18：批量补视觉描述搬到后端做后台任务——原浏览器端实现只覆盖「已加载条目」、
  // 切页/刷新即断、且没有持久化进度。这里一次传全部 repo_ids ⇒ 面板显示 1/318 进度条。
  return apiPost<{ ok: boolean; job_id: string; total: number }>("/rag/describe-missing", {
    repo_ids: repoIds, vlm, hint, ...ragEmbed(embed),
  });
}

// 清理僵尸记录：指向本机留存图但磁盘文件已不存在的条目（手动删文件留下的裂图）。返回删除条数。
export function pruneGenerations(repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean; removed: number }>("/rag/prune-generations", {
    repo_id: repoId,
    ...ragEmbed(embed),
  });
}

/** 孤儿仓库清理（2026-10-18）：把「有记录但不在注册表、也非合成 repo」的仓库也纳入 prune，
 *  只删磁盘文件已缺失的裂图记录。返回孤儿仓库列表与清理条数。 */
export function pruneOrphanRepos(embed: Embed) {
  return apiPost<{ orphan_repos: string[]; removed: number; per_repo: Record<string, number> }>(
    "/rag/prune-orphan-repos", { ...ragEmbed(embed) },
  );
}

// ===== 仓库同步（P3 ⑤，2026-10-18）：对账「注册表/磁盘/索引」三方 =====
export interface RepoSyncRepo {
  repo_id: string; name: string; folder_exists: boolean;
  disk: number; indexed: number; unindexed: string[]; missing: string[];
}
export interface RepoSyncReport {
  repos: RepoSyncRepo[]; orphan_index_repos: string[];
  total_unindexed: number; total_missing: number;
}

/** 同步体检（只读）：报告漂移，不改数据。 */
export function repoSyncReport(outputDir: string, embed: Embed) {
  return apiPost<RepoSyncReport>("/rag/repo-sync-report", {
    output_dir: outputDir, ...ragEmbed(embed),
  });
}

/** 同步修复（保守）：补录未入库文件 + 清理裂图/孤儿。 */
export function repoSyncFix(outputDir: string, embed: Embed) {
  return apiPost<{ indexed: number; pruned: number }>("/rag/repo-sync-fix", {
    output_dir: outputDir, ...ragEmbed(embed),
  });
}

// ===== P1-3 结构化搜索（FTS5 + asset_tags）＋ P1-2 回收站（2026-10-18） =====

/** 结构化搜索：tag 精确 AND + 自由文本（FTS5，中文子串 LIKE 兜底）。语义搜索另走 searchGenerations。 */
export function searchAssets(
  repoIds: string[],
  embed: Embed,
  opts: { q?: string; tags?: string[]; mediaType?: string; limit?: number; beforeTs?: number } = {},
) {
  return apiPost<{ items: Generation[]; has_more?: boolean }>("/rag/search-assets", {
    repo_ids: repoIds,
    q: opts.q ?? "",
    tags: opts.tags ?? [],
    media_type: opts.mediaType ?? "",
    limit: opts.limit ?? 0,
    before_ts: opts.beforeTs ?? 0,
    ...ragEmbed(embed),
  });
}

/** 回收站列表（软删除记录，按删除时间倒序）。 */
export function listTrash(repoIds: string[], embed: Embed) {
  return apiPost<{ items: Generation[] }>("/rag/assets/trash", {
    repo_ids: repoIds,
    ...ragEmbed(embed),
  });
}

/** 从回收站恢复（后端先重建向量成功再清 deleted_at）。 */
export function restoreAsset(id: string, repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean; id?: string; already?: boolean }>("/rag/assets/restore", {
    id, repo_id: repoId, ...ragEmbed(embed),
  });
}

/** 彻底删除（只允许回收站内记录）。 */
export function purgeAsset(id: string, repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean }>("/rag/assets/purge", {
    id, repo_id: repoId, ...ragEmbed(embed),
  });
}

/**
 * 两段式反推落库（2026-10-18）：第一段 tag 行 → 新标签快照；第二段描述 → 资产描述。
 * 视觉推理由前端发起（对话模型），后端只解析+落库（切分规则单一真源）。
 */
export function tagFromText(
  id: string,
  repoId: string,
  embed: Embed,
  text: string,
  opts: { model?: string; label?: string; saveDescription?: boolean } = {},
) {
  return apiPost<{
    ok: boolean; snapshot_id: string; tags: string[]; total: number;
    description: string; saved_description: boolean;
  }>("/rag/tag-text", {
    id,
    repo_id: repoId,
    text,
    model: opts.model ?? "",
    label: opts.label ?? "",
    save_description: opts.saveDescription ?? true,
    ...ragEmbed(embed),
  });
}

/** 清空回收站（repoIds 给范围；空数组＝全部仓库）。 */
export function clearTrash(repoIds: string[], embed: Embed) {
  return apiPost<{ removed: number }>("/rag/assets/trash-clear", {
    repo_ids: repoIds,
    ...ragEmbed(embed),
  });
}

// 聚合仓库集合的标签→图片数量（按量降序），供加标签/搜索的输入补全
export interface TagStat { tag: string; count: number; }
export function tagStats(repoIds: string[], embed: Embed) {
  return apiPost<{ items: TagStat[] }>("/rag/tag-stats", {
    repo_ids: repoIds,
    ...ragEmbed(embed),
  });
}

// 覆盖某资产条目的标签（手动增删 / AI 打标落库）
export function setTags(id: string, repoId: string, tags: string[], embed: Embed) {
  return apiPost<{ ok: boolean }>("/rag/set-tags", {
    id, repo_id: repoId, tags,
    ...ragEmbed(embed),
  });
}

// 删除单条（系统条目后端会拒绝）
export function deleteDoc(id: string, repoId: string, embed: Embed, removeFile = false) {
  return apiPost<{ ok: boolean }>("/rag/delete", {
    id, repo_id: repoId, remove_file: removeFile,
    ...ragEmbed(embed),
  });
}

// 编辑单条（系统条目后端会拒绝）
export function updateDoc(id: string, text: string, title: string, repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean }>("/rag/update", {
    id, text, title, repo_id: repoId,
    ...ragEmbed(embed),
  });
}
