/** manual e2e：画布「运转工作流」占位链路（**需要真实后端 8010 + 真实前端 dev 5173**）。
 *
 *  为什么单列 manual 目录：普通 `npm run test:e2e`（playwright.config.ts，testDir=./e2e）跑 mock 后端；
 *  本验证要的是**真实组件 + 真实后端**下的 DOM 事实 —— 画布组件没有任何 jsdom 测试，
 *  「事件 → 占位节点出现/更新/消失」这段只能在真浏览器里成立。
 *
 *  为什么不是「真的点一次运转」：真运转要 ComfyUI + 模板 + 模型（会真出图），不适合当回归网。
 *  改为**派发画布监听的同一批事件**（`laf-canvas-wf-run` / `-run-graph` / `-progress` / `-done`）——
 *  这批事件是真实运转链提交后由 `lib/runCanvasWorkflow.ts` 与 `sink.dispatchDone` 派发的，
 *  **消费方是真组件本体**（`views/CanvasStageFlow.tsx` 的 4 个 `addEventListener`），
 *  所以本 spec 验的是「事件 → DOM」这条真链路，而不是复刻件。
 *
 *  验收对象（2026-09-15 第二轮审计）：
 *    · P12 —— `wfRuns` 的列表演归（`addWfRun`/`upsertWfRun`/`updateWfRun`/`dropWfRun`），
 *      含「同一拍内连续两次事件各落一个空位」的刻意设计（落点必须在事务内用 `prev` 算）；
 *    · P10 —— 画布给编排链只提供 sink（`dispatchDone` 派发 `laf-canvas-wf-done`）；
 *    · P19 —— 终态表的执行口（`done` 后占位清除这条尾巴）。
 *
 *  运行前提：
 *    1) backend 起在 127.0.0.1:8010（start-dev.bat）
 *    2) frontend dev 起在 127.0.0.1:5173
 *  运行：
 *    cd frontend
 *    npx playwright test --config playwright.e2e-manual.config.ts --reporter=list \
 *      --output=../_tmp/e2e-canvas-workflow-r1
 *  （`--output` 覆盖 config 的 outputDir：避免与 doc-illustration 共用目录累积超阈值，
 *    沙箱 safe-delete 守卫会因此让进程非 0 退出、一条用例都不跑。）
 */
import { expect, test, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync, rmSync, rmdirSync } from "node:fs";
import { join, resolve } from "node:path";

/** 定位仓库根：兼容「在 frontend/ 下运行」（惯例）与「在仓库根运行」两种 cwd。
 *  （不用 import.meta.url —— Playwright 在本项目默认转译为 CJS，import.meta 不可用。） */
function findRepoRoot(): string {
  const cwd = process.cwd();
  for (const candidate of [cwd, resolve(cwd, "..")]) {
    if (existsSync(join(candidate, "backend", "data", "user_state.json"))) return candidate;
  }
  return resolve(cwd, "..");
}

/** 读设置里的「仓库文件夹」根（`settings.outputDir`）。读不到返回 null（则跳过清理）。 */
function worksRoot(): string | null {
  try {
    const raw = readFileSync(join(REPO, "backend", "data", "user_state.json"), "utf8").replace(/^\uFEFF/, "");
    const out = (JSON.parse(raw) as { settings?: { outputDir?: unknown } })?.settings?.outputDir;
    return typeof out === "string" && out.trim() ? out : null;
  } catch {
    return null;
  }
}

/** ★ 自造自清（技能 `demiurge-frontend-e2e-verify` 的硬约定）：入口页拿 `repoId` 挂真实 `CanvasStageFlow`
 *  时，画布会调后端、后端按 `repoId` 在**用户真实作品根**下建出 `e2e-canvas-repo/`（实测落 `_repo.json` +
 *  `canvas.json`）⇒ 不清就是往用户作品库里塞垃圾。只删**本用例自己造的固定文件名**，
 *  且 `rmdirSync` 非空即抛 —— 目录里只要还有别的东西就**保留**，绝不递归删。 */
test.afterAll(() => {
  const root = worksRoot();
  if (!root) return;
  const dir = join(root, E2E_REPO_ID);
  for (const name of ["canvas.json", "_repo.json"]) {
    try { rmSync(join(dir, name), { force: true }); } catch { /* ignore */ }
  }
  try { rmdirSync(dir); } catch { /* 非空（含用户数据）⇒ 保留 */ }
});

const REPO = findRepoRoot();
const SHOT_DIR = join(REPO, "_tmp", "e2e-canvas-workflow");
const BACKEND_HEALTH = "http://127.0.0.1:8010/api/health";

