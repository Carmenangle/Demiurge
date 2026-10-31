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

// ComfyUI 0.42 的前端会在扩展模块执行前用 Vue Router 把地址改成 "/#<clientId>"，
// location.search 里的 laf_lock/laf_full 就此消失 → 扩展判定为非受控模式，父页永远收不到 ready
// （画布空白、N:0、无报错）。这里把判定函数抠出来真跑一遍，钉住「按导航条目初始 URL 判定」这条。
describe("laf_lock 模式判定", () => {
  const fnSrc = SRC.match(/function initialSearch\(\) \{[\s\S]*?\n\}/)?.[0] ?? "";
  const resolveSearch = (navName: string | null, curSearch: string, navThrows = false): string => {
    const performance = {
      getEntriesByType: () => {
        if (navThrows) throw new Error("no navigation timing");
        return navName === null ? [] : [{ name: navName }];
      },
    };
    return new Function("performance", "location", `${fnSrc}; return initialSearch;`)(
      performance,
      { search: curSearch },
    )();
  };

  it("能从源码里找到 initialSearch", () => {
    expect(fnSrc).toContain("getEntriesByType");
  });

  it("当前地址被改写成 /#clientId 时，仍按导航条目里的初始 URL 认出锁定模式", () => {
    expect(resolveSearch("http://127.0.0.1:8188/?laf_lock=1", "")).toBe("?laf_lock=1");
    expect(resolveSearch("http://127.0.0.1:8188/?laf_full=1", "")).toBe("?laf_full=1");
  });

  it("没有导航条目或读不到时退回 location.search", () => {
    expect(resolveSearch(null, "?laf_lock=1")).toBe("?laf_lock=1");
    expect(resolveSearch(null, "?laf_lock=1", true)).toBe("?laf_lock=1");
  });

  it("判定后 LOCK/FULL 都从解析结果取值，不再直接用 location.search 构造参数", () => {
    expect(SRC).toMatch(/new URLSearchParams\(initialSearch\(\)\)/);
    expect(SRC).not.toMatch(/new URLSearchParams\(location\.search\)/);
  });
});
