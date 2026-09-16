// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { CanvasDetailPanel } from "./CanvasDetailPanel";
import type { CanvasNode, NodeDetailData } from "../../lib/canvasRuntime";

// T2.1-D2：详情面板外移后的回归网（行为网 + 逐字搬移锁 + 源码形态锁）。
// 硬要求：每个用例必须卸载；源码只能在 it 回调内读（jsdom 模块求值期 import.meta.url 非 file:）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../AudioPlayer", () => ({
  AudioPlayer: (p: { src: string }) => <div data-testid="audio-stub" data-src={p.src} />,
}));

const SEAM = "./CanvasDetailPanel.tsx";
const VIEW = "../../views/CanvasStageFlow.tsx";

function read(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
}
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").map((l) => l.replace(/(?<!:)\/\/.*$/, "")).join("\n");
}
function count(hay: string, needle: string): number {
  let n = 0;
  let i = 0;
  for (;;) {
    const at = hay.indexOf(needle, i);
    if (at === -1) return n;
    n += 1;
    i = at + needle.length;
  }
}

let container: HTMLDivElement;
let root: Root;

const node = (over: Record<string, unknown>) => ({ id: "n1", type: "image-group", ...over }) as unknown as CanvasNode;
const detailOf = (over: Partial<NodeDetailData> = {}): NodeDetailData =>
  ({ id: "n1", type: "image-group", title: "生成图组", gens: [], prompt: "提示词P", imageUrls: ["u1"], ...over });

interface Fix {
  node?: CanvasNode;
  detail?: NodeDetailData;
  sizes?: Record<string, { w: number; h: number }>;
  displayRegex?: unknown[];
  showStoryThinking?: boolean;
}

async function mountPanel(fix: Fix = {}) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  const mocks = {
    setDetailNode: vi.fn(),
    setShowStoryThinking: vi.fn(),
    openSend: vi.fn(),
    requestDelete: vi.fn(),
  };
  const detailHtmlRef = { current: null as HTMLDivElement | null };
  const naturalSizesRef = { current: fix.sizes ?? {} };
  const el = (
    <CanvasDetailPanel
      detailNode={(fix.node ?? node({})) as CanvasNode}
      detail={fix.detail ?? detailOf()}
      setDetailNode={mocks.setDetailNode}
      showStoryThinking={fix.showStoryThinking ?? false}
      setShowStoryThinking={mocks.setShowStoryThinking}
      detailHtmlRef={detailHtmlRef}
      naturalSizesRef={naturalSizesRef}
      displayRegex={fix.displayRegex as never}
      openSend={mocks.openSend}
      requestDelete={mocks.requestDelete}
    />
  );
  await act(async () => {
    root.render(el as ReactElement);
  });
  return { mocks, detailHtmlRef, naturalSizesRef };
}

async function click(el: Element | null | undefined) {
  await act(async () => {
    (el as HTMLElement | null)?.click();
  });
}
function btn(label: string): HTMLElement | null {
  return (Array.from(container.querySelectorAll("button")).find(
    (b) => (b.textContent || "").trim() === label,
  ) || null) as HTMLElement | null;
}
const mask = () => container.querySelector(".modal-mask");
const modal = () => container.querySelector(".modal");

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container?.remove();
});

