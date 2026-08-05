import { useEffect, useState } from "react";
import { Check, Copy, Download, X } from "lucide-react";
import { PageShell, StateHint } from "../../components/layout/PageShell";
import {
  extractPalette, getPaletteConstraint, setPaletteConstraint,
} from "../../api/tools";
import { ImageDrop } from "./ImageDrop";

const DEPTHS = [
  { key: "rgb888", label: "原色 (8-8-8)" },
  { key: "rgb565", label: "RGB565" },
  { key: "rgb555", label: "RGB555" },
  { key: "rgb444", label: "RGB444" },
  { key: "rgb332", label: "RGB332" },
];

export function PaletteTab({ onBack, repoId }: { onBack: () => void; repoId: string }) {
  const [src, setSrc] = useState("");
  const [maxColors, setMaxColors] = useState(16);
  const [bitDepth, setBitDepth] = useState("rgb888");
  const [method, setMethod] = useState("mediancut");
  const [colors, setColors] = useState<string[]>([]);
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState("");
  // 当前生效的色彩约束（后端按小仓库存）
  const [active, setActive] = useState<string[]>([]);

  useEffect(() => {
    getPaletteConstraint(repoId)
      .then((r) => setActive(r.colors))
      .catch(() => { /* 没设过就是空，不用报错 */ });
  }, [repoId]);

  const doExtract = async (image = src) => {
    if (!image) return;
    setBusy("正在提取…");
    try {
      const r = await extractPalette(image, maxColors, bitDepth, method);
      setColors(r.colors);
      setPreview(r.preview);
      setBusy(`提取到 ${r.count} 个颜色（按占比排序）。`);
    } catch (e) {
      setBusy(`提取失败：${(e as Error).message}`);
    }
  };

  const copy = async (text: string, tag: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(tag);
      window.setTimeout(() => setCopied(""), 1500);
    } catch {
      setBusy("复制失败，请手动选中。");
    }
  };

  const applyConstraint = async () => {
    try {
      const r = await setPaletteConstraint(repoId, colors);
      setActive(r.colors);
      setBusy(`已设为当前色彩约束（${r.colors.length} 色）。之后 AI 编排生图会自动带上这组配色。`);
    } catch (e) {
      setBusy(`设置失败：${(e as Error).message}`);
    }
  };

  const clearConstraint = async () => {
    try {
      await setPaletteConstraint(repoId, []);
      setActive([]);
      setBusy("已清除色彩约束。");
    } catch (e) {
      setBusy(`清除失败：${(e as Error).message}`);
    }
  };

  const asCss = colors.map((c, i) => `  --c${i + 1}: ${c};`).join("\n");
  const cssBlock = `:root {\n${asCss}\n}`;
  // JSON 导出带上取色参数，方便回溯这组色是怎么来的
  const jsonBlock = JSON.stringify(
    { colors, count: colors.length, bit_depth: bitDepth, method }, null, 2);

  return (
    <PageShell
      title="调色盘"
      back={onBack}
      actions={
        colors.length > 0 && (
          <>
            <button className="btn" onClick={applyConstraint} title="之后 AI 编排生图自动带上这组配色">
              设为当前配色
            </button>
            <button className="btn" onClick={() => copy(colors.join(", "), "hex")}>
              {copied === "hex"
                ? <><Check size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />已复制</>
                : <><Copy size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />复制色号</>}
            </button>
            {preview && (
              <a className="btn" href={preview} download="quantized.png">
                <Download size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                下载量化图
              </a>
            )}
          </>
        )
      }
    >
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
        从图片提取主色。设为「当前配色」后，这个小仓库里的 AI 编排生图会自动把色号追加到正向提示词
        （你自己在需求里提了配色时不会覆盖）。
      </p>

      {active.length > 0 && (
        <div className="tool-active-palette">
          <span className="tool-label">当前生效的色彩约束</span>
          <div className="tool-swatches">
            {active.map((c) => (
              <span key={c} className="tool-swatch" style={{ background: c }} title={c} />
            ))}
          </div>
          <button className="btn" onClick={clearConstraint}>
            <X size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />清除
          </button>
        </div>
      )}

      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}

      <div className="tool-split">
        <div className="tool-pane">
          <span className="tool-pane-title">图片与参数</span>
          <ImageDrop
            accept="image/*"
            hint={src ? "换一张：点选 / 拖入 / 粘贴" : "点这里选图片，或直接拖进来 / 粘贴"}
            onPick={(d) => { setSrc(d); setColors([]); setPreview(""); void doExtract(d); }}
          />

          {src && (
            <>
          <div className="tool-row">
            <label>色数
              <input type="number" min={2} max={256} value={maxColors}
                onChange={(e) => setMaxColors(Math.min(256, Math.max(2, Number(e.target.value) || 16)))} />
            </label>
            <label>位深
              <select value={bitDepth} onChange={(e) => setBitDepth(e.target.value)}>
                {DEPTHS.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
              </select>
            </label>
            <label>算法
              <select value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="mediancut">中位切分</option>
                <option value="octree">八叉树（快）</option>
              </select>
            </label>
            <button className="btn" onClick={() => void doExtract()}>重新提取</button>
            <button className="btn" onClick={() => { setSrc(""); setColors([]); setPreview(""); setBusy(""); }}>
              清空
            </button>
            {colors.length > 0 && (
              <>
                <button className="btn" onClick={() => copy(cssBlock, "css")}
                        title="复制成 :root { --c1: ... } 形式">
                  {copied === "css"
                    ? <><Check size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />已复制 CSS</>
                    : <><Copy size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />复制 CSS 变量</>}
                </button>
                <button className="btn" onClick={() => copy(jsonBlock, "json")}
                        title="复制成 JSON（含色数与取色参数）">
                  {copied === "json"
                    ? <><Check size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />已复制 JSON</>
                    : <><Copy size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />复制 JSON</>}
                </button>
              </>
            )}
          </div>
            </>
          )}
        </div>

        <div className="tool-pane-plain">
          <div className="tool-window">
            <div className="tool-window-head"><strong>原图</strong></div>
            <div className="tool-window-body">
              {src
                ? <img src={src} alt="原图" className="tool-sheet" />
                : <span className="tool-window-empty">左侧选一张图片开始。</span>}
            </div>
          </div>

          <div className="tool-window">
            <div className="tool-window-head">
              <strong>量化结果</strong>
              {colors.length > 0 && <span>{colors.length} 色</span>}
            </div>
            <div className="tool-window-body">
              {preview
                ? <img src={preview} alt="量化结果" className="tool-sheet" />
                : <span className="tool-window-empty">选好图片会自动提取。</span>}
            </div>
          </div>

          {colors.length > 0 && (
            <>
              <span className="tool-label">调色盘（点色块复制单色）</span>
              <div className="tool-swatches">
                {colors.map((c) => (
                  <button
                    key={c}
                    className="tool-swatch tool-swatch-btn"
                    style={{ background: c }}
                    title={`${c}（点击复制）`}
                    onClick={() => copy(c, c)}
                  >
                    {copied === c && <Check size={13} />}
                  </button>
                ))}
              </div>
              {/* 复制按钮已挪到左栏参数区，这里只留可展开的原文供核对 */}
              <details className="tool-details">
                <summary>CSS 变量</summary>
                <pre className="tool-code">{cssBlock}</pre>
              </details>
              <details className="tool-details">
                <summary>JSON</summary>
                <pre className="tool-code">{jsonBlock}</pre>
              </details>
            </>
          )}
        </div>
      </div>
      {!src && !busy && active.length === 0 && <StateHint>选一张图片开始。</StateHint>}
    </PageShell>
  );
}
