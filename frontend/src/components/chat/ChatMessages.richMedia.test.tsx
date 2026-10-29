// @vitest-environment jsdom
//
// 图文混排的「哨兵被显示层吃掉」回归（2026-10-29 用户实锤「剧情对话时的图片插入我看也被破坏了」）。
//
// 媒体槽靠私有哨兵 \uE000LAF_MEDIA_<n>\uE001 在正文里定位，而显示层正则只认正文不认哨兵：
// 用户的一条正则一旦连带删掉哨兵，旧实现会在 split 结果里拿不到位置 ⇒ 已 ready 的图一张都不出。
// 这里用真实组件渲染钉两条：
//   1. 哨兵完好 ⇒ 图文按位交错（TXT,IMG,TXT）；
//   2. 哨兵被吃掉 ⇒ 位置退让到尾部（TXT,IMG），但卡片必须还在、正文必须不丢。
// 口径与后端快照一致：宁可位置退让，不许媒体静默消失。

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("dompurify", () => ({ default: { sanitize: (html: string) => html } }));
vi.mock("../../lib/planTaskActivity", () => ({
  approvePlanTask: vi.fn(), cancelPlanTask: vi.fn(), getPlanTask: vi.fn(),
  keepRecipe: vi.fn(), deleteRecipe: vi.fn(), listRecipes: vi.fn(async () => ({}) ),
}));

import { AssistantMessage } from "./ChatMessages";
import type { RegexScript } from "../../lib/regexEngine";

let container: HTMLDivElement;
let root: Root;

afterEach(() => {
  act(() => { root.unmount(); });
  container.remove();
});

const LOCAL_URL = "http://127.0.0.1:8010/api/comfyui/local-view?path=D%3A%5Cw%5Ca.png";

// 显示层正则：命中私有区成对字符 ⇒ 把整条哨兵替换成空串（等价于用户写了条覆盖私有区的显示正则）
const EAT_SENTINEL: RegexScript = {
  id: "eat-sentinel",
  scriptName: "eat-sentinel",
  findRegex: "[\\uE000-\\uF8FF][^\\uE000-\\uF8FF]*[\\uE000-\\uF8FF]",
  replaceString: "",
  placement: [2], // Placement.AI_OUTPUT
  markdownOnly: true,
  disabled: false,
};

function mount(msg: Record<string, unknown>, displayRegex?: RegexScript[]) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(<AssistantMessage msg={msg as never} onSendImage={() => {}} displayRegex={displayRegex} />);
  });
}

const parts = [
  { type: "text", text: "前段正文" },
  { type: "image", url: LOCAL_URL, slotId: "illo-req-1:last", status: "ready" },
  { type: "text", text: "后段正文" },
];

// 结构串：只取 .bot-rich-parts 的直接子节点，区分文本块与插画卡
function shape(): string {
  const block = container.querySelector(".bot-rich-parts");
  if (!block) return "(无 .bot-rich-parts)";
  return [...block.children]
    .map((c) => (c.classList.contains("img-card") ? "IMG" : "TXT"))
    .join(",");
}

describe("图文混排哨兵定位", () => {
  it("哨兵完好：图文按位交错，插画落在两段正文之间", () => {
    mount({ id: "m1", role: "assistant", text: "", parts });
    expect(shape()).toBe("TXT,IMG,TXT");
    expect(container.querySelectorAll(".img-card")).toHaveLength(1);
  });

  it("哨兵被显示层正则吃掉：位置退让到尾部，但插画一张都不许丢", () => {
    mount({ id: "m1", role: "assistant", text: "", parts }, [EAT_SENTINEL]);
    // 哨兵没了 ⇒ 正文合成一块 ⇒ 卡片只能追加到尾部（位置退让）
    expect(shape()).toBe("TXT,IMG");
    expect(container.querySelectorAll(".img-card")).toHaveLength(1);
    // 正文不能被兜底逻辑吃掉
    const block = container.querySelector(".bot-rich-parts")!;
    expect(block.textContent).toContain("前段正文");
    expect(block.textContent).toContain("后段正文");
  });

  it("多槽全部被吃掉：按原顺序整批退到尾部，不静默丢任何一张", () => {
    mount({
      id: "m2", role: "assistant", text: "",
      parts: [
        { type: "text", text: "甲" },
        { type: "image", url: LOCAL_URL, slotId: "s1", status: "ready" },
        { type: "text", text: "乙" },
        { type: "image", url: LOCAL_URL, slotId: "s2", status: "ready" },
      ],
    }, [EAT_SENTINEL]);
    expect(container.querySelectorAll(".img-card img")).toHaveLength(2);
    expect(shape()).toBe("TXT,IMG,IMG");
  });

  it("没有媒体槽时不进入混排分支（正则不该凭空造出插画卡）", () => {
    mount({ id: "m3", role: "assistant", text: "纯正文" }, [EAT_SENTINEL]);
    expect(container.querySelector(".bot-rich-parts")).toBeNull();
    expect(container.textContent).toContain("纯正文");
  });
});
