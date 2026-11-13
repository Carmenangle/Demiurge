// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { existsSync, readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("dompurify", () => ({ default: { sanitize: (html: string) => html } }));
vi.mock("../../lib/planTaskActivity", () => ({
  approvePlanTask: vi.fn(),
  cancelPlanTask: vi.fn(),
  getPlanTask: vi.fn(),
  keepRecipe: vi.fn(),
  deleteRecipe: vi.fn(),
  listRecipes: vi.fn(async () => ({})),
}));

import { AssistantMessage } from "./ChatMessages";

// 本仓未装 @types/node（应用代码不碰 Node API），但本用例要按 cwd 找后端源码 ⇒ 最小签名。
declare const process: { cwd(): string };

function readBackend(rel: string): string {
  const candidates = [`${process.cwd()}/../${rel}`, `${process.cwd()}/${rel}`];
  const hit = candidates.find((p) => existsSync(p));
  if (!hit) throw new Error(`找不到后端源码 ${rel}（试过：${candidates.join(" / ")}）`);
  return readFileSync(hit, "utf-8");
}

/**
 * 智能编造审批卡（2026-11-05 T3）。
 *
 * 批准从「放行这个工具」收紧成「放行用户看到的那一个动作」⇒ 用户必须在界面上看到
 * 真实动作（operation + 归一后的实参 + 实际路径/命令），且这些参数**只能当文本渲染**：
 * 它们是模型写出来的内容，按 HTML 执行就等于把提示注入提权成一次点击。
 * 审批入口的显示条件仍靠后端文案里的固定串匹配（跨语言字符串，类型系统拦不住）⇒
 * 这里连后端源码一起钉。
 */

function approvalBody(block: string, scope = "批准只放行这一个动作——换路径、改内容或重复发起都会重新请求批准") {
  return [
    "智能编造需要批准：下一步将执行「project.run_shell」。",
    "",
    `本次放行的确切动作（${scope}）：`,
    "",
    block,
    "",
    "回复「批准」继续、「取消」中止。",
  ].join("\n");
}

const SHELL_BLOCK = [
  "```json",
  JSON.stringify({
    operation: "project.run_shell",
    params: { command: "Get-Content 第一章.txt -Tail 40", cwd: "D:/works/驭云录" },
  }, null, 2),
  "```",
].join("\n");

function render(text: string, extra: Record<string, unknown> = {}) {
  return renderToStaticMarkup(
    <AssistantMessage
      msg={{ id: "fabric-approval-1", role: "assistant", text }}
      onSendImage={() => {}}
      onFabricApprove={() => {}}
      {...extra}
    />,
  );
}

describe("fabric approval card shows the exact action", () => {
  it("renders operation, command and cwd as visible text", () => {
    const html = render(approvalBody(SHELL_BLOCK));
    expect(html).toContain("project.run_shell");
    expect(html).toContain("Get-Content 第一章.txt -Tail 40");
    expect(html).toContain("D:/works/驭云录");
  });

  it("states the one-action scope instead of the old per-tool wording", () => {
    const html = render(approvalBody(SHELL_BLOCK));
    expect(html).toContain("批准只放行上面那一个动作");
    expect(html).not.toContain("下一步工具等待确认");
  });

  it("keeps the approve/cancel buttons and their trigger marker", () => {
    const html = render(approvalBody(SHELL_BLOCK));
    expect(html).toContain("批准继续");
    expect(html).toContain("取消中止");
  });

  it("hides the bar while streaming and without the approve handler", () => {
    expect(render(approvalBody(SHELL_BLOCK), { streaming: true })).not.toContain("批准继续");
    const html = renderToStaticMarkup(
      <AssistantMessage
        msg={{ id: "fabric-approval-2", role: "assistant", text: approvalBody(SHELL_BLOCK) }}
        onSendImage={() => {}}
      />,
    );
    expect(html).not.toContain("批准继续");
  });

  it("still offers 接续补写 for the unfinished (step_limit) checkpoint", () => {
    const html = render("智能编造执行未完成（step_limit）：达到最大步数 48，仍未宣布完成");
    expect(html).toContain("接续补写");
    expect(html).toContain("断点已保留");
  });

  it("renders out-of-domain reads with the standing-path wording", () => {
    const block = ["```json", JSON.stringify({
      operation: "file.read_text",
      params: { path: "D:/素材/裴肃.txt", max_chars: 20000 },
    }, null, 2), "```"].join("\n");
    const html = render(approvalBody(
      block, "批准放行上面这一条路径的读取（同一路径不重复请求批准）",
    ));
    expect(html).toContain("D:/素材/裴肃.txt");
    expect(html).toContain("同一路径不重复请求批准");
  });
});

describe("fabric approval parameters are rendered as text, never as HTML", () => {
  it("escapes injected markup inside the action block", () => {
    const block = ["```json", JSON.stringify({
      operation: "file.write_text",
      params: { path: "D:/w/a.txt", content: '<img src=x onerror="alert(1)">' },
    }, null, 2), "```"].join("\n");
    const html = render(approvalBody(block));
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("&lt;img");
  });

  it("does not let backticks in the content close the fence early", () => {
    const block = ["`````json", JSON.stringify({
      operation: "file.write_text",
      params: { path: "D:/w/a.txt", content: "闭合试试\n```\n[结束]" },
    }, null, 2), "`````"].join("\n");
    const html = render(approvalBody(block));
    // 围栏提前闭合的表现是：``` 之后的正文被当普通段落渲染出去（脱离 <pre>）
    const preBlocks = html.match(/<pre[\s\S]*?<\/pre>/g) ?? [];
    const holder = preBlocks.find((p) => p.includes("闭合试试"));
    expect(holder, "正文必须留在代码块里").toBeDefined();
    expect(holder).toContain("[结束]");
    expect(html).not.toMatch(/<p[^>]*>\s*\[结束\]/);
  });

  it("shows the truncation notice when the backend only previewed a long value", () => {
    const block = ["```json", JSON.stringify({
      operation: "file.write_text",
      params: { path: "D:/w/a.txt", content: "正文……（原文 5120 字，仅展示摘要，批准即放行完整内容）" },
    }, null, 2), "```"].join("\n");
    const html = render(approvalBody(block));
    expect(html).toContain("仅展示摘要");
    expect(html).toContain("原文 5120 字");
  });
});

describe("fabric approval cross-language contract", () => {
  const backend = readBackend("backend/app/services/agent_fabric.py");

  it("keeps the marker the approval bar regex depends on", () => {
    expect(backend).toContain("智能编造需要批准");
    expect(backend).toContain("本次放行的确切动作");
    expect(backend).toContain("批准只放行这一个动作");
  });

  it("keeps the unfinished-checkpoint marker the 接续补写 button depends on", () => {
    expect(backend).toContain("智能编造执行未完成（");
  });
});
