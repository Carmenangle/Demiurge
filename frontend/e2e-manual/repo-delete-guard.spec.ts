import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * ②③ 删仓库的两条 fail-closed 闸——真实浏览器 + **真实 App（资产管理 → 作品）** + 真实隔离后端。
 *
 * 单测（`lib/repoDeletionGuard.test.ts`、`tests/test_user_state_delete_folder.py`）钉的是纯函数与
 * service 返回语义；证不到用户屏上这三件事：
 *   资产清单**查不到**时（本次用 route 打断 `/rag/generations` 造出真实失败），屏上弹「无法删除仓库」
 *     ·「资产清单查不到，已阻止删除」，而**不是**像改前那样 catch 掉继续删；
 *   作品文件夹被别的进程占用 ⇒ 后端不再谎报「删好了」，屏上弹「作品文件夹没能删干净，仓库条目已保留」，
 *     条目与文件夹都还在（改前 `rmtree(ignore_errors=True)` 会删一半却把条目抹掉，留下没人认领的孤儿）；
 *   占用释放后**再删一次就删干净**：条目消失、文件夹消失，且后端 repos 存档里也不再留着它
 *     （开机「以后端为准」，存档里留着就等于把删掉的作品复活回来）。
 *
 * 跑法（两个隔离进程必须先在起着。⚠ 作品根**必须**在系统临时目录，否则会删进用户真实作品库）：
 *   1) 隔离后端：LAF_DATA_DIR=<临时状态目录> .venv/Scripts/python.exe -m uvicorn app.main:app --port 8011
 *   2) 隔离前端：VITE_API_BASE=/api VITE_DEV_API_ORIGIN=http://127.0.0.1:8011 npx vite --port 5174
 *   3) E2E_REPO_DELETE_WORKS_ROOT=<临时作品根> E2E_REPO_DELETE_PYTHON=<python.exe 全路径> \
 *      E2E_BASE_URL=http://127.0.0.1:5174 \
 *      npx playwright test --config playwright.e2e-manual.config.ts e2e-manual/repo-delete-guard.spec.ts
 */

function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

const REPO = findRepoRoot();
const SHOT_DIR = join(REPO, "_tmp", "e2e-repo-delete-guard");
// 合成名 + 每次跑换后缀：作品条目会留在隔离 state 里，换名即天然干净（不回收旧条目——
// 删旧作品会真动磁盘，失败签名容易被误读成产品问题）。
const REPO_NAME = `甲作品${Date.now() % 100000}`;

/** 只接受**系统临时目录**下的作品根：这屏验收会真删文件夹，写错地方就是毁用户作品。 */
function tempRoot(envName: string): string {
  const value = (process.env[envName] || "").trim();
  if (!value) {
    throw new Error(`必须显式设置 ${envName} 指向隔离临时目录（否则会删进用户真实作品库）。`);
  }
  if (!/[\\/](temp|tmp)[\\/]/i.test(value)) {
    throw new Error(`${envName} 看起来不是临时目录：${value}`);
  }
  return value;
}

/** 另起一个进程占住文件句柄，造出 Windows 上真实的「删不净」（rmtree 撞 WinError 32）。 */
function holdFile(path: string, seconds: number) {
  const python = (process.env.E2E_REPO_DELETE_PYTHON || "").trim();
  if (!python) {
    throw new Error("必须显式设置 E2E_REPO_DELETE_PYTHON 指向 python.exe（用它占住文件句柄）。");
  }
  const code = "import sys,time\nf=open(sys.argv[1],'rb')\nf.read(1)\ntime.sleep(int(sys.argv[2]))\n";
  return spawn(python, ["-c", code, path, String(seconds)], { stdio: "ignore" });
}

/** 走用户那三步：卡片「⋯」菜单 → 删除 → 确认弹窗里点「删除」。 */
async function clickDelete(page: import("@playwright/test").Page, card: import("@playwright/test").Locator) {
  await card.locator('button[title="更多操作（重命名 / 删除）"]').click();
  await card.locator('.repo-card-menu button[role="menuitem"].danger').click();
  const modal = page.locator(".modal-mask .modal");
  await expect(modal.getByRole("heading", { name: "删除仓库" })).toBeVisible();
  await modal.getByRole("button", { name: "删除", exact: true }).click();
}

