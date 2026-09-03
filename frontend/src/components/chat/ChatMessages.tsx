import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Bot, Brush, Check, ChevronDown, CopyPlus, CornerDownRight, Download, ExternalLink, File as FileIcon, Flag, GitBranch, Image as ImageIcon, Images, Merge, MessageCircle, MoreHorizontal, Pause, Pencil, Play, Plus, RotateCw, ScanText, Search, Send, Sparkles, Square, Trash2, Video, Workflow, Wrench, X } from "lucide-react";
import type { AgentRoute, ChatMessage, MsgPart, PromptApproval, RouteChoice } from "../../types/chat";
import type { AssistantAvatarState } from "../../lib/assistantAvatar";
import { runScripts, Placement, type RegexScript } from "../../lib/regexEngine";
import { restoreOpenDetails, trackDetailsToggle } from "../../lib/stableDetailsOpen";
import { renderMarkdown } from "../../lib/renderMarkdown";
import { substituteMacros } from "../../lib/chatMacros";
import { userMessagePlainText, userMessageRichContent } from "../../lib/chatGeneration";
import type { PortOp } from "../../api/ai";
import { attachmentUrl, proxyImageUrl, selectInspiration as selectInspirationPost, saveInspirationCard } from "../../api/ai";
import { pushInspirationsToCanvas, inspirationToCanvasPayload, inspirationCanvasLabel } from "../../lib/inspirationInsert";
import { approvePlanTask, cancelPlanTask, deleteRecipe, getPlanTask, keepRecipe } from "../../lib/planTaskActivity";
import type { RichContent } from "../RichInput";
import { CopyButton } from "../CopyButton";
import { openLightbox } from "../Lightbox";
import { AudioPlayer } from "../AudioPlayer";
import { VisualCiBadgeSlot } from "./VisualCiBadgeSlot";

export { userMessagePlainText, userMessageRichContent } from "../../lib/chatGeneration";

// 下载图片/视频：拉成 blob 触发浏览器保存对话框，文件名取地址里的真实名。
// 失败（跨域/CORS）则退化为新标签打开，让用户手动另存。
async function downloadMedia(url: string) {
  const name = mediaFilename(url);
  try {
    const resp = await fetch(url);
    const blob = await resp.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // 下载已由浏览器接管；下一帧释放 objectURL，避免长定时器，也不抢在 click 之前释放。
    requestAnimationFrame(() => URL.revokeObjectURL(href));
  } catch {
    window.open(url, "_blank", "noreferrer");
  }
}

// 从取图地址推断文件名：优先 filename/path 查询参数，其次路径末段，兜底带时间戳
function mediaFilename(url: string): string {
  try {
    const u = new URL(url, window.location.origin);
    const q = u.searchParams.get("filename") || u.searchParams.get("path");
    if (q) return q.split(/[/\\]/).pop() || q;
    const last = u.pathname.split("/").pop();
    if (last && /\.\w+$/.test(last)) return last;
  } catch { /* ignore */ }
  return `download_${Date.now()}`;
}

// 秒 → mm:ss（时长/当前位置显示）
// （实现见 components/AudioPlayer.tsx，聊天与画布共用）

// AI 正文按 Markdown 渲染（**粗体**、# 标题、代码块、列表、表格等），同时保留卡内正则产出的
// HTML（<status> 等带样式 <div>）：marked 把 Markdown 转 HTML 且原样透传已有 HTML，再统一消毒。
// breaks:true → 单换行也成 <br>（扮演正文的分行有语义，对齐旧 pre-wrap 观感）。
// 实现见 lib/renderMarkdown.ts（画布剧情节点同款渲染）。

// 动图判定：GIF/WebP 用 <img> 渲染（原生循环、可放大），其余（mp4/webm/mov…）用 <video>。
// ComfyUI 的动图/视频产物都进 msg.video，这里按扩展名/类型分流。
function isAnimatedImage(url: string): boolean {
  const clean = url.split("?")[0].split("#")[0].toLowerCase();
  if (/\.(gif|webp)$/.test(clean)) return true;
  // 本地/代理取图地址把真实文件名放在 filename/path 查询参数里
  return /[?&](filename|path)=[^&]*\.(gif|webp)/i.test(url);
}

// 内联图片 chip：缩略小图，悬停弹出大图预览浮窗
export function ImageChip({ url, onAddToChat }: { url: string; onAddToChat?: (url: string) => void }) {
  const open = () => openLightbox(url);
  return (
    <span className="img-chip">
      <img src={url} alt="用户附图" className="img-chip-thumb" role="button" tabIndex={0}
        onClick={open}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }} />
      {/* 悬停大图预览：pointer-events:none + 定位在上方，避免遮挡小图触发 hover 抖动闪烁 */}
      <span className="img-chip-pop">
        <img src={url} alt="预览" />
      </span>
      {onAddToChat && (
        <button
          className="img-chip-add"
          title="把这张图添加到输入框"
          onClick={(e) => { e.stopPropagation(); onAddToChat(url); }}
        >
          <Plus size={12} />
        </button>
      )}
    </span>
  );
}

// 文件附件卡（D1 历史回放只读）：MIME 图标 + 文件名 + 大小，点击流式下载（GET /attachments/{file_id}）。
// 上传失败/已删除的占位卡（file_id 非 32 位 hex）显示「不可用」但不报错。
function FileAttachmentChip({ part }: { part: MsgPart }) {
  const fileId = part.fileId || "";
  const name = part.name || "附件";
  const url = fileId ? attachmentUrl(fileId) : "";
  const downloadable = fileId.length === 32;
  return (
    <span className="file-attach-chip" title={name}>
      <FileIcon size={14} className="file-attach-chip-icon" />
      <span className="file-attach-chip-name">{name}</span>
      <span className="file-attach-chip-size">{formatFileSize(part.size || 0)}</span>
      {downloadable ? (
        <a
          className="file-attach-chip-dl"
          href={url}
          download={name}
          title="下载附件"
          onClick={(e) => e.stopPropagation()}
        >
          <Download size={12} />
        </a>
      ) : (
        <span className="file-attach-chip-dead" title="附件已删除或不可用">×</span>
      )}
    </span>
  );
}

