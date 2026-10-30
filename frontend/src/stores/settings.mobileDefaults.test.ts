// @vitest-environment jsdom
// 手机默认嵌入（2026-10-30 用户定案）：手机上「本机 Ollama」不存在，默认应是云端 API 空档；
// 电脑构建保持原有本机 Ollama 默认（VITE_MOBILE_PROFILE 未设）。
import { afterEach, describe, expect, it, vi } from "vitest";

class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() { return this.map.size; }
  clear() { this.map.clear(); }
  getItem(k: string) { return this.map.get(k) ?? null; }
  key(i: number) { return [...this.map.keys()][i] ?? null; }
  removeItem(k: string) { return this.map.delete(k); }
  setItem(k: string, v: string) { this.map.set(k, String(v)); }
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("默认模型配置随构建形态", () => {
  it("手机默认云端嵌入空档（不是本机 Ollama 回环）", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_MOBILE_PROFILE", "1");
    const { loadPersistedSettings } = await import("./settings");

    const s = loadPersistedSettings(new MemoryStorage());

    expect(s.embedModel.mode).toBe("remote");
    expect(s.embedModel.source).toBe("api");
    expect(s.embedModel.baseUrl).toBe("");
    expect(s.embedModel.modelName).toBe("");
    expect(s.vlmModel.source).toBe("ollama"); // 视觉默认本次不动
  });

  it("电脑默认仍是本机 Ollama", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_MOBILE_PROFILE", "0");
    const { loadPersistedSettings } = await import("./settings");

    const s = loadPersistedSettings(new MemoryStorage());

    expect(s.embedModel.source).toBe("ollama");
    expect(s.embedModel.baseUrl).toBe("http://localhost:11434/v1");
  });
});
