import { describe, expect, it } from "vitest";

import { decodeChatStreamEvent } from "./chatStreamProtocol";
import { resolveVideoMode } from "../lib/illustrationMedia";
import { illustrationTemplateValues } from "../lib/imagePromptProfiles";
import rawFixture from "./__fixtures__/b1_illustrate_request.json";
import rawB2Fixture from "./__fixtures__/b2_climax_video_prompt.json";

// B1 跨语言端到端契约：真实后端 `agent_graph._streamed_illustration_events`
// + `chat_stream_protocol.encode_event` 产出的 wire JSON，经前端真实解码链路
// 走到模板 values。fixture 由 backend/scripts/b1_emit_wire.py 生成。
const fixture = rawFixture as Array<{
  protocol: string; version: number; type: string; data: Record<string, unknown>;
}>;
const b2Fixture = rawB2Fixture as Array<{
  protocol: string; version: number; type: string; data: Record<string, unknown>;
}>;

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

  it("视频模式决策（用户定稿 2026-08-28）：firstlast 首尾帧生成选项推导视频模式", () => {
    // 首尾帧生成开 → firstlast；关闭且无旧字段 → climax
    expect(resolveVideoMode({ firstlast: true })).toBe("firstlast");
    expect(resolveVideoMode({ firstlast: false, videoMode: "climax" })).toBe("climax");
    // firstlast 选项优先于旧 videoMode 字段（迁移后不再独立设置）
    expect(resolveVideoMode({ firstlast: true, videoMode: "climax" })).toBe("firstlast");
    // 旧预设无 firstlast 字段 → 退 videoMode 兼容
    expect(resolveVideoMode({ videoMode: "firstlast" })).toBe("firstlast");
    expect(resolveVideoMode(undefined)).toBe("climax");
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

  it("W3 转场视频：transition_video_prompt / params 随事件解码（snake→camel 无丢失）", () => {
    const wire = {
      protocol: "laf-chat-stream", version: 1, type: "illustrate_request",
      data: {
        prompt: "p", motion: 3, actors: ["甲"],
        video_mode: "firstlast",
        transition: "regenerate",
        transition_video_prompt: "转场分镜提示词",
        transition_video_params: { mode: "transition", size: "1280x720" },
      },
    };
    const event = decodeChatStreamEvent(wire);
    if (event.type !== "illustrate_request") throw new Error("unreachable");

    expect(event.transition).toBe("regenerate");
    expect(event.transitionVideoPrompt).toBe("转场分镜提示词");
    expect(event.transitionVideoParams).toEqual({ mode: "transition", size: "1280x720" });
  });
});

describe("B2 默认开放 climax 视频提示词 · 端到端契约（无视频模板/模型也生成）", () => {
  it("后端 wire 事件默认带 video_prompt，前端解码为完整提示词", () => {
    const wire = b2Fixture[0];
    const event = decodeChatStreamEvent(wire);
    if (event.type !== "illustrate_request") throw new Error("unreachable");

    expect(event.videoPrompt).toBeTruthy();
    // 内容要求机械检查：区块完整 + 无破甲残留 + 运镜随 motion 强度
    expect(event.videoPrompt).toContain("[元信息]");
    expect(event.videoPrompt).toContain("[参考绑定]");
    expect(event.videoPrompt).toContain("图片1中心的角色为");
    expect(event.videoPrompt).toContain("[主体/场景]");
    expect(event.videoPrompt).toContain("[时间分镜]");
    expect(event.videoPrompt).toContain("[音频]");
    expect(event.videoPrompt).not.toContain("[动作]");
    expect(event.videoPrompt).not.toContain("@(");
    // fixture 是 motion=3 → 强动态运镜
    expect(event.videoPrompt).toContain("低机位快速丝滑运镜");
    // 视频参数随事件下发（dry-run 参数上传核对）
    expect(event.videoParams?.mode).toBe("climax");
    expect(event.videoParams?.size).toBe("1280x720");
    expect(event.videoParams?.warnings?.some((w) => w.includes("缺高潮参考图"))).toBe(true);
  });

  it("video_prompt 经 illustrationTemplateValues 注入模板 exposed binding", () => {
    const wire = b2Fixture[0];
    const event = decodeChatStreamEvent(wire);
    if (event.type !== "illustrate_request") throw new Error("unreachable");

    const exposed = [
      { node_id: "9", field: "prompt", semantic: "prompt", binding: "video_prompt" },
    ];
    const values = illustrationTemplateValues(exposed, {
      prompt: event.prompt,
      videoPrompt: event.videoPrompt,
    });
    expect(values["9.prompt"]).toContain("[参考绑定]");
    expect(values["9.prompt"]).toContain("[时间分镜]");
  });
});
