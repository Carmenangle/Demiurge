import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { expect, test, type Page, type APIRequestContext } from "@playwright/test";

/**
 * 工单 §7 步骤 2/5 · 智能编造审批卡在**真实浏览器**里的那一屏。
 *
 * 单测（`ChatMessages.fabricApproval.test.tsx`，jsdom）证不到这四条：
 *   ① 后端真源那句话进了 DOM 后，Markdown 渲染**仍然**满足跨语言契约
 *      （前端分支靠 `智能编造需要批准` 字面量匹配——渲染层若吞了它就整条不出）；
 *   ② 动作参数是**文本**：`<img onerror>` 不生成元素、不弹框、不抛 JS 异常；
 *      整段动作始终是**一个**代码块，收尾那句落在块外（提前闭合的形态就是它被并进 code 里）；
 *   ③ 完整命令逐字可见（截断=用户批了一条自己没看全的命令）、超长值显示摘要后缀、
 *      密钥真值不在屏上；
 *   ④ 点「✅ 批准继续」真的发出 `message: "批准"` 这条 wire（批准＝发一句话，不是本地状态）。
 *
 * 审批文本一律来自后端生产接缝（`_tmp/seed_fabric_approval_text.py` 真跑
 * `fabric_loop.run_loop` + `agent_fabric._fabric_approval_text`），spec 不抄写句子。
 *
 * 跑法（两个隔离进程必须先在起着。⚠ 状态目录与仓库根**必须**在系统临时目录，
 * 否则会写进用户真实 `backend/data` 与真实作品库）：
 *   1) 后端：LAF_DATA_DIR=<临时状态目录> .venv/Scripts/python.exe -m uvicorn app.main:app --port 8011
 *      该目录 user_state.json 的 settings.outputDir 指向 <临时仓库根>
 *   2) 前端：MSYS_NO_PATHCONV=1 VITE_API_BASE=/api VITE_DEV_API_ORIGIN=http://127.0.0.1:8011 \
 *      node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174
 *      ⚠ 必须同源 `/api` + vite 代理：后端 CORS 白名单只有 5173，5174 跨源会被拦读。
 *      ⚠ Git Bash 里 `VITE_API_BASE=/api` 会被 MSYS _path_mangling 成 `<bash 根>/api`
 *        （实测 `D:/tool/develop/Git/api` ⇒ 所有请求撞 `file://`、会话一条都不渲染，
 *         现象长得像「快照没读到」）。加 MSYS_NO_PATHCONV=1，或改用 cmd/PowerShell 起。
 *   3) E2E_FABRIC_FIXTURE=<夹具 json> E2E_BASE_URL=http://127.0.0.1:5174 \
 *      npx playwright test --config playwright.e2e-manual.config.ts e2e-manual/fabric-approval.spec.ts
 *
 * 零 LLM：出站 `/api/ai/multi-agent` 在浏览器侧被拦截并回一条 error 事件，
 * 断言的是**请求体**，模型一次都没被叫。
 */

const APPROVE_MARK = "智能编造需要批准";
const BINDING_HINT = "本次放行的确切动作（批准只放行这一个动作——换路径、改内容或重复发起都会重新请求批准）";
const READING_HINT = "本次放行的确切动作（批准放行上面这一条路径的读取（同一路径不重复请求批准））";
const BAR_HINT = "批准只放行上面那一个动作（换路径、改内容或重复发起都会重新请求批准）";
// 真值与 _tmp/seed_fabric_approval_text.py 一致；拆开写只为不撞发布预检的密钥正则。
const SECRET = "sk-" + "SHOULD-NEVER-RENDER-0123456789abcdef";

function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

const REPO = findRepoRoot();
const SHOT_DIR = join(REPO, "_tmp", "e2e-fabric-approval");

type FixtureCase = {
  name: string;
  status: string;
  approvalText: string;
  operation: string;
  pendingParams: Record<string, unknown>;
  pendingActionHash: string;
  pendingPaths: string[];
};

