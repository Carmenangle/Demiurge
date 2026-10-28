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

/** 按**条件前缀**取出所有同条件 @media 段（不是「最后一段」：每轮改版都会往文件尾追加新段）。 */
function mediaBlocks(condition: string): string[] {
  const out: string[] = [];
  for (let at = css.indexOf(condition); at >= 0; at = css.indexOf(condition, at + 1)) {
    const open = css.indexOf("{", at + condition.length);
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

/** 取含 needle 的那一段；找不到就把段数报出来，免得下次追加新段时误判成「被搬走」。 */
function mediaBlockWith(condition: string, needle: string): string {
  const blocks = mediaBlocks(condition);
  const hit = blocks.find((b) => b.includes(needle));
  expect(hit, `${blocks.length} 段「${condition}」里都没有「${needle}」`).toBeTruthy();
  return hit as string;
}

const readSrc = (rel: string) =>
  readFileSync(new URL(`../${rel}`, import.meta.url), "utf8").replace(/\r\n/g, "\n");

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

// 真机审查第③④刀（2026-10-28）：重复标题、会话「更多」菜单、弹层透底、横屏设置导航、卡片细节。
// 口径来自用户实测反馈，不是审美偏好——每条都对应一个「看不见 / 读不透 / 白占屏」的现场问题。
const MOBILE_COND = "@media (max-width: 720px)";
const LAND_COND = "@media (pointer: coarse) and (hover: none) and (max-height: 520px)";

describe("手机弹层与卡片细节合同（2026-10-28 第③④刀）", () => {
  it("P1-2 下钻页只留一个标题：手机上顶栏不再重复「资产管理 · 角色卡」", () => {
    expect(app).toContain("{secLabel}{subLabel && !MOBILE_PROFILE ? ` · ${subLabel}` : \"\"}");
    expect(app).toContain('import { MOBILE_PROFILE } from "./lib/mobileProfile"');
  });

  it("P1-3 「更多」浮层：三列带文字的格子 + 96% 实底（触屏没 hover，纯图标读不出是什么）", () => {
    const m = mediaBlockWith(MOBILE_COND, ".chat-view-head .head-tools.open {");
    const open = blockOf(m, ".chat-view-head .head-tools.open");
    expect(open).toContain("grid-template-columns: repeat(3, minmax(0, 1fr))");
    expect(open).toContain("var(--surface-solid) 96%");
    expect(blockOf(m, ".chat-view-head .head-tools.open .head-tool-text")).toContain("display: block");
    // 文案必须真渲染出来，只写 CSS 不写节点等于没标签
    const chat = readSrc("views/ChatView.tsx");
    expect((chat.match(/className="head-tool-text"/g) ?? []).length).toBeGreaterThanOrEqual(8);
    expect(readSrc("components/chat/NarrativeCiPanel.tsx")).toContain('className="head-tool-text"');
  });

  it("P1-4 手机上弹窗 95% 实底：铺满整屏的半透明会把底下正文透上来", () => {
    const m = mediaBlockWith(MOBILE_COND, "html[data-theme] .modal {");
    expect(blockOf(m, "html[data-theme] .modal"))
      .toContain("color-mix(in srgb, var(--surface-solid) 95%, transparent)");
    // 抽屉同口径（后台活动 / 快捷工具都从 --surface-solid 取底）。
    // 「工具」汇总抽屉已按用户 2026-10-28 定案撤掉（右下保留三个独立浮钮），不再有该层。
    const fab = mediaBlockWith(MOBILE_COND, ".support-panel,");
    expect(fab).toContain("color-mix(in srgb, var(--surface-solid) 95%, transparent)");
    // 拉绳 z-index 1260 > 遮罩 1000：不挂进让位清单就会浮在全屏弹窗之上（v10f 红底实测）
    const yieldRule = (css.match(/\.app:has\(\.modal-mask\)[^{]*\{[^}]*display: none[^}]*\}/g) ?? [])
      .find((r: string) => r.includes(".lg-rope"));
    expect(yieldRule, "弹窗让位清单里没有 .lg-rope").toBeTruthy();
  });

  it("P1-4 方位词跟着版式走：手机上分栏堆叠成上下，提示不能还说「选择左侧」", () => {
    for (const file of ["PresetModal", "PromptTemplateModal", "StylePresetModal", "TableModal"]) {
      const src = readSrc(`components/${file}.tsx`);
      expect(src, `${file}.tsx`).toContain('isMobileViewport ? "上方" : "左侧"');
    }
  });

  it("P1-5 横屏设置导航换单行自滚：竖屏那段是 grid，只写 flex-wrap 会打在 grid 上失效", () => {
    const land = mediaBlockWith(LAND_COND, ".settings-nav {");
    const nav = blockOf(land, ".settings-nav");
    expect(nav).toContain("display: flex");
    expect(nav).toContain("flex-wrap: nowrap");
    expect(nav).toContain("overflow-x: auto");
    expect(nav).toContain("overscroll-behavior-x: contain");
    // 同条件段必须排在 5×2 网格那段**之后**，否则层叠顺序反过来，横屏仍是两行网格
    expect(css.indexOf(land)).toBeGreaterThan(css.indexOf(MOBILE));
    expect(blockOf(land, ".settings-nav-item")).toContain("white-space: nowrap");
  });

  it("P1-6 卡片细节：工作流卡手机上只留文件名、立绘读不出走统一占位（不露裂图和 alt）", () => {
    expect(mediaBlockWith(MOBILE_COND, ".template-path-dir { display: none; }"))
      .toContain(".template-path-dir { display: none; }");
    const wf = readSrc("pages/WorkflowTemplates.tsx");
    expect(wf).toContain('className="template-path-dir"');
    expect(wf).toContain('className="template-path-file"');
    const cards = readSrc("pages/CharacterCards.tsx");
    expect(cards).toContain('className="repo-cover-empty"');
    expect(cards).toContain("onError=");
  });

  it("P1-7 调试日志条默认不出屏：出包用的是 debug 构建，只能按显式开关收口", () => {
    // 壳层在仓库另一侧：frontend/src/lib → ../../../mobile/...
    const spike = "../../../mobile/spike/android-spike";
    const java = readFileSync(
      new URL(`${spike}/app/src/main/java/dev/demiurge/spike/MainActivity.java`, import.meta.url), "utf8",
    );
    expect(java).toContain("if (!BuildConfig.SHOW_DEBUG_LOG) {");
    const gradle = readFileSync(
      new URL(`${spike}/app/build.gradle.kts`, import.meta.url), "utf8",
    );
    expect(gradle).toContain('buildConfigField("boolean", "SHOW_DEBUG_LOG"');
  });
});
