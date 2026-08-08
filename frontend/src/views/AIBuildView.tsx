import { useEffect, useRef, useState } from "react";
import { Sparkles, RefreshCw, Save, Eraser, LayoutTemplate, FolderOpen, Plus, Trash2, Undo2, Redo2 } from "lucide-react";
import { PageShell } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { useSettings, activeChatModel, resolvedEmbedModel } from "../stores/settings";
import {
  buildWorkflow, buildModule, buildDirect, buildPlan, saveWorkflow, syncNodes,
  listSkeletons, skeletonGraph, type Skeleton,
  listBuildSessions, getBuildSession, saveBuildSession, deleteBuildSession, type BuildSessionMeta, type BuildTurn,
} from "../api/ai";
import { comfyStatus } from "../api/comfyui";
import { fullUrl, postToFrame, isLafMessageFromStrict } from "../lib/lafLock";
import { requestFrameMessage } from "../lib/workflowCapture";
import { BuildWorkspaceRuntime } from "../lib/buildWorkspaceRuntime";
import { confirmedPlanExecution } from "../lib/workflowBuildExecution";
import {
  cancelWorkflowBuild, enqueueWorkflowBuild, subscribeWorkflowBuildActivities,
  type WorkflowBuildActivity,
} from "../lib/workflowBuildActivity";

// 一轮对话消息（左栏）。pendingNeed 非空=这是顾问模式的方案消息，带「同意执行/编辑/取消」按钮。
// pendingNeed=点同意时真正搭建用的需求(原需求+方案)；planText=纯方案文本，供「编辑」填回输入框改。
interface Msg { id: string; role: "user" | "assistant"; text: string; pendingNeed?: string; planText?: string; planOriginalNeed?: string; editing?: boolean; missingNodes?: string[]; alternatives?: Record<string, string[]>; retryNeed?: string; }

