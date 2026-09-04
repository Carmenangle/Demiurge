import { useEffect, useState } from "react";
import { Plus, Trash2, Save, AlertTriangle, FileText, X } from "lucide-react";
import { listAgents, saveAgents, defaultPrompt, DEFAULT_TOOLS, listAgentKnowledge, readAgentKnowledge, getKnowledgeConfig, saveKnowledgeConfig, type Agent, type AgentTools, type KnowledgeDoc, type KnowledgeDocContent, type KnowledgeConfig } from "../../api/agents";
import { listMcpServers, type McpServer } from "../../api/mcp";
import { listSkills, type Skill } from "../../api/skills";
import { deleteRecipe, keepRecipe, listRecipes, type RecipeInfo } from "../../lib/planTaskActivity";
import { renderMarkdown } from "../../lib/renderMarkdown";
import type { PanelProps } from "./GeneralPanel";
import { normalizeContextBudgets, DEFAULT_HISTORY_PER_ROLE, DEFAULT_SELFHEAL_ATTEMPTS } from "../../stores/settings";
import { BuiltinAgentsSection } from "./BuiltinAgentsSection";
import { ProseStyleSection } from "./ProseStyleSection";

const TOOL_LABELS: { key: keyof AgentTools; label: string }[] = [
  { key: "generate_image", label: "文生图" },
  { key: "generate_video", label: "文生视频" },
  { key: "image_to_image", label: "图生图" },
  { key: "analyze_image", label: "反推提示词" },
  { key: "search_inspiration", label: "联网找灵感" },
];

// 后端缺省/坏档返回 {} → 归一为 smart 缺省；保存也走同一条归一，避免非法值回写
function normalizeKnowledgeConfig(c: Partial<KnowledgeConfig> | null | undefined): KnowledgeConfig {
  return {
    mode: c?.mode === "always" ? "always" : "smart",
    always_docs: Array.isArray(c?.always_docs) ? [...(c as KnowledgeConfig).always_docs] : [],
  };
}

