// wire 序列化器特征网（2026-10-24 拆分时补：原 api/ai.ts 私有函数，拆分后随 wire.ts 导出）。
// 真实失败路径：字段名/缺省值手滑即污染全链路（漏 proxy / embed_mode 会把远程模型打到本地档，
// 见 F/11 红线 4「三元组走序列化器」）。逐字段锁死三种序列化器的形状与缺省。
import { describe, expect, it } from "vitest";
import { chatBody, ragEmbed, ragGbc, sseEmbed, POLL_TIMEOUT_MS, type Chat, type Embed } from "./ai";

describe("api/ai wire 序列化器", () => {
  it("chatBody：camelCase → snake_case，proxy 缺省空串", () => {
    const chat: Chat = { baseUrl: "https://api.test/v1", apiKey: "sk-x", modelName: "m1", proxyUrl: "http://127.0.0.1:7890" };
    expect(chatBody(chat)).toEqual({
      base_url: "https://api.test/v1",
      api_key: "sk-x",
      model: "m1",
      proxy: "http://127.0.0.1:7890",
    });
    expect(chatBody({ baseUrl: "b", apiKey: "k", modelName: "m" })).toEqual({
      base_url: "b", api_key: "k", model: "m", proxy: "",
    });
  });

  it("ragEmbed：RAG POST 端点字段名（embed_model 直名），mode 缺省 remote，本地目录透传", () => {
    const embed: Embed = {
      mode: "local", baseUrl: "http://127.0.0.1:11434/v1", apiKey: "ollama",
      modelName: "qwen3-embedding", modelDir: "D:/models/embed", rerankerDir: "D:/models/rerank",
      proxyUrl: "http://127.0.0.1:7890",
    };
    expect(ragEmbed(embed)).toEqual({
      base_url: "http://127.0.0.1:11434/v1",
      api_key: "ollama",
      embed_model: "qwen3-embedding",
      embed_mode: "local",
      embed_model_dir: "D:/models/embed",
      reranker_model_dir: "D:/models/rerank",
      proxy_url: "http://127.0.0.1:7890",
    });
    // 缺省：未配置时全空 + remote（不得缺键，后端按字段名提取）
    expect(ragEmbed(undefined)).toEqual({
      base_url: "", api_key: "", embed_model: "", embed_mode: "remote",
      embed_model_dir: "", reranker_model_dir: "", proxy_url: "",
    });
  });

  it("sseEmbed：SSE 端点字段名（embed_ 前缀），模型缺省 embedding-3", () => {
    expect(sseEmbed(undefined)).toEqual({
      embed_base_url: "", embed_api_key: "", embed_model: "embedding-3", embed_mode: "remote",
      embed_model_dir: "", reranker_model_dir: "", embed_proxy_url: "",
    });
    const embed: Embed = { baseUrl: "https://emb.test/v1", apiKey: "k", modelName: "e1", mode: "remote" };
    expect(sseEmbed(embed)).toEqual({
      embed_base_url: "https://emb.test/v1",
      embed_api_key: "k",
      embed_model: "e1",
      embed_mode: "remote",
      embed_model_dir: "",
      reranker_model_dir: "",
      embed_proxy_url: "",
    });
  });

  it("ragGbc：VLM 代理必须透传（云端视觉模型与描述步骤同源），本地 Ollama 空代理＝直连", () => {
    const gbc = {
      exePath: "D:/gbc.exe", sam3Dir: "D:/sam3", embedDir: "D:/embed",
      device: "cuda" as const, maxTopLevel: 10,
    };
    expect(ragGbc(gbc, {
      baseUrl: "https://open.bigmodel.cn/api/paas/v4", apiKey: "sk-x",
      modelName: "glm-4v-flash", proxyUrl: "http://127.0.0.1:7890",
    })).toEqual({
      gbc_exe: "D:/gbc.exe", sam3_dir: "D:/sam3", embed_dir: "D:/embed",
      detect_device: "cuda", max_top_level: 10,
      vlm_base_url: "https://open.bigmodel.cn/api/paas/v4",
      vlm_api_key: "sk-x", vlm_model: "glm-4v-flash",
      vlm_proxy: "http://127.0.0.1:7890",
    });
    // 本地 Ollama（回环）解析出的 proxyUrl 是空串 ⇒ 后端子进程显式直连
    expect(ragGbc(gbc, {
      baseUrl: "http://localhost:11434/v1", apiKey: "ollama", modelName: "qwen3vl:8b",
    }).vlm_proxy).toBe("");
  });

  it("轮询超时常量保持 8s（轮询断链教训，见手册 F/11 §7）", () => {
    expect(POLL_TIMEOUT_MS).toBe(8000);
  });
});
