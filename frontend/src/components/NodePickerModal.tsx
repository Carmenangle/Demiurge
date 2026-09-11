import { useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { rawWorkflowByPath } from "../api/workflows";
import { lockUrl, postToFrame, isLafMessageFromStrict } from "../lib/lafLock";

interface Props {
  title: string;            // 提示词输入口 / 图像输入口
  comfyUrl: string;
  sourcePath: string;       // 工作流原始文件路径
  onPick: (id: string, nodeTitle: string) => void;
  onCancel: () => void;
}

// 在真实 ComfyUI 画布里长按选择一个节点作为输入口；选中后弹确认。
export function NodePickerModal({ title, comfyUrl, sourcePath, onPick, onCancel }: Props) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [hint, setHint] = useState("");
  const [pending, setPending] = useState<{ id: string; title: string } | null>(null);
  // 递增即强制 iframe 卸载重建（手动「重新载入画布」兜底）
  const [reloadKey, setReloadKey] = useState(0);
  // 跨标签争用自愈（对齐 WorkflowTemplates 编辑页）：另开的 ComfyUI 标签会话恢复会把本画布覆盖成
  // 别的工作流，长按选到的就不是本模板的节点。载图后过恢复窗口校验——画布出现本模板之外的节点 id
  // 即被抢占，软重发 load 重试（不整帧重挂）；隐藏使节点变少属正常，用 id 子集判定不误触发。
  const rawRef = useRef<unknown>(null);
  const expectedIdsRef = useRef<Set<string>>(new Set());
  const retryRef = useRef(0);
  const verifyRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const MAX_RELOAD_RETRY = 6;

  useEffect(() => {
    retryRef.current = 0;
    const post = (type: string, payload?: unknown) =>
      postToFrame(iframeRef.current?.contentWindow, type, payload, comfyUrl);
    const sendLoad = (wf: unknown) => post("load", { workflow: wf, exposedIds: [] });
    const scheduleVerify = (delay: number) => {
      verifyRef.current = setTimeout(() => post("request_graph"), delay);
    };
    const onMsg = async (ev: MessageEvent) => {
      if (!isLafMessageFromStrict(ev, iframeRef.current?.contentWindow, comfyUrl)) return;
      const d = ev.data;
      if (d.type === "ready") {
        try {
          const r = await rawWorkflowByPath(sourcePath);
          rawRef.current = r.workflow;
          const n = (r.workflow as { nodes?: { id?: unknown }[] } | null)?.nodes;
          expectedIdsRef.current = new Set(Array.isArray(n) ? n.map((x) => String(x?.id)) : []);
          // exposedIds 空 = 全量节点模式，启用长按选择
          sendLoad(r.workflow);
        } catch (e) {
          setHint(`载入失败：${(e as Error).message}`);
        }
      } else if (d.type === "loaded") {
        scheduleVerify(2200); // 载图后校验画布是否被别的标签抢占成别的工作流
      } else if (d.type === "graph") {
        // 画布节点 id 都属于本模板=正常（隐藏使数目变少不影响）；出现外来节点=被抢占，软重发重试
        const expected = expectedIdsRef.current;
        const liveNodes = (d.payload?.workflow?.nodes ?? []) as { id?: unknown }[];
        const foreign = liveNodes.filter((x) => !expected.has(String(x?.id)));
        if (expected.size === 0 || (foreign.length === 0 && liveNodes.length > 0)) {
          setHint("");
        } else if (retryRef.current < MAX_RELOAD_RETRY) {
          retryRef.current += 1;
          if (rawRef.current) sendLoad(rawRef.current);
          scheduleVerify(900);
        } else {
          setHint(`画布被其他 ComfyUI 标签抢占（出现 ${foreign.length} 个非本模板节点）。请关掉其他 ComfyUI 标签后点「重新载入画布」。`);
        }
      } else if (d.type === "node_selected") {
        const id = String(d.payload.id);
        setPending({ id, title: d.payload.title || `#${id}` });
      }
    };
    window.addEventListener("message", onMsg);
    // ready 竞态兜底：扩展在挂上监听前就发了 ready 的话，load 会永远发不出去，
    // 画布停在 ComfyUI 恢复的上次会话（只剩单个节点）。补问一次触发扩展回 ready。
    // 对齐 WorkflowTemplates.tsx / WorkflowCard.tsx 的既有做法。
    const ping = setTimeout(
      () => postToFrame(iframeRef.current?.contentWindow, "ping_ready", undefined, comfyUrl),
      1500,
    );
    return () => {
      clearTimeout(ping);
      if (verifyRef.current) clearTimeout(verifyRef.current);
      window.removeEventListener("message", onMsg);
    };
  }, [sourcePath, comfyUrl, reloadKey]);

  // 重选：取消当前候选并清除画布高亮
  const reselect = () => {
    if (pending) {
      postToFrame(iframeRef.current?.contentWindow, "deselect", { id: pending.id }, comfyUrl);
    }
    setPending(null);
  };

  return (
    <div className="modal-mask" style={{ zIndex: 110 }}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: "min(900px, 94vw)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h3 style={{ margin: 0 }}>{title}</h3>
          <button
            className="btn"
            style={{ marginLeft: "auto" }}
            onClick={() => { setHint(""); setPending(null); setReloadKey((k) => k + 1); }}
            title="画布空白或载入了别的工作流时点这里，重新挂载画布并重载"
          >
            <RotateCcw size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            重新载入画布
          </button>
        </div>
        <p style={{ color: "var(--text-muted)", marginTop: 6, fontSize: 13 }}>
          在画布上长按要作为「{title}」的节点；选中后确认。
        </p>
        {hint && <p style={{ color: "#c98a1a", fontSize: 13 }}>{hint}</p>}

        <div className="lock-canvas" style={{ height: "min(60vh, 520px)" }}>
          <iframe
            key={`${sourcePath}::${reloadKey}`}
            ref={iframeRef}
            src={lockUrl(comfyUrl)}
            title="选择节点"
            className="lock-frame"
          />
        </div>

        {pending && (
          <p style={{ marginTop: 10 }}>
            已选：<strong>{pending.title}</strong>{" "}
            <span style={{ color: "var(--text-muted)" }}>#{pending.id}</span>
          </p>
        )}

        <div className="modal-actions" style={{ marginTop: 12 }}>
          <button className="btn" onClick={onCancel}>
            取消
          </button>
          {pending && (
            <button className="btn" onClick={reselect}>
              重选
            </button>
          )}
          <button
            className="btn primary"
            disabled={!pending}
            onClick={() => pending && onPick(pending.id, pending.title)}
          >
            确认
          </button>
        </div>
      </div>
    </div>
  );
}
