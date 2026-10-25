// 前端正则引擎：显示层（markdownOnly）用，与后端 regex_engine.py 同逻辑、不同 runtime。
// 后端跑存储/发送档（USER_INPUT/AI_OUTPUT/WORLD_INFO/推理，改落库或改提示）；
// 这里跑显示档——把 AI 输出里的 <think> 灰魂吐槽、状态块等按脚本隐藏/压缩后再渲染。
// 对标 SillyTavern extensions/regex/engine.js（原生 JS 正则，方言天然一致）。

export const Placement = {
  MD_DISPLAY: 0,
  USER_INPUT: 1,
  AI_OUTPUT: 2,
  SLASH_COMMAND: 3,
  WORLD_INFO: 5,
  REASONING: 6,
  IMAGE_PROMPT: 7,
} as const;

// ST RegexScriptData（camelCase，与卡/全局库原样一致）
export interface RegexScript {
  id?: string;
  scriptName?: string;
  findRegex: string;
  replaceString?: string;
  trimStrings?: string[];
  placement?: number[];
  disabled?: boolean;
  markdownOnly?: boolean;
  promptOnly?: boolean;
  runOnEdit?: boolean;
  minDepth?: number | null;
  maxDepth?: number | null;
  substituteRegex?: number;  // 查找时的宏：0 不替换 / 1 原始 / 2 转义
}

// ReDoS 防护（2026-10-24 二轮审计，与后端 regex_engine.py 同逻辑双端合同——
// 常量值与判定必须两边同步改，各有测试锁值）：显示层跑在 UI 主线程，一条指数回溯
// 模式即可冻结整个界面。三道防线：pattern/输入/脚本数上限 + 嵌套无界量词形态拒编译。
// 已知局限（登记而非假装解决）：纯交替歧义（(a|aa)+）与多项式形态不在拦截之列。
export const MAX_PATTERN_CHARS = 2000;
export const MAX_INPUT_CHARS = 400_000;
export const MAX_SCRIPTS_PER_RUN = 64;

function isQuant(body: string, i: number): boolean {
  if (i >= body.length) return false;
  const c = body[i];
  if (c === "+" || c === "*" || c === "?") return true;
  if (c === "{") {
    const close = body.indexOf("}", i + 1);
    if (close !== -1 && close - i <= 32) {
      const inner = body.slice(i + 1, close);
      return /\d/.test(inner) && /^[0-9,]+$/.test(inner);
    }
  }
  return false;
}

function quantUnbounded(body: string, i: number): boolean {
  const c = body[i];
  if (c === "+" || c === "*") return true;
  if (c === "{") {
    const close = body.indexOf("}", i + 1);
    if (close !== -1) {
      const upper = body.slice(i + 1, close).split(",", 2);
      return upper.length === 2 && !/^\s*\d+\s*$/.test(upper[1]);
    }
  }
  return false; // ? 与 {n,m} 有界
}

function skipQuant(body: string, i: number): number {
  let j = i + 1;
  if (body[i] === "{") {
    const close = body.indexOf("}", i + 1);
    j = close === -1 ? body.length : close + 1;
  }
  if (j < body.length && body[j] === "?") j += 1;
  return j;
}

/** 检测 (a+)+ 家族：某层的组被无界量词修饰、且组内顶层已有无界量词化原子。
 *  有界外量词（(a+)?、(?:https?://)?）放行——真实卡里大量存在且非指数回溯。 */
export function hasNestedUnboundedQuant(body: string): boolean {
  const n = body.length;
  let i = 0;
  let depth = 0;
  const levelUnbounded: boolean[] = [false]; // 每层组：顶层是否出现「原子 + 无界量词」
  while (i < n) {
    const c = body[i];
    if (c === "\\") {
      i += 2;
    } else if (c === "[") {
      let j = i + 1;
      if (j < n && body[j] === "^") j += 1;
      if (j < n && body[j] === "]") j += 1;
      while (j < n && body[j] !== "]") j += body[j] === "\\" ? 2 : 1;
      i = j + 1;
    } else if (c === "(") {
      if (body.startsWith("(?#", i)) {
        const end = body.indexOf(")", i + 3);
        i = end === -1 ? n : end + 1;
        continue;
      }
      depth += 1;
      levelUnbounded.push(false);
      i += 1;
      continue;
    } else if (c === ")") {
      i += 1;
      if (depth > 0) {
        const inner = levelUnbounded.pop() as boolean;
        depth -= 1;
        if (isQuant(body, i)) {
          const unbounded = quantUnbounded(body, i);
          if (inner && unbounded) return true;
          if (unbounded) levelUnbounded[depth] = true;
          i = skipQuant(body, i);
        }
      }
      continue;
    } else {
      i += 1;
    }
    if (i < n && isQuant(body, i) && quantUnbounded(body, i)) {
      levelUnbounded[depth] = true;
      i = skipQuant(body, i);
    }
  }
  return false;
}

