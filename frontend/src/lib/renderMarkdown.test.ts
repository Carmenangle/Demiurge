// @vitest-environment jsdom
// C3 外链加固：用**真实** dompurify + marked（jsdom DOM）验证钩子确实补了 rel/断掉 opener，
// 而不是只测一个自说自话的纯函数——jest 桩无法证明 addHook 装上了、也没法验证消毒后仍安全。
import { describe, expect, it } from "vitest";

import { hardenAnchors, renderMarkdown } from "./renderMarkdown";

describe("renderMarkdown 外链加固（C3）", () => {
  it("http(s) 外链自动新窗口打开并断开 opener", () => {
    const html = renderMarkdown("参考 [月见八千代](https://example.com/yachiyo?a=1) 的资料");
    expect(html).toContain('href="https://example.com/yachiyo?a=1"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("外部素材里自带的 target=_blank 也会被补上 rel", () => {
    const html = renderMarkdown('<a href="https://evil.test/x" target="_blank">素材引用</a>');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("http（非 https）外链同样加固", () => {
    const html = renderMarkdown("[图源](http://img.test/a.png)");
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain('target="_blank"');
  });

  it("站内相对链接保持同页跳转，不加 target/rel", () => {
    const html = renderMarkdown("[插图](assets/a.png)");
    expect(html).toContain('href="assets/a.png"');
    expect(html).not.toContain("target=");
    expect(html).not.toContain("rel=");
  });

  it("作者自带 target 的非外链也补 rel（断 opener 不看域名）", () => {
    const html = renderMarkdown('<a href="/local" target="_blank">站内新窗</a>');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("加固不放松既有消毒：脚本与事件属性仍被拦", () => {
    const html = renderMarkdown(
      '<a href="https://e.test" onerror="alert(1)">x</a><script>alert(1)</script>',
    );
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("hardenAnchors 只认 <a>，其它节点原样放过", () => {
    const span = document.createElement("span");
    span.setAttribute("href", "https://e.test");
    hardenAnchors(span);
    expect(span.hasAttribute("target")).toBe(false);
    const text = document.createTextNode("https://e.test") as unknown as Element;
    expect(() => hardenAnchors(text)).not.toThrow();
  });
});
