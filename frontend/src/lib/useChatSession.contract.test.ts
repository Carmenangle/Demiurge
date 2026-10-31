// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { markMediaSlotFailed } from "./chatSessionEvents";

/**
 * 插画提交参数契约（2026-09-14 架构审计 C7）。
 *
 * 收口前：`submitIllustration` 是 **17 个位置参数**，失败槽快照按签名序存成数组，
 * 「重新生成」用**魔术下标** `args[7] = "manual"` 翻 `source`——签名插一个参数就静默错位，
 * 且 TS 不报错（数组按 `Parameters<...>` 展开）。本文件按**源码真源**锁住新形态：
 * 参数收成 `IllustrationSubmit` 对象、重试按名覆盖 `source`、`retryArgs` 是不透明快照。
 */
const src = readFileSync(new URL("./useChatSession.ts", import.meta.url), "utf-8");
// 注释里会提到旧写法（`args[7]`）作为背景，判定只看代码，先把注释剥掉。
const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
// T2.2-A：插画提交链（submitIllustration）已外移 → useIllustrationChain.ts；锁定面随之改指新属主。
const chainCode = readFileSync(new URL("./useIllustrationChain.ts", import.meta.url), "utf-8")
  .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
// T2.2-B2：在途生图任务恢复 effect 已外移 → useWorkflowResume.ts；P22「恢复期过期清理」调用点随之迁移。
const resumeCode = readFileSync(new URL("./useWorkflowResume.ts", import.meta.url), "utf-8")
  .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
// T2.2-B4-a：进度/节点名的跨导航分片（P18）已外移 → useWfProgressShard.ts；锁定面随之改指新属主。
const wfShardCode = readFileSync(new URL("./useWfProgressShard.ts", import.meta.url), "utf-8")
  .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
// T2.2-C1：剧情对白音频化链（submitAudio/mergeAudioTracks）已外移 → useAudioTracks.ts；音频 pollResult 调用点随之迁移。
const audioCode = readFileSync(new URL("./useAudioTracks.ts", import.meta.url), "utf-8")
  .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");

