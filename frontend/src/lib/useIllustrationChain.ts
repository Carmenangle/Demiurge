// 自动插画/视频提交链（T2.2-A 从 lib/useChatSession.ts 外移）。
//
// ▍搬移原则：只搬收口 —— 本文件 submitIllustration 函数体与搬移前**逐字一致**（同层级、零去缩进）；
//   需要的 hook 闭包符号一律以**同名 deps** 解构注入（settings/templates/repo/threadId/cardNames/
//   promptProfile/characterBaseImages/chat/modelProxies/effectivePersona/messagesRef/setMessages/
//   pollResult/discardFailedIllustration，共 14 名）⇒「行为面 = 搬移前」由「逐字搬移锁 + 直断言行为网」
//   两件互补证据承担（useIllustrationChain.test.tsx）。
//
// ▍红线（防「顺手统一」，改动即破行为面）
//   ① 编译链判据逐字保留：manual **必须重编译**（storedProfileUsable 含 `source !== "manual"`）；
//      编译失败禁止把中文帧描述原文提交给 ComfyUI（compileFramePrompt 显式 throw）；
//   ② pollResult 7 参接线逐字保留：主槽 `useVideo ? "video" : "image"` + baseSlotRef（视频 derived_from）；
//   ③ 认领重试（自动路径两轮 + saveSnapshot 刷新）、提交自愈（interruptComfy 后重提一次，上限 1 次）
//      的次数与顺序逐字保留；`IllustrationSubmit` 参数合同留在 useChatSession.ts（跨层契约，勿迁）。

import { type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type { ChatMessage, RegenerationSnapshot } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings } from "../stores/settings";
import { activeUserPersona } from "../stores/settings";
import type { Template } from "../api/workflows";
import type { Model, IllustrationSubmit } from "./useChatSession";
import {
  comfyStatus, submitWorkflow, interruptComfy, uploadImage, localViewUrl,
  moveComfyOutputToInput, uploadRemoteImageToInput,
} from "../api/comfyui";
import { listLoras } from "../api/loras";
import {
  saveSnapshot, claimIllustrationSubmission, genFramePrompts, genProfilePrompt,
  reportIllustrationSubmission,
} from "../api/ai";
import {
  applyProfileLoraTriggers, ensureAnimaIllustrationStyle, latentSizeFor,
  replacePromptQualityLine, workflowFieldBinding, illustrationTemplateValues,
  normalizePromptProfile,
} from "./imagePromptProfiles";
import {
  illustrationLoraConfigurationError, illustrationWorkflowMedia, resolveIllustrationActors,
  resolveVideoMode, resolveVideoTemplateChoice, planFirstlastFrameTasks, firstlastFrameValues,
  firstlastSlotLayout, transitionVideoValues, illustrationRequestMedia,
} from "./illustrationMedia";
import {
  resolveLoraPromptMetadata, resolveVideoBaseImageRef, resolveTransitionBaseImage, resolvePrevTailDesc,
} from "./chatGeneration";
import { templateRegenerationSnapshot } from "./regeneration";
import { pollWorkflowResult, type PendingGeneration } from "./workflowGenerationRuntime";
import { appendImageSlot, appendTransitionSlot, bindMediaSlotPrompt, resolveMediaSlot } from "./chatSessionEvents";

/** 插画链的 hook 闭包依赖（T2.2-A 同名 deps 注入；类型逐字抄 useChatSession 内声明）。 */
export interface IllustrationChainDeps {
  settings: ReturnType<typeof useSettings>["settings"];
  templates: Template[];
  repo?: Repo;
  threadId: string;
  cardNames: string[];
  promptProfile: ReturnType<typeof normalizePromptProfile>;
  characterBaseImages: ReturnType<typeof illustrationRequestMedia>["characterBaseImages"];
  chat: Model;
  modelProxies: { chatProxyUrl: string; genProxyUrl: string; videoProxyUrl: string; embedProxyUrl: string };
  effectivePersona: NonNullable<ReturnType<typeof activeUserPersona>>;
  messagesRef: MutableRefObject<ChatMessage[]>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  pollResult: (
    promptId: string,
    outputNodeIds?: string[],
    regeneration?: RegenerationSnapshot,
    target?: PendingGeneration["target"],
    prompt?: string,
    mediaType?: "image" | "video" | "audio",
    baseSlotRef?: PendingGeneration["baseSlotRef"],
  ) => void;
  discardFailedIllustration: (
    messageId: string, slotId: string, stage: string, error: string, promptId?: string, retryArgs?: unknown,
  ) => void;
}

// 第 58 刀（2026-09-24）：999 模式 12 个槽**同时** POST /comfyui/submit —— ComfyUI
// 首个任务触发模型加载、其余在队列里等，5/12 张撞上 60s 提交超时（真机实锤）。
// ⇒ 提交**串行化**：同一时刻只有一次提交在途（模型加载只发生一次，后续提交秒回）。
let _submitQueue: Promise<unknown> = Promise.resolve();
function enqueueSubmit<T>(run: () => Promise<T>): Promise<T> {
  const chained = _submitQueue.then(run, run);
  _submitQueue = chained.catch(() => undefined);
  return chained;
}

