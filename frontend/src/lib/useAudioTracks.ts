// 剧情对白音频化提交链（T2.2-C1 从 lib/useChatSession.ts 外移）。
//
// ▍搬移原则：只搬收口 —— 本文件 submitAudio / mergeAudioTracks 两个函数体与搬移前**逐字一致**
//   （同层级、零去缩进）；hook 闭包符号一律以**同名 deps** 解构注入（settings/templates/repo/
//   threadId/setMessages/messagesRef/onNotify/pollResult，共 8 名）⇒「行为面 = 搬移前」由
//   「逐字搬移锁 + 直断言行为网」两件互补证据承担（useAudioTracks.test.tsx）。
//
// ▍红线（防「顺手统一」，改动即破行为面）
//   ① 跳过闸逐字保留：无音频模板/模板不存在 ⇒ 静默 return；未配音轨角色只提示不阻断；
//      全部行不可配（resolvable 空）⇒ 同样只提示后 return；
//   ② 槽位编号 `audio-${messageId}-${index}` 与 seq/total（1-based / resolvable.length）逐字保留；
//   ③ pollResult 第 6 参必须显式 "audio"（IndexTTS 配音不受图片 20 分钟上限约束，P5 锁）；
//   ④ home 线程不补写后端快照槽（ensureAudioSlot 有 `threadId !== "home"` 闸）；
//   ⑤ 上传失败单角色 continue（不阻断其它角色）；提交失败/无 prompt_id ⇒ dropMediaSlot 摘槽；
//   ⑥ mergeAudioTracks 的「旧完整版保留 + 新完整版追加」是**既有行为**（重拼后旧 merged- 不消失，
//      同 slotId 并存）——只锁不修，修它属独立刀。

import { type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type { ChatMessage, RegenerationSnapshot } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings } from "../stores/settings";
import type { Template } from "../api/workflows";
import type { AudioDialogueLine } from "../api/chatStreamProtocol";
import { mergeAudio, submitWorkflow, localViewUrl, uploadImage } from "../api/comfyui";
import { ensureAudioSlot, reportAudioSubmission } from "../api/ai";
import {
  audioTemplateValues, resolvableAudioLines, skippedAudioSpeakers, voiceReferenceFor,
} from "./audioGeneration";
import { appendAudioSlot, dropMediaSlot } from "./chatSessionEvents";
import { resolveComfyUrl } from "./comfyMachines";
import { type PendingGeneration } from "./workflowGenerationRuntime";

/** 音频链的 hook 闭包依赖（T2.2-C1 同名 deps 注入；类型逐字抄 useChatSession 内声明）。 */
export interface AudioTracksDeps {
  settings: ReturnType<typeof useSettings>["settings"];
  templates: Template[];
  repo?: Repo;
  threadId: string;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  messagesRef: MutableRefObject<ChatMessage[]>;
  onNotify?: (msg: string, kind?: "info" | "error" | "success") => void;
  pollResult: (
    promptId: string,
    outputNodeIds?: string[],
    regeneration?: RegenerationSnapshot,
    target?: PendingGeneration["target"],
    prompt?: string,
    mediaType?: "image" | "video" | "audio",
    baseSlotRef?: PendingGeneration["baseSlotRef"],
    comfyuiUrlOverride?: string,
  ) => void;
}

