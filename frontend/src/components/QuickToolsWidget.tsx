import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Wrench } from "lucide-react";
import { announceFloatingPanel, subscribeFloatingPanels } from "../lib/floatingPanels";

const QuickToolsPanel = lazy(() => import("./QuickToolsPanel").then((m) => ({ default: m.QuickToolsPanel })));
const FAB_TOP_KEY = "laf_quick_tools_fab_top";
const FAB_HIDDEN_KEY = "laf_quick_tools_hidden";

export function QuickToolsWidget({ onOpenFull }: { onOpenFull: () => void }) {
  const [open, setOpen] = useState(false);
  const [fabTop, setFabTop] = useState(() => {
    const value = Number(localStorage.getItem(FAB_TOP_KEY));
    return Number.isFinite(value) && value > 0 ? value : window.innerHeight - 148;
  });
  const [hidden, setHidden] = useState(() => localStorage.getItem(FAB_HIDDEN_KEY) === "1");
  const dragRef = useRef<{ moved: boolean; startY: number; startTop: number } | null>(null);
  const longPressRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { localStorage.setItem(FAB_TOP_KEY, String(fabTop)); }, [fabTop]);
  useEffect(() => { localStorage.setItem(FAB_HIDDEN_KEY, hidden ? "1" : "0"); }, [hidden]);
  useEffect(() => subscribeFloatingPanels("quick-tools", () => setOpen(false)), []);

  const show = () => {
    announceFloatingPanel("quick-tools");
    setOpen(true);
  };
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
    if (Math.abs(delta) > 4) {
      drag.moved = true;
      if (longPressRef.current) clearTimeout(longPressRef.current);
    }
    setFabTop(Math.min(window.innerHeight - 64, Math.max(8, drag.startTop + delta)));
  };
  const onFabPointerUp = () => {
    if (longPressRef.current) clearTimeout(longPressRef.current);
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag && !drag.moved) show();
  };

  if (hidden) {
    return <button className="quick-tools-handle" title="显示快捷工具" onClick={() => setHidden(false)}>工具</button>;
  }
  if (open) {
    return <Suspense fallback={<div className="quick-tools-panel quick-tools-loading" role="status">正在载入工具…</div>}>
      <QuickToolsPanel onClose={() => setOpen(false)} onOpenFull={() => { setOpen(false); onOpenFull(); }} />
    </Suspense>;
  }
  return (
    <button className="quick-tools-fab" title="快捷工具（拖动可移动，长按隐藏）"
      style={{ top: fabTop, bottom: "auto" }} onPointerDown={onFabPointerDown}
      onPointerMove={onFabPointerMove} onPointerUp={onFabPointerUp}>
      <Wrench size={23} />
    </button>
  );
}