// ───────────── 行为网 ─────────────
describe("CanvasDetailPanel 非剧情分支", () => {
  it("标题 + 提示词 + 三个按钮逐字", async () => {
    await mountPanel();
    expect(container.querySelector("h3")?.textContent).toBe("生成图组");
    expect(container.textContent).toContain("提示词");
    expect(container.textContent).toContain("提示词P");
    expect(btn("发送至对话框")).toBeTruthy();
    expect(btn("发送至对话")).toBeTruthy();
    expect(btn("删除")).toBeTruthy();
  });

  it("提示词为空 → 「（无提示词记录）」", async () => {
    await mountPanel({ detail: detailOf({ prompt: "" }) });
    expect(container.textContent).toContain("（无提示词记录）");
  });

  it("原图尺寸：naturalSizesRef 命中 → 「原图尺寸 800 × 600」；未命中 → 不渲染", async () => {
    await mountPanel({ sizes: { u1: { w: 800, h: 600 } } });
    expect(container.textContent).toContain("原图尺寸 800 × 600");
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel();
    expect(container.textContent).not.toContain("原图尺寸");
  });

  it("「发送至对话框」payload：空文本 + 首图", async () => {
    const { mocks } = await mountPanel();
    await click(btn("发送至对话框"));
    expect(mocks.openSend).toHaveBeenCalledWith("发送至对话框", { text: "", images: ["u1"] });
  });

  it("「发送至对话」payload：提示词回填", async () => {
    const { mocks } = await mountPanel();
    await click(btn("发送至对话"));
    expect(mocks.openSend).toHaveBeenCalledWith("发送至对话", { text: "提示词：提示词P", images: ["u1"], prompt: "提示词P" });
  });

  it("「删除」→ requestDelete([detailNode.id]) + setDetailNode(null)", async () => {
    const { mocks } = await mountPanel();
    await click(btn("删除"));
    expect(mocks.requestDelete).toHaveBeenCalledWith(["n1"]);
    expect(mocks.setDetailNode).toHaveBeenCalledWith(null);
  });

  it("图片 src 取 imageUrls[0]；大图 alt=「大图」", async () => {
    await mountPanel();
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toBe("u1");
    expect(img?.getAttribute("alt")).toBe("大图");
  });

  it("参考图节点：有图用 imageUrls[0]，无图 → 「🖼️ 参考图加载中…」", async () => {
    await mountPanel({ node: node({ type: "reference-image", referenceImageTitle: "参考一" }), detail: detailOf({ type: "reference-image", imageUrls: [] }) });
    expect(container.textContent).toContain("🖼️ 参考图加载中…");
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel({ node: node({ type: "reference-image", referenceImageTitle: "参考一" }), detail: detailOf({ type: "reference-image", imageUrls: ["r1"] }) });
    expect(container.querySelector("img")?.getAttribute("alt")).toBe("参考一");
  });

  it("输入节点：draft 与生成中两种文案", async () => {
    await mountPanel({ node: node({ type: "input", inputStatus: "draft" }), detail: detailOf({ type: "input" }) });
    expect(container.textContent).toContain("输入节点（双击卡片输入提示词）");
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel({ node: node({ type: "input", inputStatus: "running" }), detail: detailOf({ type: "input" }) });
    expect(container.textContent).toContain("输入节点（生成中…）");
  });
});

describe("CanvasDetailPanel 剧情分支", () => {
  it("媒体并行渲染：video 与 image 同时挂载（非互斥）；音频行；四者全无 → 空置提示", async () => {
    await mountPanel({ node: node({ type: "story", storyVideo: "v.mp4", storyImage: "i.png" }), detail: detailOf({ type: "story", title: "剧情楼层" }) });
    expect(container.querySelector("video")?.getAttribute("src")).toBe("v.mp4");
    expect(container.querySelector("img")?.getAttribute("src")).toBe("i.png");
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel({ node: node({ type: "story", storyImage: "i.png", storyAudio: "a.mp3" }), detail: detailOf({ type: "story" }) });
    expect(container.querySelector("img")?.getAttribute("src")).toBe("i.png");
    expect(container.querySelector("video")).toBeNull();
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel({ node: node({ type: "story", storyAudio: "a.mp3" }), detail: detailOf({ type: "story" }) });
    expect(container.querySelector("[data-testid=audio-stub]")?.getAttribute("data-src")).toBe("a.mp3");
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel({ node: node({ type: "story" }), detail: detailOf({ type: "story" }) });
    expect(container.textContent).toContain("本楼层无配图 / 视频 / 音频");
  });

  it("storyAudioLines 多行 → 每行一个播放器 + 讲者标签", async () => {
    await mountPanel({
      node: node({ type: "story", storyAudioLines: [{ speaker: "甲", url: "a1.mp3" }, { speaker: "", url: "a2.mp3" }] }),
      detail: detailOf({ type: "story" }),
    });
    const stubs = container.querySelectorAll("[data-testid=audio-stub]");
    expect(stubs.length).toBe(2);
    expect(stubs[0].getAttribute("data-src")).toBe("a1.mp3");
    expect(container.querySelector(".audio-speaker-label")?.textContent).toBe("甲");
  });

  it("渲染管线：正文进 bot-html（renderMarkdown 输出）；空楼层兜底「（空楼层）」", async () => {
    await mountPanel({ node: node({ type: "story", storyText: "正文**加粗**A" }), detail: detailOf({ type: "story" }) });
    const html = container.querySelector(".bot-html") as HTMLElement | null;
    expect(html).toBeTruthy();
    expect(html?.innerHTML).toContain("<strong>加粗</strong>");
    expect(html?.textContent).toContain("正文加粗A");
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel({ node: node({ type: "story" }), detail: detailOf({ type: "story" }) });
    expect((container.querySelector(".bot-html") as HTMLElement).innerHTML).toContain("（空楼层）");
  });

  it("思考块：默认「查看思考」；点击函数式翻转；展开后显示正文", async () => {
    const { mocks } = await mountPanel({
      node: node({ type: "story", storyThinking: "思考过程" }),
      detail: detailOf({ type: "story" }),
    });
    expect(container.querySelector(".thinking-body")).toBeNull();
    await click(btn("查看思考"));
    expect(mocks.setShowStoryThinking).toHaveBeenCalledTimes(1);
    const updater = mocks.setShowStoryThinking.mock.calls[0][0] as (s: boolean) => boolean;
    expect(updater(false)).toBe(true);
    act(() => {
      root.unmount();
    });
    container.remove();
    await mountPanel({
      node: node({ type: "story", storyThinking: "思考过程" }),
      detail: detailOf({ type: "story" }),
      showStoryThinking: true,
    });
    expect(container.querySelector(".thinking-body")?.textContent).toBe("思考过程");
    expect(btn("收起思考")).toBeTruthy();
  });
});