export function useIllustrationChain({
  settings, templates, repo, threadId, cardNames, promptProfile, characterBaseImages,
  chat, modelProxies, effectivePersona, messagesRef, setMessages,
  pollResult, discardFailedIllustration,
}: IllustrationChainDeps) {
  // 剧情高潮点异步出图/出视频：后端发来 booru 提示词 + motion 动态强度，按本作品预设组 values 提交 ComfyUI，
  // 走 pollResult 现成异步闭环（徽记显示进度 + 离开继续 + 点击返回）。未预设模板 → 静默跳过。
  // 智能模态：smartVideo 开 且 motion>=2 且 预设了视频模板 → 用视频模板，否则用图片模板。
  const submitIllustration = async (input: IllustrationSubmit) => {
    // 解构带默认值：函数体沿用局部名，签名收成对象后调用点不必再数位置（审计 C7）。
    const {
      prompt: promptIn, motion = 0, actors = [], messageId, slotId, sceneSpec, turnId = "",
      source = "automatic", eventVideoMode, firstFrameDesc = "", lastFrameDesc = "",
      prevTailDesc = "", lastFrameUrl = "", videoPrompt = "", transition = "",
      transitionVideoPrompt = "", transitionVideoParams,
    } = input;
    // 入参 prompt 是原始请求值（不改）；编译链会在下文按 Profile 重写 → 另起可变局部承接。
    let prompt = promptIn;
    // 重试参数快照 = 本对象（默认值已落定、按名取用）：失败槽「重新生成」按钮按名重调，
    // 不再按签名序存数组、更不用魔术下标翻 source。prompt 存**原始入参**（重证时重编译）。
    const retrySnapshot: IllustrationSubmit = {
      prompt: promptIn, motion, actors, messageId, slotId, sceneSpec, turnId, source,
      eventVideoMode, firstFrameDesc, lastFrameDesc, prevTailDesc, lastFrameUrl,
      videoPrompt, transition, transitionVideoPrompt, transitionVideoParams,
    };
    const failSlot = (stage: string, error: string) =>
      discardFailedIllustration(messageId, slotId, stage, error, "", retrySnapshot);
    const preset = settings.mediaInsert?.[repo?.id || ""];
    if (!preset) { failSlot("configuration", "当前作品没有配置自动插画模板"); return; }
    if (source === "automatic") {
      // 防抖快照可能尚未把 pending 槽落库，认领在快照里找不到槽会返回 False。
      // 2026-08-31 晚实锤「新的任务请求没有生图」：认领失败曾静默 return → 槽永远
      // pending、无 submitted/failed 可查。现在刷新快照后重试一次，仍失败就把槽
      // 标记为失败（可见 + 可重新生成），绝不再静默。
      await saveSnapshot(threadId, messagesRef.current).catch(() => undefined);
      let claim = { claimed: false };
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          claim = await claimIllustrationSubmission({ threadId, messageId, slotId });
          if (claim.claimed) break;
        } catch (error) {
          failSlot("submission_claim", error instanceof Error ? error.message : "自动插画提交认领失败");
          return;
        }
        if (attempt === 0) {
          await new Promise((resolve) => setTimeout(resolve, 400));
          await saveSnapshot(threadId, messagesRef.current).catch(() => undefined);
        }
      }
      if (!claim.claimed) {
        failSlot("submission_claim", "自动插画提交认领失败（槽位未就绪或已被认领）");
        return;
      }
    }
    if (!prompt.trim() && !sceneSpec?.narrative.trim()) {
      failSlot("prompt", "剧情出图提示词为空");
      return;
    }
    // V1.5/B1：事件 videoMode 优先，其次 preset.videoMode，缺省 climax
    const videoMode = resolveVideoMode(preset, eventVideoMode);
    // V1.5/B3（R4）：firstlast 楼层触发不看 motion；climax 维持 smartVideo && motion>=2
    const useVideo = resolveVideoTemplateChoice(preset, videoMode, motion);
    // V1.6/P5+（2026-08-28 用户拍板）：首尾帧是独立图片模式——只配图片模板即生效；
    // 视频模式跟随该选项推导，有视频模板才追加视频任务。
    const useFirstlastImages = videoMode === "firstlast" && !!preset.templateId;
    const chosenId = useVideo ? preset.videoTemplateId! : preset.templateId;
    if (!chosenId) {
      failSlot("configuration", "当前图/视频模式没有配置工作流模板");
      return;
    }
    const tpl = templates.find((t) => t.id === chosenId);
    if (!tpl) {
      failSlot("configuration", "已保存的工作流模板不存在，请重新选择模板");
      return;
    }
    // ⑥ 按高潮真实角色挑 LoRA/底图。single 命中时串联角色 LoRA，无命中才回退风格；
    // multi 固定加载默认风格并叠加全部在场角色 LoRA。旧 preset.loraName 只作风格兼容值。
    const knownActorNames = preset.appearanceSource === "character_card"
      ? cardNames : Object.keys(preset.characterLoras || {});
    const resolvedActors = resolveIllustrationActors(
      actors, sceneSpec?.subjects, knownActorNames,
    );
    const workflowMedia = illustrationWorkflowMedia(preset, resolvedActors, cardNames);
    const { loras, loraName, loraWeight, baseImage, characterLora } = workflowMedia;
    // 角色 LoRA 生图：主角名 = 首个配置了角色 LoRA 的在场角色（后端据此做
    // 「角色_轮次_序号」可读命名；非角色 LoRA/兜底风格留空 → 时间戳命名）
    const characterLoraActor = characterLora
      ? (resolvedActors.find((name) => preset.characterLoras?.[name]?.loraName) || "")
      : "";
    const loraConfigurationError = illustrationLoraConfigurationError(preset, workflowMedia);
    if (loraConfigurationError) {
      failSlot("configuration", loraConfigurationError);
      return;
    }
    // V1.1 视频首帧底图：优先取「已完成插画」（本槽 > 最近一次），否则回退用户手动指定；
    // 模板无图像口 → 纯文生视频（resolveVideoBaseImage 返回 undefined 且不拦截）。
    // M2.1：ref 版本同时返回底图来源槽引用，视频入库时记录 derived_from。
    const videoBaseRef = useVideo
      ? resolveVideoBaseImageRef({
          tpl, messageId, slotId,
          messages: messagesRef.current,
          manualBaseImage: baseImage || undefined,
        })
      : undefined;
    // V1.5/W2 首帧复用（坑C：有图前提）：transition=reuse 且反查到上尾帧图 → 首帧底图用
    // 上尾帧图（视觉延续）。无尾帧图资产 → reuse 作废，维持原路径（独立生成首帧）。
    // 仅 firstlast 消费（climax 无首帧复用）；reuse 之外（regenerate/ambiguous/空）不复用。
    const baseImageForUse = resolveTransitionBaseImage({
      videoMode, transition, messages: messagesRef.current,
      fallback: videoBaseRef?.url ?? baseImage,
    }) || "";
    let negativePrompt = preset.negativePrompt?.trim() || sceneSpec?.negative_prompt || "";
    // LoRA 触发词查表与机械前置（对齐 /a 编排 lora_inject 思路）：主图与首尾帧图共用。
    // 生效 LoRA 的触发词必须逐字精确（大脑会漏写/改写 → LoRA 不生效却看不出），按文件名查触发词表
    // 前置到正向提示词；已在提示词里出现的词不重复注入（大小写不敏感）。查表失败硬失败（配置问题要暴露）。
    let loraTriggerItems: Awaited<ReturnType<typeof listLoras>>["items"] = [];
    if (loras.length) {
      try {
        loraTriggerItems = (await listLoras()).items || [];
        for (const lora of loras) {
          const metadata = resolveLoraPromptMetadata(loraTriggerItems, lora.name);
          if (!metadata.found) {
            throw new Error(`实际加载的 LoRA 没有精确元数据记录：${lora.name}`);
          }
        }
      } catch (error) {
        failSlot(
          "lora_metadata",
          error instanceof Error ? error.message : "LoRA触发词与建议提示词读取失败",
        );
        return;
      }
    }
    const withLoraTriggers = (text: string): string => {
      let out = text;
      for (const lora of loras) {
        const metadata = resolveLoraPromptMetadata(loraTriggerItems, lora.name);
        out = applyProfileLoraTriggers(out, promptProfile, metadata.additions);
      }
      return out;
    };
    // V1.6/P5+：首尾帧独立图片模式跳过高潮 Profile 渲染（帧提示词在生成循环内走同一 Profile 编译链单独编译）。
    if (!useFirstlastImages) {
    // 重新生成（manual）必须重编译：重试快照里的 profile_prompt 是当初链路的产物——
    // 编译链升级（措辞收敛/预设瘦身/LoRA 绑定修正）后旧成稿不会自愈，复用=永远提交污染提示词
    //（2026-08-30 用户实锤：重试反复提交带旧措辞的成稿）。automatic 路径仍复用 produce 已编译结果。
    const storedProfile = sceneSpec?.profile_prompt;
    const storedProfileUsable = !!storedProfile
      && sceneSpec.profile === promptProfile
      && source !== "manual";
    if (storedProfileUsable && sceneSpec) {
      prompt = storedProfile;
    } else if (sceneSpec) {
      try {
        const rendered = await genProfilePrompt(
          promptProfile,
          {
            ...sceneSpec, actors: resolvedActors, character_lora: characterLora,
            repo_id: repo?.id || "", thread_id: repo?.id || "", turn_id: turnId,
          },
          { ...chat, proxyUrl: modelProxies.chatProxyUrl },
          {
            presetDir: settings.presetDir,
            presetName: settings.activePresetName,
            userName: effectivePersona.name,
          },
        );
        prompt = rendered.prompt;
        negativePrompt = preset.negativePrompt?.trim() || rendered.negative_prompt || negativePrompt;
      } catch (error) {
        failSlot(
          "prompt_profile",
          error instanceof Error ? error.message : "智能提示词生成失败",
        );
        return;
      }
    }
    if (!prompt.trim()) {
      failSlot("prompt_profile", "智能提示词结果为空");
      return;
    }
    if (promptProfile === "anima_tags") {
      prompt = replacePromptQualityLine(
        prompt, preset.qualityPrompt || "", sceneSpec?.rating || "sfw",
      );
      prompt = ensureAnimaIllustrationStyle(
        prompt, loras.some((lora) => !lora.character),
      );
    }
    prompt = withLoraTriggers(prompt);
    }
    // V1.6/P5+（2026-08-29 用户拍板）：首尾帧提示词与高潮点生图完全同构——后端
    // /ai/prompt/profile/frames 先做「时点提取」（首/尾各自时点的英文结构化 action/visual_facts，
    // 帧描述走 @(…)@ 防拦截保护），再逐帧走同一 Profile 编译器（同一校验/带因重写/兜底），
    // 一次调用出两帧成品。触发词由 withLoraTriggers 统一前置。
    // 2026-08-29 验收「生成垃圾图」实锤：编译失败时把中文帧描述原文当正向提示词提交
    // （首帧正向只有 8 字符对白「儿子听娘的。」）——中文原文永远不是合法 Krea2 提示词，
    // 这里禁止静默降级：编译失败必须显式失败（失败槽可见、可重新生成），绝不提交原文。
    const frameCompiled: Partial<Record<"first" | "last", string>> = {};
    const compileFramePrompt = (frame: "first" | "last", frameDesc: string): string => {
      const compiled = frameCompiled[frame];
      if (!compiled || !compiled.trim()) {
        throw new Error(
          `帧提示词编译失败（${frame === "first" ? "首" : "尾"}帧），已阻止把中文原文直接提交给 ComfyUI`,
        );
      }
      return withLoraTriggers(compiled);
    };
    // 底图需先上传到 ComfyUI input 目录，取回可供 LoadImage 引用的文件名
    let uploadedImage = "";
    const needsImage = tpl.exposed.some((f) => workflowFieldBinding(f) === "base_image");
    // V1.1 视频分支：模板声明图像口但拿不到底图 → 拦截（不空图提交图生视频工作流）
    if (useVideo && needsImage && !baseImageForUse) {
      failSlot("image_required", "视频模板需要首帧底图，但当前没有可用的已完成插画，请先出图或用预设指定底图");
      return;
    }
    if (needsImage && baseImageForUse) {
      try {
        const blob = await (await fetch(localViewUrl(baseImageForUse))).blob();
        const file = new File([blob], baseImageForUse.split(/[\\/]/).pop() || "base.png", { type: blob.type || "image/png" });
        uploadedImage = (await uploadImage(file, settings.comfyuiUrl)).name;
      } catch {
        // 底图上传失败：图片分支退化为纯文生图；视频分支按文档终态失败（不重试乘法）
        if (useVideo) {
          failSlot("upload", "首帧底图上传 ComfyUI 失败");
          return;
        }
      }
    }
    // V1.5/B3 firstlast 双帧：事件携带尾帧图（lastFrameUrl）→ 上传为 last_frame_image；
    // 上传失败/无尾帧图 → 降级为首帧单图提交（后端 firstlast 缺尾帧有 warning，不挂死槽）。
    let uploadedLastFrameImage = "";
    if (useVideo && videoMode === "firstlast" && lastFrameUrl) {
      try {
        const blob = await (await fetch(localViewUrl(lastFrameUrl))).blob();
        const file = new File([blob], lastFrameUrl.split(/[\\/]/).pop() || "last.png", { type: blob.type || "image/png" });
        uploadedLastFrameImage = (await uploadImage(file, settings.comfyuiUrl)).name;
      } catch {
        uploadedLastFrameImage = "";
      }
    }
    // 按 exposed 的隐藏 binding 组 values；提交 key 始终是“节点id.原字段名”。
    const latentSize = latentSizeFor(
      sceneSpec?.aspect_ratio || "2:3",
      preset.latentLongEdge === 2048 || preset.latentLongEdge === 4096
        ? preset.latentLongEdge : 1024,
    );
    // V1.5/B2：prevTailDesc 兜底——事件没带时反查最近一条已完成视频槽的尾帧描述
    const prevTailDescForUse = prevTailDesc
      || resolvePrevTailDesc(messagesRef.current)?.lastFrameDesc || "";
    // V1.6/P5 首尾帧顺序链：先出首尾帧图（图片模板生图），双图 ready 再提视频。
    // 决策 A（2026-08-26 拍板）：首尾帧生图复用 preset.templateId，prompt=事件
    // firstFrameDesc/lastFrameDesc；reuse 免首帧生图（W2 已把上尾帧图复用为底图）；
    // 尾帧有事件图直接用。首帧生图失败 → 明确失败（视频必有首帧）；尾帧生图失败 →
    // 降级首帧单图（不挂死，后端 firstlast 缺尾帧有 warning）。
    let frameFirstImage = uploadedImage;
    let frameLastImage = uploadedLastFrameImage;
    let firstFrameGenerated = false; // W3：首帧是否独立生图成功（决定要不要发转场任务）
    let frameFirstPromptId = "";
    let frameLastPromptId = "";
    // 编译后帧提示词：提交 ComfyUI 用它，回填入库/上报 trace 必须同源（2026-08-29 验收：
    // 资产库显示帧描述原文的根因=入库链传了 firstFrameDesc/lastFrameDesc 原文）。
    let frameFirstPrompt = "";
    let frameLastPrompt = "";
    let frameFirstValueKeys: string[] = [];
    let frameLastValueKeys: string[] = [];
    let frameFirstMedia: ReturnType<typeof illustrationWorkflowMedia> | undefined;
    let frameLastMedia: ReturnType<typeof illustrationWorkflowMedia> | undefined;
    if (useFirstlastImages) {
      const frameTpl = templates.find((t) => t.id === preset.templateId);
      if (!frameTpl) {
        failSlot("configuration", "firstlast 首尾帧生图需要配置图片工作流模板");
        return;
      }
      const plan = planFirstlastFrameTasks({
        transition,
        prevTailUrl: resolvePrevTailDesc(messagesRef.current)?.lastFrameUrl,
        firstFrameDesc, lastFrameDesc, lastFrameUrl,
      });
      if (useVideo && !plan.canGenerateVideo) {
        failSlot("image_required", "firstlast 视频需要首帧图：既无上尾帧图可复用，也无首帧画面描述可生成");
        return;
      }
      if (!useVideo && !plan.tasks.length) {
        failSlot("image_required", "首尾帧生图缺少画面：既无上尾帧图可复用，也无首帧/尾帧画面描述");
        return;
      }
      // 逐帧 LoRA（2026-08-30 用户反馈实锤：首帧画面是凌若冰、尾帧是舞姬恋，
      // 却整单都挂了状态栏在场角色的 LoRA）。一轮请求只解析一次 actors，但首/尾帧
      // 画面可能各是不同角色——按帧描述原文逐帧命中已配置角色；未命中回退请求级 actors。
      const frameMediaFor = (desc: string) => {
        const hits = knownActorNames.filter((name) => name && desc.includes(name));
        return illustrationWorkflowMedia(preset, hits.length ? hits : resolvedActors, cardNames);
      };
      const buildFrameValues = (desc: string, media = workflowMedia) => firstlastFrameValues(
        frameTpl.exposed, desc,
        {
          negativePrompt: preset.negativePrompt,
          loraName: media.loraName,
          loraWeight: media.loraWeight,
          baseImage: media.baseImage,
        },
        latentSize,
      );
      // 帧提示词同构编译：一次调用出两帧成品（时点提取+同一 Profile 编译器）。
      if (sceneSpec) {
        const frameDescs: { first?: string; last?: string } = {};
        for (const task of plan.tasks) {
          if (task.kind === "generate") frameDescs[task.frame] = task.desc;
        }
        if (frameDescs.first || frameDescs.last) {
          try {
            const compiled = await genFramePrompts(
              promptProfile,
              {
                ...sceneSpec, actors: resolvedActors, character_lora: characterLora,
                repo_id: repo?.id || "", thread_id: repo?.id || "", turn_id: turnId,
              },
              frameDescs,
              { ...chat, proxyUrl: modelProxies.chatProxyUrl },
              {
                presetDir: settings.presetDir,
                presetName: settings.activePresetName,
                userName: effectivePersona.name,
              },
            );
            for (const frame of ["first", "last"] as const) {
              const item = compiled.frames[frame];
              if (item?.prompt?.trim()) frameCompiled[frame] = item.prompt;
            }
          } catch (error) {
            console.warn("[auto-video] 帧提示词同构编译失败，降级帧描述原文", error);
          }
        }
      }
      for (const task of plan.tasks) {
        if (task.kind === "reuse" || task.kind === "existing") continue; // 现成图已在上传段处理
        const isFirst = task.frame === "first";
        try {
          const framePrompt = compileFramePrompt(task.frame, task.desc);
          const frameMedia = frameMediaFor(task.desc);
          const frameLoras = frameMedia.loras.map(({ name, weight }) => ({ name, weight }));
          const frameValues = buildFrameValues(framePrompt, frameMedia);
          const recordIds = (res: { prompt_id: string }) => {
            if (isFirst) { frameFirstPromptId = res.prompt_id; frameFirstPrompt = framePrompt; frameFirstValueKeys = Object.keys(frameValues).sort(); frameFirstMedia = frameMedia; }
            else { frameLastPromptId = res.prompt_id; frameLastPrompt = framePrompt; frameLastValueKeys = Object.keys(frameValues).sort(); frameLastMedia = frameMedia; }
          };
          const runOnce = async (): Promise<{ prompt_id: string }> => {
            const res = await submitWorkflow(
              preset.templateId, frameValues, settings.comfyuiUrl,
              framePrompt, frameLoras, preset.loraMode || "single",
            );
            const pid = res.prompt_id;
            if (!pid) throw new Error("ComfyUI 没有返回任务 ID");
            recordIds({ prompt_id: pid });
            return { prompt_id: pid };
          };
          // 提交阶段自愈（对齐 stalled 自愈，2026-08-29 验收「新对话没有触发生图」）：
          // 首次提交失败（后端挂起超时/ComfyUI 瞬断）→ 中断可能残留的任务后自动重提一次；
          // 再失败才进外层 failSlot（失败槽可见+可重新生成）。上限 1 次防循环。
          let r: { prompt_id: string };
          try {
            r = await runOnce();
          } catch {
            await interruptComfy(settings.comfyuiUrl).catch(() => undefined);
            r = await runOnce();
          }
          let outcome = await pollWorkflowResult(r.prompt_id, settings.comfyuiUrl, "image");
          if (outcome.kind === "stalled") {
            // 卡死自愈（2026-08-29 用户需求）：队列无节点运转 → 删除坏死任务（清队列+中断），
            // 自动重新提交一次；再卡死才按失败处理。上限 1 次防循环。
            await interruptComfy(settings.comfyuiUrl, r.prompt_id).catch(() => undefined);
            r = await runOnce();
            outcome = await pollWorkflowResult(r.prompt_id, settings.comfyuiUrl, "image");
          }
          if (outcome.kind !== "complete" || !outcome.result.images[0]) {
            throw new Error(outcome.kind === "failed" || outcome.kind === "stalled"
              ? outcome.error : "生图未产出图像");
          }
          const inputName = await moveComfyOutputToInput(outcome.result.images[0], settings.comfyuiUrl);
          if (isFirst) { frameFirstImage = inputName; firstFrameGenerated = true; }
          else frameLastImage = inputName;
        } catch (error) {
          const message = error instanceof Error ? error.message : "首尾帧生图失败";
          if (isFirst) { failSlot("frame_gen", `首帧生图失败：${message}`); return; }
          // 尾帧生图失败：视频模式降级首帧单图（视频还能出，不挂死）；独立图片模式尾帧=楼层
          // 唯一新画面，静默降级会让主槽 pending 悬挂（刷新后变孤儿槽被清理，用户完全无感
          // ——2026-08-29 验收「什么图片都没有」实锤），必须显式失败。
          if (!useVideo) { failSlot("frame_gen", `尾帧生图失败：${message}`); return; }
          console.warn("[auto-video] 尾帧生图失败，降级首帧单图", { message });
        }
      }
      // V1.6/P5+ 独立图片模式回填（2026-08-28 拍板）：双帧图经 pollResult 走既有回填+入库全链。
      // useVideo 时主槽留给视频正片（避免同槽双 pollResult 竞态），双帧图走 :first/:last 副槽；
      // 仅图片模式时主槽=本楼层新画面（layout.main），尾帧新图进 :last。
      const layout = firstlastSlotLayout(plan);
      if (layout) {
        const lastSlotId = `${slotId}:last`;
        const frameTarget = (sid: string) => ({ messageId, slotId: sid, background: true as const });
        // 逐帧重生成快照（2026-09-22 补）：帧路径此前 `pollResult` 第 2/3 参传 `[]`/`undefined`，
        // 槽位因此**没有 regeneration** ⇒ 帧卡上的「重新生图」走 legacy 兜底（按资产库提示词
        // 提交一张**普通插画**：模板/逐帧 LoRA/尺寸全不是那一帧的，即「换成不同档的图」），
        // 「重新生图（含提示词）」也无从预填。材料本就齐全（framePrompt + frameMedia +
        // buildFrameValues 同输入同输出），这里按帧补齐，与主路径（高潮点）对齐。
        const frameOutputNodeIds = frameTpl.primary_output_node_id ? [frameTpl.primary_output_node_id] : [];
        const frameRegenSnapshot = (
          framePrompt: string,
          media: ReturnType<typeof illustrationWorkflowMedia> | undefined,
        ) => templateRegenerationSnapshot(
          preset.templateId,
          buildFrameValues(framePrompt, media ?? workflowMedia),
          settings.comfyuiUrl,
          frameOutputNodeIds,
          framePrompt,
          (media ?? workflowMedia).loras.map(({ name, weight }) => ({ name, weight })),
          preset.loraMode || "single",
        );
        const reportFrame = (
          promptId: string, sid: string, framePrompt: string, keys: string[],
          media: ReturnType<typeof illustrationWorkflowMedia> = workflowMedia,
        ) => {
          void reportIllustrationSubmission({
            threadId, repoId: repo?.id || threadId, turnId, messageId, slotId: sid,
            templateId: preset.templateId, promptId, prompt: framePrompt, promptProfile,
            loraName: media.loraName, loraWeight: media.loraWeight,
            latentWidth: latentSize.width, latentHeight: latentSize.height,
            loraMode: preset.loraMode || "single",
            loraNames: media.loras.map(({ name }) => name),
            valueKeys: keys,
            source,
          }).catch(() => undefined);
        };
        if (useVideo) {
          // 视频模式：主槽=正片（C 段提交），双帧图入 :first/:last 副槽（reuse 首帧复用上楼图，无新图不建槽）
          if (layout.main === "first_prompt" && frameFirstPromptId) {
            // 第 34 刀：首帧图固定落在**正文第一段下方**（`at: "start"`）；尾帧图（:last）仍落末段末。
            setMessages((current) => appendImageSlot(current, messageId, `${slotId}:first`, frameFirstPrompt, "start"));
            pollResult(frameFirstPromptId, frameOutputNodeIds, frameRegenSnapshot(frameFirstPrompt, frameFirstMedia), frameTarget(`${slotId}:first`), frameFirstPrompt, "image");
            reportFrame(frameFirstPromptId, `${slotId}:first`, frameFirstPrompt, frameFirstValueKeys, frameFirstMedia);
          }
          if (frameLastPromptId) {
            setMessages((current) => appendImageSlot(current, messageId, lastSlotId, frameLastPrompt));
            pollResult(frameLastPromptId, frameOutputNodeIds, frameRegenSnapshot(frameLastPrompt, frameLastMedia), frameTarget(lastSlotId), frameLastPrompt, "image");
            reportFrame(frameLastPromptId, lastSlotId, frameLastPrompt, frameLastValueKeys, frameLastMedia);
          }
        } else {
          // 独立图片模式：主槽=本楼层新画面，尾帧新图进 :last
          if (layout.main === "first_prompt" && frameFirstPromptId) {
            pollResult(frameFirstPromptId, frameOutputNodeIds, frameRegenSnapshot(frameFirstPrompt, frameFirstMedia), frameTarget(slotId), frameFirstPrompt, "image");
            reportFrame(frameFirstPromptId, slotId, frameFirstPrompt, frameFirstValueKeys, frameFirstMedia);
          } else if (layout.main === "last_prompt" && frameLastPromptId) {
            pollResult(frameLastPromptId, frameOutputNodeIds, frameRegenSnapshot(frameLastPrompt, frameLastMedia), frameTarget(slotId), frameLastPrompt, "image");
            reportFrame(frameLastPromptId, slotId, frameLastPrompt, frameLastValueKeys, frameLastMedia);
          } else if (layout.main === "last_frame_url" && lastFrameUrl) {
            setMessages((current) => resolveMediaSlot(current, messageId, slotId, lastFrameUrl, "image"));
          } else if (layout.main === "prev_tail_url") {
            const url = resolvePrevTailDesc(messagesRef.current)?.lastFrameUrl;
            if (url) setMessages((current) => resolveMediaSlot(current, messageId, slotId, url, "image"));
          }
          if (layout.lastSlot && frameLastPromptId) {
            // 2026-09-22 修复：本分支曾传 lastFrameDesc（中文原文）⇒ 资产库显示帧描述原文
            // 而非编译后提示词（2026-08-29 同类修复只补了视频分支，这里漏了）。三处必须与
            // 实际提交 ComfyUI 的 frameLastPrompt 同源，且 media 同源（逐帧 LoRA）。
            setMessages((current) => appendImageSlot(current, messageId, lastSlotId, frameLastPrompt));
            pollResult(frameLastPromptId, frameOutputNodeIds, frameRegenSnapshot(frameLastPrompt, frameLastMedia), frameTarget(lastSlotId), frameLastPrompt, "image");
            reportFrame(frameLastPromptId, lastSlotId, frameLastPrompt, frameLastValueKeys, frameLastMedia);
          }
        }
      }
    }
    // W3 转场任务（2 任务排队）：firstlast + 首帧独立生成（非 reuse）+ 后端下发转场提示词 →
    // 先提交转场视频（图片1=上尾帧、图片2=当前首帧），正片随后提交（ComfyUI 队列顺序执行）。
    // 坑F：无上尾帧图 → 降级文字转场（不拦截）；转场失败/提交失败 → 不挂死正片（仅槽位标失败）。
    if (useVideo && videoMode === "firstlast" && transition !== "reuse"
        && transitionVideoPrompt && firstFrameGenerated) {
      const transitionSlotId = `${slotId}:transition`;
      const prevTailRef = resolvePrevTailDesc(messagesRef.current);
      let prevTailInput = "";
      if (prevTailRef?.lastFrameUrl) {
        try {
          prevTailInput = await uploadRemoteImageToInput(
            localViewUrl(prevTailRef.lastFrameUrl), settings.comfyuiUrl,
          );
        } catch (error) {
          console.warn("[auto-video] 上尾帧图上传失败，转场降级文字转场", error);
        }
      }
      setMessages((current) => appendTransitionSlot(
        current, messageId, transitionSlotId, transitionVideoPrompt, transitionVideoParams,
      ));
      const transitionValues = transitionVideoValues(
        tpl.exposed, transitionVideoPrompt,
        { negativePrompt: preset.negativePrompt, loraName, loraWeight },
        latentSize,
        {
          transitionDurationHint: preset.transitionDurationHint,
          camera: preset.videoCamera,
          prevTailDesc: prevTailDescForUse,
          firstFrameDesc,
          prevTailImage: prevTailInput || undefined,
          firstFrameImage: frameFirstImage,
        },
      );
      try {
        const rt = await submitWorkflow(
          preset.videoTemplateId!, transitionValues, settings.comfyuiUrl,
          transitionVideoPrompt, loras.map(({ name, weight }) => ({ name, weight })),
          preset.loraMode || "single",
        );
        if (rt.prompt_id) {
          setMessages((current) => bindMediaSlotPrompt(
            current, messageId, transitionSlotId, rt.prompt_id!,
          ));
          const transitionOutputNodeIds = tpl.primary_output_node_id
            ? [tpl.primary_output_node_id] : [];
          pollResult(rt.prompt_id, transitionOutputNodeIds, undefined,
            { messageId, slotId: transitionSlotId, background: true as const },
            transitionVideoPrompt, "video");
        }
      } catch (error) {
        discardFailedIllustration(messageId, transitionSlotId, "transition",
          error instanceof Error ? error.message : "转场视频提交失败");
        console.warn("[auto-video] 转场视频提交失败，正片照常", error);
      }
    }
    // V1.6/P5+：首尾帧独立图片模式（无视频）不出高潮主图/正片——双帧图已回填，主槽语义已被占用。
    if (!(useFirstlastImages && !useVideo)) {
    const values = illustrationTemplateValues(tpl.exposed, {
      prompt, negativePrompt, loraName, loraWeight, baseImage: uploadedImage,
      latentSize,
      // V1.2 视频最小事实：时长/镜头 = 用户预设值（模板无 exposed binding 时自然忽略）；motion 已由后端透传
      videoDuration: useVideo && preset.videoDurationHint ? preset.videoDurationHint : undefined,
      videoCamera: useVideo ? preset.videoCamera : undefined,
      // V1.5/B1 视频模式 + 首尾帧描述：有值才传（模板无 exposed binding 时自然忽略）
      videoMode: useVideo ? videoMode : undefined,
      firstFrameDesc: useVideo && firstFrameDesc ? firstFrameDesc : undefined,
      lastFrameDesc: useVideo && lastFrameDesc ? lastFrameDesc : undefined,
      prevTailDesc: useVideo && prevTailDescForUse ? prevTailDescForUse : undefined,
      lastFrameUrl: useVideo && lastFrameUrl ? lastFrameUrl : undefined,
      // V1.5/B3 双帧图：V1.6/P5 首尾帧顺序链后，首帧=生成的图（兜底底图），尾帧=生成的图（兜底事件图）
      firstFrameImage: useVideo && frameFirstImage ? frameFirstImage : undefined,
      lastFrameImage: useVideo && frameLastImage ? frameLastImage : undefined,
      // V1.5 默认开放：后端编译的 climax 视频提示词（仅视频分支注入；无模板时仍留在槽位供测试核对）
      videoPrompt: useVideo && videoPrompt ? videoPrompt : undefined,
    });
    try {
      const st = await comfyStatus(settings.comfyuiUrl);
      if (!st.running) {
        failSlot("comfyui_status", "ComfyUI 尚未启动");
        return;
      }
      // V1.1 红线：视频模板 LoRA 不自动接线（loader 结构各异），只认模板自身暴露字段；
      // 图片分支维持既有 loraStack 注入合同。
      const loraStack = useVideo ? [] : loras.map(({ name, weight }) => ({ name, weight }));
      const loraMode = useVideo ? "none" : (preset.loraMode || "single");
      const r = await enqueueSubmit(() => submitWorkflow(
        chosenId, values, settings.comfyuiUrl, prompt, loraStack, loraMode,
      ));
      if (r.prompt_id) {
        void reportIllustrationSubmission({
          threadId, repoId: repo?.id || threadId, turnId, messageId, slotId,
          templateId: chosenId, promptId: r.prompt_id, prompt, promptProfile,
          loraName, loraWeight, latentWidth: latentSize.width, latentHeight: latentSize.height,
          loraMode, loraNames: loraStack.map((item) => item.name),
          valueKeys: Object.keys(values).sort(),
          source,
        }).catch(() => undefined);
        const outputNodeIds = tpl.primary_output_node_id ? [tpl.primary_output_node_id] : [];
        const target = { messageId, slotId, background: true as const };
        const regeneration = templateRegenerationSnapshot(
          chosenId, values, settings.comfyuiUrl, outputNodeIds, prompt, loraStack, loraMode,
          characterLoraActor,
        );
        setMessages((current) => bindMediaSlotPrompt(current, messageId, slotId, r.prompt_id!));
        const baseSlotRef = useVideo && videoBaseRef?.sourceMessageId && videoBaseRef?.sourceSlotId
          ? { messageId: videoBaseRef.sourceMessageId, slotId: videoBaseRef.sourceSlotId }
          : undefined;
        pollResult(r.prompt_id, outputNodeIds, regeneration, target, prompt, useVideo ? "video" : "image", baseSlotRef);
      } else {
        failSlot("submit", "ComfyUI 没有返回任务 ID");
      }
    } catch (error) {
      failSlot("submit", error instanceof Error ? error.message : "自动插画提交失败");
    }
    }
  };
  return { submitIllustration };
}
