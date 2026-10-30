import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StylePanel } from "./StylePanel";

const draft = (over: Record<string, unknown> = {}) => ({
  stylePresets: [],
  ...over,
}) as never;

describe("StylePanel（提示词模版）· 2026-10-25 反馈", () => {
  it("两个分组各带「新增模版」，不再有全局「管理模版」", () => {
    const html = renderToStaticMarkup(
      <StylePanel draft={draft()} setDraft={() => {}} />,
    );
    expect(html).toContain("提示词模版");
    expect(html).toContain("生图提示词模版");
    expect(html).toContain("视频提示词模版");
    // 每个分组标题右侧各一个「新增模版」按钮（正文提到一次不算）
    expect(html.match(/新增模版<\/button>/g)?.length).toBe(2);
    expect(html).not.toContain("管理模版");
    // 内置内容公开可读的说明（不是"点开才能看到"）
    expect(html).toContain("内置默认模版公开可读");
    // 说明文案必须覆盖第 4 档 MOSS（2026-10-30）
    expect(html).toContain("MOSS 200–300 词英文 caption");
  });

  it("旧风格存档未迁移时给出黄字提示", () => {
    const html = renderToStaticMarkup(
      <StylePanel
        draft={draft({ stylePresets: [{ id: "a", name: "旧", content: "x" }] })}
        setDraft={() => {}}
      />,
    );
    expect(html).toContain("旧「风格存档」未迁移");
    expect(html).toContain("不会丢失");
  });
});
