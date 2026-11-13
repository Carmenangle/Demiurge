import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * #9 正文外貌锚审核区（真实浏览器 + 真实后端）。
 *
 * 验的是「用户真能打开的那一屏」上这条确认闸是否闭环：
 * 扫描已落盘正文 → 出候选（全部待确认）→ 采纳一条 → 锚库文件里真的出现 approved，
 * 驳回的那条从屏上消失。四条红线里「未确认不进成图」只有在这一屏点过才证得实。
 *
 * 运行（前后端都要先在隔离端口起着，**仓库根必须是临时目录**，见下面 worksRoot）：
 *   1) 隔离后端：LAF_DATA_DIR=<临时状态目录> .venv/Scripts/python.exe -m uvicorn app.main:app --port 8011
 *      该目录的 user_state.json 里 settings.outputDir 指向临时仓库根；
 *   2) 隔离前端：VITE_API_BASE=http://127.0.0.1:8011/api npx vite --port 5173
 *   3) E2E_ANCHOR_WORKS_ROOT=<同一个临时仓库根> \
 *      npx playwright test --config playwright.e2e-manual.config.ts e2e-manual/appearance-anchor.spec.ts
 */

function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

const REPO = findRepoRoot();
const SHOT_DIR = join(REPO, "_tmp", "e2e-anchor");
// 每次跑用**新** repoId：锚库落在 `<仓库根>/<repoId>/appearance_anchor.json`，换新名即天然干净
// （不用删除——删除会撞 Windows 回收站守卫，且失败签名容易被误读成产品问题）。
const REPO_ID = `e2e-anchor-repo-${Date.now()}`;
const CARD_NAME = "阮清昭";

/** 隔离仓库根：spec 会把会话快照与锚库写进 `<根>/<REPO_ID>/`，所以这个根**必须**是临时目录。 */
function worksRoot(): string {
  const root = (process.env.E2E_ANCHOR_WORKS_ROOT || "").trim();
  if (!root) {
    throw new Error(
      "必须显式设置 E2E_ANCHOR_WORKS_ROOT 指向隔离仓库根（否则会往用户真实作品库写锚库文件）。");
  }
  if (!/[\\/](temp|tmp)[\\/]/i.test(root)) {
    throw new Error(`E2E_ANCHOR_WORKS_ROOT 看起来不是临时目录：${root}`);
  }
  return root;
}

const ROUND1 = `${CARD_NAME}乌发披散在肩上，末梢还坠着水珠。她抬起眼，一双蓝眸映着灯影，`
  + "廊下的风把月白法袍吹得贴在身上。泉石上铺了软垫，夜露从檐角滴下来，敲在阶前的青苔上，声音很轻。";
const ROUND2 = `${CARD_NAME}的乌发挽成发髻，插了一支素银簪。蓝眸在灯下像退了色似的浅，`
  + `${CARD_NAME}拢了拢月白法袍的袖子，指节因为用力而发白。院里的灯次第熄灭，只剩廊下那一盏还亮着，把她的影子拉得很长。`;

/** 与后端 `chat_snapshot.story_rounds` 的口径一致：assistant + route=roleplay + 够长的正文。 */
function seedSnapshot(folder: string) {
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, "chat.json"), JSON.stringify([
    { id: "u1", role: "user", text: "写一段夜里的她。" },
    { id: "a1", role: "assistant", route: "roleplay", text: ROUND1 },
    { id: "u2", role: "user", text: "再写一段。" },
    { id: "a2", role: "assistant", route: "roleplay", text: ROUND2 },
  ], null, 2), "utf-8");
}

function anchorRows(page: Page, label: string) {
  return page.locator("div")
    .filter({ has: page.getByText(`${CARD_NAME}｜${label}`, { exact: true }) })
    .last();
}