export function useAudioTracks({
  settings, templates, repo, threadId, setMessages, messagesRef, onNotify, pollResult,
}: AudioTracksDeps) {
  // 剧情对白音频化：后端发来台词分段（含 8 维情感向量），逐角色提交 IndexTTS 工作流。
  // 每段台词独立一次运转（模型一次只认一个音色），完成后按角色分条聚合到楼层气泡。
  const submitAudio = async (lines: AudioDialogueLine[], messageId: string) => {
    const preset = settings.mediaInsert?.[repo?.id || ""];
    const audioTemplateId = preset?.audioTemplateId;
    if (!audioTemplateId) {
      console.warn("[auto-audio] 跳过：未配置音频模板", { repoId: repo?.id, mediaInsert: settings.mediaInsert });
      return;
    }
    const tpl = templates.find((t) => t.id === audioTemplateId);
    if (!tpl) {
      console.warn("[auto-audio] 跳过：模板不存在", { audioTemplateId, templates: templates.map((t) => t.id) });
      return;
    }
    // 未配置参考音轨的角色台词会静默跳过——明确提示一次，避免用户以为配音漏了
    const skipped = skippedAudioSpeakers(preset, lines);
    if (skipped.length > 0) {
      const configured = Object.keys(preset?.characterVoices || {});
      console.warn("[auto-audio] 跳过未配置音轨的角色", {
        skipped, configuredVoices: configured, lines: lines.map((l) => `${l.speaker}:${l.text}`),
      });
      onNotify?.(
        `未配置音轨，已跳过配音：${skipped.join("、")}（已配置：${configured.join("、") || "无"}，在「媒体插入」设置里为角色配置音色）`,
        "info",
      );
    }
    const resolvable = resolvableAudioLines(preset, lines);
    if (!resolvable.length) {
      console.warn("[auto-audio] 无可配音台词行", {
        lines: lines.map((l) => ({ speaker: l.speaker, text: l.text })),
      });
      return;
    }
    const outputNodeIds = tpl.primary_output_node_id ? [tpl.primary_output_node_id] : [];
    // 地址簿按模态选机（B 段）：上传音轨 / 提交 / 轮询三处必须是**同一台机**，
    // 未配路由时与 `settings.comfyuiUrl` 同值。
    const comfyAudioUrl = resolveComfyUrl(settings, "audio");

    let index = 0;
    for (const line of resolvable) {
      const seq = index + 1;
      const slotId = `audio-${messageId}-${index}`;
      index += 1;
      const voiceRef = voiceReferenceFor(preset, line.speaker)!;
      // 音轨先上传 ComfyUI input，取回 LoadAudio 引用的文件名
      let uploadedRef = "";
      try {
        const blob = await (await fetch(localViewUrl(voiceRef))).blob();
        const file = new File(
          [blob], voiceRef.split(/[\\/]/).pop() || "voice.wav",
          { type: blob.type || "audio/wav" },
        );
        uploadedRef = (await uploadImage(file, comfyAudioUrl)).name;
      } catch {
        console.error("[auto-audio]", { threadId, messageId, stage: "upload_voice", speaker: line.speaker, voiceRef });
        continue; // 单角色音轨上传失败不阻断其它角色
      }
      const values = audioTemplateValues(tpl.exposed, {
        text: line.text,
        reference: uploadedRef,
        emotion: line.emotion,
      });
      // 追加按角色分条的 audio 槽（生成中占位：角色名 + 第几条/总数），完成后由 pollResult 填充音频 URL。
      // appendAudioSlot 会把纯文本消息先转成 text part，避免正文被槽位顶掉（图片插槽同款处理）。
      setMessages((current) => appendAudioSlot(current, messageId, slotId, line.speaker, seq, resolvable.length));
      // 槽位补写后端快照：finalize 时 resolve_media_slot 需在快照里命中该 slot，
      // 否则 snapshotted=false → durableFinalizeSucceeded=false → 前端不填充 → 音频不在对话。
      if (threadId && threadId !== "home") {
        void ensureAudioSlot({
          threadId, messageId, slotId, speaker: line.speaker, seq, total: resolvable.length,
        }).catch(() => undefined);
      }
      try {
        const r = await submitWorkflow(audioTemplateId, values, comfyAudioUrl, line.text, [], "none");
        if (r.prompt_id) {
          console.info("[auto-audio]", {
            threadId, messageId, slotId, speaker: line.speaker,
            promptId: r.prompt_id, valueKeys: Object.keys(values).sort(),
          });
          // 持久化音频生成日志（对齐插画 submitted trace），便于排查音色/情感/台词
          void reportAudioSubmission({
            threadId, repoId: repo?.id || threadId, messageId, slotId,
            speaker: line.speaker, text: line.text, voiceRef,
            templateId: audioTemplateId, promptId: r.prompt_id,
            emotion: line.emotion, valueKeys: Object.keys(values).sort(),
          }).catch(() => undefined);
          pollResult(r.prompt_id, outputNodeIds, undefined, { messageId, slotId, background: true }, line.text, "audio", undefined, comfyAudioUrl);
        } else {
          console.error("[auto-audio]", { threadId, messageId, stage: "submit", speaker: line.speaker, error: "ComfyUI 没有返回任务 ID" });
          setMessages((current) => dropMediaSlot(current, messageId, slotId));
        }
      } catch (error) {
        console.error("[auto-audio]", { threadId, messageId, stage: "submit", speaker: line.speaker, error });
        setMessages((current) => dropMediaSlot(current, messageId, slotId));
      }
    }
  };

  // 音频分条按顺序拼接完整版：后端 ffmpeg concat + 落盘回写快照，
  // 成功后移除分条音频、只保留 merged（刷新后从快照恢复，仍只显示完整版）。
  const mergeAudioTracks = async (messageId: string) => {
    const msg = messagesRef.current.find((m) => m.id === messageId);
    const tracks = (msg?.parts || []).filter(
      (p) => p.type === "audio" && p.url && !(p.slotId || "").startsWith("merged-"),
    );
    if (tracks.length < 2) {
      console.warn("[merge-audio] 分条音频不足，跳过", { messageId, count: tracks.length });
      return;
    }
    // 已有旧完整版（如早前 -c copy 时长错误产物）时强制覆盖重拼
    const hasMerged = (msg?.parts || []).some(
      (p) => p.type === "audio" && (p.slotId || "").startsWith("merged-"),
    );
    try {
      const r = await mergeAudio({ threadId, messageId, force: hasMerged });
      if (r.ok && r.url) {
        setMessages((current) => current.map((m) => m.id === messageId ? {
          ...m,
          // 移除所有分条音频（type=audio 且非 merged-），只保留完整版一个
          parts: [
            ...(m.parts || []).filter(
              (p) => !(p.type === "audio" && !(p.slotId || "").startsWith("merged-")),
            ),
            {
              type: "audio" as const, url: r.url, slotId: `merged-${messageId}`,
              status: "ready" as const, kind: "audio" as const, speaker: "完整版",
            },
          ],
        } : m));
        onNotify?.("完整版音频已生成", "success");
      } else {
        console.error("[merge-audio] 后端返回异常", { threadId, messageId, r });
        onNotify?.("音频拼接失败", "error");
      }
    } catch (error) {
      console.error("[merge-audio]", { threadId, messageId, error });
      // 透出后端原因（如「拼接产物时长异常…已丢弃」）；分条未动 → 按钮仍在，可重试
      const reason = error instanceof Error && error.message ? error.message : "音频拼接失败";
      onNotify?.(reason, "error");
    }
  };
  return { submitAudio, mergeAudioTracks };
}
