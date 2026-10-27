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

  it("旧 chunk 404 ⇒ 自动重载一次并提示版本更新，而不是停在错误卡片", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      window.sessionStorage.removeItem("demiurge:stale-chunk-reload-at");
      const Stale = () => {
        throw new Error(
          "Failed to fetch dynamically imported module: " +
            "http://127.0.0.1:8010/assets/NodeManagerView-BDZquKu_.js",
        );
      };
      await mount(<ErrorBoundary><Stale /></ErrorBoundary>);
      expect(container.textContent).toContain("界面版本已更新");
      expect(container.textContent).toContain("正在自动重新加载最新资源");
    } finally {
      spy.mockRestore();
      window.sessionStorage.removeItem("demiurge:stale-chunk-reload-at");
    }
  });

  it("重载限频窗口内再次失败 ⇒ 停卡片，文案并列两类原因（不许把未证明的归因写成事实）", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      window.sessionStorage.setItem(
        "demiurge:stale-chunk-reload-at",
        String(Date.now()),
      );
      const Stale = () => {
        throw new Error("Failed to fetch dynamically imported module: http://x/old.js");
      };
      await mount(<ErrorBoundary><Stale /></ErrorBoundary>);
      const text = container.textContent || "";
      expect(text).toContain("界面渲染出错");
      expect(text).toContain("资源版本不一致");
      // 真机 0.30.8→0.30.13 真实换包**没有**出卡（根 index.html 已 no-store），
      // 所以「缓存里那份旧 index」只是候选原因之一；origin 短暂不可达必须同时出现在文案里。
      expect(text).toContain("本地服务此刻不可达");
    } finally {
      spy.mockRestore();
      window.sessionStorage.removeItem("demiurge:stale-chunk-reload-at");
    }
  });

  it("「重新加载」按钮走 cache-busted 跳转，不用 location.reload()", async () => {
    // 带 `_r` 的 `replace` 是**已证明能恢复**的那条路（2026-10-27 真机解除掐链后点按钮出了列表）；
    // `location.reload()` 的缓存在各家 WebView 上语义不一致，不能保证绕开，故不用它。
    // 手动按钮必须和自动重载同一条路（同一个 forceCacheBustedReload）。
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const href = "http://127.0.0.1:8010/index.html#/assets/works/repo/r1";
    let replaced = "";
    let reloads = 0;
    const descriptor = Object.getOwnPropertyDescriptor(window, "location")!;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        href,
        replace: (url: string) => {
          replaced = url;
        },
        reload: () => {
          reloads += 1;
        },
      },
    });
    try {
      const Boom = () => {
        throw new Error("boom-手动重载");
      };
      await mount(<ErrorBoundary><Boom /></ErrorBoundary>);
      const button = container.querySelector<HTMLButtonElement>('button[type="button"]')!;
      await act(async () => {
        button.click();
      });
      expect(reloads).toBe(0);
      expect(replaced.startsWith("http://127.0.0.1:8010/index.html?_r=")).toBe(true);
      // hash 保住：手机端重载后靠它回到当前视图
      expect(replaced).toContain("#/assets/works/repo/r1");
    } finally {
      Object.defineProperty(window, "location", descriptor);
      spy.mockRestore();
    }
  });

  it("getDerivedStateFromError 返回 {error}（静态方法合约）", () => {
    const err = new Error("合约错误");
    const next = ErrorBoundary.getDerivedStateFromError(err);
    expect(next).toEqual({ error: err });
  });
});
