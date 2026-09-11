import { useEffect, useRef, useState } from "react";
import { queueStatus } from "../../api/nodeManager";

// 轮询 ComfyUI-Manager 的装/更新/卸载队列进度。
// 切换页面/卸载组件只停轮询，不影响后端任务（任务在 ComfyUI 侧跑，不会中断）。
export interface QueueProgress {
  active: boolean;      // 是否正在轮询
  total: number;
  done: number;
  processing: boolean;  // 队列是否还在处理
  text: string;         // 展示文案
}

export function useQueueProgress(url: string) {
  const [prog, setProg] = useState<QueueProgress>({ active: false, total: 0, done: 0, processing: false, text: "" });
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = () => {
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
  };

  // 开始轮询：note 是操作说明（如"更新 XXX"）
  // onDone：队列转为完成时触发一次。Manager 的 /queue/status 只报队列空否、不报每个任务成败，
  // 故完成后由调用方复查真实结果（如比对版本号），再决定最终文案。
  const track = (note: string, onDone?: () => void) => {
    stop();
    setProg({ active: true, total: 0, done: 0, processing: true, text: `${note}：排队中…` });
    const tick = async () => {
      try {
        const s = await queueStatus(url);
        const processing = s.is_processing || s.in_progress_count > 0 || (s.total_count > s.done_count);
        if (processing) {
          setProg({
            active: true, total: s.total_count, done: s.done_count, processing: true,
            text: `${note}：处理中 ${s.done_count}/${s.total_count}…（安装依赖较慢，请勿关闭 ComfyUI）`,
          });
        } else {
          stop();
          setProg({
            active: false, total: s.total_count, done: s.done_count, processing: false,
            text: `${note}：队列已结束，正在核对结果…`,
          });
          onDone?.();
        }
      } catch {
        // 查询失败按仍在处理，继续轮询
      }
    };
    tick();
    timer.current = setInterval(tick, 2000);
  };

  // 完成核对后，由调用方设置最终文案（成功/失败/未变）
  const setResult = (text: string) => setProg((p) => ({ ...p, active: false, processing: false, text }));

  useEffect(() => stop, []); // 卸载时停轮询（后端任务不受影响）
  return { prog, track, setResult };
}
