import { useRef } from "react";
import {
  beginNewSession, beginRestore, completeRestore, initialBuildSessionModel,
  markSaved, ownsGeneration, canAutosave as selectCanAutosave,
  type BuildSessionModel,
} from "./buildSessionModel";

export function useBuildSession() {
  const modelRef = useRef<BuildSessionModel>(initialBuildSessionModel);

  const startNew = () => {
    modelRef.current = beginNewSession(modelRef.current);
    return modelRef.current.generation;
  };
  const startRestore = () => {
    modelRef.current = beginRestore(modelRef.current);
    return modelRef.current.generation;
  };
  const finishRestore = (generation: number, sessionId: string) => {
    const owned = ownsGeneration(modelRef.current, generation);
    modelRef.current = completeRestore(modelRef.current, generation, sessionId);
    return owned;
  };
  const finishSave = (generation: number, sessionId: string) => {
    const owned = ownsGeneration(modelRef.current, generation);
    modelRef.current = markSaved(modelRef.current, generation, sessionId);
    return owned;
  };

  return {
    modelRef,
    startNew,
    startRestore,
    finishRestore,
    finishSave,
    owns: (generation: number) => ownsGeneration(modelRef.current, generation),
    canAutosave: (ready: boolean, hasMessages: boolean) =>
      selectCanAutosave(modelRef.current, ready, hasMessages),
  };
}
