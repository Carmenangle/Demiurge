// 输入框草稿存档（2026-09-21 用户需求）。
//
// ▍要解决的问题
//   用户在输入框里写了一半（可能还插了图片 / 文档 / 灵感卡），**还没发出**就：
//     · 切到别的仓库 → 再切回来（ChatView 按 `key={activeWork.id}` 整体重挂，草稿随组件销毁）
//     · 页面刷新（Vite dev 整页重载 / 浏览器刷新 / 崩溃恢复）
//   内容应当原样回填，而不是重新手打。
//
// ▍键
//   `laf_draft_<threadId>`，threadId 即对话线 id（= 仓库/作品 id，见 workflowGenerationRuntime.threadKey）。
//   首页（`home`）是**临时草稿区**（见 lib/homeDraft.ts 的既定口径：进程内保留、刷新即空），
//   因此这里对 `home` 直接跳过——不落 localStorage，保持既有语义。
//
// ▍为什么不是「600ms 防抖单写」
//   刷新是**硬中断**：定时器可能还没到点进程就没了。所以写盘走两条腿——
//   ① 输入变化后短防抖（300ms，避免每次按键都序列化 dataURI 大图）；
//   ② `pagehide` / `visibilitychange(hidden)` 立即 flush（刷新、关标签、切到后台都覆盖）。
//   两腿合起来：正常打字靠 ①，硬中断靠 ②。
//
// ▍配额
//   图片是 dataURI，可上 MB，而 localStorage 通常只有 ~5MB。整包写失败（多为 QuotaExceededError）时
//   **降级重写一次**「文字 + 附件元信息 + 灵感卡文本」，置 `heavyDropped=true`，由调用方提示用户——
//   既不静默丢草稿，也不因为一张大图把文字一起赔进去。

import type { FileAttachmentMeta } from "../api/ai";
import type { InspirationAttachment } from "./inspirationInsert";
import type { MaskedImageInput } from "../components/RichInput";

export interface InputDraft {
  text: string;
  images: string[];
  inspirationAttachments: InspirationAttachment[];
  maskedImage: MaskedImageInput | null;
  media: FileAttachmentMeta[];
  attachments: FileAttachmentMeta[];
  /** 落盘时间戳（仅供诊断/排序，不参与回填判定） */
  updatedAt: number;
  /** true = 因配额不足走了降级落盘：图片 / 蒙版 / 灵感卡配图未存档 */
  heavyDropped?: boolean;
}

export const INPUT_DRAFT_PREFIX = "laf_draft_";
/** 首页线程 id = 临时草稿区，不落盘（与 lib/homeDraft.ts 同口径）。 */
export const HOME_THREAD_ID = "home";

type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** 草稿键：`home` / 空 ⇒ null（调用方据此跳过全部读写）。 */
export function inputDraftKey(threadId?: string | null): string | null {
  const id = (threadId || "").trim();
  if (!id || id === HOME_THREAD_ID) return null;
  return `${INPUT_DRAFT_PREFIX}${id}`;
}

/**
 * 附件白名单：**上传中占位**（fileId=`pending-…`）不得存档。
 * 刷新后那次上传已经断了，恢复出来只会是一张「不可用」的空卡（与 doSubmit 的 pending 过滤同口径）。
 */
function settledAttachments(value: unknown): FileAttachmentMeta[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is FileAttachmentMeta => {
    const meta = entry as FileAttachmentMeta | null;
    return !!meta && typeof meta.fileId === "string" && !!meta.fileId && !meta.fileId.startsWith("pending-");
  });
}

function isMaskedImage(value: unknown): value is MaskedImageInput {
  const m = value as MaskedImageInput | null;
  return !!m && typeof m.image === "string" && typeof m.mask === "string" && typeof m.preview === "string";
}

/** 草稿是否「没东西可存」：全空 ⇒ 不写盘（并把旧键清掉）。 */
export function isInputDraftEmpty(draft: InputDraft | null | undefined): boolean {
  if (!draft) return true;
  return !draft.text.trim()
    && draft.images.length === 0
    && draft.inspirationAttachments.length === 0
    && !draft.maskedImage
    && draft.media.length === 0
    && draft.attachments.length === 0;
}

/** 读草稿：缺键 / 损坏 / 全空一律返回 null（**不抛**，坏数据不能拦住输入框挂载）。 */
export function readInputDraft(
  threadId?: string | null,
  storage: DraftStorage = localStorage,
): InputDraft | null {
  const key = inputDraftKey(threadId);
  if (!key) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<InputDraft> | null;
    if (!parsed || typeof parsed !== "object") return null;
    const draft: InputDraft = {
      text: typeof parsed.text === "string" ? parsed.text : "",
      images: Array.isArray(parsed.images) ? parsed.images.filter((u): u is string => typeof u === "string") : [],
      inspirationAttachments: Array.isArray(parsed.inspirationAttachments)
        ? (parsed.inspirationAttachments as InspirationAttachment[])
        : [],
      maskedImage: isMaskedImage(parsed.maskedImage) ? parsed.maskedImage : null,
      media: settledAttachments(parsed.media),
      attachments: settledAttachments(parsed.attachments),
      updatedAt: typeof parsed.updatedAt === "number" ? parsed.updatedAt : 0,
      ...(parsed.heavyDropped === true ? { heavyDropped: true } : {}),
    };
    return isInputDraftEmpty(draft) ? null : draft;
  } catch {
    return null;
  }
}

export type DraftWriteResult = "skipped" | "cleared" | "saved" | "degraded";

/** 降级副本：只留文字 + 附件元信息 + 灵感卡文本，丢掉 dataURI 重字段。 */
function slimDraft(draft: InputDraft): InputDraft {
  return {
    ...draft,
    images: [],
    maskedImage: null,
    inspirationAttachments: draft.inspirationAttachments.map((card) => ({
      id: card.id,
      title: card.title,
      content: card.content,
      imageUrl: "",
      sourceUrl: "",
      imageUrls: [],
    })),
    heavyDropped: true,
  };
}

/**
 * 写草稿。
 *  · `skipped`  —— 首页/空线程键（不落盘）
 *  · `cleared`  —— 草稿已全空 ⇒ 删键
 *  · `saved`    —— 整包写入成功
 *  · `degraded` —— 整包写失败（多为配额）⇒ 降级只写轻字段成功，**调用方应提示用户**
 */
export function writeInputDraft(
  threadId: string | undefined | null,
  draft: InputDraft,
  storage: DraftStorage = localStorage,
): DraftWriteResult {
  const key = inputDraftKey(threadId);
  if (!key) return "skipped";
  if (isInputDraftEmpty(draft)) {
    try { storage.removeItem(key); } catch { /* 删不掉也不影响：下次写入会覆盖 */ }
    return "cleared";
  }
  try {
    storage.setItem(key, JSON.stringify(draft));
    return "saved";
  } catch {
    try {
      storage.setItem(key, JSON.stringify(slimDraft(draft)));
      return "degraded";
    } catch {
      return "skipped";
    }
  }
}

/** 删除草稿（发送成功后调用；也用于显式清空）。 */
export function clearInputDraft(threadId?: string | null, storage: DraftStorage = localStorage): void {
  const key = inputDraftKey(threadId);
  if (!key) return;
  try { storage.removeItem(key); } catch { /* ignore */ }
}
