import { afterEach, describe, expect, it, vi } from "vitest";

import { reportIllustrationSubmission } from "./ai";

afterEach(() => vi.unstubAllGlobals());

describe("illustration submission trace", () => {
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
      latentWidth: 704, latentHeight: 1024,
      valueKeys: ["18.text", "19.strength_model", "20.width", "20.height"],
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({
      thread_id: "SAVE01", repo_id: "神权大陆", turn_id: "turn-1",
      prompt_id: "comfy-1", prompt: "trigger\nfinal prompt",
      lora_name: "style.safetensors", lora_weight: 0.8,
      latent_width: 704, latent_height: 1024,
    });
    expect(JSON.stringify(body)).not.toMatch(/api_key|data:image/i);
  });
});
