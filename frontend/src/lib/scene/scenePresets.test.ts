import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchWallpaperSceneProperties,
  type ScenePropertyItem,
  type ScenePropertyValues,
} from "../../api/userState";
import {
  filterScenePresetItems, hexToSceneColor, loadScenePresets, roundSceneNumber,
  sameScenePropertyValue, sceneColorToHex, scenePresetDeviation, scenePresetValues,
  scenePropertyVisible,
} from "./scenePresets";

vi.mock("../../api/userState", () => ({
  fetchWallpaperSceneProperties: vi.fn(),
}));

const mockedFetch = vi.mocked(fetchWallpaperSceneProperties);

function item(over: Partial<ScenePropertyItem> & { key: string }): ScenePropertyItem {
  return {
    label: over.key,
    order: 1,
    condition: null,
    default: 0,
    value: 0,
    we_overridden: false,
    type: "slider",
    ...over,
  } as ScenePropertyItem;
}

function page(properties: ScenePropertyItem[]) {
  return {
    source: "s", title: "T", folder: "F", pkg: "P", found: true, we_config: "",
    properties,
  };
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("WE 预设快照", () => {
  it("base 取 WE 生效值（含用户在 WE 里的覆盖），不是工程默认值", async () => {
    mockedFetch.mockResolvedValue(page([
      item({ key: "display", type: "combo", default: "0", value: "1", options: [] }),
      item({ key: "speed", type: "slider", default: 0.5, value: 0.02, min: 0, max: 1 }),
    ]));
    const snapshot = await loadScenePresets("workshop:101");
    expect(snapshot.base).toEqual({ display: "1", speed: 0.02 });
    expect(scenePresetValues(snapshot.base, { speed: 0.9 })).toEqual({ display: "1", speed: 0.9 });
  });

  it("同一来源只请求一次；force 才重新读（面板与壁纸层共用一份事实）", async () => {
    mockedFetch.mockResolvedValue(page([]));
    await Promise.all([loadScenePresets("workshop:102"), loadScenePresets("workshop:102")]);
    await loadScenePresets("workshop:102");
    expect(mockedFetch).toHaveBeenCalledTimes(1);
    await loadScenePresets("workshop:102", { force: true });
    expect(mockedFetch).toHaveBeenCalledTimes(2);
  });

  it("读取失败不留在缓存里，下一次仍会重试（project.json 缺席不该永久空白）", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("作品 project.json 不可读"));
    await expect(loadScenePresets("workshop:103")).rejects.toThrow("project.json");
    mockedFetch.mockResolvedValue(page([item({ key: "a" })]));
    const snapshot = await loadScenePresets("workshop:103");
    expect(snapshot.items).toHaveLength(1);
  });
});

describe("预设控件换算", () => {
  it("颜色在 WE 的分量串与控件 hex 之间换算，并按原刻度回写", () => {
    // 0~255 写法（粒子色带 / 预览图色标）
    expect(sceneColorToHex("255 128 0")).toBe("#ff8000");
    expect(sceneColorToHex("255,128,0")).toBe("#ff8000");
    expect(sceneColorToHex([10, 20, 30])).toBe("#0a141e");
    expect(sceneColorToHex("#0a141e")).toBe("#0a141e");
    expect(sceneColorToHex("#abc")).toBe("#aabbcc");
    expect(sceneColorToHex("")).toBe("#ffffff");
    expect(sceneColorToHex("not a color")).toBe("#ffffff");
    expect(sceneColorToHex("300 -12 8")).toBe("#ff0008");
    // 0~1 写法（绑到 shader tint 的颜色，本张壁纸就是这么存的）
    expect(sceneColorToHex("0 0.9254901960784314 1")).toBe("#00ecff");
    expect(sceneColorToHex("0.403921568627451 0.403921568627451 0.403921568627451")).toBe("#676767");
    expect(hexToSceneColor("#ff8000", "255 128 0")).toBe("255 128 0");
    expect(hexToSceneColor("#00ecff", "0 0.9254901960784314 1")).toBe("0 0.92549 1");
    expect(hexToSceneColor("255 128 0")).toBe("255 128 0");
  });

  it("滑杆按 precision 收敛，避免存下浮点噪声", () => {
    const fine = item({ key: "x", type: "slider", precision: 4, step: 0.001 });
    expect(roundSceneNumber(fine, 0.30000000000000004)).toBe(0.3);
    expect(roundSceneNumber(fine, 43.81700000001)).toBe(43.817);
    expect(roundSceneNumber(item({ key: "y", type: "slider", precision: 0 }), 7.6)).toBe(8);
    expect(roundSceneNumber(item({ key: "z", type: "slider" }), Number.NaN)).toBeNaN();
  });

  it("改回 WE 原样即删除该键；其他类型按值等价判定", () => {
    const base: ScenePropertyValues = { display: "0", speed: 0.5, on: true, tint: "0 0 0" };
    const combo = item({ key: "display", type: "combo" });
    const slider = item({ key: "speed", type: "slider" });
    const bool = item({ key: "on", type: "bool" });
    const color = item({ key: "tint", type: "color" });
    expect(scenePresetDeviation({}, combo, "1", base)).toEqual({ display: "1" });
    expect(scenePresetDeviation({ display: "1" }, combo, "0", base)).toEqual({});
    expect(scenePresetDeviation({}, slider, 0.5, base)).toEqual({});
    expect(scenePresetDeviation({}, bool, false, base)).toEqual({ on: false });
    // 同一颜色的不同写法不该被记成「用户改过」（含 0~1 tint 的换算误差）
    expect(scenePresetDeviation({}, color, "#000000", base)).toEqual({});
    expect(scenePresetDeviation(
      {}, color, "0 0.92549 1", { tint: "0 0.9254901960784314 1" },
    )).toEqual({});
    // base 里没有的键（WE 没写过默认值）必须照实存下
    expect(scenePresetDeviation({}, item({ key: "new" }), 3, base)).toEqual({ new: 3 });
  });
});

