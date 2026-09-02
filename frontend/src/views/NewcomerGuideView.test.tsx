// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { NewcomerGuideView } from "./NewcomerGuideView";
import type { NavSection } from "../lib/viewRouting";

// React 19 + jsdom：必须显式声明 act 环境，否则每次 act() 都打环境警告。
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// 新人引导「链接跳转」回归测试（2026-09-02 新增能力）：
// 覆盖三类链接的渲染/跳转、文档取数成功与失败路径、相对图片改写、文档内相对 md 链接跳转。
// 数据用固定 fixture，避免测试被真实引导文案的增删带崩。

const { fetchGuideDoc } = vi.hoisted(() => ({ fetchGuideDoc: vi.fn() }));

vi.mock("../api/docs", () => ({ fetchGuideDoc }));

vi.mock("../lib/newcomerGuide", () => ({
  NEWCOMER_GUIDE_SECTIONS: [
    {
      id: "quick-start",
      title: "快速开始",
      summary: "概览",
      steps: [
        { title: "1. 导入", text: "见[导入详解](doc:docs/guide/demo.md)这一篇。", image: "" },
        { title: "2. 调用", text: "见[AI 搭工作流第 3 步](guide:workflow#3)。", image: "" },
        { title: "3. 官网", text: "见[官网](https://example.com)。", image: "" },
      ],
    },
    {
      id: "workflow",
      title: "AI 搭工作流",
      steps: [
        { title: "1. 嵌入", text: "一", image: "" },
        { title: "2. 同步", text: "二", image: "" },
        { title: "3. 设置", text: "三", image: "" },
        { title: "4. 结果", text: "回到[本节的嵌入配置](guide:workflow#1)。", image: "" },
      ],
    },
  ],
}));

const DEMO_DOC = {
  path: "docs/guide/demo.md",
  title: "演示文档",
  content: "# 演示文档\n\n正文一段\n\n![示意图](../assets/guide/demo.png)\n\n[下一篇](./next.md)",
};

let container: HTMLDivElement;
let root: Root;
let onGoSection: (section: NavSection, subView: string) => void;

async function mount(activeId = "quick-start") {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  onGoSection = vi.fn<(section: NavSection, subView: string) => void>();
  await act(async () => {
    root.render(<NewcomerGuideView activeId={activeId} onGoSection={onGoSection} />);
  });
}

async function click(el: Element | null | undefined) {
  await act(async () => {
    (el as HTMLElement)?.click();
  });
}

/** 等一帧：同章定位的 scrollIntoView 走 requestAnimationFrame。 */
async function flushFrame() {
  await act(async () => {
    await new Promise((r) => requestAnimationFrame(() => r(null)));
  });
}

function linkByText(text: string) {
  return Array.from(container.querySelectorAll(".guide-link"))
    .find((el) => (el.textContent || "").includes(text));
}

beforeEach(() => {
  window.location.hash = "";
  fetchGuideDoc.mockReset();
  // jsdom 不实现 scrollIntoView（切章定位步骤用到）
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
});

describe("NewcomerGuideView 链接渲染", () => {
  it("doc: 链接渲染成按钮（不是外链 <a>）", async () => {
    await mount();
    const el = linkByText("导入详解");
    expect(el).toBeTruthy();
    expect(el?.tagName).toBe("BUTTON");
  });

  it("外链渲染成新标签页 <a>", async () => {
    await mount();
    const el = linkByText("官网");
    expect(el?.tagName).toBe("A");
    expect(el?.getAttribute("target")).toBe("_blank");
    expect(el?.getAttribute("rel")).toBe("noreferrer");
  });

  it("步骤带锚点 id，供跨章定位滚动", async () => {
    await mount();
    expect(container.querySelector("#guide-step-quick-start-2")).toBeTruthy();
  });
});

describe("NewcomerGuideView 跳转行为", () => {
  it("guide: 链接点击 → 切到目标章节", async () => {
    await mount();
    await click(linkByText("AI 搭工作流第 3 步"));

    expect(onGoSection).toHaveBeenCalledWith("guide", "workflow");
  });

  it("guide: 目标是当前章节时不切章，只滚动定位到该步", async () => {
    await mount("workflow");
    await click(linkByText("本节的嵌入配置"));
    await flushFrame();

    expect(onGoSection).not.toHaveBeenCalled();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("doc: 链接点击 → 写 hash 并加载文档", async () => {
    fetchGuideDoc.mockResolvedValue(DEMO_DOC);
    await mount();
    await click(linkByText("导入详解"));

    expect(window.location.hash).toBe("#/guide/quick-start/doc/docs/guide/demo.md");
    expect(fetchGuideDoc).toHaveBeenCalledWith("docs/guide/demo.md");
    expect(container.textContent).toContain("正文一段");
  });

  it("文档正文里的相对图片改写成 docs/assets 静态地址", async () => {
    fetchGuideDoc.mockResolvedValue(DEMO_DOC);
    await mount();
    await click(linkByText("导入详解"));
    // 图片改写跑在渲染后的 effect 里
    expect(container.querySelector(".guide-doc img")?.getAttribute("src"))
      .toBe("/docs-assets/guide/demo.png");
  });

  it("文档里的一级标题不重复显示（页头已显示标题）", async () => {
    fetchGuideDoc.mockResolvedValue(DEMO_DOC);
    await mount();
    await click(linkByText("导入详解"));

    const h1s = Array.from(container.querySelectorAll(".guide-doc h1")).map((n) => n.textContent);
    expect(h1s).toEqual([]);
  });

  it("文档里的相对 md 链接接进阅读态，不跳走", async () => {
    fetchGuideDoc.mockResolvedValue(DEMO_DOC);
    await mount();
    await click(linkByText("导入详解"));

    const inner = container.querySelector('.guide-doc a[href="./next.md"]') as HTMLAnchorElement;
    expect(inner).toBeTruthy();
    await click(inner);

    expect(window.location.hash).toBe("#/guide/quick-start/doc/docs/guide/next.md");
    expect(fetchGuideDoc).toHaveBeenLastCalledWith("docs/guide/next.md");
  });

  it("取数失败 → 显示错误与重试，重试后成功渲染", async () => {
    fetchGuideDoc.mockRejectedValueOnce(new Error("文档不存在"));
    await mount();
    await click(linkByText("导入详解"));

    expect(container.textContent).toContain("文档读取失败");
    expect(container.textContent).toContain("文档不存在");

    fetchGuideDoc.mockResolvedValue(DEMO_DOC);
    await click(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "重试"));
    expect(container.textContent).toContain("正文一段");
  });

  it("从文档页返回 → 回到章节且 hash 去掉 doc 段", async () => {
    fetchGuideDoc.mockResolvedValue(DEMO_DOC);
    await mount();
    await click(linkByText("导入详解"));
    expect(container.textContent).toContain("正文一段");

    await click(Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").includes("返回")));
    expect(window.location.hash).toBe("#/guide/quick-start");
    expect(container.textContent).toContain("快速开始");
  });
});