describe("CanvasDetailPanel 关闭行为", () => {
  it("X 按钮 / 遮罩 → setDetailNode(null)；内层点击不冒泡；bot-html 挂 detailHtmlRef（剧情分支）", async () => {
    const { mocks, detailHtmlRef } = await mountPanel({ node: node({ type: "story", storyText: "x" }), detail: detailOf({ type: "story" }) });
    expect(detailHtmlRef.current).toBeInstanceOf(HTMLElement);
    await click(modal());
    expect(mocks.setDetailNode).not.toHaveBeenCalled();
    await click(container.querySelector(".icon-btn"));
    expect(mocks.setDetailNode).toHaveBeenCalledWith(null);
    mocks.setDetailNode.mockClear();
    await click(mask());
    expect(mocks.setDetailNode).toHaveBeenCalledWith(null);
  });
});

// ───────────── 逐字搬移锁 + 源码形态锁 ─────────────
describe("逐字搬移锁（T2.1-D2 详情面板）", () => {
  const MOVED: [string, number][] = [
    ['width: detailNode.type === "story" ? "92vw" : 760,', 1],
    ["runScripts(detailNode.storyText, Placement.AI_OUTPUT, displayRegex, { isMarkdown: true, depth: 0 })", 1],
    ["本楼层无配图 / 视频 / 音频", 1],
    ["(detail.gens[0] as { video_url?: string } | undefined)?.video_url", 1],
    ["原图尺寸", 1],
    ["requestDelete([detailNode.id]);", 1],
    ["first.templateName || \"\"", 1],
    ["Array.isArray(first.loraNames) ? first.loraNames : []", 1],
    ["detailNode.storyAudioLines?.length ? detailNode.storyAudioLines : (detailNode.storyAudio ? [{ speaker: \"\", url: detailNode.storyAudio }] : [])", 1],
  ];

  it("9 条判定/渲染管线语句在新属主按次数出现", () => {
    const seam = stripComments(read(SEAM));
    for (const [needle, n] of MOVED) {
      expect([needle, count(seam, needle)]).toEqual([needle, n]);
    }
  });

  it("同样的语句在视图缺席（确属搬移，而非复制）", () => {
    const view = stripComments(read(VIEW));
    const gone = [
      "本楼层无配图 / 视频 / 音频",
      "runScripts(detailNode.storyText",
      "requestDelete([detailNode.id]);",
      "<AudioPlayer src=",
      'openSend("发送至对话框"',
    ];
    for (const needle of gone) {
      expect([needle, count(view, needle)]).toEqual([needle, 0]);
    }
  });
});

describe("源码形态锁（T2.1-D2 详情面板）", () => {
  it("视图恰一次使用 + 调用块逐字（10 props 同名接线）", () => {
    const view = read(VIEW);
    const block = [
      "      {detail && detailNode && (",
      "        <CanvasDetailPanel",
      "          detailNode={detailNode}",
      "          detail={detail}",
      "          setDetailNode={setDetailNode}",
      "          showStoryThinking={showStoryThinking}",
      "          setShowStoryThinking={setShowStoryThinking}",
      "          detailHtmlRef={detailHtmlRef}",
      "          naturalSizesRef={naturalSizesRef}",
      "          displayRegex={displayRegex}",
      "          openSend={openSend}",
      "          requestDelete={requestDelete}",
      "        />",
      "      )}",
    ].join("\n");
    expect(view.includes(block)).toBe(true);
    expect([count(view, "<CanvasDetailPanel"), 1]).toEqual([count(view, "<CanvasDetailPanel"), 1]);
  });
});
