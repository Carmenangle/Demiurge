// @vitest-environment jsdom
// 用户实锤（2026-10-30）：设置里的对话模型缺「本地模型 / Ollama」入口，
// 视觉与 Embedding 又把 API 和 Ollama 挤在同一页——这里钉住三个来源必须各自独立可达。
import { afterEach, describe, expect, it } from "vitest";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { importSettings, type Settings } from "../../stores/settings";
import { ModelsPanel } from "./ModelsPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function mount() {
  const initial = importSettings(JSON.stringify({
    chatModels: [],
    proxyUrl: "http://127.0.0.1:7897",
    proxyEnabled: true,
  }));
  function Harness() {
    const [draft, setDraft] = useState<Settings>(initial);
    return <ModelsPanel draft={draft} setDraft={setDraft} />;
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root?.render(<Harness />); });
}

function text() {
  return container?.textContent ?? "";
}

function buttonIn(scope: ParentNode | null, label: string) {
  return Array.from(scope?.querySelectorAll("button") ?? [])
    .find((b) => (b.textContent ?? "").trim() === label) as HTMLButtonElement | undefined;
}

function group(label: string) {
  return container?.querySelector(`[aria-label="${label}"]`) ?? null;
}

function click(el: HTMLElement | undefined) {
  expect(el).toBeTruthy();
  act(() => { el!.click(); });
}

afterEach(() => {
  act(() => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
});

describe("ModelsPanel · 来源三分（云端 API / 本机 Ollama / 本地模型）", () => {
  it("对话模型提供三个独立新增入口，卡片带来源徽标", () => {
    mount();
    const addGroup = container!.querySelector(".model-add-group");

    expect(buttonIn(addGroup, "API")).toBeTruthy();
    expect(buttonIn(addGroup, "Ollama")).toBeTruthy();
    expect(buttonIn(addGroup, "本地模型")).toBeTruthy();

    click(buttonIn(addGroup, "本地模型"));
    const chip = container!.querySelector(".model-source-chip.local");
    expect(chip?.textContent).toBe("本地 GGUF");
    expect(text()).toContain("本地对话模型文件（GGUF）");
    expect(text()).toContain("导入到 Ollama");

    click(buttonIn(container!.querySelector(".model-add-group"), "Ollama"));
    expect(container!.querySelectorAll(".model-source-chip.ollama")).toHaveLength(1);
    expect(text()).toContain("读取本机模型");

    click(buttonIn(container!.querySelector(".model-add-group"), "API"));
    expect(container!.querySelectorAll(".model-source-chip.api")).toHaveLength(1);
    expect(text()).toContain("Provider Profile");
  });

  it("视觉大模型把 API / Ollama / 本地模型拆成三档，本地档走 GGUF 导入", () => {
    mount();
    const tabs = group("视觉大模型来源");
    expect(tabs).toBeTruthy();
    expect(buttonIn(tabs, "API")).toBeTruthy();
    expect(buttonIn(tabs, "Ollama")).toBeTruthy();

    click(buttonIn(tabs, "本地模型"));
    expect(buttonIn(group("视觉大模型来源"), "本地模型")?.getAttribute("aria-pressed")).toBe("true");
    expect(text()).toContain("本地视觉模型文件（GGUF）");
    expect(text()).toContain("视觉模型需带视觉编码器");

    click(buttonIn(group("视觉大模型来源"), "Ollama"));
    expect(text()).toContain("Ollama 服务地址");
    expect(buttonIn(group("视觉大模型来源"), "Ollama")?.getAttribute("aria-pressed")).toBe("true");
  });

  it("Embedding 同样三档：API 预设与 Ollama 读取本机模型各自独立", () => {
    mount();
    const tabs = group("嵌入模型来源");
    expect(tabs).toBeTruthy();
    expect(buttonIn(tabs, "本地模型")).toBeTruthy();

    click(buttonIn(tabs, "API"));
    expect(text()).toContain("智谱 云端");
    expect(text()).toContain("OpenAI 云端");
    expect(buttonIn(group("嵌入模型来源"), "API")?.getAttribute("aria-pressed")).toBe("true");

    click(buttonIn(group("嵌入模型来源"), "Ollama"));
    expect(text()).toContain("读取本机模型");
    expect(text()).toContain("qwen3-embedding:latest");

    click(buttonIn(group("嵌入模型来源"), "本地模型"));
    expect(text()).toContain("本地嵌入模型文件夹");
    expect(text()).toContain("Reranker 模型文件夹（可选）");
  });
});
