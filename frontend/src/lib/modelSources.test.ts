import { describe, expect, it } from "vitest";
import { OLLAMA_DEFAULT_BASE_URL, type EmbedModel, type VlmModel } from "../stores/settings";
import {
  LOCAL_CHAT_PLACEHOLDER, applyChatLocalImport, applyVlmLocalImport, isLocalChatPlaceholder,
  newChatModel, switchEmbedSource, switchRemoteSource, switchVlmSource,
} from "./modelSources";

describe("newChatModel：三个来源各自成型", () => {
  it("API 卡只给占位模型名，不预填本机地址", () => {
    const model = newChatModel("api");

    expect(model.source).toBe("api");
    expect(model.baseUrl).toBe("");
    expect(model.apiKey).toBe("");
    expect(model.modelName).toBe("新模型");
    expect(model.id).toBeTruthy();
  });

  it("Ollama 卡预填本机地址、占位 Key 与直连", () => {
    const model = newChatModel("ollama");

    expect(model.source).toBe("ollama");
    expect(model.baseUrl).toBe(OLLAMA_DEFAULT_BASE_URL);
    expect(model.apiKey).toBe("ollama");
    expect(model.proxyMode).toBe("off");
    expect(model.modelName).toBe("");
  });

  it("本地卡以占位名起步，等 GGUF 导入后再填模型名", () => {
    const model = newChatModel("local");

    expect(model.source).toBe("local");
    expect(model.baseUrl).toBe(OLLAMA_DEFAULT_BASE_URL);
    expect(model.displayName).toBe(LOCAL_CHAT_PLACEHOLDER);
    expect(isLocalChatPlaceholder(model.displayName)).toBe(true);
    expect(model.ggufPath).toBe("");
    expect(model.modelName).toBe("");
  });
});

describe("applyChatLocalImport：导入成功后的运行时形态", () => {
  const base = { id: "c", displayName: LOCAL_CHAT_PLACEHOLDER, source: "local" as const, apiKey: "", baseUrl: "", modelName: "", ggufPath: "D:/m.gguf" };

  it("端点固定为本机 Ollama，来源保持 local 以便重新导入", () => {
    const patch = applyChatLocalImport(base, "qwen3-8b:latest");

    expect(patch.source).toBe("local");
    expect(patch.baseUrl).toBe(OLLAMA_DEFAULT_BASE_URL);
    expect(patch.apiKey).toBe("ollama");
    expect(patch.modelName).toBe("qwen3-8b:latest");
    expect(patch.proxyMode).toBe("off");
  });

  it("占位名自动换成真实模型名；用户手填的显示名不被覆盖", () => {
    expect(applyChatLocalImport(base, "qwen3-8b:latest").displayName).toBe("qwen3-8b:latest");
    expect(applyChatLocalImport({ ...base, displayName: "我的本地模型" }, "qwen3-8b:latest"))
      .not.toHaveProperty("displayName");
    expect(isLocalChatPlaceholder("")).toBe(true);
    expect(isLocalChatPlaceholder("   ")).toBe(true);
  });
});

describe("applyVlmLocalImport：视觉本地导入后自动落到 Ollama 档", () => {
  it("切 remote + source ollama，避免用户再手动切页", () => {
    const patch = applyVlmLocalImport("qwen3vl:8b");

    expect(patch).toEqual({
      mode: "remote",
      source: "ollama",
      baseUrl: OLLAMA_DEFAULT_BASE_URL,
      apiKey: "ollama",
      modelName: "qwen3vl:8b",
      proxyMode: "off",
    });
  });
});

describe("switchRemoteSource：切来源只改必要的连接字段", () => {
  it("云端地址切到 Ollama ⇒ 换成本机默认地址并清掉云端模型名", () => {
    const patch = switchRemoteSource("ollama", {
      source: "api", baseUrl: "https://api.openai.com/v1", apiKey: "sk-x", modelName: "gpt-4o",
    });

    expect(patch).toEqual({
      source: "ollama", baseUrl: OLLAMA_DEFAULT_BASE_URL, apiKey: "ollama", modelName: "", proxyMode: "off",
    });
  });

  it("Ollama 地址切回 API ⇒ 清空连接字段，不把本机地址当云端 API 保存", () => {
    const patch = switchRemoteSource("api", {
      source: "ollama", baseUrl: OLLAMA_DEFAULT_BASE_URL, apiKey: "ollama", modelName: "qwen3:8b",
    });

    expect(patch).toEqual({
      source: "api", baseUrl: "", apiKey: "", modelName: "", proxyMode: "inherit",
    });
  });

  it("当前地址是自定义 Ollama 端口 ⇒ 保留用户手填地址，只换来源", () => {
    const patch = switchRemoteSource("ollama", {
      source: "api", baseUrl: "http://192.168.1.9:11434/v1", apiKey: "ollama", modelName: "qwen3:8b",
    });

    expect(patch).toEqual({ source: "ollama" });
  });

  it("重复点同一来源不动任何字段", () => {
    expect(switchRemoteSource("api", {
      source: "api", baseUrl: "https://api.test/v1", apiKey: "sk", modelName: "m",
    })).toEqual({ source: "api" });
  });
});

describe("switchEmbedSource / switchVlmSource：任何远程档都回到 remote 模式", () => {
  it("嵌入从 local 模式切到 API/Ollama 都落 mode=remote", () => {
    const local: EmbedModel = {
      mode: "local", apiKey: "", baseUrl: "", modelName: "", modelDir: "D:/embed", proxyMode: "on",
    };

    expect(switchEmbedSource(local, "ollama")).toMatchObject({
      mode: "remote", source: "ollama", baseUrl: OLLAMA_DEFAULT_BASE_URL,
    });
    expect(switchEmbedSource(local, "api")).toMatchObject({ mode: "remote", source: "api" });
    expect(switchEmbedSource(
      { ...local, source: "ollama", baseUrl: OLLAMA_DEFAULT_BASE_URL },
      "api",
    )).toMatchObject({ mode: "remote", source: "api", baseUrl: "", apiKey: "" });
  });

  it("视觉从 local 模式切到 Ollama 落 mode=remote 且预填本机地址", () => {
    const local: VlmModel = {
      mode: "local", apiKey: "", baseUrl: "", modelName: "", ggufPath: "D:/vlm.gguf", proxyMode: "on",
    };

    expect(switchVlmSource(local, "ollama")).toMatchObject({
      mode: "remote", source: "ollama", baseUrl: OLLAMA_DEFAULT_BASE_URL, apiKey: "ollama",
    });
  });
});
