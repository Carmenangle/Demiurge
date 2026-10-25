// 工作流输入口编排（原 api/ai.ts 编排段，2026-10-24 按资源域拆分）：
// AI 根据需求 + 选中节点的输入口结构，规划如何填充各输入口。返回操作计划（不执行，前端确认后再 apply）。
import { apiPost } from "../client";
import { chatBody, type Chat } from "./wire";

export interface PortOp {
  node_id: string;
  input: string;
  output?: string;             // replace_output：输出口名
  action: "set_widget" | "set_image" | "replace_output";
  value?: string | number | boolean;
  image_index?: number;        // set_image / 图像 replace_output：用第几张用户图（从 1 开始）
  kind?: "image" | "text";     // replace_output：替换源类型
  reason?: string;
}
export interface PortsPlan {
  summary: string;
  ops: PortOp[];
  is_orchestration?: boolean;   // false=AI 判定这句不是编排意图，前端应转普通对话
}

export function workflowPorts(
  scene: string,
  imageCount: number,
  nodeSchema: unknown[],
  modelName: string,
  chat: Chat,
  force = false,
  style = "",
  styleTemplate = "",
  repoId = "",
) {
  return apiPost<PortsPlan>("/ai/workflow-ports", {
    scene,
    image_count: imageCount,
    node_schema: nodeSchema,
    model_name: modelName,
    ...chatBody(chat),
    force,
    style,
    style_template: styleTemplate,
    repo_id: repoId,   // 后端据此取该仓库的当前色彩约束
  });
}
