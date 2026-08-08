import { describe, expect, it } from "vitest";
import { agentInvocationBody, type AgentInvocation } from "./ai";

const invocation = (): AgentInvocation => ({
  threadId: "repo-1", message: "继续", images: ["image.png"], workMode: "story",
  chat: { baseUrl: "chat", apiKey: "chat-key", modelName: "chat-model" },
  gen: { baseUrl: "image", apiKey: "image-key", modelName: "image-model" },
  embed: { baseUrl: "embed", apiKey: "embed-key", modelName: "embed-model" },
  size: "832x1216", imageQuality: "high", outputDir: "out", repoId: "repo-1",
  proxyUrl: "", chatProxyUrl: "cp", genProxyUrl: "gp", videoProxyUrl: "vp", embedProxyUrl: "ep",
  messageId: "bot-1", userMessageId: "user-1", styleTemplate: "style", agentId: "agent",
  streamOutput: true, contextMaxTokens: 0, historyPerRole: 9,
  history: [{ role: "user", content: "前情" }], characterDir: "cards", cardName: "Cecilia",
  cardNames: ["Cecilia", "Nozomi"], openingCardName: "Cecilia", presetDir: "presets",
  presetName: "preset", userName: "User", userPersona: "persona", personaBound: true,
  worldbookDir: "worldbooks", worldbookName: "world", illustrate: true, comfyIllustrate: true,
  promptProfile: "anima_tags", appearanceSource: "character_card",
  characterBaseImages: { Cecilia: "portrait.png" }, illustrationActorNames: ["Cecilia"],
  styleBaseImage: "style.png",
});

describe("agent invocation wire contract", () => {
  it("preserves the complete live and queued turn context", () => {
    const body = agentInvocationBody(invocation());
    expect(body).toMatchObject({
      thread_id: "repo-1", workspace_mode: "story", message_id: "bot-1",
      context_max_tokens: 0, history_per_role: 9,
      card_names: ["Cecilia", "Nozomi"], opening_card_name: "Cecilia",
      appearance_source: "character_card", character_base_images: { Cecilia: "portrait.png" },
      chat_proxy_url: "cp", gen_proxy_url: "gp", embed_proxy_url: "ep",
    });
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
});
