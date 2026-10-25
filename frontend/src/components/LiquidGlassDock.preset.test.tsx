// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fetchWallpaperSceneProperties, type ScenePropertyItem, type ScenePropertyValues } from "../api/userState";
import { publishSceneBindings } from "../lib/scene/sceneBindings";
import type { LiquidGlassWallpaper } from "../stores/settings";
import { ScenePresetPanel } from "./LiquidGlassDock";

vi.mock("../api/userState", () => ({
  fetchWallpaperSceneProperties: vi.fn(),
  getWallpaperStore: vi.fn(),
  migrateWallpaperStore: vi.fn(),
  scanWallpapers: vi.fn(),
  scanWorkshopWallpapers: vi.fn(),
  uploadWallpaper: vi.fn(),
  wallpaperThumbUrl: vi.fn(() => "thumb"),
}));
vi.mock("./LiquidGlassWallpaperLibrary", () => ({ LiquidGlassWallpaperLibrary: () => null }));

const mockedFetch = vi.mocked(fetchWallpaperSceneProperties);

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const WALLPAPER: LiquidGlassWallpaper = {
  id: "wp-1",
  path: "D:/SteamLibrary/steamapps/workshop/content/431960/3346715292/scene.pkg",
  name: "八奈见杏菜——夜莺Night",
  kind: "image",
  scenePkg: true,
  sceneSource: "workshop:3346715292",
};

function item(over: Partial<ScenePropertyItem> & { key: string; type: ScenePropertyItem["type"] }): ScenePropertyItem {
  return {
    label: over.key, order: 1, condition: null, default: 0, value: 0, we_overridden: false,
    ...over,
  } as ScenePropertyItem;
}

const PROPERTIES: ScenePropertyItem[] = [
  item({
    key: "display", type: "combo", label: "时间选择 Timing", order: 114,
    default: "0", value: "0",
    options: [{ label: "白天 Day", value: "0" }, { label: "夜晚 Night", value: "1" }],
  }),
  item({
    key: "hrbigb", type: "combo", label: "人物 character", order: 115,
    default: "0", value: "0",
    options: [{ label: "人物1", value: "0" }, { label: "人物2", value: "1" }, { label: "不显示", value: "2" }],
  }),
  item({
    key: "newproperty10", type: "slider", label: "云层运动速度", order: 124,
    default: 0.5, value: 0.02, min: 0.01, max: 1, step: 0.001, precision: 4,
  }),
  // textinput（「可自定义文字」的正文）：面板摆单行输入，值原样进差集
  item({
    key: "newproperty55", type: "textinput", label: "自定义文字", order: 139,
    default: "Bilibili/抖音 夜莺Night", value: "Bilibili/抖音 夜莺Night",
  }),
  // 渲染器不消费的键（作者留在工程里的文字开关）不该进面板
  item({ key: "shu", type: "bool", label: "鼠标尾迹开关", order: 130, default: false, value: false }),
];

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount(el: ReactElement) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root?.render(el); });
  return host;
}

afterEach(() => {
  const current = root;
  root = null;
  if (current) act(() => { current.unmount(); });
  host?.remove();
  host = null;
});

beforeEach(() => {
  mockedFetch.mockReset();
  mockedFetch.mockResolvedValue({
    source: WALLPAPER.sceneSource!, title: "T", folder: "F", pkg: "P",
    found: true, we_config: "", properties: PROPERTIES,
  });
});

/** React 用值追踪去重，直接赋值会被当成「没变」；必须走原生 setter 再发事件。 */
function setValue(el: HTMLInputElement | HTMLSelectElement, next: string, event: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  act(() => {
    setter?.call(el, next);
    el.dispatchEvent(new Event(event, { bubbles: true }));
  });
}

/** tsconfig 的 lib 早于 es2022，用索引取最后一次回调实参。 */
function last(list: ScenePropertyValues[]): ScenePropertyValues | undefined {
  return list[list.length - 1];
}

