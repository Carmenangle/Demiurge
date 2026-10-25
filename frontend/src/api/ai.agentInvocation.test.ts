import { describe, expect, it } from "vitest";
import { agentInvocationBody, type AgentInvocation } from "./ai";

const invocation = (): AgentInvocation => ({
  threadId: "repo-1", message: "继续", images: ["image.png"], workMode: "story",
  chat: { baseUrl: "chat", apiKey: "chat-key", modelName: "chat-model" },
  gen: { baseUrl: "image", apiKey: "image-key", modelName: "image-model" },
  embed: { baseUrl: "embed", apiKey: "embed-key", modelName: "embed-model" },
  size: "832x1216", imageQuality: "high", outputDir: "out", repoId: "repo-1",
  proxyUrl: "", chatProxyUrl: "cp", genProxyUrl: "gp", videoProxyUrl: "vp", embedProxyUrl: "ep",
  searchProvider: "ddg",
  messageId: "bot-1", userMessageId: "user-1", styleTemplate: "style", agentId: "agent",
  streamOutput: true, contextMaxTokens: 0, compactTriggerPct: 80, selfhealAttempts: 2,
  presetSampling: { temperature: 1, topP: 0.98, topK: 50, frequencyPenalty: 0, presencePenalty: 0, openaiMaxContext: 2_000_000, openaiMaxTokens: 600_000 },
  history: [{ role: "user", content: "前情" }], characterDir: "cards", cardName: "Cecilia",
  cardNames: ["Cecilia", "Nozomi"], openingCardName: "Cecilia", presetDir: "presets",
  presetName: "preset", userName: "User", userPersona: "persona", personaBound: true,
  worldbookDir: "worldbooks", worldbookName: "world", illustrate: true, comfyIllustrate: true,
  comfyAudio: false, comfyVideo: true,
  promptProfile: "anima_tags", appearanceSource: "character_card",
  characterBaseImages: { Cecilia: "portrait.png" }, illustrationActorNames: ["Cecilia"],
  styleBaseImage: "style.png",
});

describe("agent invocation wire contract", () => {
  it("preserves the complete live and queued turn context", () => {
    const body = agentInvocationBody(invocation());
    expect(body).toMatchObject({
      thread_id: "repo-1", workspace_mode: "story", message_id: "bot-1",
      context_max_tokens: 0, compact_trigger_pct: 80,
      card_names: ["Cecilia", "Nozomi"], opening_card_name: "Cecilia",
      appearance_source: "character_card", character_base_images: { Cecilia: "portrait.png" },
      chat_proxy_url: "cp", gen_proxy_url: "gp", embed_proxy_url: "ep",
      search_provider: "ddg",
    });
  });

  it("搜索源未指定时 wire 送空串（后端约定「空=自动回落链 bing-cn→ddg」）", () => {
    const body = agentInvocationBody({ ...invocation(), searchProvider: undefined });
    expect(body.search_provider).toBe("");
  });

  it("uses explicit approval and route actions without rebuilding context", () => {
    const body = agentInvocationBody({
      ...invocation(),
      approvalAction: { approvalId: "approval-1", action: "change", editedPrompt: "new" },
      routeAction: { route: "generate", userMessageId: "route-user" },
    });
    expect(body).toMatchObject({
      approval_id: "approval-1", approval_action: "change", edited_prompt: "new",
      forced_route: "generate", user_message_id: "route-user",
    });
  });

  it("透传附件元信息为 wire 字段（file_id 真源，C1 增强）", () => {
    const body = agentInvocationBody({
      ...invocation(),
      attachments: [
        { fileId: "a".repeat(32), name: "计划.md", mime: "text/markdown", size: 1234 },
        { fileId: "b".repeat(32), name: "clip.mp4", mime: "video/mp4", size: 99 },
      ],
    });
    expect(body.attachments).toEqual([
      { file_id: "a".repeat(32), name: "计划.md", mime: "text/markdown", size: 1234 },
      { file_id: "b".repeat(32), name: "clip.mp4", mime: "video/mp4", size: 99 },
    ]);
  });

  it("无附件时 attachments 序列化为空数组（wire 字段始终存在）", () => {
    const body = agentInvocationBody(invocation());
    expect(body.attachments).toEqual([]);
  });

  it("画面人称手选进 wire，未选时送空串（空=后端自动判定）", () => {
    expect(agentInvocationBody(invocation()).illustration_viewpoint).toBe("");
    expect(agentInvocationBody({
      ...invocation(), illustrationViewpoint: "third_person",
    }).illustration_viewpoint).toBe("third_person");
    expect(agentInvocationBody({
      ...invocation(), illustrationViewpoint: "first_person",
    }).illustration_viewpoint).toBe("first_person");
  });

  it("批量生图数目进 wire，未设时送 0（0=后端按 1 张处理，单张路径不变）", () => {
    expect(agentInvocationBody(invocation()).illustration_batch_count).toBe(0);
    expect(agentInvocationBody({
      ...invocation(), illustrationBatchCount: 3,
    }).illustration_batch_count).toBe(3);
    // 非法值（0/负数/NaN）一律兑现 0，不把脏值透传
    expect(agentInvocationBody({
      ...invocation(), illustrationBatchCount: 0,
    }).illustration_batch_count).toBe(0);
    expect(agentInvocationBody({
      ...invocation(), illustrationBatchCount: Number.NaN,
    }).illustration_batch_count).toBe(0);
  });

  it("promptProfile 死默认修复：falsy 时送 anima_tags（旧值是已下线的 krea2）", () => {
    expect(agentInvocationBody(invocation()).prompt_profile).toBe("anima_tags");
    expect(agentInvocationBody({ ...invocation(), promptProfile: "" }).prompt_profile).toBe("anima_tags");
    expect(agentInvocationBody({
      ...invocation(), promptProfile: `custom:${"a".repeat(32)}`,
    }).prompt_profile).toBe(`custom:${"a".repeat(32)}`);
  });

  it("提示词模版字段进 wire：协议族兜底 + 视频模版 id", () => {
    const body = agentInvocationBody({
      ...invocation(),
      promptProfile: `custom:${"b".repeat(32)}`,
      promptProfileKind: "natural_language",
      videoPromptTemplateId: `custom:${"c".repeat(32)}`,
    });
    expect(body.prompt_profile_kind).toBe("natural_language");
    expect(body.video_prompt_template_id).toBe(`custom:${"c".repeat(32)}`);
    // 未配 ⇒ 空串（后端按内置默认行为）
    const bare = agentInvocationBody(invocation());
    expect(bare.prompt_profile_kind).toBe("");
    expect(bare.video_prompt_template_id).toBe("");
  });
});
