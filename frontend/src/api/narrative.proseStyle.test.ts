import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProseStyle, saveProseStyle } from "./narrative";

// 文风配置 api：路径与 payload 合同（S1 收口，属主后端 prose_style）
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

describe("prose style api", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response(JSON.stringify(
      { enabled: true, extra: [], removed: [] },
    ), { status: 200 }));
  });

  it("getProseStyle 走 GET /narrative/prose-style", async () => {
    await getProseStyle();
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/narrative/prose-style");
  });

  it("saveProseStyle POST 逐字段透传", async () => {
    await saveProseStyle({ enabled: false, extra: ["自创套路"], removed: ["赋能"], review_every: 5 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/narrative/prose-style");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      enabled: false, extra: ["自创套路"], removed: ["赋能"], review_every: 5,
    });
  });
});
