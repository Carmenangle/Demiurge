// @vitest-environment jsdom
// 手机端 ComfyUI 画布收口（M7 联机生成）：画布是 iframe 直连 ComfyUI 页面——联机同库时
// 地址是电脑回环（在手机里指向手机自己），云端地址又被后端 CSP frame-src 拦死。
// 故手机构建一律不渲染画布 iframe，改为「能力说明」占位；PC 构建逐字不变。
import { renderToStaticMarkup } from "react-dom/server";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

const COMFY = "http://127.0.0.1:8188";

function source(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), "utf8");
}

describe("手机端画布收口", () => {
  it("手机端 NodeCard 不渲染 iframe，只留一行节点说明", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "1");
    const { NodeCard } = await import("./WorkflowCard");
    const html = renderToStaticMarkup(
      <NodeCard cardId="card-1" nodeId="4" index={0} workflow={{}} comfyUrl={COMFY} />,
    );
    expect(html).not.toContain("<iframe");
    expect(html).toContain("手机端不显示节点画布");
  });

  it("PC 端 NodeCard 保持原样：仍是等待提示，不出现手机说明", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "0");
    const { NodeCard } = await import("./WorkflowCard");
    const html = renderToStaticMarkup(
      <NodeCard cardId="card-1" nodeId="4" index={0} workflow={{}} comfyUrl={COMFY} />,
    );
    expect(html).toContain("ComfyUI 未启动，等待中…");
    expect(html).not.toContain("手机端不显示节点画布");
  });

  it("手机端 AI 搭工作流整页改为能力说明，不起搭建钩子", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "1");
    const { AIBuildView } = await import("../views/AIBuildView");
    const html = renderToStaticMarkup(<AIBuildView />);
    expect(html).toContain("AI 搭工作流需要在电脑上使用");
    expect(html).not.toContain("<iframe");
  });

  it("PC 端 AI 搭工作流仍挂画布 iframe", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "0");
    const { AIBuildView } = await import("../views/AIBuildView");
    const html = renderToStaticMarkup(<AIBuildView />);
    expect(html).toContain("<iframe");
    expect(html).not.toContain("AI 搭工作流需要在电脑上使用");
  });

  it("对话页「ComfyUI 节点面板」与模板/选节点弹窗都接了手机分支（源码守卫）", () => {
    const chat = source("../views/ChatView.tsx");
    expect(chat).toContain("MOBILE_PROFILE ?");
    expect(chat).toContain("MobileCanvasNotice");
    const picker = source("./NodePickerModal.tsx");
    expect(picker).toContain("MOBILE_PROFILE ?");
    expect(picker).toContain("MobileCanvasNotice");
    const templates = source("../pages/WorkflowTemplates.tsx");
    expect(templates).toContain("MOBILE_PROFILE ?");
    expect(templates).toContain("MobileCanvasNotice");
  });
});
