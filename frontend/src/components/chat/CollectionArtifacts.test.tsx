// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ArtifactMeta } from "../../types/chat";
import { CollectionArtifacts } from "./CollectionArtifacts";

// React 19 + jsdom：必须显式声明 act 环境，否则每次 act() 调用都会打
// "The current testing environment is not configured to support act(...)" 警告。
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const version1 = {
  version_id: "001-20260909103000", seq: 1, ts: "2026-09-09T10:30:00",
  trigger: "done", summary: "《玫瑰与繁花》合集卡已完成",
  files: [
    { rel: "玫瑰与繁花/card.json", name: "card.json", size: 85764, mtime: 1, path: "D:/w/玫瑰与繁花/card.json" },
    { rel: "玫瑰与繁花/worldbook.json", name: "worldbook.json", size: 82474, mtime: 1, path: "D:/w/玫瑰与繁花/worldbook.json" },
  ],
  total_size: 168238,
};
const version2 = {
  version_id: "002-20260909120000", seq: 2, ts: "2026-09-09T12:00:00",
  trigger: "done", summary: "补写完成并通过密度验收",
  files: [
    { rel: "玫瑰与繁花/card.json", name: "card.json", size: 90000, mtime: 1, path: "D:/w/玫瑰与繁花/card.json" },
  ],
  total_size: 90000,
};

vi.mock("../../api/artifacts", () => ({
  previewArtifact: vi.fn(async () => ({
    ok: true, name: "card.json", size: 100, kind: "card",
    text: '{"name":"玫瑰与繁花","character_book":{"entries":[]}}',
    truncated: false,
  })),
  // 版本副本预览（B5）：返回与当前文件不同的内容，便于断言「看的是副本」
  previewVersionFile: vi.fn(async (_o: string, versionId: string, rel: string) => ({
    ok: true, name: rel.split("/").pop() || rel, size: 90, kind: "card",
    text: `{"from":"${versionId}"}`, truncated: false,
  })),
  artifactDownloadUrl: vi.fn((_o: string, path: string) => `http://x/download?path=${encodeURIComponent(path)}`),
  artifactAssetUrl: vi.fn((_o: string, path: string) => `http://x/asset?path=${encodeURIComponent(path)}`),
  openArtifactFolder: vi.fn(async () => ({ ok: true, path: "D:/w/card.json" })),
  listArtifacts: vi.fn(async (_o: string, _r?: string) => ({ ok: true, items: arts })),
  listVersions: vi.fn(async () => ({ ok: true, versions: [version2, version1] })),
  restoreVersion: vi.fn(async () => ({ ok: true, restored: ["D:/w/玫瑰与繁花/card.json"] })),
  deleteVersion: vi.fn(async () => ({ ok: true })),
  syncWorldbook: vi.fn(async () => ({ ok: true, dest: "D:/worlds/玫瑰与繁花.json", entries: 40 })),
}));

import {
  artifactDownloadUrl, deleteVersion, listArtifacts, listVersions,
  openArtifactFolder, previewArtifact, previewVersionFile, restoreVersion, syncWorldbook,
} from "../../api/artifacts";

const arts: ArtifactMeta[] = [
  { kind: "card", name: "角色主卡 · 玫瑰与繁花", path: "D:/w/玫瑰与繁花/card.json", size: 85764 },
  { kind: "worldbook", name: "世界书 · 玫瑰与繁花", path: "D:/w/玫瑰与繁花/worldbook.json", size: 82474 },
];

let container: HTMLDivElement;
let root: Root;
async function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(node); });
}
afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.clearAllMocks();
});

