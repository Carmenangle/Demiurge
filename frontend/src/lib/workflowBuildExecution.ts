export const WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS = 420_000;
export const WORKFLOW_BUILD_PLAN_TIMEOUT_MS = 240_000;

export interface ConfirmedPlanExecution {
  need: string;
  history: { role: "user" | "assistant"; text: string }[];
}

export function confirmedPlanExecution(originalNeed: string, plan: string): ConfirmedPlanExecution {
  return {
    need: `${originalNeed.trim()}\n\n【已和用户确认的搭建方案，请严格照此搭建】\n${plan.trim()}`,
    history: [],
  };
}
