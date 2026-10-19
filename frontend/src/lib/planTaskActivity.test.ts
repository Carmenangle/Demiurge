import { describe, it, expect } from "vitest";
import { visiblePlanActivities, type PlanTaskActivity } from "./planTaskActivity";

function plan(over: Partial<PlanTaskActivity> = {}): PlanTaskActivity {
  return {
    id: "p1", repo_id: "repo-1", intent: "做图", status: "running",
    progress: "1/3 步", needsApproval: false, ...over,
  };
}

describe("visiblePlanActivities", () => {
  it("终态计划不占面板（悬浮数字会被已完成计划顶住的旧 bug）", () => {
    const items = [
      plan({ id: "done", status: "done" }),
      plan({ id: "error", status: "error" }),
      plan({ id: "cancelled", status: "cancelled" }),
      plan({ id: "partial", status: "partial" }),
      plan({ id: "running", status: "running" }),
    ];
    expect(visiblePlanActivities(items, new Set()).map((p) => p.id)).toEqual(["running"]);
  });

  it("blocked 保留：它在等人处理，不是终态", () => {
    expect(visiblePlanActivities([plan({ status: "blocked" })], new Set())).toHaveLength(1);
    expect(visiblePlanActivities([plan({ status: "awaiting_approval" })], new Set())).toHaveLength(1);
  });

  it("同会话正有对话在跑时隐藏计划活动（避免一次生成显示两条）", () => {
    const items = [plan({ id: "a", repo_id: "repo-1" }), plan({ id: "b", repo_id: "repo-2" })];
    expect(visiblePlanActivities(items, new Set(["repo-1"])).map((p) => p.id)).toEqual(["b"]);
  });
});
