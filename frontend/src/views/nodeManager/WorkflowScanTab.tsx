import { useState } from "react";
import { Download, Upload } from "lucide-react";
import { StateHint } from "../../components/layout/PageShell";
import { analyzeWorkflow, installNode, startQueue, type AnalyzeResult, type NodePack } from "../../api/nodeManager";

// 工作流识别安装：上传/粘贴工作流 JSON → 识别缺失节点 → 一键装可映射的包。
export function WorkflowScanTab({ url }: { url: string }) {
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const analyze = async (workflow: Record<string, unknown>) => {
    setScanning(true); setErr(""); setResult(null); setBusy("");
    try {
      setResult(await analyzeWorkflow(url, workflow));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setScanning(false);
    }
  };

  const onFile = (f: File | null) => {
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      try { analyze(JSON.parse(String(reader.result || "{}"))); }
      catch { setErr("文件不是合法的工作流 JSON。"); }
    };
    reader.readAsText(f);
  };

  const installAll = async (packs: NodePack[]) => {
    setBusy(`安装 ${packs.length} 个缺失节点包中…`);
    try {
      for (const p of packs) await installNode(url, p);
      await startQueue(url);
      setBusy(`已提交安装 ${packs.length} 个节点包。完成后需重启 ComfyUI 生效（右上角「重启」）。`);
    } catch (e) {
      setBusy(`安装失败：${(e as Error).message}`);
    }
  };

  return (
    <div>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 0 }}>
        上传一个工作流 JSON，识别其中本机未安装的节点，并一键安装可匹配的节点包。
      </p>
      <label className="btn" style={{ cursor: "pointer", display: "inline-flex" }}>
        <Upload size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
        选择工作流 JSON
        <input type="file" accept=".json,application/json" style={{ display: "none" }}
          onChange={(e) => { onFile(e.target.files?.[0] || null); e.target.value = ""; }} />
      </label>

      {scanning && <StateHint>识别中…</StateHint>}
      {err && <StateHint kind="error">识别失败：{err}</StateHint>}
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}

      {result && (
        <div style={{ marginTop: 16 }}>
          {result.missing_packs.length === 0 && result.unresolved.length === 0 ? (
            <div className="build-result ok">该工作流依赖的插件均已安装，无需操作。</div>
          ) : (
            <>
              <p style={{ fontSize: 13 }}>
                缺失 {result.missing_packs.length} 个插件包，可安装 {result.packs.length} 个：
              </p>
              {result.packs.length > 0 && (
                <>
                  <button className="btn primary" onClick={() => installAll(result.packs)}>
                    <Download size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                    一键安装全部 {result.packs.length} 个包
                  </button>
                  <ul style={{ fontSize: 13, marginTop: 10 }}>
                    {result.packs.map((p) => (
                      <li key={p.id}>
                        <a href={p.repository} target="_blank" rel="noreferrer">{p.title}</a>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {result.unresolved.length > 0 && (
                <p style={{ fontSize: 13, color: "var(--warning)" }}>
                  以下节点找不到对应安装包，需手动查找：{result.unresolved.join("、")}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
