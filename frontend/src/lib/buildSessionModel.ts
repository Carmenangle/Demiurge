export type BuildSessionPhase = "empty" | "restoring" | "active";

export interface BuildSessionModel {
  generation: number;
  phase: BuildSessionPhase;
  sessionId: string;
  graphLoaded: boolean;
}

export const initialBuildSessionModel: BuildSessionModel = {
  generation: 0,
  phase: "empty",
  sessionId: "",
  graphLoaded: false,
};

export function beginNewSession(state: BuildSessionModel): BuildSessionModel {
  return { generation: state.generation + 1, phase: "empty", sessionId: "", graphLoaded: false };
}

export function beginRestore(state: BuildSessionModel): BuildSessionModel {
  return { ...state, generation: state.generation + 1, phase: "restoring", graphLoaded: false };
}

export function completeRestore(state: BuildSessionModel, generation: number, sessionId: string): BuildSessionModel {
  if (generation !== state.generation) return state;
  return { ...state, phase: "active", sessionId, graphLoaded: true };
}

export function markSaved(state: BuildSessionModel, generation: number, sessionId: string): BuildSessionModel {
  if (generation !== state.generation) return state;
  return { ...state, phase: "active", sessionId, graphLoaded: true };
}

export function ownsGeneration(state: BuildSessionModel, generation: number): boolean {
  return state.generation === generation;
}

export function canAutosave(state: BuildSessionModel, ready: boolean, hasMessages: boolean): boolean {
  return ready && hasMessages && state.phase === "active" && state.graphLoaded;
}
