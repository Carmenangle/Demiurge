// 聊天 / 群聊主界面（2026-11-09）：渲染在 ChatView 原本的对话区，不使用弹窗。
// - 模式由 ChatView 顶部「剧情 / 私聊 / 群聊」模式条控制；私聊只取一位角色，
//   群聊按成员顺序各回一次（后端 chat_room 负责轮转），明确创作命令仍走通用专家。
// - 独立会话线：threadId = <repoId>__chat，快照由后端逐条落盘（带 speaker）。
// - 纯展示：成员解析/历史序列化/消息投影都在 lib/chatRoom。
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Send, Square } from "lucide-react";
import type { Repo } from "../../stores/repos";
import { activeUserPersona, type useSettings } from "../../stores/settings";
import type { Chat, Embed } from "../../api/ai/wire";
import { multiAgent, fetchSnapshot, fetchAgentRunning, fetchChatReadState, cancelAgent, chatFollowup, chatReaction, markChatRead, chatInitiativeScan, saveSnapshot, editMessageRemote, type CharacterRosterResult } from "../../api/ai";
import { avatarUrl, characterMedia } from "../../api/characters";
import { interruptComfy, localViewUrl } from "../../api/comfyui";
import type { Template } from "../../api/workflows";
import { generateChatMediaImage, generateChatMediaImageWithRetry, resumeChatMediaImage } from "../../lib/chatMediaComfy";
import { addUnread, clearSpeakerUnread, clearUnread, getUnread, subscribeUnread } from "../../lib/chatRoomUnread";
import { markChatRunStarted, setActiveChatThread } from "../../lib/chatBackgroundActivity";
import { isWorldbookRoster, parseRosterOverrides, rosterNamesOf, findBindingKey, type CharacterRosterOverrides } from "../../lib/characterRoster";
import { waitForAgentRelease } from "../../lib/agentReleaseWait";
import { ensureComfyReady, waitForComfyIdle } from "../../lib/comfyReadiness";
import {
  cancelChatMediaTask, chatMediaTasksForThread, clearChatMediaResume, finishChatMediaTask,
  loadChatMediaResume, saveChatMediaResume, setChatMediaTaskPhase,
  startChatMediaTask, subscribeChatMediaTasks, type ChatMediaTask,
} from "../../lib/chatMediaActivity";
import { effectiveGlobalProxyUrl, globalProxyAddress, resolveEndpointProxy } from "../../lib/modelProxy";
import { promptTemplateInstructions } from "../../lib/promptTemplates";
import {
  buildChatRoomHistory, chatContextPressure, chatRoomMessagesToChat, chatRoomThreadId,
  defaultChatRoomSelection, formatChatTimeDivider, loadHandledChatMedia, markHandledChatMedia,
  mergeChatRoomMessages, mergeSnapshotMessages,
  resolveChatRoomMembers, shouldStickChatToBottom, shouldUpdateChatAvatar, shouldShowChatTimeDivider, sortChatRoomMembers,
  sanitizeChatText,
  type ChatRoomMode,
} from "../../lib/chatRoom";
import type { ChatMessage } from "../../types/chat";
import type { ChatRoomMessage } from "../../api/chatStreamProtocol";
import type { AgentInvocation } from "../../api/ai/agentInvocation";
import { CharacterRosterModal } from "./CharacterRosterModal";

type Settings = ReturnType<typeof useSettings>["settings"];

/** 拍摄彻底失败时的角色借口：技术原因只进日志，不当成系统播报铺在主区。 */
const CHAT_MEDIA_FAILED_EXCUSE = "刚网不太好，照片没发出去。等会儿我再给你补一张。";

export interface ChatRoomViewProps {
  repo: Repo;
  settings: Settings;
  chat: Chat;
  gen: Chat;
  video?: Chat;
  embed: Embed;
  /** ComfyUI 工作流模板：聊天自拍按多元数据插入绑定的模板出图。 */
  templates: Template[];
  /** 由 ChatView 顶部「剧情 / 私聊 / 群聊」模式条控制。 */
  mode: ChatRoomMode;
  /** 角色名册（合集卡来自世界书条目；cards 模式为绑定卡，后端统一返回）。 */
  roster?: CharacterRosterResult | null;
  /** 名册覆盖保存后请求 ChatView 重新拉取。 */
  onRefreshRoster?: () => void;
  /** 当前私聊角色变化（ChatView 用它计算每角色独立线程与未读聚合）。 */
  onActiveSpeakerChange?: (speaker: string) => void;
  /** 保存仓库级名册覆盖（characterRoster）。 */
  updateRepo?: (patch: Partial<Repo>) => void;
  /** 顶部维护操作（压缩/导入/清空/恢复）完成后递增，触发快照重读。 */
  refreshToken?: number;
  /** 把聊天室忙碌状态上报给 ChatView，禁用顶部维护按钮。 */
  onBusyChange?: (busy: boolean) => void;
  /** 上下文提醒里的「压缩上下文」：由 ChatView 统一压缩当前聊天线程并重读快照。 */
  onCompactContext?: () => void | Promise<void>;
  /** ChatView 正在执行压缩（禁用提醒按钮并显示「压缩中…」）。 */
  compactBusy?: boolean;
}

function asChatMessage(item: unknown): ChatMessage | null {
  if (!item || typeof item !== "object") return null;
  const message = item as Record<string, unknown>;
  const role = message.role === "user" || message.role === "assistant" ? message.role : null;
  const text = typeof message.text === "string" ? message.text : "";
  const image = typeof message.image === "string" && message.image ? message.image : "";
  const video = typeof message.video === "string" && message.video ? message.video : "";
  const ts = typeof message.ts === "number" && Number.isFinite(message.ts) && message.ts > 0
    ? message.ts
    : 0;
  // 后端 `_persist` 写 snake_case，前端 saveSnapshot 写 camelCase；两种快照都要认，
  // 否则主动消息的 media_request 会在刷新后消失（图片/头像不补拍）。
  const mediaRequest = message.media_request ?? message.mediaRequest;
  if (!role || (!text.trim() && !image && !video)) return null;
  return {
    id: typeof message.id === "string" && message.id ? message.id : crypto.randomUUID(),
    role,
    // 动作清洗只作用于角色消息：用户自己打的括号是原话，不能删。
    text: role === "assistant" ? sanitizeChatText(text) : text,
    ...(ts ? { ts } : {}),
    ...(message.poke === true ? { poke: true } : {}),
    ...(image ? { image } : {}),
    ...(video ? { video } : {}),
    ...(mediaRequest && typeof mediaRequest === "object"
      ? { mediaRequest: mediaRequest as ChatMessage["mediaRequest"] }
      : {}),
    ...(typeof message.speaker === "string" && message.speaker ? { speaker: message.speaker } : {}),
  };
}

