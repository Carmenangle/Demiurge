import { apiPost } from "./client";

export interface ScenarioSnapshot {
  snapshot_id: string;
  source_repo_id: string;
  turn: number;
  created_at: number;
}

export function createScenarioSnapshot(input: {
  output_dir: string; repo_id: string; turn?: number; label?: string; dedupe_key?: string;
}) {
  return apiPost<ScenarioSnapshot>("/scenario/snapshots", input);
}

export function listScenarioSnapshots(repoId: string) {
  return import("./client").then(({ apiGet }) => apiGet<{ items: ScenarioSnapshot[] }>(
    `/scenario/snapshots?repo_id=${encodeURIComponent(repoId)}`,
  ));
}

export function forkScenarioSnapshot(input: {
  output_dir: string; source_repo_id: string; snapshot_id: string; target_repo_id: string;
}) {
  return apiPost<{ ok: boolean; files: number; vectors: number }>("/scenario/fork", input);
}
