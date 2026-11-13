import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

/**
 * ④ 世界书整本删除（防误删刀：确认闸 + 先备份再删）——真实浏览器 + **真实 WorldBook 页** + 真实隔离后端。
 *
 * 单测证的是 `worldbook_store.delete_book` 的返回语义；证不到这四条用户可见事实：
 *   书单行尾那颗垃圾桶真在「世界书」页上；点下去先弹「删除整本世界书」，
 *   **没点确认之前源文件必须还在盘上**（取消 ⇒ 一个字没删）；
 *   确认之后屏上出现备份文件名，且那个 `.bak-<时间戳>` 真落盘、内容就是原书（中文没被转义）；
 *   备份名结尾不是 `.json` ⇒ 书单不会把 `.bak-*` 当书列出来。
 *
 * 跑法（两个隔离进程必须先在起着。⚠ 世界书根**必须**在系统临时目录，
 * 否则会删到用户真实 `worldbookDir` 里的设定资产）：
 *   1) 隔离后端：LAF_DATA_DIR=<临时状态目录> .venv/Scripts/python.exe -m uvicorn app.main:app --port 8011
 *      ⚠ 该目录下的 `user_state.json` 要写好 `settings.worldbookDir` 指向一个临时目录——
 *      第 ⑦ 刀后端只接受「`base` == 配置根」，本 spec 的隔离根也**从这里读**（不再由环境变量拼）。
 *   2) 隔离前端：VITE_API_BASE=/api VITE_DEV_API_ORIGIN=http://127.0.0.1:8011 npx vite --port 5174
 *      ⚠ 必须走同源 `/api` + vite 代理：后端 CORS 白名单只有 5173 那一条（`app/main.py`），5174 跨源会被浏览器拦读。
 *   3) E2E_BASE_URL=http://127.0.0.1:5174 \
 *      npx playwright test --config playwright.e2e-manual.config.ts e2e-manual/worldbook-delete.spec.ts
 */

function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

const REPO = findRepoRoot();
const SHOT_DIR = join(REPO, "_tmp", "e2e-worldbook-delete");
/** 每跑一次用**新**书名：配置根是共享目录，同名重跑会把上一次的 `.bak-*` 攒下来，
 *  「本次只留一份备份」这条断言就再也判不出真假（不回收旧目录——Windows 上递归删除会撞回收站守卫）。 */
const BOOK_NAME = `书甲-${Date.now()}`;
const BOOK_TEXT = "灯下那把旧藤椅是乙小满的，谁也不许搬走。";

/** 只接受**系统临时目录**下的世界书根：这屏验收会真删文件，配置写错地方就是毁用户的设定资产。 */
function requireTempRoot(value: string, from: string): string {
  const trimmed = (value || "").trim();
  if (!trimmed) {
    throw new Error(`${from} 里的「世界书文件夹」没配置：请在隔离后端的 user_state.json 指向临时目录。`);
  }
  if (!/[\\/](temp|tmp)[\\/]/i.test(trimmed)) {
    throw new Error(`世界书根看起来不是临时目录：${trimmed}`);
  }
  return trimmed;
}

/** 第 ⑦ 刀后 `base` 必须逐字等于后端配置的「世界书文件夹」⇒ 隔离根从服务侧读，不再由环境变量拼。 */
async function configuredBookRoot(page: Page): Promise<string> {
  const resp = await page.request.get("/api/state");
  if (!resp.ok()) throw new Error(`/api/state 取配置失败：HTTP ${resp.status()}`);
  const state = (await resp.json()) as { settings?: { worldbookDir?: string } };
  return requireTempRoot(state.settings?.worldbookDir ?? "", "隔离后端的设置");
}

/** 合成独立世界书：ST 式 entries 字典，两条足够让「条数」在屏上显形。 */
function buildBook() {
  return {
    name: BOOK_NAME,
    entries: {
      "0": { content: BOOK_TEXT, comment: "藤椅", keys: ["藤椅"], constant: true, enabled: true },
      "1": { content: "廊下的灯到天亮都亮着。", comment: "廊灯", keys: ["廊下"], constant: false, enabled: true },
    },
  };
}

