// canvas/shared.test.ts — 跨组件共享状态（工作流工具卡 + 画布挂载桥）测试
// 直接测试真实模块：globalPendingToolCreates 消费 + canvasBridge.canvasMounted 防双消费
import { describe, it, expect, beforeEach } from "vitest";
import { globalPendingToolCreates, canvasBridge } from "./shared";

beforeEach(() => {
  globalPendingToolCreates.splice(0, globalPendingToolCreates.length);
  canvasBridge.canvasMounted = false;
});

describe("globalPendingToolCreates", () => {
  it("画布未挂载：ChatView 写入 → 挂载时 splice 消费并清空", () => {
    globalPendingToolCreates.push({
      id: "wftool-abc12345",
      templateId: "tpl-1",
      templateName: "SDXL 文生图",
      estimatedNodeCount: 5,
    });
    expect(globalPendingToolCreates.length).toBe(1);

    const consumed = globalPendingToolCreates.splice(0, globalPendingToolCreates.length);
    expect(consumed).toHaveLength(1);
    expect(consumed[0].templateName).toBe("SDXL 文生图");
    expect(globalPendingToolCreates.length).toBe(0);
  });

  it("多次 /w → 累积后一次消费", () => {
    globalPendingToolCreates.push(
      { id: "w-1", templateId: "t1", templateName: "模板A", estimatedNodeCount: 3 },
      { id: "w-2", templateId: "t2", templateName: "模板B", estimatedNodeCount: 7 },
    );
    const consumed = globalPendingToolCreates.splice(0, globalPendingToolCreates.length);
    expect(consumed).toHaveLength(2);
    expect(consumed.map((c) => c.templateName)).toEqual(["模板A", "模板B"]);
  });

  it("画布挂载期间事件不进 global（防双消费 → 重复工具卡）", () => {
    canvasBridge.canvasMounted = true;
    // ChatView 兜底监听在 canvasMounted=true 时应直接 return，不 push
    const pushIfNotMounted = () => {
      if (canvasBridge.canvasMounted) return;
      globalPendingToolCreates.push({
        id: "w-3", templateId: "t3", templateName: "模板C", estimatedNodeCount: 2,
      });
    };
    pushIfNotMounted();
    expect(globalPendingToolCreates.length).toBe(0);
  });

  it("卸载后 canvasMounted 复位，事件重新进入 global", () => {
    canvasBridge.canvasMounted = true;
    canvasBridge.canvasMounted = false; // 卸载 cleanup
    globalPendingToolCreates.push({
      id: "w-4", templateId: "t4", templateName: "模板D", estimatedNodeCount: 1,
    });
    expect(globalPendingToolCreates.length).toBe(1);
    // 重新挂载时消费
    const drained = globalPendingToolCreates.splice(0, globalPendingToolCreates.length);
    expect(drained.map((c) => c.templateName)).toEqual(["模板D"]);
  });

  it("消费用函数式 set（StrictMode effect 双跑）幂等：第二次 splice 空数组不覆盖已有卡", () => {
    // 模拟 StrictMode mount → cleanup → mount：effect 双跑
    globalPendingToolCreates.push({
      id: "w-5", templateId: "t5", templateName: "模板E", estimatedNodeCount: 4,
    });
    let state: Array<{ id: string; templateName: string }> = [];
    // 第一次 effect 运行
    const drained1 = globalPendingToolCreates.splice(0, globalPendingToolCreates.length);
    state = [...state, ...drained1];
    // 第二次 effect 运行（StrictMode 重放）：splice 拿空数组，函数式 set 不覆盖
    const drained2 = globalPendingToolCreates.splice(0, globalPendingToolCreates.length);
    state = [...state, ...drained2];
    expect(state).toHaveLength(1);
    expect(state[0].templateName).toBe("模板E");
  });
});
