import { describe, expect, it } from "vitest";
import { dirOf, joinPath, resolveDocImageUrls } from "./docAssets";

/** 假的素材 URL 构造器，等价于 artifactAssetUrl（便于断言绝对路径）。 */
const toUrl = (absolutePath: string) => `asset://${absolutePath}`;
const doc = (docPath: string) => ({ docPath, toUrl });

describe("dirOf / joinPath", () => {
  it("取目录与拼接（含 . / .. 规范化与盘符保留）", () => {
    expect(dirOf("D:/tool/作品/docs/设定总集.md")).toBe("D:/tool/作品/docs");
    expect(dirOf("设定总集.md")).toBe("");
    expect(joinPath("D:/工具/作品/docs", "assets/a.png")).toBe("D:/工具/作品/docs/assets/a.png");
    expect(joinPath("D:/工具/作品/docs/子目录", "../assets/a.png")).toBe("D:/工具/作品/docs/assets/a.png");
    expect(joinPath("", "D:\\工具\\作品\\docs\\assets\\a.png")).toBe("D:/工具/作品/docs/assets/a.png");
  });
});

describe("resolveDocImageUrls", () => {
  it("文档同级的相对路径解析为素材 URL（attach_material 回传形态）", () => {
    const md = "外貌如下图：\n\n![八千代立绘](assets/sim-web-3.png)\n";
    const out = resolveDocImageUrls(md, doc("D:/作品/docs/设定总集.md"));
    expect(out).toContain("![八千代立绘](asset://D:/作品/docs/assets/sim-web-3.png)");
  });

  it("子目录文档用 ../ 回退到 docs/assets", () => {
    const md = "![图](../assets/a.png)";
    const out = resolveDocImageUrls(md, doc("D:/作品/docs/子目录/设定.md"));
    expect(out).toContain("asset://D:/作品/docs/assets/a.png");
  });

  it("带 title 的图片语法保留 title", () => {
    const md = '![图](assets/a.png "标题")';
    const out = resolveDocImageUrls(md, doc("D:/作品/docs/x.md"));
    expect(out).toBe('![图](asset://D:/作品/docs/assets/a.png "标题")');
  });

  it("外部/锚点/绝对路径各自处置（外部原样；绝对路径仍过 toUrl）", () => {
    const md = [
      "![a](https://ex.example/a.png)",
      "![b](data:image/png;base64,AAA)",
      "![c](#anchor)",
      "![d](D:/作品/_web_materials/x.png)",
    ].join("\n");
    const out = resolveDocImageUrls(md, doc("D:/作品/docs/x.md"));
    expect(out).toContain("https://ex.example/a.png");
    expect(out).toContain("data:image/png;base64,AAA");
    expect(out).toContain("![c](#anchor)");
    expect(out).toContain("asset://D:/作品/_web_materials/x.png");
  });

  it("HTML <img src> 同样处理", () => {
    const md = '<img src="assets/a.png" alt="图">';
    const out = resolveDocImageUrls(md, doc("D:/作品/docs/x.md"));
    expect(out).toBe('<img src="asset://D:/作品/docs/assets/a.png" alt="图">');
  });

  it("无基准目录的文档不猜测（相对路径原样保留）", () => {
    const md = "![图](assets/a.png)";
    expect(resolveDocImageUrls(md, doc("设定总集.md"))).toBe(md);
  });

  it("没有图片的内容原样返回；空串安全", () => {
    const md = "# 标题\n\n纯文字，无插图。\n";
    expect(resolveDocImageUrls(md, doc("D:/作品/docs/x.md"))).toBe(md);
    expect(resolveDocImageUrls("", doc("D:/作品/docs/x.md"))).toBe("");
  });
});
