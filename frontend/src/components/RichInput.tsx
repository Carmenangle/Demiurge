import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { Plus, X } from "lucide-react";
import { clampSelectionScroll } from "../lib/contextManagement";
import { classifyClipboardPaste } from "../lib/richPaste";
import {
  buildFileAttachmentText, isTextFile,
  readFileAsDataURL, readFileAsText,
} from "../lib/fileAttach";
import { serializeInspirationSend, type InspirationAttachment } from "../lib/inspirationInsert";

// 序列化结果：图片在上、文本在下两层。parts 保留兼容（图片在前、文本在后）。
export interface MaskedImageInput {
  image: string;
  mask: string;
  preview: string;
}

export interface RichContent {
  parts: {
    type: "text" | "image" | "masked-image";
    text?: string;
    url?: string;
    image?: string;
    mask?: string;
  }[];
  text: string;       // 纯文本（用于指令解析与回显）
  images: string[];   // 所有图片 URL（dataURI/http），按上方栏从左到右顺序
  maskedImage?: MaskedImageInput;
  /** 灵感卡附件（9:16 卡片）：发送时图文拆分，编辑回填时据此还原卡片形态 */
  inspirationAttachments?: InspirationAttachment[];
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
  onSubmit: (content: RichContent) => void;
  onTextChange?: (text: string) => void;  // 文本变化（供外部感知，可选）
  onCanSubmitChange?: (can: boolean) => void;  // 可提交状态变化（文本或图片任一非空），驱动发送按钮
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

// 当前光标所在文本的「活动段」：按 " + " 分隔取最后一段，解析 /cmd arg
function parseActiveSeg(text: string): { cmd: string; arg: string } | null {
  const idx = text.lastIndexOf(" + ");
  const seg = idx < 0 ? text : text.slice(idx + 3);
  const m = /^\/(\w+)\s*(.*)$/.exec(seg);
  return m ? { cmd: m[1].toLowerCase(), arg: m[2] } : null;
}

export const RichInput = forwardRef<RichInputHandle, Props>(
  ({ templateNames, height, placeholder, onSubmit, onTextChange, onCanSubmitChange }, ref) => {
    const taRef = useRef<HTMLTextAreaElement | null>(null);
    const fileRef = useRef<HTMLInputElement | null>(null);  // 上方 + 按钮的隐藏 file input
    const [images, setImages] = useState<string[]>([]);     // 图片栏：dataURI/URL，左到右
    const [inspCards, setInspCards] = useState<InspirationAttachment[]>([]); // 灵感卡附件（9:16 卡片）
    const [maskedImage, setMaskedImage] = useState<MaskedImageInput | null>(null);
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

    // 上方 + 按钮选图
    const onPickFiles = (files: FileList | null) => {
      if (!files) return;
      Array.from(files).forEach((f) => {
        if (!f.type.startsWith("image/") && !isTextFile(f)) return;
        if (f.type.startsWith("image/")) {
          const reader = new FileReader();
          reader.onload = () => addImage(String(reader.result || ""));
          reader.readAsDataURL(f);
        } else {
          void applyFile(f);
        }
      });
    };

    const doSubmitRef = useRef<() => void>(() => {});

    // 可提交 = 文本非空 或 有图片 或 有灵感卡。文本或图片任一变化都上报，驱动外部发送按钮启用/禁用。
    useEffect(() => {
      onCanSubmitChange?.(curText.trim().length > 0 || images.length > 0 || !!maskedImage || inspCards.length > 0);
    }, [curText, images, maskedImage, inspCards, onCanSubmitChange]);

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

    // 文件拖拽/选择：图片走图片栏，文本文件以「文件参考」块插入（agent 全链路可参考）
    const applyFile = async (file: File) => {
      if (file.type.startsWith("image/") && !isTextFile(file)) {
        addImage(await readFileAsDataURL(file));
        return;
      }
      if (isTextFile(file)) {
        const content = await readFileAsText(file);
        insertAtCursor(`
${buildFileAttachmentText(file.name, content)}
`);
      }
    };
    const onDropFiles = (e: React.DragEvent) => {
      const files = Array.from(e.dataTransfer?.files || []);
      if (!files.length) return;
      e.preventDefault();
      void (async () => { for (const f of files) await applyFile(f); })();
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
      if (!finalText && finalImages.length === 0 && !maskedImage) return;
      const parts: RichContent["parts"] = [
        ...finalImages.map((url) => ({ type: "image" as const, url })),
        ...(maskedImage ? [{
          type: "masked-image" as const,
          url: maskedImage.preview,
          image: maskedImage.image,
          mask: maskedImage.mask,
        }] : []),
        ...(finalText ? [{ type: "text" as const, text: finalText }] : []),
      ];
      onSubmit({
        parts,
        text: finalText,
        images: finalImages,
        ...(maskedImage ? { maskedImage } : {}),
        ...(inspCards.length > 0 ? { inspirationAttachments: [...inspCards] } : {}),
      });
      setImages([]);
      setInspCards([]);
      setMaskedImage(null);
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


    // 粘贴：图片(文件/截图/直链/对话里复制的生成图) → 加入上方图片栏；纯文本放行 textarea 默认。
    const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const html = e.clipboardData.getData("text/html");
      const imgItem = Array.from(e.clipboardData.items).find(
        (it) => it.type.startsWith("image/"),
      );
      const file = imgItem?.getAsFile() || null;
      const text = e.clipboardData.getData("text/plain");
      const intent = classifyClipboardPaste({
        text,
        html,
        hasImageFile: Boolean(file && file.size > 0),
      });
      if (intent.kind === "text") return;

      e.preventDefault();
      if (intent.kind === "image-file") {
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => addImage(String(reader.result || ""));
        reader.readAsDataURL(file);
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
            accept="image/*,.md,.txt,.json,.csv,.log,.yaml,.yml,.xml,.html,.ts,.py,.srt,.ass"
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