test("删除整本世界书：不确认不删 → 确认后先备份再删且屏上报出备份文件名（真实页面 + 真实端点 + 真实落盘）", async ({ page }) => {
  test.setTimeout(120_000);
  // 隔离根 = 后端配置的 `worldbookDir`（第 ⑦ 刀：`base` 指错根会被 400 挡掉），
  // 书名自带时间戳 ⇒ 重跑不撞上一次的备份，也不用递归删旧目录（会撞回收站守卫）。
  const bookDir = await configuredBookRoot(page);
  mkdirSync(bookDir, { recursive: true });
  mkdirSync(SHOT_DIR, { recursive: true });

  const bookPath = join(bookDir, `${BOOK_NAME}.json`);
  writeFileSync(bookPath, JSON.stringify(buildBook(), null, 2), "utf-8");

  const errors: string[] = [];
  const failed: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    const url = r.url();
    if (/\/worldbook/.test(url) && r.status() >= 400) failed.push(`${r.status()} ${url}`);
  });

  // 1) 真实页面上，书单里出现「书甲 2」
  const query = new URLSearchParams({ worldbookDir: bookDir, characterDir: "" });
  await page.goto(`/e2e-manual/worldbook-delete.html?${query}`);
  const row = page.locator("button.nav-item", { hasText: BOOK_NAME });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row).toContainText("2");
  const trash = page.locator('button[title="删除"]');
  await expect(trash).toHaveCount(1);
  await page.screenshot({ path: join(SHOT_DIR, "01-book-row.png"), fullPage: true });

  // 2) 点垃圾桶 ⇒ 先弹二次确认；**此时源文件必须还在**（确认闸没被绕过）
  await trash.first().click();
  const modal = page.locator(".modal-mask .modal");
  await expect(modal.getByRole("heading", { name: "删除整本世界书" })).toBeVisible();
  await expect(modal.getByText("删除之前，后端会先存一份备份")).toBeVisible();
  expect(existsSync(bookPath)).toBe(true);
  await page.screenshot({ path: join(SHOT_DIR, "02-confirm.png"), fullPage: true });

  // 3) 取消 ⇒ 一个字都没删，屏上仍是那张书
  await modal.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".modal-mask")).toHaveCount(0);
  expect(existsSync(bookPath)).toBe(true);
  await expect(row).toBeVisible();
  await page.screenshot({ path: join(SHOT_DIR, "03-after-cancel.png"), fullPage: true });

  // 4) 再点一次并确认 ⇒ 屏上报出备份文件名，且**只有文件名、没有整条本机路径**
  const deleteResp = page.waitForResponse((r) => /\/worldbook\/delete/.test(r.url()), { timeout: 30_000 });
  await trash.first().click();
  await modal.getByRole("button", { name: "删除", exact: true }).click();
  const body = (await (await deleteResp).json()) as { ok?: boolean; backup?: string };
  const notice = page.getByText("删除前的内容备份为", { exact: false });
  await expect(notice).toBeVisible({ timeout: 30_000 });
  const noticeText = (await notice.innerText()).trim();
  expect(noticeText).toContain(`已删除「${BOOK_NAME}」`);
  expect(noticeText).not.toContain(bookDir);
  const shownBackup = noticeText.split("备份为").pop()!.trim();
  const escapedName = BOOK_NAME.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  expect(shownBackup).toMatch(new RegExp(`^${escapedName}\\.json\\.bak-[0-9-]+$`));
  await page.screenshot({ path: join(SHOT_DIR, "04-deleted-notice.png"), fullPage: true });

  // 5) 盘上：源文件没了、备份在，且备份内容就是原书（中文没被转义 ⇒ 是复制不是重写）
  expect(existsSync(bookPath)).toBe(false);
  const backups = readdirSync(bookDir).filter((n) => n.startsWith(`${BOOK_NAME}.json.bak-`));
  expect(backups).toHaveLength(1);
  expect(backups[0]).toBe(shownBackup);
  const backed = JSON.parse(readFileSync(join(bookDir, backups[0]), "utf-8"));
  expect(Object.keys(backed.entries)).toHaveLength(2);
  expect(readFileSync(join(bookDir, backups[0]), "utf-8")).toContain(BOOK_TEXT);

  // 6) 端点回给前端的备份名与屏上同一个（屏上那句不是前端自己拼的）
  expect(body.backup).toBe(shownBackup);
  expect(body.ok).toBe(true);

  // 7) 书单里没有这本书：`.bak-*` 没被当成书列出来（hasText 是子串匹配，
  //    源书 `书甲-….json` 与备份 `书甲-….json.bak-…` 只要有任何一条上榜都会红）
  await expect(
    page.locator(".worldbook-library button.nav-item", { hasText: BOOK_NAME }),
  ).toHaveCount(0, { timeout: 30_000 });

  expect({ errors, failed }).toEqual({ errors: [], failed: [] });
});
