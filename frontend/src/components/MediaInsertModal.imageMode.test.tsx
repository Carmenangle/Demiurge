// @vitest-environment jsdom
// 多元数据插入 · 生图方式开关（2026-11-08 用户定稿）：
// 文生图=提示词+LoRA（不读底图）；图生图=底图+LoRA／云端图生图。
// 纯逻辑口径在 `lib/illustrationMedia.test.ts`；本文件只锁面板能选、能保存、角色卡模式不给选。
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { MediaInsertModal } from "./MediaInsertModal";
import type { ExposedField, Template } from "../api/workflows";
import type { MediaInsertPreset } from "../stores/settings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const exposed: ExposedField[] = [
  { node_id: "3", field: "text", label: "提示词", control: "textarea", semantic: "prompt", binding: "prompt", default: "" },
  { node_id: "7", field: "image", label: "底图", control: "image", semantic: "base_image", binding: "base_image", default: "" },
];

const TEMPLATE: Template = {
  id: "tpl-1", name: "测试生图模板", source_path: "", exposed, node_order: [],
  input_node_ids: [], output_node_ids: [], created_at: 0, updated_at: 0,
};

const PRESET: MediaInsertPreset = {
  templateId: "tpl-1",
  appearanceSource: "worldbook",
  characterLoras: { 甲: { loraName: "role_a.safetensors", baseImage: "a.png" } },
};

let container: HTMLDivElement;
let root: Root;

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
});

async function mountModal(preset: MediaInsertPreset) {
  const saved: MediaInsertPreset[] = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MediaInsertModal templates={[TEMPLATE]} modelsDir="" preset={preset}
        onSave={(value) => saved.push(value)} onClose={() => {}} />,
    );
  });
  return saved;
}

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button"))
    .find((b) => (b.textContent || "").trim() === label);
  if (!found) throw new Error(`button not found: ${label}`);
  return found as HTMLButtonElement;
}

async function click(el: HTMLButtonElement) {
  await act(async () => { el.click(); });
}

describe("多元数据插入 · 生图方式开关", () => {
  it("旧预设带底图 ⇒ 默认图生图；切到文生图后保存写 text2img", async () => {
    const saved = await mountModal(PRESET);
    expect(button("图生图").getAttribute("aria-pressed")).toBe("true");

    await click(button("文生图"));
    expect(button("文生图").getAttribute("aria-pressed")).toBe("true");
    await click(button("保存"));

    expect(saved).toHaveLength(1);
    expect(saved[0].imageMode).toBe("text2img");
    // 底图绑定本身不因切模式被抹掉，只是运行时不再参与。
    expect(saved[0].characterLoras?.甲?.baseImage).toBe("a.png");
  });

  it("切回图生图后保存写 img2img", async () => {
    const saved = await mountModal({ ...PRESET, imageMode: "text2img" });
    expect(button("文生图").getAttribute("aria-pressed")).toBe("true");

    await click(button("图生图"));
    await click(button("保存"));

    expect(saved[0].imageMode).toBe("img2img");
  });

  it("角色卡模式没有底图绑定 ⇒ 图生图按钮禁用，保存只能是文生图", async () => {
    const saved = await mountModal({ ...PRESET, appearanceSource: "character_card", imageMode: "img2img" });
    expect(button("文生图").getAttribute("aria-pressed")).toBe("true");
    expect(button("图生图").disabled).toBe(true);

    await click(button("保存"));
    expect(saved[0].imageMode).toBe("text2img");
  });

  it("按能力分槽的图片模板随预设保存（旧 templateId 仍写文生图槽）", async () => {
    const saved = await mountModal({
      ...PRESET,
      imageTemplates: { text2img: "tpl-1", img2img: "tpl-1" },
    });
    await click(button("保存"));

    expect(saved[0].templateId).toBe("tpl-1");
    expect(saved[0].imageTemplates).toMatchObject({
      text2img: "tpl-1",
      img2img: "tpl-1",
    });
  });
});
