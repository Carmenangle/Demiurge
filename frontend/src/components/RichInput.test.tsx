// D1 历史回放回归测试（2026-09-01 用户报告「发送出去却看不到对应文档名」后新增）。
// 关键点：通用文件/媒体附件（attachments）必须转成 type="file" 的 part 写入 userMsg.parts，
// ChatMessages.FileAttachmentChip 据此渲染文件名+大小+下载按钮。
// 历史教训：RichInput.doSubmit 早期只把 attachments 放顶层 content.attachments 字段，
// userMsg.parts 缺 file part → 渲染时只走 else 分支（仅 msg.text）→ 附件元信息丢失。

import { describe, expect, it } from "vitest";
import type { FileAttachmentMeta } from "../api/ai";
import { buildSubmitParts } from "./RichInput";

describe("buildSubmitParts（D1 历史回放关键：attachments 必须进 parts 为 file part）", () => {
  it("纯文本：仅产出 text part", () => {
    const parts = buildSubmitParts({ text: "你好", images: [], attachments: [] });
    expect(parts).toEqual([{ type: "text", text: "你好" }]);
  });

  it("附件 + 文本：attachments 必须转成 type=file part（修复点）", () => {
    // 复现用户报告：发送「读取文档…」+ 一个 .md 文档附件，消息体必须显示文件名+大小
    const attachments: FileAttachmentMeta[] = [{
      fileId: "a".repeat(32), name: "形象提示词-唐柚.md", mime: "text/markdown", size: 45_800,
    }];
    const parts = buildSubmitParts({
      text: "读取文档,调用krea2文生图模版",
      images: [],
      attachments,
    });
    // 必须有 file part（这正是 ChatMessages.FileAttachmentChip 渲染的判据）
    const filePart = parts.find((p) => p.type === "file");
    expect(filePart).toBeDefined();
    expect(filePart).toMatchObject({
      type: "file",
      fileId: "a".repeat(32),
      name: "形象提示词-唐柚.md",
      mime: "text/markdown",
      size: 45_800,
    });
    expect(parts).toContainEqual({ type: "text", text: "读取文档,调用krea2文生图模版" });
  });

  it("图片 + 附件 + 文本：parts 顺序 = image → file → text（视觉：附件在上、文本在下）", () => {
    const parts = buildSubmitParts({
      text: "读取文档",
      images: ["data:image/png;base64,AAA"],
      attachments: [{
        fileId: "b".repeat(32), name: "plan.md", mime: "text/markdown", size: 1234,
      }],
    });
    expect(parts.map((p) => p.type)).toEqual(["image", "file", "text"]);
    // 附件字段完整（fileId 真源，32-hex，编辑回填 userMessageRichContent 据此还原）
    const filePart = parts[1];
    expect(filePart).toMatchObject({
      type: "file", fileId: "b".repeat(32), name: "plan.md", mime: "text/markdown", size: 1234,
    });
  });

  it("蒙版 + 附件 + 文本：masked-image 在 image 之后、file 之前", () => {
    const parts = buildSubmitParts({
      text: "蒙化修改",
      images: ["data:image/png;base64,IMG"],
      maskedImage: {
        image: "data:image/png;base64,ORIG",
        mask: "data:image/png;base64,MASK",
        preview: "data:image/png;base64,PREV",
      },
      attachments: [{
        fileId: "c".repeat(32), name: "doc.pdf", mime: "application/pdf", size: 9_999,
      }],
    });
    expect(parts.map((p) => p.type)).toEqual(["image", "masked-image", "file", "text"]);
    const filePart = parts[2];
    expect(filePart).toMatchObject({
      type: "file", fileId: "c".repeat(32), name: "doc.pdf", mime: "application/pdf", size: 9_999,
    });
  });

  it("多附件：每个 attachment 都产出独立的 file part（媒体栏+通用文件栏合并）", () => {
    const attachments: FileAttachmentMeta[] = [
      { fileId: "a".repeat(32), name: "ref.mp4", mime: "video/mp4", size: 1_000_000 },
      { fileId: "b".repeat(32), name: "plan.md", mime: "text/markdown", size: 2000 },
      { fileId: "c".repeat(32), name: "data.csv", mime: "text/csv", size: 500 },
    ];
    const parts = buildSubmitParts({ text: "多附件", images: [], attachments });
    const fileParts = parts.filter((p) => p.type === "file");
    expect(fileParts).toHaveLength(3);
    expect(fileParts.map((p) => p.name)).toEqual(["ref.mp4", "plan.md", "data.csv"]);
  });

  it("空 attachments：不产 file part（userMessageRichContent 据此 attachments 字段为空）", () => {
    const parts = buildSubmitParts({ text: "hi", images: [], attachments: [] });
    expect(parts.filter((p) => p.type === "file")).toHaveLength(0);
    expect(parts).toEqual([{ type: "text", text: "hi" }]);
  });

  it("空文本 + 纯附件：仍产出 file part（可发送，纯附件无文本也参与对话）", () => {
    // 边界：用户只发附件不发文本（系统已允许纯附件提交，见 send L1708 判定 attachments 长度）
    const parts = buildSubmitParts({
      text: "",
      images: [],
      attachments: [{ fileId: "d".repeat(32), name: "only.txt", mime: "text/plain", size: 10 }],
    });
    expect(parts).toEqual([{
      type: "file", fileId: "d".repeat(32), name: "only.txt", mime: "text/plain", size: 10,
    }]);
  });
});