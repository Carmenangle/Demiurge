import { describe, expect, it } from "vitest";
import {
  conversationMediaUrls, conversationTurnUrls, filterGensByConversation,
  isBoundInspirationCard, isStoryNode, messageMedia, normalizeMediaUrl, projectStoryNodes, projectWorkflowTools,
  pruneUnboundInspirationCards,
} from "./canvasConversation";
import type { ChatMessage } from "../types/chat";

describe("normalizeMediaUrl", () => {
  it("local-view 取 path 参数", () => {
    expect(normalizeMediaUrl("/local-view?path=D%3A%5Cx%5C1.png")).toBe("path:D:\\x\\1.png");
  });
  it("普通 URL 去掉 query", () => {
    expect(normalizeMediaUrl("http://a/b.png?t=1")).toBe("url:/b.png");
  });
  it("空串返回空", () => {
    expect(normalizeMediaUrl("")).toBe("");
  });
});

describe("conversationMediaUrls", () => {
  it("收集 image/video/audio/parts 里的媒体 URL", () => {
    const msgs = [
      { id: "1", role: "assistant", text: "", image: "/local-view?path=a.png" },
      { id: "2", role: "assistant", text: "", parts: [{ type: "image", url: "/local-view?path=b.png" }] },
      { id: "3", role: "assistant", text: "", video: "http://x/v.mp4" },
      { id: "4", role: "assistant", text: "", audio: "/local-view?path=c.wav" },
    ] as ChatMessage[];
    const urls = conversationMediaUrls(msgs);
    expect(urls.has("path:a.png")).toBe(true);
    expect(urls.has("path:b.png")).toBe(true);
    expect(urls.has("url:/v.mp4")).toBe(true);
    expect(urls.has("path:c.wav")).toBe(true);
  });
});

describe("conversationTurnUrls", () => {
  it("从持久化 history turns 提取媒体 URL（归一化）", () => {
    const turns = [
      { role: "user", content: "hi", images: ["/local-view?path=a.png"] },
      { role: "assistant", content: "ok", images: ["http://x/v.mp4"] },
      { role: "user", content: "no img" },
    ];
    const urls = conversationTurnUrls(turns);
    expect(urls.has("path:a.png")).toBe(true);
    expect(urls.has("url:/v.mp4")).toBe(true);
    expect(urls.size).toBe(2);
  });
  it("空 turns 返回空集", () => {
    expect(conversationTurnUrls([]).size).toBe(0);
  });
});

describe("filterGensByConversation", () => {
  it("对话为空则过滤为空", () => {
    const gens = [{ id: "1", prompt: "p", image_url: "/local-view?path=a.png" }];
    expect(filterGensByConversation(gens, new Set())).toHaveLength(0);
  });
  it("只保留出现在对话里的记录", () => {
    const gens = [
      { id: "1", prompt: "p", image_url: "/local-view?path=a.png" },
      { id: "2", prompt: "q", image_url: "/local-view?path=b.png" },
    ];
    const out = filterGensByConversation(gens, new Set(["path:a.png"]));
    expect(out.map((g) => g.id)).toEqual(["1"]);
  });
});

describe("isBoundInspirationCard", () => {
  const boundCards = new Set(["Cecilia"]);
  it("无 sourceRef 保留", () => {
    expect(isBoundInspirationCard({ kind: "character" }, boundCards, "wb1", "p1")).toBe(true);
  });
  it("char 卡按绑定名过滤", () => {
    expect(isBoundInspirationCard({ kind: "character", sourceRef: "char:Cecilia" }, boundCards, "wb1", "p1")).toBe(true);
    expect(isBoundInspirationCard({ kind: "character", sourceRef: "char:Other" }, boundCards, "wb1", "p1")).toBe(false);
  });
  it("wb 卡按绑定世界书过滤", () => {
    expect(isBoundInspirationCard({ kind: "worldbook-entry", sourceRef: "wb:wb1:0" }, boundCards, "wb1", "p1")).toBe(true);
    expect(isBoundInspirationCard({ kind: "worldbook-entry", sourceRef: "wb:wb2:0" }, boundCards, "wb1", "p1")).toBe(false);
  });
  it("preset 卡按激活预设过滤", () => {
    expect(isBoundInspirationCard({ kind: "preset", sourceRef: "preset:p1:0" }, boundCards, "wb1", "p1")).toBe(true);
    expect(isBoundInspirationCard({ kind: "preset", sourceRef: "preset:p2:0" }, boundCards, "wb1", "p1")).toBe(false);
  });
});