/** 造 fixture 用的临时 repoId：决定 threadKey 与 `laf_pending_gen_<threadKey>` 存储键。
 *  本页只用它做投影命名空间，不在真实作品库里落任何文件。 */
const E2E_REPO_ID = "e2e-canvas-repo";

/** 提交后随 `laf-canvas-wf-run-graph` 带入画布的 API prompt 图（真实运转就是长这样）。 */
const GRAPH = {
  "3": { class_type: "KSampler", inputs: { positive: ["6", 0], seed: 12345 } },
  "6": { class_type: "CLIPTextEncode", inputs: { text: "e2e 占位提示词" } },
};
const GRAPH_PROMPT = "e2e 占位提示词";
const SAMPLER_NODE_LABEL = "KSampler (#3)";

/** 节点卡文案（`CardNodeComponent` 的运转态）：`生成中…` + 进度百分比 + 当前节点名。 */
const GENERATING_TEXT = "生成中";
const PROGRESS_SELECTOR = ".wf-progress-txt";

type EventDetail = Record<string, unknown>;

/** 在页面里派发画布监听的运转事件（数组里可含多个事件，同一拍内连续派发）。 */
async function dispatchWf(page: Page, events: Array<[string, EventDetail]>): Promise<void> {
  await page.evaluate((items) => {
    for (const [name, detail] of items) {
      window.dispatchEvent(new CustomEvent(name, { detail }));
    }
  }, events);
}

/** 读节点在画布上的 transform（ReactFlow 用 `translate(x, y)` 定位）。 */
async function nodeTransform(page: Page, nodeId: string): Promise<string> {
  return page
    .locator(`.react-flow__node[data-id="${nodeId}"]`)
    .evaluate((el) => (el as HTMLElement).style.transform);
}

test.beforeAll(() => {
  mkdirSync(SHOT_DIR, { recursive: true });
});

test("画布运转占位：run 出现 → graph/progress 更新 → done 后消失", async ({ page, request }) => {
  const health = await request.get(BACKEND_HEALTH).catch(() => null);
  test.skip(!health?.ok(), `后端 ${BACKEND_HEALTH} 未就绪 —— 先跑 start-dev.bat（画布要真实后端投影）`);

  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`${m.text()} @ ${m.location().url}`);
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`/e2e-manual/canvas-workflow.html?repoId=${E2E_REPO_ID}`);
  // 画布就绪：ReactFlow 容器挂上（此前是「加载生成记录…」）
  await expect(page.locator(".react-flow")).toBeVisible({ timeout: 30_000 });

  const taskId = "e2e-wf-task-1";
  const node = page.locator(`.react-flow__node[data-id="${taskId}"]`);

  // ① 运转开始（`laf-canvas-wf-run`）→ 画布出现「生成中」占位节点
  await dispatchWf(page, [
    ["laf-canvas-wf-run", { runId: taskId, toolNodeId: "wftool-e2e", templateName: "e2e-模板" }],
  ]);
  await expect(node).toBeVisible();
  await expect(node).toContainText(GENERATING_TEXT);
  await expect(node).toContainText("e2e-模板");   // graph 未到时先显示模板名
  await page.screenshot({ path: join(SHOT_DIR, "01-placeholder.png") });

  // ② `laf-canvas-wf-run-graph` 带入 API prompt 图 → 占位改显示**真实提示词**
  //    （`workflowGenMetadata` 沿 sampler.positive 上溯到 CLIPTextEncode.text）
  await dispatchWf(page, [["laf-canvas-wf-run-graph", { runId: taskId, graph: GRAPH }]]);
  await expect(node).toContainText(GRAPH_PROMPT);

  // ③ `laf-canvas-wf-progress` → 进度条与当前节点名（class_type (#id)）
  await dispatchWf(page, [
    ["laf-canvas-wf-progress", {
      taskId, promptId: "pid-e2e-1", progress: 42, node: "3", templateName: "e2e-模板",
    }],
  ]);
  await expect(node.locator(PROGRESS_SELECTOR)).toHaveText("42%");
  await expect(node.locator(`[title="${SAMPLER_NODE_LABEL}"]`)).toBeVisible();
  await page.screenshot({ path: join(SHOT_DIR, "02-progress.png") });

  // ④ 进度推进（覆盖 `updateWfRun` 分支；不带 `node` 时沿用上一次的节点名）
  await dispatchWf(page, [["laf-canvas-wf-progress", { taskId, progress: 77 }]]);
  await expect(node.locator(PROGRESS_SELECTOR)).toHaveText("77%");
  await expect(node.locator(`[title="${SAMPLER_NODE_LABEL}"]`)).toBeVisible();

  // ⑤ 终态 `done` → 占位清除（P19 执行口派发的同一事件）
  await dispatchWf(page, [["laf-canvas-wf-done", { taskId }]]);
  await expect(node).toHaveCount(0);
  await page.screenshot({ path: join(SHOT_DIR, "03-done-cleared.png") });

  const realErrors = errors.filter((e) => !e.includes("favicon"));
  expect(realErrors, `控制台报错：${realErrors.join(" | ")}`).toHaveLength(0);
});

