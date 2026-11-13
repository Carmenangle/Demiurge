import { existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * ① 对话线软删层（防误删刀）——真实浏览器 + **真实 ChatView** + 真实隔离后端。
 *
 * 单测只能证 ChatArchiveModal 自己的逻辑，证不到这四条用户可见事实：
 *   清缓存那颗按钮真在对话页头顶工具条上；点下去先二次确认；
 *   确认后端**先**归档再清空（归档文件名里就写着原因与条数，屏上随之清零）；
 *   「恢复」列表里出现「清除缓存 · 5 条」，危险确认后整条覆盖回 5 条，
 *   且覆盖前把当时那 2 条也归档了一份（压错了还能再压回去）。
 *
 * 跑法（两个隔离进程必须先在起着。⚠ 状态目录与仓库根**必须**在系统临时目录，
 * 否则会往用户真实 `backend/data` 与真实作品库写会话/归档文件）：
 *   1) 隔离后端：LAF_DATA_DIR=<临时状态目录> .venv/Scripts/python.exe -m uvicorn app.main:app --port 8011
 *      该目录的 user_state.json 里 settings.outputDir 指向 <临时仓库根>
 *   2) 隔离前端：VITE_API_BASE=/api VITE_DEV_API_ORIGIN=http://127.0.0.1:8011 npx vite --port 5174
 *      ⚠ 必须走同源 `/api` + vite 代理，不能像 #9 那样把 VITE_API_BASE 指到 8011：
 *        后端 CORS 白名单只有 5173 那一条（`app/main.py`），5174 跨源会被浏览器拦读。
 *   3) E2E_CHAT_ARCHIVE_WORKS_ROOT=<临时仓库根> \
 *      E2E_CHAT_ARCHIVE_STATE_DIR=<临时状态目录> \
 *      E2E_BASE_URL=http://127.0.0.1:5174 \
 *      npx playwright test --config playwright.e2e-manual.config.ts e2e-manual/chat-archive-restore.spec.ts
 */

function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

const REPO = findRepoRoot();
const SHOT_DIR = join(REPO, "_tmp", "e2e-chat-archive");
// 每次跑用**新** thread_id：会话线 id＝仓库 id，归档落 `<状态目录>/chat_archives/<id>/`，
// 换新名即天然干净（不用删除——删除会撞 Windows 回收站守卫，失败签名容易被误读成产品问题）。
const REPO_ID = `e2e-archive-${Date.now()}`;
const REPO_NAME = "会话甲";
const MARK = "恢复验收";

/** 只接受**系统临时目录**下的隔离根：这屏验收会真写会话与归档文件，写错地方就是毁数据。 */
function tempRoot(envName: string): string {
  const value = (process.env[envName] || "").trim();
  if (!value) {
    throw new Error(`必须显式设置 ${envName} 指向隔离临时目录（否则会写进用户真实数据目录）。`);
  }
  if (!/[\\/](temp|tmp)[\\/]/i.test(value)) {
    throw new Error(`${envName} 看起来不是临时目录：${value}`);
  }
  return value;
}

/** 合成会话流：奇数条＝用户，偶数条＝助手，每条带唯一标号供屏上定位。 */
function buildMessages(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    id: `seed-${i + 1}`,
    role: i % 2 === 0 ? "user" : "assistant",
    text: `${MARK}第${i + 1}条：廊下的灯还亮着，风把袖子吹得贴在身上。`,
  }));
}

function archiveNames(stateDir: string): string[] {
  const folder = join(stateDir, "chat_archives", REPO_ID);
  if (!existsSync(folder)) return [];
  return readdirSync(folder).filter((n) => n.endsWith(".json")).sort();
}

function readArchive(stateDir: string, name: string) {
  return JSON.parse(readFileSync(join(stateDir, "chat_archives", REPO_ID, name), "utf-8"));
}

