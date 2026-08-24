// lib/renderMarkdown.ts — AI 正文 Markdown 渲染（对话消息 + 画布剧情节点共用）
// marked 把 Markdown 转 HTML 且原样透传已有 HTML（正则产出的 <details>/<status> 等），再统一消毒。
// breaks:true → 单换行也成 <br>（扮演正文的分行有语义，对齐旧 pre-wrap 观感）。
import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ breaks: true, gfm: true });

export function renderMarkdown(text: string): string {
  // 允许内联 style（卡的状态栏全靠它）；禁脚本/事件/iframe 等由 DOMPurify 默认拦。
  const html = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ["style", "target"],
    FORBID_TAGS: ["script", "style", "iframe", "form", "input", "button"],
    FORBID_ATTR: ["onerror", "onload", "onclick"],
  });
}
