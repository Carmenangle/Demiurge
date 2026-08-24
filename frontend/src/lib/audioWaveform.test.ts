import { describe, expect, it } from "vitest";
import { computeWaveformPeaks, monoChannelData } from "./audioWaveform";

describe("computeWaveformPeaks", () => {
  it("空输入返回全 0 桶", () => {
    expect(computeWaveformPeaks(new Float32Array(0), 4)).toEqual([0, 0, 0, 0]);
  });

  it("桶数 <= 0 返回空", () => {
    expect(computeWaveformPeaks(new Float32Array(8), 0)).toEqual([]);
  });

  it("每桶取绝对峰值（含负值）", () => {
    // 2 桶：第一桶 samples [0.2, -0.9] → 0.9；第二桶 [0.1, 0.5] → 0.5
    const peaks = computeWaveformPeaks(new Float32Array([0.2, -0.9, 0.1, 0.5]), 2);
    expect(peaks[0]).toBeCloseTo(0.9, 6);
    expect(peaks[1]).toBeCloseTo(0.5, 6);
  });

  it("采样数不足桶数时逐桶至少 1 个采样（间隙填充、不越界）", () => {
    // 2 采样 4 桶：每个采样占 0.5 桶宽，间隙由相邻采样填充 → [0.3, 0.3, 0.8, 0.8]
    const peaks = computeWaveformPeaks(new Float32Array([0.3, 0.8]), 4);
    expect(peaks).toHaveLength(4);
    expect(peaks[0]).toBeCloseTo(0.3, 6);
    expect(peaks[1]).toBeCloseTo(0.3, 6);
    expect(peaks[2]).toBeCloseTo(0.8, 6);
    expect(peaks[3]).toBeCloseTo(0.8, 6);
  });

  it("静音全 0", () => {
    expect(computeWaveformPeaks(new Float32Array(6).fill(0), 3)).toEqual([0, 0, 0]);
  });

  it("峰值永不超 1", () => {
    const peaks = computeWaveformPeaks(new Float32Array([1, -1, 0.5]), 3);
    expect(Math.max(...peaks)).toBeLessThanOrEqual(1);
  });
});

describe("monoChannelData", () => {
  it("单声道直接返回", () => {
    const buf = { numberOfChannels: 1, length: 3, getChannelData: () => new Float32Array([1, 2, 3]) } as unknown as AudioBuffer;
    expect(Array.from(monoChannelData(buf))).toEqual([1, 2, 3]);
  });

  it("多声道按采样平均", () => {
    const buf = {
      numberOfChannels: 2,
      length: 2,
      getChannelData: (c: number) => new Float32Array(c === 0 ? [0, 1] : [0, 3]),
    } as unknown as AudioBuffer;
    expect(Array.from(monoChannelData(buf))).toEqual([0, 2]);
  });
});