/** 夹具必须来自系统临时目录：里面写着本机绝对路径，不能被 spec 当仓库资源读。 */
function fixtureCases(): FixtureCase[] {
  const file = (process.env.E2E_FABRIC_FIXTURE || "").trim();
  if (!file) throw new Error("必须设置 E2E_FABRIC_FIXTURE 指向 %TEMP% 下的 approval-fixtures.json。");
  if (!/[\\/](temp|tmp)[\\/]/i.test(file)) throw new Error(`E2E_FABRIC_FIXTURE 看起来不是临时目录：${file}`);
  if (!existsSync(file)) throw new Error(`夹具不存在，先跑 _tmp/seed_fabric_approval_text.py：${file}`);
  const doc = JSON.parse(readFileSync(file, "utf-8"));
  return doc.cases as FixtureCase[];
}

function caseOf(cases: FixtureCase[], name: string): FixtureCase {
  const found = cases.find((c) => c.name === name);
  if (!found) throw new Error(`夹具缺用例 ${name}`);
  return found;
}

/** 一个 thread_id 一条会话线：把「用户提出任务 + 后端待批回复」播进真实快照端点。 */
async function seedConversation(request: APIRequestContext, threadId: string, c: FixtureCase) {
  const res = await request.post("/api/ai/chat/snapshot/save", {
    data: {
      thread_id: threadId,
      messages: [
        { id: "seed-ask", role: "user", text: `让智能编造处理「${c.name}」这条任务。` },
        { id: "seed-approval", role: "assistant", text: c.approvalText },
      ],
    },
  });
  expect(res.ok()).toBe(true);
}

async function openChat(page: Page, threadId: string, name: string) {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const query = new URLSearchParams({ repoId: threadId, repoName: name });
  await page.goto(`/e2e-manual/fabric-approval.html?${query}`);
}

/** 只锁本链路：其它端点（模板/LoRA/叙事诊断）在隔离实例上未必有数据。 */
function watchNoise(page: Page, errors: string[], failed: string[]) {
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400 && /\/chat\/snapshot/.test(r.url())) failed.push(`${r.status()} ${r.url()}`);
  });
}

function lastBotBubble(page: Page) {
  return page.locator(".msg-bot").last();
}

test.beforeAll(() => {
  mkdirSync(SHOT_DIR, { recursive: true });
});

