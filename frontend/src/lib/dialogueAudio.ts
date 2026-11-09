// 第 100 刀（2026-11-08）：把本轮 TTS 分条音频拼成整条，交给 Ref2VA 的参考音频槽。
//
// 用户口径：剧情对白是一句一句生成、按顺序拼接的；视频模型要的是「整条角色对话录音」
// 作参考音轨（提示词里的 `<Audio 1>`）。哪句落在哪个镜头由后端 ④ 对白时间轴承载，
// 与本文件无关；这里只负责：等分条落定 → 后端 ffmpeg 按 seq 拼接 → 上传到视频机 input。
//
// 边界：整条音频只是**参考音轨**，不是成片声轨；等待超时/拼接失败/上传失败一律交空串，
// 调用方据此不带音频提交（绝不挂死视频槽），也绝不把引用 `<Audio 1>` 的对白块塞进提示词。

import type { MsgPart } from "../types/chat";

const MERGED_PREFIX = "merged-";
const DEFAULT_FILENAME = "dialogue.flac";

/** 分条等待上限：与桥视频「上一张画面」等待同量级（5 分钟）。 */
export const DIALOGUE_AUDIO_WAIT_MS = 300_000;
/** 一点音轨都没有时的宽限：audio_request 在事件流里排在 illustrate_request 之后。 */
const GRACE_MS = 8_000;
const TICK_MS = 1_000;
/** 分条数量/状态稳定这么久才认为「全部落定」（后一条槽的 append 可能晚于前一条成片）。 */
const SETTLE_MS = 1_500;

export interface DialogueAudioSnapshot {
  /** 分条音频 url（排除 merged-），按 seq 升序。 */
  perLine: string[];
  /** 已存在的整条 url（slotId 以 merged- 开头）。 */
  merged: string;
  /** 仍在生成中的分条数（media-slot + kind=audio + 未失败）。 */
  pending: number;
}

export function inspectDialogueAudio(parts: readonly MsgPart[]): DialogueAudioSnapshot {
  const perLine: { seq: number; url: string }[] = [];
  let merged = "";
  let pending = 0;
  for (const part of parts) {
    if (part.type === "media-slot" && part.kind === "audio") {
      if (part.status !== "failed") pending += 1;
      continue;
    }
    if (part.type !== "audio" || !part.url) continue;
    if ((part.slotId || "").startsWith(MERGED_PREFIX)) {
      merged = part.url;
      continue;
    }
    perLine.push({ seq: typeof part.seq === "number" ? part.seq : 0, url: part.url });
  }
  perLine.sort((left, right) => left.seq - right.seq);
  return { perLine: perLine.map((item) => item.url), merged, pending };
}

export type DialogueAudioChoice =
  | { kind: "per_line_single"; url: string }
  | { kind: "merge" }
  | { kind: "merged"; url: string }
  | { kind: "none" };

/** 拼接策略：≥2 条分条 ⇒ 后端拼；1 条 ⇒ 它就是整段；只剩整条 ⇒ 直接复用。 */
export function chooseDialogueAudio(state: DialogueAudioSnapshot): DialogueAudioChoice {
  if (state.perLine.length >= 2) return { kind: "merge" };
  if (state.perLine.length === 1) return { kind: "per_line_single", url: state.perLine[0] };
  if (state.merged) return { kind: "merged", url: state.merged };
  return { kind: "none" };
}

