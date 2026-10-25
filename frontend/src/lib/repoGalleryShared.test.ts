// repoGalleryShared 回归（2026-10-24 拆分刀）：纯 helper 行为网 + RepoGallery 搬移形态锁。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  AI_TAG_PROMPT, fmtCreatedAt, seqOf, TAG_MODES, VISION_DESC_PROMPT, VLM_TWO_PARA_PROMPT,
} from "./repoGalleryShared";

const gallerySrc = readFileSync(new URL("../components/RepoGallery.tsx", import.meta.url), "utf-8");

describe("fmtCreatedAt", () => {
  it("毫秒时间戳 → YYYY-MM-DD HH:mm（补零）", () => {
    // 2026-10-24 16:00 本地时区构造：用 Date 走本地时区避免 CI 偏移
    const d = new Date(2026, 9, 24, 16, 5);
    expect(fmtCreatedAt(d.getTime())).toBe("2026-10-24 16:05");
  });

  it("缺失/0 → 空串（调用处据此不渲染该行）", () => {
    expect(fmtCreatedAt(undefined)).toBe("");
    expect(fmtCreatedAt(0)).toBe("");
  });
});

describe("seqOf", () => {
  it("local-view URL 取文件名前导顺序号 000006.png → 6", () => {
    expect(seqOf("http://127.0.0.1:8010/api/comfyui/local-view?path=D:/out/pictures/000006.png")).toBe(6);
  });

  it("旧图尾部数字兜底 anima1_00073_.png → 73；反斜杠归一", () => {
    expect(seqOf("http://127.0.0.1:8010/api/comfyui/local-view?path=D:\\out\\anima1_00073_.png")).toBe(73);
  });

  it("取不到数字返回 0", () => {
    expect(seqOf("http://127.0.0.1:8010/api/comfyui/local-view?path=D:/out/no-name.png")).toBe(0);
  });
});

describe("VLM prompt 常量", () => {
  it("两段式 prompt＝四行单换行文本（「空行分隔」是对模型的输出指令，prompt 本体无空行）", () => {
    const lines = VLM_TWO_PARA_PROMPT.split("\n");
    expect(lines).toHaveLength(4);
    expect(lines[0]).toContain("两段");
    expect(lines[1].startsWith("第一段")).toBe(true);
    expect(lines[2].startsWith("第二段")).toBe(true);
    expect(lines[3]).toContain("只输出这两段正文");
  });

  it("纯描述模式不产标签；AI 标签模式限定 8-16 个", () => {
    expect(VISION_DESC_PROMPT).toContain("不要写提示词");
    expect(AI_TAG_PROMPT).toContain("8-16");
  });

  it("四种反推模式齐备", () => {
    expect(TAG_MODES.map((m) => m.value)).toEqual(["vlm", "wd14", "ai", "desc"]);
  });
});

describe("搬移形态锁（RepoGallery 消费方）", () => {
  it("RepoGallery 从 repoGalleryShared 导入，不再本地定义", () => {
    expect(gallerySrc).toContain('from "../lib/repoGalleryShared"');
    expect(gallerySrc).not.toContain("function fmtCreatedAt");
    expect(gallerySrc).not.toContain("const TAG_MODES");
    expect(gallerySrc).not.toContain("const AI_TAG_PROMPT");
  });
});