test("资产清单查不到 ⇒ 阻止删除；文件夹删不净 ⇒ 保留条目，释放后再删才成功", async ({ page, request }) => {
  test.setTimeout(180_000);
  const worksRoot = tempRoot("E2E_REPO_DELETE_WORKS_ROOT");
  mkdirSync(worksRoot, { recursive: true });
  mkdirSync(SHOT_DIR, { recursive: true });

  const errors: string[] = [];
  const failed: string[] = [];
  let folderDeleteCalls = 0;
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("request", (r) => {
    if (/\/user-state\/delete-folder/.test(r.url()) && r.method() === "POST") folderDeleteCalls += 1;
  });
  page.on("response", (r) => {
    if (/\/user-state/.test(r.url()) && r.status() >= 400) failed.push(`${r.status()} ${r.url()}`);
  });

  // 1) 真实 App 的「资产管理 → 作品」页上，用真实入口建一个作品
  await page.goto("/#/assets/works");
  await page.setViewportSize({ width: 1500, height: 950 });
  await expect(page.getByRole("button", { name: /新建仓库/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /新建仓库/ }).click();
  const prompt = page.locator(".modal-mask .modal");
  await expect(prompt.getByRole("heading", { name: "新建仓库" })).toBeVisible();
  await prompt.locator("input").fill(REPO_NAME);
  await prompt.getByRole("button", { name: "创建" }).click();
  const card = page.locator(".repo-card").filter({ hasText: REPO_NAME });
  await expect(card).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: join(SHOT_DIR, "01-repo-created.png"), fullPage: true });

  // 2) 闸一：让「资产清单」这条探针**真的失败**（不是返回空，是查不到）
  const generationsRoute = "**/api/rag/generations";
  await page.route(generationsRoute, (route) => route.abort());
  await clickDelete(page, card);

  // 3) 屏上弹「无法删除仓库」，写的是**查不到 ⇒ 已阻止删除**；这一趟连 delete-folder 都不该发出去
  const blocked = page.locator(".modal-mask .modal", { hasText: "无法删除仓库" });
  await expect(blocked).toBeVisible({ timeout: 30_000 });
  await expect(blocked).toContainText("资产清单查不到，已阻止删除");
  expect(folderDeleteCalls).toBe(0);
  await page.screenshot({ path: join(SHOT_DIR, "02-blocked-probe-failed.png"), fullPage: true });
  await blocked.getByRole("button", { name: "知道了" }).click();
  await expect(card).toBeVisible();
  await page.unroute(generationsRoute);

  // 4) 闸二：作品文件夹里有一个被别的进程占住的文件 ⇒ 后端删不净，且**不许谎报**
  const repoDir = join(worksRoot, REPO_NAME);
  mkdirSync(repoDir, { recursive: true });
  const lockedFile = join(repoDir, "占用中.png");
  writeFileSync(lockedFile, "locked by e2e");
  const holder = holdFile(lockedFile, 120);

  await clickDelete(page, card);
  await expect(blocked).toBeVisible({ timeout: 30_000 });
  await expect(blocked).toContainText("作品文件夹没能删干净，仓库条目已保留");
  await expect(blocked).toContainText(`${REPO_NAME}：删除作品文件夹失败`);
  await page.screenshot({ path: join(SHOT_DIR, "03-blocked-folder-not-removed.png"), fullPage: true });

  // 屏上报了失败，盘上就必须真的还没删、条目也还在（改前这里是「报删好了 + 条目抹掉 + 半个文件夹」）
  expect(existsSync(repoDir)).toBe(true);
  expect(existsSync(lockedFile)).toBe(true);
  await blocked.getByRole("button", { name: "知道了" }).click();
  await expect(card).toBeVisible();

  // 5) 释放占用后再删 ⇒ 这次删干净：条目消失、文件夹消失
  holder.kill();
  await new Promise<void>((done) => { holder.once("exit", () => done()); });
  await clickDelete(page, card);
  await expect(card).toHaveCount(0, { timeout: 30_000 });
  expect(existsSync(repoDir)).toBe(false);

  // 6) 后端存档里这条也得抹掉（600ms debounce 的镜像上传）：开机时「以后端为准」，
  //    存档里留着已删作品会让它带着一个不存在的文件夹复活（本用例跑在同一个浏览器会话里，等得到这次上传）。
  await expect.poll(async () => {
    const state = await (await request.get("/api/user-state")).json();
    return ((state.repos || []) as { name?: string }[]).some((r) => r.name === REPO_NAME);
  }, { timeout: 20_000, message: `后端 repos 存档里仍留着已删作品 ${REPO_NAME}` }).toBe(false);
  await page.screenshot({ path: join(SHOT_DIR, "04-deleted-after-release.png"), fullPage: true });

  expect({ errors, failed }).toEqual({ errors: [], failed: [] });
});
