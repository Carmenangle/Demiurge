// Visual CI 诊断状态管理：自动触发 + 状态缓存 + 重试。
// 诊断是 fire-and-forget，不阻塞 UI。状态 keyed by generationId。
import { useCallback, useState } from "react";
import {
  loadVisualCiDiagnostic,
  requestVisualCiRetry,
  type VisualCiDiagnostic,
} from "../api/visualCi";

/** 单条诊断在 UI 侧的状态。 */
export interface VisualCiState {
  diagnostic: VisualCiDiagnostic | null;
  loading: boolean;
  error: string | null;
}

export interface UseVisualCiReturn {
  /** key = generationId */
  diagnostics: Map<string, VisualCiState>;

  /** 加载某条 generation 的诊断（自动触发或打开面板时调用）。 */
  load: (
    generationId: string,
    repoId: string,
    outputDir: string,
  ) => Promise<void>;

  /** 申请受限重试（默认 1 次，后端限额 1-3）。 */
  requestRetry: (
    generationId: string,
    repoId: string,
    outputDir: string,
  ) => Promise<void>;

  /** 清空某条状态。 */
  clear: (generationId: string) => void;
}

/** 统一 setter：从旧 map 生成新 map，保证引用变化触发渲染。 */
type SetMap = React.Dispatch<
  React.SetStateAction<Map<string, VisualCiState>>
>;

function patchState(
  set: SetMap,
  generationId: string,
  patch: (prev: VisualCiState | undefined) => VisualCiState,
): void {
  set((prev) => {
    const next = new Map(prev);
    next.set(generationId, patch(next.get(generationId)));
    return next;
  });
}

export function useVisualCi(): UseVisualCiReturn {
  const [diagnostics, setDiagnostics] = useState<Map<string, VisualCiState>>(
    () => new Map(),
  );

  const load = useCallback(
    async (generationId: string, repoId: string, outputDir: string) => {
      if (!generationId || !repoId || !outputDir) return;
      patchState(setDiagnostics, generationId, (prev) => ({
        diagnostic: prev?.diagnostic ?? null,
        loading: true,
        error: null,
      }));
      try {
        const diag = await loadVisualCiDiagnostic(
          generationId,
          repoId,
          outputDir,
        );
        patchState(setDiagnostics, generationId, () => ({
          diagnostic: diag,
          loading: false,
          error: null,
        }));
      } catch {
        patchState(setDiagnostics, generationId, (prev) => ({
          diagnostic: prev?.diagnostic ?? null,
          loading: false,
          error: "加载诊断失败",
        }));
      }
    },
    [],
  );

  const requestRetry = useCallback(
    async (generationId: string, repoId: string, outputDir: string) => {
      if (!generationId || !repoId || !outputDir) return;
      patchState(setDiagnostics, generationId, (prev) => ({
        diagnostic: prev?.diagnostic ?? null,
        loading: true,
        error: null,
      }));
      try {
        const diag = await requestVisualCiRetry(
          generationId,
          repoId,
          outputDir,
        );
        patchState(setDiagnostics, generationId, () => ({
          diagnostic: diag,
          loading: false,
          error: null,
        }));
      } catch {
        patchState(setDiagnostics, generationId, (prev) => ({
          diagnostic: prev?.diagnostic ?? null,
          loading: false,
          error: "申请重试失败",
        }));
      }
    },
    [],
  );

  const clear = useCallback((generationId: string) => {
    setDiagnostics((prev) => {
      const next = new Map(prev);
      next.delete(generationId);
      return next;
    });
  }, []);

  return { diagnostics, load, requestRetry, clear };
}
