import { useMemo, useState, type ReactNode } from "react";
import { ArrowRightLeft, Clipboard, Download, Eraser } from "lucide-react";
import { PageShell } from "../../components/layout/PageShell";
import { downloadText } from "../../lib/download";
import {
  cleanText, convertChinese, countText, escapeText, insertBetweenCharacters, joinText, unescapeText,
  type EscapeFormat,
} from "../../lib/textTools";

function ToolActions({ text, filename }: { text: string; filename: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="text-tool-actions">
      <button className="btn" disabled={!text} onClick={copy}><Clipboard size={14} />{copied ? "已复制" : "复制"}</button>
      <button className="btn" disabled={!text} onClick={() => downloadText(text, filename)}><Download size={14} />下载 TXT</button>
    </div>
  );
}

function Workspace({ input, onInput, output, outputTitle = "处理结果", children }: {
  input: string; onInput: (value: string) => void; output: string; outputTitle?: string; children?: ReactNode;
}) {
  return (
    <div className="text-tool-grid">
      <label className="text-tool-pane">
        <span className="text-tool-pane-title">原始文本</span>
        <textarea value={input} onChange={(event) => onInput(event.target.value)} placeholder="在这里输入或粘贴文本…" />
      </label>
      <div className="text-tool-pane">
        <span className="text-tool-pane-title">{outputTitle}</span>
        {children || <textarea value={output} readOnly placeholder="结果会显示在这里" />}
      </div>
    </div>
  );
}

function Header({ children }: { children: ReactNode }) {
  return <div className="text-tool-options">{children}</div>;
}

export function TextCleanTab({ onBack }: { onBack: () => void }) {
  const [input, setInput] = useState("");
  const [markdown, setMarkdown] = useState(true);
  const [blank, setBlank] = useState(true);
  const output = useMemo(() => cleanText(input, markdown, blank), [input, markdown, blank]);
  return <PageShell title="文本清理" back={onBack} actions={<ToolActions text={output} filename="清理结果" />}>
    <Header>
      <label><input type="checkbox" checked={markdown} onChange={(e) => setMarkdown(e.target.checked)} />移除 Markdown 标记</label>
      <label><input type="checkbox" checked={blank} onChange={(e) => setBlank(e.target.checked)} />移除空行</label>
      <span className="text-tool-live"><Eraser size={14} />实时处理</span>
    </Header>
    <Workspace input={input} onInput={setInput} output={output} />
  </PageShell>;
}

export function TextJoinTab({ onBack }: { onBack: () => void }) {
  const [input, setInput] = useState("");
  const [separator, setSeparator] = useState("\\n");
  const [skipBlank, setSkipBlank] = useState(true);
  const actualSeparator = separator.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
  const output = useMemo(() => joinText(input, actualSeparator, skipBlank), [input, actualSeparator, skipBlank]);
  return <PageShell title="文本拼接" back={onBack} actions={<ToolActions text={output} filename="拼接结果" />}>
    <Header>
      <label>分隔符<input className="text-tool-short-input" value={separator} onChange={(e) => setSeparator(e.target.value)} /></label>
      <span className="text-tool-help">支持 \n 换行、\t 制表符</span>
      <label><input type="checkbox" checked={skipBlank} onChange={(e) => setSkipBlank(e.target.checked)} />跳过空行</label>
    </Header>
    <Workspace input={input} onInput={setInput} output={output} outputTitle="拼接预览" />
  </PageShell>;
}

export function TextInsertTab({ onBack }: { onBack: () => void }) {
  const [input, setInput] = useState("");
  const [addition, setAddition] = useState("\u200b");
  const output = useMemo(() => insertBetweenCharacters(input, addition), [input, addition]);
  return <PageShell title="文本加料" back={onBack} actions={<ToolActions text={output} filename="加料结果" />}>
    <Header>
      <label>插入字符串<input className="text-tool-short-input" value={addition} onChange={(e) => setAddition(e.target.value)} /></label>
      <span className="text-tool-help">默认是零宽空格，插入到每两个字符之间</span>
    </Header>
    <Workspace input={input} onInput={setInput} output={output} />
  </PageShell>;
}

export function TextStatsTab({ onBack }: { onBack: () => void }) {
  const [input, setInput] = useState("");
  const stats = useMemo(() => countText(input), [input]);
  return <PageShell title="字数统计" back={onBack}>
    <div className="text-stat-grid">
      {[['汉字 / 日文', stats.cjk], ['英文单词', stats.englishWords], ['标点符号', stats.punctuation], ['字符总数', stats.characters], ['行数', stats.lines]].map(([label, value]) => (
        <div className="text-stat" key={label}><strong>{value}</strong><span>{label}</span></div>
      ))}
    </div>
    <label className="text-tool-pane text-tool-single">
      <span className="text-tool-pane-title">统计文本</span>
      <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="输入后实时统计…" />
    </label>
  </PageShell>;
}

export function TextEscapeTab({ onBack }: { onBack: () => void }) {
  const [input, setInput] = useState("");
  const [direction, setDirection] = useState<"encode" | "decode">("encode");
  const [format, setFormat] = useState<EscapeFormat>("python");
  const [upper, setUpper] = useState(false);
  const result = useMemo(() => {
    try { return { output: direction === "encode" ? escapeText(input, format, upper) : unescapeText(input), error: "" }; }
    catch (e) { return { output: "", error: (e as Error).message }; }
  }, [input, direction, format, upper]);
  return <PageShell title="文本转义" back={onBack} actions={<ToolActions text={result.output} filename="转义结果" />}>
    <Header>
      <div className="segmented">
        <button className={direction === "encode" ? "active" : ""} onClick={() => setDirection("encode")}>字符串 → 转义</button>
        <button className={direction === "decode" ? "active" : ""} onClick={() => setDirection("decode")}>转义 → 字符串</button>
      </div>
      {direction === "encode" && <select value={format} onChange={(e) => setFormat(e.target.value as EscapeFormat)}>
        <option value="python">Python 字节字面量</option><option value="hex">Hex</option><option value="json">JSON 字符串</option>
      </select>}
      {direction === "encode" && format !== "json" && <label><input type="checkbox" checked={upper} onChange={(e) => setUpper(e.target.checked)} />Hex 大写</label>}
      <span className="text-tool-help">UTF-8</span>
    </Header>
    {result.error && <p className="text-tool-error">{result.error}</p>}
    <Workspace input={input} onInput={setInput} output={result.output} />
  </PageShell>;
}

export function ChineseConvertTab({ onBack }: { onBack: () => void }) {
  const [input, setInput] = useState("");
  const [direction, setDirection] = useState<"to-simplified" | "to-traditional">("to-simplified");
  const [quotes, setQuotes] = useState(false);
  const output = useMemo(() => convertChinese(input, direction, quotes), [input, direction, quotes]);
  return <PageShell title="简繁切换" back={onBack} actions={<ToolActions text={output} filename="简繁转换结果" />}>
    <Header>
      <div className="segmented">
        <button className={direction === "to-simplified" ? "active" : ""} onClick={() => setDirection("to-simplified")}>繁体 → 简体</button>
        <button className={direction === "to-traditional" ? "active" : ""} onClick={() => setDirection("to-traditional")}>简体 → 繁体</button>
      </div>
      <label><input type="checkbox" checked={quotes} onChange={(e) => setQuotes(e.target.checked)} />将「」替换为【】</label>
      <span className="text-tool-live"><ArrowRightLeft size={14} />实时转换</span>
    </Header>
    <Workspace input={input} onInput={setInput} output={output} />
  </PageShell>;
}
