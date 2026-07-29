import { describe, it, expect } from "vitest";
import {
  needsImageInput, hasImageProvided, pickBestText, shouldFinalize, slimSnapshot,
  registerPending, unregisterPending, pendingResumeAction, pollSchedule,
} from "./chatGeneration";
import type { Template } from "../api/workflows";
import type { ChatMessage } from "../types/chat";

const tpl = (over: Partial<Template>): Template => ({
  id: "t1", name: "x", source_path: "", exposed: [],
  node_order: [], input_node_ids: [], output_node_ids: [],
  created_at: 0, updated_at: 0, ...over,
});

describe("needsImageInput", () => {
  it("有 image_node_id → 需要图", () => {
    expect(needsImageInput(tpl({ image_node_id: "5" }))).toBe(true);
  });
  it("exposed 里有 image 控件 → 需要图", () => {
    expect(needsImageInput(tpl({ exposed: [{ node_id: "5", field: "image", label: "", control: "image", semantic: "", default: null }] }))).toBe(true);
  });
  it("都没有 → 不需要", () => {
    expect(needsImageInput(tpl({}))).toBe(false);
  });
});

describe("hasImageProvided", () => {
  const t = tpl({ image_node_id: "5" });
  it("无图像输入口 → 放行", () => {
    expect(hasImageProvided({}, tpl({}))).toBe(true);
  });
  it("litegraph 结构：节点已填 widgets_values → true", () => {
    expect(hasImageProvided({ nodes: [{ id: 5, widgets_values: ["photo.png"] }] }, t)).toBe(true);
  });
  it("litegraph 结构：目标节点为空 → false", () => {
    expect(hasImageProvided({ nodes: [{ id: 5, widgets_values: [""] }] }, t)).toBe(false);
  });
  it("litegraph 结构：目标节点缺失 → false", () => {
    expect(hasImageProvided({ nodes: [{ id: 9, widgets_values: ["x"] }] }, t)).toBe(false);
  });
  it("API 结构：inputs 有非空值 → true", () => {
    expect(hasImageProvided({ "5": { inputs: { image: "photo.png" } } }, t)).toBe(true);
  });
  it("API 结构：inputs 全空 → false", () => {
    expect(hasImageProvided({ "5": { inputs: { image: "" } } }, t)).toBe(false);
  });
  it("两种结构都不匹配（如 null）→ 拿不准放行", () => {
    expect(hasImageProvided(null, t)).toBe(true);
  });
});

describe("pickBestText", () => {
  it("空/undefined → 空串", () => {
    expect(pickBestText(undefined)).toBe("");
    expect(pickBestText([])).toBe("");
  });
  it("过滤纯符号噪声段", () => {
    expect(pickBestText(["!@#$%^&*()", "有效的中文提示词"])).toBe("有效的中文提示词");
  });
  it("多段有效 → 取最长", () => {
    expect(pickBestText(["短", "更长的一段文本"])).toBe("更长的一段文本");
  });
  it("首尾空白被 trim", () => {
    expect(pickBestText(["  hello world  "])).toBe("hello world");
  });
});

describe("slimSnapshot", () => {
  const persist = async (src: string) => (src.startsWith("data:") ? "local://x" : src);
  it("用户 parts 里的 data:URI 图被落盘转小地址", async () => {
    const msgs: ChatMessage[] = [{
      id: "1", role: "user", text: "",
      parts: [{ type: "image", url: "data:image/png;base64,AAAA" }, { type: "text", text: "hi" }],
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].parts?.[0]).toMatchObject({ type: "image", url: "local://x" });
    expect(out[0].parts?.[1]).toMatchObject({ type: "text", text: "hi" });
  });
  it("已执行的 portsPlan.images 被清空", async () => {
    const msgs: ChatMessage[] = [{
      id: "1", role: "assistant", text: "",
      portsPlan: { status: "applied", images: ["data:image/png;base64,AAAA"], ops: [] } as any,
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].portsPlan?.images).toEqual([]);
  });
  it("待执行的 portsPlan.images 被落盘保留", async () => {
    const msgs: ChatMessage[] = [{
      id: "1", role: "assistant", text: "",
      portsPlan: { status: "pending", images: ["data:image/png;base64,AAAA"], ops: [] } as any,
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].portsPlan?.images).toEqual(["local://x"]);
  });
  it("工作流 UI 草稿与执行图保持不变", async () => {
    const draftGraph = { nodes: [{ id: 51, properties: { selection_data: "new" } }], links: [[1]] };
    const capturedGraph = { "51": { class_type: "DanbooruGalleryNode", inputs: { bypass_prompts: "new" } } };
    const msgs: ChatMessage[] = [{
      id: "w", role: "assistant", text: "",
      workflow: { templateId: "t", templateName: "x", draftGraph, capturedGraph, done: true },
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].workflow?.draftGraph).toEqual(draftGraph);
    expect(out[0].workflow?.capturedGraph).toEqual(capturedGraph);
  });
  it("无图消息原样返回", async () => {
    const msgs: ChatMessage[] = [{ id: "1", role: "assistant", text: "纯文本" }];
    const out = await slimSnapshot(msgs, persist);
    expect(out).toEqual(msgs);
  });
  it("重生成快照中的参考图被落盘保留", async () => {
    const msgs: ChatMessage[] = [{
      id: "g", role: "assistant", text: "", image: "result.png",
      regeneration: {
        kind: "ai-image", prompt: "p", images: ["data:image/png;base64,AAAA"],
        size: "1024x1024", quality: "high",
        model: { baseUrl: "https://example.test/v1", modelName: "image" },
      },
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].regeneration?.kind).toBe("ai-image");
    expect((out[0].regeneration as any).images).toEqual(["local://x"]);
  });
  it("蒙版附件与重生成参数中的原图和mask一起落盘", async () => {
    const data = "data:image/png;base64,AAAA";
    const msgs: ChatMessage[] = [{
      id: "masked", role: "user", text: "修改",
      parts: [{ type: "masked-image", url: data, image: data, mask: data }],
      regeneration: {
        kind: "ai-image", prompt: "修改", images: [data], imageMask: { image: data, mask: data },
        size: "1024x1024", quality: "high",
        model: { baseUrl: "https://example.test/v1", modelName: "image" },
      },
    }];

    const out = await slimSnapshot(msgs, persist);

    expect(out[0].parts?.[0]).toMatchObject({
      type: "masked-image", url: "local://x", image: "local://x", mask: "local://x",
    });
    expect((out[0].regeneration as any).imageMask).toEqual({
      image: "local://x", mask: "local://x",
    });
  });
});

