import { useMemo, useState } from "react";
import {
  ArrowRightLeft, Braces, CaseSensitive, Clipboard, Eraser, ExternalLink,
  Sparkles, TextQuote, Trash2, Wrench, X,
} from "lucide-react";
import {
  DEFAULT_QUICK_TEXT_OPTIONS, runQuickTextTool,
  type QuickTextTool, type QuickTextToolOptions,
} from "../lib/quickTextTools";

const TOOLS: { key: QuickTextTool; label: string; icon: typeof Wrench }[] = [
  { key: "clean", label: "清理", icon: Eraser },
  { key: "join", label: "拼接", icon: ArrowRightLeft },
  { key: "insert", label: "加料", icon: Sparkles },
  { key: "stats", label: "统计", icon: CaseSensitive },
  { key: "escape", label: "转义", icon: Braces },
  { key: "convert", label: "简繁", icon: TextQuote },
];

export function QuickToolsPanel({ onClose, onOpenFull }: {
  onClose: () => void;
  onOpenFull: () => void;
}) {
  const [tool, setTool] = useState<QuickTextTool>("clean");
  const [input, setInput] = useState("");
  const [options, setOptions] = useState<QuickTextToolOptions>(DEFAULT_QUICK_TEXT_OPTIONS);
  const [copied, setCopied] = useState(false);
  const result = useMemo(() => {
    try { return { text: runQuickTextTool(tool, input, options), error: "" }; }
    catch (error) { return { text: "", error: (error as Error).message }; }
  }, [input, options, tool]);
  const patchOptions = (patch: Partial<QuickTextToolOptions>) => {
    setOptions((current) => ({ ...current, ...patch }));
  };
  const copy = async () => {
    await navigator.clipboard.writeText(result.text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <section className="quick-tools-panel" aria-label="快捷工具">
      <header>
        <strong>快捷工具</strong>
        <div>
          <button className="icon-btn" title="进入完整工具页" onClick={onOpenFull}>
            <ExternalLink size={17} />
          </button>
          <button className="icon-btn" title="关闭" onClick={onClose}><X size={18} /></button>
        </div>
      </header>
      <div className="quick-tools-tabs" role="tablist" aria-label="选择文本工具">
        {TOOLS.map((item) => (
          <button key={item.key} role="tab" aria-selected={tool === item.key}
            className={tool === item.key ? "active" : ""} onClick={() => setTool(item.key)}
            title={item.label}>
            <item.icon size={16} /><span>{item.label}</span>
          </button>
        ))}
      </div>
      <QuickOptions tool={tool} options={options} patch={patchOptions} />
      <label className="quick-tools-field">
        <span>输入</span>
        <textarea aria-label="快捷工具输入" value={input} onChange={(event) => setInput(event.target.value)}
          placeholder="输入或粘贴文本…" />
      </label>
      <label className="quick-tools-field">
        <span>结果</span>
        <textarea aria-label="快捷工具结果" value={result.text} readOnly placeholder="结果会显示在这里" />
      </label>
      {result.error && <p className="quick-tools-error">{result.error}</p>}
      <footer>
        <button className="btn" disabled={!input} onClick={() => setInput("")}><Trash2 size={14} />清空</button>
        <button className="btn primary" disabled={!result.text} onClick={copy}>
          <Clipboard size={14} />{copied ? "已复制" : "复制结果"}
        </button>
      </footer>
    </section>
  );
}

function QuickOptions({ tool, options, patch }: {
  tool: QuickTextTool;
  options: QuickTextToolOptions;
  patch: (value: Partial<QuickTextToolOptions>) => void;
}) {
  if (tool === "clean") return <div className="quick-tools-options">
    <label><input type="checkbox" checked={options.cleanMarkdown} onChange={(e) => patch({ cleanMarkdown: e.target.checked })} />Markdown</label>
    <label><input type="checkbox" checked={options.cleanBlankLines} onChange={(e) => patch({ cleanBlankLines: e.target.checked })} />空行</label>
  </div>;
  if (tool === "join") return <div className="quick-tools-options">
    <label>分隔符<input value={options.separator} onChange={(e) => patch({ separator: e.target.value })} /></label>
    <label><input type="checkbox" checked={options.skipBlankLines} onChange={(e) => patch({ skipBlankLines: e.target.checked })} />跳过空行</label>
  </div>;
  if (tool === "insert") return <div className="quick-tools-options">
    <label>插入内容<input value={options.addition} onChange={(e) => patch({ addition: e.target.value })} /></label>
  </div>;
  if (tool === "escape") return <div className="quick-tools-options">
    <select aria-label="转义方向" value={options.escapeDirection} onChange={(e) => patch({ escapeDirection: e.target.value as QuickTextToolOptions["escapeDirection"] })}>
      <option value="encode">字符串 → 转义</option><option value="decode">转义 → 字符串</option>
    </select>
    {options.escapeDirection === "encode" && <select aria-label="转义格式" value={options.escapeFormat} onChange={(e) => patch({ escapeFormat: e.target.value as QuickTextToolOptions["escapeFormat"] })}>
      <option value="python">Python 字节</option><option value="hex">Hex</option><option value="json">JSON</option>
    </select>}
  </div>;
  if (tool === "convert") return <div className="quick-tools-options">
    <select aria-label="简繁方向" value={options.convertDirection} onChange={(e) => patch({ convertDirection: e.target.value as QuickTextToolOptions["convertDirection"] })}>
      <option value="to-simplified">繁体 → 简体</option><option value="to-traditional">简体 → 繁体</option>
    </select>
    <label><input type="checkbox" checked={options.replaceQuotes} onChange={(e) => patch({ replaceQuotes: e.target.checked })} />「」→【】</label>
  </div>;
  return <div className="quick-tools-options quick-tools-options-muted">实时统计</div>;
}
