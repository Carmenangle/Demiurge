import { describe, it, expect } from "vitest";
import { buildTraceSummary, type TraceLine } from "./traceTree";

describe("traceSummary 思考/工具分离解析", () => {
  it("思考行与工具调用分离收集，结果回填对应调用，噪声行不展示", () => {
    const lines: (string | TraceLine)[] = [
      "🧭 主管分派 → 智能编造计划",
      { text: "🤔 模型正在思考…", detail: "先拿候选名单，再切素材。" },
      "✅ 模型响应完成",
      "📐 解析模型输出…",
      { text: "🔧 调用工具：knowledge.load_doc", detail: '{"doc":"a.md"}' },
      { text: "✅ 工具完成：knowledge.load_doc", detail: '{"ok":true}' },
      { text: "🤔 模型正在思考…", detail: "再列目录。" },
      { text: "🔧 调用工具：file.list_dir", detail: '{"path":"backend"}' },
      "❌ 工具失败：file.list_dir",
    ];
    const s = buildTraceSummary(lines);
    expect(s.plains).toEqual([{ text: "🧭 主管分派 → 智能编造计划" }]);
    expect(s.thinks).toEqual([{ text: "先拿候选名单，再切素材。" }, { text: "再列目录。" }]);
    expect(s.calls).toEqual([
      { op: "knowledge.load_doc", args: '{"doc":"a.md"}', result: '{"ok":true}', ok: true },
      // 无结果 detail 只回填状态
      { op: "file.list_dir", args: '{"path":"backend"}', ok: false },
    ]);
  });

  it("未完结的调用保持运行中状态（ok undefined）", () => {
    const lines: (string | TraceLine)[] = [
      { text: "🤔 模型正在思考…", detail: "查目录" },
      { text: "🔧 调用工具：file.list_dir", detail: '{"path":"."}' },
    ];
    const s = buildTraceSummary(lines);
    expect(s.thinks).toEqual([{ text: "查目录" }]);
    expect(s.calls).toEqual([{ op: "file.list_dir", args: '{"path":"."}' }]);
  });

  it("同 op 多次调用按顺序逐条回填各自结果", () => {
    const lines: (string | TraceLine)[] = [
      { text: "🔧 调用工具：file.list_dir", detail: '{"path":"a"}' },
      { text: "✅ 工具完成：file.list_dir", detail: "r1" },
      { text: "🔧 调用工具：file.list_dir", detail: '{"path":"b"}' },
      { text: "✅ 工具完成：file.list_dir", detail: "r2" },
    ];
    const s = buildTraceSummary(lines);
    expect(s.calls).toEqual([
      { op: "file.list_dir", args: '{"path":"a"}', result: "r1", ok: true },
      { op: "file.list_dir", args: '{"path":"b"}', result: "r2", ok: true },
    ]);
  });

  it("找不到匹配调用的结果行回落为平铺行，不丢失", () => {
    const s = buildTraceSummary(["✅ 工具完成：ghost.op"]);
    expect(s.calls).toEqual([]);
    expect(s.plains).toEqual([{ text: "✅ 工具完成：ghost.op" }]);
  });

  it("重试/失败/结束等关键行保留为平铺行", () => {
    const lines = [
      "🤔 模型思考中…",
      "🔄 第 1 次重试（超时）",
      "❌ 模型调用失败：额度不足",
      "🏁 计划结束：ok",
    ];
    const s = buildTraceSummary(lines);
    expect(s.thinks).toEqual([]); // 2026-09-07：空思考不占行（过滤）
    expect(s.plains.map((p) => p.text)).toEqual([
      "🔄 第 1 次重试（超时）",
      "❌ 模型调用失败：额度不足",
      "🏁 计划结束：ok",
    ]);
  });

  it("空输入返回空汇总", () => {
    expect(buildTraceSummary([])).toEqual({ plains: [], thinks: [], calls: [] });
  });
});
