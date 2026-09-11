import { describe, expect, it } from "vitest";
import {
  buildGuideHash, docAssetUrl, guideStepAnchorId, parseGuideHash, parseGuideLink,
  resolveDocLink, splitGuideLinks, stripLeadingDocTitle,
} from "./guideLinks";

describe("parseGuideLink", () => {
  it("guide: 链接解析出章节 id", () => {
    expect(parseGuideLink("guide:workflow", "去看工作流")).toEqual({
      kind: "guide", label: "去看工作流", target: "workflow",
    });
  });

  it("guide: 带 #序号 时解析出步骤号（1 起）", () => {
    expect(parseGuideLink("guide:workflow#3", "第 3 步")).toEqual({
      kind: "guide", label: "第 3 步", target: "workflow", stepNumber: 3,
    });
  });

  it("guide: 的 # 后面不是正整数时忽略步骤号", () => {
    expect(parseGuideLink("guide:workflow#0", "x")?.stepNumber).toBeUndefined();
    expect(parseGuideLink("guide:workflow#abc", "x")?.stepNumber).toBeUndefined();
  });

  it("guide: 缺章节 id 时不解析", () => {
    expect(parseGuideLink("guide:", "x")).toBeNull();
    expect(parseGuideLink("guide:#2", "x")).toBeNull();
  });

  it("doc: 链接解析出文档路径", () => {
    expect(parseGuideLink("doc:docs/guide/workflow-template-import.md", "详解")).toEqual({
      kind: "doc", label: "详解", target: "docs/guide/workflow-template-import.md",
    });
  });

  it("doc: 缺路径时不解析", () => {
    expect(parseGuideLink("doc:", "x")).toBeNull();
  });

  it("http(s) 归为外链", () => {
    expect(parseGuideLink("https://www.comfy.org", "ComfyUI")?.kind).toBe("external");
    expect(parseGuideLink("http://example.com", "例")?.kind).toBe("external");
  });

  it("未知协议不解析成链接（避免死链）", () => {
    expect(parseGuideLink("file:///C:/secret.txt", "本地文件")).toBeNull();
    expect(parseGuideLink("/docs/guide/a.md", "裸路径")).toBeNull();
  });

  it("文案为空时回退用 target 作文案", () => {
    expect(parseGuideLink("guide:story", "   ")?.label).toBe("guide:story");
  });
});

describe("splitGuideLinks", () => {
  it("无链接时整段原样返回", () => {
    expect(splitGuideLinks("普通正文，没有链接。")).toEqual([
      { type: "text", text: "普通正文，没有链接。" },
    ]);
  });

  it("切出 文本-链接-文本 三段", () => {
    const segs = splitGuideLinks("先看[工作流](guide:workflow)这一节。");
    expect(segs.map((s) => s.type)).toEqual(["text", "link", "text"]);
    expect(segs[0].text).toBe("先看");
    expect(segs[1].link?.target).toBe("workflow");
    expect(segs[2].text).toBe("这一节。");
  });

  it("一条正文里可有多个链接", () => {
    const segs = splitGuideLinks("[A](guide:story) 与 [B](doc:docs/guide/a.md)");
    expect(segs.filter((s) => s.type === "link")).toHaveLength(2);
    expect(segs[0].link?.kind).toBe("guide");
    expect(segs[2].link?.kind).toBe("doc");
  });

  it("不认识的协议按原文保留，不吞字", () => {
    const segs = splitGuideLinks("见[本地文件](file:///C:/a.txt)说明");
    expect(segs).toEqual([{ type: "text", text: "见[本地文件](file:///C:/a.txt)说明" }]);
  });

  it("空字符串返回空数组", () => {
    expect(splitGuideLinks("")).toEqual([]);
  });

  it("同一正则对象多次调用不残留状态（全局 lastIndex 已复位）", () => {
    const once = splitGuideLinks("[A](guide:story)");
    const twice = splitGuideLinks("[A](guide:story)");
    expect(twice).toEqual(once);
  });
});

