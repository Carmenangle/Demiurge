// lib/renderMarkdown.ts — AI 正文 Markdown 渲染（对话消息 + 画布剧情节点 + 产物文档预览共用）
// marked 把 Markdown 转 HTML 且原样透传已有 HTML（正则产出的 <details>/<status> 等），再统一消毒。
// breaks:true → 单换行也成 <br>（扮演正文的分行有语义，对齐旧 pre-wrap 观感）。
import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ breaks: true, gfm: true });

/** 新窗口链接统一附带的反向导航防护（见 hardenAnchors）。 */
const ANCHOR_REL = "noopener noreferrer";

/**
 * 外链加固（2026-09-10 C3，DOMPurify 消毒后钩子）：给新窗口链接补上 `rel`。
 *
 * 为什么需要：正文既可能由模型生成，也可能来自**外部网页素材**（固化04「整理外部资料
 * 成文档」），而 `target` 是放行属性（ADD_ATTR）。带 `target="_blank"` 却不带 `rel` 时，
 * 新开的页面能通过 `window.opener` 反向导航应用页（reverse tabnabbing）。
 *
 * 顺带把 http(s) 外链一律改成新窗口打开：外部页面连「替换掉当前应用标签页」都做不到，
 * 也与对话区内联链接的既有约定一致（ChatMessages 的外链同样是 target=_blank + rel）。
 * 站内相对链接/锚点保持原样（不夺走同页跳转语义）；作者自带 `target` 的也补 `rel`。
 */
export function hardenAnchors(node: Element): void {
  if (!node || node.nodeType !== 1 || String(node.tagName).toUpperCase() !== "A") return;
  const href = node.getAttribute("href") || "";
  if (/^https?:\/\//i.test(href)) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", ANCHOR_REL);
    return;
  }
  if (node.hasAttribute("target")) node.setAttribute("rel", ANCHOR_REL);
}

// 钩子只在模块加载时装一次（放进 renderMarkdown 里会每次调用重复注册）。
// typeof 兜底：单测会把 dompurify mock 成 `{ sanitize }` 桩，无 DOM 环境下 DOMPurify
// 也是一提前返回的降级实例——两者都没有 addHook，缺了这层守卫会直接抛在 import 期。
if (typeof DOMPurify.addHook === "function") {
  DOMPurify.addHook("afterSanitizeAttributes", hardenAnchors);
}

export function renderMarkdown(text: string): string {
  // 允许内联 style（卡的状态栏全靠它）；禁脚本/事件/iframe 等由 DOMPurify 默认拦。
  const html = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ["style", "target"],
    FORBID_TAGS: ["script", "style", "iframe", "form", "input", "button"],
    FORBID_ATTR: ["onerror", "onload", "onclick"],
  });
}
