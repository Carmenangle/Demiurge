import { useEffect, useRef, useState } from "react";
import { updateProgress, type UpdateProgress } from "../../api/nodeManager";

// 轮询自建更新进度（后端 services/node_update）。
// 与 useQueueProgress 的区别：那个只有 done/total 两个计数，这个有字节数、速度、
// 依赖清单，且带 changed 字段——「有没有真的更新成」由后端核对 HEAD 得出。
// 500ms 是本地进程轮询，够跟上下载进度且不浪费。
const TICK_MS = 500;

export function humanBytes(n: number): string {
  if (!n || n <= 0) return "0 B";
  const units: [string, number][] = [["GB", 1024 ** 3], ["MB", 1024 ** 2], ["KB", 1024]];
  for (const [u, size] of units) if (n >= size) return `${(n / size).toFixed(1)} ${u}`;
  return `${n} B`;
}

const PHASE_TEXT: Record<string, string> = {
  download: "下载代码",
  resolve: "应用改动",
  preflight: "预检依赖",
  deps: "下载依赖",
  "deps-install": "安装依赖",
  "needs-confirm": "等待确认",
  done: "完成",
};

export function phaseLabel(p: UpdateProgress): string {
  return PHASE_TEXT[p.phase] || "";
}

export function useUpdateProgress() {
  const [prog, setProg] = useState<UpdateProgress | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = () => {
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
  };

  // onDone 在任务结束时触发一次，带最终快照（含 changed / error / pending_sensitive）
  const track = (onDone?: (p: UpdateProgress) => void) => {
    stop();
    const tick = async () => {
      let p: UpdateProgress;
      try { p = await updateProgress(); } catch { return; }  // 查询失败按仍在跑
      setProg(p);
      if (p.finished) { stop(); onDone?.(p); }
    };
    void tick();
    timer.current = setInterval(tick, TICK_MS);
  };

  // 切页/卸载只停轮询，后端任务在 daemon 线程里继续跑，不受影响
  useEffect(() => stop, []);
  const clear = () => { stop(); setProg(null); };
  return { prog, track, clear };
}
