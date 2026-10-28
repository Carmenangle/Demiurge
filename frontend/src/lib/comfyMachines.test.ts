import { describe, expect, it } from "vitest";
import {
  DEFAULT_MACHINE_ID,
  diffComfyHosts,
  isLoopbackComfyUrl,
  listMachines,
  machineLabelOf,
  resolveComfyUrl,
  sameComfyUrl,
} from "./comfyMachines";
import type { Settings } from "../stores/settings";

const base = {
  comfyuiUrl: "http://127.0.0.1:8188",
  comfyExtra: [],
  comfyRoute: {},
} as unknown as Settings;

const twoMachines = {
  comfyuiUrl: "http://127.0.0.1:8188",
  comfyExtra: [
    { id: "cloud", name: "云 A100", url: "https://cloud.example.com:8188" },
    { id: "lan", name: "3080 台式机", url: "http://192.168.1.9:8188" },
  ],
  comfyRoute: { video: "cloud", image: "lan" },
} as unknown as Settings;

describe("listMachines", () => {
  it("条目 0 就是 settings.comfyuiUrl，不在 comfyExtra 里重复", () => {
    const list = listMachines(twoMachines);
    expect(list.map((m) => m.id)).toEqual([DEFAULT_MACHINE_ID, "cloud", "lan"]);
    expect(list[0].url).toBe("http://127.0.0.1:8188");
  });

  it("脏条目过滤：缺 id / 空 url / 非字符串 / id 撞条目 0", () => {
    const list = listMachines({
      comfyuiUrl: "http://127.0.0.1:8188",
      comfyExtra: [
        { id: "", name: "空 id", url: "http://a:8188" },
        { id: "x", name: "空地址", url: "  " },
        { id: "default", name: "撞条目 0", url: "http://b:8188" },
        { id: "x", name: "重复 id", url: "http://c:8188" },
        { id: "ok", name: "", url: "http://d:8188" },
      ],
    } as unknown as Settings);
    expect(list.map((m) => m.id)).toEqual([DEFAULT_MACHINE_ID, "x", "ok"]);
    expect(list.find((m) => m.id === "x")?.url).toBe("http://c:8188");
    expect(list.find((m) => m.id === "ok")?.name).toBe("http://d:8188");
  });

  it("comfyExtra 缺失（旧设置）退化成只有条目 0", () => {
    const list = listMachines({ comfyuiUrl: "http://127.0.0.1:8188" } as Settings);
    expect(list).toHaveLength(1);
  });
});

describe("resolveComfyUrl", () => {
  it("没配路由 ⇒ 条目 0（与改造前 settings.comfyuiUrl 等价）", () => {
    expect(resolveComfyUrl(base)).toBe("http://127.0.0.1:8188");
    expect(resolveComfyUrl(base, "video")).toBe("http://127.0.0.1:8188");
    expect(resolveComfyUrl(twoMachines, "audio")).toBe("http://127.0.0.1:8188");
  });

  it("按模态选机，返回的是那台机的具体 URL", () => {
    expect(resolveComfyUrl(twoMachines, "video")).toBe("https://cloud.example.com:8188");
    expect(resolveComfyUrl(twoMachines, "image")).toBe("http://192.168.1.9:8188");
  });

  it("路由指向已删除的 id ⇒ 回退条目 0（删条目不断生成）", () => {
    const stale = {
      comfyuiUrl: "http://127.0.0.1:8188",
      comfyExtra: [],
      comfyRoute: { video: "gone" },
    } as unknown as Settings;
    expect(resolveComfyUrl(stale, "video")).toBe("http://127.0.0.1:8188");
  });

  it("地址为空 ⇒ 回空串（调用方沿用既有 `|| \"\"` 语义）", () => {
    expect(resolveComfyUrl({ comfyuiUrl: "" } as Settings, "image")).toBe("");
  });
});

