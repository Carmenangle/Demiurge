// 手机端联机检测的判定合同（纯逻辑，无 DOM）。
// 钉死用户口径：连着不出现 / 断了才跳出 / 关掉同一次故障不重复弹 / 没联机不检测也不弹。
import { describe, expect, it } from "vitest";
import type { MobileOnlineStatus } from "../api/mobileRemote";
import {
  INITIAL_ONLINE_WATCH,
  onlineWatchView,
  onlineWatchVisible,
  reduceOnlineWatch,
} from "./mobileOnlineWatch";

const PC = "http://192.0.2.10:8010";
const COMFY = "http://192.0.2.9:8188";

function status(patch: Partial<MobileOnlineStatus> = {}): MobileOnlineStatus {
  return {
    active: true,
    base_url: PC,
    reachable: true,
    latency_ms: 12,
    comfyui_url: COMFY,
    comfyui_running: true,
    detail: "",
    ...patch,
  };
}

describe("reduceOnlineWatch", () => {
  it("联机全绿：linked 且不可见（连着就绝不出现）", () => {
    const next = reduceOnlineWatch(INITIAL_ONLINE_WATCH, { type: "probe", status: status() });
    expect(next.phase).toBe("linked");
    expect(onlineWatchVisible(next)).toBe(false);
  });

  it("电脑没应答：pc_down 且提示点名电脑地址", () => {
    const next = reduceOnlineWatch(INITIAL_ONLINE_WATCH, {
      type: "probe",
      status: status({ reachable: false, latency_ms: null, detail: "连不上电脑：确认同一 Wi‑Fi" }),
    });
    expect(next.phase).toBe("pc_down");
    expect(onlineWatchVisible(next)).toBe(true);
    const view = onlineWatchView(next);
    expect(view.title).toContain("联机中断");
    expect(view.message).toContain("连不上电脑");
    expect(view.message).toContain(PC);
  });

  it("电脑在线但 ComfyUI 没按地址开着：comfy_down 且提示带那个地址", () => {
    const next = reduceOnlineWatch(INITIAL_ONLINE_WATCH, {
      type: "probe",
      status: status({ comfyui_running: false, detail: `地址 ${COMFY} 上没有响应中的 ComfyUI` }),
    });
    expect(next.phase).toBe("comfy_down");
    expect(onlineWatchVisible(next)).toBe(true);
    const view = onlineWatchView(next);
    expect(view.title).toContain("ComfyUI 没开着");
    expect(view.message).toContain(COMFY);
    // 电脑是通的，不能把责任推给网络
    expect(view.message).not.toContain("防火墙");
  });

  it("没启用联机：off、不可见（前提是填了电脑地址并开始连接）", () => {
    const linked = reduceOnlineWatch(INITIAL_ONLINE_WATCH, { type: "probe", status: status() });
    const next = reduceOnlineWatch(linked, {
      type: "probe",
      status: status({ active: false, reachable: false, comfyui_running: false }),
    });
    expect(next.phase).toBe("off");
    expect(onlineWatchVisible(next)).toBe(false);
  });

  it("「知道了」只压同一次故障：恢复后再断要重新弹", () => {
    const down = reduceOnlineWatch(INITIAL_ONLINE_WATCH, {
      type: "probe",
      status: status({ reachable: false, detail: "连不上电脑" }),
    });
    const closed = reduceOnlineWatch(down, { type: "dismiss" });
    expect(onlineWatchVisible(closed)).toBe(false);
    // 同一次故障的下一轮探针：仍然静默，不重复打扰
    const again = reduceOnlineWatch(closed, {
      type: "probe",
      status: status({ reachable: false, detail: "连不上电脑" }),
    });
    expect(onlineWatchVisible(again)).toBe(false);
    // 恢复 → 再断：新故障，重新弹
    const linked = reduceOnlineWatch(again, { type: "probe", status: status() });
    expect(linked.detail).toBe("");
    expect(linked.target).toBe("");
    const reBroken = reduceOnlineWatch(linked, {
      type: "probe",
      status: status({ reachable: false, detail: "连不上电脑" }),
    });
    expect(onlineWatchVisible(reBroken)).toBe(true);
  });

  it("断点换了（电脑断了 → 只是 ComfyUI 断了）算新故障：复位 dismissed 并换文案", () => {
    const pcDown = reduceOnlineWatch(INITIAL_ONLINE_WATCH, {
      type: "probe",
      status: status({ reachable: false, detail: "连不上电脑" }),
    });
    const closed = reduceOnlineWatch(pcDown, { type: "dismiss" });
    const comfyDown = reduceOnlineWatch(closed, {
      type: "probe",
      status: status({ comfyui_running: false, detail: `地址 ${COMFY} 上没有响应中的 ComfyUI` }),
    });
    expect(comfyDown.phase).toBe("comfy_down");
    expect(comfyDown.dismissed).toBe(false);
    expect(onlineWatchVisible(comfyDown)).toBe(true);
  });

  it("本机探针问不通（手机后端在重启）：保持原状，不制造假警", () => {
    const linked = reduceOnlineWatch(INITIAL_ONLINE_WATCH, { type: "probe", status: status() });
    const after = reduceOnlineWatch(linked, { type: "local-error" });
    expect(after).toBe(linked);
    expect(onlineWatchVisible(after)).toBe(false);
    // 从没联过机的首查也绝不因为一次请求失败就弹
    expect(onlineWatchVisible(reduceOnlineWatch(INITIAL_ONLINE_WATCH, { type: "local-error" })))
      .toBe(false);
  });
});
