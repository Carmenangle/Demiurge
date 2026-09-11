import { useState } from "react";
import { Download } from "lucide-react";
import { PageShell, StateHint } from "../../components/layout/PageShell";
import { probeImage, resizeImage } from "../../api/tools";
import { ImageDrop } from "./ImageDrop";

// 常用档位。按长边给，短边按原图比例算 —— 素材长宽比五花八门，写死短边会变形。
const PRESETS = [
  { label: "4K (3840)", side: 3840 },
  { label: "2K (2560)", side: 2560 },
  { label: "1080p (1920)", side: 1920 },
  { label: "1K (1024)", side: 1024 },
  { label: "768", side: 768 },
  { label: "512", side: 512 },
];

const FILTERS = [
  { key: "lanczos", label: "Lanczos（最佳，推荐）" },
  { key: "bicubic", label: "Bicubic" },
  { key: "box", label: "Box（等比缩小快）" },
  { key: "bilinear", label: "Bilinear" },
  { key: "nearest", label: "Nearest（像素画专用）" },
];

export function ResizeTab({ onBack }: { onBack: () => void }) {
  const [src, setSrc] = useState("");
  const [fileName, setFileName] = useState("");
  const [orig, setOrig] = useState<{ width: number; height: number } | null>(null);
  const [targetW, setTargetW] = useState(0);
  const [targetH, setTargetH] = useState(0);
  const [keepAspect, setKeepAspect] = useState(true);
  const [filterName, setFilterName] = useState("lanczos");
  const [sharpen, setSharpen] = useState(0);
  const [format, setFormat] = useState("png");
  const [quality, setQuality] = useState(92);
  const [out, setOut] = useState<{ image: string; width: number; height: number; bytes: number } | null>(null);
  const [busy, setBusy] = useState("");
  const [activePreset, setActivePreset] = useState<number | null>(null); // 当前高亮的档位长边
  const [resizing, setResizing] = useState(false); // 缩放进行中 → 显示进度条

  const load = async (dataUri: string, name: string) => {
    setSrc(dataUri);
    setFileName(name);
    setOut(null);
    setActivePreset(null);
    setBusy("正在读取尺寸…");
    try {
      const r = await probeImage(dataUri);
      setOrig(r);
      // 默认按长边减半，这是最常见的诉求（2K→1K）
      const longSide = Math.max(r.width, r.height);
      applyLongSide(Math.round(longSide / 2), r);
      setBusy(`原图 ${r.width}×${r.height}。`);
    } catch (e) {
      setBusy(`读取失败：${(e as Error).message}`);
    }
  };

  // 按长边设定目标：短边留 0 交后端按比例算，避免前端四舍五入与后端不一致
  const applyLongSide = (side: number, o = orig) => {
    if (!o) return;
    if (o.width >= o.height) { setTargetW(side); setTargetH(0); }
    else { setTargetH(side); setTargetW(0); }
  };

  const doResize = async () => {
    if (!src) return;
    if (targetW <= 0 && targetH <= 0) { setBusy("请先给出目标尺寸。"); return; }
    setResizing(true);
    setBusy("正在缩放…");
    try {
      const r = await resizeImage(src, {
        targetW, targetH, keepAspect, filterName, sharpen, format, quality,
      });
      setOut(r);
      const kb = (r.bytes / 1024).toFixed(0);
      setBusy(`已生成 ${r.width}×${r.height}，${kb} KB。`);
    } catch (e) {
      setBusy(`缩放失败：${(e as Error).message}`);
    } finally {
      setResizing(false);
    }
  };

  const ext = format === "jpeg" ? "jpg" : format;
  const baseName = fileName.replace(/\.[^.]+$/, "") || "resized";
  const scalePct = orig && out
    ? Math.round((out.width / orig.width) * 100)
    : 0;

  return (
    <PageShell
      title="分辨率缩放"
      back={onBack}
      actions={
        out && (
          <a className="btn" href={out.image}
             download={`${baseName}-${out.width}x${out.height}.${ext}`}>
            <Download size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            下载
          </a>
        )
      }
    >
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
        缩小走 Lanczos（实测在各滤镜里保真度最高），PNG 输出无损。
        注意：放大只能做几何插值，变不出原图没有的细节；要补细节得用 ComfyUI 的超分模型。
      </p>

      {resizing ? (
        <div className="tool-progress">
          <div className="tool-progress-bar" />
          <span className="tool-progress-txt">正在缩放，请稍候…</span>
        </div>
      ) : (
        busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>
      )}

      <div className="tool-split">
        <div className="tool-pane">
          <span className="tool-pane-title">图片与参数</span>
          <ImageDrop
            accept="image/*"
            hint={src ? "换一张：点选 / 拖入 / 粘贴" : "点这里选图片，或直接拖进来 / 粘贴"}
            onPick={load}
          />

          {src && orig && (
            <>
          <div className="tool-row">
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              常用档位（按长边）
            </span>
            {PRESETS.map((p) => (
              <button
                key={p.side}
                className={`btn${activePreset === p.side ? " active" : ""}`}
                onClick={() => { setActivePreset(p.side); applyLongSide(p.side); }}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="tool-row">
            <label>宽
              <input type="number" min={0} value={targetW}
                onChange={(e) => { setActivePreset(null); setTargetW(Math.max(0, Number(e.target.value) || 0)); }} />
              <small>0 = 按比例</small>
            </label>
            <label>高
              <input type="number" min={0} value={targetH}
                onChange={(e) => { setActivePreset(null); setTargetH(Math.max(0, Number(e.target.value) || 0)); }} />
              <small>0 = 按比例</small>
            </label>
            <label className="tool-check">
              <input type="checkbox" checked={keepAspect}
                onChange={(e) => setKeepAspect(e.target.checked)} />
              保持长宽比
            </label>
            <label>重采样
              <select value={filterName} onChange={(e) => setFilterName(e.target.value)}>
                {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
              </select>
            </label>
          </div>

          <div className="tool-row">
            <label>锐化 {sharpen}%
              <input type="range" min={0} max={120} step={10} value={sharpen}
                style={{ width: 150 }}
                onChange={(e) => setSharpen(Number(e.target.value))} />
              <small>实测超过 30% 反而失真，默认关</small>
            </label>
            <label>格式
              <select value={format} onChange={(e) => setFormat(e.target.value)}>
                <option value="png">PNG（无损）</option>
                <option value="jpeg">JPEG</option>
                <option value="webp">WebP</option>
              </select>
            </label>
            {format !== "png" && (
              <label>质量
                <input type="number" min={1} max={100} value={quality}
                  onChange={(e) => setQuality(Math.min(100, Math.max(1, Number(e.target.value) || 92)))} />
              </label>
            )}
            <button className="btn primary" onClick={doResize} disabled={resizing}>
              {resizing ? "缩放中…" : "开始缩放"}
            </button>
            <button className="btn" disabled={resizing}
              onClick={() => { setSrc(""); setOut(null); setOrig(null); setBusy(""); setActivePreset(null); }}>
              清空
            </button>
          </div>
            </>
          )}
        </div>

        {/* 右栏：原图 / 结果各一个预览窗，上下叠放便于同宽对比 */}
        <div className="tool-pane-plain">
          <div className="tool-window">
            <div className="tool-window-head">
              <strong>原图</strong>
              <span>{orig ? `${orig.width}×${orig.height}` : "—"}</span>
            </div>
            <div className="tool-window-body">
              {src
                ? <img src={src} alt="原图" className="tool-sheet" />
                : <span className="tool-window-empty">左侧选一张图片开始。</span>}
            </div>
          </div>

          <div className="tool-window">
            <div className="tool-window-head">
              <strong>缩放结果</strong>
              <span>
                {out ? `${out.width}×${out.height}（${scalePct}%）· ${(out.bytes / 1024).toFixed(0)} KB` : "—"}
              </span>
            </div>
            <div className="tool-window-body">
              {out
                ? <img src={out.image} alt="缩放结果" className="tool-sheet" />
                : <span className="tool-window-empty">设好参数后点「开始缩放」。</span>}
            </div>
          </div>
        </div>
      </div>
      {!src && !busy && <StateHint>选一张图片开始。</StateHint>}
    </PageShell>
  );
}
