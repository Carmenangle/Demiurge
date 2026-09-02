import { useEffect, useRef, useState } from "react";
import { Activity, X } from "lucide-react";
import { subscribeWorkflowBuildActivities, type WorkflowBuildActivity } from "../lib/workflowBuildActivity";
import { subscribeChatBackgroundActivities, type ChatBackgroundActivity } from "../lib/chatBackgroundActivity";
import { subscribeComfyBackgroundActivities, type ComfyBackgroundActivity } from "../lib/comfyBackgroundActivity";
import {
  subscribePlanTaskActivities,
  type PlanTaskActivity,
} from "../lib/planTaskActivity";
import { announceFloatingPanel, subscribeFloatingPanels } from "../lib/floatingPanels";

const FAB_TOP_KEY = "laf_support_fab_top";
const STATUS_LABEL: Record<string, string> = {
  queued: "计划排队中", running: "计划执行中", awaiting_approval: "计划待审批",
  blocked: "计划受阻", done: "计划已完成", partial: "计划部分完成",
  error: "计划失败", cancelled: "计划已取消",
};
const FAB_HIDDEN_KEY = "laf_support_hidden";

export function SupportWidget(props: {
  chat: unknown;
  embed: unknown;
  repoId: string;
  onOpenChat: (threadId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [activities, setActivities] = useState<WorkflowBuildActivity[]>([]);
  const [chatActivities, setChatActivities] = useState<ChatBackgroundActivity[]>([]);
  const [comfyActivities, setComfyActivities] = useState<ComfyBackgroundActivity[]>([]);
  const [planActivities, setPlanActivities] = useState<PlanTaskActivity[]>([]);
  const [fabTop, setFabTop] = useState<number>(() => {
    const value = Number(localStorage.getItem(FAB_TOP_KEY));
    return Number.isFinite(value) && value > 0 ? value : window.innerHeight - 80;
  });
  const [hidden, setHidden] = useState(() => localStorage.getItem(FAB_HIDDEN_KEY) === "1");
  const dragRef = useRef<{ moved: boolean; startY: number; startTop: number } | null>(null);
  const longPressRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => subscribeWorkflowBuildActivities(setActivities), []);
  useEffect(() => subscribeChatBackgroundActivities(setChatActivities), []);
  useEffect(() => subscribeComfyBackgroundActivities(setComfyActivities), []);
  useEffect(() => subscribePlanTaskActivities(setPlanActivities), []);
  useEffect(() => { localStorage.setItem(FAB_TOP_KEY, String(fabTop)); }, [fabTop]);
  useEffect(() => { localStorage.setItem(FAB_HIDDEN_KEY, hidden ? "1" : "0"); }, [hidden]);
  useEffect(() => () => { if (longPressRef.current) clearTimeout(longPressRef.current); }, []);
  useEffect(() => subscribeFloatingPanels("support", () => setOpen(false)), []);

  const onFabPointerDown = (event: React.PointerEvent) => {
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
    dragRef.current = { moved: false, startY: event.clientY, startTop: fabTop };
    longPressRef.current = setTimeout(() => {
      if (dragRef.current && !dragRef.current.moved) { setHidden(true); dragRef.current = null; }
    }, 600);
  };
  const onFabPointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const delta = event.clientY - drag.startY;
    if (Math.abs(delta) > 4) { drag.moved = true; if (longPressRef.current) clearTimeout(longPressRef.current); }
    setFabTop(Math.min(window.innerHeight - 64, Math.max(8, drag.startTop + delta)));
  };
  const onFabPointerUp = () => {
    if (longPressRef.current) clearTimeout(longPressRef.current);
    const drag = dragRef.current; dragRef.current = null;
    if (drag && !drag.moved) {
      announceFloatingPanel("support");
      setOpen(true);
    }
  };

  const running = activities.filter((item) => item.status === "queued" || item.status === "running");
  // 同一次对话的两个活动合并：对话生成中（编译计划）时，隐藏同会话的计划活动；
  // 生成结束（chat 活动消失）后，计划活动自然显示为「待审批/执行中」。
  const runningChatThreads = new Set(
    chatActivities.filter((c) => c.kind === "running").map((c) => c.threadId),
  );
  const visiblePlanActivities = planActivities.filter(
    (p) => !runningChatThreads.has(p.repo_id),
  );
  const total = running.length + chatActivities.length + comfyActivities.length
    + visiblePlanActivities.length;
  const openRepoChat = (threadId: string) => { props.onOpenChat(threadId); setOpen(false); };
  if (hidden) return <button className="support-handle" title="显示后台活动" onClick={() => setHidden(false)}>&lt;&lt;&lt;</button>;
  if (!open) return (
    <button className="support-fab" title="后台活动（拖动可移动，长按隐藏）" style={{ top: fabTop, bottom: "auto" }}
      onPointerDown={onFabPointerDown} onPointerMove={onFabPointerMove} onPointerUp={onFabPointerUp}>
      <Activity className="support-fab-headset" size={24} />
      {total > 0 && <span className="support-activity-count">{total}</span>}
      <span className="support-fab-emblem" aria-hidden="true" />
    </button>
  );
  return (
    <div className="support-panel">
      <header><span>后台活动</span><button className="icon-btn" onClick={() => setOpen(false)}><X size={18} /></button></header>
      <div className="support-body">
        {total === 0 && <div className="support-msg bot">当前没有正在运行的后台对话。</div>}
        {comfyActivities.map((item) => (
          <button key={`comfy-${item.promptId}`} className="support-activity"
            onClick={() => openRepoChat(item.threadId)}>
            <strong>出图中</strong>
            <span>{item.label}</span>
          </button>
        ))}
        {chatActivities.map((item) => (
          <button key={`chat-${item.taskId || item.threadId}`} className="support-activity"
            onClick={() => openRepoChat(item.threadId)}>
            <strong>{item.kind === "running" ? "生成中" : "排队中"}</strong>
            <span>{item.label}{item.need ? `：${item.need}` : ""}</span>
          </button>
        ))}
        {visiblePlanActivities.map((item) => (
          <div key={`plan-${item.id}`} className="support-activity"
            style={{ display: "block", textAlign: "left", cursor: "pointer" }}
            onClick={() => openRepoChat(item.repo_id)} title="点击回到对应会话">
            <strong>{STATUS_LABEL[item.status] ?? item.status}</strong>
            <span>{item.progress}：{item.intent}</span>
            {item.status === "awaiting_approval" && (
              <span style={{ display: "block", marginTop: 4, color: "#d23b3b", fontSize: 12 }}>
                ● 待批准——点击回到对话，在计划卡上点「批准执行」
              </span>
            )}
          </div>
        ))}
        {running.map((item) => (
          <button key={item.id} className="support-activity" onClick={() => { if (item.sessionId !== "draft") localStorage.setItem("laf_build_last_session", item.sessionId); window.location.hash = "#/ai-build"; setOpen(false); }}>
            <strong>{item.status === "running" ? "思考中" : "排队中"}</strong><span>{item.need}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