test("待批条渲染：真源句子进 DOM、注入不生效、围栏不提前闭合、完整命令可见", async ({ page, request }) => {
  test.setTimeout(120_000);
  const c = caseOf(fixtureCases(), "shell-visible");
  const errors: string[] = [];
  const failed: string[] = [];
  const dialogs: string[] = [];
  page.on("dialog", (d) => { dialogs.push(d.message()); d.dismiss().catch(() => undefined); });
  watchNoise(page, errors, failed);

  const threadId = `e2e-fabric-shell-${Date.now()}`;
  await seedConversation(request, threadId, c);
  await openChat(page, threadId, "审批卡甲");

  const bubble = lastBotBubble(page);
  await expect(bubble).toBeVisible({ timeout: 30_000 });
  // ① 跨语言契约的锚点句子在**渲染之后**仍然可见（前端分支就靠它匹配）
  await expect(bubble).toContainText(APPROVE_MARK);
  await expect(bubble).toContainText(c.operation);
  await expect(bubble).toContainText(BINDING_HINT);
  // ② 审批条 + 两颗键真的在这屏上
  const bar = page.locator(".fabric-approval-bar");
  await expect(bar).toBeVisible();
  await expect(bar.locator(".fabric-approval-hint")).toHaveText(BAR_HINT);
  await expect(bar.getByRole("button", { name: "✅ 批准继续" })).toBeVisible();
  await expect(bar.getByRole("button", { name: "✖ 取消中止" })).toBeVisible();

  const text = await bubble.innerText();
  // ③ 命令逐字完整：首尾都在，且没被省略号替代
  expect(text).toContain("Get-Content 章节甲.txt -Tail 40");
  expect(text).toContain("补一段长文本用来证明命令不截断。");
  expect(text).not.toContain("仅展示摘要");

  // ④ 注入的 HTML 只作为文本存在：不生成元素、不弹框、不抛异常
  //（动作在 JSON 预览里，引号被转义成 \"，所以按标签名片段断言，别拼完整字面量）
  await expect(bubble.locator("img")).toHaveCount(0);
  expect(text).toContain("<img src=x");
  expect(text).toContain("onerror=");
  expect(dialogs).toEqual([]);

  // ⑤ 整段动作仍是**一个**代码块，收尾那句话落在块外成为一个段落
  //（提前闭合的形态就是这句被并进 code 里，屏上看不见「回复批准」这行）。
  const blocks = bubble.locator("pre");
  await expect(blocks).toHaveCount(1);
  const block = await blocks.first().innerText();
  expect(block).toContain("```json");
  // 内层 ```json 那段是被转义进 JSON 字符串的，屏上就是 \"fence\":\"inside\"
  expect(block).toContain('\\"fence\\":\\"inside\\"');
  expect(block).toContain('"operation": "project.run_shell"');
  await expect(bubble.locator("p").last()).toHaveText("回复「批准」继续、「取消」中止。");
  expect(block).not.toContain("回复「批准」");
  expect(block).not.toContain("````");
  // 注：后端「围栏长度 = 正文最长反引号串 + 1」这条在本屏**不可判别**——动作走 JSON 序列化，
  // 参数里的换行会变成 \n 转义，内容永远造不出「行首 ```」，而 CommonMark 的行首围栏才闭合代码块。
  // 受控红检实测：把该规则退化成固定三反引号，本用例**仍全绿**（改回后夹具 hash 逐字节复原）。
  // 所以它是纵深防御的余量，不是这条屏上契约；屏上真正咬得住的是 ③「命令不被摘要替代」
  //（把 _APPROVAL_FULL_TEXT_KEYS 清空 ⇒ 本用例在 `not.toContain("仅展示摘要")` 那条断言红）。
  // ⑥ 屏上动作与后端指纹绑定的是同一条命令（同一段 preview JSON 的字面量）
  expect(block).toContain(JSON.stringify(c.pendingParams.command).slice(1, -1).slice(0, 40));

  await page.screenshot({ path: join(SHOT_DIR, "01-shell-approval.png"), fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
  expect(failed, failed.join("\n")).toEqual([]);
});

test("超长参数按摘要展示、写类措辞绑动作", async ({ page, request }) => {
  test.setTimeout(120_000);
  const c = caseOf(fixtureCases(), "write-summary");
  const errors: string[] = [];
  const failed: string[] = [];
  watchNoise(page, errors, failed);

  const threadId = `e2e-fabric-write-${Date.now()}`;
  await seedConversation(request, threadId, c);
  await openChat(page, threadId, "审批卡乙");

  const bubble = lastBotBubble(page);
  await expect(bubble).toContainText(APPROVE_MARK, { timeout: 30_000 });
  const text = await bubble.innerText();
  const originalChars = String((c.pendingParams.content as string).length);
  expect(text).toContain(`…（原文 ${originalChars} 字，仅展示摘要，批准即放行完整内容）`);
  expect(text).toContain(BINDING_HINT);
  expect(text).not.toContain(READING_HINT);
  // 摘要只缩屏上那一眼，路径仍逐字可见（批的是哪条路径必须看得清）
  expect(text).toContain("章节甲.txt");
  await page.screenshot({ path: join(SHOT_DIR, "02-write-summary.png"), fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
  expect(failed, failed.join("\n")).toEqual([]);
});

test("越域读取走读类措辞：路径可见且不套用写类一次性授权文案", async ({ page, request }) => {
  test.setTimeout(120_000);
  const c = caseOf(fixtureCases(), "read-outside");
  const errors: string[] = [];
  const failed: string[] = [];
  watchNoise(page, errors, failed);

  const threadId = `e2e-fabric-read-${Date.now()}`;
  await seedConversation(request, threadId, c);
  await openChat(page, threadId, "审批卡丙");

  const bubble = lastBotBubble(page);
  await expect(bubble).toContainText(APPROVE_MARK, { timeout: 30_000 });
  const text = await bubble.innerText();
  expect(text).toContain(`读取作品域外的 ${c.pendingPaths[0]}`);
  expect(text).toContain(READING_HINT);
  expect(text).not.toContain(BINDING_HINT);
  await page.screenshot({ path: join(SHOT_DIR, "03-read-outside.png"), fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
  expect(failed, failed.join("\n")).toEqual([]);
});

test("密钥脱敏在屏上成立：真值不进 DOM，只显示占位", async ({ page, request }) => {
  test.setTimeout(120_000);
  const c = caseOf(fixtureCases(), "secret-masked");
  const errors: string[] = [];
  const failed: string[] = [];
  watchNoise(page, errors, failed);

  const threadId = `e2e-fabric-secret-${Date.now()}`;
  await seedConversation(request, threadId, c);
  await openChat(page, threadId, "审批卡丁");

  const bubble = lastBotBubble(page);
  await expect(bubble).toContainText(APPROVE_MARK, { timeout: 30_000 });
  const text = await bubble.innerText();
  expect(text).toContain('"chat_key": "***（密钥已隐藏）"');
  expect(text).not.toContain(SECRET);
  // 整页（含其它组件与属性）都不得出现密钥真值
  expect(await page.locator("body").innerText()).not.toContain(SECRET);
  await page.screenshot({ path: join(SHOT_DIR, "04-secret-masked.png"), fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
  expect(failed, failed.join("\n")).toEqual([]);
});

test("点批准/取消真的发出 message=批准 / message=取消（出站 wire 拦截，零 LLM）", async ({ page, request }) => {
  test.setTimeout(120_000);
  const c = caseOf(fixtureCases(), "shell-visible");
  const errors: string[] = [];
  const failed: string[] = [];
  watchNoise(page, errors, failed);

  const threadId = `e2e-fabric-click-${Date.now()}`;
  await seedConversation(request, threadId, c);

  // 拦截出站的 agent wire：断言请求体，回一条 error 事件收尾，模型一次都不被调用。
  const sent: Array<Record<string, unknown>> = [];
  await page.route("**/api/ai/multi-agent", (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      body: 'data: {"error":"e2e 拦截：不发起真实模型调用"}\n\ndata: [DONE]\n\n',
    });
  });

  await openChat(page, threadId, "审批卡戊");
  const bar = page.locator(".fabric-approval-bar");
  await expect(bar).toBeVisible({ timeout: 30_000 });

  await bar.getByRole("button", { name: "✅ 批准继续" }).click();
  await expect.poll(() => sent.length, { timeout: 15_000 }).toBeGreaterThan(0);
  const approveWire = sent[sent.length - 1];
  expect(approveWire.message).toBe("批准");
  expect(approveWire.thread_id).toBe(threadId);
  await page.screenshot({ path: join(SHOT_DIR, "05-approved-wire.png"), fullPage: true });

  // 取消同样是一句话：不能只在本地改状态（否则刷新就丢「用户批过/拒过」的事实）
  sent.length = 0;
  await page.locator(".fabric-approval-bar").getByRole("button", { name: "✖ 取消中止" }).click();
  await expect.poll(() => sent.length, { timeout: 15_000 }).toBeGreaterThan(0);
  expect(sent[sent.length - 1].message).toBe("取消");
  await page.screenshot({ path: join(SHOT_DIR, "06-cancelled-wire.png"), fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
  expect(failed, failed.join("\n")).toEqual([]);
});
