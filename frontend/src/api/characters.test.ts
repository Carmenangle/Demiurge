import { describe, expect, it } from "vitest";

import { avatarUrl, expressionUrl } from "./characters";

function localViewPath(url: string): string {
  return new URL(url, "http://localhost").searchParams.get("path") ?? "";
}

describe("角色卡封面 URL", () => {
  it("走 comfyui/local-view 路由（根相对 /local-view 是 404）", () => {
    const url = avatarUrl("/data/user/0/dev.demiurge.spike/files/characters", "占位卡名称");
    expect(new URL(url, "http://localhost").pathname).toBe("/api/comfyui/local-view");
    expect(new URL(expressionUrl("/x/characters", "占位卡名称", "笑.png"), "http://localhost").pathname)
      .toBe("/api/comfyui/local-view");
  });

  it("安卓本地路径用 `/` 拼接，不出现反斜杠", () => {
    const base = "/data/user/0/dev.demiurge.spike/files/characters";
    expect(localViewPath(avatarUrl(base, "占位卡名称")))
      .toBe(`${base}/占位卡名称/avatar.png`);
    expect(localViewPath(expressionUrl(base, "占位卡名称", "笑.png")))
      .toBe(`${base}/占位卡名称/expressions/笑.png`);
  });

  it("Windows 路径保留盘符，反斜杠归一成正斜杠", () => {
    const base = String.raw`C:\Users\me\Demiurge\characters`;
    expect(localViewPath(avatarUrl(base, "卡A")))
      .toBe("C:/Users/me/Demiurge/characters/卡A/avatar.png");
  });

  it("尾部分隔符不产生双斜杠", () => {
    expect(localViewPath(avatarUrl("C:/Users/me/characters/", "卡A")))
      .toBe("C:/Users/me/characters/卡A/avatar.png");
  });
});
