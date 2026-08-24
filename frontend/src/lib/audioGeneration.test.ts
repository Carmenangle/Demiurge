import { describe, expect, it } from "vitest";
import { audioTemplateValues, resolvableAudioLines, skippedAudioSpeakers, voiceReferenceFor } from "./audioGeneration";
import type { ExposedField } from "../api/workflows";

function field(node_id: string, field: string, semantic: string): ExposedField {
  return {
    node_id, field, semantic, label: field, control: "text", default: null,
  };
}

const exposed: ExposedField[] = [
  field("1", "audio", "voice_reference"),
  field("3", "text", "voice_text"),
  field("3", "Happy", "voice_emotion_happy"),
  field("3", "Angry", "voice_emotion_angry"),
  field("3", "Neutral", "voice_emotion_neutral"),
];

describe("audioTemplateValues", () => {
  it("assembles text, reference, and emotion vector by semantic binding", () => {
    const values = audioTemplateValues(exposed, {
      text: "你走开。",
      reference: "voice.wav",
      emotion: { happy: 0, angry: 0.9, neutral: 0.1 },
    });
    expect(values["1.audio"]).toBe("voice.wav");
    expect(values["3.text"]).toBe("你走开。");
    expect(values["3.Happy"]).toBe(0);
    expect(values["3.Angry"]).toBe(0.9);
    expect(values["3.Neutral"]).toBe(0.1);
  });

  it("omits emotion keys without values", () => {
    const values = audioTemplateValues(exposed, {
      text: "嗨", reference: "v.wav", emotion: { angry: 0.5 },
    });
    expect(values["3.Angry"]).toBe(0.5);
    expect(values["3.Neutral"]).toBeUndefined();
  });
});

describe("voiceReferenceFor / resolvableAudioLines", () => {
  const preset = {
    templateId: "img",
    characterVoices: { 阿尼玛: { voiceRef: "/voices/a.wav" } },
  };

  it("resolves a configured speaker's voice reference", () => {
    expect(voiceReferenceFor(preset as never, "阿尼玛")).toBe("/voices/a.wav");
    expect(voiceReferenceFor(preset as never, "李四")).toBeUndefined();
  });

  it("filters lines to only configured voices", () => {
    const lines = [
      { speaker: "阿尼玛", text: "你来了。" },
      { speaker: "李四", text: "我来了。" },
    ];
    const out = resolvableAudioLines(preset as never, lines);
    expect(out).toHaveLength(1);
    expect(out[0].speaker).toBe("阿尼玛");
  });

  it("skippedAudioSpeakers 列出未配置音轨的角色（去重保序）", () => {
    const lines = [
      { speaker: "阿尼玛", text: "你来了。" },
      { speaker: "李四", text: "我来了。" },
      { speaker: "李四", text: "我也来了。" },
      { speaker: "张三", text: "" },          // 空台词不算
      { speaker: "", text: "无角色名不算" },   // 空角色名不算
    ];
    expect(skippedAudioSpeakers(preset as never, lines)).toEqual(["李四"]);
  });

  it("全部角色都有音轨时 skipped 为空", () => {
    const lines = [{ speaker: "阿尼玛", text: "你来了。" }];
    expect(skippedAudioSpeakers(preset as never, lines)).toEqual([]);
  });
});
