import { useEffect, useMemo, useRef, useState } from "react";
import { Download, Pause, Play } from "lucide-react";
import { PageShell, StateHint } from "../../components/layout/PageShell";
import { composeSheet, decodeGif, type DecodedGif } from "../../api/tools";
import { ImageDrop } from "./ImageDrop";
import { composeSheetPreview, type SheetPreview } from "./sheetPreview";

// 帧状态留在前端：勾选剔除和动画预览都要即时反馈，走后端会卡。
// 后端只在「导出精灵图」时调一次。
export function GifToSpriteTab({ onBack }: { onBack: () => void }) {
  const [gif, setGif] = useState<DecodedGif | null>(null);
  const [fileName, setFileName] = useState("");
  const [dropped, setDropped] = useState<Set<number>>(new Set());
  const [cols, setCols] = useState(0);
  const [padding, setPadding] = useState(0);
  const [transparent, setTransparent] = useState(true);
  const [busy, setBusy] = useState("");
  const [sheet, setSheet] = useState("");
  const [playing, setPlaying] = useState(true);
  const [cur, setCur] = useState(0);
  const timer = useRef<number | null>(null);
  // 前端即时预览：改参数马上重排，导出才走后端
  const [preview, setPreview] = useState<SheetPreview | null>(null);

  const kept = useMemo(
    () => (gif ? gif.frames.map((f, i) => ({ f, i })).filter((x) => !dropped.has(x.i)) : []),
    [gif, dropped],
  );

  // 动画预览：按每帧自己的 duration 轮转（GIF 各帧时长可以不同）
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (!playing || kept.length < 2) return;
    const idx = cur % kept.length;
    const ms = gif?.durations[kept[idx].i] || 100;
    timer.current = window.setTimeout(() => setCur((c) => c + 1), ms);
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [playing, cur, kept, gif]);

  // 帧集合或排布参数一变就重画预览。stale 标记防止旧的异步结果盖掉新的。
  useEffect(() => {
    if (!kept.length) { setPreview(null); return; }
    let stale = false;
    composeSheetPreview(kept.map((x) => x.f), cols, padding, transparent)
      .then((p) => { if (!stale) setPreview(p); })
      .catch(() => { if (!stale) setPreview(null); });
    return () => { stale = true; };
  }, [kept, cols, padding, transparent]);

  // 参数改了就丢掉上次的后端成品，否则窗口一直显示旧图、预览像没反应
  useEffect(() => { setSheet(""); }, [kept, cols, padding, transparent]);

  const load = async (dataUri: string, name: string) => {
    setBusy("正在解析 GIF…");
    setSheet("");
    setDropped(new Set());
    try {
      const r = await decodeGif(dataUri);
      setGif(r);
      setFileName(name);
      setCols(Math.min(r.frames.length, 8));
      setBusy(`共 ${r.frames.length} 帧，尺寸 ${r.width}×${r.height}。`);
    } catch (e) {
      setGif(null);
      setBusy(`解析失败：${(e as Error).message}`);
    }
  };

  const doCompose = async () => {
    if (!kept.length) { setBusy("没有剩下任何帧。"); return; }
    setBusy("正在拼合…");
    try {
      const r = await composeSheet(
        kept.map((x) => x.f), cols, padding, transparent ? "" : "#ffffff",
      );
      setSheet(r.image);
      setBusy(`已生成 ${r.width}×${r.height} 精灵图，共 ${kept.length} 帧。`);
    } catch (e) {
      setBusy(`拼合失败：${(e as Error).message}`);
    }
  };

  const toggle = (i: number) =>
    setDropped((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });

  const baseName = fileName.replace(/\.gif$/i, "") || "sprite";

  return (
    <PageShell
      title="GIF 转精灵图"
      back={onBack}
      actions={
        gif && (
          <>
            {sheet && (
              <a className="btn" href={sheet} download={`${baseName}-sheet.png`}>
                <Download size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                下载 PNG
              </a>
            )}
          </>
        )
      }
    >
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
        逐帧拆开 GIF 再按网格拼成一张精灵图。点某一帧可把它剔除，不参与拼合。
      </p>
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}

      <div className="tool-split">
        <div className="tool-pane">
          <span className="tool-pane-title">GIF 与参数</span>
          <ImageDrop
            accept="image/gif"
            hint={gif ? "换一个：点选 / 拖入 / 粘贴" : "点这里选 GIF，或直接拖进来 / 粘贴"}
            onPick={load}
          />

          {gif && (
            <>
          <div className="tool-row">
            <label>每行帧数
              <input type="number" min={0} max={64} value={cols}
                onChange={(e) => setCols(Math.max(0, Number(e.target.value) || 0))} />
              <small>0 = 全部铺一行</small>
            </label>
            <label>间距(px)
              <input type="number" min={0} max={64} value={padding}
                onChange={(e) => setPadding(Math.max(0, Number(e.target.value) || 0))} />
            </label>
            <label className="tool-check">
              <input type="checkbox" checked={transparent}
                onChange={(e) => setTransparent(e.target.checked)} />
              透明背景
            </label>
            <button className="btn" onClick={doCompose}>生成精灵图</button>
            <button className="btn" onClick={() => setPlaying((p) => !p)}>
              {playing
                ? <><Pause size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />暂停</>
                : <><Play size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />播放</>}
            </button>
            <button className="btn" onClick={() => { setGif(null); setSheet(""); setBusy(""); }}>
              清空
            </button>
          </div>
            </>
          )}
        </div>

        <div className="tool-pane-plain">
          <div className="tool-window">
            <div className="tool-window-head">
              <strong>动画预览</strong>
              <span>{gif ? `${kept.length} 帧 · ${gif.width}×${gif.height}` : "—"}</span>
            </div>
            <div className="tool-window-body">
              {gif && kept.length > 0
                ? <img src={kept[cur % kept.length].f} alt="动画预览" className="tool-anim" />
                : <span className="tool-window-empty">左侧选一个 GIF 开始。</span>}
            </div>
          </div>

          {/* 精灵图预览：先给前端即时排布，点「生成精灵图」后换成后端成品 */}
          <div className="tool-window">
            <div className="tool-window-head">
              <strong>精灵图预览</strong>
              <span>
                {sheet && preview
                  ? `已生成 ${preview.width}×${preview.height} · ${preview.cols}×${preview.rows} 格 · ${kept.length} 帧`
                  : preview
                    ? `${preview.width}×${preview.height} · ${preview.cols}×${preview.rows} 格 · ${kept.length} 帧`
                    : "—"}
              </span>
            </div>
            <div className="tool-window-body">
              {sheet
                ? <img src={sheet} alt="精灵图" className="tool-sheet" />
                : preview
                  ? <img src={preview.image} alt="精灵图预览" className="tool-sheet" />
                  : <span className="tool-window-empty">左侧选一个 GIF 后这里实时显示排布。</span>}
            </div>
          </div>

          {gif && (
            <>
          <span className="tool-label">
            全部帧（点击剔除，已剔 {dropped.size} 帧）
          </span>
          <div className="tool-frames">
            {gif.frames.map((f, i) => (
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
      {!gif && !busy && <StateHint>选一个 GIF 开始。</StateHint>}
    </PageShell>
  );
}
