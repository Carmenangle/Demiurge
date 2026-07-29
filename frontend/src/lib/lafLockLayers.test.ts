import { describe, expect, it } from "vitest";
// 用 Vite 的 ?raw 把扩展源码当字符串读进来，避免为一个测试引入 @types/node
import SRC from "../../../comfyui-ext/laf_lock/js/laf_lock.js?raw";

// laf_lock.js 是 ComfyUI 侧扩展（顶层 import 了 ComfyUI 的 app.js），没法在 vitest 里直接 import。
// 但它出过的这个 bug 本质是个数值关系：widget 弹出层的 z-index 必须高于画布覆盖层，
// 否则 lora_name 之类的下拉会藏到画布底下 —— 表现为「点了选不了模型」。
// 这里直接读源码断言这个不变量，将来谁调高画布层级就会被这条测试拦住。
const num = (re: RegExp): number => {
  const m = SRC.match(re);
  if (!m) throw new Error(`没在 laf_lock.js 里找到：${re}`);
  return Number(m[1]);
};

describe("laf_lock 层级不变量", () => {
  const canvasZ = () => num(/setProperty\("z-index",\s*"(\d+)",\s*"important"\)/);
  const popupZ = () => num(/const POPUP_Z = "(\d+)"/);

  it("弹出层必须盖在画布覆盖层之上", () => {
    expect(popupZ()).toBeGreaterThan(canvasZ());
  });

  it("CSS 里的弹出层 z-index 与 JS 常量一致", () => {
    // 两处都要改，只改一处会让首帧或后续挂载之一失效
    const cssZ = num(/z-index:\s*(\d+) !important;\s*\n\s*visibility: visible/);
    expect(cssZ).toBe(popupZ());
  });

  it("长按进度环仍在弹出层之上（否则选节点时看不到环）", () => {
    const ringZ = num(/#laf-ring\s*\{[^}]*z-index:\s*(\d+)/);
    expect(ringZ).toBeGreaterThan(popupZ());
  });

  it("弹出层白名单覆盖 combo widget 实际用的 litegraph 菜单类名", () => {
    // 实测前端里模型下拉就是 .litecontextmenu（append 到 document.body）
    expect(SRC).toContain(".litecontextmenu");
    expect(SRC).toContain(".p-autocomplete-overlay");
    expect(SRC).toContain(".graphdialog");
  });

  it("hideEl 在隐藏之前先放过弹出层", () => {
    // 顺序反了就会先隐藏再提权，下拉仍然不可见
    const body = SRC.slice(SRC.indexOf("function hideEl"));
    const guardAt = body.indexOf("isPopupLayer");
    const hideAt = body.indexOf('setProperty("display", "none"');
    expect(guardAt).toBeGreaterThan(-1);
    expect(guardAt).toBeLessThan(hideAt);
  });

  it("CSS 不写死弹出层的 display（会破坏菜单关闭）", () => {
    const block = SRC.slice(
      SRC.indexOf(".litecontextmenu, .litegraph.litecontextmenu"),
      SRC.indexOf("#laf-ring {"),
    );
    expect(block).not.toMatch(/display:\s*[a-z]+ !important/);
  });
});
