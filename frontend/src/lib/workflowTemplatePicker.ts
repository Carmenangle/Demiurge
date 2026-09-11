import { useCallback, useMemo, useState } from "react";
import type { Template } from "../api/workflows";

const KEY = "laf_recent_workflow_templates";
const RECENT_LIMIT = 5;

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function loadRecentTemplateIds(storage: StorageLike): string[] {
  try {
    const value = JSON.parse(storage.getItem(KEY) || "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

export function nextRecentTemplateIds(current: string[], id: string): string[] {
  return [id, ...current.filter((item) => item !== id)].slice(0, RECENT_LIMIT);
}

export function filterWorkflowTemplates(templates: Template[], query: string, recentIds: string[]) {
  const normalized = query.trim().toLowerCase();
  const filtered = normalized
    ? templates.filter((template) =>
        template.name.toLowerCase().includes(normalized)
        || (template.description || "").toLowerCase().includes(normalized),
      )
    : templates;
  const filteredIds = new Set(filtered.map((template) => template.id));
  const byId = new Map(templates.map((template) => [template.id, template]));
  const recent = recentIds
    .map((id) => byId.get(id))
    .filter((template): template is Template => !!template && filteredIds.has(template.id));
  const recentSet = new Set(recent.map((template) => template.id));
  return {
    recent,
    normal: filtered.filter((template) => !recentSet.has(template.id)),
    count: filtered.length,
  };
}

export function useWorkflowTemplatePicker(templates: Template[], storage: StorageLike = localStorage) {
  const [query, setQuery] = useState("");
  const [recentIds, setRecentIds] = useState<string[]>(() => loadRecentTemplateIds(storage));
  const groups = useMemo(
    () => filterWorkflowTemplates(templates, query, recentIds),
    [templates, query, recentIds],
  );
  const remember = useCallback((id: string) => {
    setRecentIds((current) => {
      const next = nextRecentTemplateIds(current, id);
      try { storage.setItem(KEY, JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  }, [storage]);

  return { query, setQuery, remember, ...groups };
}
