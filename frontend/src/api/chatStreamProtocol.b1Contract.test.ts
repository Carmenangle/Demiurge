import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { decodeChatStreamEvent } from "./chatStreamProtocol";
import { resolveVideoMode } from "../lib/illustrationMedia";
import { illustrationTemplateValues } from "../lib/imagePromptProfiles";

// B1 跨语言端到端契约：真实后端 `agent_graph._streamed_illustration_events`
// + `chat_stream_protocol.encode_event` 产出的 wire JSON，经前端真实解码链路
// 走到模板 values。fixture 由 backend/scripts/b1_emit_wire.py 生成。
const fixture = JSON.parse(
  readFileSync(new URL("./__fixtures__/b1_illustrate_request.json", import.meta.url), "utf-8"),
) as Array<{ protocol: string; version: number; type: string; data: Record<string, unknown> }>;

describe("B1 协议透传 · 跨语言端到端契约", () => {
  it("后端 wire 事件能被前端解码为带视频字段的 illustrate_request", () => {
    const wire = fixture[0];
    expect(wire.protocol).toBe("laf-chat-stream");
    expect(wire.type).toBe("illustrate_request");

    const event = decodeChatStreamEvent(wire);
    expect(event.type).toBe("illustrate_request");
    if (event.type !== "illustrate_request") throw new Error("unreachable");

    // snake_case wire → camelCase TS 全链路无丢失
    expect(event.videoMode).toBe("firstlast");
    expect(event.firstFrameDesc).toBe("雨夜门口，温知夏收伞，暖黄灯笼倒影");
    expect(event.lastFrameDesc).toBe("三人举杯同框，温情对视");
    expect(event.prevTailDesc).toBe("上一楼层：林屿在门口抽烟回望");
    expect(event.lastFrameUrl).toBe("data:image/png;base64,ZmFrZS10YWlsLWZyYW1l");
    expect(event.motion).toBe(3);
    expect(event.actors).toEqual(["温知夏", "林屿", "苏绾"]);
    expect(event.sceneSpec?.locale).toBe("温暖小面馆内景");
  });

  it("视频模式决策：事件 firstlast 覆盖 preset climax", () => {
    expect(resolveVideoMode({ videoMode: "climax" }, "firstlast")).toBe("firstlast");
  });

  it("视频字段经 illustrationTemplateValues 注入模板 exposed binding", () => {
    const wire = fixture[0];
    const event = decodeChatStreamEvent(wire);
    if (event.type !== "illustrate_request") throw new Error("unreachable");

    const exposed = [
      { node_id: "1", field: "mode", semantic: "mode", binding: "video_mode" },
      { node_id: "2", field: "first", semantic: "first", binding: "first_frame_desc" },
      { node_id: "3", field: "last", semantic: "last", binding: "last_frame_desc" },
      { node_id: "4", field: "prev", semantic: "prev", binding: "prev_tail_desc" },
      { node_id: "5", field: "url", semantic: "url", binding: "last_frame_url" },
    ];
    const values = illustrationTemplateValues(exposed, {
      prompt: event.prompt,
      videoMode: event.videoMode,
      firstFrameDesc: event.firstFrameDesc,
      lastFrameDesc: event.lastFrameDesc,
      prevTailDesc: event.prevTailDesc,
      lastFrameUrl: event.lastFrameUrl,
    });

    expect(values["1.mode"]).toBe("firstlast");
    expect(values["2.first"]).toBe("雨夜门口，温知夏收伞，暖黄灯笼倒影");
    expect(values["3.last"]).toBe("三人举杯同框，温情对视");
    expect(values["4.prev"]).toBe("上一楼层：林屿在门口抽烟回望");
    expect(values["5.url"]).toBe("data:image/png;base64,ZmFrZS10YWlsLWZyYW1l");
  });
});
