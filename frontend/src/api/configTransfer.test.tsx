import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import {
  configBundleExportUrl, getConfigInventory, importConfigBundle, revealConfigBundle,
  saveConfigBundle,
} from "./configTransfer";
import { API_BASE } from "./client";
import { DeviceSyncPanel } from "../views/settings/DeviceSyncPanel";

const EMPTY_COUNT = { imported: 0, skipped: 0 };
const EMPTY_RESULT = {
  settings_updated: [],
  secrets_included: false,
  assets: {},
  repos: { ...EMPTY_COUNT, items: [] },
  regex: EMPTY_COUNT,
  web_materials: EMPTY_COUNT,
  meta: { version: 2 },
};

describe("离线资源包 wire", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("导出地址是绝对 GET 附件 URL（WebView 只认带 host 的下载地址）", () => {
    for (const include of [false, true]) {
      const raw = configBundleExportUrl(include);
      expect(raw.startsWith("http")).toBe(true);
      const url = new URL(raw);
      expect(url.host).not.toBe("");
      expect(url.pathname).toBe("/api/config/export");
      expect(url.search).toBe(`?include_secrets=${include}`);
    }
  });

  it("PC 导出落盘与打开文件夹走 JSON 端点", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/config/export/save")) {
        return new Response(JSON.stringify({
          path: "D:/Downloads/Demiurge/demiurge-config.zip",
          filename: "demiurge-config.zip",
          bytes: 2048,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ ok: true }),
        { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    const saved = await saveConfigBundle(true);
    expect(saved.filename).toBe("demiurge-config.zip");
    const [saveUrl, saveInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(saveUrl).toBe(`${API_BASE}/config/export/save?include_secrets=true`);
    expect(saveInit.method).toBe("POST");

    expect(await revealConfigBundle(saved.path)).toEqual({ ok: true });
    const [revealUrl, revealInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(revealUrl).toBe(`${API_BASE}/config/reveal`);
    expect(JSON.parse(revealInit.body as string)).toEqual({ path: saved.path });
  });

  it("导入走 multipart 并透传 overwrite", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({
        ...EMPTY_RESULT,
        settings_updated: ["theme"],
        assets: { characters: { imported: 1, skipped: 0 } },
        repos: { imported: 2, skipped: 0, items: [] },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await importConfigBundle(
      new File(["zip"], "cfg.demicfg.zip", { type: "application/zip" }), true);
    expect(result.assets.characters).toEqual({ imported: 1, skipped: 0 });
    expect(result.repos.imported).toBe(2);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/config/import?overwrite=true`);
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("本机资源清单走 GET /config/inventory", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      new Response(JSON.stringify({ counts: { characters: 34, worldbooks: 35 } }),
        { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const data = await getConfigInventory();
    expect(data.counts.characters).toBe(34);
    expect(data.counts.worldbooks).toBe(35);
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${API_BASE}/config/inventory`);
  });

  it("导入失败透出后端 detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "不是 Demiurge 配置包（meta 校验失败）" }),
        { status: 400, headers: { "Content-Type": "application/json" } }),
    ));
    await expect(importConfigBundle(new File(["x"], "x.zip"), false))
      .rejects.toThrow("不是 Demiurge 配置包");
  });
});

describe("DeviceSyncPanel", () => {
  it("入口叫离线资源同步，导出/导入不限定设备，并列出资源范围", () => {
    const html = renderToStaticMarkup(<DeviceSyncPanel />);
    expect(html).toContain("离线资源同步");
    expect(html).toContain("导出配置包");
    expect(html).toContain("导入配置包");
    expect(html).not.toContain("（在电脑上）");
    expect(html).not.toContain("（在手机上）");
    expect(html).toContain("作品仓库");
    expect(html).toContain("全局正则");
    expect(html).toContain("上网素材");
    expect(html).toContain("覆盖本机同名素材");
    expect(html).toContain("本机现有资源");
    expect(html).toContain("完整资源包");
  });
});
