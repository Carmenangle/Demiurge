import { useEffect, useState } from "react";
import { type Settings, activeChatModel } from "../stores/settings";
import { syncNodes, buildWorkflow } from "../api/ai";
import { type DescribeValue } from "../components/DescribeModal";
import {
  scanWorkflows,
  parseWorkflowByPath,
  parseWorkflowJson,
  listTemplates,
  updateTemplate,
  deleteTemplate,
  type ParsedNode,
  type ScannedWorkflow,
  type Template,
} from "../api/workflows";

/** 工作流模板页的编排层：数据拉取 + AI 搭建/节点同步 + 模板增删改。
 *  组件只消费返回值渲染，逻辑不散在组件闭包里（对齐 useChatSession 模式）。 */
export function useWorkflowTemplates(settings: Settings) {
  const [files, setFiles] = useState<ScannedWorkflow[]>([]);
  const [parsed, setParsed] = useState<ParsedNode[] | null>(null);
  const [fileName, setFileName] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [editingTemplate, setEditingTemplate] = useState<Template | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // 单独编辑描述的目标模板 + 其节点结构
  const [describeTarget, setDescribeTarget] = useState<{
    template: Template;
    nodes: ParsedNode[];
  } | null>(null);
  const [deleting, setDeleting] = useState<Template | null>(null); // 待确认删除的模板
  // AI 搭工作流 / 节点库同步
  const [nodeSyncing, setNodeSyncing] = useState(false);
  const [building, setBuilding] = useState(false);
  const [showBuild, setShowBuild] = useState(false);       // 搭建需求输入弹窗
  const [alertMsg, setAlertMsg] = useState<{ title: string; message: string } | null>(null);
  const embed = { baseUrl: settings.embedModel.baseUrl, apiKey: settings.embedModel.apiKey, modelName: settings.embedModel.modelName };

  // 同步节点知识库：扫描 ComfyUI 已装节点入库，供 AI 搭工作流检索
  const onSyncNodes = async () => {
    setNodeSyncing(true);
    setError("");
    try {
      const r = await syncNodes(embed, settings.comfyuiUrl, false);
      setAlertMsg({ title: "已开始同步", message: `共 ${r.total_packs} 个节点包，正在后台建立索引。进度见「节点知识库」页。` });
    } catch (e) {
      setAlertMsg({ title: "同步失败", message: `${(e as Error).message}（需先启动 ComfyUI 并配置嵌入模型）` });
    } finally {
      setNodeSyncing(false);
    }
  };
  // AI 按需求搭工作流，成功后落盘并刷新扫描列表
  const onBuild = async (need: string) => {
    setShowBuild(false);
    if (!need.trim()) return;
    const cm = activeChatModel(settings);
    if (!cm) { setAlertMsg({ title: "未配置对话模型", message: "请先在「设置 → 对话模型」配置。" }); return; }
    setBuilding(true);
    setError("");
    try {
      const r = await buildWorkflow({
        need, chat: { baseUrl: cm.baseUrl, apiKey: cm.apiKey, modelName: cm.modelName },
        embed, comfyUrl: settings.comfyuiUrl, workflowDir: settings.workflowDir,
      });
      if (r.ok) {
        setAlertMsg({ title: "工作流已生成", message: `已保存到：${r.path}\n可点「扫描默认目录」查看并配置。` });
        onScan();
      } else {
        setAlertMsg({ title: "搭建未成功", message: `AI 多次尝试仍有错误：\n${r.errors.slice(0, 5).join("\n")}` });
      }
    } catch (e) {
      setAlertMsg({ title: "搭建失败", message: (e as Error).message });
    } finally {
      setBuilding(false);
    }
  };

  const refreshTemplates = async () => {
    try {
      const res = await listTemplates();
      setTemplates(res.items);
    } catch (e) {
      setError(`加载模板失败：${(e as Error).message}`);
    }
  };

  useEffect(() => {
    refreshTemplates();
  }, []);

  const onScan = async () => {
    setError("");
    setBusy(true);
    try {
      const res = await scanWorkflows(settings.workflowDir);
      setFiles(res.items);
      if (res.items.length === 0) setError("该目录下没有找到 .json 工作流文件。");
    } catch (e) {
      setError(`扫描失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const onOpenScanned = async (f: ScannedWorkflow) => {
    setError("");
    setBusy(true);
    try {
      const res = await parseWorkflowByPath(f.path);
      setFileName(f.name);
      setSourcePath(f.path);
      setEditingTemplate(null);
      setParsed(res.nodes);
    } catch (e) {
      setError(`解析失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError("");
    setBusy(true);
    try {
      const json = JSON.parse(await file.text());
      const res = await parseWorkflowJson(json);
      setFileName(file.name);
      setSourcePath("");
      setEditingTemplate(null);
      setParsed(res.nodes);
    } catch (e) {
      setError(`无法解析该文件：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  // 重新编辑已保存模板：需先拿到原始 workflow 的节点结构
  const onEditTemplate = async (t: Template) => {
    if (!t.source_path) {
      setError("该模板没有记录原始文件路径，无法重新解析节点结构。");
      return;
    }
    setError("");
    setBusy(true);
    try {
      const res = await parseWorkflowByPath(t.source_path);
      setFileName(t.name);
      setSourcePath(t.source_path);
      setEditingTemplate(t);
      setParsed(res.nodes);
    } catch (e) {
      setError(`无法重新解析：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const onDeleteTemplate = async (t: Template) => {
    await deleteTemplate(t.id);
    refreshTemplates();
  };

  // 单独编辑能力描述：解析节点结构后弹同一个描述弹窗
  const onEditDescribe = async (t: Template) => {
    setError("");
    try {
      const nodes = t.source_path ? (await parseWorkflowByPath(t.source_path)).nodes : [];
      setDescribeTarget({ template: t, nodes });
    } catch (e) {
      setError(`无法解析节点：${(e as Error).message}`);
    }
  };

  const saveDescribe = async (d: DescribeValue) => {
    const t = describeTarget?.template;
    if (!t) return;
    setDescribeTarget(null);
    await updateTemplate(t.id, {
      name: t.name,
      source_path: t.source_path,
      exposed: t.exposed,
      node_order: t.node_order,
      description: d.description,
      input_node_ids: d.input_node_ids,
      output_node_ids: d.output_node_ids,
      primary_output_node_id: d.primary_output_node_id || "",
    });
    refreshTemplates();
  };

  const onSaved = () => {
    setParsed(null);
    setEditingTemplate(null);
    refreshTemplates();
  };

  return {
    onDeleteTemplate,
    files, parsed, setParsed, fileName, sourcePath, editingTemplate, templates,
    error, busy, describeTarget, setDescribeTarget, deleting, setDeleting,
    nodeSyncing, building, showBuild, setShowBuild, alertMsg, setAlertMsg,
    onSyncNodes, onBuild, onScan, onOpenScanned, onPickFile, onEditTemplate,
    onEditDescribe, saveDescribe, onSaved,
  };
}
