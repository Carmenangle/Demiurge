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

// 把 /body/flags 或裸 body 编译成 RegExp（全局替换需 g）。失败返回 null（对标 regexFromString 容错）。
function compile(pattern: string): RegExp | null {
  if (!pattern) return null;
  let body = pattern;
  let flags = "";
  if (pattern.length >= 2 && pattern.startsWith("/")) {
    const last = pattern.lastIndexOf("/");
    if (last > 0) {
      body = pattern.slice(1, last);
      flags = pattern.slice(last + 1);
    }
  }
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
  const o = { isMarkdown: false, isPrompt: false, isEdit: false, ...opts };
  let out = text;
  for (const s of scripts) {
    if (!applies(s, o)) continue;
    if (s.placement && s.placement.length > 0 && !s.placement.includes(placement)) continue;
    out = runScript(s, out);
  }
  return out;
}
