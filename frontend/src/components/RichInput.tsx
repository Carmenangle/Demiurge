import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { File as FileIcon, Film, Music, Plus, X } from "lucide-react";
import { clampSelectionScroll } from "../lib/contextManagement";
import { classifyClipboardPaste } from "../lib/richPaste";
import { uploadAttachment, type FileAttachmentMeta } from "../api/ai";
import { readFileAsDataURL } from "../lib/fileAttach";
import { serializeInspirationSend, type InspirationAttachment } from "../lib/inspirationInsert";

// 序列化结果：图片在上、文本在下两层。parts 保留兼容（图片在前、文本在后）。
export interface MaskedImageInput {
  image: string;
  mask: string;
  preview: string;
}

export interface RichContent {
  parts: Array<
    | { type: "text"; text?: string }
    | { type: "image"; url?: string }
    | { type: "masked-image"; url?: string; image?: string; mask?: string }
    // 通用文件/媒体附件（type=file）：fileId 真源，D1 历史回放 ChatMessages.FileAttachmentChip
    // 据此渲染文件名+大小+下载按钮。修复 2026-09-01 bug：原本 RichContent.parts 类型不含 file，
    // attachments 仅放顶层 content.attachments 字段 → userMsg.parts 缺 file → 用户消息不显示附件。
    | { type: "file"; fileId: string; name: string; mime: string; size: number }
  >;
  text: string;       // 纯文本（用于指令解析与回显）
  images: string[];   // 所有图片 URL（dataURI/http），按上方栏从左到右顺序
  maskedImage?: MaskedImageInput;
  /** 灵感卡附件（9:16 卡片）：发送时图文拆分，编辑回填时据此还原卡片形态 */
  inspirationAttachments?: InspirationAttachment[];
  /** 通用文件附件（A1 第三栏：任意类型文件，上传后持 file_id 元信息随消息透传 agent） */
  attachments?: FileAttachmentMeta[];
}

export interface RichInputHandle {
  insertImage: (url: string) => void;  // 追加一张图片到上方图片栏末尾
  insertMaskedImage: (value: MaskedImageInput) => void; // 插入原图+独立蒙版绑定附件
  insertInspirationCard: (card: InspirationAttachment) => void; // 插入灵感卡附件（9:16 卡片，发送时图文拆分）
  insertText: (text: string) => void;  // 在文本框光标处插入文本
  replaceContent: (content: RichContent) => void;  // 用一份完整图文内容替换当前草稿
  submit: () => void;                  // 触发提交（外部发送按钮用）
  focus: () => void;
}

interface Props {
  templateNames: string[];
  height?: number;
  placeholder?: string;
  /** 当前会话 thread_id（附件上传目标会话，默认 "home"） */
  threadId?: string;
  onSubmit: (content: RichContent) => void;
  onTextChange?: (text: string) => void;  // 文本变化（供外部感知，可选）
  onCanSubmitChange?: (can: boolean) => void;  // 可提交状态变化（文本或图片任一非空），驱动发送按钮
  onNotify?: (msg: string, kind: "info" | "error" | "success") => void;  // 上传失败等轻提示
}

interface CmdCandidate {
  value: string;
  label: string;
  hint?: string;
}

