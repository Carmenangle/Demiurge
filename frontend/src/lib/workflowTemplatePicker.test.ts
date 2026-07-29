import { describe, expect, it } from "vitest";
import type { Template } from "../api/workflows";
import {
  filterWorkflowTemplates,
  loadRecentTemplateIds,
  nextRecentTemplateIds,
} from "./workflowTemplatePicker";

const template = (id: string, name: string, description: string): Template => ({
  id,
  name,
  description,
  source_path: "",
  exposed: [],
  node_order: [],
  input_node_ids: [],
  output_node_ids: [],
  created_at: 0,
  updated_at: 0,
});

const templates = [
  template("a", "角色立绘", "人物"),
  template("b", "场景", "古典学院"),
  template("c", "头像", "角色特写"),
];

describe("workflow template picker", () => {
  it("按最近顺序分组并过滤搜索结果", () => {
    const result = filterWorkflowTemplates(templates, "角色", ["c", "a"]);
    expect(result.recent.map((item) => item.id)).toEqual(["c", "a"]);
    expect(result.normal).toEqual([]);
    expect(result.count).toBe(2);
  });

  it("最近模板去重并限制五条", () => {
    expect(nextRecentTemplateIds(["a", "b", "c", "d", "e"], "c"))
      .toEqual(["c", "a", "b", "d", "e"]);
  });

  it("损坏的持久化内容回退为空", () => {
    expect(loadRecentTemplateIds({ getItem: () => "{", setItem: () => {} })).toEqual([]);
  });
});
