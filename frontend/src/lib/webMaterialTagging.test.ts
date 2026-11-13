// 素材反推编排（2026-11-07）：五档端点路由、GBC 打标后自动生成官方布局，
// 以及打开详情时回读快照/场景图。
// 断的是「编排接对了哪条链」，不是后端行为（后端有各自测试）。
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/ai", () => ({
  WEB_MATERIALS_REPO_ID: "web-materials",
  describeImage: vi.fn(),
  ensureWebMaterialIndex: vi.fn(),
  gbcTagAsset: vi.fn(),
  generateMossLayout: vi.fn(),
  getGbcGraph: vi.fn(),
  listTagSnapshots: vi.fn(),
  setGenerationDescription: vi.fn(),
  tagFromText: vi.fn(),
  tagGeneration: vi.fn(),
}));
vi.mock("./imageData", () => ({
  imageDataUri: vi.fn(async () => "data:image/png;base64,QUJD"),
}));

import {
  describeImage, ensureWebMaterialIndex, gbcTagAsset, generateMossLayout, getGbcGraph,
  listTagSnapshots, setGenerationDescription, tagFromText, tagGeneration,
  type Embed, type WebMaterial,
} from "../api/ai";
import { imageDataUri } from "./imageData";
import {
  AI_TAG_PROMPT, VISION_DESC_PROMPT, VLM_TWO_PARA_PROMPT,
} from "./repoGalleryShared";
import {
  generateMaterialMossLayout, loadMaterialTagView, runMaterialTag,
  type MaterialTagDeps,
} from "./webMaterialTagging";

const embed: Embed = { baseUrl: "http://embed", apiKey: "k", modelName: "embed" };
const vlm = { baseUrl: "http://vlm", apiKey: "k", modelName: "vision" };
const gbc = {
  exePath: "C:/gbc/gbc.exe", sam3Dir: "C:/sam3", embedDir: "C:/embed",
  device: "cuda" as const, maxTopLevel: 10,
};
const deps: MaterialTagDeps = { vlm, gbc, embed, outputDir: "D:/out" };

const item: WebMaterial = {
  path: "out/_web_materials/a.png",
  url: "/api/comfyui/view?path=out%2F_web_materials%2Fa.png",
  source_url: "https://example.com/p/1",
  title: "a",
  filename: "a.png",
  assetId: "asset-1",
};

const gbcOutline = {
  entities: [{ name: "girl", caption: "a girl" }],
  relations: ["girl wears skirt"],
  elapsed_s: 12,
  moss_items: 1,
};
const gbcResult = {
  ok: true, model: "gbc", snapshot_id: "snap-gbc", tags: ["1girl", "skirt"], total: 2,
  outline: gbcOutline, elapsed_s: 30, warning: "",
};
const layoutResult = {
  ok: true,
  layout: {
    summary: { canvas: "1024x1024", caption: "girl in skirt" },
    items: [{ label: "girl", caption: "a girl in a skirt", bbox: [0.5, 0.5, 0.2, 0.4] }],
    bbox_coord_space: "normalized_center",
    bbox_format: "cxcywh",
  },
  parts: {
    style: "anime", caption: "girl in skirt", lighting: "soft",
    cast: [], items: [{ name: "girl", caption: "a girl in a skirt" }],
  },
  model: "vision",
  elapsed_s: 4,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(describeImage).mockResolvedValue({ prompt: "1girl, solo\n\nA girl stands." });
  // 默认：资产记录本来就在（历史素材补登记另有两例）
  vi.mocked(ensureWebMaterialIndex).mockResolvedValue({
    registered: [], already: ["asset-1"], missing: [], failed: [],
  });
});

