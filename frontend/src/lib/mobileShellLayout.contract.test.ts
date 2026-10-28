// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 工作副本可能是 CRLF，先归一化换行，避免平台差异误报。
const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const app = readFileSync(new URL("../App.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");

function media720Blocks(): string[] {
  const out: string[] = [];
  // 只认前缀：条件后面可能还挂着触屏横屏补语（`(pointer: coarse) and …`）。
  const needle = "@media (max-width: 720px)";
  for (let at = css.indexOf(needle); at >= 0; at = css.indexOf(needle, at + 1)) {
    const open = css.indexOf("{", at + needle.length);
    if (open < 0) continue;
    let depth = 0;
    for (let i = open; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}" && --depth === 0) {
        out.push(css.slice(at, i + 1));
        break;
      }
    }
  }
  return out;
}

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

const round3 = media720Blocks().find((b) => b.includes(".topbar-settings"));
if (!round3) throw new Error("第三轮手机段被搬走或改名了（含 .topbar-settings 的 @media）");
const MOBILE = round3;

describe("手机四项反馈合同（2026-10-27）", () => {
  it("「…」浮层贴回对话头部：不定位时绝对定位的包含块退化成视口，浮层落到屏幕外", () => {
    expect(blockOf(MOBILE, ".chat-view-head")).toContain("position: relative");
    // 桌面仍是扁平布局：包装层不能变成会吃掉按钮间距的真容器
    expect(blockOf(css, ".head-tools")).toContain("display: contents");
    expect(blockOf(css, ".head-tools-toggle")).toContain("display: none");
  });

  it("Smithery 市场手机上改上下堆叠：筛选横滑 chips、结果吃满宽度", () => {
    expect(blockOf(MOBILE, ".smithery-body")).toContain("flex-direction: column");
    const filters = blockOf(MOBILE, ".smithery-filters");
    expect(filters).toContain("width: 100%");
    expect(filters).toContain("overflow-x: auto");
    expect(filters).toContain("border-right: 0");
    const item = blockOf(MOBILE, ".smithery-filter-item");
    expect(item).toContain("width: auto");
    expect(item).toContain("white-space: nowrap");
  });

  it("设置页顶部：隐藏与底栏重复的「设置」标题条，栏目压成 5×2 图标网格", () => {
    expect(blockOf(MOBILE, ".app-topbar.topbar-settings")).toContain("display: none");
    const nav = blockOf(MOBILE, ".settings-nav");
    expect(nav).toContain("display: grid");
    expect(nav).toContain("repeat(5, minmax(0, 1fr))");
    // 分组在手机上摊平（10 项 / 5 列 = 2 行，不再按 常规/模型/扩展/凭证 分行占掉半屏）
    expect(blockOf(MOBILE, ".settings-nav-group")).toContain("display: contents");
    const item = blockOf(MOBILE, ".settings-nav-item");
    expect(item).toContain("flex-direction: column");
    expect(item).toContain("white-space: normal");
  });

  it("标题条只在设置打开时才有那个 class（正常顶栏 / 分区顶栏不受影响）", () => {
    expect(app).toContain('className="app-topbar topbar-settings"');
    expect(app).toContain('<span className="topbar-title">设置</span>');
    expect(app).not.toContain('<header className="app-topbar topbar-settings">\n        <span className="topbar-title">创作');
  });
});

// 问题8（2026-10-27 真机）：真机上横拖图区会带着整页跑。溢出源是吸底分页条
// （.pager 483px + justify-content:center ⇒ 左右各溢出一截）.app-body scrollWidth 432/390。
const round8 = media720Blocks().find((b) => b.includes(".pager-dock"));
if (!round8) throw new Error("第四轮手机段被搬走或改名了（含 .pager-dock 的 @media）");

describe("手机页面级横滑合同（2026-10-27 问题8）", () => {
  it("溢出一律收进控件自己的滚动容器：吸底分页条自滚、页面不横滚", () => {
    const dock = blockOf(round8, ".pager-dock");
    expect(dock).toContain("overflow-x: auto");
    expect(dock).toContain("overscroll-behavior-x: contain");
    const pager = blockOf(round8, ".pager-dock .pager");
    // max-content 才排得成一行交给吸底条滚；auto margin 让放得下时仍居中
    expect(pager).toContain("width: max-content");
    expect(pager).toContain("margin: 0 auto");
    // 390px 下 12 按钮 + 输入框 = 428px（差 66px）⇒ 手机上收掉输入框后整条 298px 不滚
    expect(blockOf(round8, ".pager-dock .page-jump")).toContain("display: none");
  });

  it("工具条只吃容器宽：横滑发生在它自己身上，不把 .page 撑宽", () => {
    // 双保险并入问题5那条工具条规则（同层再加一条会把 assetsMobileLayout 合同测试的
    // 「第一条 .page-toolbar.asset-toolbar 规则」顶掉），这里按声明找落点，不认块。
    const toolbarRule = (css.match(/\.page-toolbar\.asset-toolbar \{[^}]*overflow-x:\s*auto[^}]*\}/g) ?? [])
      .find((rule: string) => rule.includes("max-width: 100%") && rule.includes("min-width: 0"));
    expect(toolbarRule, "工具条横滑规则缺 max-width/min-width").toBeTruthy();
    // 手机段之外，吸底条的滚动属性不能外溢到桌面：全表只有这一处给 .pager-dock 加 overflow-x
    expect(css.match(/\.pager-dock\s*\{[^}]*overflow-x:\s*auto/g)?.length ?? 0).toBe(1);
  });
});

// 问题9（2026-10-27 真机）：设置面板「标题 + 动作按钮」头行在 390px 下被挤爆。
// 实测（_tmp/probe/skills-head-before.json）：标题「技能扩展」59×58 两行，
// Smithery 市场 / 新建 / 保存 三按钮全 58 高且各自折行；保存版式的按钮斜纹侧片
// 还会再吃掉一截内容宽 ⇒ 中文逐字竖排。口径：文案永不断行，按钮不参与压缩。
describe("设置面板头行合同（2026-10-27 问题9）", () => {
  it("标题与按钮文案永不断行：放不下时整排按钮落到标题下一行", () => {
    expect(blockOf(MOBILE, ".settings-actions-head")).toContain("flex-wrap: wrap");
    expect(blockOf(MOBILE, ".settings-actions-head > h4")).toContain("white-space: nowrap");
    expect(blockOf(MOBILE, ".settings-actions-head > label")).toContain("white-space: nowrap");
    expect(blockOf(MOBILE, ".settings-actions-head > div")).toContain("flex-wrap: wrap");
    const btn = blockOf(MOBILE, ".settings-actions-head .btn");
    expect(btn).toContain("white-space: nowrap");
    expect(btn).toContain("flex: 0 0 auto");
  });

  it("六个同型头行都挂了类名：改版式不能只改「技能扩展」一处", () => {
    for (const file of [
      "SkillsPanel", "McpPanel", "AgentPanel", "BuiltinAgentsSection", "StylePanel", "PathsPanel",
    ]) {
      const src = readFileSync(new URL(`../views/settings/${file}.tsx`, import.meta.url), "utf8");
      expect(src, `${file}.tsx`).toContain('className="settings-actions-head"');
    }
  });
});
