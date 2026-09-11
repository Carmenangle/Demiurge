// 执行过程面板解析（2026-09-06 重构：思考与工具调用分离，参照双面板可视化）。
//
// 行模式（后端 trace/sink 生成）：
//   🤔 模型思考中… / 🤔 模型正在思考…    = 思考开始（detail=模型 thinking 文本）
//   🔧 调用工具：<op>                    = 工具调用（detail=参数摘要）
//   ✅/❌ 工具完成/失败：<op>             = 工具结果（detail=结果摘要，回填到对应调用）
//   ✅ 模型响应完成 / 📐 解析模型输出…    = 纯状态噪声，面板不展示
//   其它（🧭 分派 / 🏁 结束 / 🔄 重试 / ❌ 模型失败 / ▶ 步骤…）= 平铺行
//
// 汇总结构（执行过程面板顶层渲染）：
//   plains —— 分派/结束/重试/失败等关键节点行，按序平铺
//   thinks —— 思考条目列表（chip「思考 ×N」展开逐条查看 thinking 文本）
//   calls  —— 工具调用列表（chip「工具 ×N」展开逐条查看参数/结果/状态）

/** agentTrace 行（2026-09-06 起可带 detail） */
export interface TraceLine {
  text: string;
  detail?: string;
}

/** 一次模型思考（detail=thinking 文本；空串=后端未下发内容） */
export interface TraceThinkEntry {
  text: string;
}

/** 一次工具调用；ok: true=成功 / false=失败 / undefined=运行中 */
export interface TraceCallEntry {
  op: string;
  args?: string;
  result?: string;
  ok?: boolean;
}

export interface TraceSummary {
  plains: TraceLine[];
  thinks: TraceThinkEntry[];
  calls: TraceCallEntry[];
}

const THINK_RE = /^🤔/;
const CALL_RE = /^🔧\s*调用工具：(.+)$/;
const CALL_RESULT_RE = /^[✅❌]\s*工具(?:完成|失败)：(.+)$/;
const NOISE_RES = [/^✅\s*模型响应完成$/, /^📐\s*解析模型输出/];

/** 解析 agentTrace 行序列为思考/工具分离的汇总。空输入返回空汇总。
 *  兼容 string[] 与 {text, detail}[]。 */
export function buildTraceSummary(lines: (string | TraceLine)[]): TraceSummary {
  const summary: TraceSummary = { plains: [], thinks: [], calls: [] };
  for (const raw of lines || []) {
    const line = typeof raw === "string" ? raw : raw.text;
    const detail = typeof raw === "string" ? undefined : raw.detail;
    if (THINK_RE.test(line)) {
      // 2026-09-07 用户反馈：空思考（后端未下发 thinking 文本）不占行——
      // 不 push 空 text，计数与展开列表都不出现，避免「（本轮无思考内容记录）」占行
      const thinkText = (detail || "").trim();
      if (thinkText) {
        summary.thinks.push({ text: thinkText });
      }
      continue;
    }
    const call = CALL_RE.exec(line);
    if (call) {
      summary.calls.push({ op: call[1].trim(), ...(detail ? { args: detail } : {}) });
      continue;
    }
    const result = CALL_RESULT_RE.exec(line);
    if (result) {
      // 结果回填同 op 最近一次未完结的调用；找不到（截断/乱序）则按平铺行保留
      const op = result[1].trim();
      const target = [...summary.calls].reverse().find((c) => c.op === op && c.ok === undefined);
      if (target) {
        target.ok = line.startsWith("✅");
        if (detail) target.result = detail;
      } else {
        summary.plains.push({ text: line, ...(detail ? { detail } : {}) });
      }
      continue;
    }
    if (NOISE_RES.some((re) => re.test(line))) continue;
    summary.plains.push({ text: line, ...(detail ? { detail } : {}) });
  }
  return summary;
}