// AI 搭工作流（双栏）：左栏多轮对话与 AI 探讨，右栏完整功能 ComfyUI 画布。
// AI 每轮读回右侧画布作上下文 → 输出完整 graph → 写入右侧；用户可在画布里手动接着改。
export function AIBuildView({ onInstallNode }: { onInstallNode?: (q: string) => void } = {}) {
  const { settings } = useSettings();
  const chat = activeChatModel(settings);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [frameKey, setFrameKey] = useState(0);  // 递增强制重挂 iframe：ComfyUI 由未起→起后重连白屏 iframe
  const sawComfyDownRef = useRef(false);  // 是否观测到过 ComfyUI 未运行（仅在真·未起→起 时才需重挂）
  const [note, setNote] = useState("");
  const [incremental, setIncremental] = useState(false);  // 增量模式：冻结现有图只加模块（与精简直连互斥，初始关，选骨架时自动开）
  const [advisor, setAdvisor] = useState(false);  // 顾问模式：先出人话方案+确认，再执行（面向小白）
  const [direct, setDirect] = useState(true);  // 精简直连：信任强模型一次到位，只调1次模型，最快（默认开，与增量互斥）
  const [skeletons, setSkeletons] = useState<Skeleton[]>([]);
  const [loadingSkel, setLoadingSkel] = useState("");  // 正在载入的骨架 id
  // 搭建会话（进度保存 + 多开）
  const [sessionId, setSessionId] = useState<string>("");
  const sessionIdRef = useRef("");
  const sessionCreationRef = useRef<Promise<string> | null>(null);
  const [sessionName, setSessionName] = useState<string>("未命名工作流");
  const [sessions, setSessions] = useState<BuildSessionMeta[]>([]);
  const [showSessions, setShowSessions] = useState(false);
  const [deleteSess, setDeleteSess] = useState<BuildSessionMeta | null>(null);
  const skeletonIdRef = useRef<string>("");   // 当前会话用的骨架 id（存进会话）
  const frameRef = useRef<HTMLIFrameElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const buildRuntimeRef = useRef<BuildWorkspaceRuntime | null>(null);
  if (!buildRuntimeRef.current) buildRuntimeRef.current = new BuildWorkspaceRuntime(localStorage);
  const buildRuntime = buildRuntimeRef.current;
  const abortRef = useRef<AbortController | null>(null);   // 正在跑的搭建请求，供“停止”按钮中止
  const [activities, setActivities] = useState<WorkflowBuildActivity[]>([]);

  // 版本历史：每次成功写画布/载入底座都压一版，可撤销/重做（graph 存在前端，撤销即重载回画布）
  type GraphVer = { graph: Record<string, unknown>; label: string; at: number };
  const [versions, setVersions] = useState<GraphVer[]>([]);
  const [verIdx, setVerIdxState] = useState(-1);   // 当前生效版本下标，-1=无
  const verIdxRef = useRef(-1);   // 同步镜像，避免连续 pushVersion 读到过期下标
  const setVerIdx = (n: number) => { verIdxRef.current = n; setVerIdxState(n); };
  const VER_CAP = 20;

  // 停止当前长请求：中止 fetch，apiPost 会抛“已停止”，由各 catch 收尾
  const stopBuild = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    activities.filter((item) => item.status === "running" || item.status === "queued")
      .filter((item) => item.sessionId === (sessionId || "draft") || item.sessionId === "draft")
      .forEach((item) => cancelWorkflowBuild(item.id));
  };

  // 压入一个新版本：截掉当前之后的重做分支，超上限从头淘汰
  const pushVersion = (graph: Record<string, unknown>, label: string) => {
    if (!graph || !Object.keys(graph).length) return;
    setVersions((prev) => {
      const kept = prev.slice(0, verIdxRef.current + 1);
      const next = [...kept, { graph, label, at: Date.now() }];
      const trimmed = next.length > VER_CAP ? next.slice(next.length - VER_CAP) : next;
      setVerIdx(trimmed.length - 1);
      return trimmed;
    });
  };

  // 重置版本栈（新建/恢复/载入底座时用；base 非空则作为第 0 版）
  const resetVersions = (base?: Record<string, unknown>, label = "初始画布") => {
    if (base && Object.keys(base).length) {
      setVersions([{ graph: base, label, at: Date.now() }]);
      setVerIdx(0);
    } else {
      setVersions([]);
      setVerIdx(-1);
    }
  };

  // 跳到某版本：重载回画布
  const gotoVersion = (idx: number) => {
    if (idx < 0 || idx >= versions.length) return;
    postToFrame(frameRef.current?.contentWindow, "load", { workflow: versions[idx].graph }, settings.comfyuiUrl);
    setVerIdx(idx);
    setNote(`已切到版本 ${idx + 1}/${versions.length}：${versions[idx].label}`);
  };
  const undoVersion = () => gotoVersion(verIdx - 1);
  const redoVersion = () => gotoVersion(verIdx + 1);

  const src = fullUrl(settings.comfyuiUrl);
  const canSend = input.trim().length > 0 && !busy && !!chat.modelName && ready;

  // 收子帧 ready（画布可用）。依赖 frameKey：iframe 重挂后重新补 ping 重握手。
  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      if (isLafMessageFromStrict(ev, frameRef.current?.contentWindow, settings.comfyuiUrl, "ready")) setReady(true);
    };
    window.addEventListener("message", onMsg);
    // 补一次 ping，防错过首帧 ready
    const t = setTimeout(() => postToFrame(frameRef.current?.contentWindow, "ping_ready", undefined, settings.comfyuiUrl), 1500);
    return () => { window.removeEventListener("message", onMsg); clearTimeout(t); };
  }, [frameKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ComfyUI 就绪守护：ComfyUI 首次启动需 20~40 秒，iframe 若在其未起时加载会白屏且不自重连。
  // 仅在观测到「未运行 → 运行」真转变时才重挂 iframe：
  // 若 ComfyUI 一开始就在跑（页面打开时已就绪），iframe 不会白屏，此时重挂反而会打断
  // 正在进行的 laf_full 扩展初始化/握手（重扩展装载 >3s 很常见）→ 画布载入被中断 → 空白。
  // 只有 autostart 引入的「起初没起、几十秒后才起」场景才需要重挂救白屏。
  useEffect(() => {
    if (ready) return;
    let alive = true;
    const timer = setInterval(async () => {
      try {
        const st = await comfyStatus(settings.comfyuiUrl);
        if (!alive) return;
        if (!st.running) {
          sawComfyDownRef.current = true;   // 记下确实见过未运行
          return;
        }
        // 运行中：只有此前见过未运行（真·未起→起）才重挂救白屏；
        // 一直在跑的情况不动，交给正常握手，避免打断扩展初始化。
        if (sawComfyDownRef.current) {
          clearInterval(timer);             // 只重挂一次，交给重握手；避免反复重挂打断握手
          setReady(false);                  // 重挂期间禁用发送/骨架按钮，避免 load 投进未挂扩展的空窗被丢弃
          setFrameKey((k) => k + 1);
        }
      } catch { sawComfyDownRef.current = true; /* 探测失败视作未起，继续等 */ }
    }, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [ready, settings.comfyuiUrl]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [msgs]);

  useEffect(() => subscribeWorkflowBuildActivities(setActivities), []);

  // 后端已把终态消息和 graph 写入会话；当前页只重新载入，避免前后端各追加一次。
  useEffect(() => {
    if (!ready) return;
    const key = sessionId || "draft";
    for (const activity of buildRuntime.claimTerminal(activities, key)) {
      if (activity.id.startsWith("pending-")) {
        setMsgs((current) => [...current, { id: crypto.randomUUID(), role: "assistant", text: `请求失败：${activity.error || "未知错误"}` }]);
        buildRuntime.completeActivity(key, activity.id);
        continue;
      }
      void getBuildSession(activity.sessionId).then((saved) => {
        setMsgs((saved.msgs as Msg[]) || []);
        if (saved.graph && Object.keys(saved.graph).length) {
          postToFrame(frameRef.current?.contentWindow, "load", { workflow: saved.graph }, settings.comfyuiUrl);
          pushVersion(saved.graph, activity.need.slice(0, 20) || "AI 生成");
        }
        buildRuntime.completeActivity(key, activity.id);
        refreshSessions();
      }).catch(() => { buildRuntime.releaseActivity(activity.id); });
    }
  }, [activities, ready, sessionId, settings.comfyuiUrl]);

  // 自动保存进度：对话变化后防抖 1.5s 存一次（有内容且画布就绪才存），避免刷新/重启丢进度
  const autoSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!buildRuntime.canAutosave(ready, msgs.length > 0)) return;
    if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    autoSaveRef.current = setTimeout(() => { saveProgress(true); }, 1500);
    return () => { if (autoSaveRef.current) clearTimeout(autoSaveRef.current); };
  }, [msgs]); // eslint-disable-line react-hooks/exhaustive-deps

  // 载入骨架候选（内置 + 工作流文件夹），供空画布时选正确底座
  useEffect(() => {
    listSkeletons(settings.workflowDir).then((r) => setSkeletons(r.skeletons)).catch(() => setSkeletons([]));
  }, [settings.workflowDir]);

  const push = (role: Msg["role"], text: string) =>
    setMsgs((m) => [...m, { id: crypto.randomUUID(), role, text }]);

  // 选骨架：取 graph → load 进画布 → 等 iframe 回 loaded 确认渲染后再报成功。
  // 不再乐观直接报成功：iframe 可能因慢启动/扩展未就绪把 load 丢弃 → 画布空白但提示已载入。
  const loadSkeleton = async (s: Skeleton) => {
    setLoadingSkel(s.id);
    setNote("");
    try {
      const r = await skeletonGraph(s.id, settings.workflowDir);
      // 等 iframe 回 loaded（applyLoad/loadAnyFormat 完成，无论成败都会回）。丢弃/超时→null。
      const ack = await requestFrameMessage<{ ok?: boolean }>({
        frameWindow: frameRef.current?.contentWindow, comfyUrl: settings.comfyuiUrl,
        requestType: "load", expectedType: "loaded", timeoutMs: 15000,
        payload: { workflow: r.graph },
      });
      if (!ack) { setNote("画布未响应载入（ComfyUI 可能仍在初始化），请稍候重试"); return; }
      resetVersions(r.graph, `骨架「${s.name}」`);
      skeletonIdRef.current = s.id;
      // 选了骨架底座 → 默认切增量模式（在此底座上增量加模块，而非精简直连推倒重搭）
      setDirect(false);
      setIncremental(true);
      push("assistant", `已载入骨架「${s.name}」(${s.node_count} 节点)作为底座，已切到增量模式。接下来告诉我要改什么，我会在它基础上增量调整，不会推倒重来。`);
    } catch (e) {
      setNote("载入骨架失败：" + (e as Error).message);
    } finally {
      setLoadingSkel("");
    }
  };

  // —— 搭建会话：进度保存 + 多开 ——
  const refreshSessions = () =>
    listBuildSessions().then((r) => setSessions(r.sessions)).catch(() => {});

  useEffect(() => { refreshSessions(); }, []);

  // 首次画布就绪后：优先处理「从生图对话 /w 带过来的工作流」，否则恢复上次会话
  const restoredRef = useRef(false);
  useEffect(() => {
    if (!ready || restoredRef.current) return;
    restoredRef.current = true;
    // 1) 生图对话点「在搭工作流页编辑」带来的 graph → 新建会话装入（不覆盖既有进度）
    const pending = localStorage.getItem("laf_pending_build_graph");
    if (pending) {
      localStorage.removeItem("laf_pending_build_graph");
      try {
        const graph = JSON.parse(pending);
        buildRuntime.startNew();
        setSessionId("");
        postToFrame(frameRef.current?.contentWindow, "load", { workflow: graph }, settings.comfyuiUrl);
        resetVersions(graph, "带入的工作流");
        push("assistant", "已把工作流写入右侧画布，你可以直接手动调整，或继续告诉我要改什么。");
        setTimeout(() => saveProgress(true), 800);  // load 后自动存成新会话
        return;
      } catch { /* 解析失败则走正常恢复 */ }
    }
    // 2) 否则恢复上次会话
    const last = buildRuntime.lastSessionId();
    if (last) restoreSession(last);
  }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // 保存当前进度：读回画布图 + 当前对话，存进会话（新建则生成 id）
  const saveProgress = async (silent = false) => {
    const generation = buildRuntime.generation();
    const currentSessionId = sessionId;
    try {
      const graph = await readGraph();
      if (!buildRuntime.owns(generation)) return;
      const r = await saveBuildSession({
        id: currentSessionId, name: sessionName, msgs, graph, skeletonId: skeletonIdRef.current,
      });
      if (!buildRuntime.finishSave(generation, r.id)) return;
      if (!currentSessionId) setSessionId(r.id);
      if (!currentSessionId) sessionIdRef.current = r.id;
      refreshSessions();
      if (!silent) setNote(`已保存进度到「${r.name}」`);
    } catch (e) {
      if (!silent) setNote("保存进度失败：" + (e as Error).message);
    }
  };

  // 恢复某会话：拉完整内容 → 恢复对话 → load 画布图回右侧
  const restoreSession = async (id: string) => {
    const generation = buildRuntime.startRestore();
    if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    try {
      const s = await getBuildSession(id);
      if (!buildRuntime.finishRestore(generation, s.id)) return;
      setSessionId(s.id);
      sessionIdRef.current = s.id;
      setSessionName(s.name);
      skeletonIdRef.current = s.skeleton_id || "";
      setMsgs((s.msgs as Msg[]) || []);
      if (s.graph && Object.keys(s.graph).length) {
        postToFrame(frameRef.current?.contentWindow, "load", { workflow: s.graph }, settings.comfyuiUrl);
        resetVersions(s.graph, "恢复的会话");
      } else {
        resetVersions();
      }
      setShowSessions(false);
      setNote(`已恢复会话「${s.name}」`);
    } catch (e) {
      setNote("恢复会话失败：" + (e as Error).message);
    }
  };

  // 新建会话：清空对话 + 清空画布 + 重置 id
  const newSession = () => {
    buildRuntime.startNew();
    if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    setSessionId("");
    sessionIdRef.current = "";
    sessionCreationRef.current = null;
    setSessionName("未命名工作流");
    skeletonIdRef.current = "";
    setMsgs([]);
    resetVersions();
    postToFrame(frameRef.current?.contentWindow, "clear_graph", undefined, settings.comfyuiUrl);
    setShowSessions(false);
    setNote("已新建空白会话");
  };

  const doDeleteSession = async () => {
    if (!deleteSess) return;
    const id = deleteSess.id;
    setDeleteSess(null);
    try {
      await deleteBuildSession(id);
      if (id === sessionId) newSession();
      refreshSessions();
    } catch (e) {
      setNote("删除失败：" + (e as Error).message);
    }
  };

  // 读回右侧画布当前 API 格式（作 AI 上下文；空画布返回 {}）
  const readGraph = async (): Promise<Record<string, unknown>> => {
    const r = await requestFrameMessage<{ output?: Record<string, unknown>; ok?: boolean }>({
      frameWindow: frameRef.current?.contentWindow, comfyUrl: settings.comfyuiUrl,
      requestType: "request_api_prompt", expectedType: "api_prompt", timeoutMs: 8000,
    });
    return r?.output || {};
  };

  // 读回右侧画布的 UI(编辑器)格式（app.graph.serialize()，含 nodes/links 与布局）。
  // 落盘保存必须用这个：ComfyUI 侧栏打开工作流走标准载入，只认 UI 格式；
  // 存 API 格式(无 nodes 数组)会被解析成空白画布。同一条 api_prompt 消息里已带回 workflow。
  const readGraphUI = async (): Promise<Record<string, unknown> | null> => {
    const r = await requestFrameMessage<{ workflow?: Record<string, unknown>; ok?: boolean }>({
      frameWindow: frameRef.current?.contentWindow, comfyUrl: settings.comfyuiUrl,
      requestType: "request_api_prompt", expectedType: "api_prompt", timeoutMs: 8000,
    });
    return r?.workflow || null;
  };

  const ensureSessionForTask = async (graph: Record<string, unknown>, nextMsgs: Msg[] = msgs) => {
    if (sessionIdRef.current) {
      await saveBuildSession({
        id: sessionIdRef.current, name: sessionName, msgs: nextMsgs,
        graph, skeletonId: skeletonIdRef.current,
      });
      refreshSessions();
      return sessionIdRef.current;
    }
    if (!sessionCreationRef.current) {
      sessionCreationRef.current = saveBuildSession({
        name: sessionName, msgs: nextMsgs, graph, skeletonId: skeletonIdRef.current,
      }).then((saved) => {
        sessionIdRef.current = saved.id;
        setSessionId(saved.id);
        buildRuntime.finishSave(buildRuntime.generation(), saved.id);
        refreshSessions();
        return saved.id;
      }).finally(() => { sessionCreationRef.current = null; });
    }
    return sessionCreationRef.current;
  };

  const doSend = async () => {
    const need = input.trim();
    if (!need || !ready || !chat.modelName) return;
    setInput("");
    const history: BuildTurn[] = msgs.map(({ role, text }) => ({ role: role as BuildTurn["role"], text }));
    const userMsg: Msg = { id: crypto.randomUUID(), role: "user", text: need };
    const nextMsgs = [...msgs, userMsg];
    setMsgs(nextMsgs);
    try {
      const current = await readGraph();
      const targetSessionId = await ensureSessionForTask(current, nextMsgs);
      const hasNodes = Object.keys(current).length > 0;
      const mode = advisor ? "plan" : direct ? "direct" : incremental && hasNodes ? "module" : "workflow";
      enqueueWorkflowBuild({ need, mode, sessionId: targetSessionId, chat, embed: resolvedEmbedModel(settings), comfyUrl: settings.comfyuiUrl, workflowDir: settings.workflowDir, currentGraph: current, history, direct, incremental });
      setNote("已加入搭建队列；离开此页面后仍会继续运行。右下角活动入口可查看进度。");
    } catch (e) {
      push("assistant", "请求失败：" + (e as Error).message);
    }
  };

  // 真正生成并写入画布（直接模式直接调；顾问模式由「同意执行」按钮调）
  // signal 为空时自建一个 AbortController 并登记，让“停止”按钮也能中止这些内部入口
  const doExecute = async (need: string, signal?: AbortSignal, historyOverride?: BuildTurn[]) => {
    let sig = signal;
    if (!sig) {
      const ac = new AbortController();
      abortRef.current = ac;
      sig = ac.signal;
    }
    const current = await readGraph();               // 带上当前画布作上下文
    const hasNodes = Object.keys(current).length > 0;
    const history: BuildTurn[] = historyOverride || msgs.map(({ role, text }) => ({ role, text }));
    // 精简直连(默认)：只调1次模型，信任Opus一次到位，最快不超时——优先走它。
    // 否则按增量/整图老路（多层校验自修，慢但对弱模型稳）。
    // 不传 proxy 给对话模型：与仓库对话同路径（默认 httpx 读系统环境），强行代理反而切断中转连接
    const r = direct
      ? await buildDirect({ need, chat, embed: resolvedEmbedModel(settings), comfyUrl: settings.comfyuiUrl, currentGraph: hasNodes ? current : undefined, history, signal: sig })
      : incremental && hasNodes
      ? await buildModule({ need, chat, embed: resolvedEmbedModel(settings), comfyUrl: settings.comfyuiUrl, currentGraph: current, history, signal: sig })
      : await buildWorkflow({
          need, chat, embed: resolvedEmbedModel(settings),
          comfyUrl: settings.comfyuiUrl, workflowDir: settings.workflowDir,
          currentGraph: current, save: false,  // 迭代中途不落盘，只回图写画布
          history,
          signal: sig,
        });
    const miss = r.missing_nodes && r.missing_nodes.length ? r.missing_nodes : undefined;
    const alts = r.alternatives && Object.keys(r.alternatives).length ? r.alternatives : undefined;
    if (r.ok && r.graph && Object.keys(r.graph).length) {
      postToFrame(frameRef.current?.contentWindow, "load", { workflow: r.graph }, settings.comfyuiUrl);
      // 版本历史：栈空且改前画布非空，先把改前状态压成基版，撤销可回到它
      if (versions.length === 0 && hasNodes) pushVersion(current, "改前画布");
      pushVersion(r.graph, need.slice(0, 20) || "AI 生成");
      const warn = r.warnings && r.warnings.length
        ? "\n提示（不影响写入，可继续调整）：\n" + r.warnings.join("\n")
        : "";
      setMsgs((m) => [...m, { id: crypto.randomUUID(), role: "assistant",
        text: "已把工作流写入右侧画布，你可以直接手动调整，或继续告诉我要改什么。" + warn,
        missingNodes: miss, alternatives: alts, retryNeed: need }]);
    } else {
      const warn = r.warnings && r.warnings.length ? "\n" + r.warnings.join("\n") : "";
      setMsgs((m) => [...m, { id: crypto.randomUUID(), role: "assistant",
        text: "没能生成合法工作流：\n" + (r.errors.join("\n") || "未知错误") + warn,
        missingNodes: miss, alternatives: alts, retryNeed: need }]);
    }
  };

  // 用本机平替节点重新生成：把「缺失节点→本机平替」映射拼进需求，让 AI 改用平替重搭
  const retryWithAlternatives = async (alts: Record<string, string[]>, need: string) => {
    const lines = Object.entries(alts)
      .filter(([, v]) => v && v.length)
      .map(([miss, subs]) => `- 「${miss}」本机没装，改用本机已有的：${subs.slice(0, 3).join(" 或 ")}`);
    if (lines.length === 0) {
      push("assistant", "知识库里没找到这些缺失节点的本机平替，建议去节点管理安装原节点。");
      return;
    }
    const newNeed = `${need}\n\n【重要·节点替换要求】以下节点本机未安装，请改用括号内的本机已装平替节点重新搭建，不要再用未装的：\n${lines.join("\n")}`;
    try {
      const current = await readGraph();
      const targetSessionId = await ensureSessionForTask(current);
      const history = msgs.map(({ role, text }) => ({ role: role as BuildTurn["role"], text }));
      enqueueWorkflowBuild({ need: newNeed, mode: direct ? "direct" : incremental && Object.keys(current).length ? "module" : "workflow", sessionId: targetSessionId, chat, embed: resolvedEmbedModel(settings), comfyUrl: settings.comfyuiUrl, workflowDir: settings.workflowDir, currentGraph: current, history, direct, incremental });
      setNote("平替重搭已加入队列。");
    } catch (e) {
      push("assistant", "请求失败：" + (e as Error).message);
    }
  };

  // 顾问模式「同意执行」：用方案对应的 need 真正生成
  const approvePlan = async (msgId: string, need: string) => {
    setMsgs((m) => m.map((x) => (x.id === msgId ? { ...x, pendingNeed: undefined } : x)));  // 收起按钮
    try {
      const current = await readGraph();
      const targetSessionId = await ensureSessionForTask(current);
      const mode = direct ? "direct" : incremental && Object.keys(current).length ? "module" : "workflow";
      enqueueWorkflowBuild({ need, mode, sessionId: targetSessionId, chat, embed: resolvedEmbedModel(settings), comfyUrl: settings.comfyuiUrl, workflowDir: settings.workflowDir, currentGraph: current, history: msgs.map(({ role, text }) => ({ role: role as BuildTurn["role"], text })), direct, incremental });
      setNote("确认执行已加入搭建队列。");
    } catch (e) {
      push("assistant", "请求失败：" + (e as Error).message);
    }
  };

  const doSync = async () => {
    setNote("同步节点库中…");
    try {
      const r = await syncNodes(resolvedEmbedModel(settings), settings.comfyuiUrl);
      setNote(`已开始同步 ${r.total_packs} 个节点包，进度见「节点知识库」页`);
    } catch (e) {
      setNote("同步失败：" + (e as Error).message);
    }
  };

  const doClear = () => {
    postToFrame(frameRef.current?.contentWindow, "clear_graph", undefined, settings.comfyuiUrl);
    resetVersions();
    setNote("已清空画布");
  };

  const doSave = async () => {
    setNote("读取画布并保存…");
    try {
      // 保存 UI 格式（含 nodes/links + 布局），ComfyUI 侧栏打开才不空白。
      const ui = await readGraphUI();
      const nodes = (ui?.nodes as unknown[]) || [];
      if (!ui || !nodes.length) { setNote("画布为空，无可保存内容"); return; }
      const r = await saveWorkflow({ graph: ui, embed: resolvedEmbedModel(settings), workflowDir: settings.workflowDir });
      setNote("已保存到：" + r.path);
    } catch (e) {
      setNote("保存失败：" + (e as Error).message);
    }
  };

  return (
    <PageShell
      title="AI 搭工作流"
      actions={
        <>
          <button className="btn" onClick={newSession} title="新建一个空白搭建会话">
            <Plus size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />新建
          </button>
          <button className="btn" onClick={() => saveProgress(false)} disabled={!ready} title="保存当前对话+画布进度，重启/刷新后可恢复">
            <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />保存进度
          </button>
          <div style={{ position: "relative", display: "inline-block" }}>
            <button className="btn" onClick={() => { refreshSessions(); setShowSessions((v) => !v); }} title="打开/切换已保存的会话">
              <FolderOpen size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />会话（{sessions.length}）
            </button>
            {showSessions && (
              <div className="sess-dropdown">
                {sessions.length === 0 && <div className="sess-empty">还没有保存的会话</div>}
                {sessions.map((s) => (
                  <div key={s.id} className={`sess-item${s.id === sessionId ? " active" : ""}`}>
                    <button className="sess-open" onClick={() => restoreSession(s.id)} title="恢复此会话">
                      <span className="sess-name">{s.name}</span>
                      <span className="sess-meta">{s.node_count} 节点 · {s.msg_count} 条对话</span>
                    </button>
                    <button className="icon-btn" title="删除会话" onClick={() => setDeleteSess(s)}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <button className="btn" onClick={doSync}>
            <RefreshCw size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />同步节点库
          </button>
          <button className="btn" onClick={undoVersion} disabled={!ready || verIdx <= 0}
            title={verIdx > 0 ? `撤销到上一版：${versions[verIdx - 1]?.label ?? ""}` : "没有可撤销的版本"}>
            <Undo2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />撤销
          </button>
          <button className="btn" onClick={redoVersion} disabled={!ready || verIdx < 0 || verIdx >= versions.length - 1}
            title={verIdx < versions.length - 1 ? `重做到下一版：${versions[verIdx + 1]?.label ?? ""}` : "没有可重做的版本"}>
            <Redo2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />重做
          </button>
          {versions.length > 0 && (
            <span style={{ fontSize: 12, color: "var(--text-muted)", alignSelf: "center" }}>
              版本 {verIdx + 1}/{versions.length}
            </span>
          )}
          <button className="btn" onClick={doClear} disabled={!ready} title="清空右侧画布，从空白重新搭建">
            <Eraser size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />清空画布
          </button>
          <button className="btn" onClick={doSave} disabled={!ready}>
            <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />保存到工作流目录
          </button>
        </>
      }
    >
      {note && <p style={{ color: "var(--text-muted)", fontSize: 12, margin: "0 0 8px" }}>{note}</p>}
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 8px" }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>当前会话：</span>
        <input value={sessionName} onChange={(e) => setSessionName(e.target.value)}
          onBlur={() => { if (sessionId) saveProgress(true); }}
          placeholder="给这个工作流起个名字" style={{ fontSize: 13, maxWidth: 260, padding: "3px 8px" }} />
      </div>
      <div className="ai-build-split">
        <div className="ai-build-chat">
          <div className="ai-build-msgs" ref={scrollRef}>
            {msgs.length === 0 && (
              <div className="ai-build-empty">
                <div className="ico"><Sparkles size={22} /></div>
                先选一个<b>骨架底座</b>再让我改，比从零硬搭更稳（底座已验证正确，我只做增量调整）。<br />
                也可直接描述需求让我从零搭，但复杂流更推荐先挑底座。<br />
                <span style={{ opacity: 0.8 }}>首次使用请先点右上「同步节点库」。</span>
                {skeletons.length > 0 && (
                  <div className="skel-picker">
                    <div className="skel-picker-title"><LayoutTemplate size={14} /> 选一个骨架底座</div>
                    {skeletons.map((s) => (
                      <button key={s.id} className="skel-item" disabled={!ready || !!loadingSkel}
                        onClick={() => loadSkeleton(s)} title={s.desc}>
                        <span className="skel-name">{s.name}</span>
                        <span className="skel-meta">{s.source === "builtin" ? "内置" : "文件"} · {s.node_count} 节点</span>
                        {loadingSkel === s.id && <span className="skel-loading">载入中…</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {msgs.map((m) => (
              <div key={m.id} className={`ai-build-msg ${m.role}`}>
                <div className="avatar">{m.role === "user" ? "我" : <Sparkles size={14} />}</div>
                <div className={`bubble${m.editing ? " bubble-editing" : ""}`}>
                  {m.editing ? (
                    // 编辑态：直接在方案气泡里改文本，改完「保存并执行」照改后方案搭
                    <>
                      <textarea
                        className="plan-edit"
                        value={m.planText ?? m.text}
                        onChange={(e) => setMsgs((arr) => arr.map((x) => (x.id === m.id ? { ...x, planText: e.target.value } : x)))}
                        rows={10}
                      />
                      <div className="plan-actions">
                        <button className="btn primary" disabled={busy}
                          onClick={() => {
                            const plan = (m.planText ?? m.text);
                            const execution = confirmedPlanExecution(m.planOriginalNeed || "按确认方案整理当前工作流", plan);
                            setMsgs((arr) => arr.map((x) => (x.id === m.id ? { ...x, editing: false, text: plan, pendingNeed: undefined } : x)));
                            approvePlan(m.id, execution.need);
                          }}>
                          <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />保存并执行
                        </button>
                        <button className="btn" disabled={busy}
                          title="退出编辑，保留原方案和按钮"
                          onClick={() => setMsgs((arr) => arr.map((x) => (x.id === m.id ? { ...x, editing: false } : x)))}>
                          取消编辑
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      {m.text}
                      {m.pendingNeed && (
                        <div className="plan-actions">
                          <button className="btn primary" disabled={busy}
                            onClick={() => approvePlan(m.id, m.pendingNeed!)}>
                            <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />同意执行
                          </button>
                          <button className="btn" disabled={busy}
                            title="直接在这段方案上修改，改完保存并执行"
                            onClick={() => setMsgs((arr) => arr.map((x) => (x.id === m.id ? { ...x, editing: true } : x)))}>
                            编辑
                          </button>
                          <button className="btn" disabled={busy}
                            title="撤销这条方案"
                            onClick={() => setMsgs((arr) => arr.filter((x) => x.id !== m.id))}>
                            取消
                          </button>
                        </div>
                      )}
                      {m.missingNodes && m.missingNodes.length > 0 && (
                        <div className="plan-actions">
                          {m.missingNodes.map((n) => (
                            <button key={n} className="btn" title={`跳到节点管理市场，自动搜索 ${n}`}
                              onClick={() => onInstallNode?.(n)}>
                              去安装「{n}」
                            </button>
                          ))}
                          {m.alternatives && Object.values(m.alternatives).some((v) => v && v.length) && m.retryNeed && (
                            <button className="btn primary" disabled={busy}
                              title="查节点知识库里的本机同类节点，改用平替重新生成一份方案"
                              onClick={() => retryWithAlternatives(m.alternatives!, m.retryNeed!)}>
                              <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />用本机平替重搭
                            </button>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
            {activities.some((item) => item.sessionId === (sessionId || "draft") && item.status === "running") && (
              <div className="ai-build-msg assistant">
                <div className="avatar"><Sparkles size={14} /></div>
                <div className="bubble" style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span>思考中…</span>
                  <button className="btn" style={{ padding: "2px 10px", fontSize: 12 }}
                    title="停止本次搭建请求" onClick={stopBuild}>
                    停止
                  </button>
                </div>
              </div>
            )}
          </div>
          {activities.filter((item) => (item.sessionId === (sessionId || "draft") || item.sessionId === "draft") && (item.status === "queued" || item.status === "running")).length > 0 && (
            <div className="ai-build-activity-list">
              {activities.filter((item) => (item.sessionId === (sessionId || "draft") || item.sessionId === "draft") && (item.status === "queued" || item.status === "running")).map((item) => (
                <div className="ai-build-activity" key={item.id}>
                  <span className="ai-build-activity-status">{item.status === "running" ? "思考中" : "排队中"}</span>
                  <span className="ai-build-activity-text">{item.need}</span>
                  <button className="btn" onClick={() => { cancelWorkflowBuild(item.id); setInput(item.need); setNote("已停止该条搭建，修改要求后可重新发送。"); }}>
                    引导修改
                  </button>
                  <button className="icon-btn" title="取消这条搭建" onClick={() => cancelWorkflowBuild(item.id)}>×</button>
                </div>
              ))}
            </div>
          )}
          {!chat.modelName && (
            <p style={{ color: "var(--warning)", fontSize: 12, margin: "4px 0" }}>
              未配置对话模型，请先到「设置 → 对话模型」添加。
            </p>
          )}
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)", margin: "0 0 6px", opacity: incremental ? 0.5 : 1 }}>
            <input type="checkbox" checked={direct} disabled={incremental}
              onChange={(e) => { setDirect(e.target.checked); if (e.target.checked) setIncremental(false); }} />
            精简直连（信任强模型一次到位，只调 1 次模型、不反复自修，最快不超时；推荐 Opus/GPT-4 等强模型开）
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)", margin: "0 0 6px", opacity: direct ? 0.5 : 1 }}>
            <input type="checkbox" checked={incremental} disabled={direct}
              onChange={(e) => { setIncremental(e.target.checked); if (e.target.checked) setDirect(false); }} />
            增量模式（冻结现有画布，只在其上增量加模块；关精简直连后生效，多层校验自修，慢但对弱模型稳）
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)", margin: "0 0 6px" }}>
            <input type="checkbox" checked={advisor} onChange={(e) => setAdvisor(e.target.checked)} />
            顾问模式（先用大白话讲清方案，你点「同意执行」再动画布，适合不熟节点的新手）
          </label>
          <div className="ai-build-input">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (canSend) doSend(); } }}
              placeholder={ready ? "描述需求或要改的地方，回车发送（Shift+回车换行）" : "画布载入中…"}
              rows={2}
            />
            <button className="btn primary" disabled={!canSend} onClick={doSend}>
              <Sparkles size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送
            </button>
          </div>
        </div>
        <div className="ai-build-canvas-wrap">
          <div className="ai-build-canvas">
            {!ready && (
              <div className="ai-build-canvas-hint">ComfyUI 启动中，请稍候…（首次启动约 20~40 秒）</div>
            )}
            <iframe key={frameKey} ref={frameRef} src={src} title="ComfyUI 画布" />
          </div>
          <p className="ai-build-canvas-note">
            提示：若画布空白，多半是另开了 ComfyUI 网页标签抢占了画布——关掉其他 ComfyUI 标签后刷新本页即可。
          </p>
        </div>
      </div>
      {deleteSess && (
        <ConfirmModal
          title="删除会话"
          message={`确认删除会话「${deleteSess.name}」？此进度将无法恢复。`}
          confirmText="删除"
          danger
          onConfirm={doDeleteSession}
          onCancel={() => setDeleteSess(null)}
        />
      )}
    </PageShell>
  );
}