describe("isolation of illustration submit params", () => {
  it("no positional-shape magic index and no positional snapshot array", () => {
    expect(code).not.toMatch(/args\[\d+\]/);          // 不得按位置下标取字段
    expect(code).not.toMatch(/illustrationRetrySnapshot/); // 位置数组快照已废弃
  });

  it("重试按名覆盖 source（不再依赖签名序）", () => {
    expect(code).toMatch(/submitIllustration\(\{\s*\.\.\.snapshot,\s*source:\s*"manual"\s*\}\)/);
  });

  it("IllustrationSubmit 字段清单稳定（改名/漏字段在此挡下）", () => {
    const match = code.match(/export interface IllustrationSubmit \{([\s\S]*?)\n\}/);
    expect(match).not.toBeNull();
    const names = [...match![1].matchAll(/^\s*(\w+)\??:/gm)].map((m) => m[1]);
    expect(names).toEqual([
      "prompt", "motion", "actors", "messageId", "slotId", "sceneSpec", "turnId",
      "source", "eventVideoMode", "firstFrameDesc", "lastFrameDesc", "prevTailDesc",
      "lastFrameUrl", "videoPrompt", "videoParams", "transition", "transitionVideoPrompt",
      "transitionVideoParams",
      // 2026-10-18 试跑成稿直送：试跑（promptOnly）上报时把编译成稿写进快照，
      // 「重新生成」据此逐字提交 ComfyUI（不再重编译）；普通失败槽无这两键 ⇒ 仍重编译
      "finalPrompt", "finalFramePrompts",
      // 2026-10-31 高潮点「图 + 视频」两段链：主槽只出图，成图后 `videoOnly` 递归提交
      // `:video` 副槽；两键描述本次提交只出视频、首帧用哪张图（来源槽显式携带，不反查消息）。
      "videoOnly", "videoBaseUrl", "videoBaseSlotId",
      // 2026-10-31 第 74 刀（首尾帧多批次）：段桥视频的图片1＝上一张成图、图片2＝本槽成图。
      // bridgePrevSlotId 由后端按正文序标注随事件下发；bridgeVideo/bridgePrevImageUrl 只在
      // 视频槽那次递归里出现。
      "bridgePrevSlotId", "bridgeVideo", "bridgePrevImageUrl",
    ]);
  });

  it("事件→提交对象逐字段接线（字段清单锁不住漏接线）", () => {
    // 上一条锁只管 IllustrationSubmit 的**形状**；把 `bridgePrevSlotId: event.bridgePrevSlotId`
    // 整行删掉，形状锁照样绿，而段桥视频从此退化成「拿上一楼层尾帧图」——**接线错要在出口断言**。
    const count = (re: RegExp) => (code.match(re) || []).length;
    expect(count(/bridgePrevSlotId:\s*event\.bridgePrevSlotId,/g)).toBe(1);
    // 首尾帧多批次的另两键只属于**视频槽那次递归**，自动派发侧不该出现（出现即形状漂移）。
    expect(code).not.toMatch(/bridgeVideo:/);
    expect(code).not.toMatch(/bridgePrevImageUrl:/);
  });

  it("submitIllustration 只吃一个对象参数", () => {
    expect(chainCode).toMatch(/const submitIllustration = async \(input: IllustrationSubmit\)/);
  });

  it("入参 prompt 与可变局部 prompt 分离（编译链要重写它）", () => {
    // 编译链会按 Profile 重写 prompt（storedProfile / genProfilePrompt / anima 收尾 / LoRA 触发词）。
    // 若把解构写成 `const { prompt, ... } = input`，这些重赋值会让 `tsc` 直接报 TS2588——
    // 而 vitest 走 esbuild 剥类型，跑得再绿也发现不了。这里把形态钉死，让回归落在快测面。
    expect(chainCode).toMatch(/prompt:\s*promptIn,/);           // 解构时改名承接
    expect(chainCode).toMatch(/let prompt = promptIn;/);         // 再起可变局部
    expect(chainCode).toMatch(/prompt:\s*promptIn,\s*motion,\s*actors,/); // 快照存原始入参
  });
});

describe("retryArgs 是不透明快照", () => {
  it("对象快照原样保留（消费方不做形状假设）", () => {
    const snapshot = { prompt: "p", motion: 0, source: "automatic" as const };
    const failed = markMediaSlotFailed(
      [{ id: "bot", role: "assistant", text: "",
         parts: [{ type: "media-slot", slotId: "s1", status: "pending" }] }] as never,
      "bot", "s1", "submission", "boom", snapshot,
    );
    const part = failed[0].parts?.find((p) => p.slotId === "s1") as { retryArgs?: unknown };
    expect(part.retryArgs).toEqual(snapshot);
  });

  it("空/缺省快照不写入槽位", () => {
    const failed = markMediaSlotFailed(
      [{ id: "bot", role: "assistant", text: "",
         parts: [{ type: "media-slot", slotId: "s1", status: "pending" }] }] as never,
      "bot", "s1", "submission", "boom",
    );
    const part = failed[0].parts?.find((p) => p.slotId === "s1") as { retryArgs?: unknown };
    expect(part.retryArgs).toBeUndefined();
  });
});

// ── C4-A（画布生成生命周期收口）接线锁 ────────────────────────────────────────
//
// 这一组锁的都是**调用点接线**，而不是纯函数行为：`pollResult` / `pollWorkflowResult`
// 的 `mediaType` 参数与 `getRuntime().start` 的搬迁都发生在 hook 闭包与视图组件里，
// 既没有组件测试也进不了 `vitest` 的渲染面（审计 R5）。
//
// 前科（2026-09-15 实锤）：`GET /narrative/` 曾漏调 `with_sequence`，界面显示错位而
// 2650 例单测全绿 —— **接线错必须在出口层断言**。所以这里直接读源码文本。
const viewSrc = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");