describe("CollectionArtifacts 交付产物卡（Claude 式）", () => {
  it("无产物时不渲染任何内容", async () => {
    await mount(<CollectionArtifacts outputDir="D:/w" />);
    expect(container.textContent || "").toBe("");
  });

  it("autoLoad：历史智能编造消息无实时 artifacts 时按作品目录惰性补卡", async () => {
    await mount(<CollectionArtifacts outputDir="D:/w" repoId="repo-1" autoLoad />);
    // 拉取后渲染卡片（快照里没有产物信息，靠 listArtifacts 回填）
    expect(listArtifacts).toHaveBeenCalledWith("D:/w", "repo-1");
    const text = container.textContent || "";
    expect(text).toContain("玫瑰与繁花");
    expect(text).toContain("Card·JSON");
  });

  it("autoLoad 拉到空产物时不渲染且不重复请求", async () => {
    vi.mocked(listArtifacts).mockResolvedValueOnce({ ok: true, items: [] });
    await mount(<CollectionArtifacts outputDir="D:/w" autoLoad />);
    expect(container.textContent || "").toBe("");
    // 同一实例重渲染（模拟消息 props 更新）不再发请求：lazyItems 已缓存为空
    await act(async () => { root.render(<CollectionArtifacts outputDir="D:/w" autoLoad />); });
    expect(listArtifacts).toHaveBeenCalledTimes(1);
  });

  it("渲染产物卡片行：标签 + 每张卡的名字与大小", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    const text = container.textContent || "";
    expect(text).toContain("交付产物");
    // 标题 = 作品名（目录名），不是磁盘文件名 card/world
    expect(text).toContain("玫瑰与繁花");
    expect(text).toContain("角色主卡");
    expect(text).toContain("世界书");
    // 85764 B = 83.75 KB，整数显示 84 KB
    expect(text).toContain("84 KB");
    expect(container.querySelectorAll(".artifact-tile")).toHaveLength(2);
    // 文件类型标识：每张卡都带「类型·扩展名」标注（Card·JSON / WorldBook·JSON）
    expect(container.querySelectorAll(".artifact-tile-ext").length).toBeGreaterThanOrEqual(2);
    expect(text).toContain("Card·JSON");
    expect(text).toContain("WorldBook·JSON");
    // 卡片底部操作行（对齐参考图）：预览 / 打开位置 / 下载
    const actions = container.querySelectorAll(".artifact-tile-action");
    expect(actions.length).toBeGreaterThanOrEqual(2);
    expect(text).toContain("预览");
    expect(text).toContain("打开位置");
  });

  it("卡片底部「下载」直接触发下载且不冒泡打开预览", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    const dlBtn = Array.from(container.querySelectorAll(".artifact-tile-action"))
      .find((b) => (b.textContent || "").includes("下载")) as HTMLButtonElement;
    expect(dlBtn).toBeTruthy();
    expect(previewArtifact).not.toHaveBeenCalled();
    expect(openArtifactFolder).not.toHaveBeenCalled();
  });

  it("点击卡片打开预览：请求后端文本并显示 JSON 美化内容", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    const card = container.querySelector(".artifact-tile") as HTMLButtonElement;
    await act(async () => { card.click(); });
    expect(previewArtifact).toHaveBeenCalledWith("D:/w", "D:/w/玫瑰与繁花/card.json", "");
    // 预览弹层出现：路径 + 美化后的 JSON 字段 + 两个操作按钮
    const text = container.textContent || "";
    expect(text).toContain("D:/w/玫瑰与繁花/card.json");
    expect(text).toContain('"name": "玫瑰与繁花"');
    expect(text).toContain("打开文件位置");
    expect(text).toContain("下载");
  });

  it("「打开文件位置」调用后端定位 API 并提示结果", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    const openBtn = Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").includes("打开文件位置"));
    await act(async () => { openBtn?.click(); });
    expect(openArtifactFolder).toHaveBeenCalledWith("D:/w", "D:/w/玫瑰与繁花/card.json", true, "");
    expect(container.textContent).toContain("已在资源管理器中定位该文件");
  });

  it("「下载」使用后端下载地址（Content-Disposition 附件）", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    // mock 返回 http://x/download?path=…，校验下载路径携带 path（真实实现经 apiUrl 拼 /api/artifacts/download）
    expect(artifactDownloadUrl("D:/w", "D:/w/玫瑰与繁花/card.json")).toContain("download?");
    expect(artifactDownloadUrl("D:/w", "D:/w/玫瑰与繁花/card.json")).toContain(encodeURIComponent("D:/w/玫瑰与繁花/card.json"));
  });
});

