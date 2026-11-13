import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { expect, test, type Page, type APIRequestContext } from "@playwright/test";

/**
 * 工单 §7-1/3/5 · 智能编造**审批链整条**在真实浏览器里走一遍（零 LLM 花费）。
 *
 * 与 `fabric-approval.spec.ts` 的分工：那条只验「待批卡渲染」（会话由快照端点播种、
 * 出站请求被拦截，一次模型都不叫）。本条验**跑起来的链**：
 *   任务 → 固化询问 → 直接跑 → 执行期挂起（真断点落盘）→ 点批准 →
 *   ① 租约盘面写失败：屏上报错、**断点原样保留（动作指纹不变）**、动作没执行、
 *     租约里没多出授权、且这一轮**没烧任何 fabric 模型调用**（只有主管路由那一次）；
 *   ② 撤掉故障再批：同一个动作逐字执行、文件真落盘、断点被清；
 *   ③ 批准后模型改了路径：**重新挂起**请求批准，被批准的那个动作一次都没执行；
 *   ④（full 对照组）域内写**不逐条审批**、一轮交付、屏上不出审批条；越出作品域的写
 *     照样被路径域闸门拦下（full 的既定边界不豁免写入域），且是**拒绝**不是挂起。
 *
 * 模型是 `_tmp/stub_llm_server.py`（OpenAI 兼容桩，127.0.0.1:8399），
 * 后端是 `_tmp/probe_fabric_chain_app.py`（隔离 LAF_DATA_DIR + 只在标记文件存在时
 * 让 `capability_sandbox._save_persisted` 抛 OSError）。其余全是真代码：真 graph、
 * 真审批闸、真断点/租约盘面、真写盘。
 *
 * ⚠ 盘面与作品域**必须**在系统临时目录：`%TEMP%/demiurge-fabric-chain-e2e`
 *   （先跑 `_tmp/seed_chain_state.py` 生成隔离 user_state.json 与桩规则表）。
 *
 * 跑法（三个进程都得先起着）：
 *   1) 一键起桩+隔离后端：bash _tmp/run_chain_stack.sh
 *   2) 前端：MSYS_NO_PATHCONV=1 VITE_API_BASE=/api VITE_DEV_API_ORIGIN=http://127.0.0.1:8011 \
 *      node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174
 *      ⚠ 必须同源 `/api` + vite 代理（后端 CORS 白名单只有 5173）；
 *      ⚠ Git Bash 里不加 MSYS_NO_PATHCONV=1 会把 `/api` 变成 `<bash 根>/api`。
 *   3) E2E_BASE_URL=http://127.0.0.1:5174 \
 *      npx playwright test --config playwright.e2e-manual.config.ts \
 *        e2e-manual/fabric-approval-chain.spec.ts --output=../_tmp/e2e-fabric-chain-r1 --reporter=list
 */

const APPROVE_MARK = "智能编造需要批准";
const FAIL_MARK_TEXT = "审批失败：租约存储写入失败";
const FAIL_KEEP_TEXT = "断点已保留";
const TARGET_A = "审批链甲.md";
const TARGET_B = "审批链乙.md";
const TARGET_B2 = "审批链乙改.md";

function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

const REPO = findRepoRoot();
const SHOT_DIR = join(REPO, "_tmp", "e2e-fabric-approval");

/** 隔离盘面根：只认系统临时目录，写错地方就是往用户真实 backend/data 与作品库里塞垃圾。 */
function stateRoot(): string {
  const temp = (process.env.TEMP || process.env.TMP || "/tmp").replace(/\\/g, "/");
  const dir = `${temp}/demiurge-fabric-chain-e2e`;
  if (!/[\\/]temp[\\/]/i.test(`${temp}/`)) throw new Error(`TEMP 不像临时目录：${temp}`);
  if (!existsSync(join(dir, "works"))) {
    throw new Error(`隔离盘面未初始化，先跑 backend/.venv/Scripts/python.exe _tmp/seed_chain_state.py：${dir}`);
  }
  return dir;
}

const STATE = stateRoot();
const DATA = join(STATE, "data");
const WORKS = join(STATE, "works");
const MARKER = join(STATE, "fail_lease_write");
const CP_FILE = join(DATA, "task_progress", "fabric_checkpoints.json");
const LEASE_FILE = join(DATA, "capability_leases.json");
const STUB_LOG = join(STATE, "stub-log.jsonl");

