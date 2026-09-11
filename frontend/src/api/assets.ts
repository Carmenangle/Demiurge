import { apiPost } from "./client";

export interface OutputPathAudit {
  changed: boolean;
  asset_count: number;
  file_count: number;
  missing_count: number;
  conflict_count: number;
  total_bytes: number;
}

export interface OutputPathMigration {
  migrated_files: number;
  updated_assets: number;
  updated_references: number;
  delete_failures: number;
  skipped_missing: number;
}

export function auditOutputPath(oldDir: string, newDir: string) {
  return apiPost<OutputPathAudit>("/assets/output-path/audit", {
    old_dir: oldDir,
    new_dir: newDir,
  });
}

export function migrateOutputPath(oldDir: string, newDir: string) {
  return apiPost<OutputPathMigration>("/assets/output-path/migrate", {
    old_dir: oldDir,
    new_dir: newDir,
  });
}