test("清缓存先归档 → 恢复整条覆盖回来（真实 ChatView + 真实端点 + 真实落盘）", async ({ page, request }) => {
  test.setTimeout(150_000);
  // ⚠ 宽度必须 >1400px：`styles.css` 在 (max-width:1400px) 里把「会话维护」整组收进
  //   「更多」浮层（`.head-tools-more{display:none}`），本用例要的是宽屏那一屏上直接可见的两颗键。
  await page.setViewportSize({ width: 1500, height: 950 });
  const worksRoot = tempRoot("E2E_CHAT_ARCHIVE_WORKS_ROOT");
  const stateDir = tempRoot("E2E_CHAT_ARCHIVE_STATE_DIR");
  mkdirSync(worksRoot, { recursive: true });
  mkdirSync(SHOT_DIR, { recursive: true });

  const errors: string[] = [];
  const failed: string[] = [];
  // 只锁**本链路**的响应：这一屏还会拉工作流模板／LoRA／叙事诊断，那些端点在隔离实例上
  // 未必有数据；但本链路的 4xx/5xx 与任何 JS 异常一律算失败。
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    const url = r.url();
    if (r.status() >= 400 && (/\/chat\/archives/.test(url) || /\/chat\/clear-cache/.test(url) || /\/chat\/snapshot/.test(url))) {
      failed.push(`${r.status()} ${url}`);
    }
  });

  const saveSnapshot = async (count: number) => {
    const res = await request.post("/api/ai/chat/snapshot/save", {
      data: { thread_id: REPO_ID, messages: buildMessages(count) },
    });
    expect(res.ok()).toBe(true);
  };
  const bubbles = page.locator(".msg-user, .msg-bot");

  // 1) 后端真源先放 5 条（走真实端点＝顺带验前端那台 vite 的 /api 转发）
  await saveSnapshot(5);
  const query = new URLSearchParams({ repoId: REPO_ID, repoName: REPO_NAME });
  await page.goto(`/e2e-manual/chat-archive-restore.html?${query}`);

  // 2) 用户可见的那一屏上，5 条消息真渲染出来了
  await expect(bubbles).toHaveCount(5, { timeout: 30_000 });
  await expect(page.getByText(`${MARK}第5条`)).toBeVisible();
  await page.screenshot({ path: join(SHOT_DIR, "01-five-messages.png"), fullPage: true });

  // 3) 点「清缓存」→ 先弹二次确认（不点确认就不该动数据）→ 屏上清零
  const clearButton = page.locator('button[title^="清除缓存"]');
  await expect(clearButton).toBeVisible();
  await clearButton.click();
  await expect(page.getByText("清除缓存会清空当前对话内容")).toBeVisible();
  await page.screenshot({ path: join(SHOT_DIR, "02-clear-confirm.png"), fullPage: true });
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await expect(bubbles).toHaveCount(0, { timeout: 30_000 });

  // 4) 归档真的**先**落盘了：文件名写着 clear-cache 与 5 条，正文里就是那 5 条原文
  const afterClear = archiveNames(stateDir);
  expect(afterClear.filter((n) => /-clear-cache-5\.json$/.test(n))).toHaveLength(1);
  const clearArchive = afterClear.find((n) => /-clear-cache-5\.json$/.test(n))!;
  expect(readArchive(stateDir, clearArchive).messages).toHaveLength(5);
  expect(readArchive(stateDir, clearArchive).reason).toBe("clear-cache");
  await page.screenshot({ path: join(SHOT_DIR, "03-after-clear.png"), fullPage: true });

  // 5) 清空后又写了 2 条新会话 ⇒ 「恢复」必须是**整条覆盖**，不是往后追加
  await saveSnapshot(2);
  await page.reload();
  await expect(bubbles).toHaveCount(2, { timeout: 30_000 });

  // 6) 「恢复」入口在同一屏，列表里出现「清除缓存 · 5 条」
  const restoreEntry = page.locator('button[title^="恢复对话"]');
  await expect(restoreEntry).toBeVisible();
  await restoreEntry.click();
  const modal = page.locator(".modal-mask .modal");
  await expect(modal.getByText("清除缓存 · 5 条")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: join(SHOT_DIR, "04-archive-list.png"), fullPage: true });

  // 7) 危险二次确认：确认之前不许覆盖（红线）
  await modal.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByText("用这份归档覆盖当前会话？")).toBeVisible();
  await page.screenshot({ path: join(SHOT_DIR, "05-restore-confirm.png"), fullPage: true });
  await page.getByRole("button", { name: "恢复这份" }).click();

  // 8) 屏上回到 5 条 + 后端真源回到 5 条 + 覆盖前那 2 条也留了一份 restore 归档
  await expect(bubbles).toHaveCount(5, { timeout: 30_000 });
  await expect(page.getByText(`${MARK}第5条`)).toBeVisible();
  await expect(page.locator(".canvas-toast")).toContainText("已恢复到", { timeout: 30_000 });
  const snapshot = await (await request.get(`/api/ai/chat/snapshot?thread_id=${REPO_ID}`)).json();
  expect(snapshot.items).toHaveLength(5);
  const names = archiveNames(stateDir);
  expect(names.filter((n) => /-clear-cache-/.test(n))).toHaveLength(1);
  expect(names.filter((n) => /-restore-2\.json$/.test(n))).toHaveLength(1);
  await page.screenshot({ path: join(SHOT_DIR, "06-restored.png"), fullPage: true });

  expect({ errors, failed }).toEqual({ errors: [], failed: [] });
});
