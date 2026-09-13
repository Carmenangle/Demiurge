// AudioPlayer.tsx — 通用音频播放器：时长、可拖动进度、播放/暂停、±5s 跳转、倍速、静态波形。
// 用隐藏 <audio> 驱动播放（preload=metadata 取时长）；进度条在 Web Audio 解码出波形后
// 升级为「波形条」（点击/拖动 seek，已播部分高亮），解码失败（CORS/格式）自动回退原滑块。
// 对话消息与画布剧情节点共用。
import { useEffect, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import { computeWaveformPeaks, monoChannelData } from "../lib/audioWaveform";

// 秒 → mm:ss（时长/当前位置显示）
export function formatTime(sec: number): string {
  if (!isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// 倍速档位（点击循环切换）
const RATES = [0.5, 1, 1.5, 2];

// 波形桶数（渲染为竖条；96 条在窄容器里仍可辨）
const WAVE_BUCKETS = 96;

// 惰性共享 AudioContext：只用 decodeAudioData（suspended 状态也可用），不占用播放。
let audioCtx: AudioContext | null = null;
function getAudioCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  try {
    if (!audioCtx) {
      const Ctor = window.AudioContext
        || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return null;
      audioCtx = new Ctor();
    }
    return audioCtx;
  } catch {
    return null;
  }
}

/** 拉取并解码音频为波形峰值；任何失败（CORS/格式/解码）返回 null，播放器静默回退滑块。 */
async function decodeWaveformPeaks(src: string, signal: AbortSignal): Promise<number[] | null> {
  const ctx = getAudioCtx();
  if (!ctx) return null;
  const response = await fetch(src, { signal });
  if (!response.ok) return null;
  const arrayBuffer = await response.arrayBuffer();
  const buffer = await ctx.decodeAudioData(arrayBuffer);
  return computeWaveformPeaks(monoChannelData(buffer), WAVE_BUCKETS);
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function AudioPlayer({ src }: { src: string }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [seeking, setSeeking] = useState(false);
  const [seekValue, setSeekValue] = useState(0);
  const [rateIdx, setRateIdx] = useState(1);
  const [failed, setFailed] = useState(false);
  const [peaks, setPeaks] = useState<number[] | null>(null);
  const draggingRef = useRef(false);

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    // 换源后重置状态，并强制 load() 重新拉取 metadata（否则可能停在 0s）
    setCurrent(0);
    setDuration(0);
    setPlaying(false);
    setFailed(false);
    const onMeta = () => setDuration(el.duration || 0);
    const onTime = () => setCurrent(el.currentTime || 0);
    const onEnd = () => { setPlaying(false); setCurrent(el.duration || 0); };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onError = () => { setPlaying(false); setFailed(true); };
    el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("durationchange", onMeta);
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("ended", onEnd);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("error", onError);
    el.load();
    return () => {
      el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("durationchange", onMeta);
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("ended", onEnd);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("error", onError);
    };
  }, [src]);

  // 解码波形：失败静默回退（peaks 保持 null → 渲染原滑块）
  useEffect(() => {
    setPeaks(null);
    const controller = new AbortController();
    decodeWaveformPeaks(src, controller.signal)
      .then((p) => { if (!controller.signal.aborted && p) setPeaks(p); })
      .catch(() => { /* 降级为滑块 */ });
    return () => controller.abort();
  }, [src]);

  // 画波形：已播部分高亮（primary），未播灰条；容器尺寸变化重绘
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks || peaks.length === 0) return;
    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (width <= 0 || height <= 0) return;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const g = canvas.getContext("2d");
      if (!g) return;
      g.scale(dpr, dpr);
      g.clearRect(0, 0, width, height);
      const playedColor = cssVar("--primary", "#3b82f6");
      const idleColor = "rgba(148,163,184,0.35)";
      const fraction = duration > 0 ? Math.min(Math.max(current / duration, 0), 1) : 0;
      const gap = 1;
      const barW = Math.max(1, (width - gap * (peaks.length - 1)) / peaks.length);
      for (let i = 0; i < peaks.length; i += 1) {
        const h = Math.max(2, peaks[i] * (height - 2));
        const x = i * (barW + gap);
        g.fillStyle = (i + 1) / peaks.length <= fraction ? playedColor : idleColor;
        g.fillRect(x, (height - h) / 2, barW, h);
      }
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [peaks, current, duration]);

  const toggle = () => {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) void el.play().catch(() => {});
    else el.pause();
  };

  const seek = (value: number) => {
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = value;
    setCurrent(value);
  };

  // ±5s 跳转：相对当前播放位置，夹在 [0, duration]（时长未知时只限制不小于 0）
  const skip = (delta: number) => {
    const el = audioRef.current;
    if (!el) return;
    const max = isFinite(el.duration) && el.duration > 0 ? el.duration : Infinity;
    const next = Math.min(Math.max(el.currentTime + delta, 0), max);
    el.currentTime = next;
    setCurrent(next);
  };

  const cycleRate = () => {
    const el = audioRef.current;
    if (!el) return;
    const next = (rateIdx + 1) % RATES.length;
    setRateIdx(next);
    el.playbackRate = RATES[next];
  };

  // 波形点击/拖动 seek：x 位置 → 时长比例
  const seekFromPointer = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0) return;
    const fraction = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
    seek(fraction * (duration || 0));
  };

  const shown = seeking ? seekValue : current;
  const max = duration > 0 ? duration : 0;
  return (
    <div className="audio-player">
      <audio ref={audioRef} src={src} preload="metadata" style={{ display: "none" }} />
      <button type="button" className="audio-play-btn" title={playing ? "暂停" : "播放"} onClick={toggle}>
        {playing ? <Pause size={16} /> : <Play size={16} />}
      </button>
      <button type="button" className="audio-chip" title="后退 5 秒" onClick={() => skip(-5)}>-5</button>
      {peaks ? (
        <canvas
          ref={canvasRef}
          className="audio-waveform"
          role="slider"
          aria-label="播放进度（波形）"
          aria-valuemin={0}
          aria-valuemax={Math.round(max)}
          aria-valuenow={Math.round(shown)}
          onPointerDown={(e) => {
            draggingRef.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            seekFromPointer(e);
          }}
          onPointerMove={(e) => { if (draggingRef.current) seekFromPointer(e); }}
          onPointerUp={() => { draggingRef.current = false; }}
          onPointerCancel={() => { draggingRef.current = false; }}
        />
      ) : (
        <input
          type="range"
          className="audio-seek"
          min={0}
          max={max}
          step={0.1}
          value={shown}
          aria-label="播放进度"
          disabled={max === 0}
          onPointerDown={() => { setSeeking(true); setSeekValue(current); }}
          onInput={(e) => setSeekValue(Number((e.target as HTMLInputElement).value))}
          onChange={(e) => { seek(Number((e.target as HTMLInputElement).value)); setSeeking(false); }}
        />
      )}
      <button type="button" className="audio-chip" title="前进 5 秒" onClick={() => skip(5)}>+5</button>
      <button
        type="button"
        className={`audio-chip audio-rate${RATES[rateIdx] !== 1 ? " active" : ""}`}
        title="倍速（点击切换）"
        onClick={cycleRate}
      >
        {RATES[rateIdx].toFixed(1)}x
      </button>
      <span className="audio-time">{failed ? "无法播放" : `${formatTime(shown)} / ${formatTime(duration)}`}</span>
    </div>
  );
}
