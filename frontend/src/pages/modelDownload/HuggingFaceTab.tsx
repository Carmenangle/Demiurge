import { useState } from "react";
import { Download, CheckSquare, Square } from "lucide-react";
import { type Settings } from "../../stores/settings";
import { downloadModel } from "../../api/models";
import { HF_PRESETS, type HFPreset } from "./hfPresets";

// HuggingFace 常用模型预设清单：分组勾选 + 批量下载（对齐截图2）。
export function HuggingFaceTab({ settings, modelsDir }: { settings: Settings; modelsDir: string }) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState("");

  const all = HF_PRESETS.flatMap((g) => g.items);
  const toggle = (url: string) =>
    setSel((s) => { const n = new Set(s); n.has(url) ? n.delete(url) : n.add(url); return n; });
  const selectAll = () => setSel(new Set(all.map((i) => i.url)));
  const clear = () => setSel(new Set());

  const download = async (items: HFPreset[]) => {
    if (!modelsDir) { setMsg("未配置模型目录（设置 → 路径）。"); return; }
    if (items.length === 0) { setMsg("请先勾选要下载的模型。"); return; }
    setMsg(`已提交 ${items.length} 个模型下载（后台进行，切页不中断）。进度见上方「下载任务」面板。`);
    for (const it of items) {
      try {
        await downloadModel({ url: it.url, modelType: it.type, modelsDir, hfToken: settings.hfToken, name: it.name, proxy: settings.proxyEnabled ? settings.proxyUrl : "" });
      } catch (e) {
        setMsg(`「${it.name}」下载失败：${(e as Error).message}`);
      }
    }
  };

  const selectedItems = all.filter((i) => sel.has(i.url));

  return (
    <div>
      <div className="page-toolbar">
        <button className="btn" onClick={() => download(selectedItems)} disabled={sel.size === 0}>
          <Download size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />导入所选（{sel.size}）
        </button>
        <button className="btn" onClick={selectAll}><CheckSquare size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />全选</button>
        <button className="btn" onClick={clear} disabled={sel.size === 0}><Square size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />清除选择</button>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>常用模型预设，勾选批量下载；更多模型用「链接下载」tab</span>
      </div>
      {msg && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{msg}</p>}

      {HF_PRESETS.map((g) => (
        <div key={g.label} className="hf-group">
          <div className="hf-group-title">{g.label}</div>
          <div className="hf-items">
            {g.items.map((it) => (
              <label className={`hf-item ${sel.has(it.url) ? "sel" : ""}`} key={it.url}>
                <input type="checkbox" checked={sel.has(it.url)} onChange={() => toggle(it.url)} />
                <span className="hf-item-name">{it.name}</span>
                <button className="hf-item-dl" title="单独下载" onClick={(e) => { e.preventDefault(); download([it]); }}>
                  <Download size={13} />
                </button>
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
