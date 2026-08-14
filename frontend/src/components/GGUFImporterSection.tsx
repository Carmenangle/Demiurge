// GGUF 模型导入面板：在「模型」设置面板的 Embedding 区块下方嵌入
import { useState, useCallback } from "react";
import {
  FolderOpen, HardDrive, PlayCircle, RefreshCw, Search, CheckCircle2, XCircle, AlertCircle, Loader2,
} from "lucide-react";
import {
  ggufStatus, ggufScan, ggufParse, ggufImport,
  type GgufMeta, type GgufFit, type GgufStatus, type GgufParseResult, type GgufImportResult,
} from "../api/ggufImporter";

function sizeLabel(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function FitBadge({ fit }: { fit: GgufFit }) {
  const map: Record<string, { color: string; bg: string; label: string }> = {
    ok: { color: "#16a34a", bg: "#dcfce7", label: "可运行" },
    partial_offload: { color: "#d97706", bg: "#fef3c7", label: "GPU 分层" },
    low: { color: "#dc2626", bg: "#fee2e2", label: "显存不足" },
    cpu_only: { color: "#7c3aed", bg: "#ede9fe", label: "仅 CPU" },
  };
  const s = map[fit.level] || map.low;
  return (
    <span style={{ color: s.color, background: s.bg, borderRadius: 4, padding: "2px 8px", fontSize: 12, fontWeight: 600 }}>
      {s.label}
    </span>
  );
}

function MetaRow({ meta }: { meta: GgufMeta }) {
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 12, color: "#6b7280", margin: "4px 0" }}>
      <span>架构：<strong>{meta.architecture}</strong></span>
      <span>量化：<strong>{meta.quant}</strong></span>
      <span>参数量：<strong>{meta.parameters_b} B</strong></span>
      <span>大小：<strong>{sizeLabel(meta.size_bytes)}</strong></span>
      {meta.context_length > 0 && <span>上下文：<strong>{meta.context_length.toLocaleString()}</strong></span>}
      {meta.is_vision && <span style={{ color: "#7c3aed", fontWeight: 700 }}>📷 视觉</span>}
      {meta.is_embedding && <span style={{ color: "#0891b2", fontWeight: 700 }}>🔢 嵌入</span>}
    </div>
  );
}