type Cp = {
  id?: string;
  status?: string;
  pending_tool?: string;
  pending_action_hash?: string;
  pending_paths?: string[];
};

/** 断点盘面真形态＝{断点 id: 条目}（`task_progress_store` 按 id 建索引）。 */
function checkpoint(threadId: string): Cp | null {
  if (!existsSync(CP_FILE)) return null;
  const raw = JSON.parse(readFileSync(CP_FILE, "utf8"));
  const entries: Cp[] = Array.isArray(raw) ? raw : Object.values(raw) as Cp[];
  return entries.find((e) => e?.id === `fabric:${threadId}`) ?? null;
}

function awaiting(threadId: string): Cp | null {
  const cp = checkpoint(threadId);
  return cp && cp.status === "awaiting_approval" ? cp : null;
}

type Lease = { subject?: string; approved_by?: string; capabilities?: { operation?: string; path?: string }[] };

/** 本会话线的租约（按 subject 精确取，避免把上一轮的遗留条目算进来）。 */
function grantedOps(subject: string): string[] {
  if (!existsSync(LEASE_FILE)) return [];
  const list = JSON.parse(readFileSync(LEASE_FILE, "utf8")) as Lease[];
  return list.filter((l) => l?.subject === subject)
    .flatMap((l) => (l.capabilities || []).map((c) => String(c.operation || "")));
}

type StubRow = { seq?: number; system_head?: string; rule?: string; reply?: string };

function stubRows(): StubRow[] {
  if (!existsSync(STUB_LOG)) return [];
  return readFileSync(STUB_LOG, "utf8").split("\n").filter(Boolean)
    .map((l) => { try { return JSON.parse(l) as StubRow; } catch { return {}; } });
}

/** 自由循环自己的调用（主管路由的 system 是「调度主管」，不计）。 */
function fabricCallCount(): number {
  return stubRows().filter((r) => /智能编造 Agent/.test(String(r.system_head || ""))).length;
}

async function resetStub(request: APIRequestContext) {
  const res = await request.post("http://127.0.0.1:8399/reset");
  expect(res.ok()).toBe(true);
}

/** 只清本链路的盘面痕迹（标记/断点/租约/目标文件/桩日志），不动隔离 user_state 与规则表。 */
function resetDisk() {
  for (const p of [MARKER, CP_FILE, LEASE_FILE, STUB_LOG]) rmSync(p, { force: true });
  for (const n of [TARGET_A, TARGET_B, TARGET_B2]) rmSync(join(WORKS, n), { force: true });
}

function setFault(on: boolean) {
  if (on) writeFileSync(MARKER, "on", "utf8");
  else rmSync(MARKER, { force: true });
}

async function openChat(page: Page, threadId: string, name: string) {
  // 设置回填（GET /api/user-state）必须**先于**发消息完成，否则请求体里没有桩模型的 base_url/model。
  const hydrated = page.waitForResponse(
    (r) => /\/api\/user-state/.test(r.url()) && r.ok(), { timeout: 30_000 });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const query = new URLSearchParams({ repoId: threadId, repoName: name });
  await page.goto(`/e2e-manual/fabric-approval.html?${query}`);
  await hydrated;
  await expect(page.locator("textarea.rich-input")).toBeVisible({ timeout: 30_000 });
}

async function sendText(page: Page, text: string) {
  const box = page.locator("textarea.rich-input");
  await box.click();
  await box.fill(text);
  await box.press("Enter");
}

function botBubbles(page: Page) {
  return page.locator(".msg-bot");
}

function lastBotBubble(page: Page) {
  return botBubbles(page).last();
}

/** 等屏上**追加**一条已回复完的 bot 气泡（多出一条、没有「生成中」、非空）。 */
async function waitReply(page: Page, before: number, timeout = 180_000) {
  await expect
    .poll(async () => {
      const n = await botBubbles(page).count();
      if (n <= before) return false;
      const spinning = await lastBotBubble(page).locator(".bot-streaming").count();
      const body = (await lastBotBubble(page).innerText()).trim();
      return spinning === 0 && body.length > 0;
    }, { timeout, intervals: [500, 1_000, 2_000] })
    .toBe(true);
  return (await lastBotBubble(page).innerText()).trim();
}

