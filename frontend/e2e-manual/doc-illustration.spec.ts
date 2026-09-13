/** manual e2e：链路③ 文档插图端到端可视化验证（**需要真实后端与前端在运行**）。
 *
 *  为什么单列一个 manual 目录：普通 `npm run test:e2e`（playwright.config.ts，testDir=./e2e）
 *  跑的是 mock 后端 + 15173 的隔离环境；本验证要证明「真实后端返回的内联素材端点能被浏览器
 *  当图片解码渲染」，必须连真实 vite(5173) + 真实后端(8010)，因此不并入默认套件。
 *
 *  自包含：本 spec 自己按后端 `settings.outputDir`（作品库根）造 fixture（作品文件夹下
 *  `docs/*.md` + `docs/assets/*.png`），跑完清理，可重复运行。
 *
 *  为什么是「作品文件夹下」而不是作品库根（2026-09-10 B3/A）：产物域已从作品库根收窄到
 *  **作品域**（仓库/小仓库文件夹），`docs/` 与 `_versions/` 都随作品域走。若仍造在作品库根，
 *  验证的就是一条生产上不再产生的路径——真实路径必须带 `repoId` 才会被后端认到。
 *  本 spec 用一个未登记的 repoId（`repo_folder_path` 会回退 `safe_seg(repoId)` 作文件夹名），
 *  故 `repoId === 文件夹名`。
 *
 *  运行前提：
 *    1) backend 起在 127.0.0.1:8010（start-dev.bat）
 *    2) frontend dev 起在 127.0.0.1:5173
 *    3) 已在设置里配置「仓库文件夹」（settings.outputDir）
 *  运行：
 *    cd frontend
 *    npx playwright test --config playwright.e2e-manual.config.ts --reporter=list
 */
import { expect, test } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync, rmdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { deflateSync } from "node:zlib";

/** 定位仓库根：兼容「在 frontend/ 下运行」（惯例）与「在仓库根运行」两种 cwd。
 *  （不用 import.meta.url——Playwright 在本项目默认转译为 CJS，import.meta 不可用） */
function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

const REPO = findRepoRoot();
const STATE_FILE = join(REPO, "backend", "data", "user_state.json");
const SHOT_DIR = join(REPO, "_tmp", "e2e-doc-illustration");

const DOC_NAME = "_e2e_设定总集.md";
const IMG_NAME = "_e2e_demo.png";

/** 造 fixture 用的临时 repoId（B3/A）：未在 user_state 登记 → 作品文件夹名 = safe_seg(repoId)
 *  （只允许 [A-Za-z0-9._-]，本串原样保留），故 `repoId` 与文件夹名一致，spec 侧无需再查表。 */
const E2E_REPO_ID = "e2e-doc-repo";

/** 读后端运行时配置的「仓库文件夹根」（settings.outputDir）；未配置返回空串。 */
function worksRoot(): string {
  try {
    const state = JSON.parse(readFileSync(STATE_FILE, "utf-8"));
    const dir = state?.settings?.outputDir;
    return typeof dir === "string" ? dir.trim() : "";
  } catch {
    return "";
  }
}

// ── 无依赖 PNG 生成（Node 内置 zlib）：造一张肉眼可辨的渐变图，便于截图核对 ──
const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buf: Buffer): number {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([len, body, crc]);
}

