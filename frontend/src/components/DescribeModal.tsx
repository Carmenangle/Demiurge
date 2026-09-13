import { useState } from "react";
import { describeWorkflow, polishDescription } from "../api/ai";
import type { ChatModel } from "../stores/settings";
import { NodePickerModal } from "./NodePickerModal";

export interface DescribeValue {
  description: string;
  input_node_ids: string[];
  output_node_ids: string[];
  primary_output_node_id?: string;  // 主输出节点（单选，可选）
}

interface Props {
  workflowName: string;
  // 可选节点列表，用于「AI 辅助生成」与节点口下拉
  nodes: { id: string; type: string; title: string }[];
  chat: ChatModel;
  initial: DescribeValue;
  // 画布选节点所需：ComfyUI 地址 + 工作流原始路径（无路径则退化为下拉）
  comfyUrl?: string;
  sourcePath?: string;
  // 确定（保存）
  onConfirm: (v: DescribeValue) => void;
  onCancel: () => void;
}

// 模板能力描述弹窗：点击遮罩不关闭，只有「取消」「确定」能关闭。
export function DescribeModal({ workflowName, nodes, chat, initial, comfyUrl, sourcePath, onConfirm, onCancel }: Props) {
  const [desc, setDesc] = useState(initial.description);
  const [inputIds, setInputIds] = useState<string[]>(initial.input_node_ids || []);
  const [outputIds, setOutputIds] = useState<string[]>(initial.output_node_ids || []);
  const [primaryOutputId, setPrimaryOutputId] = useState<string>(initial.primary_output_node_id || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // 当前正在用画布选哪一组："input" | "output" | "primary" | null
  const [picking, setPicking] = useState<"input" | "output" | "primary" | null>(null);
  const canPick = !!(comfyUrl && sourcePath);

  // 切换某 id 在某组中的存在（多选累加/移除）
  const toggle = (group: "input" | "output", id: string) => {
    const setter = group === "input" ? setInputIds : setOutputIds;
    setter((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  // 节点 id -> 显示名
  const nameOf = (id: string) => {
    const n = nodes.find((x) => String(x.id) === String(id));
    return n ? `#${id} ${n.title || n.type}` : id ? `#${id}` : "（无）";
  };

  const onAiGen = async () => {
    setErr("");
    if (!chat.baseUrl || !chat.modelName) {
      setErr("请先在「设置 → 对话模型」配置接口地址与模型");
      return;
    }
    setBusy(true);
    try {
      const r = await describeWorkflow(workflowName, nodes, chat);
      setDesc(r.description);
    } catch (e) {
      setErr(`生成失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const onPolish = async () => {
    setErr("");
    if (!desc.trim()) {
      setErr("请先输入能力描述再润色");
      return;
    }
    if (!chat.baseUrl || !chat.modelName) {
      setErr("请先在「设置 → 对话模型」配置接口地址与模型");
      return;
    }
    setBusy(true);
    try {
      const r = await polishDescription(desc, chat);
      setDesc(r.description);
    } catch (e) {
      setErr(`润色失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    // 遮罩不绑 onClick → 点外部不关闭
    <div className="modal-mask">
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 480 }}>
        <h3>编写能力描述</h3>
        <p style={{ color: "var(--text-muted)", marginTop: 0, fontSize: 13 }}>
          描述这个工作流能做什么，供 AI 对话时智能调用。
        </p>

        <label style={{ fontSize: 13, color: "var(--text-muted)" }}>能力描述</label>
        <textarea
          autoFocus
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="例如：反推图片得到 Danbooru 标签提示词"
          style={{ width: "100%", minHeight: 80, marginTop: 4, marginBottom: 10 }}
        />

        <p style={{ color: "var(--text-muted)", fontSize: 12, margin: "0 0 6px" }}>
          选定的节点只是 AI 的可操作范围（不必全填）：对话时 AI 读取这些节点的现状，
          按你的话判断哪些口要改，确认后只覆盖认可的部分。
        </p>
        {([
          { group: "input" as const, label: "替换输入节点（左侧接线 / 自身参数）", ids: inputIds },
          { group: "output" as const, label: "替换输出节点（右侧接线，流入下游）", ids: outputIds },
        ]).map(({ group, label, ids }) => (
          <div key={group} style={{ marginBottom: 10 }}>
            <label style={{ fontSize: 13, color: "var(--text-muted)" }}>{label}</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "4px 0" }}>
              {ids.length === 0 && (
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>（未选）</span>
              )}
              {ids.map((id) => (
                <button key={id} className="btn" style={{ fontSize: 12 }}
                  title="点击移除" onClick={() => toggle(group, id)}>
                  {nameOf(id)} ✕
                </button>
              ))}
            </div>
            {canPick ? (
              <button className="btn" style={{ width: "100%" }} onClick={() => setPicking(group)}>
                + 在画布选择节点
              </button>
            ) : (
              <select value="" onChange={(e) => { if (e.target.value) toggle(group, e.target.value); }}
                style={{ width: "100%" }}>
                <option value="">+ 添加节点…</option>
                {nodes.filter((n) => !ids.includes(String(n.id))).map((n) => (
                  <option key={n.id} value={n.id}>
                    #{n.id} {n.title || n.type}
                  </option>
                ))}
              </select>
            )}
          </div>
        ))}

        {/* 主输出节点：多输出工作流时指定哪个节点的产物作为默认结果 */}
        <div style={{ marginBottom: 10 }}>
          <label style={{ fontSize: 13, color: "var(--text-muted)" }}>
            主输出节点（可选，多输出工作流优先取此节点产物）
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "4px 0" }}>
            {primaryOutputId ? (
              <button className="btn" style={{ fontSize: 12 }}
                title="点击清除" onClick={() => setPrimaryOutputId("")}>
                {nameOf(primaryOutputId)} ✕
              </button>
            ) : (
              <span style={{ color: "var(--text-muted)", fontSize: 12 }}>（未设置，取所有输出节点产物）</span>
            )}
          </div>
          {canPick ? (
            <button className="btn" style={{ width: "100%" }} onClick={() => setPicking("primary")}>
              {primaryOutputId ? "重新在画布选择" : "+ 在画布选择节点"}
            </button>
          ) : (
            <select value={primaryOutputId}
              onChange={(e) => setPrimaryOutputId(e.target.value)}
              style={{ width: "100%" }}>
              <option value="">（不设置，取所有产物）</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  #{n.id} {n.title || n.type}
                </option>
              ))}
            </select>
          )}
        </div>

        {err && <p style={{ color: "#d9534f", fontSize: 13 }}>{err}</p>}

        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>
            取消
          </button>
          <button className="btn" disabled={busy} onClick={onAiGen}>
            {busy ? "生成中…" : "AI 辅助生成"}
          </button>
          <button className="btn" disabled={busy} onClick={onPolish}>
            {busy ? "处理中…" : "AI 润色"}
          </button>
          <button
            className="btn primary"
            onClick={() => onConfirm({
              description: desc.trim(),
              input_node_ids: inputIds,
              output_node_ids: outputIds,
              primary_output_node_id: primaryOutputId || undefined,
            })}
          >
            确定
          </button>
        </div>
      </div>

      {picking && canPick && (
        <NodePickerModal
          title={picking === "input" ? "选择替换输入节点" : picking === "primary" ? "选择主输出节点" : "选择替换输出节点"}
          comfyUrl={comfyUrl!}
          sourcePath={sourcePath!}
          onPick={(id) => {
            if (picking === "primary") {
              setPrimaryOutputId(id);
            } else {
              toggle(picking, id);
            }
            setPicking(null);
          }}
          onCancel={() => setPicking(null)}
        />
      )}
    </div>
  );
}