test("progress-only（编辑器弹窗路径不发 run）也会创建占位 —— upsert 语义", async ({ page, request }) => {
  const health = await request.get(BACKEND_HEALTH).catch(() => null);
  test.skip(!health?.ok(), `后端 ${BACKEND_HEALTH} 未就绪`);

  await page.goto(`/e2e-manual/canvas-workflow.html?repoId=${E2E_REPO_ID}`);
  await expect(page.locator(".react-flow")).toBeVisible({ timeout: 30_000 });

  const taskId = "e2e-wf-task-2";
  const node = page.locator(`.react-flow__node[data-id="${taskId}"]`);
  await expect(node).toHaveCount(0);

  // 只发 progress：真实场景里 `WorkflowToolModal` 走的就是这条（不发 run）
  await dispatchWf(page, [
    ["laf-canvas-wf-progress", { taskId, promptId: "pid-e2e-2", progress: null, templateName: "弹窗模板" }],
  ]);
  await expect(node).toBeVisible();
  await expect(node).toContainText(GENERATING_TEXT);
  await expect(node).toContainText("弹窗模板");
  // progress 为 null ⇒ 不渲染进度条（与「运转中…」的弹窗侧不同，画布占位只在有真进度时显示）
  await expect(node.locator(PROGRESS_SELECTOR)).toHaveCount(0);
  await page.screenshot({ path: join(SHOT_DIR, "04-progress-only.png") });

  await dispatchWf(page, [["laf-canvas-wf-done", { taskId }]]);
  await expect(node).toHaveCount(0);
});

test("同一拍内连续两次 progress：两个占位各落一个空位（落点必须在事务内用 prev 算）", async ({
  page, request,
}) => {
  const health = await request.get(BACKEND_HEALTH).catch(() => null);
  test.skip(!health?.ok(), `后端 ${BACKEND_HEALTH} 未就绪`);

  await page.goto(`/e2e-manual/canvas-workflow.html?repoId=${E2E_REPO_ID}`);
  await expect(page.locator(".react-flow")).toBeVisible({ timeout: 30_000 });

  const a = "e2e-wf-race-a";
  const b = "e2e-wf-race-b";
  // ★ 关键：两次派发在**同一个 evaluate 调用**里（同一拍），React 尚未重渲染。
  //   若落点改从 `wfRunsRef.current` 取占用矩形，两次都会看到「同一批旧占位」⇒ 算到同一个空位。
  //   对照实测：`placeNewNodes` 无锚点时首个占位落 (24,24)，第二个因 occupied 冲突右移一格。
  await dispatchWf(page, [
    ["laf-canvas-wf-progress", { taskId: a, progress: null, templateName: "t" }],
    ["laf-canvas-wf-progress", { taskId: b, progress: null, templateName: "t" }],
  ]);

  await expect(page.locator(`.react-flow__node[data-id="${a}"]`)).toBeVisible();
  await expect(page.locator(`.react-flow__node[data-id="${b}"]`)).toBeVisible();

  const ta = await nodeTransform(page, a);
  const tb = await nodeTransform(page, b);
  expect(ta, `两个占位落到了同一格（占位重叠）：${ta}`).not.toBe(tb);
  expect(ta, "占位节点应带 ReactFlow 的 translate 定位").toContain("translate");
  expect(tb, "占位节点应带 ReactFlow 的 translate 定位").toContain("translate");
  await page.screenshot({ path: join(SHOT_DIR, "05-two-same-tick.png") });

  // 清场：同一拍连发的两个占位都要能被 done 清掉
  await dispatchWf(page, [
    ["laf-canvas-wf-done", { taskId: a }],
    ["laf-canvas-wf-done", { taskId: b }],
  ]);
  await expect(page.locator(`.react-flow__node[data-id="${a}"]`)).toHaveCount(0);
  await expect(page.locator(`.react-flow__node[data-id="${b}"]`)).toHaveCount(0);
});
