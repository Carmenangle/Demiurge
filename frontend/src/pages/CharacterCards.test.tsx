// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { CharacterCards } from "./CharacterCards";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/characters", () => ({
  listCharacters: vi.fn(async () => ({
    items: [{
      name: "占位卡名称", folder: "占位卡名称",
      has_worldbook: false, has_regex: false, has_chat: false, has_avatar: false,
    }],
  })),
  scanLooseCards: vi.fn(async () => ({ imported: [], skipped: [], failed: [] })),
  snapshotToWork: vi.fn(async () => ({ ok: true, created: true, persona_created: false })),
  importCharacter: vi.fn(),
  deleteCharacter: vi.fn(),
  exportChat: vi.fn(),
  characterDetail: vi.fn(async () => ({})),
  avatarUrl: vi.fn(() => "/avatar.png"),
}));

vi.mock("../components/CardPreviewModal", () => ({
  CardPreviewModal: ({ name }: { name: string }) => (
    <div data-testid="card-preview">{name}</div>
  ),
}));

let container: HTMLDivElement;
let root: Root;

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function click(el: Element) {
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

describe("角色卡预览入口", () => {
  it("单击封面即打开预览（手机双击才开＝缺陷）", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <CharacterCards
          characterDir="/cards"
          outputDir=""
          worldbookDir=""
          persona={{ name: "", content: "" }}
          onOpenCard={() => {}}
        />,
      );
    });
    const cover = container.querySelector('[title="点击预览角色卡"]');
    expect(cover).not.toBeNull();
    await act(async () => { click(cover!); });
    expect(container.querySelector('[data-testid="card-preview"]')?.textContent)
      .toBe("占位卡名称");
  });
});