describe("pruneUnboundInspirationCards", () => {
  it("移除未绑定来源的卡，保留自建卡", () => {
    const cards = [
      { kind: "character", sourceRef: "char:Cecilia" },
      { kind: "character", sourceRef: "char:Other" },
      { kind: "character" }, // 自建，无 sourceRef
    ];
    const out = pruneUnboundInspirationCards(cards, new Set(["Cecilia"]), "", "");
    expect(out).toHaveLength(2);
  });
});

describe("projectWorkflowTools", () => {
  it("从对话 workflow 消息投影工具卡（同模板去重）", () => {
    const msgs = [
      { id: "1", role: "assistant", text: "", workflow: { templateId: "t1", templateName: "模板A", draftGraph: { a: 1 }, capturedGraph: null, done: false } },
      { id: "2", role: "assistant", text: "", workflow: { templateId: "t1", templateName: "模板A", draftGraph: null, capturedGraph: { b: 2 }, done: true } },
      { id: "3", role: "assistant", text: "", workflow: { templateId: "t2", templateName: "模板B", draftGraph: null, capturedGraph: null, done: false } },
      { id: "4", role: "assistant", text: "普通剧情" },
    ] as ChatMessage[];
    const out = projectWorkflowTools(msgs);
    expect(out).toHaveLength(2);
    // 同模板取最后一条（done 状态最新）
    const t1 = out.find((t) => t.templateId === "t1")!;
    expect(t1.id).toBe("wftool-t1");
    expect(t1.wfConfirmed).toBe(true);
    expect(t1.wfCaptured).toEqual({ b: 2 });
    const t2 = out.find((t) => t.templateId === "t2")!;
    expect(t2.id).toBe("wftool-t2");
  });
  it("无 workflow 消息返回空", () => {
    expect(projectWorkflowTools([{ id: "1", role: "assistant", text: "hi" }] as ChatMessage[])).toHaveLength(0);
  });
});

describe("projectStoryNodes", () => {
  it("assistant 剧情文本每楼层投影一节点", () => {
    const msgs = [
      { id: "m1", role: "assistant", text: "第一层剧情", route: "roleplay" },
      { id: "m2", role: "user", text: "用户输入" },
      { id: "m3", role: "assistant", text: "第二层剧情", route: "roleplay" },
      { id: "m4", role: "assistant", text: "", workflow: { templateId: "t1", templateName: "A" } },
    ] as ChatMessage[];
    const out = projectStoryNodes(msgs);
    expect(out.map((s) => s.id)).toEqual(["story-m1", "story-m3"]);
    expect(out[0].text).toBe("第一层剧情");
  });
  it("跳过空文本/特殊卡", () => {
    const msgs = [
      { id: "m1", role: "assistant", text: "  " },
      { id: "m2", role: "assistant", text: "", inspiration: { title: "x", content: "y" } },
      { id: "m3", role: "assistant", text: "正文", route: "roleplay" },
    ] as ChatMessage[];
    expect(projectStoryNodes(msgs).map((s) => s.id)).toEqual(["story-m3"]);
  });
  it("携带剧情顺序序号与总数（消息数组顺序 = 剧情顺序）", () => {
    const msgs = [
      { id: "m1", role: "assistant", text: "第一段", route: "roleplay" },
      { id: "m2", role: "user", text: "用户输入" },
      { id: "m3", role: "assistant", text: "第二段", route: "roleplay" },
      { id: "m4", role: "assistant", text: "第三段", route: "answer" },
    ] as ChatMessage[];
    const out = projectStoryNodes(msgs);
    expect(out.map((s) => s.index)).toEqual([0, 1, 2]);
    expect(out.map((s) => s.total)).toEqual([3, 3, 3]);
    // 顺序 = 消息出现顺序，与 id 无关
    expect(out.map((s) => s.id)).toEqual(["story-m1", "story-m3", "story-m4"]);
  });
});

