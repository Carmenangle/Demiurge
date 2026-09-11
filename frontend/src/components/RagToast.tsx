import { useEffect, useState } from "react";

// RAG 记忆库创建状态提示：剧情后台抽纪要/写知识库时，右下角弹一条轻提示。
// 三态：创建中（start）/ 创建成功（ok）/ 创建失败（fail）。由 useChatSession 收到 rag_status 事件后派发。
export interface RagStatusDetail {
  state: "start" | "ok" | "fail" | string;
  kind: string; // "chronicle" 纪要记忆 | "curator" 知识库条目
  count?: number;
}

export function emitRagStatus(detail: RagStatusDetail) {
  window.dispatchEvent(new CustomEvent("laf-rag-status", { detail }));
}

export function ragStatusLabel(d: RagStatusDetail): { text: string; cls: string } {
  if (d.kind === "worldbook") {
    const amount = typeof d.count === "number" ? `（共 ${d.count} 条）` : "";
    if (d.state === "start") {
      return {
        text: `正在将世界书条目索引化${amount}，首次处理可能较慢，剧情生成将继续…`,
        cls: "rag-toast--wait",
      };
    }
    if (d.state === "fail") return { text: "世界书条目索引失败", cls: "rag-toast--fail" };
    return { text: "世界书条目索引完成", cls: "rag-toast--ok" };
  }
  const what = d.kind === "curator" ? "RAG 知识库" : "记忆纪要";
  if (d.state === "start") return { text: `${what} 创建中…`, cls: "rag-toast--wait" };
  if (d.state === "fail") return { text: `${what} 创建失败`, cls: "rag-toast--fail" };
  // ok
  const n = typeof d.count === "number" ? d.count : undefined;
  const tail = n !== undefined ? (n > 0 ? `，新增 ${n} 条` : "，本轮无新增") : "";
  return { text: `${what} 创建成功${tail}`, cls: "rag-toast--ok" };
}

export function RagToast() {
  const [cur, setCur] = useState<RagStatusDetail | null>(null);
  useEffect(() => {
    const h = (e: Event) => setCur((e as CustomEvent).detail as RagStatusDetail);
    window.addEventListener("laf-rag-status", h);
    return () => window.removeEventListener("laf-rag-status", h);
  }, []);
  useEffect(() => {
    if (!cur) return;
    // 终态（成功/失败）2.5s 后自动消失；创建中保持到下一个事件覆盖。
    if (cur.state === "start" && cur.kind !== "worldbook") return;
    if (cur.state === "start") {
      const t = setTimeout(() => setCur(null), 6000);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setCur(null), 2500);
    return () => clearTimeout(t);
  }, [cur]);
  if (!cur) return null;
  const { text, cls } = ragStatusLabel(cur);
  return (
    <div className={`rag-toast ${cls}`} onClick={() => setCur(null)} role="status">
      {text}
    </div>
  );
}
