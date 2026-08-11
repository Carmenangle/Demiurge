import { describe, it, expect } from "vitest";
import {
  buildHash, calcSize, ASPECTS, IMAGE_QUALITIES, RES_TIERS, supportsImageQuality,
  normalizeCustomDimension, resolveHomeWorkspace, resolveImageSize,
  resolveActivityChatTarget, resolveOpenedWorkRoute,
  SECTION_SUBNAV, WORK_MODES, workspaceModeForWire,
} from "./viewRouting";
import type { Repo } from "../stores/repos";

describe("home workspace", () => {
  it.each(["story", "generate", "code"] as const)("%s mode binds to the selected work chat", (mode) => {
    expect(resolveHomeWorkspace(mode, true)).toBe("chat");
    expect(resolveHomeWorkspace(mode, false)).toBe("need-work");
  });
});

describe("opening a work from assets", () => {
  it.each(["story", "generate", "code"] as const)("keeps %s mode", (mode) => {
    expect(resolveOpenedWorkRoute(mode)).toEqual({ workMode: mode, hash: `#/${mode}` });
  });
});

describe("edit mode", () => {
  it("uses the edit label while preserving the compatible code id", () => {
    expect(WORK_MODES.find((mode) => mode.id === "code")).toMatchObject({
      label: "编辑模式", hint: "角色卡、作品脚本与排错",
    });
    expect(workspaceModeForWire("code")).toBe("edit");
    expect(workspaceModeForWire("story")).toBe("story");
  });
});

describe("system navigation", () => {
  it("places LoRA data beside tools and between model downloads and node management", () => {
    expect(SECTION_SUBNAV.system.map((item) => item.id)).toEqual([
      "models", "lora-data", "node-manager", "tools",
    ]);
  });
});

describe("buildHash", () => {
  it("各视图映射到对应 hash", () => {
    expect(buildHash("home", null)).toBe("#/home");
    expect(buildHash("repos", null)).toBe("#/repos");
    expect(buildHash("workflows", null)).toBe("#/workflows");
    expect(buildHash("models", null)).toBe("#/models");
    expect(buildHash("repo-detail", "abc")).toBe("#/repo/abc");
    expect(buildHash("chat", "abc")).toBe("#/chat/abc");
  });
  it("repo-detail/chat 缺 repoId 回退首页", () => {
    expect(buildHash("repo-detail", null)).toBe("#/home");
    expect(buildHash("chat", null)).toBe("#/home");
  });
});

describe("background activity navigation", () => {
  const repos: Repo[] = [
    { id: "parent", name: "大仓库", createdAt: 1 },
    { id: "work", name: "SAVE01", parentId: "parent", createdAt: 2 },
  ];

  it("selects both parent repository and child work", () => {
    expect(resolveActivityChatTarget(repos, "work")).toEqual({ repoId: "parent", workId: "work" });
  });

  it("rejects missing or top-level targets", () => {
    expect(resolveActivityChatTarget(repos, "missing")).toBeNull();
    expect(resolveActivityChatTarget(repos, "parent")).toBeNull();
  });
});

describe("calcSize", () => {
  it("1:1 各档取基准长边", () => {
    expect(calcSize("1:1", "1k")).toBe("1280x1280");
    expect(calcSize("1:1", "2k")).toBe("2560x2560");
    expect(calcSize("1:1", "4k")).toBe("3840x3840");
  });
  it("横向：最长边按档位，另一边按比例并对齐 16", () => {
    expect(calcSize("2:1", "1k")).toBe("1280x640");
    expect(calcSize("2:1", "2k")).toBe("2560x1280");
    expect(calcSize("2:1", "4k")).toBe("3840x1920");
    expect(calcSize("16:9", "1k")).toBe("1280x720");
    expect(calcSize("16:9", "2k")).toBe("2560x1440");
    expect(calcSize("16:9", "4k")).toBe("3840x2160");
    expect(calcSize("21:9", "4k")).toBe("3840x1648");
  });
  it("纵向比例与标准表一致", () => {
    expect(calcSize("1:2", "1k")).toBe("640x1280");
    expect(calcSize("1:2", "2k")).toBe("1280x2560");
    expect(calcSize("1:2", "4k")).toBe("1920x3840");
    expect(calcSize("3:4", "1k")).toBe("960x1280");
    expect(calcSize("3:4", "2k")).toBe("1920x2560");
    expect(calcSize("3:4", "4k")).toBe("2880x3840");
    expect(calcSize("9:16", "1k")).toBe("720x1280");
    expect(calcSize("9:16", "2k")).toBe("1440x2560");
    expect(calcSize("9:16", "4k")).toBe("2160x3840");
    expect(calcSize("9:21", "1k")).toBe("544x1280");
    expect(calcSize("9:21", "2k")).toBe("1104x2560");
    expect(calcSize("9:21", "4k")).toBe("1648x3840");
  });
  it("所有预设尺寸的宽高都是 16 的倍数", () => {
    for (const aspect of ASPECTS) {
      for (const tier of Object.keys(RES_TIERS)) {
        const [width, height] = calcSize(aspect, tier).split("x").map(Number);
        expect(width % 16, `${aspect} ${tier} width`).toBe(0);
        expect(height % 16, `${aspect} ${tier} height`).toBe(0);
      }
    }
  });
  it("未知档位回退 1280", () => {
    expect(calcSize("1:1", "9k")).toBe("1280x1280");
  });
  it("常量表齐全", () => {
    expect(ASPECTS).toContain("1:1");
    expect(ASPECTS).toContain("1:2");
    expect(ASPECTS).toContain("2:1");
    expect(Object.keys(RES_TIERS)).toEqual(["1k", "2k", "4k"]);
    expect(Object.keys(IMAGE_QUALITIES)).toEqual(["auto", "low", "medium", "high"]);
  });
  it("仅已知 GPT Image 模型启用质量参数", () => {
    expect(supportsImageQuality("gpt-image-2-4k")).toBe(true);
    expect(supportsImageQuality("nano-banana-pro")).toBe(false);
    expect(supportsImageQuality("gemini-3-pro-image-preview")).toBe(false);
    expect(supportsImageQuality("unknown-image-model")).toBe(false);
  });
});

describe("custom image size", () => {
  it("passes an allowed custom size directly to capable providers", () => {
    expect(resolveImageSize("1:1", "1k", true, 1536, 192, true)).toEqual({
      size: "1536x192", mode: "custom", aspect: "1536:192", resTier: "custom",
    });
  });

  it("maps custom dimensions to the nearest preset for unsupported providers", () => {
    expect(resolveImageSize("1:1", "4k", true, 1920, 1080, false)).toEqual({
      size: "2560x1440", mode: "fallback", aspect: "16:9", resTier: "2k",
    });
  });

  it("enforces the custom dimension bounds", () => {
    expect(normalizeCustomDimension(32)).toBe(64);
    expect(normalizeCustomDimension(5000)).toBe(3840);
    expect(normalizeCustomDimension("bad", 1024)).toBe(1024);
  });

  it("aligns custom dimensions to multiples of 16", () => {
    expect(normalizeCustomDimension(1672)).toBe(1680);
    expect(normalizeCustomDimension(941)).toBe(944);
    expect(resolveImageSize("1:1", "1k", true, 1537, 193, true)).toMatchObject({
      size: "1536x192", mode: "custom",
    });
  });
});