describe("isStoryNode（按调度主管分派的 Agent 路由标签判定）", () => {
  it("剧情路由 roleplay/answer 才是剧情节点", () => {
    expect(isStoryNode({ id: "1", role: "assistant", text: "正文", route: "roleplay" } as ChatMessage)).toBe(true);
    expect(isStoryNode({ id: "2", role: "assistant", text: "正文", route: "answer" } as ChatMessage)).toBe(true);
    expect(isStoryNode({ id: "3", role: "assistant", text: "提示词", route: "generate" } as ChatMessage)).toBe(false);
    expect(isStoryNode({ id: "4", role: "assistant", text: "提示词", route: "video" } as ChatMessage)).toBe(false);
    expect(isStoryNode({ id: "5", role: "assistant", text: "反推提示词", route: "analyze" } as ChatMessage)).toBe(false);
  });
  it("状态/Toast 消息（system）不是剧情节点", () => {
    expect(isStoryNode({ id: "1", role: "assistant", text: "已提交到 ComfyUI 生成…", system: true } as ChatMessage)).toBe(false);
  });
  it("无标签消息无法确认是剧情产出 → 默认不是剧情节点（旧数据由迁移回填标签）", () => {
    expect(isStoryNode({ id: "1", role: "assistant", text: "旧正文（未迁移）" } as ChatMessage)).toBe(false);
    expect(isStoryNode({ id: "2", role: "user", text: "用户" } as ChatMessage)).toBe(false);
    expect(isStoryNode({ id: "3", role: "assistant", text: "旧 toast 文本", route: undefined } as ChatMessage)).toBe(false);
  });
  it("顶层媒体气泡（工作流/Agent 产出图/视频/音频）不是剧情节点", () => {
    expect(isStoryNode({ id: "1", role: "assistant", text: "1girl, portrait", image: "a.png" } as ChatMessage)).toBe(false);
    expect(isStoryNode({ id: "2", role: "assistant", text: "girl dancing", video: "v.mp4" } as ChatMessage)).toBe(false);
    expect(isStoryNode({ id: "3", role: "assistant", text: "voiceover", audio: "s.wav" } as ChatMessage)).toBe(false);
    // 自动插画走 parts 媒体槽：带剧情标签的正文消息仍是剧情节点（封面来源）
    expect(isStoryNode({ id: "4", role: "assistant", text: "正文", route: "roleplay", parts: [{ type: "image", url: "cover.png" }] } as ChatMessage)).toBe(true);
  });
});

describe("messageMedia", () => {
  it("优先顶层 image/video/audio，其次 parts", () => {
    expect(messageMedia({ id: "1", role: "assistant", text: "", image: "a.png" } as ChatMessage).image).toBe("a.png");
    expect(messageMedia({ id: "2", role: "assistant", text: "", video: "v.mp4" } as ChatMessage).video).toBe("v.mp4");
    expect(messageMedia({ id: "3", role: "assistant", text: "", audio: "s.wav" } as ChatMessage).audio).toBe("s.wav");
    expect(messageMedia({ id: "4", role: "assistant", text: "", parts: [{ type: "image", url: "b.png" }] } as ChatMessage).image).toBe("b.png");
    expect(messageMedia({ id: "5", role: "assistant", text: "" } as ChatMessage).image).toBe("");
  });
  it("剧情节点投影携带封面图（parts 自动插画）与音频", () => {
    const out = projectStoryNodes([
      { id: "m1", role: "assistant", text: "正文", route: "roleplay", parts: [{ type: "image", url: "cover.png", status: "ready" }] },
      { id: "m2", role: "assistant", text: "旁白", route: "roleplay", parts: [{ type: "audio", url: "/local-view?path=n.wav", status: "ready" }] },
    ] as ChatMessage[]);
    expect(out[0].image).toBe("cover.png");
    expect(out[1].audio).toBe("/local-view?path=n.wav");
  });
  it("音频分条按台词顺序携带角色名（楼层逐条播放标签）", () => {
    const out = projectStoryNodes([
      { id: "m1", role: "assistant", text: "正文", route: "roleplay", parts: [
        { type: "audio", url: "/local-view?path=a.wav", status: "ready", speaker: "阿尼玛", slotId: "s1" },
        { type: "audio", url: "/local-view?path=b.wav", status: "ready", speaker: "李四", slotId: "s2" },
        { type: "audio", url: "/local-view?path=c.wav", status: "ready", speaker: "阿尼玛", slotId: "s3" },
      ] },
    ] as ChatMessage[]);
    expect(out[0].audioLines).toEqual([
      { speaker: "阿尼玛", url: "/local-view?path=a.wav" },
      { speaker: "李四", url: "/local-view?path=b.wav" },
      { speaker: "阿尼玛", url: "/local-view?path=c.wav" },
    ]);
    // audio 封面 = 第一条分条
    expect(out[0].audio).toBe("/local-view?path=a.wav");
  });
  it("无 speaker 的旧音频分条仍投影（speaker 空串兜底）", () => {
    const out = projectStoryNodes([
      { id: "m1", role: "assistant", text: "正文", route: "roleplay",
        parts: [{ type: "audio", url: "/local-view?path=n.wav", status: "ready" }] },
    ] as ChatMessage[]);
    expect(out[0].audioLines).toEqual([{ speaker: "", url: "/local-view?path=n.wav" }]);
  });
});
