// @vitest-environment jsdom
//
// 「打开文件位置」的组件接线回归（2026-10-14 用户需求）。
//
// 需求原话：「生图下面不是有如图所示的按钮吗…添加个打开文件所在地址的按钮，
// 其次就是我新生成的图片在对应的仓库却找不到」。
//
// 这里只钉接线（三条）：
//   1. local-view URL（本机已留存）⇒ 按钮可用 ⇒ 点击调 /artifacts/open-folder，
//      带 (outputDir, path, select=true, repoId)——与产物卡「打开位置」同一端点；
//   2. 远程 URL（云端图未留存、回退了远程地址）⇒ 按钮禁用并说明原因；
//   3. parts 图片卡与旧式 msg.image 图片卡两条渲染路径都挂上，且不与既有按钮互相挤掉。

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("dompurify", () => ({ default: { sanitize: (html: string) => html } }));
vi.mock("../../lib/planTaskActivity", () => ({
  approvePlanTask: vi.fn(), cancelPlanTask: vi.fn(), getPlanTask: vi.fn(),
  keepRecipe: vi.fn(), deleteRecipe: vi.fn(), listRecipes: vi.fn(async () => ({})),
}));
// vi.mock 工厂会随 import 提升先于 const 求值 ⇒ 用 vi.hoisted 造 spy
const { openArtifactFolder } = vi.hoisted(() => ({
  openArtifactFolder: vi.fn(async () => ({ ok: true, path: "D:/w/a.png" })),
}));
vi.mock("../../api/artifacts", () => ({ openArtifactFolder }));

import { AssistantMessage } from "./ChatMessages";

let container: HTMLDivElement;
let root: Root;

afterEach(() => {
  act(() => { root.unmount(); });
  container.remove();
  openArtifactFolder.mockClear();
});

const LOCAL_URL = "http://127.0.0.1:8010/api/comfyui/local-view?path=D%3A%5Cw%5Ca.png";
const REMOTE_URL = "https://webstatic.aiproxy.vip/output/20261013/138334/a.png";

function mount(msg: Record<string, unknown>) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <AssistantMessage
        msg={msg as never}
        onSendImage={() => {}}
        visualCiOutputDir="D:/w"
        visualCiRepoId="repo-1"
      />,
    );
  });
}

function btnByText(text: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(text));
}

describe("打开文件位置", () => {
  it("parts 图片卡：本机已留存的图出现该按钮，且与下载/重新生图并存", () => {
    mount({
      id: "m1", role: "assistant", text: "",
      parts: [{ type: "image", url: LOCAL_URL, slotId: "illo-req-1:last" }],
    });
    const btn = btnByText("打开文件位置");
    expect(btn).toBeTruthy();
    expect(btn!.disabled).toBe(false);
    expect(btnByText("下载")).toBeTruthy();
    expect(btnByText("重新生图")).toBeTruthy();
  });

  it("点击后用 local-view 里的 path 调 open-folder（select + repoId）", async () => {
    mount({
      id: "m1", role: "assistant", text: "",
      parts: [{ type: "image", url: LOCAL_URL, slotId: "illo-req-1:last" }],
    });
    await act(async () => { btnByText("打开文件位置")!.click(); });
    expect(openArtifactFolder).toHaveBeenCalledWith("D:/w", "D:\\w\\a.png", true, "repo-1");
  });

  it("远程 URL（未留存到本机）时按钮禁用，并说明原因", () => {
    mount({
      id: "m1", role: "assistant", text: "",
      parts: [{ type: "image", url: REMOTE_URL }],
    });
    const btn = btnByText("打开文件位置");
    expect(btn).toBeTruthy();
    expect(btn!.disabled).toBe(true);
    expect(btn!.title).toContain("未留存");
  });

  it("旧式 msg.image 图片卡同样挂上并传参", async () => {
    mount({ id: "m2", role: "assistant", text: "", image: LOCAL_URL });
    const btn = btnByText("打开文件位置");
    expect(btn).toBeTruthy();
    expect(btn!.disabled).toBe(false);
    await act(async () => { btn!.click(); });
    expect(openArtifactFolder).toHaveBeenCalledWith("D:/w", "D:\\w\\a.png", true, "repo-1");
  });

  it("后端返回 ok=false 时弹窗提示（文件可能被移动/删除）", async () => {
    openArtifactFolder.mockResolvedValueOnce({ ok: false, path: "D:/w/a.png" });
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    mount({
      id: "m1", role: "assistant", text: "",
      parts: [{ type: "image", url: LOCAL_URL, slotId: "illo-req-1:last" }],
    });
    await act(async () => { btnByText("打开文件位置")!.click(); });
    expect(alertSpy).toHaveBeenCalled();
    alertSpy.mockRestore();
  });
});
