// @vitest-environment jsdom
// 「用户人设」区块住在**通用**面板：多档新增/删除、人设名写回、选中档切换，
// 并钉住它不再出现在「路径与代理」里（区块搬迁后不要漂回去）。
import { afterEach, describe, expect, it } from "vitest";
import { act, useState, type ComponentType } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { Settings } from "../../stores/settings";
import type { PanelProps } from "./GeneralPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let current: Settings;

function base(over: Record<string, unknown> = {}): Settings {
  return {
    workflowDir: "",
    outputDir: "",
    characterDir: "",
    worldbookDir: "",
    presetDir: "",
    comfyuiPath: "",
    comfyuiUrl: "http://127.0.0.1:8188",
    comfyuiPython: "",
    comfyExtra: [],
    comfyRoute: {},
    proxyUrl: "",
    searchProvider: "auto",
    modelsDir: "",
    userPersonas: [],
    activeUserPersonaId: undefined,
    ...over,
  } as unknown as Settings;
}

async function loadPanel(which: "misc" | "paths"): Promise<ComponentType<PanelProps>> {
  if (which === "misc") return (await import("./MiscPanel")).MiscPanel;
  return (await import("./PathsPanel")).PathsPanel;
}

async function mount(Panel: "misc" | "paths", over: Record<string, unknown> = {}) {
  const Comp = await loadPanel(Panel);
  const initial = base(over);
  current = initial;
  function Harness() {
    const [d, setD] = useState<Settings>(initial);
    current = d;
    return <Comp draft={d} setDraft={setD} />;
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<Harness />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

function text() {
  return container?.textContent ?? "";
}

function button(label: string) {
  return Array.from(container?.querySelectorAll("button") ?? [])
    .find((b) => (b.textContent ?? "").includes(label)) as HTMLButtonElement | undefined;
}

function nameInputs() {
  return Array.from(container?.querySelectorAll("input[placeholder^='人设名']") ?? []) as HTMLInputElement[];
}

function radios() {
  return Array.from(container?.querySelectorAll("input[type=radio][name='active-user-persona']") ?? []) as HTMLInputElement[];
}

function deleteButtons() {
  return Array.from(container?.querySelectorAll("button[title='删除此人设']") ?? []) as HTMLButtonElement[];
}

function setInput(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  root = null;
  container = null;
});

describe("通用面板 · 用户人设", () => {
  it("点「新增人设」建一档、首个自动选中，人设名写回草稿", async () => {
    await mount("misc");
    expect(text()).toContain("还没有用户人设");
    act(() => { button("新增人设")?.click(); });
    expect(current.userPersonas).toHaveLength(1);
    const id = current.userPersonas![0].id;
    expect(current.activeUserPersonaId).toBe(id);
    expect(radios()[0].checked).toBe(true);
    setInput(nameInputs()[0], "凌一");
    expect(current.userPersonas![0].name).toBe("凌一");
  });

  it("多档时切换选中档；删除选中档后清空选中", async () => {
    const a = { id: "p-a", name: "甲", content: "" };
    const b = { id: "p-b", name: "乙", content: "" };
    await mount("misc", { userPersonas: [a, b], activeUserPersonaId: "p-a" });
    expect(radios()[0].checked).toBe(true);
    act(() => { radios()[1].click(); });
    expect(current.activeUserPersonaId).toBe("p-b");
    // 卡片按档位顺序渲染 ⇒ 第二张卡的删除钮对应「乙」
    act(() => { deleteButtons()[1].click(); });
    expect(current.userPersonas).toHaveLength(1);
    expect(current.activeUserPersonaId).toBeUndefined();
  });

  it("「路径与代理」面板不再渲染用户人设区块", async () => {
    await mount("paths", { userPersonas: [{ id: "p-a", name: "甲", content: "" }], activeUserPersonaId: "p-a" });
    expect(text()).not.toContain("用户人设");
    expect(nameInputs()).toHaveLength(0);
  });
});
