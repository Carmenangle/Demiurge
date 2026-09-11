import { useEffect, useRef, useState } from "react";
import { Download, Pause, Play } from "lucide-react";
import { PageShell, StateHint } from "../../components/layout/PageShell";
import { encodeGif, sliceSheet } from "../../api/tools";
import { ImageDrop } from "./ImageDrop";

export function SpriteToGifTab({ onBack }: { onBack: () => void }) {
  const [src, setSrc] = useState("");
  const [fileName, setFileName] = useState("");
  const [cols, setCols] = useState(4);
  const [rows, setRows] = useState(4);
  const [padding, setPadding] = useState(0);
  // 真正的空白帧和网格补空格长得一样，分不出来 —— 所以这个开关交给用户。
  const [dropEmpty, setDropEmpty] = useState(true);
  const [fps, setFps] = useState(12);
  const [transparent, setTransparent] = useState(true);
  const [frames, setFrames] = useState<string[]>([]);
  const [dropped, setDropped] = useState<Set<number>>(new Set());
  const [gif, setGif] = useState("");
  const [busy, setBusy] = useState("");
  const [playing, setPlaying] = useState(true);
  const [cur, setCur] = useState(0);
  const timer = useRef<number | null>(null);

  const kept = frames.map((f, i) => ({ f, i })).filter((x) => !dropped.has(x.i));

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (!playing || kept.length < 2) return;
    timer.current = window.setTimeout(
      () => setCur((c) => c + 1), Math.max(20, Math.round(1000 / fps)));
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [playing, cur, kept.length, fps]);

  const doSlice = async () => {
    if (!src) return;
    setBusy("正在切割…");
    setGif("");
    try {
      const r = await sliceSheet(src, cols, rows, padding, dropEmpty);
      setFrames(r.frames);
      setDropped(new Set());
      setBusy(`切出 ${r.count} 帧。`);
    } catch (e) {
      setFrames([]);
      setBusy(`切割失败：${(e as Error).message}`);
    }
  };

  const doEncode = async () => {
    if (!kept.length) { setBusy("没有剩下任何帧。"); return; }
    setBusy("正在合成 GIF…");
    try {
      const r = await encodeGif(
        kept.map((x) => x.f), Math.round(1000 / Math.max(1, fps)), transparent);
      setGif(r.image);
      // 连续相同的帧会被合并成一帧并累加时长（播放效果不变），说明一下免得像丢帧
      setBusy(r.frames < r.input_frames
        ? `已合成 GIF：${r.input_frames} 帧中有连续重复的，合并为 ${r.frames} 帧，播放时长不变。`
        : `已合成 ${r.frames} 帧的 GIF。`);
    } catch (e) {
      setBusy(`合成失败：${(e as Error).message}`);
    }
  };

  const toggle = (i: number) =>
    setDropped((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });

  const baseName = fileName.replace(/\.(png|jpe?g|webp|bmp)$/i, "") || "anim";

  return (
    <PageShell
      title="精灵图转 GIF"
      back={onBack}
      actions={
        src && (
          <>
            {gif && (
              <a className="btn" href={gif} download={`${baseName}.gif`}>
                <Download size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                下载 GIF
              </a>
            )}
          </>
        )
      }
    >
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
        按行列数把精灵图均分成帧，再合成 GIF。切完可以点掉不想要的帧。
      </p>
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}

      <div className="tool-split">
        <div className="tool-pane">
          <span className="tool-pane-title">精灵图与参数</span>
          <ImageDrop
            accept="image/*"
            hint={src ? "换一张：点选 / 拖入 / 粘贴" : "点这里选精灵图，或直接拖进来 / 粘贴"}
            onPick={(d, n) => { setSrc(d); setFileName(n); setFrames([]); setGif(""); setBusy(""); }}
          />

          {src && (
            <>
          <div className="tool-row">
            <label>列数
              <input type="number" min={1} max={64} value={cols}
                onChange={(e) => setCols(Math.max(1, Number(e.target.value) || 1))} />
            </label>
            <label>行数
              <input type="number" min={1} max={64} value={rows}
                onChange={(e) => setRows(Math.max(1, Number(e.target.value) || 1))} />
            </label>
            <label>间距(px)
              <input type="number" min={0} max={64} value={padding}
                onChange={(e) => setPadding(Math.max(0, Number(e.target.value) || 0))} />
            </label>
            <label>帧率(fps)
              <input type="number" min={1} max={50} value={fps}
                onChange={(e) => setFps(Math.min(50, Math.max(1, Number(e.target.value) || 12)))} />
            </label>
            <label className="tool-check">
              <input type="checkbox" checked={dropEmpty}
                onChange={(e) => setDropEmpty(e.target.checked)} />
              丢弃空白格
            </label>
            <label className="tool-check">
              <input type="checkbox" checked={transparent}
                onChange={(e) => setTransparent(e.target.checked)} />
              透明背景
            </label>
            <button className="btn" onClick={doSlice}>按网格切割</button>
            {frames.length > 0 && <button className="btn" onClick={doEncode}>合成 GIF</button>}
            {kept.length > 0 && (
              <button className="btn" onClick={() => setPlaying((p) => !p)}>
                {playing
                  ? <><Pause size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />暂停</>
                  : <><Play size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />播放</>}
              </button>
            )}
            <button className="btn" onClick={() => { setSrc(""); setFrames([]); setGif(""); setBusy(""); }}>
              清空
            </button>
          </div>
            </>
          )}
        </div>

        <div className="tool-pane-plain">
          <div className="tool-window">
            <div className="tool-window-head">
              <strong>原图</strong>
              <span>{frames.length > 0 ? `切出 ${frames.length} 帧` : "—"}</span>
            </div>
            <div className="tool-window-body">
              {src
                ? <img src={src} alt="精灵图原图" className="tool-sheet" />
                : <span className="tool-window-empty">左侧选一张精灵图开始。</span>}
            </div>
          </div>

          <div className="tool-window">
            <div className="tool-window-head">
              <strong>{gif ? "GIF 结果" : "动画预览"}</strong>
              <span>{kept.length > 0 ? `${kept.length} 帧 @ ${fps}fps` : "—"}</span>
            </div>
            <div className="tool-window-body">
              {gif
                ? <img src={gif} alt="GIF 结果" className="tool-anim" />
                : kept.length > 0
                  ? <img src={kept[cur % kept.length].f} alt="动画预览" className="tool-anim" />
                  : <span className="tool-window-empty">先点「按网格切割」。</span>}
            </div>
          </div>

          {frames.length > 0 && (
            <>
              <span className="tool-label">切出的帧（点击剔除，已剔 {dropped.size} 帧）</span>
              <div className="tool-frames">
                {frames.map((f, i) => (
                  <button
                    key={i}
                    className={`tool-frame${dropped.has(i) ? " tool-frame-off" : ""}`}
                    onClick={() => toggle(i)}
                    title={dropped.has(i) ? "点击恢复这一帧" : "点击剔除这一帧"}
                  >
                    <img src={f} alt={`第 ${i + 1} 帧`} />
                    <span>{i + 1}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      {!src && !busy && <StateHint>选一张精灵图开始。</StateHint>}
    </PageShell>
  );
}
