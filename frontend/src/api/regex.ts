import { apiGet, apiPost } from "./client";
import type { RegexScript } from "../lib/regexEngine";

export function listRegex() {
  return apiGet<{ items: RegexScript[] }>("/regex/");
}

export function saveRegex(scripts: RegexScript[]) {
  return apiPost<{ items: RegexScript[] }>("/regex/save", { scripts });
}

export function testRegex(payload: {
  script: RegexScript; text: string; placement?: number;
  isMarkdown?: boolean; isPrompt?: boolean; depth?: number | null;
}) {
  return apiPost<{ result: string; changed: boolean }>("/regex/test", {
    script: payload.script,
    text: payload.text,
    placement: payload.placement ?? 2,
    is_markdown: payload.isMarkdown ?? false,
    is_prompt: payload.isPrompt ?? false,
    depth: payload.depth ?? null,
  });
}
