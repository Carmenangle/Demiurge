import { describe, expect, it } from "vitest";
import {
  confirmedPlanExecution,
  WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS,
  WORKFLOW_BUILD_PLAN_TIMEOUT_MS,
} from "./workflowBuildExecution";

describe("workflow build execution policy", () => {
  it("sends an approved plan once without duplicating conversation history", () => {
    const request = confirmedPlanExecution("整理当前工作流", "保留 Anima 并修正接线");

    expect(request.need).toContain("整理当前工作流");
    expect(request.need.match(/保留 Anima 并修正接线/g)).toHaveLength(1);
    expect(request.history).toEqual([]);
  });

  it("keeps frontend timeouts above the backend in-flight build window", () => {
    expect(WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS).toBeGreaterThanOrEqual(420_000);
    expect(WORKFLOW_BUILD_PLAN_TIMEOUT_MS).toBeGreaterThanOrEqual(240_000);
  });
});
