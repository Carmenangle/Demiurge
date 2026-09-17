// 进入仓库/切回时恢复「进行中的生图任务」（T2.2-B2：2026-09-17 从 lib/useChatSession.ts 外移）。
//
// ▍搬移原则：只搬收口 —— `resumedRef` 与 `useEffect` 体与搬移前**逐字一致**，其上语义注释随体迁入；
//   需要的 hook 闭包符号以**同名 deps** 注入（threadId / loadedRef / workflowRuntime / workflowObserver /
//   settings / discardFailedIllustration / cancelForegroundRun，共 7 名）；`comfyRegenerationUrl` 由本模块自带 import。
//   ⇒「行为面 = 搬移前」由「逐字搬移锁 + 直断言行为网」两件互补证据承担（useWorkflowResume.test.tsx）。
//
// ▍红线（改动即破行为面）：
//   ① 依赖数组保持 `[threadId]`（含其上 `eslint-disable-next-line react-hooks/exhaustive-deps`）——
//      「重订阅时机」就是本接缝的行为面：observer / settings 每渲染都是新对象，补进依赖会让恢复反复重跑；
//   ② `resumedRef` **必须跨 effect 重订阅保留**（同一 prompt_id 只处理一次），故随本 hook 一起迁入；
//   ③ 等加载的 40×100ms 轮询、过期任务「先 inspect 再判 watching」的顺序、
//      P22 出队调用（`cancelForegroundRun`）逐字保留。

import { type MutableRefObject, useEffect, useRef } from "react";
import type { useSettings } from "../stores/settings";
import { comfyRegenerationUrl } from "./regeneration";
import type { WorkflowGenerationRuntime, WorkflowWatchObserver } from "./workflowGenerationRuntime";

export interface WorkflowResumeDeps {
  threadId: string;
  loadedRef: MutableRefObject<boolean>;
  workflowRuntime: WorkflowGenerationRuntime;
  workflowObserver: WorkflowWatchObserver;
  settings: ReturnType<typeof useSettings>["settings"];
  discardFailedIllustration: (
    messageId: string, slotId: string, stage: string, error: string, promptId?: string, retryArgs?: unknown,
  ) => void;
  cancelForegroundRun: (promptId: string) => void;
}

export function useWorkflowResume({
  threadId, loadedRef, workflowRuntime, workflowObserver, settings,
  discardFailedIllustration, cancelForegroundRun,
}: WorkflowResumeDeps) {
  // 进入仓库/切回时，恢复"进行中的生图任务"。
  const resumedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    let alive = true;
    const resume = async () => {
      for (let i = 0; i < 40 && !loadedRef.current; i++) await new Promise((r) => setTimeout(r, 100));
      if (!alive) return;
      const list = workflowRuntime.list();
      for (const p of list) {
        const action = workflowRuntime.recoveryAction(p, resumedRef.current);
        if (action === "skip") continue;  // 本会话已处理过，不重复
        resumedRef.current.add(p.prompt_id);
        const comfyuiUrl = comfyRegenerationUrl(p.regeneration) || settings.comfyuiUrl;
        if (action === "expire") {
          // 超过 30 分钟的遗留任务先查一次 ComfyUI history：任务其实已完成时
          // 直接归档回填（2026-08-30 21:25 轮实锤：图 3 分钟就出完了，页面回来
          // 时却按年龄判死丢弃）。只有 history 查不到（watching→not_found 终态）
          // 才真正交给失败槽，不再凭年龄静默丢弃已完成的图。
          const outcome = await workflowRuntime.inspect(p, comfyuiUrl, workflowObserver);
          if (!alive) return;
          if (outcome !== "watching") continue;
          if (p.target) discardFailedIllustration(
            p.target.messageId, p.target.slotId, "resume_expired",
            "后台出图任务已过期", p.prompt_id,
          );
          cancelForegroundRun(p.prompt_id);   // P22：统一入库路径（恢复期队列为空，出队为幂等空操作）
          continue;
        }
        await workflowRuntime.inspect(p, comfyuiUrl, workflowObserver);
        if (!alive) return;
      }
    };
    resume();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

}
