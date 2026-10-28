// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 工作副本可能是 CRLF，先归一化换行，避免平台差异误报。
const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const gallery = readFileSync(
  new URL("../components/RepoGallery.tsx", import.meta.url),
  "utf8",
).replace(/\r\n/g, "\n");

/** 取 `@media (…){ … }` 整块（条件必须逐字匹配到第一个 `{` 之前）。 */
function mediaBlock(condition: string): string {
  const open = css.indexOf(`${condition} {`);
  expect(open, `找不到 @media ${condition}`).toBeGreaterThanOrEqual(0);
  return blockFrom(open);
}

/** 取**含指定内容**的那一段同条件窄屏段。
 *  ⚠ 别再用「最后一段」：后续每一轮都会往文件尾追加新的手机段，「最后一段」会跟着轮次漂移，
 *  把上一轮钉住的规则顶掉（第③刀就因此误报「生成详情堆叠规则被搬走」）。
 *  合同要保的是「这条规则只在手机断点里生效」，与它落在哪一段无关。 */
function mediaBlockWith(condition: string, needle: string): string {
  const blocks: string[] = [];
  for (let at = css.indexOf(`${condition} {`); at >= 0; at = css.indexOf(`${condition} {`, at + 1)) {
    blocks.push(blockFrom(at));
  }
  const hit = blocks.find((b) => b.includes(needle));
  expect(hit, `${blocks.length} 段手机断点里都没有「${needle}」`).toBeTruthy();
  return hit as string;
}

/** 从 at 处的规则/媒体块起始位置，返回到配对 `}` 为止的整段文本。 */
function blockFrom(at: number): string {
  const open = css.indexOf("{", at);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return css.slice(at, i + 1);
  }
  throw new Error("花括号没配对");
}

/** 取含指定选择器（可能是逗号分隔选择器列表中的一员）的整条规则。 */
function ruleFrom(needle: string): string {
  const at = css.indexOf(needle);
  expect(at, `找不到含 ${needle} 的规则`).toBeGreaterThanOrEqual(0);
  return blockFrom(at);
}

/** 同上，但从文件末尾往前找，且回退到上一个 `}` —— 取整条规则含**全部**逗号分隔选择器。 */
function lastRuleFrom(needle: string): string {
  const at = css.lastIndexOf(needle);
  expect(at, `找不到含 ${needle} 的规则`).toBeGreaterThanOrEqual(0);
  return blockFrom(css.lastIndexOf("}", at) + 1);
}

describe("手机生成内容页（2026-10-27 问题5：按钮太大、图片太小）", () => {
  it("工具栏改成两行 + 横滑：14 个固定像素按钮不再换行成 6 排吃满纵向空间", () => {
    const rule = ruleFrom(".page-toolbar.asset-toolbar");
    for (const decl of [
      "display: grid",
      "grid-auto-flow: column",
      "grid-template-rows: repeat(2, auto)",
      "grid-auto-columns: max-content",
      "overflow-x: auto",
    ]) {
      expect(rule, `工具栏缺 ${decl}`).toContain(decl);
    }
    // 横滑容器不能把纵向也滚起来，否则整页手势打架
    expect(rule).toContain("overflow-y: hidden");
  });

  it("工具栏按钮在手机上压到 30px 高（桌面仍走 .btn 的 8px 16px）", () => {
    const rule = ruleFrom(".page-toolbar.asset-toolbar .btn");
    expect(rule).toContain("padding: 3px 9px");
    expect(rule).toContain("min-height: 30px");
    expect(rule).toContain("white-space: nowrap");
    // 主题皮肤 `html:is([data-theme=…]) .btn:not(.danger)` 给所有按钮钉了 min-height:40px，
    // 比 .page-toolbar.asset-toolbar .btn 更具体 ⇒ 必须带 html[data-theme] 前缀才压得住，
    // 否则两行各 40px（实测工具栏 88px）。
    expect(rule).toContain("html[data-theme] .page-toolbar.asset-toolbar .btn");
  });

  it("瀑布流观察器挂在「节点出现」上（挂载首帧 gridRef 为空 ⇒ 手机永远停在 6 列）", () => {
    // 真根因：瀑布流只在 items>0 时渲染，[] 依赖的 effect 早已跑完并 return，观察器永不接管。
    expect(gallery).toContain("const [gridEl, setGridEl] = useState<HTMLDivElement | null>(null);");
    expect(gallery).toContain("}, [gridEl]);");
    expect(gallery).toContain("ref={setGridEl}");
    // 列数必须走纯函数（断点由 masonry.test.ts 锁），不能在组件里另写一套
    expect(gallery).toContain("masonryColumnCount(");
  });
});

