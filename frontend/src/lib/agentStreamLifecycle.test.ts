import { describe, expect, it, vi } from "vitest";
import { releaseAgentStream } from "./agentStreamLifecycle";

describe("agent stream lifecycle", () => {
  it("navigation detaches the foreground without aborting the background run", () => {
    const abort = vi.fn();
    expect(releaseAgentStream({ botId: "bot", abort }, "navigation")).toBeNull();
    expect(abort).not.toHaveBeenCalled();
  });

  it("explicit stop aborts the stream", () => {
    const abort = vi.fn();
    expect(releaseAgentStream({ botId: "bot", abort }, "stop")).toBeNull();
    expect(abort).toHaveBeenCalledOnce();
  });
});
