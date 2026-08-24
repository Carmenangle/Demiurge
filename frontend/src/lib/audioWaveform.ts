// audioWaveform.ts — 音频波形纯逻辑：把 PCM 采样降采样为 N 桶峰值（0..1）。
// 渲染（canvas/seek）在 AudioPlayer 组件内；这里只做可单测的数值部分。

/** 把单声道 Float32 PCM（-1..1）降采样为 buckets 个桶的峰值（0..1）。
 *  每桶取该区间内 |sample| 的最大值，保留瞬态尖峰（比 RMS 更适合波形观感）。
 *  采样数不足 buckets 时按原样覆盖（桶内至少 1 个采样）。 */
export function computeWaveformPeaks(channel: Float32Array, buckets: number): number[] {
  if (buckets <= 0) return [];
  if (channel.length === 0) return new Array(buckets).fill(0);
  const peaks: number[] = new Array(buckets);
  const per = channel.length / buckets;
  for (let i = 0; i < buckets; i += 1) {
    const start = Math.floor(i * per);
    const end = Math.max(start + 1, Math.floor((i + 1) * per));
    let max = 0;
    for (let j = start; j < end && j < channel.length; j += 1) {
      const v = Math.abs(channel[j]);
      if (v > max) max = v;
    }
    peaks[i] = max;
  }
  return peaks;
}

/** 从多声道 AudioBuffer 合成单声道采样（各声道平均），供 computeWaveformPeaks 使用。 */
export function monoChannelData(buffer: AudioBuffer): Float32Array {
  if (buffer.numberOfChannels === 0) return new Float32Array(0);
  if (buffer.numberOfChannels === 1) return buffer.getChannelData(0);
  const length = buffer.length;
  const mono = new Float32Array(length);
  for (let c = 0; c < buffer.numberOfChannels; c += 1) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < length; i += 1) mono[i] += data[i];
  }
  for (let i = 0; i < length; i += 1) mono[i] /= buffer.numberOfChannels;
  return mono;
}