describe("手机内容弹窗全屏（2026-10-27 问题7：顶部空出一大片）", () => {
  it("左右分栏/大列表类弹窗铺满视口，不再留 14% 遮罩带", () => {
    // 旧规则 .modal:has(.modal-split-body) { height: 86dvh } 也在文件里，取最新那条
    const rule = lastRuleFrom(".modal:has(.modal-split-body)");
    for (const decl of [
      "position: fixed",
      "inset: 0",
      "width: 100vw !important",
      "height: 100dvh !important",
      "max-height: 100dvh !important",
      "border-radius: 0",
    ]) {
      expect(rule, `全屏弹窗缺 ${decl}`).toContain(decl);
    }
    // 预设 / 正则 / Smithery / 生成详情四类也都要全屏（不止 :has 那一种）
    for (const sel of [
      ".modal:has(.modal-split-body)",
      ".modal.asset-detail-modal",
      ".modal.regex-modal",
      ".modal.preset-modal",
      ".modal.smithery-modal",
    ]) {
      // 主题皮肤 `html:is([data-theme=…]) .modal`（特异性 0,2,1）钉了 position:relative /
      // border-radius:8px，只靠 height/max-height 的 !important 量出来是「满屏但相对定位、圆角 8px」
      // （2026-10-27 实测），所以每个选择器都必须带 html[data-theme] 前缀。
      expect(rule, `${sel} 缺 html[data-theme] 前缀，压不住主题皮肤`).toContain(
        `html[data-theme] ${sel}`,
      );
    }
    // 生成详情弹窗原先没类名（只有内联 95vw/90vh），补类名后才能被手机段选中
    expect(gallery).toContain('<div className="modal asset-detail-modal"');
    // 旧的 86dvh 规则还在文件里，必须被更高优先级 + !important 压住
    expect(css).toContain("height: 86dvh; max-height: 86dvh !important;");
  });
  it("生成详情在竖屏改上下堆叠（左右分栏把图压到 190px）、横屏改回左右", () => {
    const phone = mediaBlockWith(
      "@media (max-width: 720px), (pointer: coarse) and (hover: none) and (max-width: 1024px)",
      ".modal.asset-detail-modal .asset-detail-body { flex-direction: column; gap: 10px; }",
    );
    expect(gallery).toContain('className="asset-detail-body"');
    expect(phone).toContain(
      ".modal.asset-detail-modal .asset-detail-body { flex-direction: column; gap: 10px; }",
    );
    // 左栏内联 flex-basis 52% 在 column 方向会变成高度，必须显式覆盖成固定值
    expect(phone).toContain(
      ".modal.asset-detail-modal .asset-detail-body > div:first-child { flex: 0 0 min(300px, 42dvh) !important; }",
    );
    // 横屏 844px 宽：row 更合适（后出现的横屏段覆盖竖屏段）
    const landscape = mediaBlock("@media (pointer: coarse) and (hover: none) and (max-height: 520px)");
    expect(landscape).toContain(
      ".modal.asset-detail-modal .asset-detail-body { flex-direction: row; gap: 12px; }",
    );
  });
});

describe("手机横屏（2026-10-27 问题6：转到宽屏就回落电脑版式）", () => {
  it("所有窄屏段都要带上触屏横屏补语（否则 844×390 命不中任何一段）", () => {
    const needle = "@media (max-width: 720px)";
    let at = css.indexOf(needle);
    let count = 0;
    while (at >= 0) {
      const head = css.slice(at, css.indexOf("{", at));
      expect(head, `这条窄屏媒体查询缺触屏补语：${head.trim()}`).toContain("(pointer: coarse) and (hover: none)");
      count++;
      at = css.indexOf(needle, at + 1);
    }
    // 文件里目前有 10 段（主题/壳层/对话/表格/设置…），至少不能一段都没有
    expect(count).toBeGreaterThanOrEqual(6);
  });

  it("低视口高横屏：顶栏压回一行、底栏压薄、仓库卡 3 列", () => {
    const landscape = mediaBlock("@media (pointer: coarse) and (hover: none) and (max-height: 520px)");
    const topbar = /\.app-topbar \{[\s\S]*?\}/.exec(landscape)?.[0] ?? "";
    expect(topbar).toContain("display: flex");
    expect(topbar).toContain("flex-wrap: nowrap");
    expect(landscape).toContain("min-height: 42px");
    expect(landscape).toContain(".repo-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }");
  });
});