// 是否是视频地址（mp4/webm/mov/mkv）：这些用 <video> 渲染，gif/webp 仍当图片。
function isVideoUrl(url: string): boolean {
  const path = url.split(/[?#]/)[0].toLowerCase();
  return /\.(mp4|webm|mov|mkv)$/.test(path);
}

// 文件大小人类可读：B/KB/MB/GB
function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const idx = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** idx;
  return `${value >= 100 || idx === 0 ? Math.round(value) : value.toFixed(1)} ${units[idx]}`;
}

// 附件占位卡唯一 id：优先 crypto.randomUUID（安全上下文），否则回退时间戳+随机数
// （非 localhost/HTTPS 访问（如局域网 IP）时 randomUUID 不可用，直接调用会 TypeError）。
function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// 当前光标所在文本的「活动段」：按 " + " 分隔取最后一段，解析 /cmd arg
function parseActiveSeg(text: string): { cmd: string; arg: string } | null {
  const idx = text.lastIndexOf(" + ");
  const seg = idx < 0 ? text : text.slice(idx + 3);
  const m = /^\/(\w+)\s*(.*)$/.exec(seg);
  return m ? { cmd: m[1].toLowerCase(), arg: m[2] } : null;
}

export const RichInput = forwardRef<RichInputHandle, Props>(
  ({ templateNames, height, placeholder, threadId = "home", onSubmit, onTextChange, onCanSubmitChange, onNotify }, ref) => {
    const taRef = useRef<HTMLTextAreaElement | null>(null);
    const fileRef = useRef<HTMLInputElement | null>(null);  // 上方 + 按钮的隐藏 file input
    const [images, setImages] = useState<string[]>([]);     // 图片栏：dataURI/URL，左到右
    const [inspCards, setInspCards] = useState<InspirationAttachment[]>([]); // 灵感卡附件（9:16 卡片）
    const [maskedImage, setMaskedImage] = useState<MaskedImageInput | null>(null);
    const [media, setMedia] = useState<FileAttachmentMeta[]>([]);       // 媒体栏：视频/音频（A1 第二栏）
    const [attachments, setAttachments] = useState<FileAttachmentMeta[]>([]); // 通用文件栏（A1 第三栏）
    const [active, setActive] = useState(0);
    const [closed, setClosed] = useState(false);
    const [curText, setCurText] = useState("");  // 当前纯文本（驱动补全）
    const [preview, setPreview] = useState<string | null>(null);  // 悬停放大预览
    const [dragIdx, setDragIdx] = useState<number | null>(null);  // 拖拽中的图片索引（排序）
    const selectingRef = useRef(false);
    const selectionScrollRef = useRef({ top: 0, time: 0 });
    const correctingScrollRef = useRef(false);

    useEffect(() => {
      const stopSelecting = () => { selectingRef.current = false; };
      window.addEventListener("pointerup", stopSelecting);
      window.addEventListener("pointercancel", stopSelecting);
      window.addEventListener("blur", stopSelecting);
      return () => {
        window.removeEventListener("pointerup", stopSelecting);
        window.removeEventListener("pointercancel", stopSelecting);
        window.removeEventListener("blur", stopSelecting);
      };
    }, []);

    const onSelectionStart = (e: React.PointerEvent<HTMLTextAreaElement>) => {
      if (e.button !== 0) return;
      selectingRef.current = true;
      selectionScrollRef.current = { top: e.currentTarget.scrollTop, time: performance.now() };
    };

    const onSelectionScroll = (e: React.UIEvent<HTMLTextAreaElement>) => {
      const textarea = e.currentTarget;
      if (!selectingRef.current) return;
      if (correctingScrollRef.current) {
        correctingScrollRef.current = false;
        selectionScrollRef.current = { top: textarea.scrollTop, time: performance.now() };
        return;
      }
      const now = performance.now();
      const previous = selectionScrollRef.current;
      const next = clampSelectionScroll(previous.top, textarea.scrollTop, now - previous.time);
      if (next !== textarea.scrollTop) {
        correctingScrollRef.current = true;
        textarea.scrollTop = next;
      }
      selectionScrollRef.current = { top: next, time: now };
    };

    // 拖拽重排：把 dragIdx 处的图移动到 toIdx 前
    const reorder = (from: number, to: number) => {
      if (from === to) return;
      setImages((arr) => {
        const next = [...arr];
        const [moved] = next.splice(from, 1);
        next.splice(to, 0, moved);
        return next;
      });
    };


    // 追加一张图片到图片栏末尾（去重：同一 url 不重复加）
    const addImage = (url: string) => {
      if (!url) return;
      setImages((arr) => (arr.includes(url) ? arr : [...arr, url]));
    };
    const removeImage = (url: string) => setImages((arr) => arr.filter((u) => u !== url));
    const removeInspCard = (id: string) => setInspCards((arr) => arr.filter((c) => c.id !== id));

    // 上方 + 按钮选文件：图片/媒体/任意文件都收，accept 仅作系统选择器提示（不再白名单静默丢弃）
    const onPickFiles = (files: FileList | null) => {
      if (!files) return;
      Array.from(files).forEach((f) => {
        void applyFile(f);
      });
    };

    const doSubmitRef = useRef<() => void>(() => {});

    // 可提交 = 文本非空 或 有图片/灵感卡/蒙版 或 有附件。任一变化都上报，驱动外部发送按钮启用/禁用。
    useEffect(() => {
      onCanSubmitChange?.(curText.trim().length > 0 || images.length > 0 || !!maskedImage || inspCards.length > 0 || media.length > 0 || attachments.length > 0);
    }, [curText, images, maskedImage, inspCards, media, attachments, onCanSubmitChange]);

    useImperativeHandle(ref, () => ({
      insertImage: (url: string) => addImage(url),
      insertMaskedImage: (value: MaskedImageInput) => setMaskedImage(value),
      insertInspirationCard: (card: InspirationAttachment) => {
        if (!card) return;
        setInspCards((arr) => (arr.some((c) => c.id === card.id) ? arr : [...arr, card]));
      },
      insertText: (text: string) => insertAtCursor(text),
      replaceContent: (content: RichContent) => {
        const text = content.text || "";
        setImages([...new Set(content.images.filter(Boolean))]);
        setMaskedImage(content.maskedImage || null);
        setInspCards(content.inspirationAttachments ? [...content.inspirationAttachments] : []);
        setMedia([]);
        setAttachments(content.attachments ? [...content.attachments] : []);
        setCurText(text);
        setClosed(false);
        setActive(0);
        onTextChange?.(text);
        requestAnimationFrame(() => {
          const ta = taRef.current;
          if (!ta) return;
          ta.focus();
          ta.selectionStart = ta.selectionEnd = text.length;
        });
      },
      submit: () => doSubmitRef.current(),
      focus: () => taRef.current?.focus(),
    }));

    // 光标处插入文本（insertText handle 与文件附件共用）
    const insertAtCursor = (text: string) => {
      const ta = taRef.current;
      if (!ta) return;
      const s = ta.selectionStart ?? ta.value.length;
      const e = ta.selectionEnd ?? ta.value.length;
      const next = ta.value.slice(0, s) + text + ta.value.slice(e);
      setCurText(next);
      onTextChange?.(next);
      requestAnimationFrame(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = s + text.length; });
    };

    // 移除媒体/通用文件附件（按 file_id 定位，上传失败占位卡也用唯一 id 移除）
    const removeMedia = (fileId: string) => setMedia((arr) => arr.filter((a) => a.fileId !== fileId));
    const removeAttachment = (fileId: string) => setAttachments((arr) => arr.filter((a) => a.fileId !== fileId));

    // 通用文件/媒体：上传到会话级附件存储，持 file_id 元信息（A1/B1）。失败移除占位并轻提示。
    const uploadAsAttachment = async (file: File, kind: "media" | "file") => {
      const pendingId = `pending-${randomId()}`;
      const pending: FileAttachmentMeta = {
        fileId: pendingId, name: file.name, mime: file.type || "application/octet-stream", size: file.size,
      };
      if (kind === "media") setMedia((arr) => [...arr, pending]);
      else setAttachments((arr) => [...arr, pending]);
      try {
        const meta = await uploadAttachment(threadId, file);
        // meta 已是完整 FileAttachmentMeta（uploadAttachment 负责 snake→camel 映射），整体替换占位卡。
        const next = (arr: FileAttachmentMeta[]) =>
          arr.map((a) => (a.fileId === pendingId ? meta : a));
        if (kind === "media") setMedia((arr) => next(arr));
        else setAttachments((arr) => next(arr));
      } catch (error) {
        if (kind === "media") removeMedia(pendingId);
        else removeAttachment(pendingId);
        onNotify?.(
          `附件「${file.name}」上传失败：${error instanceof Error ? error.message : String(error)}`,
          "error",
        );
      }
    };

    // 文件拖拽/粘贴/选择统一入口：图片 → 图片栏（vision 多模态），视频/音频 → 媒体栏，其余 → 通用文件栏。
    // 图片不重灌 token 不随消息透传（沿用插画红线）；媒体/文件上传后仅持元信息，agent 按类型消化。
    const applyFile = async (file: File) => {
      if (file.type.startsWith("image/")) {
        addImage(await readFileAsDataURL(file));
        return;
      }
      if (file.type.startsWith("video/") || file.type.startsWith("audio/")) {
        void uploadAsAttachment(file, "media");
        return;
      }
      void uploadAsAttachment(file, "file");
    };
    const onDropFiles = (e: React.DragEvent) => {
      const files = Array.from(e.dataTransfer?.files || []);
      if (!files.length) return;
      e.preventDefault();
      void (async () => {
        for (const f of files) {
          try { await applyFile(f); } catch { /* 单个文件失败不中断其余 */ }
        }
      })();
    };

    const onTextInput = (v: string) => {
      setClosed(false);
      setActive(0);
      setCurText(v);
      onTextChange?.(v);
    };

    // 补全候选：/w 选模板、/a 编排指定模板（都补全模板名）；其余交给智能体，无需指令
    const candidates = useMemo<CmdCandidate[]>(() => {
      const p = parseActiveSeg(curText);
      if (!p) return [];
      const filter = p.arg.toLowerCase();
      if (p.cmd === "w" || p.cmd === "a") {
        const hint = p.cmd === "w" ? "工作流" : "AI 编排";
        return templateNames
          .filter((n) => n.toLowerCase().includes(filter))
          .map((n) => ({ value: n, label: n, hint }));
      }
      return [];
    }, [curText, templateNames]);

    const open = candidates.length > 0 && !closed;

    // 确认补全：把当前段尾部 arg 替换为候选值（纯文本，图片独立不受影响）。
    const confirmPick = (cand: CmdCandidate) => {
      const p = parseActiveSeg(curText);
      if (!p) return;
      const idx = curText.lastIndexOf(" + ");
      const head = idx < 0 ? "" : curText.slice(0, idx + 3);
      const next = `${head}/${p.cmd} ${cand.value}`;
      setCurText(next);
      onTextChange?.(next);
      setClosed(true);
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (ta) { ta.focus(); ta.selectionStart = ta.selectionEnd = next.length; }
      });
    };

    const doSubmit = () => {
      const text = curText.trim();
      // ★ 灵感卡附件：图文拆分发送——封面图作为图片参数上传，title/content 转成
      //   Agent 语义文本（「灵感参考」身份标记），追加在用户文本之后。
      const { text: finalText, images: finalImages } = serializeInspirationSend(inspCards, text, images);
      // D1 回放过滤 pending：未上传成功的占位卡（fileId="pending-..."）不入 parts 与顶层 attachments，
      // 避免 FileAttachmentChip 显示「不可用」+ 后端 file_reference_blocks 收到无效 fileId。
      const allAttachments = [...media, ...attachments].filter(
        (a) => !String(a.fileId || "").startsWith("pending-") && a.fileId,
      );
      if (!finalText && finalImages.length === 0 && !maskedImage && allAttachments.length === 0) return;
      const parts = buildSubmitParts({
        text: finalText,
        images: finalImages,
        ...(maskedImage ? { maskedImage } : {}),
        attachments: allAttachments,
      });
      onSubmit({
        parts,
        text: finalText,
        images: finalImages,
        ...(maskedImage ? { maskedImage } : {}),
        ...(inspCards.length > 0 ? { inspirationAttachments: [...inspCards] } : {}),
        ...(allAttachments.length > 0 ? { attachments: allAttachments } : {}),
      });
      setImages([]);
      setInspCards([]);
      setMaskedImage(null);
      setMedia([]);
      setAttachments([]);
      setCurText("");
      onTextChange?.("");
    };
    doSubmitRef.current = doSubmit;

    const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (open) {
        if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i + 1) % candidates.length); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i - 1 + candidates.length) % candidates.length); return; }
        if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); confirmPick(candidates[active]); return; }
        if (e.key === "Escape") { e.preventDefault(); setClosed(true); return; }
      }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSubmit(); }
    };


    // 粘贴：文件（Ctrl+C/V 复制的文件、截图）→ 按类型入栏；纯文本放行 textarea 默认。
    // 关键修复：遍历 clipboardData.files（含文件管理器复制的任意文件），不再只查 items 里的图片。
    // 2026-09-01 修复：混合内容（文本+图片）现在**两者都保留**——图片进图片栏，文本插入输入框。
    // ⚠ 分支顺序红线：text-with-image-file 判断必须在 pastedFiles 文件分支**之前**——混合粘贴时
    // clipboardData.files 非空，若先走文件分支会 preventDefault + return，文本被静默丢弃
    // （2026-09-01 e2e 实测捕获：textarea 为空）。
    const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const html = e.clipboardData.getData("text/html");
      const imageItems = Array.from(e.clipboardData.items).filter(
        (it) => it.type.startsWith("image/"),
      );
      const firstFile = imageItems.length > 0 ? imageItems[0].getAsFile() : null;
      const text = e.clipboardData.getData("text/plain");
      const pastedFiles = Array.from(e.clipboardData.files || []);
      const intent = classifyClipboardPaste({
        text,
        html,
        hasImageFile: Boolean(firstFile && firstFile.size > 0),
      });

      // 混合内容（有意义文本 + 图片文件）：两者都保留（必须在文件分支之前）。
      if (intent.kind === "text-with-image-file") {
        e.preventDefault();
        // 混合：所有 image items 进图片栏（多张都加），文本插入光标处。
        for (const item of imageItems) {
          const f = item.getAsFile();
          if (!f || f.size === 0) continue;
          const reader = new FileReader();
          reader.onload = () => addImage(String(reader.result || ""));
          reader.readAsDataURL(f);
        }
        if (text) insertAtCursor(text);
        return;
      }
      // 纯文件（无文本）：文件管理器复制/Ctrl+C 复制的文件 → 按类型入栏（图片→图片栏，媒体→媒体栏，其余→文件栏）。
      if (pastedFiles.length > 0) {
        e.preventDefault();
        void (async () => {
          for (const f of pastedFiles) {
            try { await applyFile(f); } catch { /* 单个文件失败不中断其余 */ }
          }
        })();
        return;
      }
      // 纯文本：放行 textarea 默认插入。
      if (intent.kind === "text") return;
      e.preventDefault();
      if (intent.kind === "image-file") {
        if (!firstFile) return;
        const reader = new FileReader();
        reader.onload = () => addImage(String(reader.result || ""));
        reader.readAsDataURL(firstFile);
        return;
      }
      addImage(intent.url);
    };


    return (
      <div style={{ position: "relative" }}
        onDragOver={(e) => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); }}
        onDrop={onDropFiles}
      >
        {open && (
          <div className="cmd-popup">
            {candidates.map((c, i) => (
              <div
                key={c.value}
                className={`cmd-item ${i === active ? "active" : ""}`}
                onMouseDown={(e) => { e.preventDefault(); confirmPick(c); }}
                onMouseEnter={() => setActive(i)}
              >
                <span>{c.label}</span>
                {c.hint && <span className="cmd-hint">{c.hint}</span>}
              </div>
            ))}
          </div>
        )}
        {/* 上方图片栏：+ 按钮固定最左，图片从左到右横排，各带删除 */}
        <div className="rich-imgbar">
          <input
            ref={fileRef}
            type="file"
            accept="image/*,video/*,audio/*,.md,.txt,.json,.csv,.log,.yaml,.yml,.xml,.html,.ts,.py,.srt,.ass,.pdf,.docx,.xlsx,.zip,.safetensors,.bin,.ckpt"
            multiple
            style={{ display: "none" }}
            onChange={(e) => { onPickFiles(e.target.files); e.target.value = ""; }}
          />
          <button
            type="button"
            className="rich-imgbar-add"
            title="上传图片"
            onClick={() => fileRef.current?.click()}
          >
            <Plus size={22} />
          </button>
          {images.map((url, idx) => (
            <span
              key={url}
              className={`rich-imgbar-item ${dragIdx === idx ? "dragging" : ""}`}
              draggable
              onDragStart={() => { setDragIdx(idx); setPreview(null); }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); if (dragIdx !== null) reorder(dragIdx, idx); setDragIdx(null); }}
              onDragEnd={() => setDragIdx(null)}
              onMouseEnter={() => setPreview(url)}
              onMouseLeave={() => setPreview(null)}
              title="拖动可排序"
            >
              {/* 视频用 <video> 渲染，gif/图片用 <img>（避免 mp4 走 img 显示裂图） */}
              {isVideoUrl(url) ? (
                <video src={url} muted playsInline draggable={false} />
              ) : (
                <img src={url} alt="图片" draggable={false} />
              )}
              <button
                type="button"
                className="rich-imgbar-del"
                title="移除"
                onClick={() => { setPreview(null); removeImage(url); }}
              >
                <X size={12} />
              </button>
            </span>
          ))}
          {/* 灵感卡附件：9:16 卡片（封面图 / 纯文本占位），发送时图文拆分 */}
          {inspCards.map((card) => (
            <span
              key={card.id}
              className="rich-imgbar-item rich-imgbar-insp"
              title={card.content || card.title || "灵感卡"}
              onMouseEnter={() => card.imageUrl && setPreview(card.imageUrl)}
              onMouseLeave={() => setPreview(null)}
            >
              <div className="rich-imgbar-insp-body">
                {card.imageUrl ? (
                  <img src={card.imageUrl} alt={card.title || "灵感卡封面"} draggable={false} />
                ) : (
                  <div className="rich-imgbar-insp-empty">9:16</div>
                )}
                <div className="rich-imgbar-insp-label">{card.title || "灵感卡"}</div>
              </div>
              <button
                type="button"
                className="rich-imgbar-del"
                title="移除灵感卡"
                onClick={() => { setPreview(null); removeInspCard(card.id); }}
              >
                <X size={12} />
              </button>
            </span>
          ))}
          {maskedImage && (
            <span
              className="rich-imgbar-item rich-imgbar-masked"
              onMouseEnter={() => setPreview(maskedImage.preview)}
              onMouseLeave={() => setPreview(null)}
              title="原图与蒙版绑定附件"
            >
              <img src={maskedImage.preview} alt="蒙版预览" draggable={false} />
              <span className="rich-imgbar-mask-badge">蒙版</span>
              <button
                type="button"
                className="rich-imgbar-del"
                title="移除"
                onClick={() => { setPreview(null); setMaskedImage(null); }}
              >
                <X size={12} />
              </button>
            </span>
          )}
        </div>
        {/* 媒体栏（A1 第二栏）：视频/音频附件卡。上传中显示占位，完成后持 file_id 元信息随消息透传 */}
        {media.length > 0 && (
          <div className="rich-filebar">
            {media.map((m) => (
              <span key={m.fileId} className="rich-filebar-item" title={m.name}>
                {String(m.fileId).startsWith("pending-")
                  ? <span className="bot-spinner" />
                  : m.mime?.startsWith("video/")
                    ? <Film size={18} className="rich-filebar-icon" />
                    : <Music size={18} className="rich-filebar-icon" />}
                <span className="rich-filebar-name">{m.name}</span>
                <span className="rich-filebar-size">
                  {String(m.fileId).startsWith("pending-") ? "上传中…" : formatFileSize(m.size)}
                </span>
                <button
                  type="button"
                  className="rich-imgbar-del"
                  title="移除"
                  onClick={() => removeMedia(m.fileId)}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        {/* 通用文件栏（A1 第三栏）：任意类型文件（md/code/office/pdf/zip/模型…），上传后持元信息 */}
        {attachments.length > 0 && (
          <div className="rich-filebar">
            {attachments.map((a) => (
              <span key={a.fileId} className="rich-filebar-item" title={a.name}>
                {String(a.fileId).startsWith("pending-")
                  ? <span className="bot-spinner" />
                  : <FileIcon size={18} className="rich-filebar-icon" />}
                <span className="rich-filebar-name">{a.name}</span>
                <span className="rich-filebar-size">
                  {String(a.fileId).startsWith("pending-") ? "上传中…" : formatFileSize(a.size)}
                </span>
                <button
                  type="button"
                  className="rich-imgbar-del"
                  title="移除"
                  onClick={() => removeAttachment(a.fileId)}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        {/* 下方纯文本输入 */}
        <textarea
          ref={taRef}
          className="rich-input"
          rows={4}
          style={height ? { height } : undefined}
          placeholder={placeholder}
          value={curText}
          onChange={(e) => onTextInput(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onDrop={onDropFiles}
          onPointerDown={onSelectionStart}
          onScroll={onSelectionScroll}
        />
        {/* 悬停放大预览：独立元素，不占布局。仅当图仍在栏内才显示，防删除后悬空卡住 */}
        {preview && (images.includes(preview) || maskedImage?.preview === preview) && (
          isVideoUrl(preview)
            ? <video className="rich-chip-preview" src={preview} muted autoPlay loop playsInline />
            : <img className="rich-chip-preview" src={preview} alt="预览" />
        )}
      </div>
    );
  },
);

// 纯函数：构造 doSubmit 的 RichContent.parts。
// **D1 历史回放关键**——通用文件/媒体附件（attachments 数组）必须**转成 type="file" 的 part**，
// 写入 userMsg.parts，ChatMessages.FileAttachmentChip 据此渲染文件名+大小+下载按钮。
// 历史教训（2026-09-01 用户报告）：RichInput.doSubmit 早期只把 attachments 放顶层 `content.attachments`，
// userMsg.parts 缺 file part → ChatMessages 走 else 分支只渲染 msg.text → 用户看到消息里"只有文本+图，
// 没有附件元信息"。fix：file part 在此构造，由 useChatSession.runFreeText 的 `content?.parts || fallback`
// 直接透传（不会重复拼接）。
// pending-* 占位卡由调用方在传入前过滤——本函数不再二次过滤（保证单元测试可观测纯净入参）。
export function buildSubmitParts(input: {
  text: string;
  images: string[];
  maskedImage?: MaskedImageInput;
  attachments: FileAttachmentMeta[];
}): RichContent["parts"] {
  const fileParts: RichContent["parts"] = input.attachments.map((a) => ({
    type: "file" as const,
    fileId: a.fileId,
    name: a.name,
    mime: a.mime,
    size: a.size,
  }));
  return [
    ...input.images.map((url) => ({ type: "image" as const, url })),
    ...(input.maskedImage ? [{
      type: "masked-image" as const,
      url: input.maskedImage.preview,
      image: input.maskedImage.image,
      mask: input.maskedImage.mask,
    }] : []),
    ...fileParts,
    ...(input.text ? [{ type: "text" as const, text: input.text }] : []),
  ];
}