function makePng(w: number, h: number): Buffer {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  let o = 0;
  for (let y = 0; y < h; y++) {
    raw[o++] = 0; // filter type: none
    for (let x = 0; x < w; x++) {
      raw[o++] = Math.round(40 + (180 * y) / (h - 1));
      raw[o++] = Math.round(90 + (60 * (h - 1 - y)) / (h - 1));
      raw[o++] = Math.round(180 - (60 * y) / (h - 1));
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // color type: truecolor
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(raw)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

const MD = `# 设定总集（链路③端到端验证）

> 验证：文档预览走 Markdown 渲染 + 相对插图 \`assets/${IMG_NAME}\` 解析成可访问素材 URL。

## 一、角色层

### 月见八千代

![月见八千代 立绘参考](assets/${IMG_NAME})

外貌细节：银白发、赤瞳、和服。

外部资料：参见 [角色原案](https://example.com/yachiyo)（外部素材整理进文档的场景）。

| 项 | 值 |
|---|---|
| 名字 | 月见八千代 |
| 发色 | 银色 |
| 瞳色 | 赤红 |

## 二、近期纪要

- 回合 1-3：初次登场。
`;

test("文档产物：预览走 Markdown + 相对插图真实渲染", async ({ page }) => {
  const root = worksRoot();
  test.skip(!root, "未配置 settings.outputDir（仓库文件夹根），跳过端到端验证");

  // B3/A：产物域 = 作品域（作品文件夹），docs/ 随作品域走
  const workDir = join(root, E2E_REPO_ID);
  const docsDir = join(workDir, "docs");
  const docPath = join(docsDir, DOC_NAME);
  const imgPath = join(docsDir, "assets", IMG_NAME);

  // ── 造 fixture（跑完在 finally 清理）──
  mkdirSync(dirname(imgPath), { recursive: true });
  writeFileSync(imgPath, makePng(360, 200));
  writeFileSync(docPath, MD, "utf-8");

  const errors: string[] = [];
  const failed: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`${m.text()} @ ${m.location().url}`);
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`);
  });

  try {
    const query = new URLSearchParams({
      outputDir: root,
      repoId: E2E_REPO_ID,
      docPath,
      docSize: String(statSync(docPath).size),
    });
    await page.goto(`/e2e-manual/doc-illustration.html?${query}`);

    // 1) 产物卡出现：文档名 + 「文档」类型标签（且不再露出父目录名 docs）
    const tile = page.locator(".artifact-tile").first();
    await expect(tile).toBeVisible();
    await expect(tile).toContainText("设定总集");
    await expect(tile).toContainText("文档");
    await expect(tile).not.toContainText("docs");
    await expect(tile).toContainText("Doc·MD");
    await page.screenshot({ path: join(SHOT_DIR, "01-card.png") });

    // 2) 点击卡片打开预览弹层
    await tile.click();
    const body = page.locator(".artifact-modal-markdown");
    await expect(body).toBeVisible();

    // 3) 走 Markdown 渲染（不是纯文本 <pre>）：标题与表格都成了 HTML
    await expect(page.locator(".artifact-modal-pre")).toHaveCount(0);
    await expect(body.locator("h1")).toContainText("设定总集");
    await expect(body.locator("table")).toHaveCount(1);

    // 4) 插图 src 指向**内联**素材端点（而非原样相对路径）
    const img = body.locator("img").first();
    await expect(img).toBeVisible();
    const src = await img.getAttribute("src");
    expect(src).toContain("/api/artifacts/asset?");
    expect(src).toContain(encodeURIComponent(imgPath.replace(/\\/g, "/")));

    // 5) 关键：浏览器真的把图片解码渲染出来了（证明 /asset 返回的是可内联图片，
    //    而不是 download 端点那样带 attachment 的响应）
    await page.waitForFunction(() => {
      const el = document.querySelector(".artifact-modal-markdown img") as HTMLImageElement | null;
      return !!el && el.complete && el.naturalWidth > 0;
    });
    const natural = await img.evaluate((el) => (el as HTMLImageElement).naturalWidth);
    expect(natural).toBeGreaterThan(0);
    await page.screenshot({ path: join(SHOT_DIR, "02-preview.png") });

    // 6) 外链加固（C3，2026-09-10）：文档正文可能来自**外部网页素材**，其中的 http(s)
    //    链接必须落成 `target="_blank" + rel="noopener noreferrer"`——少一个 rel，新开
    //    页面就能经 `window.opener` 反向导航本应用（reverse tabnabbing）。在真实 DOM 上断言，
    //    证明预览这条 `dangerouslySetInnerHTML` 路径确实带上了钩子的加固（jsdom 单测只证明
    //    `renderMarkdown` 的输出，证明不了应用真的用它渲染）。
    const ext = body.locator("a[href^='http']").first();
    await expect(ext).toHaveAttribute("href", "https://example.com/yachiyo");
    await expect(ext).toHaveAttribute("target", "_blank");
    await expect(ext).toHaveAttribute("rel", /noopener/);
    await expect(ext).toHaveAttribute("rel", /noreferrer/);
    // 浏览器侧真实性：opener 已断开（新窗口拿不到本页句柄）
    expect(await ext.evaluate((el) => (el as HTMLAnchorElement).rel)).toContain("noopener");

    // 7) 除本页缺失的 favicon 外，无控制台报错、无异常响应
    const realErrors = errors.filter((e) => !e.includes("favicon"));
    expect(realErrors, `控制台报错：${realErrors.join(" | ")}`).toHaveLength(0);
    const bad = failed.filter((f) => !f.includes("favicon"));
    expect(bad, `异常响应：${bad.join(" | ")}`).toHaveLength(0);
  } finally {
    rmSync(docPath, { force: true });
    rmSync(imgPath, { force: true });
    // 清理由本 spec 创建的目录；非空/不存在则原样保留。
    // 注意：删除**空目录**要用 rmdirSync——rmSync 不带 recursive 对目录会抛 ERR_FS_EISDIR。
    for (const dir of [join(docsDir, "assets"), docsDir, workDir]) {
      try {
        rmdirSync(dir);
      } catch { /* 目录非空或不存在 */ }
    }
  }
});
