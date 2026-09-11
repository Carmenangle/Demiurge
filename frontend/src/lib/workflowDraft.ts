const PROTECTED_NODE_FIELDS = new Set(["id", "type", "pos", "size", "order", "inputs", "outputs"]);

function clone<T>(value: T): T {
  // JSON.stringify(undefined) === undefined → JSON.parse(undefined) 抛 "undefined" is not valid JSON。
  // 未连线端口/自定义节点常缺 link/links 字段，直接透传 undefined。
  return value === undefined ? value : JSON.parse(JSON.stringify(value));
}

function mergePorts(basePorts: any[] | undefined, nextPorts: any[] | undefined, linkKey: "link" | "links") {
  if (!Array.isArray(basePorts) || !Array.isArray(nextPorts)) return basePorts;
  return basePorts.map((basePort, index) => {
    const named = basePort?.name != null
      ? nextPorts.find((port) => port?.name === basePort.name)
      : undefined;
    const nextPort = named || nextPorts[index];
    if (!nextPort) return basePort;
    return { ...basePort, ...clone(nextPort), [linkKey]: clone(basePort?.[linkKey]) };
  });
}

export function mergeRequestedNodes(baseWorkflow: unknown, requestedNodes: readonly unknown[]): unknown {
  const draft: any = clone(baseWorkflow);
  if (!draft || !Array.isArray(draft.nodes)) return draft;
  const updates = new Map<string, any>();
  for (const value of requestedNodes) {
    const node = (value as any)?.node ?? value;
    if (node && node.id != null) updates.set(String(node.id), node);
  }
  draft.nodes = draft.nodes.map((baseNode: any) => {
    const update = updates.get(String(baseNode?.id));
    if (!update) return baseNode;
    const merged = { ...baseNode };
    for (const [key, value] of Object.entries(update)) {
      if (!PROTECTED_NODE_FIELDS.has(key)) merged[key] = clone(value);
    }
    merged.inputs = mergePorts(baseNode.inputs, update.inputs, "link");
    merged.outputs = mergePorts(baseNode.outputs, update.outputs, "links");
    return merged;
  });
  return draft;
}

function nodeSignature(workflow: unknown): Map<string, string> | null {
  const nodes = (workflow as any)?.nodes;
  if (!Array.isArray(nodes) || nodes.length === 0) return null;
  const signatures = new Map<string, string>();
  for (const node of nodes) {
    if (node?.id == null) return null;
    signatures.set(String(node.id), String(node.type || node.class_type || ""));
  }
  return signatures;
}

// 单节点编辑画布只能改参数；捕获结果必须仍包含模板完整图的全部节点。
// 允许 AI 编排追加节点，但不允许裁剪图覆盖完整工作流草稿。
export function preservesWorkflowTopology(requiredWorkflow: unknown, candidateWorkflow: unknown): boolean {
  const required = nodeSignature(requiredWorkflow);
  const candidate = nodeSignature(candidateWorkflow);
  if (!required || !candidate) return false;
  for (const [id, type] of required) {
    if (candidate.get(id) !== type) return false;
  }
  return true;
}

export function canonicalWorkflowDraft(templateWorkflow: unknown, savedDraft: unknown): unknown {
  return preservesWorkflowTopology(templateWorkflow, savedDraft) ? savedDraft : templateWorkflow;
}
