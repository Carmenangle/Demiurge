import {
  cleanText, convertChinese, countText, escapeText, insertBetweenCharacters, joinText, unescapeText,
  type EscapeFormat,
} from "./textTools";

export type QuickTextTool = "clean" | "join" | "insert" | "stats" | "escape" | "convert";

export interface QuickTextToolOptions {
  cleanMarkdown: boolean;
  cleanBlankLines: boolean;
  separator: string;
  skipBlankLines: boolean;
  addition: string;
  escapeDirection: "encode" | "decode";
  escapeFormat: EscapeFormat;
  convertDirection: "to-simplified" | "to-traditional";
  replaceQuotes: boolean;
}

export const DEFAULT_QUICK_TEXT_OPTIONS: QuickTextToolOptions = {
  cleanMarkdown: true,
  cleanBlankLines: true,
  separator: "\\n",
  skipBlankLines: true,
  addition: "\u200b",
  escapeDirection: "encode",
  escapeFormat: "python",
  convertDirection: "to-simplified",
  replaceQuotes: false,
};

export function runQuickTextTool(
  tool: QuickTextTool,
  input: string,
  options: QuickTextToolOptions,
): string {
  if (tool === "clean") return cleanText(input, options.cleanMarkdown, options.cleanBlankLines);
  if (tool === "join") {
    const separator = options.separator.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
    return joinText(input, separator, options.skipBlankLines);
  }
  if (tool === "insert") return insertBetweenCharacters(input, options.addition);
  if (tool === "escape") {
    return options.escapeDirection === "encode"
      ? escapeText(input, options.escapeFormat)
      : unescapeText(input);
  }
  if (tool === "convert") {
    return convertChinese(input, options.convertDirection, options.replaceQuotes);
  }
  const stats = countText(input);
  return [
    `汉字 / 日文：${stats.cjk}`,
    `英文单词：${stats.englishWords}`,
    `标点符号：${stats.punctuation}`,
    `字符总数：${stats.characters}`,
    `行数：${stats.lines}`,
  ].join("\n");
}