export function GGUFImporterSection() {
  // Ollama 状态
  const [status, setStatus] = useState<GgufStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);

  // 扫描
  const [dirInput, setDirInput] = useState("");        // 用户输入的目录路径
  const [scanDir, setScanDir] = useState("");          // 已扫描的目录
  const [scanResult, setScanResult] = useState<{ models: GgufMeta[]; mmproj: GgufMeta[] } | null>(null);
  const [scanError, setScanError] = useState("");
  const [scanning, setScanning] = useState(false);

  // 选中模型 → parse 结果
  const [selected, setSelected] = useState<GgufMeta | null>(null);
  const [parseResult, setParseResult] = useState<GgufParseResult | null>(null);
  const [parseLoading, setParseLoading] = useState(false);

  // 导入
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<GgufImportResult | null>(null);
  const [importError, setImportError] = useState("");

  // 刷新状态
  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      setStatus(await ggufStatus(8000));
    } catch {
      setStatus(null);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  // 扫描目录
  const doScan = useCallback(async () => {
    const dir = dirInput.trim();
    if (!dir) return;
    setScanning(true);
    setScanError("");
    setScanResult(null);
    setSelected(null);
    setParseResult(null);
    setImportResult(null);
    setImportError("");
    try {
      const r = await ggufScan(dir, 30000);
      if (r.error) { setScanError(r.error); return; }
      setScanDir(dir);
      setScanResult({ models: r.models, mmproj: r.mmproj });
    } catch (e) {
      setScanError(`扫描失败：${(e as Error).message}`);
    } finally {
      setScanning(false);
    }
  }, [dirInput]);

  // 点一个模型 → parse
  const selectModel = useCallback(async (meta: GgufMeta) => {
    setSelected(meta);
    setParseResult(null);
    setImportResult(null);
    setImportError("");
    setParseLoading(true);
    try {
      const r = await ggufParse(meta.path, 15000);
      setParseResult(r);
    } catch (e) {
      setParseError(`解析失败：${(e as Error).message}`);
    } finally {
      setParseLoading(false);
    }
  }, []);

  const [parseError, setParseError] = useState("");

  // 导入
  const doImport = useCallback(async () => {
    if (!selected) return;
    setImporting(true);
    setImportResult(null);
    setImportError("");
    try {
      const r = await ggufImport({
        ggufPath: selected.path,
        modelName: parseResult?.suggested_name || "",
        mmprojPath: scanResult?.mmproj[0]?.path || "",
        registerProvider: true,
      }, 900_000);   // 900s = 15min
      setImportResult(r);
      if (r.ok) {
        await refreshStatus(); // 刷新已安装列表
      }
    } catch (e) {
      setImportError(`导入异常：${(e as Error).message}`);
    } finally {
      setImporting(false);
    }
  }, [selected, parseResult, scanResult, refreshStatus]);

  return (
    <div className="settings-section" style={{ marginTop: 16, border: "1.5px dashed #a78bfa", borderRadius: 8, padding: "12px 16px" }}>
      {/* 标题栏 */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 16 }}>🔮</span>
        <strong style={{ fontSize: 14 }}>GGUF 模型导入</strong>
        <span style={{ fontSize: 12, color: "#9ca3af" }}>扫描本地 GGUF 文件，导入 Ollama，立即可用于 Visual CI / 对话</span>
        <button
          className="btn"
          style={{ marginLeft: "auto", padding: "3px 10px", fontSize: 12 }}
          onClick={refreshStatus}
          disabled={statusLoading}
        >
          <RefreshCw size={12} style={{ display: "inline", marginRight: 4 }} />
          {statusLoading ? "刷新中…" : "刷新 Ollama 状态"}
        </button>
      </div>

      {/* Ollama 状态 */}
      {status && (
        <div style={{ background: "#f9fafb", borderRadius: 6, padding: "8px 12px", marginBottom: 12, fontSize: 12 }}>
          <strong style={{ color: status.running ? "#16a34a" : "#dc2626" }}>
            {status.running ? "✅ Ollama 运行中" : "❌ Ollama 未运行"}
          </strong>
          <span style={{ color: "#6b7280", marginLeft: 8 }}>
            已安装 {status.count} 个模型：
          </span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
            {status.models.map((m: string) => (
              <code key={m} style={{ background: "#e5e7eb", borderRadius: 4, padding: "1px 6px", fontSize: 11 }}>{m}</code>
            ))}
          </div>
        </div>
      )}

      {/* 扫描区 */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input
          value={dirInput}
          onChange={(e) => setDirInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doScan()}
          placeholder="粘贴 GGUF 文件所在目录路径，例如：D:\tool\ComfyUI\models\LLM"
          style={{
            flex: 1, padding: "6px 10px", border: "1px solid #d1d5db",
            borderRadius: 6, fontSize: 13,
          }}
        />
        <button className="btn primary" onClick={doScan} disabled={scanning || !dirInput.trim()}>
          {scanning ? <Loader2 size={13} className="spin" /> : <Search size={13} />}
          {scanning ? "扫描中…" : "扫描目录"}
        </button>
      </div>

      {/* 扫描错误 */}
      {scanError && (
        <div style={{ color: "#dc2626", fontSize: 12, marginBottom: 8 }}>⚠️ {scanError}</div>
      )}

      {/* 扫描结果 */}
      {scanResult && (
        <>
          <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 6 }}>
            在 <code>{scanDir}</code> 找到 {scanResult.models.length} 个主模型、{scanResult.mmproj.length} 个视觉投影（mmproj）
          </div>

          {/* 主模型列表 */}
          {scanResult.models.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
              {scanResult.models.map((m) => (
                <div
                  key={m.path}
                  onClick={() => selectModel(m)}
                  style={{
                    cursor: "pointer",
                    border: selected?.path === m.path ? "2px solid #7c3aed" : "1.5px solid #e5e7eb",
                    borderRadius: 8, padding: "8px 12px",
                    background: selected?.path === m.path ? "#f5f3ff" : "#fff",
                    transition: "all 0.15s",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <HardDrive size={14} color={selected?.path === m.path ? "#7c3aed" : "#6b7280"} />
                    <strong style={{ fontSize: 13 }}>{m.filename}</strong>
                    <MetaRow meta={m} />
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* mmproj 提示 */}
          {scanResult.mmproj.length > 0 && (
            <div style={{ fontSize: 11, color: "#7c3aed", marginBottom: 10, background: "#f5f3ff", borderRadius: 6, padding: "4px 10px" }}>
              📷 视觉投影文件（导入时自动配对）：{scanResult.mmproj.map((m) => m.filename).join("、")}
            </div>
          )}
        </>
      )}

      {/* 选中模型详情 */}
      {(selected || parseLoading) && (
        <div style={{ background: "#fafafa", borderRadius: 8, padding: "10px 14px", marginBottom: 10, fontSize: 12, border: "1px solid #e5e7eb" }}>
          {parseLoading ? (
            <div style={{ color: "#6b7280" }}><Loader2 size={12} className="spin" style={{ display: "inline" }} /> 解析元数据中…</div>
          ) : parseResult ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <strong style={{ fontSize: 13 }}>建议模型名：</strong>
                <code style={{ background: "#ede9fe", borderRadius: 4, padding: "1px 6px", fontSize: 12 }}>{parseResult.suggested_name}</code>
                <FitBadge fit={parseResult.fit} />
              </div>
              <MetaRow meta={parseResult.meta} />
              {/* 硬件建议 */}
              {parseResult.fit.suggestions.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  {parseResult.fit.suggestions.map((s: string, i: number) => (
                    <div key={i} style={{ color: "#6b7280", lineHeight: 1.6 }}>• {s}</div>
                  ))}
                </div>
              )}
            </>
          ) : parseError ? (
            <div style={{ color: "#dc2626" }}>⚠️ {parseError}</div>
          ) : null}
        </div>
      )}

      {/* 导入按钮 */}
      {selected && !importing && !importResult && (
        <button
          className="btn primary"
          onClick={doImport}
          style={{ width: "100%", padding: "8px", fontSize: 13 }}
        >
          <PlayCircle size={14} style={{ display: "inline", marginRight: 6 }} />
          导入到 Ollama
        </button>
      )}

      {/* 导入中 */}
      {importing && (
        <div style={{ textAlign: "center", padding: "12px", color: "#6b7280", fontSize: 13 }}>
          <Loader2 size={16} className="spin" style={{ display: "inline" }} />
          <div style={{ marginTop: 4 }}>正在导入（首次可能需要几分钟，后台复制模型文件…）</div>
        </div>
      )}

      {/* 导入结果 */}
      {(importResult || importError) && (
        <div style={{
          borderRadius: 8, padding: "10px 14px", marginTop: 8,
          background: importResult?.ok ? "#dcfce7" : "#fee2e2",
          color: importResult?.ok ? "#16a34a" : "#dc2626",
          fontSize: 13,
        }}>
          {importResult?.ok ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <CheckCircle2 size={14} />
                <strong>{importResult.message}</strong>
              </div>
              <div style={{ fontSize: 12, opacity: 0.8 }}>
                耗时 {importResult.elapsed_sec.toFixed(1)}s
                {importResult.register && importResult.register.ok && ` · 已注册至「Ollama 本地」provider`}
              </div>
            </>
          ) : (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <XCircle size={14} />
                <strong>{importError}</strong>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}