import { useState } from "react";
import { probeModel, type ModelProbeResult } from "../api/modelProbe";
import { ggufImport, ggufScan } from "../api/ggufImporter";
import { ProbeResultView } from "./ModelProbeResultView";

type GgufPatch = { ggufPath?: string; mmprojPath?: string; ollamaName?: string };
type ScannedModel = { path: string; filename: string; size_bytes: number; is_vision: boolean };

/**
 * 本地模型文件（GGUF）→ 导入本机 Ollama。
 * 对话与视觉共用：唯一差别是文件检测是否要求视觉能力（useVision）。
 * 导入成功后回调真实模型名，由所属能力卡切到 Ollama 端点。
 */
export function LocalGgufImport({
  ggufPath, mmprojPath, ollamaName, useVision, onPatch, onImported,
}: {
  ggufPath?: string;
  mmprojPath?: string;
  ollamaName?: string;
  useVision: boolean;
  onPatch: (patch: GgufPatch) => void;
  onImported: (modelName: string) => void;
}) {
  const [scanDir, setScanDir] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScannedModel[] | null>(null);
  const [scanError, setScanError] = useState("");
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ModelProbeResult | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ ok: boolean; message: string } | null>(null);

  const scan = async () => {
    const dir = scanDir.trim();
    if (!dir) { setScanError("请先填写 GGUF 目录"); return; }
    setScanning(true);
    setScanError("");
    setScanResult(null);
    try {
      const result = await ggufScan(dir, 30_000);
      if (result.error) { setScanError(result.error); return; }
      setScanResult(result.models.map((model) => ({
        path: model.path, filename: model.filename,
        size_bytes: model.size_bytes, is_vision: model.is_vision,
      })));
    } catch (error) {
      setScanError(`扫描失败：${(error as Error).message}`);
    } finally {
      setScanning(false);
    }
  };

  const probeFile = async () => {
    setProbing(true);
    setProbeResult(null);
    try {
      setProbeResult(await probeModel({
        kind: useVision ? "vlm-local" : "chat-local",
        ggufPath: (ggufPath || "").trim(),
      }));
    } catch (error) {
      setProbeResult({ status: "error", message: (error as Error).message, billable: false });
    } finally {
      setProbing(false);
    }
  };

  const importToOllama = async () => {
    const path = (ggufPath || "").trim();
    if (!path) { setImportResult({ ok: false, message: "请先填写 GGUF 文件路径" }); return; }
    setImporting(true);
    setImportResult(null);
    try {
      const result = await ggufImport({
        ggufPath: path,
        modelName: (ollamaName || "").trim(),
        mmprojPath: (mmprojPath || "").trim(),
        registerProvider: true,
      }, 900_000);
      if (!result.ok) { setImportResult({ ok: false, message: result.message }); return; }
      const name = (ollamaName || "").trim() || result.model_name || "";
      if (name) onPatch({ ollamaName: name });
      setImportResult({ ok: true, message: name ? `导入成功：${name}` : result.message });
      if (name) onImported(name);
    } catch (error) {
      setImportResult({ ok: false, message: `导入异常：${(error as Error).message}` });
    } finally {
      setImporting(false);
    }
  };

  const label = useVision ? "视觉" : "对话";
  return <>
    <div className="field">
      <label>本地{label}模型文件（GGUF）</label>
      <input
        value={ggufPath || ""}
        onChange={(event) => onPatch({ ggufPath: event.target.value })}
        placeholder="D:\\models\\Qwen3-8B-Q4_K_M.gguf"
      />
      <p className="field-hint">
        「本地模型」= 本机 GGUF 文件经 Ollama 运行，全程不走云；文件导入后自动切到 Ollama 档。
        {useVision ? "视觉模型需带视觉编码器（mmproj 可自动配对）。" : ""}
      </p>
    </div>
    <div className="field">
      <label>扫描目录发现 GGUF（可选）</label>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={scanDir}
          onChange={(event) => setScanDir(event.target.value)}
          placeholder="D:\\models"
          style={{ flex: 1 }}
        />
        <button className="btn" type="button" onClick={scan} disabled={scanning}>
          {scanning ? "扫描中…" : "扫描"}
        </button>
      </div>
      {scanError && <p className="field-hint" style={{ color: "#c0392b" }}>{scanError}</p>}
      {scanResult && scanResult.length > 0 && (
        <div style={{ marginTop: 6, maxHeight: 160, overflowY: "auto", border: "1px solid var(--border, #e5e5e5)", borderRadius: 8 }}>
          {scanResult.map((model) => (
            <button
              key={model.path}
              type="button"
              className="model-preset-row"
              style={{ display: "flex", width: "100%", padding: "6px 8px", background: "none", border: "none", borderBottom: "1px solid var(--border, #eee)", cursor: "pointer", textAlign: "left", fontSize: 12 }}
              onClick={() => onPatch({ ggufPath: model.path })}
              title={model.path}
            >
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {model.is_vision ? "🖼️ " : "📦 "}{model.filename}
              </span>
              <span style={{ marginLeft: 8, opacity: 0.7 }}>{model.is_vision ? "视觉" : "非视觉"} · {(model.size_bytes / 1073741824).toFixed(1)}GB</span>
            </button>
          ))}
        </div>
      )}
      {scanResult && scanResult.length === 0 && (
        <p className="field-hint">该目录未发现 GGUF 主模型（mmproj 投影文件会单独列出）。</p>
      )}
    </div>
    <div className="field">
      <label>{useVision ? "视觉投影 mmproj（可选，自动配对）" : "视觉投影 mmproj（对话模型通常不需要）"}</label>
      <input
        value={mmprojPath || ""}
        onChange={(event) => onPatch({ mmprojPath: event.target.value })}
        placeholder="与主模型同目录的 *mmproj*.gguf；留空自动查找"
      />
    </div>
    <div className="field">
      <label>导入 Ollama 后的模型名</label>
      <input
        value={ollamaName || ""}
        onChange={(event) => onPatch({ ollamaName: event.target.value })}
        placeholder="留空按文件名自动生成"
      />
    </div>
    <div className="model-test-row">
      <button className="btn" type="button" onClick={probeFile} disabled={probing}>
        {probing ? "检测中…" : "检测模型文件"}
      </button>
      {probeResult && <ProbeResultView result={probeResult} />}
    </div>
    <div className="model-test-row">
      <button className="btn" type="button" onClick={importToOllama} disabled={importing}>
        {importing ? "导入中…" : "导入到 Ollama"}
      </button>
      {importResult && (
        <span className={`model-probe-result ${importResult.ok ? "success" : "error"}`}>
          {importResult.ok ? `✅ ${importResult.message}` : importResult.message}
        </span>
      )}
    </div>
  </>;
}
