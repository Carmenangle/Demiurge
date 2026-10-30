import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import type { ModelProbeResult } from "../api/modelProbe";

export function ProbeResultView({ result }: { result: ModelProbeResult }) {
  const Icon = result.status === "success" ? CheckCircle2 : result.status === "warning" ? AlertTriangle : XCircle;
  return (
    <span className={`model-probe-result ${result.status}`} title={result.source || result.message}>
      <Icon size={14} /> {result.message}
    </span>
  );
}
