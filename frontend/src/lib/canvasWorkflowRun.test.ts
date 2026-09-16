import { describe, expect, it } from "vitest";
import {
  canvasRunEffects, deriveWorkflowMediaType, pendingRunLabel, runCanvasEffects, type CanvasRunSink,
} from "./canvasWorkflowRun";

// 2026-09-15 C4-A 第 3 步：画布搬到 runtime 后，唯一还需要单测的就是「终态 → 事件序列」这层映射
// （CanvasStageFlow.tsx 没有组件测试）。这 5 条是跨组件契约的锁：
// laf-canvas-wf-done 漏派发 → 画布占位节点与 ChatView 的进度列表**永久残留**（审计 R1）。
describe("canvasRunEffects · 终态到事件序列", () => {
  it("completed：清占位 + 派发 done + 回读快照 + 报产出数", () => {
    const effects = canvasRunEffects("completed", { images: 3, videos: 1, audios: 2 });
    expect(effects).toEqual({
      keepPlaceholder: false, dispatchDone: true, interruptQueue: false, reloadFromSnapshot: true,
      toast: { text: "运转完成，3 张图、1 个视频、2 个音频已入库。", kind: "info" },
    });
  });

  it("completed 只有图时不赘述视频/音频", () => {
    expect(canvasRunEffects("completed", { images: 1 }).toast.text).toBe("运转完成，1 张图已入库。");
  });

  it("empty：清占位 + 派发 done，但**不回读快照**（没产出可投影）", () => {
    const effects = canvasRunEffects("empty");
    expect(effects.keepPlaceholder).toBe(false);
    expect(effects.dispatchDone).toBe(true);
    expect(effects.reloadFromSnapshot).toBe(false);
    expect(effects.toast.kind).toBe("error");
  });

  it("failed：清占位 + 派发 done，错误文案透传", () => {
    const effects = canvasRunEffects("failed", { error: "采样失败" });
    expect(effects).toEqual({
      keepPlaceholder: false, dispatchDone: true, interruptQueue: false, reloadFromSnapshot: false,
      toast: { text: "运转失败：采样失败", kind: "error" },
    });
  });

  it("stalled：额外**清队列并中断**该 prompt（唯一会打断 ComfyUI 的终态）", () => {
    const effects = canvasRunEffects("stalled", { error: "长时间未开始加载节点，已停止" });
    expect(effects.interruptQueue).toBe(true);
    expect(effects.dispatchDone).toBe(true);
    expect(effects.toast.text).toBe("运转失败：长时间未开始加载节点，已停止");
  });

  it("timed_out：**保留占位且不派发 done**（ComfyUI 后台仍在跑，不能假装任务结束）", () => {
    const effects = canvasRunEffects("timed_out");
    expect(effects).toEqual({
      keepPlaceholder: true, dispatchDone: false, interruptQueue: false, reloadFromSnapshot: false,
      toast: { text: "运转仍在进行中（已超过预期时长）。画布会保留「生成中」节点，请稍后在 ComfyUI 控制台查看或重新运转。", kind: "info" },
    });
  });

  it("只有 timed_out 允许保留占位（其余终态都必须派发 done，否则占位永久残留）", () => {
    const terminals = ["completed", "empty", "failed", "stalled", "timed_out"] as const;
    for (const terminal of terminals) {
      const effects = canvasRunEffects(terminal);
      expect(effects.keepPlaceholder).toBe(terminal === "timed_out");
      expect(effects.dispatchDone).toBe(terminal !== "timed_out");
    }
  });
});

// 审计 R8：画布 / 弹窗 / 对话 /w 三处都硬编码 image ⇒ 视频模板吃图片 20 分钟硬上限。
// 模板结构里没有媒体种类字段，只能从抓取图节点类名推导；判错方向必须安全（宁可等久，不可提前掐断）。
describe("deriveWorkflowMediaType · 超时合同推导", () => {
  const graph = (...classes: string[]) => Object.fromEntries(
    classes.map((name, index) => [String(index + 1), { class_type: name, inputs: {} }]),
  );

  it("普通图片工作流 → image", () => {
    expect(deriveWorkflowMediaType(graph("CheckpointLoaderSimple", "KSampler", "VAEDecode", "SaveImage")))
      .toBe("image");
  });

  it("视频合成节点 → video（60 分钟合同）", () => {
    expect(deriveWorkflowMediaType(graph("KSampler", "VAEDecode", "VHS_VideoCombine"))).toBe("video");
    expect(deriveWorkflowMediaType(graph("CreateVideo", "SaveVideo"))).toBe("video");
  });

  it("音频保存节点 → audio", () => {
    expect(deriveWorkflowMediaType(graph("SaveAudio"))).toBe("audio");
    expect(deriveWorkflowMediaType(graph("VHS_AudioCombine"))).toBe("audio");
  });

  it("视频与音频同时存在时取 video（更宽的合同）", () => {
    expect(deriveWorkflowMediaType(graph("SaveAudio", "VHS_VideoCombine"))).toBe("video");
  });

  it("SaveAnimatedWEBP 这类动图**不**升级（避免把图片任务拖到 60 分钟）", () => {
    expect(deriveWorkflowMediaType(graph("SaveAnimatedWEBP"))).toBe("image");
    expect(deriveWorkflowMediaType(graph("SaveImage"))).toBe("image");
  });

  it("空图 / 畸形输入一律退回 image（默认合同＝改前行为）", () => {
    expect(deriveWorkflowMediaType({})).toBe("image");
    expect(deriveWorkflowMediaType(null)).toBe("image");
    expect(deriveWorkflowMediaType(undefined)).toBe("image");
    expect(deriveWorkflowMediaType("不是对象")).toBe("image");
    expect(deriveWorkflowMediaType({ 1: null, 2: { inputs: {} }, 3: { class_type: 42 } })).toBe("image");
  });

  it("精确匹配：名字里带 Video 但非合成节点的类不升级", () => {
    expect(deriveWorkflowMediaType(graph("VideoHelperSuite"))).toBe("image");
    expect(deriveWorkflowMediaType(graph("LoadVideo"))).toBe("image");
  });
});

