import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

/**
 * 清单 #3／#4 无书诊断口径（真实浏览器 + 真实后端，零 LLM）。
 *
 * 验的是用户真能打开的那一屏：聊天页「诊断」面板在世界书缺席时，**不能**只留一句
 * 「暂无未处置的一致性诊断」——那与「查过、没问题」逐字同形（绿是空转出来的）。
 * 现在要把两条判据的参与情况摆出来：没参与 ⇒ 写「本轮未参与」+ 为什么没参与。
 *
 * 台账由**生产接缝**产出（`agency_scene_state._wb_run_narrative_ci`），不是手填的 JSON：
 * 见 `_tmp` 侧驱动 `seed_arms_live.py`（写两个隔离仓库，再让本用例只读地打开面板）。
 *
 * 运行：
 *   1) 隔离后端：LAF_DATA_DIR=<临时状态目录> .venv/Scripts/python.exe -m uvicorn app.main:app --port 8013
 *   2) 隔离前端：MSYS_NO_PATHCONV=1 VITE_DEV_API_ORIGIN=http://127.0.0.1:8013 VITE_API_BASE=/api \
 *      npx vite --port 5174 --strictPort
 *      ⚠ 两个变量都不能省，且 **`MSYS_NO_PATHCONV=1` 只在 Git Bash 下需要**：
 *      · 省掉 `VITE_API_BASE=/api` ⇒ dev 态 `client.ts` 的 `API_BASE` 缺省是**绝对的**
 *        `http://127.0.0.1:8010/api`（不走 vite 代理），8010 没在听时浏览器里只看到
 *        "Failed to fetch"，看着像产品坏了，其实是打错了端口。
 *      · Git Bash 会把以 `/` 开头的**环境变量值**改写成 Windows 路径：`/api` 到 node 手里
 *        变成 `<Git 安装目录>/api` ⇒ fetch 拼成 `file:///…`，同样是 "Failed to fetch"。
 *        判据：`node -e "console.log(process.env.VITE_API_BASE)"` 打出的不是 `/api`。
 *        （`MSYS2_ARG_CONV_EXCL="*"` 治不了这条，实测仍被改写。）
 *   3) E2E_BASE_URL=http://127.0.0.1:5174 E2E_ARMS_WORKS_ROOT=<隔离仓库根> \
 *      npx playwright test --config playwright.e2e-manual.config.ts e2e-manual/narrative-ci-arms.spec.ts
 */

const SHOT_DIR = join(tmpdir(), "demiurge-approb", "e2e-arms");
const PURE = "armspure";
const WITH_BOOK = "armsbook";

function worksRoot(): string {
  const root = (process.env.E2E_ARMS_WORKS_ROOT || "").trim();
  if (!root) {
    throw new Error("必须显式设置 E2E_ARMS_WORKS_ROOT 指向隔离仓库根（否则读到的是用户真实作品库）。");
  }
  if (!/[\\/](temp|tmp)[\\/]/i.test(root)) {
    throw new Error(`E2E_ARMS_WORKS_ROOT 看起来不是临时目录：${root}`);
  }
  return root;
}

async function openPanel(page: Page, root: string, repoId: string) {
  const query = new URLSearchParams({ outputDir: root, repoId });
  await page.goto(`/e2e-manual/narrative-ci-arms.html?${query}`);
  const toggle = page.getByRole("button", { name: "诊断" });
  await expect(toggle).toBeVisible();
  await toggle.click();
  return toggle;
}

test("纯卡作品：两条判据都写明「本轮未参与」，空列表文案跟着改口", async ({ page }) => {
  const root = worksRoot();
  mkdirSync(SHOT_DIR, { recursive: true });

  const errors: string[] = [];
  const failed: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/narrative")) failed.push(`${r.status()} ${r.url()}`);
  });

  await openPanel(page, root, PURE);

  const ledger = page.locator(".nci-arms");
  await expect(ledger).toBeVisible();
  await expect(ledger).toContainText("第 3 轮判据参与情况");
  await expect(ledger.locator(".nci-arm-name").nth(0)).toHaveText("世界规则");
  await expect(ledger.locator(".nci-arm-name").nth(1)).toHaveText("棘轮·设定存量");
  await expect(ledger.locator(".nci-arm-state")).toHaveText(["本轮未参与", "本轮未参与"]);
  // 原因要说对：这一臂不是「查了没问题」，是**没有输入**
  await expect(ledger.locator(".nci-arm-reason")).toHaveText([
    "这个作品没有世界书，判据没有输入",
    "这个作品没有世界书，判据没有输入",
  ]);
  await expect(page.locator(".nci-empty"))
    .toContainText("未参与＝没查，不等于查过没问题");
  await page.screenshot({ path: join(SHOT_DIR, "01-pure-card-idle.png"), fullPage: true });

  expect({ errors, failed }).toEqual({ errors: [], failed: [] });
});

test("有书作品：喂到输入的判据改报「已评估（输入 N 条）」，不再有空转文案", async ({ page }) => {
  const root = worksRoot();
  const errors: string[] = [];
  const failed: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/narrative")) failed.push(`${r.status()} ${r.url()}`);
  });

  await openPanel(page, root, WITH_BOOK);

  const ledger = page.locator(".nci-arms");
  await expect(ledger).toContainText("第 3 轮判据参与情况");
  await expect(ledger.locator(".nci-arm-state")).toHaveText([
    "已评估（输入 1 条）",
    "已评估（输入 2 条）",
  ]);
  await expect(ledger).not.toContainText("本轮未参与");
  // 两臂都跑了 ⇒ 空列表回到原句，不蹭「未参与」那半句
  await expect(page.locator(".nci-empty")).toHaveText("暂无未处置的一致性诊断。");
  await page.screenshot({ path: join(SHOT_DIR, "02-with-worldbook.png"), fullPage: true });

  expect({ errors, failed }).toEqual({ errors: [], failed: [] });
});
