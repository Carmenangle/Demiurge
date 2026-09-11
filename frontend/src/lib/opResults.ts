// 工作流编排「写入结果」格式化，单点。
// 此前 WorkflowCard 与 workflowOrchestration 各有一份字节相同的拷贝。
export function fmtOpResults(rs: any[]): string {
  return rs
    .map((r) => `${r.ok ? "✓" : "✗"} #${r.node_id} ${r.input || ""}${r.ok ? "" : "（" + (r.msg || "失败") + "）"}`)
    .join("\n");
}