async function sendTurn(page: Page, text: string, timeout = 180_000) {
  const before = await botBubbles(page).count();
  await sendText(page, text);
  return waitReply(page, before, timeout);
}

/** 点审批条上的键＝**由界面发出**「批准/取消」这句话，同样要等一轮新回复。 */
async function clickApprove(page: Page, timeout = 180_000) {
  const before = await botBubbles(page).count();
  await page.locator(".fabric-approval-bar").last()
    .getByRole("button", { name: "✅ 批准继续" }).click();
  return waitReply(page, before, timeout);
}

test.beforeAll(() => {
  mkdirSync(SHOT_DIR, { recursive: true });
});

test.beforeEach(async ({ request }) => {
  resetDisk();
  await resetStub(request);
});

test("批准时租约写盘失败：屏上报错、断点与动作指纹保留、动作与授权都没落地、没烧模型；撤障后重批真落盘并清断点", async ({ page }) => {
  test.setTimeout(420_000);
  const threadId = `e2e-fabric-chain-A-${Date.now()}`;
  const target = join(WORKS, TARGET_A);
  await openChat(page, threadId, "审批链甲");

  // ① 任务 → 固化询问 → 直接跑 → 执行期挂起（真断点，不是播种的文本）
  const offer = await sendTurn(page, `甲案：把章节要点整理成一份设定文档`);
  expect(offer.length).toBeGreaterThan(0);
  const paused = await sendTurn(page, "直接跑");
  expect(paused).toContain(APPROVE_MARK);
  expect(paused).toContain(TARGET_A);
  await expect(page.locator(".fabric-approval-bar")).toBeVisible({ timeout: 15_000 });

  const cp1 = awaiting(threadId);
  expect(cp1, "批准前应有一条 awaiting_approval 断点").not.toBeNull();
  expect(cp1!.pending_tool).toBe("file.write_text");
  const hash1 = cp1!.pending_action_hash || "";
  expect(hash1.length).toBeGreaterThan(0);
  expect(grantedOps(`fabric:${threadId}`), "挂起时不该已有写授权").toEqual([]);
  expect(existsSync(target)).toBe(false);
  await page.screenshot({ path: join(SHOT_DIR, "chain-01-awaiting.png"), fullPage: true });

  // ② 故障注入开：点「✅ 批准继续」
  const callsBefore = fabricCallCount();
  const rowsBefore = stubRows().length;
  setFault(true);
  const failure = await clickApprove(page);
  expect(failure).toContain(FAIL_MARK_TEXT);
  expect(failure).toContain(FAIL_KEEP_TEXT);
  await page.screenshot({ path: join(SHOT_DIR, "chain-02-lease-write-failed.png"), fullPage: true });

  // ③ 失败必须是「假成功」的反面：断点原样在、指纹未变、动作没跑、授权没落盘
  const cp2 = awaiting(threadId);
  expect(cp2, "写盘失败后断点必须保留").not.toBeNull();
  expect(cp2!.pending_action_hash).toBe(hash1);
  expect(existsSync(target), "失败的批准绝不能把文件写出去").toBe(false);
  expect(grantedOps(`fabric:${threadId}`), "失败的批准绝不能留下写授权").toEqual([]);
  // ④ 失败的那一次批准**没叫过自由循环模型**：新增的桩调用全是主管路由
  expect(fabricCallCount()).toBe(callsBefore);
  const added = stubRows().slice(rowsBefore);
  expect(added.length, "批准这一轮至少有主管路由一次").toBeGreaterThan(0);
  expect(added.every((r) => !/智能编造 Agent/.test(String(r.system_head || ""))),
    `失败批准不该叫自由循环模型，实际新增=${JSON.stringify(added.map((r) => r.system_head))}`)
    .toBe(true);

  // ⑤ 撤障重批：同一个动作逐字执行 → 文件落盘 → 断点被清
  setFault(false);
  const resumed = await sendTurn(page, "批准");
  expect(resumed).toContain(TARGET_A);
  expect(existsSync(target), "重批后应真落盘").toBe(true);
  expect(readFileSync(target, "utf8")).toContain("第一行设定");
  expect(await awaiting(threadId), "执行完必须清断点").toBeNull();
  await page.screenshot({ path: join(SHOT_DIR, "chain-03-retry-written.png"), fullPage: true });
});