describe("CollectionArtifacts 产物版本历史", () => {
  it("打开预览即拉取版本列表并展示版本行", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    // 版本列表在预览弹层打开时拉取（卡片行本身不请求）
    expect(listVersions).not.toHaveBeenCalled();
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    expect(listVersions).toHaveBeenCalledTimes(1);
    const text = container.textContent || "";
    expect(text).toContain("版本历史");
    // 当前产物固定行 + 两个历史版本
    expect(container.querySelectorAll(".artifact-version-item")).toHaveLength(3);
    expect(text).toContain("#2");
    expect(text).toContain("#1");
    expect(text).toContain("补写完成并通过密度验收");
    expect(text).toContain("1 文件 · 88 KB");
  });

  it("点击「查看」读的是**版本副本**而不是当前文件（B5）", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    // 版本 #2 只有 card.json；点它的「查看」→ 必须走 /versions/file（version_id + rel），
    // 不能走 previewArtifact(live path)——那会显示「当前」文件内容，旧缺陷。
    const viewBtns = Array.from(container.querySelectorAll(".artifact-version-btn"))
      .filter((b) => (b.textContent || "").includes("查看"));
    await act(async () => { (viewBtns[1] as HTMLButtonElement).click(); });
    expect(previewVersionFile).toHaveBeenCalledWith("D:/w", "002-20260909120000", "玫瑰与繁花/card.json", "");
    expect(previewArtifact).toHaveBeenCalledTimes(1);        // 首屏那次当前产物预览，未被版本复用
    expect(container.textContent).toContain("版本 #2 存档副本");
    expect(container.textContent).toContain('"from": "002-20260909120000"');
  });

  it("看版本副本时「下载/打开位置」被禁用，切回当前后恢复（B5）", async () => {
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    const btnByText = (t: string) => Array.from(container.querySelectorAll(".artifact-modal-foot button"))
      .find((b) => (b.textContent || "").includes(t)) as HTMLButtonElement;
    expect(btnByText("下载").disabled).toBe(false);
    const viewBtns = Array.from(container.querySelectorAll(".artifact-version-btn"))
      .filter((b) => (b.textContent || "").includes("查看"));
    await act(async () => { (viewBtns[1] as HTMLButtonElement).click(); });
    expect(btnByText("下载").disabled).toBe(true);
    expect(btnByText("打开文件位置").disabled).toBe(true);
    // 当前行「查看」= 回到当前产物
    await act(async () => { (viewBtns[0] as HTMLButtonElement).click(); });
    expect(btnByText("下载").disabled).toBe(false);
    expect(container.textContent).not.toContain("存档副本");
  });

  it("回档后回到「当前产物」视图（B5）", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    const viewBtns = Array.from(container.querySelectorAll(".artifact-version-btn"))
      .filter((b) => (b.textContent || "").includes("查看"));
    await act(async () => { (viewBtns[1] as HTMLButtonElement).click(); });
    expect(container.textContent).toContain("存档副本");
    const row = Array.from(container.querySelectorAll(".artifact-version-item"))
      .find((el) => (el.textContent || "").includes("#2")) as HTMLElement;
    const restoreBtn = Array.from(row.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("回档")) as HTMLButtonElement;
    await act(async () => { restoreBtn.click(); });
    expect(restoreVersion).toHaveBeenCalledWith("D:/w", "002-20260909120000", "");
    expect(container.textContent).not.toContain("存档副本");   // 已切回当前产物
    confirmSpy.mockRestore();
  });

  it("点击「回档」确认后调用后端并刷新版本列表", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    // 版本 #2 所在行的「回档」按钮
    const row = Array.from(container.querySelectorAll(".artifact-version-item"))
      .find((el) => (el.textContent || "").includes("#2")) as HTMLElement;
    const restoreBtn = Array.from(row.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("回档")) as HTMLButtonElement;
    await act(async () => { restoreBtn.click(); });
    expect(confirmSpy).toHaveBeenCalled();
    expect(restoreVersion).toHaveBeenCalledWith("D:/w", "002-20260909120000", "");
    expect(container.textContent).toContain("已回档到 #2");
    // 刷新列表：listVersions 被再次调用
    expect(vi.mocked(listVersions).mock.calls.length).toBeGreaterThanOrEqual(2);
    confirmSpy.mockRestore();
  });

  it("点击「删除」确认后调用后端并移除该版本行", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    const row = Array.from(container.querySelectorAll(".artifact-version-item"))
      .find((el) => (el.textContent || "").includes("#1")) as HTMLElement;
    const delBtn = Array.from(row.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("删除")) as HTMLButtonElement;
    await act(async () => { delBtn.click(); });
    expect(confirmSpy).toHaveBeenCalled();
    expect(deleteVersion).toHaveBeenCalledWith("D:/w", "001-20260909103000", "");
    confirmSpy.mockRestore();
  });

  it("取消确认时不调用任何后端", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    const row = Array.from(container.querySelectorAll(".artifact-version-item"))
      .find((el) => (el.textContent || "").includes("#2")) as HTMLElement;
    const delBtn = Array.from(row.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("删除")) as HTMLButtonElement;
    await act(async () => { delBtn.click(); });
    expect(deleteVersion).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

describe("CollectionArtifacts 同步到资产库（手动同步按钮）", () => {
  it("有世界书产物时显示「同步到资产库」按钮，点击确认后调后端", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    const btn = Array.from(container.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("同步到资产库")) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => { btn.click(); });
    expect(confirmSpy).toHaveBeenCalled();
    // 优先用世界书产物路径同步
    expect(syncWorldbook).toHaveBeenCalledWith("D:/w", "D:/w/玫瑰与繁花/worldbook.json", "");
    expect(alertSpy).toHaveBeenCalled();
    expect(String(alertSpy.mock.calls[0][0])).toContain("40");
    confirmSpy.mockRestore();
    alertSpy.mockRestore();
  });

  it("取消确认时不调用同步", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" />);
    const btn = Array.from(container.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("同步到资产库")) as HTMLButtonElement;
    await act(async () => { btn.click(); });
    expect(syncWorldbook).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("无世界书产物时不显示同步按钮", async () => {
    const onlyCard: ArtifactMeta[] = [
      { kind: "card", name: "角色主卡 · 测试", path: "D:/w/测试/card.json", size: 100 },
    ];
    await mount(<CollectionArtifacts artifacts={onlyCard} outputDir="D:/w" />);
    const btn = Array.from(container.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("同步到资产库"));
    expect(btn).toBeUndefined();
  });

  it("文档产物（kind=doc）预览走 Markdown，相对插图解析成可访问素材 URL", async () => {
    vi.mocked(previewArtifact).mockResolvedValueOnce({
      ok: true, name: "设定总集.md", size: 64, kind: "doc",
      text: "# 设定总集\n\n![八千代立绘](assets/a.png)\n\n外貌描述。\n",
      truncated: false,
    });
    const docs: ArtifactMeta[] = [
      { kind: "doc", name: "文档 · 设定总集", path: "D:/w/docs/设定总集.md", size: 64 },
    ];
    await mount(<CollectionArtifacts artifacts={docs} outputDir="D:/w" />);
    const text0 = container.textContent || "";
    // 平铺标题取文档名去扩展名（kind 语义由「文档」徽标承担），而非父目录名「docs」
    expect(text0).toContain("设定总集");
    expect(text0).toContain("文档");      // 类型标签认识 doc
    expect(text0).not.toContain("docs");  // 不再露出父目录名
    expect(text0).toContain("Doc·MD");   // 扩展名徽标认识 doc

    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    const body = container.querySelector(".artifact-modal-markdown");
    // 文档走 Markdown 渲染（不再是纯文本 <pre>），标题与图片都成了 HTML
    expect(body).toBeTruthy();
    expect(container.querySelector(".artifact-modal-pre")).toBeNull();
    const html = body!.innerHTML;
    expect(html).toContain("<h1");
    expect(html).toContain("<img");
    // 相对路径 assets/a.png → 基于文档目录解析为作品域素材 URL（不是原样相对路径）
    expect(html).toContain("http://x/asset?path=");
    expect(html).toContain(encodeURIComponent("D:/w/docs/assets/a.png"));
    expect(html).not.toContain('src="assets/a.png"');
  });
});

