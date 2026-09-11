import {
  beginNewSession, beginRestore, canAutosave, completeRestore, initialBuildSessionModel,
  markSaved, ownsGeneration, type BuildSessionModel,
} from "./buildSessionModel";
import type { WorkflowBuildActivity } from "./workflowBuildActivity";

const LAST_SESSION_KEY = "laf_build_last_session";
const handledKey = (sessionId: string) => `laf_workflow_handled_${sessionId}`;

export class BuildWorkspaceRuntime {
  private model: BuildSessionModel = initialBuildSessionModel;
  private readonly inFlight = new Set<string>();

  constructor(private readonly storage: Pick<Storage, "getItem" | "setItem" | "removeItem">) {}

  startNew(): number {
    this.model = beginNewSession(this.model);
    this.storage.removeItem(LAST_SESSION_KEY);
    return this.model.generation;
  }

  startRestore(): number {
    this.model = beginRestore(this.model);
    return this.model.generation;
  }

  finishRestore(generation: number, sessionId: string): boolean {
    if (!ownsGeneration(this.model, generation)) return false;
    this.model = completeRestore(this.model, generation, sessionId);
    this.rememberSession(sessionId);
    return true;
  }

  finishSave(generation: number, sessionId: string): boolean {
    if (!ownsGeneration(this.model, generation)) return false;
    this.model = markSaved(this.model, generation, sessionId);
    this.rememberSession(sessionId);
    return true;
  }

  owns(generation: number): boolean {
    return ownsGeneration(this.model, generation);
  }

  generation(): number {
    return this.model.generation;
  }

  canAutosave(ready: boolean, hasMessages: boolean): boolean {
    return canAutosave(this.model, ready, hasMessages);
  }

  lastSessionId(): string {
    return this.storage.getItem(LAST_SESSION_KEY) || "";
  }

  claimTerminal(
    activities: WorkflowBuildActivity[], currentSessionId: string,
  ): WorkflowBuildActivity[] {
    const sessionId = currentSessionId || "draft";
    const handled = new Set(this.readHandled(sessionId));
    const claimed: WorkflowBuildActivity[] = [];
    for (const activity of activities) {
      const matches = activity.sessionId === sessionId || activity.sessionId === "draft";
      const terminal = activity.status === "done" || activity.status === "error";
      if (!matches || !terminal || handled.has(activity.id) || this.inFlight.has(activity.id)) continue;
      this.inFlight.add(activity.id);
      claimed.push(activity);
    }
    return claimed;
  }

  completeActivity(currentSessionId: string, activityId: string): void {
    const sessionId = currentSessionId || "draft";
    const ids = [...new Set([...this.readHandled(sessionId), activityId])].slice(-100);
    this.storage.setItem(handledKey(sessionId), JSON.stringify(ids));
    this.inFlight.delete(activityId);
  }

  releaseActivity(activityId: string): void {
    this.inFlight.delete(activityId);
  }

  private rememberSession(sessionId: string): void {
    if (sessionId) this.storage.setItem(LAST_SESSION_KEY, sessionId);
  }

  private readHandled(sessionId: string): string[] {
    try {
      const parsed = JSON.parse(this.storage.getItem(handledKey(sessionId)) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
}