describe("hash 合同", () => {
  it("只切章时 hash 为 #/guide/<章节>", () => {
    expect(buildGuideHash("quick-start")).toBe("#/guide/quick-start");
    expect(buildGuideHash("quick-start", null)).toBe("#/guide/quick-start");
  });

  it("带文档时追加 /doc/<路径>", () => {
    expect(buildGuideHash("quick-start", "docs/guide/a.md"))
      .toBe("#/guide/quick-start/doc/docs/guide/a.md");
  });

  it("往返解析一致（文档路径含多级目录）", () => {
    const hash = buildGuideHash("story", "docs/guide/workflow-template-import.md");
    expect(parseGuideHash(hash)).toEqual({
      sectionId: "story", docPath: "docs/guide/workflow-template-import.md",
    });
  });

  it("无文档段时 docPath 为 null", () => {
    expect(parseGuideHash("#/guide/story")).toEqual({ sectionId: "story", docPath: null });
  });

  it("非引导区 hash 返回空", () => {
    expect(parseGuideHash("#/system/models")).toEqual({ sectionId: null, docPath: null });
    expect(parseGuideHash("")).toEqual({ sectionId: null, docPath: null });
  });

  it("三段之后的多余斜杠都归进文档路径", () => {
    expect(parseGuideHash("#/guide/story/doc/docs/a/b/c.md").docPath).toBe("docs/a/b/c.md");
  });
});

describe("resolveDocLink", () => {
  it("同级文档按当前文档所在目录解析", () => {
    expect(resolveDocLink("docs/guide/a.md", "b.md")).toBe("docs/guide/b.md");
  });

  it("上级目录用 .. 回退", () => {
    expect(resolveDocLink("docs/guide/a.md", "../onboarding/b.md")).toBe("docs/onboarding/b.md");
  });

  it("以 / 开头视作相对仓库根", () => {
    expect(resolveDocLink("docs/guide/a.md", "/docs/onboarding/b.md")).toBe("docs/onboarding/b.md");
  });

  it("带锚点与查询串的链接去掉后缀再解析", () => {
    expect(resolveDocLink("docs/guide/a.md", "b.md#第三节?x=1")).toBe("docs/guide/b.md");
  });

  it("外链与页内锚点不进文档阅读态", () => {
    expect(resolveDocLink("docs/guide/a.md", "https://example.com/b.md")).toBeNull();
    expect(resolveDocLink("docs/guide/a.md", "#第三节")).toBeNull();
    expect(resolveDocLink("docs/guide/a.md", "mailto:a@b.c")).toBeNull();
  });

  it("非 md 不进文档阅读态", () => {
    expect(resolveDocLink("docs/guide/a.md", "img.png")).toBeNull();
    expect(resolveDocLink("docs/guide/a.md", "")).toBeNull();
  });

  it("反斜杠路径按正斜杠解析", () => {
    expect(resolveDocLink("docs/guide/a.md", "sub\\b.md")).toBe("docs/guide/sub/b.md");
  });
});

describe("docAssetUrl", () => {
  it("docs/assets 下的相对图片改成静态 URL", () => {
    expect(docAssetUrl("docs/guide/a.md", "../assets/guide/quick-start-5.png"))
      .toBe("/docs-assets/guide/quick-start-5.png");
  });

  it("已是静态 URL 时原样返回", () => {
    expect(docAssetUrl("docs/guide/a.md", "/docs-assets/guide/x.png"))
      .toBe("/docs-assets/guide/x.png");
  });

  it("外链与 data URI 不改写", () => {
    expect(docAssetUrl("docs/guide/a.md", "https://x.dev/a.png")).toBeNull();
    expect(docAssetUrl("docs/guide/a.md", "data:image/png;base64,AAA")).toBeNull();
  });

  it("不在 docs/assets 下的图片返回 null（交给浏览器默认解析）", () => {
    expect(docAssetUrl("docs/guide/a.md", "../other/x.png")).toBeNull();
  });
});

describe("stripLeadingDocTitle", () => {
  it("去掉正文首个一级标题", () => {
    expect(stripLeadingDocTitle("# 标题\n\n正文")).toBe("\n正文");
  });

  it("首个标题前有空行也能去掉", () => {
    expect(stripLeadingDocTitle("\n\n# 标题\n正文")).toBe("\n\n正文");
  });

  it("没有一级标题时原样返回", () => {
    expect(stripLeadingDocTitle("## 小标题\n正文")).toBe("## 小标题\n正文");
  });

  it("只去掉第一个，正文中间的标题保留", () => {
    expect(stripLeadingDocTitle("# A\n正文\n# B")).toBe("正文\n# B");
  });
});

describe("guideStepAnchorId", () => {
  it("锚点 id 由章节与步骤号拼出", () => {
    expect(guideStepAnchorId("workflow", 3)).toBe("guide-step-workflow-3");
  });
});
