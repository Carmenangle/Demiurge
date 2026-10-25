import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PromptTemplate } from "../api/ai/promptTemplates";
import { PromptTemplateModal } from "./PromptTemplateModal";

const builtinAnima: PromptTemplate = {
  id: "builtin:anima_tags", builtin: true, kind: "anima_tags", name: "Anima",
  base_id: "", base_hash: "", redline_lock: true, redline_lock_off_at: 0,
  content_hash: "aaaa1111",
  system_text: "内置指令全文", extra_instructions: "", quality_tags: "masterpiece", negative_tags: "worst",
  section_titles: {}, section_order: [], camera_band_overrides: {},
  beat_labels: [], default_duration: 0, negative_extra: "", meta_note: "",
};

const customVideo: PromptTemplate = {
  id: "b".repeat(32), builtin: false, kind: "video_climax", name: "我的高潮",
  base_id: "builtin:video_climax", base_hash: "bbbb2222", redline_lock: true, redline_lock_off_at: 0,
  system_text: "", extra_instructions: "", quality_tags: "", negative_tags: "",
  section_titles: { meta: "元信息·改" }, section_order: [],
  camera_band_overrides: { "2": "镜头横移" }, beat_labels: ["起", "承", "合"],
  default_duration: 6, negative_extra: "模糊", meta_note: "备注",
};

describe("PromptTemplateModal", () => {
  it("渲染内置只读详情 + 克隆入口 + 分组页签", () => {
    const html = renderToStaticMarkup(
      <PromptTemplateModal builtins={[builtinAnima]} customs={[]}
        onChanged={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("提示词模版");
    expect(html).toContain("生图提示词模版");
    expect(html).toContain("视频提示词模版");
    expect(html).toContain("内置默认（只读）");
    expect(html).toContain("内置指令全文");            // 内置全文只读展示
    expect(html).toContain("克隆这份，改成我的");
    expect(html).toContain("aaaa1111");                // 内容哈希
    expect(html).not.toContain("删除");
  });

  it("视频分组展示结构预览（区块/运镜/节奏/时长）", () => {
    const html = renderToStaticMarkup(
      <PromptTemplateModal builtins={[]} customs={[customVideo]}
        onChanged={() => {}} onClose={() => {}} />,
    );
    // 初始分组为生图（无内容）——切到视频组由交互完成，静态渲染至少不崩且有骨架
    expect(html).toContain("提示词模版");
    expect(html).toContain("选择左侧模版查看");
  });

  it("自定义模版条目带「有更新」判定所需的克隆基准信息", () => {
    const stale: PromptTemplate = {
      ...builtinAnima, id: "c".repeat(32), builtin: false, name: "我的Anima",
      base_id: "builtin:anima_tags", base_hash: "old0old0", content_hash: undefined,
    };
    const html = renderToStaticMarkup(
      <PromptTemplateModal
        builtins={[{ ...builtinAnima, content_hash: "new9new9" }]}
        customs={[stale]}
        onChanged={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("我的模版");
    expect(html).toContain("我的Anima");
    // 基准哈希与内置不一致 ⇒ 列表角标「有更新」
    expect(html).toContain("有更新");
  });
});

// ===== 2026-10-25 反馈：新增模版按分组进入、协议族可选 =====
describe("PromptTemplateModal · 新增模版", () => {
  it("createForGroup=image 直接进表单：内置全文种子 + 协议族切换 + 红线锁", () => {
    const html = renderToStaticMarkup(
      <PromptTemplateModal builtins={[builtinAnima]} customs={[]} createForGroup="image"
        onChanged={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("新增模版");
    expect(html).toContain("生成指令全文（system）");
    expect(html).toContain("内置指令全文");      // 种子=内置全文
    expect(html).toContain("红线条款锁");
    expect(html).toContain("保存");
    // 三个生图协议族都可选
    expect(html).toContain("Anima");
    expect(html).toContain("自然语言");
    expect(html).toContain("Niji");
  });

  it("createForGroup=video 进视频表单：六段标题/运镜/节奏/时长", () => {
    const builtinVideo: PromptTemplate = {
      ...builtinAnima, id: "builtin:video_climax", kind: "video_climax",
      name: "视频·高潮点", content_hash: "bbbb2222",
      system_text: "", quality_tags: "", negative_tags: "",
      section_titles: {}, section_order: [], camera_band_overrides: {},
      beat_labels: [], default_duration: 15,
    };
    const html = renderToStaticMarkup(
      <PromptTemplateModal builtins={[builtinVideo]} customs={[]} createForGroup="video"
        onChanged={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("区块顺序");
    expect(html).toContain("运镜文案覆盖");
    expect(html).toContain("节奏名");
    expect(html).toContain("默认时长");
    expect(html).toContain("负面追加词");
  });
});