describe("R8 超时合同接线：工作流出图的 mediaType 必须推导（不能默认 image）", () => {
  it("对话 /w（/s 启动）出图按抓取图节点类名推导", () => {
    // 省略第 6 个参数的旧写法会让视频模板吃图片的 20 分钟硬上限
    expect(code).toMatch(
      /pollResult\(\s*r\.prompt_id, outputNodeIds, regeneration, undefined, wfPrompt,\s*deriveWorkflowMediaType\(wf\.capturedGraph\)/,
    );
    expect(code).not.toMatch(/pollResult\(r\.prompt_id, outputNodeIds, regeneration, undefined, wfPrompt\);/);
  });

  it("重新生图按快照推导（workflow 快照可推导；template/ai-image 结构无此字段，保持 image）", () => {
    expect(code).toMatch(
      /snapshot\.kind === "workflow" \? deriveWorkflowMediaType\(snapshot\.graph\) : "image"/,
    );
  });

  it("帧路径保持显式 image（首尾帧出的确定是帧图，不该改推导），且轮询打在**图片机**上", () => {
    // 地址簿按模态选机后地址不再是 settings.comfyuiUrl ⇒ 锁「图片机解析值」这个具名常量，
    // 同时反向锁住「不得退回 settings.comfyuiUrl」（否则配了路由就问到别的机器上）。
    const framePolls = [...chainCode.matchAll(/await pollWorkflowResult\(r\.prompt_id, comfyImageUrl, "image"\)/g)];
    expect(framePolls).toHaveLength(2);   // 首次提交 + 停顿自愈重提
    expect(chainCode).not.toMatch(/await pollWorkflowResult\(r\.prompt_id, settings\.comfyuiUrl/);
  });
});

describe("C4-A 单一属主接线：画布/弹窗走 runtime，对话只余帧路径同步轮询", () => {
  // 2026-09-15 P10（F1）：原先这两条锁的是「组件里出现 `getRuntime(threadId).start(`」。
  // 编排链收归 `lib/runCanvasWorkflow.ts` 之后，启动点**搬出组件**——断言随之改指新属主，
  // 并且**加严**：组件里再出现 `getRuntime(` 就是分层回退（P9/P8 同款结论：行为网抓不到分层回退）。
  it("画布不再自持轮询与编排（只调 runCanvasWorkflow）", () => {
    const canvas = viewSrc("../views/CanvasStageFlow.tsx");
    expect(canvas).not.toMatch(/pollWorkflowResult/);
    expect(canvas).not.toMatch(/getRuntime\(/);
    expect(canvas).toMatch(/runCanvasWorkflow\(/);
  });

  it("弹窗不再自持轮询与编排（只调 runCanvasWorkflow）", () => {
    const modal = viewSrc("../components/WorkflowToolModal.tsx");
    expect(modal).not.toMatch(/pollWorkflowResult/);
    expect(modal).not.toMatch(/getRuntime\(/);
    expect(modal).toMatch(/runCanvasWorkflow\(/);
  });

  it("runtime 的启动点收归单一属主 lib/runCanvasWorkflow.ts（P17 归一键仍在此）", () => {
    const primitive = viewSrc("./runCanvasWorkflow.ts");
    expect(primitive).toMatch(/getRuntime\(input\.threadId\)\.start\(/);
  });

  it("runtime 不再导出 trackCanvasWorkflow/untrackCanvasWorkflow（画布标记已成 start 的内部动作）", () => {
    const runtime = viewSrc("./workflowGenerationRuntime.ts");
    expect(runtime).not.toMatch(/export function trackCanvasWorkflow/);
    expect(runtime).not.toMatch(/export function untrackCanvasWorkflow/);
    expect(runtime).not.toMatch(/trackCanvasWorkflow/);
  });
});

// ── P17（线程键统一）/ P18（进度分片）接线锁 ───────────────────────────────
//
// 背景（2026-09-15 实锤）：画布用 `repo?.id || ""`，对话与视图用 `repo?.id || "home"`
// ⇒ 首页场景 `getRuntime("") !== getRuntime("home")`：两个 runtime 实例、两套 finalized/cancelled
// 判重集合、两个 `laf_pending_gen_*` 存储键。后果：同一任务的 pending 可被归档两次；首页在途任务
// 既不被恢复（恢复循环走 `"home"` 实例那个键），也不进后台活动面板（面板只扫非 home 键，属既有约定）。
// 修法是**单点推导** `threadKey(repoId)`。这一组锁「所有 runtime 入口都用归一键」，
// 禁止后人再各自 `|| ""` / `|| "home"` 回退。
describe("P17 线程键单点接线：runtime 入口一律用 threadKey 归一键", () => {
  const runtimeSrc = viewSrc("./workflowGenerationRuntime.ts");

  it("threadKey 是唯一推导点（仓库 id；首页 = home）", () => {
    expect(runtimeSrc).toMatch(
      /export function threadKey\(repoId\?: string \| null\): string \{\s*return repoId \|\| "home";\s*\}/,
    );
  });

  it("四个 runtime 入口（画布 / 弹窗 / hook / 视图）都经 threadKey 归一", () => {
    for (const rel of [
      "../views/CanvasStageFlow.tsx",
      "../components/WorkflowToolModal.tsx",
      "./useChatSession.ts",
      "../views/ChatView.tsx",
    ]) {
      expect(viewSrc(rel), rel).toMatch(/threadKey\(/);
    }
  });

  it("不得再出现裸 repoId 实参的 getRuntime 调用", () => {
    for (const rel of [
      "../views/CanvasStageFlow.tsx",
      "../components/WorkflowToolModal.tsx",
      "./useChatSession.ts",
    ]) {
      expect(viewSrc(rel), rel).not.toMatch(/getRuntime\(repoId\)/);
    }
  });

  it("getRuntime 首次进入补一次空键迁移（旧 `laf_pending_gen_` 任务不静默丢）", () => {
    expect(runtimeSrc).toMatch(/if \(!legacyKeyMigrated\) \{[\s\S]*?migrateLegacyEmptyThreadKey\(\)/);
  });
});

describe("P18 进度/节点按线程键分片（不再跨仓库串）", () => {
  it("分片状态本体只在新属主 useWfProgressShard.ts（视图不得再内联，T2.2-B4-a）", () => {
    // 视图侧：分片符号一律归零（去注释后判，注释里提一句不算「还在」）
    expect(code).not.toMatch(/persistedWfProgress/);
    expect(code).not.toMatch(/persistedWfNode/);
    expect(code).not.toMatch(/setWfProgress/);
    expect(code).not.toMatch(/setWfNode/);
    // 新属主：恰一次调用点
    expect(code).toMatch(
      /const \{ wfProgress, wfNode, updateWfProgress, updateWfNode \} = useWfProgressShard\(threadId\);/,
    );
  });

  it("持久化容器是按线程键分片的 Map（不再是模块级单例 { current }）", () => {
    expect(wfShardCode).not.toMatch(/persistedWfProgress[^;]*= \{ current:/);
    expect(wfShardCode).toMatch(/const persistedWfProgress = new Map<string, number \| null>\(\)/);
    expect(wfShardCode).toMatch(/const persistedWfNode = new Map<string, string>\(\)/);
  });

  it("读写都带线程键（写分片 + 读分片）", () => {
    expect(wfShardCode).toMatch(/persistedWfProgress\.set\(threadId, v\)/);
    expect(wfShardCode).toMatch(/persistedWfNode\.set\(threadId, v\)/);
    expect(wfShardCode).toMatch(/persistedWfProgress\.get\(threadId\) \?\? null/);
    expect(wfShardCode).toMatch(/persistedWfNode\.get\(threadId\) \?\? ""/);
  });

  it("切线程时 state 重新对齐到目标线程的分片", () => {
    expect(wfShardCode).toMatch(/setWfProgress\(persistedWfProgress\.get\(threadId\) \?\? null\)/);
    expect(wfShardCode).toMatch(/setWfNode\(persistedWfNode\.get\(threadId\) \?\? ""\)/);
    expect(wfShardCode).toMatch(/\}, \[threadId\]\)/);
  });

  it("零引用死代码 persistedWfPromptParams 已删除", () => {
    expect(code).not.toMatch(/persistedWfPromptParams/);
    expect(wfShardCode).not.toMatch(/persistedWfPromptParams/);
  });
});

// ── P5（F9）`pollResult` 调用点接线锁 ────────────────────────────────────────
//
// `pollResult` 是 **7 个位置参数**（`promptId, outputNodeIds, regeneration, target, prompt, mediaType, baseSlotRef`），
// 后 4 个可选 ⇒ **漏一个 TS 不报错**，症状只在运行期显形：漏 `mediaType` ⇒ 视频吃图片的 20 分钟硬上限；
// 漏 `baseSlotRef` ⇒ 视频 `derived_from` 记录**静默消失**（无报错、无 trace）。
// 收口前 10 个调用点里只有 2 个有形态锁（即下方「R8 超时合同接线」那两条），其余 **8 个裸奔**：
// 5 个首尾帧（`"image"`）、1 个转场（`"video"`）、1 个主槽（`mainIsVideo ? "video" : "image"` + 第 7 参）、1 个音频（`"audio"`）。
// 本组把这 8 点补齐，并把**调用点总数**钉住（增删调用点必须同步改本锁）。
// 2026-10-24 T2.2-C1：音频调用点随 submitAudio 外移到 useAudioTracks.ts ⇒ 音频那条锁改指音域属主（audioCode）并加严。
// 注：与 R5 同一处境（两个调用方组件没有组件测试）⇒ 只读源码文本上锁。
describe("P5 pollResult 调用点接线锁：10 点全锁（本组 8 + R8 组 2）", () => {
  it("调用点共 10 个：视图 2（/s runWorkflow + regenerateResult）+ 新属主 7（帧 5 + 转场 + 主槽）+ 音域 1（T2.2-C1 外移）", () => {
    expect([...code.matchAll(/pollResult\(/g)]).toHaveLength(2);
    expect([...chainCode.matchAll(/pollResult\(/g)]).toHaveLength(7);
    expect([...audioCode.matchAll(/pollResult\(/g)]).toHaveLength(1);
  });

  it("5 个首尾帧调用点：显式 \"image\" + 逐帧 regeneration 快照（帧图不许退成推导、帧卡重生成不许走 legacy 兜底）", () => {
    // 帧路径产出的确定是帧图 ⇒ 第 6 参必须是字面 "image"。
    // 改成 `deriveWorkflowMediaType(...)` 会让这里的计数掉到 4 ⇒ 本例如实变红（正向已足够，下方反向再加一道）。
    // 2026-09-22 补：第 2/3 参此前是 `[], undefined` ⇒ 帧槽**没有 regeneration**，帧卡上的
    // 「重新生图」走 legacy 兜底（按资产库提示词提交一张普通插画：模板/逐帧 LoRA/尺寸全不是
    // 那一帧的），「重新生图（含提示词）」也无从预填。这里把「逐帧自带快照」一并钉住，
    // 并用反向引用强制**首帧只能用 frameFirstPrompt/Media、尾帧只能用 frameLastPrompt/Media**（不许串帧）。
    const frameCalls = [...chainCode.matchAll(
      /pollResult\(frame(First|Last)PromptId, frameOutputNodeIds, frameRegenSnapshot\(frame\1Prompt, frame\1Media\), frameTarget\([^)]*\), frame\1Prompt, "image"\)/g,
    )];
    expect(frameCalls).toHaveLength(5);
    expect(chainCode).not.toMatch(/pollResult\(frame(?:First|Last)PromptId, \[\], undefined/);
    expect(chainCode).not.toMatch(/pollResult\(frame(?:First|Last)PromptId[\s\S]{0,200}?deriveWorkflowMediaType/);
  });

  it("转场调用点显式 \"video\" + 后台 target + 第 8 参=视频机（转场是视频，不许吃图片超时）", () => {
    expect(chainCode).toMatch(
      /pollResult\(rt\.prompt_id, transitionOutputNodeIds, undefined,\s*\{ messageId, slotId: transitionSlotId, background: true as const \},\s*transitionVideoPrompt, "video", undefined, comfyVideoUrl\)/,
    );
  });

  it("主槽调用点必须带第 6/7 参（mediaType 推导 + baseSlotRef）—— **最险的一点**", () => {
    expect(chainCode).toMatch(
      /pollResult\(r\.prompt_id, outputNodeIds, regeneration, target, prompt, mainIsVideo \? "video" : "image", baseSlotRef\)/,
    );
    // 反向锁：7 参形态不得回退成 6 参（丢 baseSlotRef ⇒ 视频 derived_from 记录消失）
    expect(chainCode).not.toMatch(
      /pollResult\(r\.prompt_id, outputNodeIds, regeneration, target, prompt, mainIsVideo \? "video" : "image"\)/,
    );
  });

  it("音频调用点显式 \"audio\" + 第 8 参=配音机（IndexTTS 配音不受图片 20 分钟上限约束）", () => {
    // T2.2-C1：音频链外移 → useAudioTracks.ts；锁定面改指新属主并加严（视图不得回潮）。
    // 第 8 参是地址簿「按模态选机」的落点：没有 regeneration 快照可推导，不传就问错机器。
    expect(audioCode).toMatch(
      /pollResult\(r\.prompt_id, outputNodeIds, undefined, \{ messageId, slotId, background: true \}, line\.text, "audio", undefined, comfyAudioUrl\)/,
    );
    expect(code).not.toMatch(
      /pollResult\(r\.prompt_id, outputNodeIds, undefined, \{ messageId, slotId, background: true \}, line\.text, "audio"/,
    );
  });
});

// ── P22（被取消的任务必须出队）接线锁 ────────────────────────────────────────
//
// 背景（2026-09-15 实锤）：`workflowRuntime.cancel(pid)` 只做 `cancelled.add` + `pending.remove`，
// **不派发任何终态回调** —— 被取消的任务随后在 runtime 里走 `notFound`（pending 已不在列表
// ⇒ 静默 `return`）或停顿守卫的同款静默 `return`，5 个终态回调一个都不跑。而出队原本只挂在
// 终态回调上 ⇒ 被取消的 pid 永远留在 `activePromptQueue`，下一次前台任务 `joinForeground`
// 得 `first=false`、**不再订阅进度 WS**（症状：停一次之后，后续出图全程无进度，直到完成才跳结果）。
//
// 典型**分层漂移**：纯函数（`foregroundRunQueue`）怎么测都是对的，错在「终止路径漏了一个动作」
// —— 只有源码接线锁抓得住（P6–P12 已 7 次实证：行为网全绿、唯独接线锁变红）。
describe("P22 取消必须出队：`workflowRuntime.cancel` 不得裸调", () => {
  it("cancel 全文件只剩 1 个调用点，且形态必在属主 `cancelForegroundRun` 内", () => {
    expect([...code.matchAll(/workflowRuntime\.cancel\(/g)]).toHaveLength(1);
    expect(code).toMatch(
      /const cancelForegroundRun = \(promptId: string\): void => \{\s*workflowRuntime\.cancel\(promptId\);\s*const left = leaveForeground\(activePromptQueue\.current, promptId\);\s*activePromptQueue\.current = left\.queue;\s*\};/,
    );
  });

  it("三个取消场景全部走属主（停止按钮 / 停止单个媒体槽 / 恢复期过期清理）", () => {
    // `cancelForegroundRun(` 只匹配调用形态（定义处写作 `cancelForegroundRun = (`，中间有 ` = `）
    //
    // T2.2-B2 起**跨两个属主**计数：第三处（恢复期过期清理）随 effect 一起搬进了
    // `useWorkflowResume.ts`。锁不能删、只能改指并加严 —— 总数仍须恰 3，
    // 且必须是「视图 2 + 新属主 1」，任一侧少一处或多一处都算漂移。
    expect([...code.matchAll(/cancelForegroundRun\(/g)]).toHaveLength(2);
    expect([...resumeCode.matchAll(/cancelForegroundRun\(/g)]).toHaveLength(1);
    expect(
      [...code.matchAll(/cancelForegroundRun\(/g)].length
      + [...resumeCode.matchAll(/cancelForegroundRun\(/g)].length,
    ).toBe(3);

    const stopGenerating = code.slice(code.indexOf("const stopGenerating = async ()"));
    expect(stopGenerating.slice(0, 1200)).toMatch(/if \(pid\) cancelForegroundRun\(pid\);/);
    expect(code).toMatch(/cancelForegroundRun\(promptId\);\s*try \{ await interruptComfy/);
    // 第三处：新属主里「过期且 history 仍在跑」才归档 + 出队，且出队**在 `if (p.target)` 之外**
    // （无落点的遗留任务也必须出队，否则 pid 永远留在前台队列 ⇒ 后续出图不订阅进度 WS）
    expect(resumeCode).toMatch(
      /if \(outcome !== "watching"\) continue;\s*\n\s*if \(p\.target\) discardFailedIllustration\([\s\S]*?\);\s*\n\s*cancelForegroundRun\(p\.prompt_id\);\s*\n\s*continue;/,
    );
  });

  it("反向锁：不得回退成裸 cancel（两个已修形态都不许复现）", () => {
    expect(code).not.toMatch(/if \(pid\) workflowRuntime\.cancel\(/);       // stopGenerating 旧写法
    expect(code).not.toMatch(/workflowRuntime\.cancel\(p\.prompt_id\)/);    // 恢复期旧写法
    expect(code).not.toMatch(/workflowRuntime\.cancel\(promptId\);\s*\n\s*try \{ await interruptComfy/);
    // T2.2-B2 扩展：新属主只许经属主 `cancelForegroundRun` 出队，恢复链里不得出现裸 cancel
    expect(resumeCode).not.toMatch(/workflowRuntime\.cancel\(/);
  });

  it("runtime 侧文档化了「cancel 不派发终态、调用方须自行出队」这条义务", () => {
    // 用未剥注释的原文断言（`viewSrc` 会把注释一起去掉）
    const raw = readFileSync(new URL("./workflowGenerationRuntime.ts", import.meta.url), "utf-8");
    expect(raw).toMatch(/本方法不派发任何终态回调/);
    expect(raw).toMatch(/cancelForegroundRun/);
  });
});

// ── 第 34 刀（首尾帧固定落点）接线锁 ──────────────────────────────────────────
//
// 用户规格（2026-09-21）：「第一张图在第一段下方、最后一张图在最后一段下方」——
// 与高潮点（插在后端判定的 anchor_offset）不同，首尾帧**不需要任何判定**，前端按正文自算。
// 锁的是**接线**而非纯函数行为（`firstlastMainOffset` 的行为在 illustrationMedia.test.ts /
// chatSessionEvents.test.ts 里测）：谁把 placement 递给 reducer、哪个副槽走 `at="start"`。
// 这类漂移行为网抓不到（P5/P17/P22 同款处境：纯函数全对，错在调用点漏了一个参数）。
describe("第 34 刀 首尾帧固定落点接线锁", () => {
  it("illustrate_request 用前端解析出的 videoMode 生成 placement（不靠事件字段猜）", () => {
    expect(code).toMatch(
      /const firstlast = resolveVideoMode\(mediaPreset, event\.videoMode\) === "firstlast";/,
    );
    expect(code).toMatch(
      /applyStreamEvent\(botId, \{ \.\.\.event, id: slotId \}, firstlast \? \{ firstlast \} : undefined\)/,
    );
  });

  it("placement 必须一路传到 reducer（丢了就退回高潮锚点 ⇒ 独立图片模式落点错）", () => {
    expect(code).toMatch(/placement\?: SlotPlacement/);
    expect(code).toMatch(/reduceChatStreamEvent\(messagesRef\.current, botId, event, placement\)/);
  });

  it('视频模式的 :first 首帧图必须 at="start"（落在第一段下方）', () => {
    expect(chainCode).toMatch(
      /appendImageSlot\(current, messageId, [`"][^`"]*:first[`"], frameFirstPrompt, "start"\)/,
    );
  });

  it("反向锁：:first 副槽不得回退成默认（末段末）", () => {
    expect(chainCode).not.toMatch(
      /appendImageSlot\(current, messageId, [`"][^`"]*:first[`"], frameFirstPrompt\)/,
    );
  });
});


// ── 2026-10-14 A 方案：暂停后必须等后端释放 thread 准入再亮发送键 ──────────────
//
// 实锤：`request_cancel` 只 set cancel_event，release 要等后台运行自己走 finally；
// 它卡在同步阻塞（历史压缩摘要 LLM 调用、参考图上传）时取消无人消费，用户暂停后
// 立即重发就撞 409「该对话已有生成任务正在运行」（换模型无效——按 thread_id 判）。
// 这两条锁**接线顺序**，行为细节在 agentReleaseWait.test.ts。
describe("暂停后等待准入释放（A 方案接线锁）", () => {
  it("hardCancel 必须 await waitForAgentRelease（取消后不立刻返回）", () => {
    expect(code).toMatch(/await waitForAgentRelease\(threadId/);
  });

  it("stopGenerating 的 busy 释放必须晚于 hardCancel（等待期间发送键保持禁用）", () => {
    const body = code.slice(
      code.indexOf("const stopGenerating"),
      code.indexOf("const stopSlotGeneration"),
    );
    expect(body).toContain("await hardCancel(pid)");
    expect(body.indexOf("await hardCancel(pid)")).toBeLessThan(
      body.indexOf("agentBusyRef.current = false"),
    );
  });
});

// ── 试跑（promptOnlyGeneration）→「重新生成」出图 接线锁（2026-10-18 用户诉求） ──
//
// 诉求：试跑只生成提示词（不提交 ComfyUI），后续要能用「那个提示词」生成图片。
// 路径＝失败槽（试跑终态即 failed+「试跑」文案）的「重新生成」按钮 →
// retryIllustration 读 retryArgs 快照 → submitIllustration({...snapshot, source:"manual"})。
// 行为细节（快照原参重编译、同源提交）在 useIllustrationChain.test.tsx 的试跑组；
// 这里锁**出口层接线**（按钮不是死的、回调不是断的）。
describe("试跑后重试出图：失败槽按钮 → retryIllustration 接线", () => {
  const chatMessages = viewSrc("../components/chat/ChatMessages.tsx");
  const chatView = viewSrc("../views/ChatView.tsx");

  it("失败槽渲染「重新生成」按钮并回调 onRetryIllustration(msg.id, slotId)", () => {
    expect(chatMessages).toContain("media-slot-retry");
    expect(chatMessages).toMatch(/onRetryIllustration\(msg\.id, part\.slotId!\)/);
  });

  it("试跑槽的按钮口径＝成稿直送（title 明示，不与普通失败槽混词）", () => {
    // 2026-10-18 用户定案：试跑槽的「重新生成」意思是「直接用成稿跑图」
    expect(chatMessages).toContain("用试跑成稿直接提交 ComfyUI 出图");
  });

  it("ChatView 把 retryIllustration 接进 ChatMessages（断线即按钮变死键）", () => {
    expect(chatView).toContain("onRetryIllustration={retryIllustration}");
  });

  it("retryIllustration 读槽位 retryArgs 快照、按名翻 manual 重调（无快照静默返回）", () => {
    expect(code).toMatch(/const snapshot = part\?\.retryArgs;/);
    expect(code).toMatch(/if \(!snapshot\) return;/);
    expect(code).toMatch(/submitIllustration\(\{\s*\.\.\.snapshot,\s*source:\s*"manual"\s*\}\)/);
  });
});