describe("machineLabelOf", () => {
  it("命中的机台返回名字", () => {
    expect(machineLabelOf(twoMachines, "https://cloud.example.com:8188")).toBe("云 A100");
  });

  it("尾斜杠/大小写差异不影响命中", () => {
    expect(machineLabelOf(twoMachines, "HTTPS://Cloud.Example.com:8188/")).toBe("云 A100");
  });

  it("反查不到原样返回 URL（历史里可能是已删除的机台）", () => {
    expect(machineLabelOf(twoMachines, "http://10.0.0.9:8188")).toBe("http://10.0.0.9:8188");
  });

  it("空 URL 回空串；条目 0 不冠名字，回 URL 本身", () => {
    expect(machineLabelOf(twoMachines, "")).toBe("");
    expect(machineLabelOf(twoMachines, "http://127.0.0.1:8188")).toBe("http://127.0.0.1:8188");
  });
});

describe("isLoopbackComfyUrl", () => {
  it("回环三形态 + 空串（未配置等于本机）都算回环", () => {
    expect(isLoopbackComfyUrl("http://127.0.0.1:8188")).toBe(true);
    expect(isLoopbackComfyUrl("http://localhost:8188")).toBe(true);
    expect(isLoopbackComfyUrl("http://[::1]:8188")).toBe(true);
    expect(isLoopbackComfyUrl("127.0.0.1:8188")).toBe(true);
    expect(isLoopbackComfyUrl("")).toBe(true);
  });

  it("局域网/云端都是非回环", () => {
    expect(isLoopbackComfyUrl("http://192.168.1.9:8188")).toBe(false);
    expect(isLoopbackComfyUrl("https://cloud.example.com:8188")).toBe(false);
  });
});

describe("diffComfyHosts（保存时白名单对账）", () => {
  const settingsOf = (urls: string[], comfyuiUrl = "http://127.0.0.1:8188") =>
    ({
      comfyuiUrl,
      comfyExtra: urls.map((url, i) => ({ id: `m${i}`, name: "", url })),
    }) as unknown as Settings;

  it("新增 / 删除各归各的集合", () => {
    const diff = diffComfyHosts(
      settingsOf(["http://a:8188"]),
      settingsOf(["http://b:8188"]),
    );
    expect(diff.added).toEqual(["http://b:8188"]);
    expect(diff.removed).toEqual(["http://a:8188"]);
  });

  it("尾斜杠 / 大小写差异不算增删（别为同一台机反复登记）", () => {
    const diff = diffComfyHosts(
      settingsOf(["http://a:8188"]),
      settingsOf(["HTTP://A:8188/"]),
    );
    expect(diff).toEqual({ added: [], removed: [] });
  });

  it("与默认地址同址的额外条目既不登记也不注销（删重复条目不能拆掉默认机的白名单）", () => {
    const head = "http://192.168.1.9:8188";
    const diff = diffComfyHosts(
      settingsOf([head], head),
      settingsOf([], head),
    );
    expect(diff).toEqual({ added: [], removed: [] });
  });

  it("同一地址填了两条只算一次", () => {
    const diff = diffComfyHosts(
      settingsOf([]),
      settingsOf(["http://a:8188", "http://a:8188/"]),
    );
    expect(diff.added).toEqual(["http://a:8188"]);
  });
});

describe("sameComfyUrl", () => {
  it("尾斜杠 / 大小写 / 空白差异算同一台（帧图不用跨机搬运）", () => {
    expect(sameComfyUrl("http://image:8188", "http://image:8188/")).toBe(true);
    expect(sameComfyUrl("HTTP://IMAGE:8188", " http://image:8188 ")).toBe(true);
  });

  it("主机或端口不同算两台（必须把帧图字节传过去）", () => {
    expect(sameComfyUrl("http://image:8188", "http://video:8188")).toBe(false);
    expect(sameComfyUrl("http://image:8188", "http://image:8189")).toBe(false);
  });
});