// 把 /body/flags 或裸 body 编译成 RegExp（全局替换需 g）。失败返回 null（对标 regexFromString 容错）。
function compile(pattern: string): RegExp | null {
  if (!pattern) return null;
  if (pattern.length > MAX_PATTERN_CHARS) return null;
  let body = pattern;
  let flags = "";
  if (pattern.length >= 2 && pattern.startsWith("/")) {
    const last = pattern.lastIndexOf("/");
    if (last > 0) {
      body = pattern.slice(1, last);
      flags = pattern.slice(last + 1);
    }
  }
  if (hasNestedUnboundedQuant(body)) return null;
  if (!flags.includes("g")) flags += "g"; // 默认全替，与 Python re.sub 一致
  try {
    return new RegExp(body, flags);
  } catch {
    return null;
  }
}

function filterMatch(matched: string, trimStrings?: string[]): string {
  let out = matched;
  for (const t of trimStrings || []) if (t) out = out.split(t).join("");
  return out;
}

function runScript(script: RegexScript, text: string): string {
  if (script.disabled || !script.findRegex || !text) return text;
  const re = compile(script.findRegex);
  if (!re) return text;
  return text.replace(re, (...args) => {
    // args: match, p1, p2, ..., offset, string, [groups]
    const groups = typeof args[args.length - 1] === "object" ? args[args.length - 1] : undefined;
    const repl = (script.replaceString || "").replace(/\{\{match\}\}/gi, "$0");
    return repl.replace(/\$(\d+)|\$<([^>]+)>/g, (_m, num, name) => {
      let val: string | undefined;
      if (num !== undefined) val = num === "0" ? String(args[0]) : (args[Number(num)] as string | undefined);
      else if (name && groups) val = groups[name];
      if (!val) return "";
      return filterMatch(val, script.trimStrings);
    });
  });
}

function applies(
  s: RegexScript,
  opts: { isMarkdown: boolean; isPrompt: boolean; isEdit: boolean; depth?: number },
): boolean {
  const md = !!s.markdownOnly;
  const pr = !!s.promptOnly;
  if (!((md && opts.isMarkdown) || (pr && opts.isPrompt) || (!md && !pr && !opts.isMarkdown && !opts.isPrompt)))
    return false;
  if (opts.isEdit && s.runOnEdit === false) return false;
  if (typeof opts.depth === "number") {
    if (s.minDepth != null && s.minDepth >= -1 && opts.depth < s.minDepth) return false;
    if (s.maxDepth != null && s.maxDepth >= 0 && opts.depth > s.maxDepth) return false;
  }
  return true;
}

// 对标 getRegexedString：按序跑命中 placement 且通过三档/depth 过滤的脚本。
export function runScripts(
  text: string,
  placement: number,
  scripts: RegexScript[],
  opts: { isMarkdown?: boolean; isPrompt?: boolean; isEdit?: boolean; depth?: number } = {},
): string {
  if (typeof text !== "string" || !text) return text;
  // ReDoS 上限（与后端同语义）：超长输入整轮跳过、脚本数截到前 N（保 ST 顺序优先级）。
  if (text.length > MAX_INPUT_CHARS) return text;
  const list = scripts.slice(0, MAX_SCRIPTS_PER_RUN);
  const o = { isMarkdown: false, isPrompt: false, isEdit: false, ...opts };
  let out = text;
  for (const s of list) {
    if (!applies(s, o)) continue;
    if (s.placement && s.placement.length > 0 && !s.placement.includes(placement)) continue;
    out = runScript(s, out);
  }
  return out;
}
