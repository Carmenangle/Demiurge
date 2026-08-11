import type { ScenarioSnapshot } from "../api/scenario";
import type { RepoBinding } from "../stores/repos";

export interface ScenarioBranchDeps {
  saveMessages: (repoId: string, messages: unknown[]) => Promise<unknown>;
  listSnapshots: (repoId: string) => Promise<{ items: ScenarioSnapshot[] }>;
  createSnapshot: (input: {
    output_dir: string; repo_id: string; turn: number; label: string;
  }) => Promise<ScenarioSnapshot>;
  forkSnapshot: (input: {
    output_dir: string; source_repo_id: string; snapshot_id: string; target_repo_id: string;
  }) => Promise<unknown>;
  addBranch: (parentId: string, binding: Partial<RepoBinding>, branchId: string) => string;
  openWork: (parentId: string, childId: string) => void;
  persistMessages: (repoId: string, messages: unknown[]) => void;
  newId: () => string;
}

export type ScenarioBranchResult =
  | { status: "created"; childId: string; snapshotId: string }
  | { status: "missing_snapshot"; turn: number };

export function completedTurn(messages: unknown[]): number {
  return messages.filter((item) => (
    typeof item === "object" && item !== null
    && "role" in item && item.role === "assistant"
    && "text" in item && typeof item.text === "string" && Boolean(item.text.trim())
  )).length;
}

export async function createScenarioBranch(
  input: {
    outputDir: string;
    sourceRepoId: string;
    parentId: string;
    binding: Partial<RepoBinding>;
    messages: unknown[];
    isLatest: boolean;
  },
  deps: ScenarioBranchDeps,
): Promise<ScenarioBranchResult> {
  if (input.isLatest) await deps.saveMessages(input.sourceRepoId, input.messages);
  const turn = completedTurn(input.messages);
  const history = await deps.listSnapshots(input.sourceRepoId);
  let snapshot = history.items.find((item) => item.turn === turn);
  if (!snapshot && input.isLatest) {
    snapshot = await deps.createSnapshot({
      output_dir: input.outputDir,
      repo_id: input.sourceRepoId,
      turn,
      label: "剧情分支",
    });
  }
  if (!snapshot) return { status: "missing_snapshot", turn };

  const childId = deps.newId();
  await deps.forkSnapshot({
    output_dir: input.outputDir,
    source_repo_id: input.sourceRepoId,
    snapshot_id: snapshot.snapshot_id,
    target_repo_id: childId,
  });
  deps.addBranch(input.parentId, input.binding, childId);
  deps.persistMessages(childId, input.messages);
  deps.openWork(input.parentId, childId);
  return { status: "created", childId, snapshotId: snapshot.snapshot_id };
}
