// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 工作副本可能是 CRLF，先归一化换行，避免平台差异误报。
const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");

/** 文件里 `@media (max-width: 720px)` 有多段（主题一段、页面一段…），全部取出来。 */
function media720Blocks(): string[] {
  const out: string[] = [];
  const needle = "@media (max-width: 720px) {";
  for (let at = css.indexOf(needle); at >= 0; at = css.indexOf(needle, at + 1)) {
    let depth = 0;
    for (let i = at + needle.length - 1; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}" && --depth === 0) {
        out.push(css.slice(at, i + 1));
        break;
      }
    }
  }
  return out;
}

/** 取 haystack 里某条选择器规则的声明块（第一个 `{` 到配对的 `}`）。 */
function blockOf(haystack: string, selector: string): string {
  const at = haystack.indexOf(`${selector} {`);
  expect(at, `找不到规则 ${selector} {`).toBeGreaterThanOrEqual(0);
  const open = haystack.indexOf("{", at);
  let depth = 0;
  for (let i = open; i < haystack.length; i++) {
    if (haystack[i] === "{") depth++;
    else if (haystack[i] === "}" && --depth === 0) return haystack.slice(open + 1, i);
  }
  throw new Error(`规则 ${selector} 的花括号没配对`);
}

const mobile = media720Blocks().find((b) => b.includes(".nci-body"));
if (!mobile) throw new Error("窄屏段里找不到 .nci-body（对话页那段 @media 被搬走或改名了）");
const MOBILE = mobile;

describe("窄屏对话页版式合同（2026-10-26 用户报「对话设置把对话遮挡了」）", () => {
  it("气泡里的两类不可断长串各自有收口：代码块自滚动、失败槽可断行", () => {
    // 关掉 .media-slot-failed 的 anywhere，真机数据实测 .bot-content 256→722、.bot-rich-parts 228→708
    expect(blockOf(css, ".media-slot-failed")).toContain("overflow-wrap: anywhere");
    // 关掉 .bot-html pre 的收口，注入一行长 JSON 实测 .bot-content scrollWidth 冲到 3330
    expect(css).toContain(".bot-html pre { max-width: 100%; overflow-x: auto; }");
  });

  it("顶部功能栏缩到一行按钮大小；正文列拿到两侧留白与气泡上限", () => {
    // PC 那套 44×44 里 min-height 带 !important，只能同级压回；
    // 首个按钮行内写了 margin-left:auto，手机上会把第一行拉开一个大洞。
    const icon = blockOf(MOBILE, ".chat-view-head .btn.icon-only");
    for (const decl of [
      "width: 32px !important",
      "min-width: 32px !important",
      "height: 32px !important",
      "min-height: 32px !important",
      "margin-left: 0 !important",
    ]) {
      expect(icon, `功能栏按钮收缩缺 ${decl}`).toContain(decl);
    }
    expect(blockOf(MOBILE, ".chat-stream")).toContain("padding: 12px 32px 12px 2px");
    expect(blockOf(MOBILE, ".bot-content")).toContain("max-width: calc(100% - 42px)");
  });

  it("「对话设置」浮层改视口锚定（桌面 420px + right:0 在 390px 上左边界 -208px 被裁）", () => {
    const body = blockOf(MOBILE, ".nci-body");
    expect(body).toContain("position: fixed");
    expect(body).toContain("left: 12px");
    // 面板必须让开自己的触发钮：盖住它既打不开也关不掉（实测 .nci-item 拦 pointer events）。
    expect(body).toContain("margin-top: 36px");
  });

  it("窄屏收成「缩小到放得下」而不是横向滚动：绝对定位浮层会被滚动容器裁掉", () => {
    // `.nci-body`／`.model-switch-menu` 是 position:absolute 的子层，
    // 一旦给这两行加 overflow-x:auto/scroll，它们会被静默裁掉 → 手机上打不开也看不见。
    for (const sel of [".chat-view-head", ".chat-actions"]) {
      const rule = blockOf(MOBILE, sel);
      expect(`${sel} 不能用横向滚动收一屏：${rule}`).not.toMatch(/overflow-x:\s*(auto|scroll)/);
    }
  });
});

describe("窄屏对话页第二轮合同（2026-10-27 真机：功能栏浮标化/顶栏两行/TableModal 全屏）", () => {
  const mobile2 = media720Blocks().find((b) => b.includes(".head-tools.open"));
  if (!mobile2) throw new Error("第二轮手机段被搬走或改名了（含 .head-tools.open 的 @media）");

  it("桌面布局零影响：工具组包装层 display:contents、浮标在桌面隐藏", () => {
    expect(blockOf(css, ".head-tools")).toContain("display: contents");
    expect(blockOf(css, ".head-tools-toggle")).toContain("display: none");
  });

  it("手机端工具组默认隐藏，浮标点开为 grid 浮层（不再两排按钮吃掉纵向空间）", () => {
    const hidden = blockOf(mobile2, ".chat-view-head .head-tools");
    expect(hidden).toContain("display: none");
    const open = blockOf(mobile2, ".chat-view-head .head-tools.open");
    expect(open).toContain("display: grid");
    expect(open).toContain("position: absolute");
    expect(blockOf(mobile2, ".chat-view-head .head-tools-toggle")).toContain("display: inline-flex");
  });

  it("顶栏三行压两行：grid-areas 显式断行，methods 不再独占一行", () => {
    const bar = blockOf(mobile2, ".app-topbar");
    expect(bar).toContain("grid-template-areas:");
    expect(bar.split("grid-template-areas:")[1]).toMatch(/"\w+ \w+ \w+"\s*"\w+ \w+ \w+"/);
  });

  it("TableModal 全屏化 + 表选择横滑 chips（弹窗不再贴顶/左右分栏各半）", () => {
    const modal = blockOf(mobile2, ".table-modal");
    expect(modal).toContain("position: fixed");
    expect(modal).toContain("inset: 0");
    expect(blockOf(mobile2, ".table-modal-body")).toContain("flex-direction: column");
    const list = blockOf(mobile2, ".table-list");
    expect(list).toContain("flex-direction: row");
    expect(list).toContain("overflow-x: auto");
  });
});