describe("预设的条件显示与有效键收窄", () => {
  const items = [
    item({ key: "display", type: "combo", order: 1 }),
    item({ key: "time", type: "bool", order: 2 }),
    item({ key: "clock24", type: "bool", order: 3, condition: "time.value" }),
    item({ key: "font", type: "combo", order: 4, condition: "display.value == 1" }),
    item({ key: "trail", type: "slider", order: 5, condition: "a.value && b.value" }),
  ];

  it("只摆 userBindings 里的键：改了不影响画面的属性不进面板", () => {
    const rows = filterScenePresetItems(items, ["display", "time"], {});
    expect(rows.map((r) => r.key)).toEqual(["display", "time"]);
  });

  it("清单还没解出来（bindings=null）时一个控件都不给", () => {
    expect(filterScenePresetItems(items, null, { display: "1", time: true })).toEqual([]);
  });

  it("子选项跟着开关出现/消失，认不出的表达式照常显示", () => {
    const values: ScenePropertyValues = { display: "0", time: false };
    expect(filterScenePresetItems(items, ["display", "time", "clock24", "font", "trail"], values)
      .map((r) => r.key)).toEqual(["display", "time", "trail"]);
    expect(scenePropertyVisible({ condition: "display.value == 1" }, values)).toBe(false);
    expect(scenePropertyVisible({ condition: "display.value != 1" }, { display: 1 })).toBe(false);
    expect(scenePropertyVisible({ condition: "display.value" }, { display: "1" })).toBe(true);
    expect(scenePropertyVisible({ condition: "unknown_key.value" }, {})).toBe(false);
    expect(scenePropertyVisible({ condition: "" }, {})).toBe(true);
  });
});

describe("scenePresetDeviation · textinput（自定义文字内容）", () => {
  const item = {
    key: "newproperty55", type: "textinput" as const, label: "自定义文字", order: 139,
    condition: null, default: "Bilibili/抖音 夜莺Night", value: "Bilibili/抖音 夜莺Night",
    we_overridden: false,
  };
  const base = { newproperty55: "Bilibili/抖音 夜莺Night" };

  it("改动进差集；改回原样即清空（空串也是合法偏离）", () => {
    const d1 = scenePresetDeviation({}, item, "夜莺 Night 二号", base);
    expect(d1).toEqual({ newproperty55: "夜莺 Night 二号" });
    const d2 = scenePresetDeviation(d1, item, "Bilibili/抖音 夜莺Night", base);
    expect(d2).toEqual({});
    const d3 = scenePresetDeviation({}, item, "", base);
    expect(d3).toEqual({ newproperty55: "" });
  });

  it("sameScenePropertyValue 字符串语义：数字/字符串同形不算改", () => {
    const comboItem = { ...item, type: "combo" as const };
    expect(sameScenePropertyValue(comboItem, "1", 1)).toBe(true);
    expect(sameScenePropertyValue(item, "a", "a ")).toBe(false);
  });
});
