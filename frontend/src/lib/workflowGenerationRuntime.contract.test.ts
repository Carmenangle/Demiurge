// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * `workflowGenerationRuntime` 的**导出面契约锁**（2026-09-15 架构审计 **P14 · F8**）。
 *
 * 背景：六个纯判定函数 —— `registerPending` / `unregisterPending` / `pendingResumeAction` /
 * `generationResultAction` / `notFoundPollAction` / `shouldFinalize` —— 在模块内**都有生产调用**
 * （`track` / `remove` / `recoveryAction` / `inspect` / `advanceWorkflowTick` / `finalize`），
 * 但**对外零生产消费方**：唯一外部引用是 `lib/chatGeneration.test.ts`，而那是
 * `chatGeneration.ts` 的测试文件（这些函数早已搬来本模块、测试没跟着搬）⇒ **导出面 = 测试面**，
 * 「模块内实现细节」被当成公开 API 挂着。
 *
 * 处置：六个降为模块内可见。**行为面**改由 `workflowGenerationRuntime.test.ts` 的
 * 「P14 · 公开门面等价网」经公开门面（`track`/`remove`/`recoveryAction`/`inspect`）逐条覆盖；
 * **分层面**只能靠本文件钉住 —— 本轮审计已多次实证「**行为网抓不到分层回退**」
 * （P8 narrative delete 回内联、P9 `load_overrides` 回内联、P10 画布内联一行等价语句，均行为全绿）。
 *
 * ⚠ 锁的是**源码形态**，不是运行期行为：把某个函数加回 `export` 前缀，**不会让任何行为用例变红**。
 */
const SRC = readFileSync(new URL("./workflowGenerationRuntime.ts", import.meta.url), "utf-8");

/** 注释里会引用这些名字作背景说明，判定只看代码。 */
const strip = (src: string): string =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
const CODE = strip(SRC);

/** 已收窄为模块内可见的六个纯判定函数（P14）。 */
const INTERNAL_ONLY = [
  "registerPending",
  "unregisterPending",
  "pendingResumeAction",
  "generationResultAction",
  "notFoundPollAction",
  "shouldFinalize",
] as const;

/** 收窄时**不得顺手删实现**：这些是运行期入口，必须留在导出面上。 */
const PUBLIC_SURFACE = [
  "export function pollSchedule(",
  "export function advanceWorkflowTick(",
  "export function initialTickState(",
  "export async function pollWorkflowResult(",
  "export function durableFinalizeSucceeded(",
  "export function getRuntime(",
  "export class WorkflowGenerationRuntime",
] as const;

/** 递归收集 `frontend/src` 下的生产文件（排除 `*.test.ts(x)`）——「零生产消费方」要被持续证明。 */
function productionFiles(): Array<{ path: string; code: string }> {
  const found: Array<{ path: string; code: string }> = [];
  const walk = (dir: URL): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const child = new URL(entry.isDirectory() ? `${entry.name}/` : entry.name, dir);
      if (entry.isDirectory()) walk(child);
      else if (/\.tsx?$/.test(entry.name) && !/\.test\./.test(entry.name)) {
        found.push({ path: decodeURIComponent(child.pathname), code: strip(readFileSync(child, "utf-8")) });
      }
    }
  };
  walk(new URL("../", import.meta.url));
  return found;
}

describe("P14 · 导出面收窄：六个纯判定函数只允许模块内可见", () => {
  it("六个名字都不得再带 `export`（函数声明与常量赋值两种形态）", () => {
    for (const name of INTERNAL_ONLY) {
      expect(CODE, `${name} 仍是导出的函数声明（导出面回退）`)
        .not.toContain(`export function ${name}(`);
      expect(CODE, `${name} 仍是导出的常量（导出面回退）`)
        .not.toContain(`export const ${name} =`);
    }
    expect(CODE, "模块内出现了 barrel 式 `export {` —— 收窄会被它绕过").not.toContain("export {");
  });

  it("六个名字在模块内仍各有定义（收窄 ≠ 删实现）", () => {
    for (const name of INTERNAL_ONLY) {
      const defined = CODE.includes(`function ${name}(`) || CODE.includes(`const ${name} =`);
      expect(defined, `${name} 在模块内找不到定义`).toBe(true);
    }
  });

  it("六个名字在模块内都有生产调用（证伪「只被测试引用的死代码」）", () => {
    for (const name of INTERNAL_ONLY) {
      const occurrences = CODE.split(name).length - 1;   // 1 次定义 + ≥1 次调用
      expect(occurrences, `${name} 在模块内只有定义、没有生产调用`).toBeGreaterThan(1);
    }
  });

  it("公开门面仍在导出面上（防止「收窄」被误读成「一律不导出」）", () => {
    for (const surface of PUBLIC_SURFACE) {
      expect(CODE, `公开门面缺失：${surface}`).toContain(surface);
    }
  });

  it("全仓生产文件零引用这六个名字（源码锁——行为网抓不到这种分层回退）", () => {
    const offenders: string[] = [];
    for (const file of productionFiles()) {
      if (file.path.endsWith("workflowGenerationRuntime.ts")) continue;   // 属主自身
      for (const name of INTERNAL_ONLY) {
        if (file.code.includes(name)) offenders.push(`${file.path} → ${name}`);
      }
    }
    expect(offenders, `这些生产文件仍在引用已收窄的内部判定函数：\n${offenders.join("\n")}`)
      .toEqual([]);
  });
});