describe("壁纸预设面板", () => {
  it("清单就绪后按 userBindings 摆控件，初值是 WE 生效值", async () => {
    publishSceneBindings(WALLPAPER.sceneSource!, ["display", "hrbigb", "newproperty10"]);
    const host = await mount(<ScenePresetPanel wallpaper={WALLPAPER} onChange={vi.fn()} />);
    const text = host.textContent || "";
    expect(text).toContain("时间选择 Timing");
    expect(text).toContain("人物 character");
    expect(text).toContain("云层运动速度");
    expect(text).not.toContain("鼠标尾迹开关");
    const selects = host.querySelectorAll("select");
    expect(selects).toHaveLength(2);
    expect(selects[0].value).toBe("0");
    expect(host.querySelector('input[type="range"]')?.getAttribute("step")).toBe("0.001");
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it("切换 combo 只落「相对 WE 的偏离量」，改回原样即清空", async () => {
    publishSceneBindings("workshop:3346715293", ["display"]);
    const changes: ScenePropertyValues[] = [];
    const host = await mount(
      <ScenePresetPanel
        wallpaper={{ ...WALLPAPER, sceneSource: "workshop:3346715293" }}
        onChange={(next) => changes.push(next)}
      />,
    );
    const select = host.querySelector("select") as HTMLSelectElement;
    setValue(select, "1", "change");
    expect(last(changes)).toEqual({ display: "1" });
    setValue(select, "0", "change");
    expect(last(changes)).toEqual({});
  });

  it("滑杆改动按 precision 收敛后落盘，并带上已有偏离", async () => {
    publishSceneBindings("workshop:3346715294", ["newproperty10", "shu"]);
    const changes: ScenePropertyValues[] = [];
    const host = await mount(
      <ScenePresetPanel
        wallpaper={{ ...WALLPAPER, sceneSource: "workshop:3346715294", preset: { shu: true } }}
        onChange={(next) => changes.push(next)}
      />,
    );
    const range = host.querySelector('input[type="range"]') as HTMLInputElement;
    expect(range.getAttribute("min")).toBe("0.01");
    expect(range.getAttribute("max")).toBe("1");
    setValue(range, "0.30000000000000004", "input");
    expect(last(changes)).toEqual({ shu: true, newproperty10: 0.3 });
  });

  it("textinput（自定义文字内容）摆单行输入，编辑进差集", async () => {
    // id 不得与其它用例复用：publishSceneBindings 是模块级注册表，撞号会互相覆盖
    publishSceneBindings("workshop:3346715298", ["newproperty55"]);
    const changes: ScenePropertyValues[] = [];
    const host = await mount(
      <ScenePresetPanel
        wallpaper={{ ...WALLPAPER, sceneSource: "workshop:3346715298" }}
        onChange={(next) => changes.push(next)}
      />,
    );
    const input = host.querySelector('input[type="text"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    expect(input.value).toBe("Bilibili/抖音 夜莺Night");
    // React 的 text input onChange 挂在 input 事件上（与滑杆用例同口径）。
    // 「改回原样即清空」不在本用例测：面板受控于父组件，测试 spy 不回写时
    // 同值二连发会被 React 值追踪去重——该语义由 scenePresets.test.ts 纯函数覆盖。
    setValue(input, "夜莺 Night 二号", "input");
    expect(last(changes)).toEqual({ newproperty55: "夜莺 Night 二号" });
  });

  it("已有偏离会盖过 WE 初值显示，并给出「还原 WE 原样」入口", async () => {
    publishSceneBindings("workshop:3346715295", ["hrbigb"]);
    const changes: ScenePropertyValues[] = [];
    const host = await mount(
      <ScenePresetPanel
        wallpaper={{ ...WALLPAPER, sceneSource: "workshop:3346715295", preset: { hrbigb: "2" } }}
        onChange={(next) => changes.push(next)}
      />,
    );
    expect((host.querySelector("select") as HTMLSelectElement).value).toBe("2");
    const reset = Array.from(host.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("还原 WE 原样"));
    act(() => reset?.click());
    expect(last(changes)).toEqual({});
  });

  it("实时渲染还没就绪时不摆控件，读不到预设时给错误与重试", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("作品 project.json 不可读"));
    publishSceneBindings("workshop:3346715296", []);
    const host = await mount(
      <ScenePresetPanel wallpaper={{ ...WALLPAPER, sceneSource: "workshop:3346715296" }} onChange={vi.fn()} />,
    );
    expect(host.textContent).toContain("作品 project.json 不可读");
    expect(host.querySelectorAll("select")).toHaveLength(0);
    const retry = Array.from(host.querySelectorAll("button"))
      .find((b) => (b.textContent || "").includes("重试"));
    expect(retry).toBeTruthy();
    await act(async () => { retry?.click(); });
    expect(mockedFetch).toHaveBeenCalledTimes(2);
    expect(host.textContent).not.toContain("不可读");

    // 清单未解出（快照还在读）时同样不该出现控件
    publishSceneBindings("workshop:3346715297", []);
    const pending = new Promise<Awaited<ReturnType<typeof fetchWallpaperSceneProperties>>>(() => { /* 永不 resolve */ });
    mockedFetch.mockReturnValue(pending);
    const host2 = await mount(
      <ScenePresetPanel wallpaper={{ ...WALLPAPER, sceneSource: "workshop:3346715297" }} onChange={vi.fn()} />,
    );
    expect(host2.textContent).toContain("正在读取该作品的预设");
  });

  it("非场景壁纸（图片/视频）不该走到这里：空来源时不发请求", async () => {
    const host = await mount(
      <ScenePresetPanel
        wallpaper={{ ...WALLPAPER, scenePkg: false, sceneSource: undefined }}
        onChange={vi.fn()}
      />,
    );
    expect(mockedFetch).not.toHaveBeenCalled();
    expect(host.querySelector("select")).toBeNull();
  });
});
