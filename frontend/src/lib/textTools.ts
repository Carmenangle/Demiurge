import * as OpenCC from "opencc-js";

export function cleanText(text: string, removeMarkdown: boolean, removeBlankLines: boolean): string {
  let result = text.replace(/\r\n?/g, "\n");
  if (removeMarkdown) {
    result = result
      .replace(/^\s*```[^\n]*\n?/gm, "")
      .replace(/^\s{0,3}(?:#{1,6}|>|[-+*]\s|\d+[.)]\s)\s*/gm, "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/(\*\*|__|~~)(.*?)\1/g, "$2")
      .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "$1")
      .replace(/(?<!_)_([^_\n]+)_(?!_)/g, "$1")
      .replace(/`([^`]+)`/g, "$1");
  }
  if (removeBlankLines) result = result.replace(/\n[\t ]*\n+/g, "\n");
  return result.trim();
}

export function joinText(text: string, separator: string, skipBlank = true): string {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  return (skipBlank ? lines.filter((line) => line.trim().length > 0) : lines).join(separator);
}

/** 分隔符输入 → 真实分隔符：\n / \t 转义解码；textarea 直接回车 / 粘贴得到的真实换行原样保留。 */
export function resolveSeparator(raw: string): string {
  return raw.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
}

export function insertBetweenCharacters(text: string, addition: string): string {
  return Array.from(text).join(addition);
}

export interface TextStats {
  cjk: number;
  englishWords: number;
  punctuation: number;
  characters: number;
  lines: number;
}

export function countText(text: string): TextStats {
  return {
    cjk: (text.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/gu) || []).length,
    englishWords: (text.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g) || []).length,
    punctuation: (text.match(/\p{P}/gu) || []).length,
    characters: Array.from(text).length,
    lines: text ? text.replace(/\r\n?/g, "\n").split("\n").length : 0,
  };
}

export type EscapeFormat = "python" | "hex" | "json";

export function escapeText(text: string, format: EscapeFormat, upperHex = false): string {
  if (format === "json") return JSON.stringify(text);
  const bytes = new TextEncoder().encode(text);
  let body = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (upperHex) body = body.toUpperCase();
  if (format === "hex") return body;
  const escaped = body.match(/.{2}/g)?.map((part) => `\\x${part}`).join("") || "";
  return `b'${escaped}'`;
}

export function unescapeText(text: string): string {
  const value = text.trim();
  if (!value) return "";
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    try { return JSON.parse(value); } catch { /* continue with byte parsing */ }
  }
  const body = value.replace(/^b(['"])/i, "").replace(/(['"])$/, "");
  const hexPairs = body.includes("\\x")
    ? Array.from(body.matchAll(/\\x([0-9a-f]{2})/gi), (match) => match[1])
    : (body.replace(/\s+/g, "").match(/[0-9a-f]{2}/gi) || []);
  if (!hexPairs.length) throw new Error("没有识别到 UTF-8 十六进制字节");
  return new TextDecoder("utf-8", { fatal: true }).decode(
    Uint8Array.from(hexPairs, (part) => Number.parseInt(part, 16)),
  );
}

const toSimplified = OpenCC.Converter({ from: "twp", to: "cn" });
const toTraditional = OpenCC.Converter({ from: "cn", to: "twp" });

export function convertChinese(text: string, direction: "to-simplified" | "to-traditional", replaceQuotes = false): string {
  let result = direction === "to-simplified" ? toSimplified(text) : toTraditional(text);
  if (replaceQuotes) result = result.replace(/「/g, "【").replace(/」/g, "】");
  return result;
}