describe("runMaterialTag · 历史素材补登记", () => {
  it("反推前先按需补登记；已登记的直接放行", async () => {
    vi.mocked(tagGeneration).mockResolvedValue({
      ok: true, model: "wd14", snapshot_id: "s", tags: ["a"], total: 1,
    });
    await runMaterialTag(item, "wd14", deps);
    expect(ensureWebMaterialIndex).toHaveBeenCalledWith("D:/out", ["asset-1"]);
  });

  it("本次补登记成功 ⇒ 继续原来那一档", async () => {
    vi.mocked(ensureWebMaterialIndex).mockResolvedValue({
      registered: ["asset-1"], already: [], missing: [], failed: [],
    });
    vi.mocked(tagGeneration).mockResolvedValue({
      ok: true, model: "wd14", snapshot_id: "s", tags: ["a"], total: 1,
    });
    const r = await runMaterialTag(item, "wd14", deps);
    expect(r.ok).toBe(true);
    expect(tagGeneration).toHaveBeenCalled();
  });

  it("补不上（failed）⇒ 当场止损，不花视觉模型调用", async () => {
    vi.mocked(ensureWebMaterialIndex).mockResolvedValue({
      registered: [], already: [], missing: [], failed: ["asset-1"],
    });
    const r = await runMaterialTag(item, "vlm", deps);
    expect(r.ok).toBe(false);
    expect(r.summary).toContain("没登记进资产库");
    expect(r.summary).toContain("补登记失败");
    expect(describeImage).not.toHaveBeenCalled();
    expect(imageDataUri).not.toHaveBeenCalled();
    expect(tagFromText).not.toHaveBeenCalled();
  });

  it("盘上找不到文件（missing）⇒ 说清是缺文件，不是缺模型", async () => {
    vi.mocked(ensureWebMaterialIndex).mockResolvedValue({
      registered: [], already: [], missing: ["asset-1"], failed: [],
    });
    const r = await runMaterialTag(item, "wd14", deps);
    expect(r.ok).toBe(false);
    expect(r.summary).toContain("找不到这张素材的文件");
    expect(tagGeneration).not.toHaveBeenCalled();
  });
});

describe("runMaterialTag · 五档路由", () => {
  it("wd14：只走本地打标端点，不碰视觉模型", async () => {
    vi.mocked(tagGeneration).mockResolvedValue({
      ok: true, model: "wd14", snapshot_id: "s", tags: ["a", "b"], total: 2,
    });
    const r = await runMaterialTag(item, "wd14", deps);
    expect(tagGeneration).toHaveBeenCalledWith("", "asset-1", "web-materials", embed);
    expect(r).toMatchObject({ ok: true, tags: ["a", "b"], summary: "2 个标签" });
    expect(describeImage).not.toHaveBeenCalled();
    expect(imageDataUri).not.toHaveBeenCalled();
  });

  it("vlm 两段式：描述写回 + 标签存快照（saveDescription=true）", async () => {
    vi.mocked(tagFromText).mockResolvedValue({
      ok: true, snapshot_id: "s", tags: ["1girl"], total: 1,
      description: "A girl stands.", saved_description: true,
    });
    const r = await runMaterialTag(item, "vlm", deps);
    expect(imageDataUri).toHaveBeenCalledWith(item.url);
    expect(describeImage).toHaveBeenCalledWith(["data:image/png;base64,QUJD"], vlm, VLM_TWO_PARA_PROMPT);
    expect(tagFromText).toHaveBeenCalledWith(
      "asset-1", "web-materials", embed, "1girl, solo\n\nA girl stands.",
      { model: "vlm", label: "两段式", saveDescription: true },
    );
    expect(r).toMatchObject({ ok: true, description: "A girl stands.", summary: "1 个标签＋描述" });
  });

  it("ai 纯标签：只出标签、不动描述", async () => {
    vi.mocked(tagFromText).mockResolvedValue({
      ok: true, snapshot_id: "s", tags: ["1girl"], total: 1,
      description: "", saved_description: false,
    });
    const r = await runMaterialTag(item, "ai", deps);
    expect(describeImage).toHaveBeenCalledWith(["data:image/png;base64,QUJD"], vlm, AI_TAG_PROMPT);
    expect(tagFromText).toHaveBeenCalledWith(
      "asset-1", "web-materials", embed, "1girl, solo\n\nA girl stands.",
      { model: "ai", label: "视觉打标", saveDescription: false },
    );
    expect(r).toMatchObject({ ok: true, summary: "1 个标签" });
  });

  it("desc 只写描述：不产标签、不动快照", async () => {
    vi.mocked(setGenerationDescription).mockResolvedValue({ ok: true });
    const r = await runMaterialTag(item, "desc", deps);
    expect(describeImage).toHaveBeenCalledWith(["data:image/png;base64,QUJD"], vlm, VISION_DESC_PROMPT);
    expect(setGenerationDescription).toHaveBeenCalledWith("asset-1", "web-materials", "1girl, solo\n\nA girl stands.", embed);
    expect(tagFromText).not.toHaveBeenCalled();
    expect(tagGeneration).not.toHaveBeenCalled();
    expect(r).toMatchObject({ ok: true, summary: "已写回视觉描述", description: "1girl, solo\n\nA girl stands." });
  });

  it("没有视觉模型时 VLM 档直接失败，不请求图片字节", async () => {
    const r = await runMaterialTag(item, "vlm", { ...deps, vlm: null });
    expect(r.ok).toBe(false);
    expect(r.summary).toContain("视觉模型");
    expect(imageDataUri).not.toHaveBeenCalled();
  });

  it("没有 assetId 时不发任何网络请求", async () => {
    const r = await runMaterialTag({ ...item, assetId: undefined }, "wd14", deps);
    expect(r.ok).toBe(false);
    expect(ensureWebMaterialIndex).not.toHaveBeenCalled();
    expect(tagGeneration).not.toHaveBeenCalled();
  });
});