// 2026-09-15 架构审计 P19：`keepPlaceholder` / `interruptQueue` 原先**没有任何生产消费方**
// （全仓 grep 只命中本文件定义 + 上面那组断言），真实中断写在两个组件的 `stalled` 回调里直接调
// `interruptComfy` **绕过表** ⇒ 表在那两点是「文档」不是「合同」。
// 现补执行口：`runCanvasEffects(effects, sink)` 是表的**唯一消费入口**，此处用 spy sink 逐格锁死。
describe("runCanvasEffects · 终态表的执行口（P19）", () => {
  function spySink() {
    const calls: string[] = [];
    const sink: CanvasRunSink = {
      keepPlaceholder: () => calls.push("keepPlaceholder"),
      dispatchDone: () => calls.push("dispatchDone"),
      interruptQueue: () => calls.push("interruptQueue"),
      reloadFromSnapshot: () => calls.push("reloadFromSnapshot"),
      toast: (text, kind) => calls.push(`toast:${kind}:${text}`),
    };
    return { calls, sink };
  }

  it("completed：提示 → 回读快照 → 派发 done（不保留占位、不中断）", () => {
    const { calls, sink } = spySink();
    runCanvasEffects(canvasRunEffects("completed", { images: 3 }), sink);
    expect(calls).toEqual([
      "toast:info:运转完成，3 张图已入库。", "reloadFromSnapshot", "dispatchDone",
    ]);
  });

  it("empty：提示 → 派发 done，但**不回读快照**", () => {
    const { calls, sink } = spySink();
    runCanvasEffects(canvasRunEffects("empty"), sink);
    expect(calls).toEqual([
      "toast:error:运转完成但未拿到图片/视频/音频（可能输出节点未正确配置），请到 ComfyUI 控制台查看。",
      "dispatchDone",
    ]);
  });

  it("stalled：**先中断队列再提示**（中断由表驱动，不再绕过表直调 interruptComfy）", () => {
    const { calls, sink } = spySink();
    runCanvasEffects(canvasRunEffects("stalled", { error: "长时间未开始加载节点，已停止" }), sink);
    expect(calls).toEqual([
      "interruptQueue", "toast:error:运转失败：长时间未开始加载节点，已停止", "dispatchDone",
    ]);
  });

  it("timed_out：只保留占位 + 提示，**绝不派发 done**", () => {
    const { calls, sink } = spySink();
    runCanvasEffects(canvasRunEffects("timed_out"), sink);
    expect(calls[0]).toMatch(/^toast:info:运转仍在进行中/);
    expect(calls).toEqual([calls[0], "keepPlaceholder"]);
  });

  it("不变量：任何终态都不会同时调 keepPlaceholder 与 dispatchDone", () => {
    const terminals = ["completed", "empty", "failed", "stalled", "timed_out"] as const;
    for (const terminal of terminals) {
      const { calls, sink } = spySink();
      runCanvasEffects(canvasRunEffects(terminal), sink);
      // 每个终态至少发一条提示（跨组件契约的可见面）
      expect(calls.filter((c) => c.startsWith("toast:"))).toHaveLength(1);
      // 保留占位与派发 done 互斥（同时为真会让占位「既留着又清掉」）
      expect(calls.includes("keepPlaceholder") && calls.includes("dispatchDone")).toBe(false);
      // timed_out 以外都必须派发 done，否则占位与 ChatView 进度列表永久残留
      expect(calls.includes("dispatchDone")).toBe(terminal !== "timed_out");
      // 只有 stalled 会打断 ComfyUI
      expect(calls.includes("interruptQueue")).toBe(terminal === "stalled");
    }
  });
});

// 2026-09-15（C4-A Step 3/4）：`pending.prompt` 从「模板名」改成「真实提示词」后，
// 恢复路径（切走仓库再切回、任务仍在跑时渲染一次）不能直接拿 `prompt` 当标签。
// 这条是不可见路径，故单独立锁。
describe("pendingRunLabel · 占位/进度条的运转任务名", () => {
  it("workflow 快照存在时取模板名（不进 prompt 的真实提示词）", () => {
    expect(pendingRunLabel({
      prompt: "1girl, silver hair, glowing green firefly, cinematic",
      regeneration: { kind: "workflow", templateName: "QRQ 立绘" },
    })).toBe("QRQ 立绘");
  });

  it("快照没有模板名时回退 prompt（兼容收口前的旧 pending）", () => {
    expect(pendingRunLabel({ prompt: "旧标签", regeneration: { kind: "workflow" } })).toBe("旧标签");
    expect(pendingRunLabel({ prompt: "旧标签" })).toBe("旧标签");
  });

  it("非 workflow 快照不吃它的字段，最后兜底「工作流」", () => {
    // 只为锁「只认 kind === 'workflow'」：非 workflow 快照即使带了同名键也不该被采纳
    expect(pendingRunLabel({ prompt: "", regeneration: { kind: "template", templateName: "X" } })).toBe("工作流");
    expect(pendingRunLabel({})).toBe("工作流");
  });
});