// 多 Agent 预设管理：列表 + 编辑（人设/记忆/请求参数/工具开关）。
// 独立于 settings 草稿（存后端 data/agents.json），点保存整体写回。
export function AgentPanel({ draft, setDraft }: PanelProps) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [defPrompt, setDefPrompt] = useState("");
  const [mcpList, setMcpList] = useState<McpServer[]>([]);   // 可选的 MCP 服务器
  const [skillList, setSkillList] = useState<Skill[]>([]);   // 可选的技能
  const [recipes, setRecipes] = useState<RecipeInfo[]>([]);  // 固化流程预设（计划配方）
  const [knowledge, setKnowledge] = useState<KnowledgeDoc[]>([]);          // 固化知识库（规范文档）
  const [knowledgeDoc, setKnowledgeDoc] = useState<KnowledgeDocContent | null>(null); // 预览弹窗
  // 注入模式：smart=技能按需装载（默认）/ always=全量常驻（老行为）；always_docs=smart 下常驻名单
  const [kcfg, setKcfg] = useState<KnowledgeConfig>({ mode: "smart", always_docs: [] });
  const [kcfgSaving, setKcfgSaving] = useState(false);
  const [kcfgError, setKcfgError] = useState("");

  useEffect(() => {
    Promise.all([listAgents(), defaultPrompt()])
      .then(([a, d]) => { setAgents(a); setDefPrompt(d.prompt); })
      .catch(() => {})
      .finally(() => setLoading(false));
    listMcpServers().then((m) => setMcpList(m.filter((x) => x.enabled))).catch(() => {});
    listSkills().then((s) => setSkillList(s.filter((x) => x.enabled))).catch(() => {});
    listRecipes()
      .then((m) => setRecipes(Object.values(m || {}).sort((a, b) => (b.created_at || 0) - (a.created_at || 0))))
      .catch(() => {});
    listAgentKnowledge()
      .then((list) => setKnowledge([...list].sort((a, b) => b.mtime - a.mtime)))
      .catch(() => {});
    getKnowledgeConfig()
      .then((c) => setKcfg(normalizeKnowledgeConfig(c)))
      .catch(() => {});
  }, []);

  // 后端缺省/坏档返回 {} → 归一为 smart 缺省；保存也走同一条归一，避免非法值回写
  const applyKcfg = (next: KnowledgeConfig) => {
    setKcfgSaving(true);
    setKcfgError("");
    saveKnowledgeConfig(next)
      .then((r) => setKcfg(normalizeKnowledgeConfig(r)))
      .catch((e: unknown) => setKcfgError(e instanceof Error ? e.message : String(e)))
      .finally(() => setKcfgSaving(false));
  };
  const setMode = (mode: "smart" | "always") => applyKcfg({ ...kcfg, mode });
  const toggleAlwaysDoc = (name: string, pinned: boolean) => {
    const set = new Set(kcfg.always_docs);
    if (pinned) set.add(name); else set.delete(name);
    applyKcfg({ ...kcfg, always_docs: [...set] });
  };

  const refreshRecipes = () =>
    listRecipes()
      .then((m) => setRecipes(Object.values(m || {}).sort((a, b) => (b.created_at || 0) - (a.created_at || 0))))
      .catch(() => {});
  const keepRecipePreset = async (id: string) => {
    try { await keepRecipe(id); } catch { /* 保留失败：刷新回到真实状态 */ }
    refreshRecipes();
  };
  const removeRecipePreset = async (id: string) => {
    try { await deleteRecipe(id); } catch { /* 已不存在视为删除成功 */ }
    refreshRecipes();
  };
  const openKnowledge = async (name: string) => {
    try {
      const doc = await readAgentKnowledge(name);
      setKnowledgeDoc(doc);
    } catch { /* 读取失败：保持关闭 */ }
  };

  // 新建 Agent 默认带普通对话优先、显式意图才调用工具的内置规则
  const add = () =>
    setAgents((s) => [...s, {
      id: crypto.randomUUID(),
      name: "新智能体",
      systemPrompt: defPrompt,
      memory: "", temperature: null, topP: null, maxTokens: null,
      tools: { ...DEFAULT_TOOLS }, mcpServerIds: [], skillIds: [],
      isDefault: false, enabled: true,
    }]);
  const upd = (id: string, patch: Partial<Agent>) =>
    setAgents((s) => s.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  const updTool = (id: string, key: keyof AgentTools, val: boolean) =>
    setAgents((s) => s.map((x) => (x.id === id ? { ...x, tools: { ...x.tools, [key]: val } } : x)));
  // 勾选/取消某 Agent 的 MCP 服务器或技能（在其 id 列表里增删）
  const toggleId = (id: string, field: "mcpServerIds" | "skillIds", val: string) =>
    setAgents((s) => s.map((x) => {
      if (x.id !== id) return x;
      const list = x[field] || [];
      return { ...x, [field]: list.includes(val) ? list.filter((v) => v !== val) : [...list, val] };
    }));
  const del = (id: string) => setAgents((s) => s.filter((x) => x.id !== id));

  const save = async () => {
    setSaving(true);
    try {
      const r = await saveAgents(agents);
      setAgents(r);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } finally {
      setSaving(false);
    }
  };

  // APPEND_AGENT_RENDER
  if (loading) return <div className="settings-section"><p className="field-hint">加载中…</p></div>;

  return (
    <div className="settings-section">
      <div className="settings-subsection">
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={draft.streamOutput}
            onChange={(e) => setDraft((current) => ({ ...current, streamOutput: e.target.checked }))}
          />
          流式输出智能体回复
        </label>
        <p className="field-hint" style={{ marginTop: 6 }}>
          开启后，模型生成的正文会实时显示；关闭时等待完整回复后一次显示。
        </p>
      </div>
        <div className="settings-subsection">
          <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              checked={draft.agentAccessMode === "full"}
              onChange={(e) => setDraft((current) => ({
                ...current,
                agentAccessMode: e.target.checked ? "full" : "approval",
              }))}
            />
            智能编造 Agent 完全访问（full）
          </label>
          <p className="field-hint" style={{ marginTop: 6 }}>
            默认「允许后访问」：写文件、烧 GPU、越域读取需逐项审批。开启「完全访问」后免逐项审批自动执行，但 GPU/LLM 配额与路径域校验照旧，全程留审计日志。
          </p>
        </div>
      <div className="settings-subsection">
        <h4>固化流程预设（智能编造）</h4>
        <p className="field-hint" style={{ marginTop: 4 }}>
          自由循环成功跑通的流程会先存为草稿，在对话里点「保留」（或在此处保留）后进入清单；
          之后对话里出现同类目标时优先整条重放，省去逐步探索的 token（durable/expensive 步骤照常走审批与配额）。
        </p>
        {recipes.length === 0 ? (
          <p className="field-hint" style={{ marginTop: 8 }}>
            还没有固化流程预设。让智能编造完整跑通一次任务，会自动固化为草稿；需在对话或此处点「保留」才进入此清单（当前没有任何已保留记录，属正常空态）。
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
            {recipes.map((r) => (
              <div className="image-model-card" key={r.id} style={{ padding: "8px 10px" }}>
                <div className="row-head">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <strong>{r.name}</strong>
                    <span className="field-hint" style={{ marginLeft: 8 }}>
                      {(r.status || "saved") === "draft" ? "草稿" : "已保留"}
                      {r.origin === "fabric" ? " · 自由循环固化" : " · 计划固化"}
                      {(r.plan?.steps?.length || 0) > 0 ? ` · ${r.plan?.steps?.length} 步` : ""}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    {(r.status || "saved") === "draft" && (
                      <button className="btn" onClick={() => void keepRecipePreset(r.id)}>保留</button>
                    )}
                    <button className="icon-btn" style={{ background: "#d23b3b" }} onClick={() => void removeRecipePreset(r.id)}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                {r.intent && <p className="field-hint" style={{ margin: "4px 0 0" }}>意图：{r.intent}</p>}
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="settings-subsection">
        <h4>固化知识库（智能编造流程规范）</h4>
        <p className="field-hint" style={{ marginTop: 4 }}>
          保存在 data/agent_knowledge/ 的流程规范与映射表：带 frontmatter（skill）的技能文档（如固化 01/02/03）
          按下方注入模式装载，无头的普通知识文档每次会话常驻注入。与上方「固化流程预设」是两套机制——配方需要
          完整跑通流程才会产生，知识文档放进来即生效。
        </p>
        <div className="knowledge-mode-cards">
          <label className={`knowledge-mode-card${kcfg.mode === "smart" ? " is-active" : ""}`}>
            <input
              type="radio" name="knowledge-mode"
              checked={kcfg.mode === "smart"}
              disabled={kcfgSaving}
              onChange={() => setMode("smart")}
            />
            <div className="knowledge-mode-card-body">
              <span className="knowledge-mode-card-title">smart</span>
              <span className="knowledge-mode-card-sub">按需装载（默认 · 推荐）</span>
            </div>
          </label>
          <label className={`knowledge-mode-card${kcfg.mode === "always" ? " is-active" : ""}`}>
            <input
              type="radio" name="knowledge-mode"
              checked={kcfg.mode === "always"}
              disabled={kcfgSaving}
              onChange={() => setMode("always")}
            />
            <div className="knowledge-mode-card-body">
              <span className="knowledge-mode-card-title">always</span>
              <span className="knowledge-mode-card-sub">全量常驻（老行为）</span>
            </div>
          </label>
          {kcfgSaving && <span className="field-hint" style={{ alignSelf: "center" }}>保存中…</span>}
          {kcfgError && <span className="field-hint" style={{ color: "#d23b3b", alignSelf: "center" }}>保存失败：{kcfgError}</span>}
        </div>
        <p className="field-hint" style={{ marginTop: 6 }}>
          {kcfg.mode === "smart"
            ? "smart：技能文档不再整篇常驻，会话只注入「技能名 → 触发描述」目录，模型命中对应场景时自动调用 knowledge.load_doc 按需拉全文执行——省上下文、多份规范互不干扰。勾选下方文档「常驻」可让个别关键技能回退为每次会话全量注入。"
            : "always：所有文档每次会话全量注入并强制遵循（老行为；受单次最多 4 份、每份 2 万字符上限约束）。"}
        </p>
        {knowledge.length === 0 ? (
          <p className="field-hint" style={{ marginTop: 8 }}>
            还没有固化知识文档。把规范以 .md 放入 data/agent_knowledge/，重新打开设置即可看到。
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
            {knowledge.map((k) => {
              const isSkill = Boolean(k.skill);
              const pinned = kcfg.always_docs.includes(k.name);
              return (
                <div className="image-model-card" key={k.file} style={{ padding: "8px 10px" }}>
                  <div className="row-head">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <strong>{k.name}</strong>
                      <span className="field-hint" style={{ marginLeft: 8 }}>
                        {k.file} · {(k.size / 1024).toFixed(1)} KB · {new Date(k.mtime).toLocaleString()}
                        {k.truncated ? " · 超出预览上限" : ""}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
                      <span
                        style={{
                          fontSize: 11, padding: "1px 7px", borderRadius: 999,
                          border: `1px solid ${isSkill ? "var(--accent)" : "var(--text-muted)"}`,
                          color: isSkill ? "var(--accent)" : "var(--text-muted)",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {isSkill ? `技能 · ${k.skill}` : "普通知识"}
                      </span>
                      {isSkill && kcfg.mode === "smart" && (
                        <label style={{ display: "flex", alignItems: "center", gap: 4, fontWeight: 400, cursor: "pointer", whiteSpace: "nowrap" }}>
                          <input
                            type="checkbox"
                            checked={pinned}
                            disabled={kcfgSaving}
                            onChange={(e) => toggleAlwaysDoc(k.name, e.target.checked)}
                            title="smart 模式下强制每次会话常驻注入全文"
                          />
                          常驻
                        </label>
                      )}
                      <button className="btn" onClick={() => void openKnowledge(k.name)}>
                        <FileText size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />查看
                      </button>
                    </div>
                  </div>
                  {isSkill && k.whenToUse && (
                    <p className="field-hint" style={{ margin: "4px 0 0" }}>触发：{k.whenToUse}</p>
                  )}
                  {isSkill && (k.tools?.length || 0) > 0 && (
                    <p className="field-hint" style={{ margin: "2px 0 0" }}>
                      工具：{k.tools!.join("、")}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="settings-subsection">
        <h4>全局上下文预算</h4>
        <div className="field-row">
          <div className="field">
            <label>提醒压缩 tokens</label>
            <input
              type="number"
              min={1000}
              max={draft.contextMaxTokens > 0 ? draft.contextMaxTokens - 1000 : undefined}
              step={1000}
              value={draft.contextReminderTokens}
              onChange={(e) => {
                const budgets = normalizeContextBudgets(Number(e.target.value), draft.contextMaxTokens);
                setDraft((current) => ({
                  ...current,
                  contextReminderTokens: budgets.reminder,
                  contextMaxTokens: budgets.max,
                }));
              }}
            />
          </div>
          <div className="field">
            <label>历史上下文上限 tokens（0=无限）</label>
            <input
              type="number"
              min={0}
              max={200000}
              step={1000}
              value={draft.contextMaxTokens}
              onChange={(e) => {
                const budgets = normalizeContextBudgets(draft.contextReminderTokens, Number(e.target.value));
                setDraft((current) => ({
                  ...current,
                  contextReminderTokens: budgets.reminder,
                  contextMaxTokens: budgets.max,
                }));
              }}
            />
          </div>
          <div className="field">
            <label>每角色历史条数</label>
            <input
              type="number"
              min={1}
              max={50}
              step={1}
              value={draft.historyPerRole}
              onChange={(e) => {
                const n = Number(e.target.value);
                const safe = Number.isFinite(n) ? Math.min(50, Math.max(1, Math.round(n))) : DEFAULT_HISTORY_PER_ROLE;
                setDraft((current) => ({ ...current, historyPerRole: safe }));
              }}
            />
          </div>
          <div className="field">
            <label>截断自愈次数（0=不自愈）</label>
            <input
              type="number"
              min={0}
              max={5}
              step={1}
              value={draft.selfhealAttempts}
              onChange={(e) => {
                const n = Number(e.target.value);
                const safe = Number.isFinite(n) ? Math.min(5, Math.max(0, Math.round(n))) : DEFAULT_SELFHEAL_ATTEMPTS;
                setDraft((current) => ({ ...current, selfhealAttempts: safe }));
              }}
            />
          </div>
        </div>
        <p className="field-hint">
          token 数为跨模型估算值。提醒值必须低于上限；上限只约束历史消息，本轮输入、系统提示和模型输出另行占用上下文。
          上限填 0 表示「无上限」：历史全量入上下文不裁剪（剧情模式降低失败率用；实际量仍受「每角色历史条数」约束，不会失控）。
          「每角色历史条数」是用户与 AI 各自读取的最近消息条数（默认 6，即共 12 条），再在上限内裁剪。
          修改后使用页面底部的“保存”生效。
        </p>
      </div>
      <BuiltinAgentsSection />
      <ProseStyleSection />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
        <h4 style={{ margin: 0 }}>自定义智能体（Agent 预设）</h4>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={add}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />新建</button>
          <button className="btn primary" onClick={save} disabled={saving}>
            <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{saving ? "保存中…" : "保存智能体"}
          </button>
        </div>
      </div>
      <p className="field-hint" style={{ marginTop: 8 }}>
        创建多个智能体预设，对话时在左下角切换。新建智能体默认进行普通对话，仅在明确执行意图下调用图片、视频或外部工具。改动需点「保存」。
        {saved && <span className="settings-saved" style={{ marginLeft: 8 }}>已保存</span>}
      </p>
      <div style={{ marginTop: 12 }}>
        {agents.length === 0 && <p className="field-hint">还没有自定义智能体（当前对话用内置默认行为）。点「新建」创建一个。</p>}
        {agents.map((a) => (
          <div className="image-model-card" key={a.id}>
            <div className="row-head">
              <label style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
                <input type="checkbox" checked={a.enabled} onChange={(e) => upd(a.id, { enabled: e.target.checked })} />
                <input value={a.name} onChange={(e) => upd(a.id, { name: e.target.value })} placeholder="智能体名称" style={{ fontWeight: 600, flex: 1 }} />
              </label>
              <button className="icon-btn" style={{ background: "#d23b3b" }} onClick={() => del(a.id)}><Trash2 size={14} /></button>
            </div>
            <div className="field">
              <label>系统提示词（人设/行为）</label>
              <textarea
                value={a.systemPrompt}
                onChange={(e) => upd(a.id, { systemPrompt: e.target.value })}
                placeholder="定义这个智能体的角色、语气、行为规则…"
                rows={6}
                style={{ width: "100%", resize: "vertical" }}
              />
              <p className="field-hint" style={{ marginTop: 4 }}>
                <AlertTriangle size={12} style={{ verticalAlign: "-2px", marginRight: 3 }} />
                系统提示词包含工具调用边界；大幅改动可能导致工具误调用。
              </p>
            </div>
            <div className="field">
              <label>长期记忆（可选）</label>
              <textarea value={a.memory} onChange={(e) => upd(a.id, { memory: e.target.value })} placeholder="关于用户的偏好/背景，会一直提供给这个智能体" rows={2} style={{ width: "100%", resize: "vertical" }} />
            </div>
            <div className="field">
              <label>本地工具</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                {TOOL_LABELS.map((t) => (
                  <label key={t.key} style={{ display: "flex", alignItems: "center", gap: 4, fontWeight: 400 }}>
                    <input type="checkbox" checked={a.tools[t.key]} onChange={(e) => updTool(a.id, t.key, e.target.checked)} />
                    {t.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="field">
              <label>MCP 服务器（勾选此智能体可调用的）</label>
              {mcpList.length === 0 ? (
                <p className="field-hint">还没有 MCP 服务器。去「扩展 → MCP 服务器」添加。</p>
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                  {mcpList.map((m) => (
                    <label key={m.id} style={{ display: "flex", alignItems: "center", gap: 4, fontWeight: 400 }}>
                      <input type="checkbox" checked={(a.mcpServerIds || []).includes(m.id)} onChange={() => toggleId(a.id, "mcpServerIds", m.id)} />
                      {m.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div className="field">
              <label>技能扩展（勾选此智能体启用的）</label>
              {skillList.length === 0 ? (
                <p className="field-hint">还没有技能。去「扩展 → 技能扩展」添加。</p>
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                  {skillList.map((s) => (
                    <label key={s.id} style={{ display: "flex", alignItems: "center", gap: 4, fontWeight: 400 }}>
                      <input type="checkbox" checked={(a.skillIds || []).includes(s.id)} onChange={() => toggleId(a.id, "skillIds", s.id)} />
                      {s.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div className="field">
              <label>请求参数（可选，留空用默认）</label>
              <div style={{ display: "flex", gap: 10 }}>
                <input type="number" step="0.1" min="0" max="2" value={a.temperature ?? ""} onChange={(e) => upd(a.id, { temperature: e.target.value === "" ? null : Number(e.target.value) })} placeholder="温度 (0~2)" />
                <input type="number" step="1" min="1" value={a.maxTokens ?? ""} onChange={(e) => upd(a.id, { maxTokens: e.target.value === "" ? null : Number(e.target.value) })} placeholder="最大 tokens" />
              </div>
            </div>
          </div>
        ))}
      </div>
      {knowledgeDoc && (
        <div className="modal-mask" onClick={() => setKnowledgeDoc(null)}>
          <div className="modal" style={{ width: "min(780px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {knowledgeDoc.name}
              </span>
              <button className="icon-btn" title="关闭" onClick={() => setKnowledgeDoc(null)}>
                <X size={16} />
              </button>
            </h3>
            <div
              className="guide-doc"
              style={{ maxHeight: "58vh", overflowY: "auto", fontSize: 14, lineHeight: 1.7 }}
              dangerouslySetInnerHTML={{ __html: renderMarkdown(knowledgeDoc.content) }}
            />
            {knowledgeDoc.truncated && (
              <p className="field-hint" style={{ marginTop: 8 }}>
                正文超过单文档注入/预览上限（2 万字符），仅展示前半部分。
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