describe("runMaterialTag · GBC 链", () => {
  it("不再自己写整图描述：种子描述由后端用 WD14 标签生成", async () => {
    vi.mocked(gbcTagAsset).mockResolvedValue(gbcResult);
    vi.mocked(generateMossLayout).mockResolvedValue(layoutResult);
    const r = await runMaterialTag(item, "gbc", deps);
    // 一次视觉模型调用都不在 GBC 打标前发生（布局那次是后端，不走 describeImage）
    expect(describeImage).not.toHaveBeenCalled();
    expect(imageDataUri).not.toHaveBeenCalled();
    expect(gbcTagAsset).toHaveBeenCalledWith("asset-1", "web-materials", gbc, vlm, embed);
    expect(generateMossLayout).toHaveBeenCalledWith("asset-1", vlm);
    expect(r.ok).toBe(true);
    expect(r.tags).toEqual(["1girl", "skirt"]);
    expect(r.gbc?.moss_layout?.layout).toEqual(layoutResult.layout);
    expect(r.summary).toContain("官方布局已生成");
  });

  it("GBC 引擎未配置时止损（不调外部引擎、不生成布局）", async () => {
    const r = await runMaterialTag(item, "gbc", {
      ...deps,
      gbc: { exePath: "", sam3Dir: "", embedDir: "", device: "cuda", maxTopLevel: 10 },
    });
    expect(r.ok).toBe(false);
    expect(r.summary).toContain("GBC 引擎未配置");
    expect(gbcTagAsset).not.toHaveBeenCalled();
    expect(generateMossLayout).not.toHaveBeenCalled();
  });

  it("官方布局失败：保留场景图、返回可重试的错误摘要", async () => {
    vi.mocked(gbcTagAsset).mockResolvedValue(gbcResult);
    vi.mocked(generateMossLayout).mockRejectedValue(new Error("vlm timeout"));
    const r = await runMaterialTag(item, "gbc", deps);
    expect(r.ok).toBe(false);
    expect(r.mossError).toBe("vlm timeout");
    expect(r.gbc?.outline).toEqual(gbcOutline);
    expect(r.gbc?.moss_layout).toBeUndefined();
    expect(r.summary).toContain("重新打开详情会自动重试");
  });
});

describe("loadMaterialTagView / generateMaterialMossLayout", () => {
  it("优先取 active 快照；场景图一并带回", async () => {
    vi.mocked(listTagSnapshots).mockResolvedValue({
      snapshots: [
        { id: "s1", tags: ["old"], model: "wd14", label: "WD14", at: 1 },
        { id: "s2", tags: ["new"], model: "vlm", label: "两段式", at: 2 },
      ],
      active: "s1",
    });
    vi.mocked(getGbcGraph).mockResolvedValue({ ok: true, outline: gbcOutline });
    const view = await loadMaterialTagView("asset-1", embed);
    expect(view.tags).toEqual({ tags: ["old"], label: "WD14" });
    expect(view.gbc?.outline).toEqual(gbcOutline);
  });

  it("active 不在列表里时退回最新一版", async () => {
    vi.mocked(listTagSnapshots).mockResolvedValue({
      snapshots: [
        { id: "s1", tags: ["old"], model: "wd14", label: "WD14", at: 1 },
        { id: "s2", tags: ["new"], model: "vlm", label: "两段式", at: 2 },
      ],
      active: "missing",
    });
    vi.mocked(getGbcGraph).mockResolvedValue({ ok: false });
    const view = await loadMaterialTagView("asset-1", embed);
    expect(view.tags).toEqual({ tags: ["new"], label: "两段式" });
    expect(view.gbc).toBeNull();
  });

  it("补生成官方布局：成功返回 payload，失败返回错误串", async () => {
    vi.mocked(generateMossLayout).mockResolvedValueOnce(layoutResult);
    const ok = await generateMaterialMossLayout("asset-1", vlm);
    expect(ok.mossLayout?.layout).toEqual(layoutResult.layout);
    expect(ok.error).toBeUndefined();

    vi.mocked(generateMossLayout).mockRejectedValueOnce(new Error("boom"));
    const bad = await generateMaterialMossLayout("asset-1", vlm);
    expect(bad.mossLayout).toBeUndefined();
    expect(bad.error).toBe("boom");
  });
});
