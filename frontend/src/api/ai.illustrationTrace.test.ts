import { afterEach, describe, expect, it, vi } from "vitest";

import { claimIllustrationSubmission, reportIllustrationSubmission } from "./ai";

afterEach(() => vi.unstubAllGlobals());

describe("illustration submission trace", () => {
  it("claims the authoritative pending slot before calling ComfyUI", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ ok: true, claimed: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(claimIllustrationSubmission({
      threadId: "SAVE01", messageId: "bot-1", slotId: "slot-1",
    })).resolves.toEqual({ ok: true, claimed: true });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      thread_id: "SAVE01", message_id: "bot-1", slot_id: "slot-1",
    });
  });

  it("reports the final ComfyUI values without credentials or image data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ ok: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await reportIllustrationSubmission({
      threadId: "SAVE01", repoId: "神权大陆", turnId: "turn-1",
      messageId: "bot-1", slotId: "slot-1", templateId: "tpl-1",
      promptId: "comfy-1", prompt: "trigger\nfinal prompt",
      promptProfile: "anima_tags", loraName: "style.safetensors", loraWeight: 0.8,
      loraMode: "multi", loraNames: ["style.safetensors", "role.safetensors"],
      latentWidth: 704, latentHeight: 1024,
      valueKeys: ["18.text", "19.strength_model", "20.width", "20.height"],
      source: "manual",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({
      thread_id: "SAVE01", repo_id: "神权大陆", turn_id: "turn-1",
      prompt_id: "comfy-1", prompt: "trigger\nfinal prompt",
      lora_name: "style.safetensors", lora_weight: 0.8,
      lora_mode: "multi", lora_names: ["style.safetensors", "role.safetensors"],
      latent_width: 704, latent_height: 1024,
      source: "manual",
    });
    expect(JSON.stringify(body)).not.toMatch(/api_key|data:image/i);
  });
});
