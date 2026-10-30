import { describe, expect, it } from "vitest";
import { isLoopbackUrl, replaceLoopbackHost } from "./loopbackUrl";

describe("回环模型地址识别", () => {
  it.each([
    "http://127.0.0.1:11434/v1",
    "http://127.1.2.3:8080",
    "http://localhost:11434/v1",
    "http://LOCALHOST/v1",
    "http://[::1]:8080/v1",
    "http://0.0.0.0:11434/v1",
    "127.0.0.1:11434/v1",
    "https://ollama.localhost/v1",
  ])("%s 是回环", (url) => {
    expect(isLoopbackUrl(url)).toBe(true);
  });

  it.each([
    "https://open.bigmodel.cn/api/paas/v4",
    "http://192.168.1.7:11434/v1",
    "http://10.0.0.5:8000/v1",
    "http://my-pc:11434/v1",
    "",
    "not a url",
  ])("%s 不是回环", (url) => {
    expect(isLoopbackUrl(url)).toBe(false);
  });
});

describe("回环地址换手机局域网地址", () => {
  it("保留协议、端口、路径与查询", () => {
    expect(replaceLoopbackHost("http://127.0.0.1:11434/v1", "192.168.1.7"))
      .toBe("http://192.168.1.7:11434/v1");
    expect(replaceLoopbackHost("https://localhost:8443/embeddings?x=1", "192.168.1.7"))
      .toBe("https://192.168.1.7:8443/embeddings?x=1");
  });

  it("无协议地址也能换", () => {
    expect(replaceLoopbackHost("localhost:11434/v1", "192.168.1.7"))
      .toBe("192.168.1.7:11434/v1");
  });

  it("非回环地址与没有 lan_ip 时原样返回", () => {
    expect(replaceLoopbackHost("https://api.openai.com/v1", "192.168.1.7"))
      .toBe("https://api.openai.com/v1");
    expect(replaceLoopbackHost("http://127.0.0.1:11434/v1", ""))
      .toBe("http://127.0.0.1:11434/v1");
  });
});