describe("CollectionArtifacts repoId 透传（2026-09-10 B3/A）", () => {
  /** 产物域由后端按 repo_id 解析（作品域 = 仓库/小仓库文件夹 ∪ 本作品拥有的卡目录）。
   * 任何一个产物请求漏传 repoId，后端就**不给任何域**（2026-09-10 定案「缺值返回空结果」：
   * 列表回空、单文件 403）→ 产物卡不显示。本用例把「每个端点都带上 repoId」钉死。 */
  it("预览/下载/打开位置/版本查看/回档/删除/同步 全部带 repoId", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    await mount(<CollectionArtifacts artifacts={arts} outputDir="D:/w" repoId="repo-1" />);

    // 1) 打开预览弹层 → 当前产物预览 + 版本列表
    await act(async () => { (container.querySelector(".artifact-tile") as HTMLButtonElement).click(); });
    expect(previewArtifact).toHaveBeenCalledWith("D:/w", "D:/w/玫瑰与繁花/card.json", "repo-1");
    expect(listVersions).toHaveBeenCalledWith("D:/w", "repo-1");

    // 2) 弹层「打开文件位置」
    const openBtn = Array.from(container.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("打开文件位置"));
    await act(async () => { openBtn?.click(); });
    expect(openArtifactFolder).toHaveBeenCalledWith("D:/w", "D:/w/玫瑰与繁花/card.json", true, "repo-1");

    // 3) 版本「查看」→ 版本副本端点
    const viewBtns = Array.from(container.querySelectorAll(".artifact-version-btn"))
      .filter((b) => (b.textContent || "").includes("查看"));
    await act(async () => { (viewBtns[1] as HTMLButtonElement).click(); });
    expect(previewVersionFile).toHaveBeenCalledWith(
      "D:/w", "002-20260909120000", "玫瑰与繁花/card.json", "repo-1");

    // 4) 回档（先切回当前行再点版本 #2 的「回档」）
    await act(async () => { (viewBtns[0] as HTMLButtonElement).click(); });
    const row2 = Array.from(container.querySelectorAll(".artifact-version-item"))
      .find((el) => (el.textContent || "").includes("#2")) as HTMLElement;
    const restoreBtn = Array.from(row2.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("回档")) as HTMLButtonElement;
    await act(async () => { restoreBtn.click(); });
    expect(restoreVersion).toHaveBeenCalledWith("D:/w", "002-20260909120000", "repo-1");

    // 5) 删除版本 #1
    const row1 = Array.from(container.querySelectorAll(".artifact-version-item"))
      .find((el) => (el.textContent || "").includes("#1")) as HTMLElement;
    const delBtn = Array.from(row1.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("删除")) as HTMLButtonElement;
    await act(async () => { delBtn.click(); });
    expect(deleteVersion).toHaveBeenCalledWith("D:/w", "001-20260909103000", "repo-1");

    // 6) 同步到资产库
    await act(async () => { (container.querySelector(".artifact-modal-close") as HTMLButtonElement).click(); });
    const syncBtn = Array.from(container.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("同步到资产库")) as HTMLButtonElement;
    await act(async () => { syncBtn.click(); });
    expect(syncWorldbook).toHaveBeenCalledWith("D:/w", "D:/w/玫瑰与繁花/worldbook.json", "repo-1");

    confirmSpy.mockRestore();
    alertSpy.mockRestore();
  });
});