/** 等分条音频落定（不再有 pending 且数量稳定）。无音轨时只等宽限窗口，绝不无限等。 */
export async function waitForDialogueAudioReady(
  readParts: () => readonly MsgPart[],
  opts: { timeoutMs?: number; graceMs?: number; tickMs?: number; settleMs?: number } = {},
): Promise<DialogueAudioSnapshot> {
  const timeoutMs = opts.timeoutMs ?? DIALOGUE_AUDIO_WAIT_MS;
  const graceMs = opts.graceMs ?? GRACE_MS;
  const tickMs = opts.tickMs ?? TICK_MS;
  const settleMs = opts.settleMs ?? SETTLE_MS;
  const deadline = Date.now() + timeoutMs;
  const graceDeadline = Date.now() + graceMs;
  let signature = "";
  let lastChangeAt = Date.now();
  for (;;) {
    const state = inspectDialogueAudio(readParts());
    const next = `${state.perLine.length}|${state.pending}|${state.merged ? 1 : 0}`;
    if (next !== signature) {
      signature = next;
      lastChangeAt = Date.now();
    }
    const hasAny = state.perLine.length > 0 || !!state.merged;
    if (hasAny && state.pending === 0 && Date.now() - lastChangeAt >= settleMs) return state;
    if (!hasAny && Date.now() >= graceDeadline) return state;
    if (Date.now() >= deadline) return state;
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

export interface PrepareDialogueAudioArgs {
  readParts: () => readonly MsgPart[];
  /** 后端 ffmpeg 拼接（force=true 覆盖旧产物），返回整条 url。 */
  mergeMerged: (force: boolean) => Promise<string>;
  fetchBlob: (url: string) => Promise<Blob>;
  /** 上传到视频机 input，返回 LoadAudio 可引用的文件名。 */
  upload: (blob: Blob, filename: string) => Promise<string>;
  warn: (message: string, detail?: unknown) => void;
  wait?: Parameters<typeof waitForDialogueAudioReady>[1];
}

/** 从 local-view / 裸路径里取真实文件名（`path` 查询参数优先）。 */
export function dialogueAudioFilename(url: string): string {
  const queryAt = url.indexOf("?");
  if (queryAt >= 0) {
    for (const pair of url.slice(queryAt + 1).split("&")) {
      const [key, value = ""] = pair.split("=");
      if (key !== "path" && key !== "filename") continue;
      try {
        const name = decodeURIComponent(value).split(/[\\/]/).pop();
        if (name) return name;
      } catch { /* 坏编码交下面的兜底 */ }
      break;
    }
  }
  try {
    const name = decodeURIComponent(url.split(/[?#]/)[0].split(/[\\/]/).pop() || "");
    if (name) return name;
  } catch { /* 兜底默认名 */ }
  return DEFAULT_FILENAME;
}

/** 拼好并上传整条对白音频，返回 ComfyUI input 文件名；任何失败都返回空串。
 *  `jobs` 由调用方持有（同一条消息 + 同一台视频机只做一次拼接/上传）。 */
export function prepareDialogueAudio(
  args: PrepareDialogueAudioArgs,
  jobs: Map<string, Promise<string>>,
  key: string,
): Promise<string> {
  const cached = jobs.get(key);
  if (cached) return cached;
  const job = (async () => {
    const state = await waitForDialogueAudioReady(args.readParts, args.wait);
    const choice = chooseDialogueAudio(state);
    let url = "";
    if (choice.kind === "per_line_single" || choice.kind === "merged") {
      url = choice.url;
    } else if (choice.kind === "merge") {
      try {
        url = await args.mergeMerged(true);
      } catch (error) {
        args.warn("对白音频拼接失败，改用已有整条", error);
        url = state.merged || await args.mergeMerged(false).catch(() => "");
      }
    }
    if (!url) {
      args.warn("对白音频未就绪，本次视频不带参考音频");
      return "";
    }
    const blob = await args.fetchBlob(url);
    return await args.upload(blob, dialogueAudioFilename(url));
  })().catch((error) => {
    args.warn("对白音频上传失败，本次视频不带参考音频", error);
    return "";
  });
  // 失败不缓存（下一槽可能刚好等到音轨）；成功保留：同轮多槽复用同一个上传文件名。
  const tracked = job.then((name) => {
    if (!name) jobs.delete(key);
    return name;
  });
  jobs.set(key, tracked);
  return tracked;
}