test("full 访问标准：域内写不逐条审批、一拍落盘；越出作品域的写照样被路径域闸门拦下", async ({ page, request }) => {
  test.setTimeout(300_000);
  const threadId = `e2e-fabric-chain-C-${Date.now()}`;
  const inDomain = join(WORKS, "审批链丙.md");
  const outDomain = join(STATE, "outside", "越域探针.md");
  const stateFile = join(DATA, "user_state.json");
  const original = readFileSync(stateFile, "utf8");
  const setMode = (mode: string) => {
    const doc = JSON.parse(original);
    doc.settings.agentAccessMode = mode;
    writeFileSync(stateFile, JSON.stringify(doc, null, 2), "utf8");
  };
  try {
    setMode("full");
    rmSync(inDomain, { force: true });
    rmSync(outDomain, { force: true });
    await resetStub(request);
    await openChat(page, threadId, "审批链丙");

    // ① 域内写：full 不逐条审批 ⇒ 屏上不出审批条，一轮就交付，盘面不留 awaiting 断点
    await sendTurn(page, `丙案：把章节要点整理成一份设定文档`);
    const done = await sendTurn(page, "直接跑");
    expect(done).toContain("审批链丙.md");
    await expect(page.locator(".fabric-approval-bar")).toHaveCount(0);
    expect(await awaiting(threadId), "full 不该挂起审批").toBeNull();
    expect(existsSync(inDomain), "full 下域内写应直接落盘").toBe(true);
    await page.screenshot({ path: join(SHOT_DIR, "chain-05-full-no-approval.png"), fullPage: true });

    // ② 越域写：full 的既定边界**不豁免写入路径域** ⇒ 拦下、不写、不挂审批（不是授权问题）
    const threadId2 = `e2e-fabric-chain-D-${Date.now()}`;
    await openChat(page, threadId2, "审批链丁");
    await sendTurn(page, `丁案：把章节要点整理成一份设定文档`);
    const blocked = await sendTurn(page, "直接跑");
    expect(blocked.length).toBeGreaterThan(0);
    expect(existsSync(outDomain), "越域写批准也不放行，full 更不放行").toBe(false);
    expect(await awaiting(threadId2), "越域写是拒绝，不是挂起等批").toBeNull();
    await page.screenshot({ path: join(SHOT_DIR, "chain-06-full-out-of-domain.png"), fullPage: true });
  } finally {
    writeFileSync(stateFile, original, "utf8");
    rmSync(inDomain, { force: true });
    rmSync(outDomain, { force: true });
  }
});

test("批准后模型改了路径：重新挂起请求批准，被批准的那一条动作一次都没执行", async ({ page }) => {
  test.setTimeout(300_000);
  const threadId = `e2e-fabric-chain-B-${Date.now()}`;
  const targetB = join(WORKS, TARGET_B);
  const targetB2 = join(WORKS, TARGET_B2);
  await openChat(page, threadId, "审批链乙");

  await sendTurn(page, `乙案：把章节要点整理成一份设定文档`);
  const paused = await sendTurn(page, "直接跑");
  expect(paused).toContain(APPROVE_MARK);
  expect(paused).toContain(TARGET_B);
  const cp1 = awaiting(threadId);
  expect(cp1, "批准前应有一条 awaiting_approval 断点").not.toBeNull();
  const hash1 = cp1!.pending_action_hash || "";

  // 批准只放行上面那一条；模型下一拍换了路径 ⇒ 必须重新挂起
  const again = await clickApprove(page);
  expect(again).toContain(APPROVE_MARK);
  expect(again).toContain(TARGET_B2);
  const cp2 = awaiting(threadId);
  expect(cp2, "换路径后应重新挂起").not.toBeNull();
  expect(cp2!.pending_action_hash).not.toBe(hash1);
  expect(existsSync(targetB), "被批准的动作没被执行才是「一次一批」成立").toBe(false);
  expect(existsSync(targetB2), "换掉的新路径更不该写").toBe(false);
  await page.screenshot({ path: join(SHOT_DIR, "chain-04-repause-new-path.png"), fullPage: true });
});
