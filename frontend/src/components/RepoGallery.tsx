import { useEffect, useRef, useState } from "react";
import { Check, ExternalLink, Images, Plus, Search, Send, Sparkles, Trash2, X } from "lucide-react";
import { activeChatModel, useSettings } from "../stores/settings";
import { listGenerations, deleteDoc, setTags, describeImage, tagStats, pruneGenerations, type Generation } from "../api/ai";
import { ConfirmModal, AlertModal } from "./Modal";
import { CopyButton } from "./CopyButton";
import { Pager } from "./Pager";

// 合并展示时给每张图附带来源仓库 id（资产库按仓库名搜索用）
type GenWithRepo = Generation & { repoId?: string };

// 从 image_url 里解析顺序编号做时间序：每仓库顺序命名 000001.png…（越大越新）；
// 旧图 anima1_00073_.png 取尾部数字兜底；都取不到返回 0。
function seqOf(url: string): number {
  const name = decodeURIComponent(url.split("path=").pop() || url).replace(/\\/g, "/").split("/").pop() || "";
  const m = name.match(/(\d+)(?=\D*$)/);  // 文件名里最后一段数字
  return m ? parseInt(m[1], 10) : 0;
}

// 单张图的标签编辑条：加/删标签、AI 打标、按标签搜索。
function GalleryTags({
  g, tagging, onAdd, onRemove, onAiTag, onSearch, onAppendSearch, onClear,
}: {
  g: Generation; tagging: boolean;
  onAdd: (t: string) => void; onRemove: (t: string) => void; onAiTag: () => void;
  onSearch: (t: string) => void; onAppendSearch: (t: string) => void; onClear: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [val, setVal] = useState("");
  const [menuTag, setMenuTag] = useState<string | null>(null);  // 当前弹悬浮框的标签
  const rootRef = useRef<HTMLDivElement | null>(null);
  const commit = () => { if (val.trim()) onAdd(val.trim()); setVal(""); setAdding(false); };
  // 回车：存下当前标签后不关闭，清空并留在输入框，接着输下一个（连续加标签）
  const commitContinue = () => { if (val.trim()) onAdd(val.trim()); setVal(""); };

  // 点标签外部（含弹框外）关闭悬浮菜单
  useEffect(() => {
    if (!menuTag) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setMenuTag(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuTag]);

  return (
    <div className="gallery-tags" ref={rootRef} onClick={(e) => e.stopPropagation()}>
      {g.tags.map((t) => (
        <span key={t} className="tag-chip" style={{ position: "relative" }}>
          <span style={{ cursor: "pointer" }} onClick={() => setMenuTag(menuTag === t ? null : t)}>{t}</span>
          <X size={11} style={{ cursor: "pointer", marginLeft: 3 }} onClick={() => onRemove(t)} />
          {menuTag === t && (
            <div className="tag-menu" onClick={(e) => e.stopPropagation()}>
              <button onClick={() => { onSearch(t); setMenuTag(null); }}>🔍 搜索此标签</button>
              <button onClick={() => { onAppendSearch(t); setMenuTag(null); }}>＋ 添加此标签</button>
            </div>
          )}
        </span>
      ))}
      {adding ? (
        <input
          autoFocus
          className="tag-input"
          value={val}
          onChange={(e) => setVal(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitContinue(); } if (e.key === "Escape") { setVal(""); setAdding(false); } }}
          placeholder="标签"
        />
      ) : (
        <button className="tag-add" title="加标签" onClick={() => setAdding(true)}>
          <Plus size={11} />
        </button>
      )}
      <button className="tag-add" title="AI 打标" disabled={tagging} onClick={onAiTag}>
        {tagging ? "…" : <Sparkles size={11} />}
      </button>
      {g.tags.length > 0 && (
        <button className="tag-add" title="清除全部标签" onClick={onClear}>
          <Trash2 size={11} />
        </button>
      )}
    </div>
  );
}

// 仓库图片网格：拉取一组仓库的生成记录（图+提示词），点击看大图与生成参数。
// repoIds 传多个时合并展示（顶层仓库聚合自身 + 所有子仓库的图）。
// 支持：出图后自动刷新、超量折叠、删除资产索引（保留本机文件）。
export function RepoGallery({ repoIds, embed, repoNames, hideTitle, enhanced, onSendToChat }: {
  repoIds: string[];
  embed: ReturnType<typeof useSettings>["settings"]["embedModel"];
  repoNames?: Record<string, string>;
  hideTitle?: boolean;
  enhanced?: boolean;                          // 资产库页开：批量删除/标签统计排序/发送至对话
  onSendToChat?: (g: GenWithRepo) => void;     // 发送至对话（弹框选仓库由上层处理）
}) {
  const { settings } = useSettings();
  const [items, setItems] = useState<GenWithRepo[]>([]);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<Generation | null>(null);
  const [page, setPage] = useState(1);
  const [deleting, setDeleting] = useState<Generation | null>(null);
  const [tagQuery, setTagQuery] = useState("");      // 标签/提示词搜索
  const [tagging, setTagging] = useState<string | null>(null);  // 正在 AI 打标的图 id
  const [alertMsg, setAlertMsg] = useState<string | null>(null); // 打标失败等提示
  const [selMode, setSelMode] = useState(false);     // 批量选择模式（增强页开）
  const [selected, setSelected] = useState<Set<string>>(new Set());  // 选中的图 id
  const [batchDel, setBatchDel] = useState(false);   // 批量删除确认
  const [showTagCloud, setShowTagCloud] = useState(false);  // 标签统计面板
  const [allTags, setAllTags] = useState<{ tag: string; count: number }[]>([]);  // 后端全量标签统计
  const PAGE_SIZE = 32; // 一行 8 个 × 四行

  // AI 打标专用对话模型（与生图主流程用的是同一处配置）
  const chat = activeChatModel(settings);

  const key = repoIds.join(",");
  const reposSet = repoIds;

  const refresh = (retryLeft = 1) => {
    let alive = true;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    setLoading(true);
    Promise.all(repoIds.map((id) =>
      listGenerations(id, embed)
        .then((r) => (r.items || []).map((g) => ({ ...g, repoId: id } as GenWithRepo)))
        .catch(() => [] as GenWithRepo[]),
    ))
      .then((lists) => {
        if (!alive) return;
        // 合并去重（按 id），保留来源仓库 id
        const seen = new Set<string>();
        const merged: GenWithRepo[] = [];
        for (const list of lists) for (const g of list) {
          if (!seen.has(g.id)) { seen.add(g.id); merged.push(g); }
        }
        setItems(merged);
        // 全量标签统计（新增图/改标签后刷新时同步更新，供输入补全）
        if (enhanced) {
          tagStats(repoIds, embed).then((r) => { if (alive) setAllTags(r.items || []); }).catch(() => {});
        }
        // 首次读到空可能是 Chroma 写入尚未可见（刚出图/刚进仓库），短延迟重试一次兜底
        if (merged.length === 0 && retryLeft > 0) {
          retryTimer = setTimeout(() => { if (alive) refresh(retryLeft - 1); }, 800);
        }
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; if (retryTimer) clearTimeout(retryTimer); };
  };

  useEffect(() => {
    const cancel = refresh();
    // 出图后（finalize 入库落定后派发）若属于本组仓库则刷新。
    // 延迟一拍 + 二次刷新兜底：Chroma 写入到可检索有轻微延迟，避免刷太早读不到新图。
    const onSaved = (e: Event) => {
      const rid = (e as CustomEvent).detail as string;
      if (!reposSet.includes(rid)) return;
      refresh();
      setTimeout(() => refresh(), 700);
    };
    window.addEventListener("laf-generation-saved", onSaved);
    return () => { cancel?.(); window.removeEventListener("laf-generation-saved", onSaved); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, embed]);

  // 详情大图弹窗 Esc 关闭
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setActive(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  // 哪个仓库的库要删这条：repoIds 里逐个尝试（删错库会 no-op）。这里用第一个，
  // 因 image_url 全局唯一，delete_doc 会在系统库+该仓库库定位 id；多仓库时用所属仓库。
  // 删一条：优先用图自带 repoId，回退遍历各仓库库
  const deleteOne = async (g: GenWithRepo) => {
    const tryIds = g.repoId ? [g.repoId, ...repoIds] : repoIds;
    for (const rid of tryIds) {
      try { await deleteDoc(g.id, rid, embed, false); return; }
      catch { /* 该库没有则试下一个 */ }
    }
  };
  const doDelete = async (g: GenWithRepo) => {
    await deleteOne(g);
    setDeleting(null);
    setItems((prev) => prev.filter((x) => x.id !== g.id));
  };

  // 批量删除选中项
  const doBatchDelete = async () => {
    setBatchDel(false);
    const ids = new Set(selected);
    const targets = items.filter((x) => ids.has(x.id));
    for (const g of targets) await deleteOne(g);
    setItems((prev) => prev.filter((x) => !ids.has(x.id)));
    setSelected(new Set());
    setSelMode(false);
  };
  const toggleSel = (id: string) =>
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  // 清理裂图：删除指向本机图但磁盘文件已不存在的僵尸记录（多因手动删文件留下）。逐仓库清后刷新。
  const [pruning, setPruning] = useState(false);
  const doPrune = async () => {
    setPruning(true);
    try {
      let total = 0;
      for (const id of repoIds) {
        try { total += (await pruneGenerations(id, embed)).removed; } catch { /* 单库失败不阻断 */ }
      }
      setAlertMsg(total > 0 ? `已清理 ${total} 条裂图记录（磁盘文件已不存在）。` : "没有发现裂图记录。");
      refresh();
    } finally { setPruning(false); }
  };

  // 标签统计：整合所有图的标签，按出现数目降序（供标签云排序/筛选）
  // 标签统计：enhanced 用后端全量 allTags；否则本地聚合当前 items。统一 [标签,数目] 按量降序。
  const tagCounts: [string, number][] = enhanced && allTags.length > 0
    ? allTags.map((s) => [s.tag, s.count] as [string, number])
    : (() => {
        const m = new Map<string, number>();
        for (const g of items) for (const t of g.tags) m.set(t, (m.get(t) || 0) + 1);
        return [...m.entries()].sort((a, b) => b[1] - a[1]);
      })();

  // 更新某图标签：先乐观改本地，再落库（失败回滚）
  const saveTags = async (g: Generation, tags: string[]) => {
    const prev = g.tags;
    setItems((arr) => arr.map((x) => (x.id === g.id ? { ...x, tags } : x)));
    setActive((a) => (a && a.id === g.id ? { ...a, tags } : a));  // 大图弹窗同步
    for (const rid of repoIds) {
      try { await setTags(g.id, rid, tags, embed); return; } catch { /* 试下一个库 */ }
    }
    setItems((arr) => arr.map((x) => (x.id === g.id ? { ...x, tags: prev } : x)));  // 全失败回滚
    setActive((a) => (a && a.id === g.id ? { ...a, tags: prev } : a));
  };
  const addTag = (g: Generation, t: string) => {
    const v = t.trim();
    if (!v || g.tags.includes(v)) return;
    saveTags(g, [...g.tags, v]);
  };
  const removeTag = (g: Generation, t: string) => saveTags(g, g.tags.filter((x) => x !== t));

  // AI 打标：分析图片本身提取标签（始终反推图片），结果追加到现有标签。人工点才触发。
  // 注意：g.image_url 是后端本地代理地址(127.0.0.1)，外部视觉模型访问不到，必须先取回转 dataURI 再喂模型。
  const aiTag = async (g: Generation) => {
    setTagging(g.id);
    try {
      let imgForModel = g.image_url;
      if (!/^data:/i.test(imgForModel)) {
        const blob = await (await fetch(g.image_url)).blob();
        imgForModel = await new Promise<string>((res, rej) => {
          const fr = new FileReader();
          fr.onload = () => res(String(fr.result || ""));
          fr.onerror = () => rej(new Error("读图失败"));
          fr.readAsDataURL(blob);
        });
      }
      const r = await describeImage([imgForModel], chat, "只输出 4-8 个描述画面内容的简短标签（主体/风格/场景/动作等），英文逗号分隔，不要解释、不要句子");
      const got = (r.prompt || "").split(/[,，;；\n]+/).map((s) => s.trim()).filter(Boolean);
      if (got.length === 0) { setAlertMsg("AI 打标没返回有效标签，可能模型不支持视觉，请换支持视觉的对话模型。"); return; }
      const merged = Array.from(new Set([...g.tags, ...got])).slice(0, 16);
      await saveTags(g, merged);
    } catch (e) {
      setAlertMsg(`AI 打标失败：${(e as Error).message}（需支持视觉的对话模型）`);
    }
    finally { setTagging(null); }
  };

  // 标签/提示词搜索：多关键词（空格/逗号/分号分隔），全部命中才显示（AND）。纯前端过滤。
  // 资产库模式额外支持按仓库名搜索：命中仓库名则显示该仓库全部图。
  const terms = tagQuery.toLowerCase().split(/[\s,，;；]+/).filter(Boolean);
  const filtered = terms.length === 0
    ? items
    : items.filter((g) => {
        const repoName = (repoNames?.[(g as GenWithRepo).repoId || ""] || "").toLowerCase();
        const hay = [g.prompt.toLowerCase(), ...g.tags.map((t) => t.toLowerCase()), repoName];
        return terms.every((term) => hay.some((h) => h.includes(term)));
      });
  // 从新到旧：优先按后端权威时间戳 created_at 倒序（不受文件改名/删图影响）；
  // 历史记录无 created_at(=0) 时回退到 image_url 文件名编号（时间戳/旧顺序命名都能取到）。
  const orderKey = (g: Generation) => g.created_at || seqOf(g.image_url);
  const sorted = [...filtered].sort((a, b) => orderKey(b) - orderKey(a));
  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);  // filtered 变短后自愈，避免越界空页
  const shown = sorted.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);

  // 搜索框标签补全：取当前输入最后一段(分隔符后)为前缀，从标签统计里匹配，按图片数降序
  const lastSeg = tagQuery.split(/[,，;；]/).pop()?.trim().toLowerCase() || "";
  const tagSuggest = lastSeg && enhanced
    ? tagCounts.filter(([t]) => t.toLowerCase().includes(lastSeg)).slice(0, 8)
    : [];
  // 选中补全：替换最后一段为选中标签，末尾补分隔符方便接着输
  const pickSuggest = (tag: string) => {
    const head = tagQuery.replace(/[^,，;；]*$/, "");
    setTagQuery(head + tag + "，");
    setPage(1);
  };

  return (
    <div style={{ marginTop: hideTitle ? 0 : 28 }}>
      {!hideTitle && <h3 style={{ margin: "4px 0 12px", fontSize: 15 }}>资产库</h3>}
      {items.length > 0 && (
        <div style={{ position: "relative", marginBottom: 12, maxWidth: 320 }}>
          <Search size={14} style={{ position: "absolute", left: 9, top: 9, color: "var(--text-muted)" }} />
          <input
            style={{ width: "100%", paddingLeft: 28, boxSizing: "border-box" }}
            placeholder={repoNames ? "按标签或仓库名搜索…" : "按标签搜索（回车确认一个标签后自动补「，」接着输下一个）…"}
            value={tagQuery}
            onChange={(e) => { setTagQuery(e.target.value); setPage(1); }}
            onKeyDown={(e) => {
              // 回车确认当前标签：末尾补「，」分隔，方便接着输下一个（已是分隔符结尾则不重复补）
              if (e.key === "Enter") {
                e.preventDefault();
                setTagQuery((q) => (q.trim() && !/[,，;；]\s*$/.test(q) ? q.trimEnd() + "，" : q));
              }
            }}
          />
          {tagSuggest.length > 0 && (
            <div className="tag-suggest-pop">
              {tagSuggest.map(([t, n]) => (
                <button key={t} className="tag-suggest-item" onMouseDown={(e) => { e.preventDefault(); pickSuggest(t); }}>
                  <span>{t}</span>
                  <span className="tag-suggest-count">{n}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {enhanced && items.length > 0 && (
        <div className="page-toolbar" style={{ marginBottom: 12 }}>
          <button className="btn" onClick={() => { setSelMode((v) => !v); setSelected(new Set()); }}>
            {selMode ? "退出多选" : "批量选择"}
          </button>
          {selMode && (
            <>
              <button className="btn danger" disabled={selected.size === 0} onClick={() => setBatchDel(true)}>
                <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />删除所选（{selected.size}）
              </button>
              <button className="btn" onClick={() => setSelected(new Set(shown.map((g) => g.id)))}>选本页</button>
              <button className="btn" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>清除</button>
            </>
          )}
          <button className="btn" onClick={() => setShowTagCloud((v) => !v)}>
            标签统计（{tagCounts.length}）
          </button>
          <button className="btn" onClick={doPrune} disabled={pruning}
            title="删除指向本机图但磁盘文件已不存在的裂图记录（多因手动删文件留下）">
            {pruning ? "清理中…" : "清理裂图"}
          </button>
        </div>
      )}
      {enhanced && showTagCloud && (
        <div className="tag-cloud">
          {tagCounts.length === 0 ? (
            <span style={{ color: "var(--text-muted)", fontSize: 13 }}>还没有任何标签。</span>
          ) : tagCounts.map(([t, n]) => (
            <button key={t} className="tag-cloud-chip"
              title={`点击筛选「${t}」（${n} 张）`}
              onClick={() => { setTagQuery(t); setPage(1); }}>
              {t} <span className="tag-cloud-num">{n}</span>
            </button>
          ))}
        </div>
      )}
      {loading ? (
        <div className="gallery-grid">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="gallery-cell skeleton" style={{ aspectRatio: "1 / 1" }} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <Images size={32} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>该仓库还没有生成记录。出图后会自动出现在这里。</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <Search size={30} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>没有匹配「{tagQuery}」的图片。</p>
        </div>
      ) : (
        <>
          <div className="gallery-grid">
            {shown.map((g) => (
              <div key={g.id} className={`gallery-cell ${selMode && selected.has(g.id) ? "sel" : ""}`} title={g.prompt}>
                <img src={g.image_url} alt="生成图" loading="lazy"
                  onClick={() => selMode ? toggleSel(g.id) : setActive(g)} />
                {selMode ? (
                  <span className={`gallery-check ${selected.has(g.id) ? "on" : ""}`}>
                    {selected.has(g.id) && <Check size={14} />}
                  </span>
                ) : (
                  <>
                    {enhanced && onSendToChat && (
                      <button className="gallery-send" title="发送至对话（选仓库）"
                        onClick={(e) => { e.stopPropagation(); onSendToChat(g); }}>
                        <Send size={13} />
                      </button>
                    )}
                    <button className="gallery-del" title="从资产库删除（保留本机文件）"
                      onClick={(e) => { e.stopPropagation(); setDeleting(g); }}>
                      <Trash2 size={14} />
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>
          <Pager page={curPage} pageCount={pageCount} onPage={setPage} always />
        </>
      )}
      {deleting && (
        <ConfirmModal
          title="删除生成图"
          message="确认从资产库删除这条记录？本机图片文件会保留。"
          confirmText="删除"
          danger
          onConfirm={() => doDelete(deleting)}
          onCancel={() => setDeleting(null)}
        />
      )}
      {batchDel && (
        <ConfirmModal
          title="批量删除"
          message={`确认从资产库删除选中的 ${selected.size} 条记录？本机图片文件会保留。`}
          confirmText="删除"
          danger
          onConfirm={doBatchDelete}
          onCancel={() => setBatchDel(false)}
        />
      )}
      {active && (
        <div className="modal-mask" onClick={() => setActive(null)}>
          <div className="modal" style={{ width: 720, maxHeight: "88vh", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <h3 style={{ margin: 0 }}>生成详情</h3>
              <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={() => setActive(null)}>
                <X size={18} />
              </button>
            </div>
            <div style={{ overflowY: "auto" }}>
              <img src={active.image_url} alt="大图" style={{ width: "100%", borderRadius: 10, marginBottom: 12 }} />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>生成参数 / 提示词</div>
                {active.prompt && <CopyButton text={active.prompt} className="img-tool" />}
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{active.prompt || "（无提示词记录）"}</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>标签</div>
              <GalleryTags
                g={active}
                tagging={tagging === active.id}
                onAdd={(t) => addTag(active, t)}
                onRemove={(t) => removeTag(active, t)}
                onAiTag={() => aiTag(active)}
                onSearch={(t) => { setTagQuery(`${t}，`); setPage(1); setActive(null); }}
                onAppendSearch={(t) => {
                  // 追加到已有搜索词，末尾自带「，」方便继续加下一个标签
                  setTagQuery((q) => (q.trim() ? `${q.trim().replace(/[,，;；]\s*$/, "")}，${t}，` : `${t}，`));
                  setPage(1);
                  setActive(null);
                }}
                onClear={() => saveTags(active, [])}
              />
              <div style={{ marginTop: 12 }}>
                <a className="img-tool" href={active.image_url} target="_blank" rel="noreferrer">
                  <ExternalLink size={14} /> 查看原图
                </a>
              </div>
            </div>
          </div>
        </div>
      )}
      {alertMsg && <AlertModal title="提示" message={alertMsg} onClose={() => setAlertMsg(null)} />}
    </div>
  );
}
