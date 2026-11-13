import { describe, expect, it, vi } from "vitest";

import {
  appendCharacterAnchor, chatMediaNegativePrompt, chatMediaSceneSpec, finalizeChatMediaPrompt,
  isRetryableChatMediaError, runChatMediaWithRetry, stripAnimaPhotoStyleTags,
} from "./chatMediaComfy";

describe("chat media comfy prompt pipeline", () => {
  it("聊天自拍场景合同带入外貌锚点与发言角色", () => {
    const scene = chatMediaSceneSpec({
      prompt: "婚纱自拍",
      appearance: "腓特烈大帝：【外貌】暗黄色长发；暗黄色眼睛",
      speaker: "腓特烈大帝",
      repoId: "repo-1",
    });

    expect(scene.narrative).toBe("婚纱自拍");
    expect(scene.appearance).toContain("暗黄色长发");
    expect(scene.actors).toEqual(["腓特烈大帝"]);
    expect(scene.rating).toBe("sfw");
    expect(scene.aspect_ratio).toBe("3:4");
    expect(scene.draft_prompt).toBe("");
  });

  it("Anima 成稿删掉正向写实/摄影标签，再换成预设质量行并锁死手绘二次元", () => {
    const prompt = [
      "masterpiece, best quality, photorealistic, professional photography, realistic photography",
      "An elegant mature woman in a white wedding dress.",
    ].join("\n");

    const out = finalizeChatMediaPrompt(
      prompt, "anima_tags", "masterpiece, best quality, anime coloring", true,
    );

    expect(out.split("\n")[0]).toBe(
      "masterpiece, best quality, anime coloring, "
      + "anime illustration, hand-drawn anime style, 2d cel shading, non-photorealistic",
    );
    expect(out.split("\n")[1]).toBe("An elegant mature woman in a white wedding dress.");
  });

  it("正向写实标签只从 tags 行删除，不动第二行画面描述", () => {
    expect(stripAnimaPhotoStyleTags(
      "1girl, photo, realistic photography, cinematic lighting\nA photo-realistic room in warm light.",
    )).toBe("1girl, cinematic lighting\nA photo-realistic room in warm light.");
  });

  it("非 Anima 档不替换质量行也不追加风格词", () => {
    const prompt = "A woman in a white wedding dress, natural language.";
    expect(finalizeChatMediaPrompt(prompt, "natural_language", "anime coloring", true))
      .toBe(prompt);
  });

  it("编译后强制追加角色固定外貌锚点，避免随机换发色/瞳色", () => {
    const out = appendCharacterAnchor(
      "adult woman, standing, medium shot", "腓特烈大帝：暗黄色长发；暗黄色眼睛",
    );
    expect(out).toContain("adult woman");
    expect(out).toContain("人物固定外貌（不得改动");
    expect(out).toContain("暗黄色长发");
    expect(appendCharacterAnchor("adult woman", "")).toBe("adult woman");
  });

  it("Anima 负向追加写实/摄影词；其他档保持用户配置", () => {
    expect(chatMediaNegativePrompt("worst quality, low quality", "anima_tags")).toBe(
      "worst quality, low quality, photorealistic, photo-realistic, realistic, 3d render, "
      + "professional photography",
    );
    expect(chatMediaNegativePrompt("worst quality", "natural_language")).toBe("worst quality");
    expect(chatMediaNegativePrompt("", "anima_tags")).toContain("photorealistic");
  });

  it("可恢复错误等 GPU 空闲后重拍；空提示词/模板缺失不重拍", () => {
    expect(isRetryableChatMediaError(new Error("Connection error"))).toBe(true);
    // 原任务还在跑：先等队列清空再重拍，不能立刻抢显卡。
    expect(isRetryableChatMediaError(
      new Error("拍摄任务仍在 ComfyUI 里运行，稍后可在后台活动查看"),
    )).toBe(true);
    expect(isRetryableChatMediaError(new Error("画面提示词为空"))).toBe(false);
    expect(isRetryableChatMediaError(new Error("模板不存在"))).toBe(false);
  });

  it("重拍前先等 GPU 空闲，再提交第二次", async () => {
    const order: string[] = [];
    let calls = 0;
    const run = vi.fn(async () => {
      calls += 1;
      if (calls === 1) throw new Error("Connection error");
      return "image";
    });

    const out = await runChatMediaWithRetry(run, {
      attempts: 2,
      onRetry: () => order.push("notify"),
      beforeRetry: async () => { order.push("idle"); },
    });

    expect(out).toBe("image");
    expect(order).toEqual(["notify", "idle"]);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("等 GPU 空闲失败就不再提交第二次，错误交给调用方降级", async () => {
    const run = vi.fn(async () => { throw new Error("Connection error"); });
    const beforeRetry = vi.fn(async () => { throw new Error("等待显卡空闲超时"); });

    await expect(runChatMediaWithRetry(run, { attempts: 2, beforeRetry }))
      .rejects.toThrow("等待显卡空闲超时");
    expect(run).toHaveBeenCalledTimes(1);
  });
});