test("正文外貌锚：扫描出候选 → 采纳入锚 → 驳回退场（真实浏览器 + 真实端点）", async ({ page }) => {
  const root = worksRoot();
  const folder = join(root, REPO_ID);
  const anchorFile = join(folder, "appearance_anchor.json");
  seedSnapshot(folder);
  mkdirSync(SHOT_DIR, { recursive: true });

  const errors: string[] = [];
  const failed: string[] = [];
  // 只锁**本链路**：整个弹窗还会拉工作流模板／LoRA 列表／提示词默认值，那些端点在隔离实例上
  // 未必有数据（实测一条 `/vite.svg` 类的 404 资源报错与本刀无关）。JS 异常一律算失败。
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/appearance")) failed.push(`${r.status()} ${r.url()}`);
  });

  const query = new URLSearchParams({
    outputDir: root, repoId: REPO_ID, cardNames: CARD_NAME,
  });
  await page.goto(`/e2e-manual/appearance-anchor.html?${query}`);

  // 1) 审核区挂在用户真能打开的那一屏
  const summary = page.locator("summary", { hasText: "正文外貌锚" });
  await expect(summary).toBeVisible();
  await summary.click();
  await expect(page.getByRole("button", { name: "重新扫描正文" })).toBeEnabled();
  await expect(page.getByText("只有在这里点「采纳」的条目才会进成图提示词")).toBeVisible();

  // 2) 扫描 → 三类候选都只在「待确认」，锚库文件里还没有 approved（红线 1）
  await page.getByRole("button", { name: "重新扫描正文" }).click();
  await expect(anchorRows(page, "发色")).toBeVisible({ timeout: 15_000 });
  await expect(anchorRows(page, "瞳色")).toBeVisible();
  await expect(anchorRows(page, "衣着")).toBeVisible();
  await expect(summary).toContainText("待确认 3 条");
  await expect(page.getByText("长驻设定：发色／瞳色，跨轮不变")).toBeVisible();
  await expect(page.getByText("场景状态：衣着只在这类画面里用")).toBeVisible();
  if (existsSync(anchorFile)) {
    const before = JSON.parse(readFileSync(anchorFile, "utf-8"));
    expect(before.filter((item) => item.status === "approved")).toHaveLength(0);
  }
  await page.screenshot({ path: join(SHOT_DIR, "01-candidates.png"), fullPage: true });

  // 3) 采纳发色 → 该条转「已入锚」，且落盘文件里真的出现 approved
  await anchorRows(page, "发色").getByRole("button", { name: "采纳" }).click();
  await expect(page.getByText("已入锚 1 条：阮清昭·乌发")).toBeVisible();
  await expect(anchorRows(page, "发色")).toContainText("已入锚");
  const stored = JSON.parse(readFileSync(anchorFile, "utf-8"));
  expect(stored.filter((item) => item.status === "approved")
    .map((item) => `${item.actor}·${item.value}·${item.bucket}`)).toEqual([`${CARD_NAME}·乌发·外貌`]);
  await page.screenshot({ path: join(SHOT_DIR, "02-adopted.png"), fullPage: true });

  // 4) 改写后采纳（用户措辞优先于正文原话）
  const eyeRow = anchorRows(page, "瞳色");
  await eyeRow.getByRole("button", { name: "改写" }).click();
  await page.getByLabel("改写后的外貌写法").fill("浅蓝眸");
  await page.getByRole("button", { name: "按此采纳" }).click();
  await expect(anchorRows(page, "瞳色")).toContainText("浅蓝眸");
  expect(JSON.parse(readFileSync(anchorFile, "utf-8"))
    .some((item) => item.status === "approved" && item.value === "浅蓝眸")).toBe(true);

  // 5) 驳回衣着 → 该条从屏上退场，且不写进锚
  await anchorRows(page, "衣着").getByRole("button", { name: "驳回" }).click();
  await expect(anchorRows(page, "衣着")).toHaveCount(0);
  expect(JSON.parse(readFileSync(anchorFile, "utf-8"))
    .some((item) => item.status === "approved" && item.kind === "outfit")).toBe(false);

  // 6) 逐条删除两条已入锚的条目（本用例自己造的锚不留给用户），锚库回到「零 approved」
  await anchorRows(page, "发色").getByRole("button", { name: "删除" }).click();
  await expect(anchorRows(page, "发色")).toHaveCount(0);
  await anchorRows(page, "瞳色").getByRole("button", { name: "删除" }).click();
  await expect(anchorRows(page, "瞳色")).toHaveCount(0);
  expect(JSON.parse(readFileSync(anchorFile, "utf-8"))
    .some((item) => item.status === "approved")).toBe(false);
  await page.screenshot({ path: join(SHOT_DIR, "03-after-cleanup.png"), fullPage: true });

  expect({ errors, failed }).toEqual({ errors: [], failed: [] });
});