export function ChatRoomView({
  repo, settings, chat, gen, video, embed, templates, mode, roster, onRefreshRoster, updateRepo,
  onActiveSpeakerChange, refreshToken = 0, onBusyChange,
  onCompactContext, compactBusy = false,
}: ChatRoomViewProps) {
  const boundCardNames = useMemo(
    () => (repo.cardNames?.length ? repo.cardNames : (repo.cardName ? [repo.cardName] : [])),
    [repo.cardNames, repo.cardName],
  );
  // 合集卡：成员来自世界书名册；普通玩法仍用绑定卡（后端 roster 已统一口径，前端只做回退）。
  // 排序：只按各角色私聊线程「最后一条消息时间」倒序；打开/选中/未读都不改变顺序。
  const [localActivity, setLocalActivity] = useState<Record<string, number>>({});
  useEffect(() => { setLocalActivity({}); }, [repo.id]);
  const cardNames = useMemo(() => {
    const activity = { ...(roster?.member_activity || {}), ...localActivity };
    return sortChatRoomMembers(rosterNamesOf(roster, boundCardNames), activity);
  }, [roster, boundCardNames, localActivity]);
  // 只有真正新增消息才推进该角色的最近消息时间；快照恢复/进入私聊绝不推进。
  const bumpActivity = (speaker: string, ts?: number) => {
    const name = (speaker || "").trim();
    if (!name) return;
    const at = typeof ts === "number" && Number.isFinite(ts) && ts > 0
      ? ts
      : Date.now() / 1000;
    setLocalActivity((prev) => ((prev[name] || 0) >= at ? prev : { ...prev, [name]: at }));
  };
  const openingCardName = (isWorldbookRoster(roster) ? cardNames[0] : "")
    || repo.openingCardName || repo.cardName || cardNames[0] || "";
  const cardKey = cardNames.join("\u0000");
  const [rosterOpen, setRosterOpen] = useState(false);
  // 顶栏只留头像：优先角色卡自带 avatar.png；世界书角色没有卡目录，
  // 回退「多元数据插入」里该角色的定妆底图（characterLoras[name].baseImage）。
  const [avatars, setAvatars] = useState<Record<string, string>>({});
  const portraitBindings = settings.mediaInsert?.[repo.id]?.characterLoras || {};
  // 底图存的是本机路径，必须转 local-view URL；已是 http/data/api 的直接用。
  const imageSrc = (value: string): string => {
    const raw = (value || "").trim();
    if (!raw) return "";
    if (/^(https?:|data:|blob:|\/api\/)/i.test(raw)) return raw;
    return localViewUrl(raw);
  };
  // 世界书全名 ↔ 多元数据短名（腓特烈大帝 ↔ 腓特烈）按包含/前缀匹配，取最长公共前缀。
  const baseImageFor = (name: string): string => {
    const key = findBindingKey(name, Object.keys(portraitBindings));
    return key ? imageSrc(portraitBindings[key]?.baseImage || "") : "";
  };
  const portraitKey = cardNames
    .map((name) => imageSrc(repo.characterAvatars?.[name] || "") || baseImageFor(name))
    .join("|");
  useEffect(() => {
    let alive = true;
    if (!cardNames.length) {
      setAvatars({});
      return;
    }
    Promise.all(cardNames.map(async (name) => {
      const override = imageSrc(repo.characterAvatars?.[name] || "");
      if (override) return [name, override] as const;
      const fallback = baseImageFor(name);
      if (!settings.characterDir) return [name, fallback] as const;
      try {
        const media = await characterMedia(
          settings.characterDir, name, settings.outputDir, repo.id,
        );
        return [name, media.has_avatar
          ? avatarUrl(media.base || settings.characterDir, media.folder)
          : fallback] as const;
      } catch {
        return [name, fallback] as const;
      }
    })).then((items) => {
      if (alive) setAvatars(Object.fromEntries(items));
    }).catch(() => {
      if (alive) setAvatars({});
    });
    return () => { alive = false; };
  }, [cardKey, portraitKey, settings.characterDir, settings.outputDir, repo.id, repo.characterAvatars]); // eslint-disable-line react-hooks/exhaustive-deps
  // 私聊按角色分线程：不同角色各自一份上下文；群聊仍共用一条线程。
  const [selected, setSelected] = useState<string[]>(
    () => defaultChatRoomSelection(mode, cardNames, openingCardName),
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messagesRef = useRef<ChatMessage[]>(messages);
  messagesRef.current = messages;
  // 上下文堆叠提醒（2026-11-10）：私聊/群聊按消息估算可入模文本，变多时提示压缩。
  // 2026-11-12：服务端会自动滚动摘要，压力只按「还会原样保留的尾部」算，旧记录不再计入。
  const pressure = useMemo(
    () => chatContextPressure(
      messages,
      settings.contextMaxTokens ?? 0,
      settings.compactTriggerPct ?? 80,
    ),
    [messages, settings.contextMaxTokens, settings.compactTriggerPct],
  );
  const [dismissedPressure, setDismissedPressure] = useState("");
  const pressureKey = pressure.level === "ok"
    ? ""
    : `${pressure.level}:${Math.floor(pressure.chars / 4000)}`;
  const showPressureHint = pressureKey !== "" && pressureKey !== dismissedPressure;
  const [input, setInput] = useState("");
  // 消息级编辑（2026-11-10）：与剧情模式一致，用户/AI 消息都可编辑；编辑只改快照正文。
  const [editingId, setEditingId] = useState("");
  const [editingText, setEditingText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  // 后台追问/重开没有用户发送回合，`busy` 一直是 false；单独给一个「正在输入中」提示，
  // 否则角色隔一段时间补的话会凭空出现（2026-11-10 用户实锤）。
  const [backgroundTyping, setBackgroundTyping] = useState("");
  // 拍摄任务按线程登记在模块级订阅表里：切屏/切角色后回来仍能看到「正在拍摄中」。
  const [mediaTasks, setMediaTasks] = useState<ChatMediaTask[]>([]);
  // 媒体忙碌只锁编辑/删除等破坏性操作；聊天输入与发送保持可用（#3）。
  const mediaBusy = mediaTasks.length > 0;
  const [loaded, setLoaded] = useState(false);
  // 切走再回来时，后台生成可能已结束/仍在跑；这个 tick 用于结束后重读快照。
  const [snapshotTick, setSnapshotTick] = useState(0);
  const abortRef = useRef<(() => void) | null>(null);
  // 生成中标记的 ref 版：快照兜底轮询 / 主动扫描要在不重建 effect 的情况下读最新值。
  const busyRef = useRef(false);
  useEffect(() => { busyRef.current = busy; }, [busy]);
  // 后台主动/追问/拍摄 reaction 是否在跑：用户发送前据此取消并等待线程锁。
  const backgroundBusyRef = useRef(false);
  // 主动扫描每 2 分钟重跑：进入私聊只扫一次，会让一直停在私聊里的用户永远等不到她开口。
  const initiativeScanningRef = useRef(false);
  const lastInitiativeScanRef = useRef(0);
  const pendingMediaRef = useRef<Array<{
    kind: "image" | "video"; prompt: string; speaker: string; avatar?: boolean;
    appearance?: string;
    sourceMessageId?: string;
  }>>([]);
  const atBottomRef = useRef(true);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // 1:1 聊天走通用 Agent（answer/tool/generate），正文可能以 delta/replace 到达；
  // 这里记住本轮 botId，保证流式增量与最终落盘快照用同一条消息 id。
  const streamRef = useRef<{ id: string; created: boolean } | null>(null);
  // 群聊本轮实际走到的路由：chat=多角色轮转；其余=通用专家（正文按首位成员展示）。
  const routeRef = useRef<string>("");
  const threadIdRef = useRef("");   // 异步收尾时判断「当前是否还在原线程」

  const members = resolveChatRoomMembers(mode, cardNames, selected);
  const activeSpeaker = members[0] || openingCardName || "助手";
  // 无成员时保留旧基线程（__chat），有成员时一人一线程；thread id 以后端为准（与快照 mtime 同源）。
  const threadId = mode === "group_chat"
    ? chatRoomThreadId(repo.id, "group_chat")
    : (roster?.member_threads?.[members[0] || ""]
      || chatRoomThreadId(repo.id, "chat", members[0] || ""));
  threadIdRef.current = threadId;
  // 拍摄任务跨切屏保留：订阅模块级登记表，回到该线程时恢复「正在拍摄中」。
  useEffect(() => {
    // 切线程先清空：避免旧线程的任务在新线程的第一帧显示（2026-11-11 实锤
    // 「奥古斯特的私聊里出现埃吉尔正在拍摄中」）；随后按新线程重新同步。
    setMediaTasks([]);
    const sync = () => setMediaTasks(chatMediaTasksForThread(threadId));
    sync();
    return subscribeChatMediaTasks(sync);
  }, [threadId]);
  // 已读上报（2026-11-10）：可见且滚到底时把最后一条角色消息时间告诉后端；
  // 追问/主动开场据此区分「未读太久」与「已读不回」，由角色按性格/好感度反应。
  const readReportedRef = useRef<Record<string, number>>({});
  // 已读/未读圆点：角色消息右侧（红=未读，绿=已读）；用户消息左侧（红=发送中/失败，绿=已送达）。
  const [readUpTo, setReadUpTo] = useState(0);
  const [deliveredUserIds, setDeliveredUserIds] = useState<Set<string>>(() => new Set());
  const [failedUserIds, setFailedUserIds] = useState<Set<string>>(() => new Set());
  const mountedAtRef = useRef(Date.now() / 1000);
  // 已读上报：页面可见、且在底部时，把最后一条角色消息时间告诉后端/本地圆点。
  // 抽成回调的原因：消息到达时可能页面在后台（document.hidden）被跳过；用户回到
  // 页面/滚动到底后必须补报一次，否则明明正在看消息，时间右侧还是红点（2026-11-11 实锤）。
  const reportRead = useCallback(() => {
    if (!loaded || document.hidden || !messagesRef.current.length || !atBottomRef.current) return;
    const last = [...messagesRef.current].reverse().find((message) => (
      message.role === "assistant" && !message.system && !message.poke
      && typeof message.ts === "number" && message.ts > 0
    ));
    if (!last?.ts) return;
    const lastTs = last.ts;
    if ((readReportedRef.current[threadId] || 0) >= lastTs) return;
    readReportedRef.current[threadId] = lastTs;
    setReadUpTo((current) => Math.max(current, lastTs));
    void markChatRead(threadId, lastTs).catch(() => undefined);
  }, [loaded, threadId]);
  useEffect(() => {
    let alive = true;
    setReadUpTo(0);
    setDeliveredUserIds(new Set());
    setFailedUserIds(new Set());
    mountedAtRef.current = Date.now() / 1000;
    void fetchChatReadState(threadId)
      .then((res) => {
        // 取最大值：本地可能已先按可见消息推进，不能被较早的服务器状态打回红点。
        if (alive) setReadUpTo((current) => Math.max(current, Number(res.state?.ts) || 0));
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [threadId]);
  useEffect(() => {
    reportRead();
  }, [messages, threadId, loaded, reportRead]);
  // 快照已被清空（本机「清空」或外部维护）：同步清掉该角色线程的红点，
  // 否则空会话仍挂着旧未读数，看起来像没清干净。
  useEffect(() => {
    if (loaded && messages.length === 0) clearUnread(threadId);
  }, [loaded, messages.length, threadId]);
  // 页面重新可见 / 窗口聚焦：后台到达的消息此前被 document.hidden 挡住，回到页面补报。
  useEffect(() => {
    const onVisible = () => reportRead();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [reportRead]);
  // 换角色/换群聊线程时重置「稍后」：别让 A 的收起状态把 B 的提醒一起吞掉。
  useEffect(() => { setDismissedPressure(""); }, [threadId]);
  useEffect(() => {
    onActiveSpeakerChange?.(mode === "chat" ? (members[0] || "") : "");
  }, [mode, members[0], onActiveSpeakerChange]); // eslint-disable-line react-hooks/exhaustive-deps
  // 头像红点：每个角色看自己的私聊线程（扫描/后台消息写给未打开角色时也能亮）。
  const [memberUnread, setMemberUnread] = useState<Record<string, number>>({});
  useEffect(() => {
    const update = () => {
      const next: Record<string, number> = {};
      for (const name of cardNames) {
        const id = roster?.member_threads?.[name]
          || chatRoomThreadId(repo.id, "chat", name);
        next[name] = getUnread(id).total;
      }
      setMemberUnread(next);
    };
    update();
    return subscribeUnread(update);
  }, [threadId, cardNames.join("|"), roster, repo.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const persona = useMemo(() => {
    const bound = repo.personaId
      ? (settings.userPersonas || []).find((item) => item.id === repo.personaId)
      : undefined;
    return bound || activeUserPersona(settings);
  }, [repo.personaId, settings]);

  // 切换作品/线程：从独立聊天快照恢复。旧消息没有 speaker 时按开场卡兜底
  // （1:1 的回复由 agent_runner 补 speaker；群聊消息本身带 speaker）。
  useEffect(() => {
    let alive = true;
    // 私聊旧消息缺 speaker 时只能归给当前角色；不能用排序后的首位（可能是别人）。
    const fallbackSpeaker = mode === "chat" ? (members[0] || "") : (cardNames[0] || "");
    setLoaded(false);
    setMessages([]);
    fetchSnapshot(threadId)
      .then((snap) => {
        if (!alive) return;
        setMessages((snap.items || [])
          .map(asChatMessage)
          .filter((item): item is ChatMessage => !!item)
          .map((item) => item.role === "assistant" && !item.speaker && fallbackSpeaker
            ? { ...item, speaker: fallbackSpeaker }
            : item));
        atBottomRef.current = true;
        clearUnread(threadId);
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setLoaded(true);
      });
    return () => { alive = false; };
  }, [threadId, refreshToken, snapshotTick]); // eslint-disable-line react-hooks/exhaustive-deps

  // 生成中快照兜底（2026-11-10 用户实锤：留在生成页不切换，SSE 偶发丢帧时消息不显示）。
  // 每 2.5s 轻量重读快照并按 id 合并；最终消息一定会从服务端快照补进当前列表。
  useEffect(() => {
    if (!busy) return;
    let alive = true;
    const fallbackSpeaker = mode === "chat" ? activeSpeaker : (cardNames[0] || "");
    const timer = window.setInterval(() => {
      fetchSnapshot(threadId)
        .then((snap) => {
          if (!alive) return;
          const incoming = (snap.items || [])
            .map(asChatMessage)
            .filter((item): item is ChatMessage => !!item)
            .map((item) => item.role === "assistant" && !item.speaker && fallbackSpeaker
              ? { ...item, speaker: fallbackSpeaker }
              : item);
          if (incoming.length) {
            // 只有本地还没见过的消息才推进排序；同一批流式消息重复扫到不重复计时。
            const known = new Set(messagesRef.current.map((message) => message.id));
            for (const message of incoming) {
              if (!known.has(message.id) && message.role === "assistant") {
                bumpActivity(message.speaker || "", message.ts);
              }
            }
            setMessages((current) => mergeSnapshotMessages(current, incoming));
          }
        })
        .catch(() => { /* 后端未就绪时等下一轮 */ });
    }, 2500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [busy, threadId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 空闲态快照同步（2026-11-10 用户实锤：不刷新页面就看不到角色新内容）。
  // 主动扫描/追问漏收、或后台任务由别的入口写进快照时，每 12s 轻量合并一次；
  // 生成中已有 2.5s 的兜底轮询，这里只补空闲态，避免两套轮询同时跑。
  useEffect(() => {
    if (!loaded) return;
    let alive = true;
    const timer = window.setInterval(() => {
      if (!alive || document.hidden || busyRef.current) return;
      const fallbackSpeaker = mode === "chat" ? activeSpeaker : (cardNames[0] || "");
      fetchSnapshot(threadId)
        .then((snap) => {
          if (!alive) return;
          const incoming = (snap.items || [])
            .map(asChatMessage)
            .filter((item): item is ChatMessage => !!item)
            .map((item) => item.role === "assistant" && !item.speaker && fallbackSpeaker
              ? { ...item, speaker: fallbackSpeaker }
              : item);
          if (!incoming.length) return;
          const known = new Set(messagesRef.current.map((message) => message.id));
          const fresh = incoming.filter((message) => !known.has(message.id));
          if (!fresh.length) return;
          for (const message of fresh) {
            if (message.role === "assistant") {
              bumpActivity(message.speaker || activeSpeaker, message.ts);
            }
          }
          setMessages((current) => mergeSnapshotMessages(current, incoming));
          if (atBottomRef.current && !document.hidden) clearUnread(threadId);
        })
        .catch(() => { /* 后端未就绪时等下一轮 */ });
    }, 12_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [threadId, loaded, mode, activeSpeaker]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    // 只有用户刚发消息，或本来就在底部时才自动贴底；向上翻历史时不能被
    // 新消息/图片/快照轮询拽回最下边。
    if (!shouldStickChatToBottom({ lastRole: last?.role, atBottom: atBottomRef.current })) return;
    el.scrollTop = el.scrollHeight;
    // 自动贴底等同用户看到最新消息：同步清掉已读红点（程序化滚动不保证触发 onScroll）。
    atBottomRef.current = true;
    reportRead();
  }, [messages, busy, status, reportRead]);

  useEffect(() => {
    // 媒体拍摄也算「后台忙碌」：顶部维护按钮要禁用，但聊天输入不能锁。
    onBusyChange?.(busy || mediaBusy);
  }, [busy, mediaBusy, onBusyChange]);

  // 切回剧情/切换私聊↔群聊/关闭页面时只断开前端 SSE；后端仍会跑完并写进对应独立快照，
  // 下次切回同一模式可直接恢复（不误杀用户的后台生成，也不把旧流写到新线程）。
  useEffect(() => () => { abortRef.current?.(); }, [threadId]);
  // 切角色时允许立即离开：前端断流、后端继续写到旧线程；新角色回到空闲可发言。
  useEffect(() => {
    abortRef.current = null;
    streamRef.current = null;
    setBusy(false);
    setStatus("");
    setBackgroundTyping("");
    setEditingId("");
    setEditingText("");
  }, [threadId]);
  // 后台运行归属：当前可见线程告诉后台活动轮询，切走/卸载后它跑完才补未读红点。
  useEffect(() => {
    setActiveChatThread(threadId);
    return () => setActiveChatThread("");
  }, [threadId]);
  // 切走再回来：该线程可能仍由 agent_runner 在后台跑。恢复「生成中」并在结束后重读快照。
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let sawRunning = false;
    const probe = async () => {
      try {
        const { running } = await fetchAgentRunning(threadId);
        if (!alive) return;
        if (running) {
          sawRunning = true;
          setBusy(true);
          setStatus(`${activeSpeaker} 正在输入中…`);
          timer = setTimeout(() => { void probe(); }, 1500);
          return;
        }
        if (sawRunning) {
          // 后台跑完：清掉生成中，重读快照拿到正式结果。
          setBusy(false);
          setStatus("");
          setSnapshotTick((value) => value + 1);
        }
      } catch {
        // 后端未起/网络失败：不把「生成中」永久挂住；下一次切回会再探测。
        if (alive && sawRunning) {
          setBusy(false);
          setStatus("");
        }
      }
    };
    void probe();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [threadId]);
  // 每次切线程允许一次「角色主动找你」判定；后端还有闲置/冷却闸，不会频繁打扰。
  // （2026-11-10：改为由主动扫描 effect 的 2 分钟定时器负责重扫，不再依赖刷新。）

  // 模式由 ChatView 顶部模式条控制：私聊收敛到一位，群聊恢复绑定卡全集。
  useEffect(() => {
    if (mode === "group_chat") {
      const all = defaultChatRoomSelection("group_chat", cardNames, openingCardName);
      setSelected(all.length > 0 ? all : []);
      return;
    }
    setSelected((current) => {
      const resolved = resolveChatRoomMembers("chat", cardNames, current);
      return resolved.length > 0 ? [resolved[0]] : current;
    });
  }, [mode, cardNames, openingCardName]);

  const toggleMember = (name: string) => {
    if (mode === "chat") {
      clearSpeakerUnread(threadId, name);
      setSelected([name]);
      return;
    }
    setSelected((current) => {
      const next = current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name];
      return next.length > 0 ? next : current;
    });
  };

  const buildInvocation = (
    text: string, history: AgentInvocation["history"], userMessageId: string, botId: string,
  ): AgentInvocation => ({
    threadId,
    message: text,
    images: [],
    interactionMode: mode,
    chatMembers: members,
    workMode: "story",
    chat,
    gen,
    video,
    embed,
    providerProfile: chat.providerProfile || "openai_compatible",
    size: "1024x1024",
    imageQuality: "high",
    outputDir: settings.outputDir,
    repoId: repo.id,
    proxyUrl: effectiveGlobalProxyUrl(settings),
    chatProxyUrl: chat.proxyUrl || "",
    genProxyUrl: "",
    videoProxyUrl: "",
    embedProxyUrl: resolveEndpointProxy(
      embed.baseUrl, settings.embedModel.proxyMode, globalProxyAddress(settings), settings.proxyEnabled,
    ),
    styleTemplate: promptTemplateInstructions(settings.promptTemplateId || "", []),
    agentId: settings.activeAgentId || "",
    streamOutput: settings.streamOutput,
    // 群聊 chat 路由不产最终正文（逐条 chat_messages 自带 id），此 id 不会被使用；
    // 群聊落 answer/generate 等专家时则用它把该条回复写进独立快照。
    messageId: botId,
    contextMaxTokens: settings.contextMaxTokens,
    compactTriggerPct: settings.compactTriggerPct,
    selfhealAttempts: settings.selfhealAttempts,
    chatGuardMaxChars: settings.chatGuardMaxChars,
    chatDefaultMaxTokens: settings.chatDefaultMaxTokens,
    chatFollowupMax: settings.chatFollowupMax,
    chatReopenMax: settings.chatReopenMax,
    chatInitiativeIdleMinutes: settings.chatInitiativeIdleMinutes,
    chatInitiativeCooldownMinutes: settings.chatInitiativeCooldownMinutes,
    chatInitiativeDailyMax: settings.chatInitiativeDailyMax,
    chatCadence: settings.chatCadence,
    chatActiveWindowMinutes: settings.chatActiveWindowMinutes,
    chatMaxBotTurns: settings.chatMaxBotTurns,
    chatMinAffinity: settings.chatMinAffinity,
    chatQuietStart: settings.chatQuietStart,
    chatQuietEnd: settings.chatQuietEnd,
    chatGroupActivity: settings.chatGroupActivity,
    presetSampling: settings.presetSampling,
    history,
    characterDir: settings.characterDir,
    // 绑定卡字段必须保持「作品绑定的角色卡」，不能换成世界书名册成员：
    // 后端用 card_names 读卡/世界书归属，成员身份只走 chat_members。
    cardName: repo.openingCardName || repo.cardName || boundCardNames[0] || "",
    cardNames: boundCardNames,
    openingCardName: repo.openingCardName || repo.cardName || boundCardNames[0] || "",
    presetDir: settings.presetDir,
    presetName: repo.presetName || settings.activePresetName,
    userName: persona.name,
    userPersona: persona.content,
    personaBound: !!repo.personaId,
    worldbookDir: settings.worldbookDir,
    worldbookName: repo.worldbookName || "",
    illustrate: false,
    // 聊天室多元数据：开启后把「多元数据插入」预设的图片/视频能力透传给聊天请求；
    // 真正的自发媒体由 chat_room 的 marker + 前端二次生成完成，不在这里直接出图。
    comfyIllustrate: settings.chatRoomMediaInsert === true
      && Boolean(settings.mediaInsert?.[repo.id]?.templateId),
    comfyAudio: false,
    comfyVideo: settings.chatRoomMediaInsert === true
      && Boolean(settings.mediaInsert?.[repo.id]?.videoTemplateId),
    promptProfile: "anima_tags",
    appearanceSource: "worldbook",
    characterBaseImages: {},
    illustrationActorNames: [],
    styleBaseImage: "",
    userMessageId,
  });

  // 聊天室自发媒体（2026-11-09）：角色在回复里请求 [SELFIE]/[VIDEO] 时，走现有
  // generate/video 通道生成，生成完作为该角色的媒体气泡插回聊天；资产库由生成链落盘。
  // 2026-11-10：不能用聊天回合的 `busy` 当闸——媒体请求是在 onDone 里发起的，
  // 那一刻闭包里的 busy 仍是 true，会把所有拍摄静默吞掉（用户实锤「始终没有拍摄中」）。
  // 2026-11-10 二次修正：也不能用全局单闸——两个角色同时发照片时，后一个会被静默丢弃
  // （用户实锤：腓特烈拍成功、奥古斯特没有）。不同角色允许并行（ComfyUI 自带队列），
  // 同一角色已有拍摄时忽略重复请求即可。
  const generateChatMedia = async (request: {
    kind: "image" | "video"; prompt: string; speaker: string; avatar?: boolean;
    appearance?: string;
    sourceMessageId?: string;
    /** 刷新后从已提交的 ComfyUI 任务续接，不重新提交。 */
    resumePromptId?: string;
  }) => {
    if (settings.chatRoomMediaInsert !== true) return;
    // 私聊只拍当前角色：切角色前未收尾的媒体请求绝不能登记到新线程。
    if (mode !== "group_chat" && request.speaker !== activeSpeaker) return;
    if (request.sourceMessageId) {
      markHandledChatMedia(threadId, request.sourceMessageId);
      // 快照里也写 handled：换设备/清缓存后不会把旧请求再拍一遍。
      setMessages((current) => current.map((message) => (
        message.id === request.sourceMessageId
          ? {
              ...message,
              mediaRequest: {
                kind: request.kind,
                prompt: request.prompt,
                ...(request.avatar ? { avatar: true } : {}),
                ...(request.appearance ? { appearance: request.appearance } : {}),
                handled: true,
              },
            }
          : message
      )));
    }
    // 「聊天头像自动更新」只决定是否额外生成 1:1 头像，不拦聊天图本身；
    // 否则关掉头像开关时，婚纱 [AVATAR] 会被整条丢弃，只剩一句「看好了」。
    if (chatMediaTasksForThread(threadId).some(
      (task) => task.speaker === request.speaker,
    )) return;
    const mediaTaskThread = threadId;
    let cancelled = false;
    let cancelRunning: (() => void) | null = null;
    let submittedPromptId = request.resumePromptId || "";
    const mediaTaskId = startChatMediaTask({
      threadId: mediaTaskThread, speaker: request.speaker, kind: request.kind,
      cancel: () => {
        cancelled = true;
        clearChatMediaResume(mediaTaskThread, request.speaker);
        // 已提交到 ComfyUI 的任务尽量一起中断，别只停前端等待。
        if (submittedPromptId) {
          void interruptComfy(settings.comfyuiUrl, submittedPromptId).catch(() => undefined);
        }
        cancelRunning?.();
      },
    });
    const preset = settings.mediaInsert?.[repo.id];
    const mediaThread = threadId;
    // ComfyUI 媒体预设存在时，先把它拉起来再拍：离线阶段任务留在这条等待队列里，
    // 不立刻判死；切到别的角色也不会丢掉这次拍摄，后端仍写回原线程。
    if (preset?.templateId) {
      if (threadIdRef.current === mediaThread) {
        setStatus(`${request.speaker} 正在拍摄中…（等待 ComfyUI）`);
      }
      const ready = await ensureComfyReady(settings.comfyuiUrl, {
        timeoutMs: 10 * 60 * 1000,
      });
      if (cancelled) {
        finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId);
        return;
      }
      if (!ready) {
        finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId);
        if (threadIdRef.current === mediaThread) {
          setStatus("");
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", system: true,
            text: "ComfyUI 启动超时，本次拍摄未提交；启动后可重新触发。",
          }]);
        }
        return;
      }
    }
    // 2026-11-10 用户口径：聊天自拍必须走「多元数据插入」绑定的 ComfyUI 模板，
    // 不能落到生图 API（那是另一套密钥/模型，剧情插画也不走它）。
    // 视频仍保留原有 generate/video 通道；图片有模板时优先 ComfyUI。
    const comfyTemplate = preset?.templateId
      ? templates.find((item) => item.id === preset.templateId)
      : undefined;
    if (request.kind === "image" && preset?.templateId) {
      if (!comfyTemplate) {
        finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId);
        if (!cancelled && threadIdRef.current === mediaThread) {
          setStatus("");
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", system: true,
            text: "拍摄失败：多元数据插入绑定的工作流模板不存在，请重新选择模板。",
          }]);
        }
        return;
      }
      if (threadIdRef.current === mediaThread) {
        setStatus(`${request.speaker} 正在拍摄中…`);
      }
      try {
        const actorKey = findBindingKey(
          request.speaker, Object.keys(preset.characterLoras || {}),
        ) || request.speaker;
        const out = request.resumePromptId
          ? await resumeChatMediaImage({
              promptId: request.resumePromptId,
              prompt: request.prompt,
              repoId: repo.id,
              outputDir: settings.outputDir || "",
              comfyUrl: settings.comfyuiUrl,
            })
          : await generateChatMediaImageWithRetry({
          prompt: request.prompt,
          speaker: request.speaker,
          actorKey,
          appearance: request.appearance,
          presetDir: settings.presetDir,
          presetName: settings.activePresetName,
          userName: activeUserPersona(settings).name,
          repoId: repo.id,
          outputDir: settings.outputDir || "",
          comfyUrl: settings.comfyuiUrl,
          chat,
          preset,
          template: comfyTemplate,
          onSubmitted: (promptId) => {
            submittedPromptId = promptId;
            saveChatMediaResume({
              threadId: mediaTaskThread,
              speaker: request.speaker,
              promptId,
              prompt: request.prompt,
              avatar: request.avatar,
              appearance: request.appearance,
              sourceMessageId: request.sourceMessageId,
              createdAt: Date.now(),
            });
          },
        }, {
          attempts: 2,
          onRetry: () => {
            // 第一次没拍成：不立刻抢显卡，先切到「等空闲」，让用户还能继续聊。
            if (cancelled) return;
            setChatMediaTaskPhase(
              mediaTaskThread, request.speaker, "waiting", mediaTaskId,
            );
            if (threadIdRef.current === mediaThread) {
              setStatus(`${request.speaker} 第一次没拍成，正在等显卡空闲重拍…`);
            }
          },
          beforeRetry: async () => {
            if (cancelled) throw new Error("拍摄已取消");
            const idle = await waitForComfyIdle(settings.comfyuiUrl, {
              timeoutMs: 10 * 60 * 1000,
              shouldStop: () => cancelled,
              onWait: () => {
                if (!cancelled && threadIdRef.current === mediaThread) {
                  setStatus(`${request.speaker} 正在等显卡空闲，稍后补拍…`);
                }
              },
            });
            if (cancelled) throw new Error("拍摄已取消");
            if (!idle) throw new Error("等待显卡空闲超时");
            // 后台重拍不再占用聊天输入；媒体任务表负责继续显示「正在拍摄中」。
            setChatMediaTaskPhase(
              mediaTaskThread, request.speaker, "shooting", mediaTaskId,
            );
            if (threadIdRef.current === mediaThread) {
              setStatus(`${request.speaker} 正在拍摄中…`);
            }
          },
        });
        if (cancelled) return;
        if (threadIdRef.current !== mediaThread) return;
        // 源消息在拍摄期间被删除/重新生成：迟到的图不要再插回聊天。
        if (request.sourceMessageId
          && !messagesRef.current.some((message) => message.id === request.sourceMessageId)) {
          return;
        }
        clearChatMediaResume(mediaTaskThread, request.speaker);
        bumpActivity(request.speaker);
        const imageMessage: ChatMessage = {
          id: crypto.randomUUID(), role: "assistant", speaker: request.speaker,
          text: "", ts: Date.now() / 1000, image: out.url,
        };
        setMessages((current) => current.some((message) => message.id === imageMessage.id)
          ? current
          : [...current, imageMessage]);
        // 刷新后仍要能看到这张照片：服务端快照只存了 media_request，图片气泡并入快照。
        // 失败只影响刷新后回显，不影响本次已经显示在聊天里的图。
        void saveSnapshot(threadId, [...messagesRef.current, imageMessage]).catch(() => undefined);
        // 图1 已成功：先结束「拍摄中」，让角色对照片补话。头像单独在后台跑：
        // 成功只更新头像槽，失败只留一句「头像没换好」，不拖住整轮 reaction。
        finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId);
        if (threadIdRef.current === mediaThread) {
          setStatus("");
        }
        if (shouldUpdateChatAvatar(request, settings.chatRoomAvatarUpdate)) {
          void (async () => {
            try {
              const avatarOut = await generateChatMediaImage({
                prompt: `${request.prompt}，头像特写，正方形构图`,
                speaker: request.speaker,
                actorKey,
                appearance: request.appearance,
                presetDir: settings.presetDir,
                presetName: settings.activePresetName,
                userName: activeUserPersona(settings).name,
                repoId: repo.id,
                outputDir: settings.outputDir || "",
                comfyUrl: settings.comfyuiUrl,
                chat,
                preset,
                template: comfyTemplate,
                aspectRatio: "1:1",
                // 头像优先级低于聊天图：5 分钟没出就中断，避免卡住后续拍摄。
                pollTimeoutMs: 5 * 60 * 1000,
              });
              updateRepo?.({
                characterAvatars: {
                  ...(repo.characterAvatars || {}),
                  [request.speaker]: avatarOut.url,
                },
              });
            } catch (avatarErr) {
              const detail = avatarErr instanceof Error ? avatarErr.message : String(avatarErr);
              // 技术原因只进控制台/日志，聊天主区只留一句可读的短提示。
              console.warn("[chat-media] avatar failed", detail);
              if (threadIdRef.current === mediaThread) {
                setMessages((current) => [...current, {
                  id: crypto.randomUUID(), role: "assistant", system: true,
                  text: "头像没换好。",
                }]);
              }
            }
          })();
        }
        // 用户已经在聊下一轮：照片已上屏，跳过 reaction，避免和聊天回合抢线程锁。
        if (!busyRef.current) {
          backgroundBusyRef.current = true;
          try {
            const reactionInvocation = buildInvocation(
              request.prompt, buildChatRoomHistory(messagesRef.current), "", crypto.randomUUID(),
            );
            reactionInvocation.chatMembers = [request.speaker];
            // 角色只对已经成功的照片补话；头像在后台更新，不参与本轮 reaction。
            reactionInvocation.reactionKind = "image";
            reactionInvocation.reactionPrompt = request.prompt;
            if (threadIdRef.current === mediaThread) {
              setBackgroundTyping(`${request.speaker} 正在输入中…`);
            }
            const reaction = await chatReaction(reactionInvocation);
            if (reaction.messages?.length && threadIdRef.current === mediaThread) {
              const incoming = chatRoomMessagesToChat(
                reaction.messages as unknown as ChatRoomMessage[],
              );
              setMessages((current) => mergeChatRoomMessages(current, incoming));
            }
          } catch { /* 反应失败只少一句，不影响照片 */ }
          finally {
            backgroundBusyRef.current = false;
            if (threadIdRef.current === mediaThread) setBackgroundTyping("");
          }
        }
      } catch (err) {
        if (cancelled) return;
        const detail = err instanceof Error ? err.message : String(err);
        // 任务还在 ComfyUI 里跑就保留续接记录；其他失败清掉，避免刷新后重试一个死任务。
        if (!/仍在 ComfyUI 里运行/.test(detail)) {
          clearChatMediaResume(mediaTaskThread, request.speaker);
        }
        // 技术原因只进控制台/日志；角色用「网不好」把没发出去的图带过去。
        console.warn("[chat-media] image failed", detail);
        if (threadIdRef.current === mediaThread) {
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", speaker: request.speaker,
            text: CHAT_MEDIA_FAILED_EXCUSE,
          }]);
        }
      } finally {
        finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId);
        if (threadIdRef.current === mediaThread) {
          setStatus("");
        }
      }
      return;
    }
    const mediaId = crypto.randomUUID();
    const invocation = buildInvocation(request.prompt, [], "", mediaId);
    invocation.message = `【${request.speaker}的自拍】${request.prompt}`;
    invocation.routeAction = {
      route: request.kind === "video" ? "video" : "generate", userMessageId: "",
    };
    invocation.illustrate = false;
    invocation.comfyIllustrate = false;
    invocation.comfyVideo = false;
    invocation.comfyAudio = false;
    invocation.illustrationActorNames = [request.speaker];
    invocation.appearanceSource = preset?.appearanceSource || "worldbook";
    invocation.styleBaseImage = preset?.styleBaseImage || "";
    invocation.promptProfile = preset?.promptProfile || "anima_tags";
    invocation.promptProfileKind = preset?.promptProfileKind || "";
    if (preset?.characterLoras) {
      const baseImages = Object.fromEntries(
        Object.entries(preset.characterLoras)
          .map(([name, binding]) => [name, binding.baseImage || ""] as const)
          .filter(([, url]) => Boolean(url)),
      );
      // 世界书全名（腓特烈大帝）与用户配置短名（腓特烈）不同：给实际发言人补一份
      // 精确 key，后端按 actor 名取底图/一致性时才不会漏。
      const speakerBase = baseImageFor(request.speaker);
      invocation.characterBaseImages = speakerBase
        ? { ...baseImages, [request.speaker]: speakerBase }
        : baseImages;
    }
    if (cancelled) {
      finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId);
      return;
    }
    if (threadIdRef.current === mediaThread) {
      setStatus(request.kind === "video"
        ? `${request.speaker} 正在拍摄视频…`
        : `${request.speaker} 正在拍摄中…`);
    }
    let produced = false;
    let failure = "";
    cancelRunning = multiAgent(invocation, {
      onEvent: (event) => {
        if (cancelled) return;
        if (event.type === "image" || event.type === "video") {
          if (threadIdRef.current !== mediaThread) return;
          produced = true;
          bumpActivity(request.speaker);
          const url = event.url;
          const kind = event.type;
          const id = event.id || crypto.randomUUID();
          // 头像更新：只有 [AVATAR]（有纪念意义的非常规装扮自拍）生成成功才替换聊天头像；
          // 普通 [SELFIE] 只插一张图，不会把有纪念意义的头像顶掉（所有作品统一）。
          if (kind === "image" && shouldUpdateChatAvatar(request, settings.chatRoomAvatarUpdate)) {
            updateRepo?.({
              characterAvatars: {
                ...(repo.characterAvatars || {}),
                [request.speaker]: url,
              },
            });
          }
          setMessages((current) => current.some((message) => message.id === id)
            ? current
            : [...current, {
                id, role: "assistant" as const, speaker: request.speaker,
                text: "", ts: Date.now() / 1000, [kind]: url,
              } as ChatMessage]);
        } else if (event.type === "error") {
          failure = String(event.message || "");
        } else if (event.type === "replace" && !produced) {
          // generate 路由失败时正文就是错误说明（如生图接口 401）：
          // 不记下来会被当成「拍摄没有返回内容」，用户看不到真实原因。
          failure = String(event.text || "");
        }
      },
      onDone: (err) => {
        finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId);
        clearChatMediaResume(mediaTaskThread, request.speaker);
        if (cancelled || threadIdRef.current !== mediaThread) return;
        if (!produced) {
          const detail = (err || failure || "").trim().slice(0, 260);
          // 技术原因只进日志；角色用「网不好」把没发出去的图带过去。
          if (detail) console.warn("[chat-media] image failed", detail);
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", speaker: request.speaker,
            text: CHAT_MEDIA_FAILED_EXCUSE,
          }]);
        }
        setStatus("");
      },
    });
  };

  // 角色主动找上门（2026-11-09/11-10）：进入私聊立即扫描一次，之后每 2 分钟重扫。
  // 只在进入私聊时扫一次不够——用户一直停在私聊里、剧情/表格已经推进时，不刷新页面
  // 就永远等不到她主动开口（2026-11-10 用户实锤「一定要刷新前端」）。后端四闸仍生效，
  // 重复扫描会被 decide 静默跳过，不会刷屏。
  // 后台主动/追问与用户发送共用同一条 thread 锁：后台任务在跑时用户点发送会撞 409。
  // 用户消息优先——发送前先取消后台任务并等准入释放，UI 也给出「正在结束后台消息…」。
  useEffect(() => {
    const scanNames = (roster?.members?.length ? roster.members : cardNames);
    if (settings.chatRoomAutoChat === false || !roster
      || mode !== "chat" || !loaded || !scanNames.length) {
      return;
    }
    let alive = true;
    const scan = () => {
      if (!alive || initiativeScanningRef.current || backgroundBusyRef.current
        || busyRef.current || document.hidden) return;
      // roster/消息变化会重建 effect；60s 内的重复触发直接跳过，避免扫描风暴。
      const now = Date.now();
      if (now - lastInitiativeScanRef.current < 60_000) return;
      lastInitiativeScanRef.current = now;
      const invocation = buildInvocation(
        "", buildChatRoomHistory(messagesRef.current), "", crypto.randomUUID(),
      );
      // 扫描整份名册：chat_members 不能只传当前角色。
      invocation.chatMembers = scanNames;
      initiativeScanningRef.current = true;
      backgroundBusyRef.current = true;
      chatInitiativeScan(invocation)
        .then((res) => {
          if (!alive) return;
          const currentSpeaker = members[0] || "";
          let touched = false;
          for (const item of res.results || []) {
            const incoming = chatRoomMessagesToChat(
              (item.messages || []) as ChatRoomMessage[],
            );
            if (!incoming.length) continue;
            touched = true;
            // 主动消息也是「该线程的新消息」：当前角色与其他角色都按时间推进排序。
            for (const message of incoming) {
              bumpActivity(message.speaker || item.speaker, message.ts);
            }
            if (item.speaker === currentSpeaker) {
              setMessages((current) => mergeChatRoomMessages(current, incoming));
              if (atBottomRef.current && !document.hidden) clearUnread(threadId);
              for (const message of incoming) {
                if (message.mediaRequest) {
                  pendingMediaRef.current.push({
                    ...message.mediaRequest, speaker: message.speaker || currentSpeaker,
                    sourceMessageId: message.id,
                  });
                }
              }
              const pendingMedia = pendingMediaRef.current;
              pendingMediaRef.current = [];
              if (settings.chatRoomMediaInsert === true) {
                // 同一批可能有多条 media_request（群聊不同角色/连续多条）：
                // 全部发起，同角色重复交给 generateChatMedia 的任务闸挡掉。
                for (const media of pendingMedia) generateChatMedia(media);
              }
            } else {
              // 其他角色的私聊线程：不打扰当前界面，只记未读；头像会因快照 mtime 跳左。
              for (let index = 0; index < incoming.length; index += 1) {
                addUnread(item.thread_id, item.speaker, 1);
              }
            }
          }
          if (touched) onRefreshRoster?.();
        })
        .catch(() => { /* 主动扫描失败静默，不打扰用户 */ })
        .finally(() => {
          initiativeScanningRef.current = false;
          backgroundBusyRef.current = false;
        });
    };
    scan();
    const timer = window.setInterval(scan, 120_000);
    // 切走再回来/窗口重新聚焦时补扫一次：剧情可能刚在这段时间推进了。
    const onVisible = () => { if (!document.hidden) scan(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [mode, loaded, roster, threadId, members[0], cardNames.join("|"), settings.chatRoomAutoChat, settings.chatRoomMediaInsert]); // eslint-disable-line react-hooks/exhaustive-deps

  // 长时间不回复（2026-11-10）：私聊打开期间按 20s 轮询一次，后端按 starlit 同款节奏
  // 决定是否追问/重开（60–180s 首次、2–5min/10–40min 退避、15min 会话窗口、
  // 20–90min 重开、最多 3 次追问 + 2 次重开、深夜最多 1 条）。不做全局并发上限。
  const followupBusyRef = useRef(false);
  useEffect(() => {
    if (mode !== "chat" || !loaded || settings.chatRoomAutoChat === false) return;
    let alive = true;
    const tick = () => {
      if (!alive || followupBusyRef.current || busyRef.current || document.hidden) return;
      // 正在拍照片/视频时不追问：否则会出现「看好了」重复、甚至追问再触发一次拍摄。
      if (chatMediaTasksForThread(threadId).length > 0) return;
      const last = messagesRef.current[messagesRef.current.length - 1];
      if (!last || last.role !== "assistant" || last.system) return;
      const speaker = members[0] || "";
      if (!speaker) return;
      followupBusyRef.current = true;
      backgroundBusyRef.current = true;
      // 后端零 LLM 预检通常 1 秒内返回；只有真正开始生成时才亮「正在输入中」，
      // 避免每 20s 轮询都闪一下提示。
      const typingTimer = window.setTimeout(() => {
        if (alive) setBackgroundTyping(`${speaker} 正在输入中…`);
      }, 700);
      const invocation = buildInvocation(
        "", buildChatRoomHistory(messagesRef.current), "", crypto.randomUUID(),
      );
      invocation.chatMembers = [speaker];
      chatFollowup(invocation)
        .then((res) => {
          if (!alive || !res.messages?.length) return;
          const incoming = chatRoomMessagesToChat(res.messages as ChatRoomMessage[]);
          if (!incoming.length) return;
          for (const message of incoming) {
            bumpActivity(message.speaker || speaker, message.ts);
          }
          setMessages((current) => mergeChatRoomMessages(current, incoming));
          if (atBottomRef.current && !document.hidden) clearUnread(threadId);
          onRefreshRoster?.();
          // 追问/重开里角色自己发的照片也要拍：此前只合并了文字，media_request 被丢掉。
          const pendingMedia = incoming
            .filter((message) => Boolean(message.mediaRequest))
            .map((message) => ({
              ...message.mediaRequest!, speaker: message.speaker || speaker,
              sourceMessageId: message.id,
            }))[0];
          if (pendingMedia && settings.chatRoomMediaInsert === true) {
            generateChatMedia(pendingMedia);
          }
        })
        .catch(() => { /* 轮询失败静默，等下一轮 */ })
        .finally(() => {
          window.clearTimeout(typingTimer);
          setBackgroundTyping("");
          followupBusyRef.current = false;
          backgroundBusyRef.current = false;
        });
    };
    const timer = window.setInterval(tick, 20000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [mode, loaded, threadId, members[0], settings.chatRoomAutoChat]); // eslint-disable-line react-hooks/exhaustive-deps

  // 打开聊天时补拍快照里的自发媒体（2026-11-11）：主动/追问消息由后端落盘，
  // 用户当时不在这个私聊页，media_request 没有被实时消费；快照恢复时补一次。
  // 只补最近一条，其余标记为已处理——打开聊天不批量烧图，也不会每次刷新重拍。
  const generateChatMediaRef = useRef(generateChatMedia);
  generateChatMediaRef.current = generateChatMedia;
  useEffect(() => {
    if (!loaded || settings.chatRoomMediaInsert !== true) return;
    const handled = loadHandledChatMedia(threadId);
    const pending = messages.filter((message) => (
      message.role === "assistant" && !message.system
      // 切角色瞬间 messages 可能还是上一位角色的；绝不能把她的拍摄登记到当前线程。
      && (mode === "group_chat" || message.speaker === activeSpeaker)
      && message.mediaRequest && !message.mediaRequest.handled && !handled.has(message.id)
    ));
    if (!pending.length) return;
    const latest = pending[pending.length - 1];
    for (const message of pending) markHandledChatMedia(threadId, message.id);
    void generateChatMediaRef.current({
      ...latest.mediaRequest!,
      speaker: latest.speaker || activeSpeaker,
      sourceMessageId: latest.id,
    });
  }, [loaded, threadId, messages, mode, settings.chatRoomMediaInsert, activeSpeaker]);

  // 刷新后从已提交的 ComfyUI 任务续接（私聊）：只轮询/落盘，不重新提交。
  useEffect(() => {
    if (!loaded || mode !== "chat" || settings.chatRoomMediaInsert !== true) return;
    const speaker = activeSpeaker;
    if (!speaker) return;
    const record = loadChatMediaResume(threadId, speaker);
    if (!record) return;
    void generateChatMediaRef.current({
      kind: "image",
      prompt: record.prompt,
      speaker: record.speaker || speaker,
      avatar: record.avatar,
      appearance: record.appearance,
      sourceMessageId: record.sourceMessageId,
      resumePromptId: record.promptId,
    });
  }, [loaded, threadId, mode, activeSpeaker, settings.chatRoomMediaInsert]);

  const runChatTurn = (
    text: string, history: ReturnType<typeof buildChatRoomHistory>, userMessageId: string,
  ) => {
    const botId = crypto.randomUUID();
    clearUnread(threadId);
    // 这条线程刚收到用户消息：它现在是「最近有消息」的线程，按消息时间前置。
    bumpActivity(activeSpeaker);
    markChatRunStarted(threadId);
    setBusy(true);
    setStatus(mode === "group_chat" ? "群聊成员正在输入中…" : `${activeSpeaker} 正在输入中…`);
    streamRef.current = { id: botId, created: false };
    routeRef.current = "";
    pendingMediaRef.current = [];
    let produced = false;

    // 1:1 聊天：answer/tool/generate 的正文（delta/replace）都落到同一个 botId，
    // 与后端 `messageId` 一致，刷新后快照能对上。
    const applyStreamText = (delta: string, replace: boolean) => {
      if (!delta) return;
      produced = true;
      const stream = streamRef.current;
      if (!stream) return;
      if (!stream.created) {
        const initial = sanitizeChatText(delta);
        if (!initial) return;
        stream.created = true;
        bumpActivity(activeSpeaker);
        setMessages((current) => [...current, {
          id: stream.id, role: "assistant" as const, speaker: activeSpeaker, text: initial,
          ts: Date.now() / 1000,
        }]);
        return;
      }
      setMessages((current) => current.map((message) => message.id === stream.id
        ? {
            ...message,
            // 展示层兜底：流式期间后端还没拆分媒体标记，不能把 [SELFIE: …] 露给用户；
            // 多余的连续空行也在这里压掉。
            text: sanitizeChatText(replace ? delta : message.text + delta),
          }
        : message));
    };

    const appendMedia = (kind: "image" | "video", url: string, id?: string) => {
      produced = true;
      bumpActivity(activeSpeaker);
      const mediaId = id || crypto.randomUUID();
      setMessages((current) => current.some((message) => message.id === mediaId)
        ? current
        : [...current, {
            id: mediaId, role: "assistant" as const, speaker: activeSpeaker, text: "",
            ts: Date.now() / 1000, [kind]: url,
          } as ChatMessage]);
    };

    setDeliveredUserIds((current) => (
      current.has(userMessageId) ? current : new Set(current).add(userMessageId)
    ));
    const abort = multiAgent(buildInvocation(text, history, userMessageId, botId), {
      onEvent: (event) => {
        if (event.type === "route") {
          routeRef.current = event.route;
        } else if (event.type === "chat_messages") {
          const allIncoming = chatRoomMessagesToChat(event.messages as ChatRoomMessage[]);
          // 私聊只接当前角色的消息：其他角色的事件可能来自切角色前未收尾的流，
          // 绝不能并进当前聊天（否则腓特烈的图会出现在埃吉尔/奥古斯特的私聊里）。
          const incoming = mode === "group_chat"
            ? allIncoming
            : allIncoming.filter((message) => (
              !message.speaker || message.speaker === activeSpeaker
            ));
          if (!incoming.length) {
            for (const message of allIncoming) {
              if (message.speaker && message.speaker !== activeSpeaker) {
                const privateId = roster?.member_threads?.[message.speaker]
                  || chatRoomThreadId(repo.id, "chat", message.speaker);
                addUnread(privateId, message.speaker, 1);
              }
            }
            return;
          }
          produced = true;
          for (const message of incoming) {
            bumpActivity(message.speaker || activeSpeaker, message.ts);
          }
          setMessages((current) => mergeChatRoomMessages(current, incoming));
          if (atBottomRef.current && !document.hidden) {
            clearUnread(threadId);
          } else {
            for (const message of incoming) {
              addUnread(threadId, message.speaker || activeSpeaker, 1);
              // 群聊里没看到的话也亮对应角色的私聊头像红点（用户口径：群聊未读 →
              // 私聊按钮 + 该角色头像都提示）；打开该角色私聊即清掉。
              if (mode === "group_chat" && message.speaker) {
                const privateId = roster?.member_threads?.[message.speaker]
                  || chatRoomThreadId(repo.id, "chat", message.speaker);
                addUnread(privateId, message.speaker, 1);
              }
            }
          }
          for (const message of incoming) {
            if (message.mediaRequest) {
              pendingMediaRef.current.push({
                ...message.mediaRequest, speaker: message.speaker || activeSpeaker,
                sourceMessageId: message.id,
              });
            }
          }
        } else if (event.type === "media_request") {
          // 1:1 answer 路由的自发媒体：聊天流结束后再走 generate/video，避免并发流。
          pendingMediaRef.current.push({
            kind: event.kind, prompt: event.prompt, speaker: activeSpeaker,
            ...(event.avatar ? { avatar: true } : {}),
            ...(event.appearance ? { appearance: event.appearance } : {}),
            ...(streamRef.current?.id ? { sourceMessageId: streamRef.current.id } : {}),
          });
        } else if (event.type === "delta") {
          if (mode === "chat" || routeRef.current !== "chat") applyStreamText(event.text, false);
        } else if (event.type === "replace") {
          if (!event.text.trim()) return;
          if (mode === "chat" || routeRef.current !== "chat") {
            applyStreamText(event.text, true);
          } else {
            // 群聊节点的失败说明不伪装成角色台词。
            produced = true;
            setMessages((current) => [...current, {
              id: crypto.randomUUID(), role: "assistant", text: event.text, system: true,
            }]);
          }
        } else if (event.type === "image") {
          appendMedia("image", event.url, event.id);
        } else if (event.type === "video") {
          appendMedia("video", event.url, event.id);
        } else if (event.type === "inspiration") {
          produced = true;
          bumpActivity(activeSpeaker);
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", speaker: activeSpeaker,
            text: `灵感卡「${event.card.title}」：${event.card.content}`,
            ts: Date.now() / 1000,
          }]);
        } else if (event.type === "route_choice") {
          produced = true;
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", system: true,
            text: "主管无法确定这次该怎么处理，请把需求说得更具体一些。",
          }]);
        } else if (event.type === "error") {
          produced = true;
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", text: `聊天失败：${event.message}`, system: true,
          }]);
        } else if (event.type === "interrupted") {
          setStatus("已停止");
        }
      },
      onDone: (err) => {
        const stream = streamRef.current;
        streamRef.current = null;
        if (stream?.created) {
          // 空占位（模型没吐任何字）不留在聊天记录里。
          setMessages((current) => current.filter((message) => message.id !== stream.id
            || Boolean(message.text.trim()) || Boolean(message.image) || Boolean(message.video)));
        }
        if (err) {
          produced = true;
          setFailedUserIds((current) => (
            current.has(userMessageId) ? current : new Set(current).add(userMessageId)
          ));
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", text: `聊天失败：${err}`, system: true,
          }]);
        } else if (!produced) {
          setMessages((current) => [...current, {
            id: crypto.randomUUID(), role: "assistant", system: true, text: "没有返回内容，请重试或换一句说法。",
          }]);
        }
        setBusy(false);
        setStatus("");
        abortRef.current = null;
        // 收尾再轻量补一次快照：SSE 丢帧时，最终消息也能在当前页面直接出现，不必切走再回来。
        const finishedThread = threadId;
        window.setTimeout(() => {
          if (threadIdRef.current !== finishedThread) return;
          fetchSnapshot(finishedThread).then((snap) => {
            if (threadIdRef.current !== finishedThread) return;
            const fallbackSpeaker = mode === "chat" ? activeSpeaker : (cardNames[0] || "");
            const incoming = (snap.items || [])
              .map(asChatMessage)
              .filter((item): item is ChatMessage => !!item)
              .map((item) => item.role === "assistant" && !item.speaker && fallbackSpeaker
                ? { ...item, speaker: fallbackSpeaker }
                : item);
            if (incoming.length) {
              const known = new Set(messagesRef.current.map((message) => message.id));
              for (const message of incoming) {
                if (!known.has(message.id) && message.role === "assistant") {
                  bumpActivity(message.speaker || activeSpeaker, message.ts);
                }
              }
              setMessages((current) => mergeSnapshotMessages(current, incoming));
            }
          }).catch(() => { /* 快照收尾失败不打扰 */ });
        }, 800);
        const pendingMedia = pendingMediaRef.current;
        pendingMediaRef.current = [];
        if (settings.chatRoomMediaInsert === true) {
          // 群聊一轮可能有多位角色各发一条：全部发起，不能只取第一条。
          for (const media of pendingMedia) generateChatMedia(media);
        }
      },
    });
    abortRef.current = abort;
  };

  const persistMessages = async (next: ChatMessage[], failText: string): Promise<boolean> => {
    try {
      const result = await saveSnapshot(threadId, next);
      if (!result.ok) throw new Error("快照保存失败");
      setMessages(next);
      return true;
    } catch (err) {
      setMessages((current) => [...current, {
        id: crypto.randomUUID(), role: "assistant", system: true,
        text: `${failText}：${err instanceof Error ? err.message : String(err)}`,
      }]);
      return false;
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy || members.length === 0) return;
    // 后台主动/追问任务持有同一条 thread 锁时，用户发送会撞 409。用户消息优先：
    // 先取消后台任务并等它释放，期间显示「正在结束后台消息…」；等不到就提示稍后再发。
    if (backgroundBusyRef.current) {
      setBusy(true);
      setStatus("正在结束后台消息…");
      try { await cancelAgent(threadId); } catch { /* 后端未起/已结束忽略 */ }
      const released = await waitForAgentRelease(threadId);
      if (!released) {
        setBusy(false);
        setStatus("");
        setMessages((current) => [...current, {
          id: crypto.randomUUID(), role: "assistant", system: true,
          text: "后台消息还在生成，稍等几秒再发。",
        }]);
        return;
      }
      backgroundBusyRef.current = false;
    }
    const userMessageId = crypto.randomUUID();
    const history = buildChatRoomHistory(messagesRef.current);
    const userMessage: ChatMessage = {
      id: userMessageId, role: "user", text, ts: Date.now() / 1000,
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    runChatTurn(text, history, userMessageId);
  };

  const startEdit = (message: ChatMessage) => {
    if (busy || mediaBusy || message.system || message.poke) return;
    setEditingId(message.id);
    setEditingText(message.text);
  };

  const saveEdit = async () => {
    const text = editingText.trim();
    if (!editingId || !text || mediaBusy) return;
    const message = messagesRef.current.find((item) => item.id === editingId);
    if (!message) {
      setEditingId("");
      setEditingText("");
      return;
    }
    const updated: ChatMessage = { ...message, text };
    try {
      const result = await editMessageRemote(threadId, updated);
      if (!result.ok || !result.updated) throw new Error("消息不存在或保存失败");
      setMessages((current) => current.map((item) => item.id === editingId ? updated : item));
      setEditingId("");
      setEditingText("");
    } catch (err) {
      setMessages((current) => [...current, {
        id: crypto.randomUUID(), role: "assistant", system: true,
        text: `编辑失败：${err instanceof Error ? err.message : String(err)}`,
      }]);
    }
  };

  const deleteMessage = async (message: ChatMessage) => {
    if (busy || mediaBusy || message.system || message.poke) return;
    if (!window.confirm("删除这条消息？")) return;
    const next = messagesRef.current.filter((item) => item.id !== message.id);
    if (editingId === message.id) {
      setEditingId("");
      setEditingText("");
    }
    await persistMessages(next, "删除失败");
  };

  const resendUserMessage = async (message: ChatMessage) => {
    if (busy || mediaBusy || message.role !== "user") return;
    const current = messagesRef.current;
    const index = current.findIndex((item) => item.id === message.id);
    if (index < 0) return;
    // 只数用户看得见的消息：system 提示与 poke 动作也在数组里，不能算进「删几条」。
    const visibleAfter = current.slice(index + 1).filter(
      (item) => !item.system && !item.poke,
    ).length;
    if (visibleAfter > 0 && !window.confirm(
      `重新发送将删除这条消息及其后的 ${visibleAfter} 条，是否继续？`,
    )) {
      return;
    }
    const retained = current.slice(0, index);
    if (!await persistMessages(retained, "重新发送失败")) return;
    const userMessageId = crypto.randomUUID();
    const userMessage: ChatMessage = { ...message, id: userMessageId, ts: Date.now() / 1000 };
    setMessages([...retained, userMessage]);
    runChatTurn(message.text, buildChatRoomHistory(retained), userMessageId);
  };

  const regenerateMessage = async (message: ChatMessage) => {
    if (busy || mediaBusy || message.role !== "assistant" || message.system) return;
    const current = messagesRef.current;
    const index = current.findIndex((item) => item.id === message.id);
    if (index < 0) return;
    let userIndex = index - 1;
    while (userIndex >= 0 && current[userIndex].role !== "user") userIndex -= 1;
    if (userIndex < 0) return;
    const userMessage = current[userIndex];
    const retained = current.slice(0, userIndex + 1);
    const discarded = current.slice(retained.length).filter(
      (item) => !item.system && !item.poke,
    ).length;
    if (discarded > 0 && !window.confirm(
      `重新生成将删除此消息后的 ${discarded} 条消息，是否继续？`,
    )) {
      return;
    }
    if (!await persistMessages(retained, "重新生成失败")) return;
    runChatTurn(
      userMessage.text,
      buildChatRoomHistory(retained.slice(0, userIndex)),
      userMessage.id,
    );
  };

  const stop = async () => {
    abortRef.current?.();
    abortRef.current = null;
    try { await cancelAgent(threadId); } catch { /* 后端未起/已结束忽略 */ }
    await waitForAgentRelease(threadId);
    setBusy(false);
    setStatus("已停止");
  };

  return (
    <div className="chatroom-inline">
      <div className="chatroom-member-bar">
        <span className="chatroom-mode-label">
          {mode === "chat" ? "私聊" : "群聊"} · 独立会话，不推进剧情
        </span>
        <div className="chatroom-member-chips">
          {cardNames.length === 0 && (
            <span className="chatroom-empty">未找到角色：请绑定角色卡，或在世界书写入角色条目。</span>
          )}
          {cardNames.map((name) => {
            const active = members.includes(name);
            return (
              <button
                key={name}
                type="button"
                className={`chatroom-chip${active ? " active" : ""}`}
                onClick={() => toggleMember(name)}
                title={mode === "chat" ? `和 ${name} 私聊` : `群聊成员：${name}`}
                aria-label={name}
              >
                <span className="chatroom-avatar">
                  {name.slice(0, 1)}
                  {avatars[name] && (
                    <img
                      src={avatars[name]}
                      alt=""
                      onError={(event) => { event.currentTarget.remove(); }}
                    />
                  )}
                </span>
                {active && mode === "group_chat" && (
                  <span className="chatroom-order">{members.indexOf(name) + 1}</span>
                )}
                {memberUnread[name] > 0 && <span className="chatroom-unread-dot" />}
              </button>
            );
          })}
        </div>
        <span className="chatroom-bar-hint">
          {mode === "group_chat"
            ? "按群聊节奏挑人回应，角色之间会互相接话；整段对话都作为上下文；明确生图/检索/计划仍走专家。"
            : "通用 Agent 套角色人设：按整段对话衔接，闲聊、问答、创作协助与工具能力都保留。"}
        </span>
        <button
          type="button"
          className="btn"
          style={{ marginLeft: 8, flexShrink: 0 }}
          onClick={() => setRosterOpen(true)}
          disabled={busy || mediaBusy}
          title="管理聊天角色：合集卡从世界书条目识别，可补选/排除/改名/排序"
        >
          管理角色
        </button>
      </div>

      {showPressureHint && (
        <div className={`chatroom-context-hint ${pressure.level}`} role="status">
          <span className="chatroom-context-text">
            {pressure.level === "full" ? "最近这段对话已经很长" : "聊天记录开始变多"}
            （最近约 {pressure.count} 条 / {Math.max(1, Math.round(pressure.chars / 1000))}k 字）。
            服务端会自动把更早的内容滚动摘要；想保留更多原话细节时也可以手动压缩：
            原文会归档，当前会话换成一条历史摘要，角色仍保留剧情状态与数据表。
          </span>
          <span className="chatroom-context-actions">
            <button
              type="button"
              className="btn"
              onClick={() => setDismissedPressure(pressureKey)}
              title="先不压缩；继续聊 4k 字左右会再提醒一次"
            >
              稍后
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy || compactBusy || !onCompactContext}
              onClick={() => {
                setDismissedPressure(pressureKey);
                void onCompactContext?.();
              }}
              title="把整段聊天压成一条历史摘要：原文会归档，可随时从顶部「恢复」取回"
            >
              {compactBusy ? "压缩中…" : "压缩上下文"}
            </button>
          </span>
        </div>
      )}

      <div
        className="chatroom-stream chat-stream"
        ref={scrollRef}
        onScroll={(event) => {
          const el = event.currentTarget;
          const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          atBottomRef.current = bottom;
          if (bottom) {
            clearUnread(threadId);
            reportRead();
          }
        }}
      >
        {!loaded && <div className="chatroom-empty">正在翻聊天记录…</div>}
        {loaded && messages.length === 0 && (
          <div className="chatroom-empty">
            开始和 {members.join("、") || "角色"} 聊天吧。不会推进剧情；需要时也可以让角色帮你创作、生图或查资料。
          </div>
        )}
        {messages.map((message, index) => {
          const previousTs = index > 0 ? messages[index - 1]?.ts : undefined;
          const timeDivider = shouldShowChatTimeDivider(message.ts, previousTs) ? (
            <div className="chatroom-time-divider">
              <span>{formatChatTimeDivider(message.ts)}</span>
            </div>
          ) : null;
          if (message.poke) {
            return (
              <Fragment key={message.id}>
                {timeDivider}
                <div className="chatroom-poke">
                  {message.speaker || activeSpeaker} 戳了戳你
                </div>
              </Fragment>
            );
          }
          const speakerName = message.role === "assistant"
            ? (message.speaker || activeSpeaker)
            : (persona.name || "我");
          // 已读/送达圆点：角色消息右侧红=未读、绿=已读；用户消息左侧红=发送中/失败、绿=已送达。
          const assistantRead = message.role === "assistant"
            && typeof message.ts === "number" && message.ts > 0 && message.ts <= readUpTo;
          const userDelivered = message.role === "user" && (
            deliveredUserIds.has(message.id)
            || (typeof message.ts === "number" && message.ts > 0
              && message.ts < mountedAtRef.current - 1)
          );
          const userFailed = failedUserIds.has(message.id);
          return (
            <Fragment key={message.id}>
              {timeDivider}
              <div className={`chatroom-msg ${message.role}${message.system ? " system" : ""}`}>
                {message.role === "assistant" && !message.system && (
                  <span className="chatroom-msg-avatar" aria-hidden="true">
                    {(message.speaker || activeSpeaker).slice(0, 1)}
                    {avatars[message.speaker || activeSpeaker] && (
                      <img
                        src={avatars[message.speaker || activeSpeaker]}
                        alt=""
                        onError={(event) => { event.currentTarget.remove(); }}
                      />
                    )}
                  </span>
                )}
                <div className="chatroom-msg-body">
                  {!message.system && (
                    <div className="chatroom-msg-meta">
                      {message.role === "user" && (
                        <span
                          className={`chatroom-read-dot ${userFailed ? "unread" : userDelivered ? "read" : "unread"}`}
                          title={userFailed ? "发送失败" : userDelivered ? "已送达" : "发送中"}
                          aria-label={userFailed ? "发送失败" : userDelivered ? "已送达" : "发送中"}
                        />
                      )}
                      <span className="chatroom-msg-name">{speakerName}</span>
                      {message.role === "assistant" && typeof message.ts === "number" && message.ts > 0 && (
                        <span
                          className={`chatroom-read-dot ${assistantRead ? "read" : "unread"}`}
                          title={assistantRead ? "已读" : "未读"}
                          aria-label={assistantRead ? "已读" : "未读"}
                        />
                      )}
                    </div>
                  )}
                {message.image && (
                  <img className="chatroom-media" src={message.image} alt="" loading="lazy" />
                )}
                {message.video && (
                  <video className="chatroom-media" src={message.video} controls preload="metadata" />
                )}
                {editingId === message.id ? (
                  <div className="chatroom-edit">
                    <textarea
                      value={editingText}
                      onChange={(event) => setEditingText(event.target.value)}
                      rows={3}
                      autoFocus
                    />
                    <div className="chatroom-edit-actions">
                      <button
                        type="button"
                        className="btn"
                        onClick={() => { setEditingId(""); setEditingText(""); }}
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        className="btn primary"
                        disabled={!editingText.trim() || mediaBusy}
                        onClick={() => void saveEdit()}
                      >
                        保存
                      </button>
                    </div>
                  </div>
                ) : (
                  message.text && <div className="chatroom-bubble">{message.text}</div>
                )}
                {!message.system && editingId !== message.id && (
                  <div className="chatroom-msg-actions">
                    <button type="button" disabled={busy || mediaBusy} onClick={() => startEdit(message)}>
                      编辑
                    </button>
                    {message.role === "user" ? (
                      <button
                        type="button"
                        disabled={busy || mediaBusy}
                        onClick={() => void resendUserMessage(message)}
                      >
                        重新发送
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busy || mediaBusy}
                        onClick={() => void regenerateMessage(message)}
                      >
                        重新生成
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busy || mediaBusy}
                      onClick={() => void deleteMessage(message)}
                    >
                      删除
                    </button>
                  </div>
                )}
              </div>
            </div>
            </Fragment>
          );
        })}
        {(busy || backgroundTyping || mediaTasks.length > 0) && (
          <div className="chatroom-typing">
            {status
              || (mediaTasks[0]
                ? `${mediaTasks[0].speaker} ${mediaTasks[0].phase === "waiting" ? "正在等显卡空闲…" : "正在拍摄中…"}`
                : "")
              || backgroundTyping
              || `${activeSpeaker} 正在输入中…`}
            {mediaTasks.length > 0 && (
              <button
                type="button"
                className="chatroom-typing-cancel"
                onClick={() => cancelChatMediaTask(threadId, mediaTasks[0].speaker)}
              >
                取消拍摄
              </button>
            )}
          </div>
        )}
      </div>

      <div className="chat-input-wrap chatroom-compose-wrap">
        <div className="chatroom-compose">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder={
              members.length === 0
                ? "请先绑定角色卡，或在世界书写入角色条目"
                : mode === "group_chat"
                  ? `在群聊里发言（${members.join("、")} 将依次回复）`
                  : `和 ${members[0]} 聊天…`
            }
            rows={2}
            disabled={busy || members.length === 0}
          />
          {busy ? (
            <button type="button" className="btn danger chat-send-btn" onClick={() => void stop()}>
              <Square size={15} style={{ marginRight: 6, verticalAlign: "-2px" }} />
              停止
            </button>
          ) : (
            <button
              type="button"
              className="btn primary chat-send-btn"
              onClick={() => void send()}
              disabled={!input.trim() || members.length === 0}
            >
              <Send size={15} style={{ marginRight: 6, verticalAlign: "-2px" }} />
              发送
            </button>
          )}
        </div>
      </div>
      {rosterOpen && (
        <CharacterRosterModal
          roster={roster || null}
          overrides={parseRosterOverrides(repo.characterRoster)}
          onSave={(next: CharacterRosterOverrides) => {
            updateRepo?.({ characterRoster: next });
            onRefreshRoster?.();
          }}
          onClose={() => setRosterOpen(false)}
        />
      )}
    </div>
  );
}
