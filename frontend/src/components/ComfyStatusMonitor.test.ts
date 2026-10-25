import { describe, expect, it } from "vitest";
import {
  INITIAL_COMFY_MONITOR,
  comfyMonitorView,
  comfyMonitorVisible,
  reduceComfyMonitor,
  type ComfyMonitorAction,
  type ComfyMonitorState,
} from "./ComfyStatusMonitor";

const run = (state: ComfyMonitorState, ...actions: ComfyMonitorAction[]) =>
  actions.reduce(reduceComfyMonitor, state);

describe("ComfyStatusMonitor 状态机", () => {
  it("初始检测中保持可见", () => {
    expect(comfyMonitorVisible(INITIAL_COMFY_MONITOR)).toBe(true);
    expect(comfyMonitorView(INITIAL_COMFY_MONITOR.phase).title).toBe("正在检测 ComfyUI…");
  });

  it("未启动时一直可见，并给出原因提示", () => {
    const state = run(INITIAL_COMFY_MONITOR, { type: "probe", running: false });
    expect(state.phase).toBe("down");
    expect(comfyMonitorVisible(state)).toBe(true);
    expect(comfyMonitorView(state.phase).title).toBe("ComfyUI 未启动");
  });

  it("应用启动时就已就绪 ⇒ 不打扰，直接隐藏", () => {
    const state = run(INITIAL_COMFY_MONITOR, { type: "probe", running: true });
    expect(state.phase).toBe("up");
    expect(comfyMonitorVisible(state)).toBe(false);
  });

  it("检测到启动 ⇒ 短暂提示已启动，随后自动收起", () => {
    const down = run(INITIAL_COMFY_MONITOR, { type: "probe", running: false });
    const up = run(down, { type: "probe", running: true });
    expect(up.upNotice).toBe(true);
    expect(comfyMonitorVisible(up)).toBe(true);
    expect(comfyMonitorView(up.phase).title).toBe("ComfyUI 已启动");

    const elapsed = run(up, { type: "notice-elapsed" });
    expect(comfyMonitorVisible(elapsed)).toBe(false);
  });

  it("点 X 关闭后同一轮故障不再弹；下次掉线重新提醒", () => {
    const down = run(INITIAL_COMFY_MONITOR, { type: "probe", running: false });
    const dismissed = run(down, { type: "dismiss" });
    expect(comfyMonitorVisible(dismissed)).toBe(false);

    const stillDown = run(dismissed, { type: "probe", running: false });
    expect(comfyMonitorVisible(stillDown)).toBe(false);

    const up = run(stillDown, { type: "probe", running: true });
    expect(comfyMonitorVisible(up)).toBe(false); // 已关闭过本轮，不再弹「已启动」

    const downAgain = run(up, { type: "probe", running: false });
    expect(downAgain.phase).toBe("down");
    expect(comfyMonitorVisible(downAgain)).toBe(true);
  });

  it("后端不可达 ⇒ 状态未知但仍可见", () => {
    const state = run(INITIAL_COMFY_MONITOR, { type: "offline" });
    expect(state.phase).toBe("unknown");
    expect(comfyMonitorVisible(state)).toBe(true);
    expect(comfyMonitorView(state.phase).title).toBe("ComfyUI 状态未知");
  });
});
