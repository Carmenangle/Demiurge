// Visual CI 诊断槽：自包含组件，内部调用 useVisualCi，自动触发加载与展示。
// 挂在图片下方；generationId 为空时不渲染。
import { useEffect, useState } from "react";
import { useVisualCi } from "../../lib/useVisualCi";
import { VisualCiBadge } from "./VisualCiBadge";

export function VisualCiBadgeSlot({
  generationId,
  repoId,
  outputDir,
}: {
  generationId?: string;
  repoId?: string;
  outputDir?: string;
}) {
  const { diagnostics, load, requestRetry } = useVisualCi();
  const [expanded, setExpanded] = useState(false);
  const key = generationId || "";
  const state = diagnostics.get(key);

  // 有 generationId 时自动加载诊断（幂等：hook 内 map 有则跳过）
  useEffect(() => {
    if (generationId && repoId && outputDir) {
      void load(generationId, repoId, outputDir);
    }
  }, [generationId, repoId, outputDir, load]);

  if (!generationId || !repoId || !outputDir) return null;

  return (
    <VisualCiBadge
      diagnostic={state?.diagnostic ?? null}
      loading={!!state?.loading}
      error={state?.error ?? null}
      expanded={expanded}
      onToggleExpanded={() => setExpanded((e) => !e)}
      onRetry={() => {
        void requestRetry(generationId, repoId, outputDir);
      }}
    />
  );
}
