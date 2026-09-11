// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ErrorBoundary } from "./ErrorBoundary";

// React 19 + jsdom：必须显式声明 act 环境，否则每次 act() 调用都会打
// "The current testing environment is not configured to support act(...)" 警告。
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ErrorBoundary 回归测试（2026-09-01 对话附件黑屏事故后新增的兜底基建）：
// 验证「子组件渲染抛错 → 显示可见错误卡片（错误信息 + 组件栈 + 重新加载），而非整树卸载黑屏」。

let container: HTMLDivElement;
let root: Root;

function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  return act(async () => { root.render(node); });
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
});

describe("ErrorBoundary", () => {
  it("正常子组件照常渲染，不吞内容", async () => {
    await mount(<ErrorBoundary><div className="ok-child">正常内容</div></ErrorBoundary>);
    expect(container.querySelector(".ok-child")?.textContent).toBe("正常内容");
  });

  it("子组件渲染抛错 → 显示错误卡片（错误信息 + 重新加载按钮），不再黑屏", async () => {
    // 屏蔽 React 调试期 console.error（React 19 渲染期错误会打日志，属预期）
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const Boom = () => { throw new Error("boom-渲染崩溃"); };
      await mount(<ErrorBoundary><Boom /></ErrorBoundary>);
      const text = container.textContent || "";
      expect(text).toContain("界面渲染出错");
      expect(text).toContain("boom-渲染崩溃");
      expect(container.querySelector('button[type="button"]')?.textContent).toContain("重新加载");
      // 完整组件栈入口存在
      expect(text).toContain("展开完整组件栈");
    } finally {
      spy.mockRestore();
    }
  });

  it("componentDidCatch 记录组件栈到 state", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const Boom = () => { throw new Error("x"); };
      await mount(<ErrorBoundary><Boom /></ErrorBoundary>);
      expect(container.textContent).toContain("Boom");
    } finally {
      spy.mockRestore();
    }
  });

  it("getDerivedStateFromError 返回 {error}（静态方法合约）", () => {
    const err = new Error("合约错误");
    const next = ErrorBoundary.getDerivedStateFromError(err);
    expect(next).toEqual({ error: err });
  });
});
