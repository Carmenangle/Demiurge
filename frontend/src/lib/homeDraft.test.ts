// homeDraft 的特征网（T2.2-B3-a · 2026-09-17）
//
// 背景：`homeDraft` 原是 `useChatSession.ts` 的模块级 `let`，被**四个消费方**共享
// （加载链读、持久化 effect 写、`ConversationHistoryRuntime.persist` 回调写、`clearHome` 清）。
// 加载链已于 T2.2-B3-b 外移到 `lib/useSessionLoad.ts` ⇒ 状态必须先脱离视图模块，否则视图与 hook 各持一份。
//
// 两道网：
//   ① 行为网 —— 单例的「写 → 读 → 清」闭环，且**读的是调用时的值**（不是模块加载期快照）。
//      这一条是「函数访问器 vs 裸 `let`」的关键差异：草图在事件回调里被写、不一定伴随重渲染。
//   ② 形态锁 —— 视图里不得再出现裸 `homeDraft` 的读/写形态，且四个消费点都走访问器；
//      T2.2-B3-b 后**读**访问器的持有者是 `useSessionLoad.ts`（视图 0 处、新属主恰 2 处）。
//      ⚠ 不能用「子串计数 == 0」当判据：**模块路径 `"./homeDraft"` 本身**就含小写 `homeDraft`。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import { clearHomeDraft, readHomeDraft, writeHomeDraft } from "./homeDraft";

const msg = (id: string, text = ""): ChatMessage =>
  ({ id, role: "assistant", text } as ChatMessage);

beforeEach(() => {
  clearHomeDraft();   // ⚠ 模块级状态跨用例泄漏 ⇒ 每个用例前显式清空
});

describe("homeDraft 单例：写 → 读 → 清", () => {
  it("初始为空数组", () => {
    expect(readHomeDraft()).toEqual([]);
  });

  it("写后读回**同一个引用**（不是拷贝）—— 与原 `setMessages(homeDraft)` 语义一致", () => {
    const draft = [msg("m1"), msg("m2")];
    writeHomeDraft(draft);
    expect(readHomeDraft()).toBe(draft);
  });

  it("整体覆盖（不合并）：第二次写替换第一次", () => {
    writeHomeDraft([msg("m1")]);
    writeHomeDraft([msg("m2")]);
    expect(readHomeDraft().map((m) => m.id)).toEqual(["m2"]);
  });

  it("清空后为空，且不影响已交出去的旧引用（旧数组仍是旧内容）", () => {
    const draft = [msg("m1")];
    writeHomeDraft(draft);
    clearHomeDraft();
    expect(readHomeDraft()).toEqual([]);
    expect(draft.map((m) => m.id)).toEqual(["m1"]);   // 清空是换引用，不是原地清数组
  });

  it("读的是**调用时**的值：写之后再读能拿到新值（不是模块加载期快照）", () => {
    const before = readHomeDraft();
    writeHomeDraft([msg("late")]);
    const after = readHomeDraft();
    expect(after).not.toBe(before);
    expect(after.map((m) => m.id)).toEqual(["late"]);
  });
});

describe("源码形态锁：草稿读写已全部改走访问器", () => {
  const VIEW = "./useChatSession.ts";
  const read = () => readFileSync(new URL(VIEW, import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const stripComments = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, "").split("\n")
      .map((l) => l.replace(/(?<!:)\/\/.*$/, "")).join("\n");
  const count = (hay: string, needle: string): number => {
    let n = 0;
    for (let i = hay.indexOf(needle); i !== -1; i = hay.indexOf(needle, i + needle.length)) n += 1;
    return n;
  };

  it("视图代码里不再有裸 `homeDraft` 的声明/读/写形态", () => {
    const code = stripComments(read());
    for (const shape of ["let homeDraft", "homeDraft =", "homeDraft.", "homeDraft[", "homeDraft)",
      "homeDraft;", "homeDraft,"]) {
      expect(code, shape).not.toContain(shape);
    }
    // `"./homeDraft"` 模块路径是**唯一**允许出现的形态
    expect(code).toContain('from "./homeDraft"');
  });

  it("四个消费点都走访问器；view 只 import 访问器、不自持状态", () => {
    const src = read();
    const code = stripComments(src);
    expect(count(code, "readHomeDraft(")).toBe(0);     // ① length 判空 ② 取值上交 —— 随加载链迁入 useSessionLoad
    expect(count(code, "writeHomeDraft(")).toBe(2);    // persist 回调 / 持久化 effect
    expect(count(code, "clearHomeDraft(")).toBe(1);    // clearHome
    expect(count(src, 'import { clearHomeDraft, writeHomeDraft } from "./homeDraft";')).toBe(1);
    // 状态本体必须只在新属主里声明
    expect(code).not.toMatch(/let\s+homeDraft/);
  });

  it("T2.2-B3-b：读访问器随加载链迁入 useSessionLoad，视图不再持有；新属主恰两处", () => {
    const SEAM = "./useSessionLoad.ts";
    const seamSrc = readFileSync(new URL(SEAM, import.meta.url), "utf8").replace(/\r\n/g, "\n");
    const seamCode = stripComments(seamSrc);
    expect(count(seamCode, "readHomeDraft(")).toBe(2);   // ① length 判空 ② 取值上交
    expect(count(seamSrc, 'import { readHomeDraft } from "./homeDraft";')).toBe(1);
    // 新属主只读、不写不清（写/清仍是视图的四个消费点）
    expect(count(seamCode, "writeHomeDraft(")).toBe(0);
    expect(count(seamCode, "clearHomeDraft(")).toBe(0);
  });
});
