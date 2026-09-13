import { describe, expect, it } from "vitest";
import {
  initialGenState, reduce, canInterruptFreely, needsConfirm,
  blocksDialogueSubmission, runningPromptId, streamingBotId,
} from "./generationLifecycle";

const item = { id: "q1", text: "x", content: { text: "x", images: [], parts: [] } };

describe("generation lifecycle", () => {
  it("tracks agent image state and selectors", () => {
    let state = reduce(initialGenState, { t: "agentStart", botId: "b1" });
    expect(streamingBotId(state)).toBe("b1");
    expect(canInterruptFreely(state)).toBe(true);
    state = reduce(state, { t: "agentImage", botId: "b1" });
    expect(needsConfirm(state)).toBe(true);
    expect(reduce(state, { t: "agentDone", botId: "b1" }).agent).toBeNull();
  });

  it("ignores stale agent callbacks", () => {
    const old = reduce(initialGenState, { t: "agentStart", botId: "old" });
    const current = reduce(old, { t: "agentStart", botId: "new" });
    expect(reduce(current, { t: "agentImage", botId: "old" })).toBe(current);
    expect(reduce(current, { t: "agentDone", botId: "old" })).toBe(current);
    expect(reduce(current, { t: "agentImage", botId: "new" }).agent).toEqual({
      botId: "new", imageStarted: true,
    });
  });

  it("keeps Agent and ComfyUI as independent generation channels", () => {
    const workflow = reduce(initialGenState, { t: "workflowStart", promptId: "p1" });
    expect(blocksDialogueSubmission(workflow)).toBe(false);

    const concurrent = reduce(workflow, { t: "agentStart", botId: "b1" });
    expect(runningPromptId(concurrent)).toBe("p1");
    expect(streamingBotId(concurrent)).toBe("b1");
    expect(blocksDialogueSubmission(concurrent)).toBe(true);

    const agentDone = reduce(concurrent, { t: "agentDone", botId: "b1" });
    expect(runningPromptId(agentDone)).toBe("p1");
    expect(streamingBotId(agentDone)).toBeNull();

    const workflowDone = reduce(concurrent, { t: "workflowDone", promptId: "p1" });
    expect(streamingBotId(workflowDone)).toBe("b1");
    expect(runningPromptId(workflowDone)).toBeNull();
  });

  it("ignores stale workflow completion", () => {
    const state = reduce(initialGenState, { t: "workflowStart", promptId: "new" });
    expect(runningPromptId(state)).toBe("new");
    expect(reduce(state, { t: "workflowDone", promptId: "old" })).toBe(state);
  });

  it("stop preserves queue while reset clears it", () => {
    const queued = reduce(initialGenState, { t: "enqueue", item });
    const running = reduce(queued, { t: "workflowStart", promptId: "p1" });
    expect(reduce(running, { t: "stop" }).queue).toEqual([item]);
    expect(reduce(running, { t: "reset" })).toEqual(initialGenState);
  });

  it("removes only the queue item returned to the composer for editing", () => {
    const second = {
      id: "q2",
      text: "带图修改",
      content: {
        text: "带图修改",
        images: ["image.png"],
        parts: [{ type: "image" as const, url: "image.png" }, { type: "text" as const, text: "带图修改" }],
      },
    };
    const queued = reduce(reduce(initialGenState, { t: "enqueue", item }), { t: "enqueue", item: second });

    expect(reduce(queued, { t: "removeQueued", id: second.id }).queue).toEqual([item]);
    expect(second.content.images).toEqual(["image.png"]);
  });
});
