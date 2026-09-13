import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ConfirmModal, shouldCancelConfirmFromBackdrop } from "./Modal";

describe("ConfirmModal", () => {
  it("can require an explicit choice instead of cancelling from the backdrop", () => {
    expect(shouldCancelConfirmFromBackdrop(false, false)).toBe(false);
    expect(shouldCancelConfirmFromBackdrop(true, false)).toBe(true);
    expect(shouldCancelConfirmFromBackdrop(true, true)).toBe(false);
  });

  it("keeps the workflow overlay class when portal rendering falls back outside a browser", () => {
    const html = renderToStaticMarkup(
      <ConfirmModal
        title="应用 LoRA 数据保存内容"
        portal
        closeOnBackdrop={false}
        overlayClassName="workflow-lora-modal-mask"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(html).toContain("workflow-lora-modal-mask");
  });
});
