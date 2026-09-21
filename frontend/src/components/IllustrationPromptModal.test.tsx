// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";

import { IllustrationPromptModal } from "./IllustrationPromptModal";

// 提示词设置（多元数据插入右上角齿轮）的回归网。钉三件事：
// ①「想让模型怎么想」可编辑并回传；
// ②「程序怎么读」只读——输出合同只展示，界面上不提供任何编辑入口（改了会让判定结果无法被解析）；
// ③ **没有「启用」开关**（2026-09-18 用户定案）：判据档常开，界面上不得再出现勾选框等开关控件。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SETTINGS = {
  climax_anchor: { rubric: "用户自己的判据" },
  default_rubric: "缺省判据",
  output_contract: "字段固定为 anchor、composition、subjects、reason",
};

let container: HTMLDivElement;
let root: Root;

async function mount(el: ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(el); });
  await act(async () => { /* 等 fetch 链落地 */ });
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.unstubAllGlobals();
});

function stubFetch(handler: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => new Response(
    JSON.stringify(handler(String(url), init)),
    { status: 200, headers: { "Content-Type": "application/json" } },
  ));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button"))
    .find((b) => (b.textContent || "").includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found as HTMLButtonElement;
}

async function typeInto(el: Element | null, value: string) {
  const node = el as HTMLTextAreaElement | null;
  if (!node) throw new Error("textarea missing");
  const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value");
  await act(async () => {
    desc?.set?.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function rubricBox(): HTMLTextAreaElement {
  const node = container.querySelector("textarea");
  if (!node) throw new Error("rubric textarea missing");
  return node as HTMLTextAreaElement;
}

describe("IllustrationPromptModal", () => {
  it("回填判据、只读展示输出合同，且没有启用开关", async () => {
    stubFetch(() => SETTINGS);
    await mount(<IllustrationPromptModal onClose={() => {}} />);

    expect(rubricBox().value).toBe("用户自己的判据");
    expect(rubricBox().readOnly).toBe(false);
    const readonly = container.querySelector("pre");
    expect(readonly?.textContent).toContain("anchor、composition、subjects、reason");
    // 只读区不得退化成可编辑控件
    expect(container.querySelectorAll("textarea")).toHaveLength(1);
    // 「常开」：界面上不存在任何开关控件
    expect(container.querySelector("input[type=checkbox]")).toBeNull();
    expect(container.querySelector("input[type=radio]")).toBeNull();
    expect(container.textContent).toContain("常开");
  });

  it("保存回传当前判据", async () => {
    const fetchMock = stubFetch((url, init) => (
      init?.method === "POST" ? JSON.parse(String(init.body)) : SETTINGS
    ));
    await mount(<IllustrationPromptModal onClose={() => {}} />);

    await typeInto(rubricBox(), "我自己改的判据");
    await act(async () => { button("保存").click(); });

    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit)?.body))).toEqual({
      climax_anchor: { rubric: "我自己改的判据" },
    });
  });

  it("恢复默认判据只回填输入框、不落盘", async () => {
    const fetchMock = stubFetch(() => SETTINGS);
    await mount(<IllustrationPromptModal onClose={() => {}} />);

    await typeInto(rubricBox(), "写坏了");
    await act(async () => { button("恢复默认判据").click(); });

    expect(rubricBox().value).toBe("缺省判据");
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST")).toHaveLength(0);
  });

  it("留空判据保存后以后端回填为准", async () => {
    stubFetch((_url, init) => (
      init?.method === "POST"
        ? { ...SETTINGS, climax_anchor: { rubric: "缺省判据" } }
        : SETTINGS
    ));
    await mount(<IllustrationPromptModal onClose={() => {}} />);

    await typeInto(rubricBox(), "");
    await act(async () => { button("保存").click(); });

    expect(rubricBox().value).toBe("缺省判据");
  });
});