// 文件大小人类可读（B/KB/MB/GB）
function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const idx = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** idx;
  return `${value >= 100 || idx === 0 ? Math.round(value) : value.toFixed(1)} ${units[idx]}`;
}

// 用户消息：支持图文混排（parts 按顺序渲染，图片为内联 chip+悬停预览），否则纯文本
// memo：长列表里流式/进度刷新时，未变的历史消息跳过重渲染（回调需稳定，见 ChatView 的 useCallback）
function UserMessageBase({
  msg,
  macros,
  onAddToChat,
  onEdit,
  onDelete,
  onRegenerate,
  regenerationDisabled = false,
}: {
  msg: ChatMessage;
  macros?: { char: string; user: string };
  onAddToChat?: (url: string) => void;
  onEdit?: (content: RichContent, sourceId?: string) => void;
  onDelete?: (id: string) => void;
  onRegenerate?: (id: string, slotId?: string) => void;
  regenerationDisabled?: boolean;
}) {
  const plainText = userMessagePlainText(msg);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);
  // 显示层宏替换：用户消息里的 {{user}}/{{char}} 也统一替（缺省 user→「我」）；不改存储。
  const sub = (t: string) => (macros ? substituteMacros(t, macros.char, macros.user) : t);
  return (
    <div className="msg-user">
      <div className="bubble-user">
        <div className="user-message-text">
          {msg.parts && msg.parts.length > 0 ? (
            msg.parts.map((p, i) =>
              (p.type === "image" || p.type === "masked-image") && p.url ? (
                <ImageChip
                  key={`img-${p.url}`}
                  url={p.url}
                  onAddToChat={p.type === "image" ? onAddToChat : undefined}
                />
              ) : p.type === "file" ? (
                <FileAttachmentChip key={`file-${p.fileId || i}`} part={p} />
              ) : (
                <span key={`text-${i}-${(p.text || "").slice(0, 20)}`}>{sub(p.text || "")}</span>
              ),
            )
          ) : (
            sub(msg.text || "")
          )}
        </div>
        {/* 收起时只显示「…」；点开原地展开成一排图标（复制/编辑/删除），再点「…」收起 */}
        <div className="user-message-actions" ref={menuRef}>
          {!menuOpen ? (
            <button
              type="button" className="user-msg-act" title="操作" aria-label="展开操作"
              onClick={() => setMenuOpen(true)}
            >
              <MoreHorizontal size={15} />
            </button>
          ) : (
            <>
              <CopyButton text={plainText} className="user-msg-act" label="复制纯文本" />
              {onEdit && (
                <button
                  type="button" className="user-msg-act" title="复制图文内容到输入框编辑" aria-label="编辑此消息"
                  onClick={() => { onEdit(userMessageRichContent(msg), msg.id); setMenuOpen(false); }}
                >
                  <Pencil size={13} />
                </button>
              )}
              {onRegenerate && (
                <button
                  type="button" className="user-msg-act" title="从此消息重新生成"
                  aria-label="从此消息重新生成" disabled={regenerationDisabled}
                  onClick={() => { onRegenerate(msg.id); setMenuOpen(false); }}
                >
                  <RotateCw size={13} />
                </button>
              )}
              {onDelete && (
                <button
                  type="button" className="user-msg-act user-msg-act-danger" title="删除该消息" aria-label="删除该消息"
                  onClick={() => { onDelete(msg.id); setMenuOpen(false); }}
                >
                  <Trash2 size={13} />
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function PromptApprovalCard({
  approval,
  onAction,
}: {
  approval: PromptApproval;
  onAction?: (approval: PromptApproval, action: "submit" | "change" | "cancel", editedPrompt?: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [editedPrompt, setEditedPrompt] = useState(approval.prompt);
  const [busy, setBusy] = useState(false);
  const pending = approval.status === "pending";
  const actionable = pending || approval.status === "failed";
  const deliveryUnknown = approval.stage === "delivery_unknown";
  const requestFailed = approval.stage === "request_failed";
  const statusLabel = approval.status === "submitted" ? "已提交"
    : approval.status === "cancelled" ? "已取消"
      : deliveryUnknown ? "上游交付状态未知"
        : requestFailed ? "请求未发送到上游"
        : approval.status === "failed" ? "生成失败，等待后续处理" : "等待确认";
  const act = async (action: "submit" | "change" | "cancel", prompt?: string) => {
    if (!onAction || busy) return;
    setBusy(true);
    try {
      await onAction(approval, action, prompt);
      if (action === "change") setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="prompt-approval-card">
      <div className="prompt-approval-head">
        <span>{approval.kind === "video" ? "待审核视频提示词" : "待审核生图提示词"}</span>
        <span className={`prompt-approval-status ${approval.status}`}>{statusLabel}</span>
      </div>
      {editing ? (
        <textarea
          className="prompt-approval-editor"
          value={editedPrompt}
          onChange={(event) => setEditedPrompt(event.target.value)}
          autoFocus
        />
      ) : (
        <pre className="prompt-approval-code">{approval.prompt}</pre>
      )}
      {actionable && onAction && (
        <div className="prompt-approval-actions">
          {editing ? (
            <>
              <button className="btn primary" disabled={busy || !editedPrompt.trim()} onClick={() => act("change", editedPrompt)}>
                <Check size={14} /> 保存更改
              </button>
              <button className="btn" disabled={busy} onClick={() => { setEditedPrompt(approval.prompt); setEditing(false); }}>
                <X size={14} /> 返回
              </button>
            </>
          ) : (
            <>
              <button className="btn primary" disabled={busy} onClick={() => act("submit")}>
                <Check size={14} /> {deliveryUnknown || requestFailed ? "确认重新提交" : "确认提交"}
              </button>
              <button className="btn" disabled={busy} onClick={() => { setEditedPrompt(approval.prompt); setEditing(true); }}>
                <Pencil size={14} /> 更改
              </button>
              <button className="btn danger" disabled={busy} onClick={() => act("cancel")}>
                <X size={14} /> 取消
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const routeChoiceIcon = (route: AgentRoute) => {
  if (route === "answer") return <MessageCircle size={15} />;
  if (route === "generate" || route === "img2img") return <Images size={15} />;
  if (route === "analyze") return <ScanText size={15} />;
  if (route === "video") return <Video size={15} />;
  if (route === "inspire") return <Search size={15} />;
  return <Wrench size={15} />;
};

export function RouteChoiceCard({
  choice,
  onSelect,
}: {
  choice: RouteChoice;
  onSelect?: (choice: RouteChoice, route: AgentRoute) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const select = async (route: AgentRoute) => {
    if (!onSelect || busy || choice.status !== "pending") return;
    setBusy(true);
    try {
      await onSelect(choice, route);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="route-choice-card">
      <div className="route-choice-head">
        <span>选择本次功能</span>
        {choice.status === "selected" && <span>已选择</span>}
      </div>
      <div className="route-choice-actions">
        {choice.options.map((option) => (
          <button
            key={option.route}
            className={`btn ${choice.selectedRoute === option.route ? "primary is-selected" : ""}`}
            disabled={!onSelect || busy || choice.status === "selected"}
            title={option.label}
            onClick={() => select(option.route)}
          >
            {routeChoiceIcon(option.route)} {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

const MarkdownHtml = memo(function MarkdownHtml({ text, className, htmlRef }: {
  text: string; className?: string; htmlRef?: (el: HTMLDivElement | null) => void;
}) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return <div className={className} ref={htmlRef} dangerouslySetInnerHTML={{ __html: html }} />;
}, (prev, next) => prev.text === next.text && prev.className === next.className);

function AssistantMessageBase({ msg, streaming, avatarState = "default", portrait, displayRegex, depth, macros, onSendImage, onMaskImage, onRunCommand, onSetCover, onPromptApproval, onRouteChoice, onRegenerate, onCancelGeneration, onRetryIllustration, regenerating = false, onEdit, onDelete, onCreateCheckpoint, onBranch, onMergeAudio, visualCiRepoId, visualCiOutputDir }: { msg: ChatMessage; streaming?: boolean; avatarState?: AssistantAvatarState; portrait?: { name: string; url: string } | null; displayRegex?: RegexScript[]; depth?: number; macros?: { char: string; user: string }; onSendImage: (url: string) => void; onMaskImage?: (url: string) => void; onRunCommand?: (cmd: string) => void; onSetCover?: (url: string) => void; onPromptApproval?: (approval: PromptApproval, action: "submit" | "change" | "cancel", editedPrompt?: string) => Promise<void>; onRouteChoice?: (choice: RouteChoice, route: AgentRoute) => Promise<void>; onRegenerate?: (messageId: string, slotId?: string) => void; onCancelGeneration?: (messageId: string, slotId: string) => void; onRetryIllustration?: (messageId: string, slotId: string) => void; regenerating?: boolean; onEdit?: (id: string, text: string) => void; onDelete?: (id: string) => void; onCreateCheckpoint?: (id: string) => void; onBranch?: (id: string) => void; onMergeAudio?: (messageId: string) => Promise<void>; visualCiRepoId?: string; visualCiOutputDir?: string }) {
  const [showThinking, setShowThinking] = useState(false); // 思考默认折叠（2026-08-31 深夜用户反馈：28k 长思考自动展开吞掉正文区），可手动展开
  // 非流式 agent「执行过程」面板：流式中自动展开实时可见；replace 后保留可折叠回看
  const [showTrace, setShowTrace] = useState(false);
  // 正文里预设正则折叠出的 <details>（思考过程等）在 innerHTML 重写后保持展开状态：
  // 插画回填/流式/状态更新都会重写 bot-html，无保态时用户展开几秒就被收起（2026-08-29）。
  const stableDetailsRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const openDetailsKeys = useRef<Set<string>>(new Set());
  useLayoutEffect(() => {
    for (const el of Object.values(stableDetailsRefs.current)) {
      if (!el) continue;
      restoreOpenDetails(el, openDetailsKeys.current);
      // toggle 不冒泡，但 capture 阶段可达祖先：容器上委托记录展开状态。
      if (!el.dataset.detailsBound) {
        el.dataset.detailsBound = "1";
        el.addEventListener("toggle", (e) => trackDetailsToggle(e.target, openDetailsKeys.current), true);
      }
    }
  });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [mergingAudio, setMergingAudio] = useState(false);
  const [planActions, setPlanActions] = useState<Record<string, string>>({});
  const [planImages, setPlanImages] = useState<Record<string, string[]>>({});
  // 固化询问卡动作状态：undefined=待选择，kept/removed=已处理
  const [recipeActions, setRecipeActions] = useState<Record<string, "kept" | "removed">>({});
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);
  // 正文与媒体槽先组合成带私有哨兵的字符串：显示正则仍对完整正文运行，之后再按哨兵还原图文顺序。
  const orderedMediaParts: MsgPart[] = [];
  const richRawBase = msg.parts && msg.parts.length > 0
    ? msg.parts.map((part) => {
      if (part.type === "text") return part.text || "";
      const index = orderedMediaParts.push(part) - 1;
      return `\uE000LAF_MEDIA_${index}\uE001`;
    }).join("")
    : (msg.text || "");
  const rawTextBase = msg.parts && msg.parts.length > 0
    ? msg.parts.filter((p) => p.type === "text").map((p) => p.text || "").join("")
    : (msg.text || "");
  // 显示层正则（markdownOnly）：渲染前隐藏/压缩 <think>/状态块等（不改存储，仅改渲染）。
  // 流式生成中不跑，避免半截标签闪烁；生成完成后应用。
  const rawText = (!streaming && displayRegex && displayRegex.length > 0)
    ? runScripts(richRawBase, Placement.AI_OUTPUT, displayRegex, { isMarkdown: true, depth })
    : richRawBase;
  // 从正文解析 AI 给出的可执行指令标记 [[cmd:/w 文生图]]，渲染成按钮；正文里移除标记
  const cmds: string[] = [];
  const withCmds = rawText.replace(/\[\[cmd:([^\]]+)\]\]/g, (_, c) => {
    const v = String(c).trim();
    if (v) cmds.push(v);
    return "";
  }).trim();
  // 计划审批标记：[[plan:<task_id>]] → 对话内直接批准/取消，不必去后台活动面板
  const planTaskIds: string[] = [];
  const withPlan = withCmds.replace(/\[\[plan:([^\]]+)\]\]/g, (_, t) => {
    const v = String(t).trim();
    if (v) planTaskIds.push(v);
    return "";
  }).trim();
  // 固化询问标记：[[recipe:<id>|<name>]] → 自由循环成功后的「保留/不保留」内联卡
  const recipeOffers: { id: string; name: string }[] = [];
  const withRecipe = withPlan.replace(/\[\[recipe:([^|\]]+)\|([^\]]+)\]\]/g, (_, id, name) => {
    const rid = String(id).trim();
    const rname = String(name).trim();
    if (rid && rname) recipeOffers.push({ id: rid, name: rname });
    return "";
  }).trim();
  // 显示层宏替换：模型输出/历史里残留的字面 {{user}}/{{char}} 统一替成人设名/角色名（缺省 user→「我」），
  // 像全局宏一样在渲染处生效（不改存储，与开场白/提示词侧对齐）。
  const cleanText = macros ? substituteMacros(withRecipe, macros.char, macros.user) : withRecipe;
  const orderedChunks = orderedMediaParts.length > 0
    ? cleanText.split(/\uE000LAF_MEDIA_(\d+)\uE001/g)
    : [];
  // 旧消息的顶层图片保持原渲染；parts 媒体统一在下方按槽位顺序渲染。
  const imgs: string[] = [];
  if (msg.image) imgs.push(msg.image);
  const renderMediaPart = (part: MsgPart | undefined, index: number) => {
    if (!part) return null;
    if (part.type === "file") {
      return <FileAttachmentChip key={part.fileId || `file-${index}`} part={part} />;
    }
    if (part.type === "media-slot") {
      const isAudio = part.kind === "audio";
      return (
        <div className={`media-slot media-slot-${part.status || "pending"}${isAudio ? " media-slot-audio" : ""}`} key={part.slotId || `slot-${index}`}>
          {part.status === "failed" ? (
            isAudio ? (part.error || `「${part.speaker || ""}」配音生成失败`) : (
              <span className="media-slot-failed">
                {part.error || "插画生成失败"}
                {onRetryIllustration && part.slotId && (
                  <button
                    type="button"
                    className="media-slot-retry"
                    title="用原参数重新生成这张图"
                    aria-label="重新生成"
                    onClick={() => onRetryIllustration(msg.id, part.slotId!)}
                  >
                    <RotateCw size={13} /> 重新生成
                  </button>
                )}
              </span>
            )
          ) : isAudio ? (
            <><span className="bot-spinner" /><span>正在生成 {part.speaker || ""}（{part.seq ?? "?"}/{part.total ?? "?"}）…</span></>
          ) : (
            <div className="media-slot-generating">
              <span><span className="bot-spinner" /> 插画生成中…</span>
              {onCancelGeneration && part.slotId && (
                <button
                  type="button"
                  className="media-slot-stop"
                  title="停止本次生成"
                  aria-label="停止本次生成"
                  onClick={() => onCancelGeneration(msg.id, part.slotId!)}
                >
                  <Square size={13} /> 停止
                </button>
              )}
            </div>
          )}
        </div>
      );
    }
    if (part.type === "video" && part.url) {
      return (
        <div className="img-card" key={part.slotId || part.url}>
          {isAnimatedImage(part.url)
            ? <img src={part.url} alt="剧情插画" loading="lazy" onClick={() => openLightbox(part.url!)} />
            : <video src={part.url} controls loop playsInline />}
          <div className="img-tools">
            <a className="img-tool" href={part.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> 查看原文件</a>
            <button className="img-tool" onClick={() => downloadMedia(part.url!)}><Download size={14} /> 下载</button>
          </div>
        </div>
      );
    }
    if (part.type === "audio" && part.url) {
      return (
        <div className="img-card audio-bubble" key={part.slotId || part.url}>
          {part.speaker && <div className="audio-speaker-label">{part.speaker}</div>}
          <AudioPlayer src={part.url} />
          <div className="img-tools">
            <a className="img-tool" href={part.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> 查看原文件</a>
            <button className="img-tool" onClick={() => downloadMedia(part.url!)}><Download size={14} /> 下载</button>
          </div>
        </div>
      );
    }
    if ((part.type === "image" || part.type === "masked-image") && part.url) {
      const regeneration = part.regeneration || msg.regeneration;
      const canRegenerate = !!regeneration || !!part.slotId;
      return (
        <div className="img-card" key={part.slotId || part.url}>
          <img src={part.url} alt="剧情插画" loading="lazy" onClick={() => openLightbox(part.url!)} style={{ cursor: "zoom-in" }} />
          <div className="img-tools">
            <a className="img-tool" href={part.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> 查看原图</a>
            <button className="img-tool" onClick={() => downloadMedia(part.url!)}><Download size={14} /> 下载</button>
            <button className="img-tool" disabled={!canRegenerate || !onRegenerate || regenerating}
              title={regeneration
                ? "使用这张结果绑定的原始参数重新生成并替换"
                : "使用资产库原提示词与当前作品模板重新生成并替换"}
              onClick={() => onRegenerate?.(msg.id, part.slotId)}><RotateCw size={14} /> 重新生图</button>
            <button className="img-tool" onClick={() => onMaskImage?.(part.url!)}><Brush size={14} /> 蒙化修改</button>
            <button className="img-tool" onClick={() => onSendImage(part.url!)}><Send size={14} /> 发送至对话</button>
            {onSetCover && <button className="img-tool" onClick={() => onSetCover(part.url!)}><ImageIcon size={14} /> 设为封面</button>}
          </div>
          <VisualCiBadgeSlot
            generationId={part.generationId}
            repoId={visualCiRepoId}
            outputDir={visualCiOutputDir}
          />
        </div>
      );
    }
    return null;
  };
  // 音频分条拼接完整版：≥2 段 ready 分条时显示按钮（已有旧完整版则显示「重新拼接」）
  const audioTracks = (msg.parts || []).filter(
    (p) => p.type === "audio" && p.url && !(p.slotId || "").startsWith("merged-"),
  );
  const hasMergedTrack = (msg.parts || []).some(
    (p) => p.type === "audio" && (p.slotId || "").startsWith("merged-"),
  );
  return (
    <div className="msg-bot">
      <div className={`bot-avatar bot-avatar-${avatarState}`}>
        {portrait
          ? <img src={portrait.url} alt={`${portrait.name}头像`} title={portrait.name} />
          : <Bot size={18} />}
      </div>
      <div className="bot-content">
        {!streaming && (onEdit || onCreateCheckpoint || onBranch || onDelete) && (
          <div className="bot-msg-actions" ref={menuRef}>
            {/* 收起时只显示「…」；点开原地展开成一排图标（编辑/检查点/分支/复制），再点「…」收起 */}
            {!menuOpen ? (
              <button
                type="button" className="bot-msg-act" title="操作" aria-label="展开操作"
                onClick={() => setMenuOpen(true)}
              >
                <MoreHorizontal size={15} />
              </button>
            ) : (
              <>
                {onEdit && (
                  <button
                    type="button" className="bot-msg-act" title="编辑此消息" aria-label="编辑此消息"
                    onClick={() => { setDraft(rawTextBase); setEditing(true); setMenuOpen(false); }}
                  >
                    <Pencil size={13} />
                  </button>
                )}
                {onCreateCheckpoint && (
                  <button
                    type="button" className="bot-msg-act" title="创建检查点" aria-label="创建检查点"
                    onClick={() => { onCreateCheckpoint(msg.id); setMenuOpen(false); }}
                  >
                    <Flag size={13} />
                  </button>
                )}
                {onBranch && (
                  <button
                    type="button" className="bot-msg-act" title="创建分支" aria-label="创建分支"
                    onClick={() => { onBranch(msg.id); setMenuOpen(false); }}
                  >
                    <GitBranch size={13} />
                  </button>
                )}
                <button
                  type="button" className="bot-msg-act" title="复制" aria-label="复制"
                  onClick={() => { navigator.clipboard?.writeText(rawTextBase).catch(() => {}); setMenuOpen(false); }}
                >
                  <CopyPlus size={13} />
                </button>
                {onDelete && (
                  <button
                    type="button" className="bot-msg-act bot-msg-act-danger" title="删除该消息" aria-label="删除该消息"
                    onClick={() => { onDelete(msg.id); setMenuOpen(false); }}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </>
            )}
          </div>
        )}
        {msg.thinking && (
          <div className="thinking">
            <button className="thinking-head" onClick={() => setShowThinking((s) => !s)}>
              <span>思考过程</span>
              <ChevronDown
                size={16}
                style={{ transform: showThinking ? "none" : "rotate(-90deg)", transition: "transform .15s" }}
              />
            </button>
            {showThinking && <div className="thinking-body">{msg.thinking}</div>}
          </div>
        )}
        {msg.agentTrace?.length ? (
          <div className={`agent-trace${streaming ? " agent-trace-live" : ""}`}>
            <button className="agent-trace-head" onClick={() => setShowTrace((s) => !s)}>
              <span>
                {streaming ? "执行过程 · 进行中" : `执行过程 · ${msg.agentTrace.length} 条`}
              </span>
              <ChevronDown
                size={16}
                style={{ transform: showTrace ? "none" : "rotate(-90deg)", transition: "transform .15s" }}
              />
            </button>
            {(showTrace || streaming) && (
              <div className="agent-trace-body">
                {msg.agentTrace.map((line, index) => (
                  <div className="agent-trace-line" key={`${index}:${line.slice(0, 16)}`}>{line}</div>
                ))}
                {streaming && (
                  <div className="agent-trace-tail"><span className="bot-spinner" /> 执行中…</div>
                )}
              </div>
            )}
          </div>
        ) : null}
        {imgs.map((url, i) => (
          <div className="img-card" key={url}>
            <img src={url} alt={`生成结果 ${i + 1}`} loading="lazy" onClick={() => openLightbox(url)} style={{ cursor: "zoom-in" }} />
            <div className="img-tools">
              <a className="img-tool" href={url} target="_blank" rel="noreferrer">
                <ExternalLink size={14} /> 查看原图
              </a>
              <button className="img-tool" onClick={() => downloadMedia(url)}><Download size={14} /> 下载</button>
              <button
                className="img-tool"
                disabled={!msg.regeneration || !onRegenerate || regenerating}
                title={msg.regeneration ? "使用这张结果绑定的原始参数重新生成" : "旧结果未保存完整生成参数"}
                onClick={() => onRegenerate?.(msg.id)}
              >
                <RotateCw size={14} /> {regenerating ? "重新生成中…" : "重新生图"}
              </button>
              <button className="img-tool" onClick={() => onMaskImage?.(url)}><Brush size={14} /> 蒙化修改</button>
              <button className="img-tool" onClick={() => onSendImage(url)}>
                <Send size={14} /> 发送至对话
              </button>
              {onSetCover && (
                <button className="img-tool" onClick={() => onSetCover(url)}><ImageIcon size={14} /> 设为封面</button>
              )}
            </div>
          </div>
        ))}
        {msg.video && (
          <div className="img-card">
            {isAnimatedImage(msg.video) ? (
              // GIF/WebP 动图：用 <img> 渲染（原生循环播放、可放大），而非 <video>
              <img src={msg.video} alt="生成动图结果" loading="lazy"
                onClick={() => openLightbox(msg.video!)} style={{ maxWidth: "100%", borderRadius: 8, cursor: "zoom-in" }} />
            ) : (
              <video src={msg.video} controls loop playsInline style={{ maxWidth: "100%", borderRadius: 8 }} />
            )}
            <div className="img-tools">
              <a className="img-tool" href={msg.video} target="_blank" rel="noreferrer">
                <ExternalLink size={14} /> 查看原文件
              </a>
              <button className="img-tool" onClick={() => downloadMedia(msg.video!)}><Download size={14} /> 下载</button>
              <button className="img-tool" onClick={() => onSendImage(msg.video!)}>
                <Send size={14} /> 发送至对话
              </button>
            </div>
          </div>
        )}
        {msg.audio && (
          <div className="img-card">
            <AudioPlayer src={msg.audio} />
            <div className="img-tools">
              <a className="img-tool" href={msg.audio} target="_blank" rel="noreferrer">
                <ExternalLink size={14} /> 查看原文件
              </a>
              <button className="img-tool" onClick={() => downloadMedia(msg.audio!)}><Download size={14} /> 下载</button>
              <button className="img-tool" onClick={() => onSendImage(msg.audio!)}>
                <Send size={14} /> 发送至对话
              </button>
            </div>
          </div>
        )}
        {editing ? (
          <div className="bot-edit">
            <textarea
              className="bot-edit-area" value={draft} autoFocus rows={Math.min(20, Math.max(3, draft.split("\n").length))}
              onChange={(e) => setDraft(e.target.value)}
            />
            <div className="bot-edit-actions">
              <button className="btn primary" onClick={() => { onEdit?.(msg.id, draft.trim()); setEditing(false); }}>
                <Check size={14} /> 保存
              </button>
              <button className="btn" onClick={() => setEditing(false)}><X size={14} /> 取消</button>
            </div>
          </div>
        ) : orderedMediaParts.length > 0 ? (
          <div className="bot-rich-parts">
            {orderedChunks.map((chunk, index) => index % 2 === 0
              ? (chunk ? <MarkdownHtml key={`text-${index}`} className="bot-text bot-html" text={chunk} htmlRef={(el) => { stableDetailsRefs.current[index] = el; }} /> : null)
              : renderMediaPart(orderedMediaParts[Number(chunk)], Number(chunk)))}
          </div>
        ) : cleanText ? (
          <MarkdownHtml className="bot-text bot-html" text={cleanText} htmlRef={(el) => { stableDetailsRefs.current[-1] = el; }} />
        ) : null}
        {audioTracks.length >= 2 && onMergeAudio && (
          <div className="audio-merge-bar">
            <button
              className="btn btn-sm"
              disabled={mergingAudio}
              onClick={async () => {
                setMergingAudio(true);
                try { await onMergeAudio(msg.id); } finally { setMergingAudio(false); }
              }}
            >
              {mergingAudio
                ? <><span className="bot-spinner" /> 拼接中…</>
                : <><Merge size={14} /> {hasMergedTrack ? "重新拼接" : "拼接"}完整版（{audioTracks.length} 段按顺序）</>}
            </button>
          </div>
        )}
        {msg.promptApproval && (
          <PromptApprovalCard approval={msg.promptApproval} onAction={onPromptApproval} />
        )}
        {msg.routeChoice && (
          <RouteChoiceCard choice={msg.routeChoice} onSelect={onRouteChoice} />
        )}
        {streaming && (
          <div className="bot-streaming" title="正在生成，请稍候…">
            <span className="bot-spinner" />
            <span className="bot-streaming-text">生成中…</span>
          </div>
        )}
        {cmds.length > 0 && onRunCommand && (
          <div className="cmd-suggest">
            {cmds.map((c) => (
              <button key={c} className="cmd-chip" onClick={() => onRunCommand(c)} title={`执行 ${c}`}>
                <Play size={12} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                执行 {c}
              </button>
            ))}
          </div>
        )}
        {(planTaskIds.length > 0 || recipeOffers.length > 0) && (
          <div className="cmd-suggest">
            {planTaskIds.map((taskId) => planActions[taskId] ? (
              <span key={taskId} className="cmd-chip" style={{ opacity: 0.8 }}>
                {planActions[taskId] === "approved" ? "已批准，等待执行" : "已取消计划"}
              </span>
            ) : (
              <span key={taskId} style={{ display: "inline-flex", gap: 8 }}>
                <button
                  className="cmd-chip"
                  onClick={async () => {
                    try {
                      await approvePlanTask(taskId);
                      setPlanActions((p) => ({ ...p, [taskId]: "执行中…" }));
                      const timer = setInterval(async () => {
                        try {
                          const task = await getPlanTask(taskId);
                          const imgs: string[] = [];
                          for (const st of task.steps || []) {
                            const outs = (st.outputs || {}) as Record<string, unknown>;
                            const results = outs.results;
                            if (Array.isArray(results)) {
                              for (const r of results) {
                                if (r && typeof r === "object") {
                                  const u = (r as Record<string, unknown>).url;
                                  if (typeof u === "string" && u) imgs.push(u);
                                }
                              }
                            }
                          }
                          if (imgs.length) setPlanImages((prev) => ({ ...prev, [taskId]: imgs }));
                          const label = task.progress || `${task.steps.length} 步`;
                          if (["done", "partial", "error", "cancelled", "blocked"].includes(task.status)) {
                            clearInterval(timer);
                            const endLabel = task.status === "done" ? "已完成"
                              : task.status === "cancelled" ? "已取消"
                              : task.status === "error" ? "执行失败"
                              : task.status === "blocked" ? "已受阻" : "部分完成";
                            setPlanActions((p) => ({ ...p, [taskId]: `${endLabel}（${label}）` }));
                          } else {
                            setPlanActions((p) => ({ ...p, [taskId]: `${task.status === "awaiting_approval" ? "待审批" : "执行中"} ${label}` }));
                          }
                        } catch {
                          clearInterval(timer);
                        }
                      }, 2500);
                    } catch (e) {
                      setPlanActions((p) => ({ ...p, [taskId]: `批准失败：${(e as Error).message}` }));
                    }
                  }}
                  title="批准执行该计划（durable/expensive 步骤将获得一次性租约）"
                >
                  <Check size={12} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                  批准执行
                </button>
                <button
                  className="cmd-chip"
                  onClick={async () => {
                    try {
                      const res = await cancelPlanTask(taskId);
                      setPlanActions((p) => ({
                        ...p,
                        [taskId]: res?.ok ? "cancelled" : "无法取消（任务已在执行）",
                      }));
                    } catch (e) {
                      setPlanActions((p) => ({ ...p, [taskId]: `取消失败：${(e as Error).message}` }));
                    }
                  }}
                  title="取消该计划"
                >
                  <X size={12} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                  取消计划
                </button>
              </span>
            ))}
            {planTaskIds.map((taskId) => planImages[taskId] && planImages[taskId].length > 0 && (
              <div key={`imgs-${taskId}`} style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                {planImages[taskId].map((u) => (
                  <img key={u} src={u} alt="生成图" style={{ width: 72, height: 72, objectFit: "cover", borderRadius: 6, border: "1px solid #444" }} />
                ))}
              </div>
            ))}
            {recipeOffers.map((offer) => recipeActions[offer.id] ? (
              <span key={offer.id} className="cmd-chip" style={{ opacity: 0.8 }}>
                {recipeActions[offer.id] === "kept"
                  ? `已保留固化流程《${offer.name}》（设置 → 智能体 → 固化流程预设可管理）`
                  : `已不保留《${offer.name}》`}
              </span>
            ) : (
              <span key={offer.id} style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span className="cmd-chip" style={{ opacity: 0.75 }}>
                  把这次流程固化为预设《{offer.name}》？
                </span>
                <button
                  className="cmd-chip"
                  onClick={async () => {
                    try {
                      await keepRecipe(offer.id);
                      setRecipeActions((p) => ({ ...p, [offer.id]: "kept" }));
                    } catch (e) {
                      // 保留失败不动状态：卡片保持可重试，不谎报已处理
                      console.warn("keepRecipe failed", e);
                    }
                  }}
                  title="保留后进入固化流程清单，后续同类目标可整条重放（省 token，durable/expensive 步骤照常审批）"
                >
                  <Check size={12} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                  保留
                </button>
                <button
                  className="cmd-chip"
                  onClick={async () => {
                    try {
                      await deleteRecipe(offer.id);
                    } catch (e) {
                      console.warn("deleteRecipe failed", e);
                    }
                    setRecipeActions((p) => ({ ...p, [offer.id]: "removed" }));
                  }}
                  title="删除本次固化的草稿配方"
                >
                  <X size={12} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                  不保留
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

type AssistantMessageProps = Parameters<typeof AssistantMessageBase>[0];

// 角色头像解析会按消息返回等值的新对象。只比较其可见值，避免后台 2 秒轮询时
// 无意义地重写 Markdown innerHTML，从而破坏用户正在复制的浏览器选区。
export function assistantMessagePropsEqual(previous: AssistantMessageProps, next: AssistantMessageProps): boolean {
  const previousPortrait = previous.portrait;
  const nextPortrait = next.portrait;
  if (previousPortrait?.name !== nextPortrait?.name || previousPortrait?.url !== nextPortrait?.url) return false;
  const keys = new Set([
    ...Object.keys(previous) as (keyof AssistantMessageProps)[],
    ...Object.keys(next) as (keyof AssistantMessageProps)[],
  ]);
  return [...keys].every((key) => key === "portrait" || previous[key] === next[key]);
}

// memo 导出：props 未变（同一 msg 引用、稳定回调）则跳过重渲染。
export const UserMessage = memo(UserMessageBase);
export const AssistantMessage = memo(AssistantMessageBase, assistantMessagePropsEqual);

// 灵感卡：联网搜到并整理的「标题+内容」中文总结。代码块风格，右侧「插入对话」把内容作为文本填进输入框。
// M1.2：图片搜索结果显示为缩略图网格，用户点选后持久化选中项。
export function InspirationCard({
  data,
  threadId,
  messageId,
  proxyUrl,
  outputDir = "",
  onNotify,
  onInsert,
  onSentToCanvas,
}: {
  data: ChatMessage["inspiration"] & { images?: any[]; selected?: string[] };
  threadId?: string;
  messageId?: string;
  proxyUrl?: string;
  outputDir?: string;
  onNotify?: (msg: string, kind: "success" | "error" | "info") => void;
  onInsert: (text: string, card?: ChatMessage["inspiration"]) => void;
  /** 发送画布成功后回调（父级可切到画布模式让节点可见）。 */
  onSentToCanvas?: () => void;
}) {
  const images = data?.images || [];
  const [selected, setSelected] = useState<string[]>(data?.selected || []);
  const [saving, setSaving] = useState(false);
  useEffect(() => { setSelected(data?.selected || []); }, [data?.selected]);

  const toggle = useCallback(async (url: string) => {
    const next = selected.includes(url)
      ? selected.filter((u) => u !== url)
      : [...selected, url];
    setSelected(next);
    if (threadId && messageId) {
      try {
        await selectInspirationPost(threadId, messageId, next);
      } catch { /* 后端更新失败不阻断本地状态 */ }
    }
  }, [selected, threadId, messageId]);

  // M1.4 灵感卡资产库化：把整张灵感卡（标题+内容+选中图片）保存为资产库成员。
  // 图片走受控下载（候选校验 + 安全链），后端存 _web_materials/inspiration/。
  const saveSelected = useCallback(async () => {
    if (!outputDir || saving) return;
    setSaving(true);
    const urlSet = new Set(selected);
    const picked = images.filter((img) => img && urlSet.has(img.full_url));
    try {
      await saveInspirationCard(outputDir, {
        title: data?.title || "",
        content: data?.content || "",
        sources: data?.sources || [],
        images: picked.map((img) => ({
          full_url: img.full_url, source_url: img.source_url || "", title: img.title || "",
        })),
        threadId: threadId || "",
      });
      onNotify?.("已保存灵感卡到素材库（可在「上网素材 → 灵感卡」查看）", "success");
    } catch (err) {
      onNotify?.(`保存灵感卡失败：${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
    }
  }, [outputDir, selected, saving, images, threadId, data]);

  // M1.5：直接发送画布（不经过素材库）——走「缓存 + 通知」通道，画布未挂载也不丢。
  const sendToCanvas = useCallback(() => {
    const payload = inspirationToCanvasPayload({ ...data, messageId });
    // 对话灵感卡图片是远程 URL（full_url），画布节点直接加载会防盗链失败 → 走代理中转
    if (payload.imageUrl && proxyUrl) {
      payload.imageUrl = proxyImageUrl(payload.imageUrl, proxyUrl);
    }
    pushInspirationsToCanvas([payload]);
    onSentToCanvas?.();
    onNotify?.(`已发送「${data?.title || "灵感卡"}」到画布`, "success");
  }, [data, messageId, proxyUrl, onSentToCanvas, onNotify]);

  return (
    <div className="msg-bot">
      <div className="bot-avatar"><Bot size={18} /></div>
      <div className="bot-content">
        <div className="insp-card">
          <div className="insp-head">
            <Sparkles size={14} />
            <span>灵感 · {data?.title || ""}</span>
          </div>
          <pre className="insp-prompt">{data?.content || ""}</pre>
          {images.length > 0 && (
            <div className="insp-images">
              {images.map((img, i) => {
                const src = proxyUrl ? proxyImageUrl(img.thumb_url || img.full_url, proxyUrl) : (img.thumb_url || img.full_url);
                const sel = selected.includes(img.full_url);
                return (
                  <div
                    key={img.full_url || i}
                    className={`insp-thumb ${sel ? "sel" : ""}`}
                    onClick={() => img.full_url && toggle(img.full_url)}
                    title={img.title || img.source_url || ""}
                  >
                    <img src={src} alt={img.title || ""} loading="lazy"
                      onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                    {sel && <div className="insp-thumb-check">✓</div>}
                  </div>
                );
              })}
            </div>
          )}
          {selected.length > 0 && (
            <div className="insp-selected">已选 {selected.length} 张图片</div>
          )}
          {(data?.sources || []).length > 0 && (
            <div className="insp-sources">
              {data!.sources.map((s) => (
                <a key={s.url} href={s.url} target="_blank" rel="noreferrer" title={s.url}>
                  <ExternalLink size={11} /> {s.title || s.url}
                </a>
              ))}
            </div>
          )}
          <div className="insp-actions">
            <CopyButton text={data?.content || ""} className="insp-insert" />
            {((data?.title || data?.content) && !saving ? (
              <button
                className="insp-insert"
                disabled={!outputDir}
                title={outputDir ? "把这张灵感卡保存到素材库（含文本与选中图片）" : "未配置输出路径，无法保存"}
                onClick={() => void saveSelected()}
              >
                <Download size={13} /> 保存到素材库{selected.length > 0 ? `（${selected.length} 图）` : ""}
              </button>
            ) : null)}
            {saving && (
              <span className="insp-insert" style={{ opacity: 0.7 }}>
                <Download size={13} /> 保存中…
              </span>
            )}
            <button
              className="insp-insert"
              title="直接发送到画布（无需先保存素材库）"
              onClick={() => sendToCanvas()}
            >
              <GitBranch size={13} /> {inspirationCanvasLabel(data)}
            </button>
            <button
              className="insp-insert"
              title="插入到输入框：封面图进图片栏，发送时图文拆分（图片作参考图、文本带灵感卡语义）"
              onClick={() => onInsert(data?.content || "", data)}
            >
              <CornerDownRight size={13} /> 插入对话
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// 输入口编排计划卡：展示 AI 规划的「各输入口放什么」，用户确认后写入画布
export function PortsPlanCard({
  plan,
  onApply,
  onIgnore,
  onEditOp,
}: {
  plan: NonNullable<ChatMessage["portsPlan"]>;
  onApply: () => void;
  onIgnore: () => void;
  onEditOp?: (opIndex: number, value: string) => void;
}) {
  // 文本类 op（set_widget、文本 replace_output）的 value 可在执行前内联编辑
  const isEditableText = (op: PortOp) =>
    op.action === "set_widget" || (op.action === "replace_output" && op.kind !== "image");
  const actionLabel = (op: PortOp) => {
    if (op.action === "set_image") return `放入图${op.image_index || "?"}（新建/接入图像节点）`;
    if (op.action === "replace_output") {
      return op.kind === "image"
        ? `输出口替换为图${op.image_index || "?"}（重接下游）`
        : `输出口替换为文本：${String(op.value ?? "")}`;
    }
    return `写入：${String(op.value ?? "")}`;
  };
  return (
    <div className="msg-bot">
      <div className="bot-avatar">
        <Workflow size={18} />
      </div>
      <div className="bot-content" style={{ width: "100%" }}>
        <div style={{ marginBottom: 8 }}>
          <strong>工作流输入口编排</strong>
          {plan.status === "applied" && (
            <span style={{ color: "#3a9e5b", fontSize: 12, marginLeft: 8 }}>已应用</span>
          )}
          {plan.status === "ignored" && (
            <span style={{ color: "var(--text-muted)", fontSize: 12, marginLeft: 8 }}>已忽略</span>
          )}
        </div>
        {plan.summary && (
          <p style={{ fontSize: 13, margin: "0 0 8px" }}>{plan.summary}</p>
        )}
        {plan.ops.length === 0 ? (
          <p style={{ color: "#c98a1a", fontSize: 13 }}>
            AI 未给出可自动执行的操作（可能需要手动在画布里处理，见上面说明）。
          </p>
        ) : (
          <ul style={{ margin: "0 0 10px", paddingLeft: 18, fontSize: 13 }}>
            {plan.ops.map((op, i) => {
              const editable = plan.status === "pending" && onEditOp && isEditableText(op);
              return (
                <li key={`${op.node_id}-${op.action}-${op.input || op.output || i}`} style={{ marginBottom: 4 }}>
                  <code>#{op.node_id} · {op.action === "replace_output" ? op.output : op.input}</code>
                  {editable ? (
                    <>
                      {" → 写入（可编辑）："}
                      <textarea
                        className="ports-op-edit"
                        value={String(op.value ?? "")}
                        onChange={(e) => onEditOp!(i, e.target.value)}
                        rows={(() => {
                          // 长提示词多为无换行长句：按显式换行数 + 字符折行估算（约 48 字/行），
                          // 数字/短值仍是 1~2 行，长文本自动撑高，最多 12 行避免过长。
                          const s = String(op.value ?? "");
                          const byNewline = s.split("\n").length;
                          const byLength = Math.ceil(s.length / 48);
                          return Math.min(12, Math.max(1, byNewline, byLength));
                        })()}
                        style={{ width: "100%", marginTop: 4, fontSize: 12, fontFamily: "inherit",
                          padding: "6px 8px", borderRadius: 6, border: "1px solid var(--border)",
                          background: "var(--bg)", color: "var(--text)", resize: "vertical",
                          lineHeight: 1.5, boxSizing: "border-box" }}
                      />
                    </>
                  ) : (
                    <> → {actionLabel(op)}</>
                  )}
                  {op.reason && (
                    <span style={{ color: "var(--text-muted)" }}>　{op.reason}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {plan.status === "pending" && plan.ops.length > 0 && (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn primary" onClick={onApply}>
              应用到画布
            </button>
            <button className="btn" onClick={onIgnore}>
              忽略
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
