import { describe, expect, it } from "vitest";
import { AGENT_INVOCATION_WIRE_FIELDS } from "../generated/wireContracts";
import { agentInvocationBody, type AgentInvocation } from "./ai";

describe("generated wire contract", () => {
  it("keeps the agent serializer inside the shared schema", () => {
    const request = {
      threadId: "repo", message: "hello", images: [], workMode: "story",
      chat: { baseUrl: "chat", apiKey: "key", modelName: "model" },
      gen: { baseUrl: "gen", apiKey: "key", modelName: "image" },
      size: "1024x1024", imageQuality: "high", outputDir: "", repoId: "repo",
      proxyUrl: "", chatProxyUrl: "", genProxyUrl: "", videoProxyUrl: "", embedProxyUrl: "",
      styleTemplate: "", agentId: "", streamOutput: true, contextMaxTokens: 20000,
      historyPerRole: 6, history: [], characterDir: "", cardName: "", cardNames: [],
      openingCardName: "", presetDir: "", presetName: "", userName: "", userPersona: "",
      personaBound: false, worldbookDir: "", worldbookName: "", illustrate: false,
      comfyIllustrate: false, comfyAudio: false, promptProfile: "anima", appearanceSource: "worldbook",
      characterBaseImages: {}, illustrationActorNames: [], styleBaseImage: "",
    } satisfies AgentInvocation;
    const body = agentInvocationBody(request);
    expect(Object.keys(body).filter((key) => !AGENT_INVOCATION_WIRE_FIELDS.includes(
      key as (typeof AGENT_INVOCATION_WIRE_FIELDS)[number],
    ))).toEqual([]);
  });
});