describe("pending generation", () => {
  it("注册时去重后追加，且不修改输入", () => {
    const input = [
      { prompt_id: "p1", createdAt: 1 },
      { prompt_id: "p2", createdAt: 2 },
    ];
    expect(registerPending(input, "p1", 3)).toEqual([
      { prompt_id: "p2", createdAt: 2 },
      { prompt_id: "p1", createdAt: 3 },
    ]);
    expect(input).toEqual([
      { prompt_id: "p1", createdAt: 1 },
      { prompt_id: "p2", createdAt: 2 },
    ]);
  });
  it("工作流 pending 绑定自己的完整重生成快照", () => {
    const regeneration = {
      kind: "workflow" as const,
      graph: { "1": { class_type: "KSampler", inputs: { seed: 7 } } },
      comfyuiUrl: "http://127.0.0.1:8188",
      outputNodeIds: ["9"],
      prompt: "",
    };
    const out = registerPending([], "prompt-a", 1, ["9"], regeneration);
    expect(out[0].regeneration).toEqual(regeneration);
  });

  it("带主输出过滤时记录 outputNodeIds，空数组则省略该键", () => {
    expect(registerPending([], "p1", 1, ["47"])).toEqual([
      { prompt_id: "p1", createdAt: 1, outputNodeIds: ["47"] },
    ]);
    expect(registerPending([], "p1", 1, [])).toEqual([
      { prompt_id: "p1", createdAt: 1 },
    ]);
  });

  it("删除只移除指定任务并保持顺序", () => {
    const input = [
      { prompt_id: "p1", createdAt: 1 },
      { prompt_id: "p2", createdAt: 2 },
    ];
    expect(unregisterPending(input, "p1")).toEqual([{ prompt_id: "p2", createdAt: 2 }]);
    expect(unregisterPending(input, "missing")).toEqual(input);
  });

  it("恢复判定保持已处理和 30 分钟边界", () => {
    const item = { prompt_id: "p1", createdAt: 1000 };
    expect(pendingResumeAction(item, new Set(["p1"]), 1000)).toBe("skip");
    expect(pendingResumeAction(item, new Set(), 1000 + 30 * 60 * 1000)).toBe("inspect");
    expect(pendingResumeAction(item, new Set(), 1001 + 30 * 60 * 1000)).toBe("expire");
  });

  it.each([
    [149, false, 2000],
    [150, true, 15000],
    [151, false, 15000],
    [209, false, 15000],
    [210, false, null],
  ])("第 %i 次轮询维持原调度", (tries, releaseBusy, delayMs) => {
    expect(pollSchedule(tries)).toEqual({ releaseBusy, delayMs });
  });
});

describe("shouldFinalize", () => {
  const pend = (...ids: string[]) => ids.map((prompt_id) => ({ prompt_id }));

  it("无 promptId → 直接放行（老路径兼容）", () => {
    expect(shouldFinalize(undefined, [], new Set())).toBe(true);
  });
  it("promptId 在 pending 且未收尾 → 放行", () => {
    expect(shouldFinalize("p1", pend("p1"), new Set())).toBe(true);
  });
  it("promptId 已不在 pending（别的路径已收尾）→ 拦（治重挂后重复出图）", () => {
    expect(shouldFinalize("p1", pend("p2"), new Set())).toBe(false);
  });
  it("promptId 在内存已收尾集合（并发窗口）→ 拦", () => {
    expect(shouldFinalize("p1", pend("p1"), new Set(["p1"]))).toBe(false);
  });
  it("pending 为空 → 拦", () => {
    expect(shouldFinalize("p1", [], new Set())).toBe(false);
  });
  it("两闸都触发 → 拦", () => {
    expect(shouldFinalize("p1", pend("p2"), new Set(["p1"]))).toBe(false);
  });
});
