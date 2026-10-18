// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const gallery = readFileSync(new URL("../components/RepoGallery.tsx", import.meta.url), "utf8");
const assetsView = readFileSync(new URL("../views/PlaceholderViews.tsx", import.meta.url), "utf8");
const aiApi = readFileSync(new URL("../api/ai.ts", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../../index.html", import.meta.url), "utf8");
const view = readFileSync(new URL("../views/WebMaterialsView.tsx", import.meta.url), "utf8");
const settings = readFileSync(new URL("../stores/settings.ts", import.meta.url), "utf8");
const syncModal = readFileSync(new URL("../components/RepoSyncModal.tsx", import.meta.url), "utf8");

describe("资产库删除与恢复合同", () => {
  it("删除资产记录时保留本机图片", () => {
    expect(gallery).toContain("deleteDoc(g.id, rid, embed, removeFile)");
    expect(gallery).toContain("本机图片文件会保留");
  });

  it("资产页不得扫描本机目录自动恢复已删除记录", () => {
    expect(assetsView).not.toContain("syncExistingGenerations");
    expect(assetsView).not.toContain("sync-generations");
  });

  it("视觉描述只更新资产索引，语义搜索走 generation 专用端点", () => {
    expect(gallery).toContain("setGenerationDescription");
    expect(gallery).toContain("searchGenerations");
    expect(gallery).toContain("indexVisualGenerations");
    expect(gallery).not.toContain("appendMessage");
  });

  it("发送至对话框（仅图片）与发送至对话（完整配方）两个按钮并存且走 chatAppend", () => {
    // 修复后的 AppBody 不再传空函数：AssetsView 接收两个动作
    expect(assetsView).toContain("onSendToChat");
    expect(assetsView).toContain("onSendAsRecipe");
    // RepoGallery 两个按钮：gallery-send（对话框）+ gallery-send-recipe（对话）
    expect(gallery).toContain("gallery-send-recipe");
    expect(gallery).toContain("发送至对话框");
    expect(gallery).toContain("发送至对话");
    // 发送动作由上层（AppBody）通过 chatAppend 落盘，RepoGallery 本身不直接调用
    expect(gallery).not.toContain("chatAppend");
  });

  it("发送至对话按钮仅在提供 onSendAsRecipe 时显示（向后兼容）", () => {
    expect(gallery).toContain("enhanced && onSendAsRecipe &&");
    expect(gallery).toContain("enhanced && onSendToChat &&");
  });
});

describe("P0-1 资产列表 keyset 分页（RepoGallery 契约）", () => {
  it("消费后端 has_more，翻页游标用列表末条 created_at（beforeTs）", () => {
    expect(gallery).toContain("has_more === true");
    expect(gallery).toContain("beforeTs: cursor");
    expect(gallery).toContain("loadMore");
  });

  it("分页在资产库（单/多仓库）都启用；搜索/筛选触发一次全量（sticky）", () => {
    // 2026-10-18：多仓库改走后端合并 keyset ⇒ 不再按仓库数强制全量
    expect(gallery).toContain("const needFull = !enhanced || fullyLoaded;");
    expect(gallery).toContain("const filtering = !!tagQuery.trim() || semanticMode || mediaFilter !== \"all\";");
  });

  it("切仓库重置回分页浏览态（不沿用上一个仓库的 fullyLoaded）", () => {
    expect(gallery).toContain("prevKeyRef.current !== key");
    expect(gallery).toContain("cancel = refresh(1, false);");
  });

  it("自动描述在分页态先升级全量再执行（不只看已加载页）", () => {
    expect(gallery).toContain("setPendingDescribe(true)");
    expect(gallery).toContain("if (!pendingDescribe || !fullyLoaded || loading) return;");
  });
});

describe("P0-2 缩略图派生层（RepoGallery 契约）", () => {
  it("网格优先缩略图，无 thumbUrl 回落原图", () => {
    expect(gallery).toContain("src={g.thumbUrl || g.image_url}");
  });

  it("详情弹窗仍取原图（thumbUrl 只用于网格）", () => {
    expect(gallery).toContain("<img src={active.image_url}");
  });

  it("工具栏提供补缩略图入口（新图入库时已自动生成，此为历史补生成）", () => {
    expect(gallery).toContain("buildThumbs");
    expect(gallery).toContain("补缩略图");
  });
});

describe("P0-3 WD14 反推打标（RepoGallery 契约）", () => {
  it("WD14 反推作为统一入口的一个模式存在（2026-10-18 起不再单独占一键）", () => {
    expect(gallery).toContain("wd14TagActive");
    expect(gallery).toContain("WD14 本地（纯标签）");
    expect(gallery).toContain('if (tagMode === "wd14")');
  });

  it("反推打标走「快照」而非合并（2026-10-17 定案）：tags＝本次快照结果，另拉快照列表", () => {
    expect(gallery).toContain("await tagGeneration(taggerModel, g.id, repoId, embed)");
    expect(gallery).toContain("tags: result.tags");                       // 不合并旧标签
    expect(gallery).toContain("listTagSnapshots(id, repoId, embed)");    // 打标后刷新版本列表
    expect(gallery).toContain("activateSnapshot");                        // 版本可切换
  });
});

describe("P2-1 素材入库即资产记录（契约）", () => {
  it("Generation 契约带素材来源三字段（可选 ⇒ 兼容无来源的历史记录）", () => {
    expect(aiApi).toContain("source?: string;");
    expect(aiApi).toContain("sourceUrl?: string;");
    expect(aiApi).toContain("sourceMeta?: {");
  });

  it("合成仓库常量前后端同值（web-materials），且注明不進仓库选择器", () => {
    expect(aiApi).toContain('export const WEB_MATERIALS_REPO_ID = "web-materials";');
    expect(aiApi).toContain("WEB_MATERIALS_REPO_ID");
  });

  it("「生成内容」页只聚合真实仓库（上网素材不并入，2026-10-18 用户定案）", () => {
    // 素材 RAG 记录仍在合成库 repo_web-materials（留作上网素材页接检索时复用），
    // 但不进本聚合列表（用户实测：素材混在生成内容里造成混淆）
    expect(assetsView).not.toContain("WEB_MATERIALS_REPO_ID");
    expect(assetsView).not.toContain('[WEB_MATERIALS_REPO_ID]: "上网素材"');
  });

  it("空态判断基于真实仓库（repoIds 不含合成仓库）", () => {
    expect(assetsView).toContain("{repos.length === 0 ? (");
  });

  it("详情面板来源段：source 非空才渲染，渠道/作者/评级/搜索词/来源页", () => {
    expect(gallery).toContain("{active.source ? (");
    expect(gallery).toContain('<DetailSection title="来源">');
    expect(gallery).toContain("active.sourceMeta?.author");
    expect(gallery).toContain("active.sourceMeta?.rating");
    expect(gallery).toContain("active.sourceMeta?.query");
    expect(gallery).toContain("active.sourceUrl");
  });
});

describe("多仓库增量加载（2026-10-18）", () => {
  it("listGenerations 支持数组作用域（发 repo_ids 走后端合并分页）", () => {
    expect(aiApi).toContain("repo_ids: repoId");
    expect(aiApi).toContain("const multi = Array.isArray(repoId);");
  });

  it("资产库聚合页也走分页（不再按仓库数强制全量 dump）", () => {
    expect(gallery).toContain("const browsing = enhanced && !fullyLoaded;");
    expect(gallery).toContain("const needFull = !enhanced || fullyLoaded;");
    expect(gallery).toContain("repoIds.length === 1 ? repoIds[0] : repoIds");
    expect(gallery).toContain("listGenerations(scope, embed, { limit: PAGE_SIZE })");
  });

  it("翻页不再限定单仓库（多仓库同样接续）", () => {
    expect(gallery).not.toContain("repoIds.length !== 1 || items.length === 0");
  });
});

describe("裂图清理（2026-10-18 用户实测：清理裂图后仍有破图）", () => {
  it("网格 img 有 onError 回退：缩略图坏→原图，原图也坏→记为裂图", () => {
    // 根因：网格此前无 onError（详情弹窗有）——陈旧 thumb_url / 云端死链直接显示破图图标
    expect(gallery).toContain('el.dataset.fb !== "1"');
    expect(gallery).toContain("setBrokenIds");
  });

  it("工具栏出浏览器侧裂图记录清理（复用既有批量删除确认，不新增删除路径）", () => {
    expect(gallery).toContain("条裂图记录");
    expect(gallery).toContain("setSelected(new Set(brokenIds))");
    expect(gallery).toContain("setBatchDel(true)");
  });

  it("刷新/切仓库重置裂图集合（防计数虚高）", () => {
    expect(gallery).toContain("setBrokenIds(new Set())");
  });
});

describe("标签条观感（2026-10-18 用户实拍：快照下拉与胶囊格格不入）", () => {
  it("快照/模型选择改成胶囊下拉（不再原生 select）", () => {
    expect(gallery).toContain("function TagPillSelect");
    expect(gallery).toContain("tag-pill-select");
    expect(gallery).not.toContain('<select className="tag-input"');
  });

  it("浮层列表有选中态与打勾（版本信息完整展示，不靠截断）", () => {
    expect(gallery).toContain("tag-pill-pop");
    expect(gallery).toContain("aria-selected");
    expect(gallery).toContain("标签");
  });

  it("× 平时压淡、悬停显形（与胶囊同一套观感）", () => {
    expect(styles).toContain(".tag-chip:hover .tag-chip-x");
    expect(styles).toContain(".tag-pill-pop");
  });
});

describe("统一反推入口（2026-10-18 用户定案：一套按钮 + 模式选择）", () => {
  it("只有一个反推按钮，模式用胶囊下拉选（含三种机制）", () => {
    expect(gallery).toContain("const TAG_MODES");
    expect(gallery).toContain('label: "VLM 两段式（标签+描述）"');
    expect(gallery).toContain('label: "WD14 本地（纯标签）"');
    expect(gallery).toContain('label: "视觉模型（纯标签）"');
    expect(gallery).toContain("onTag={() => runTag(active)}");
    expect(gallery).not.toContain("onAiTag");
  });

  it("两段式提示词要求「空行分隔」两段（对齐 ComfyUI 反推动漫产物）", () => {
    expect(gallery).toContain("VLM_TWO_PARA_PROMPT");
    expect(gallery).toContain("严格输出两段");
    expect(gallery).toContain("@//画师名/");
  });

  it("两段式落库走 /rag/tag-text（标签快照 + 描述写回）", () => {
    expect(aiApi).toContain('"/rag/tag-text"');
    expect(gallery).toContain("tagFromText(g.id, repoId, embed, text");
    expect(gallery).toContain("saveDescription: tagMode === \"vlm\"");
  });
});

describe("应用图标（2026-10-18 用户实拍：标签页显示的是 ComfyUI 图标）", () => {
  it("index.html 显式声明自己的 favicon（不再回退到浏览器默认/缓存图标）", () => {
    expect(indexHtml).toContain('rel="icon"');
    expect(indexHtml).toContain("/demiurge-icon.png");
  });
});

describe("快照切换提速 / 复制 / 纯描述模式（2026-10-18 用户反馈）", () => {
  it("切换快照先乐观更新（后端要重嵌入 ≈1.6s，不能挡 UI），失败回滚", () => {
    expect(gallery).toContain("乐观更新");
    expect(gallery).toContain("const prev = { tags: g.tags, active: g.active_tag_snapshot };");
    expect(gallery).toContain("patchItem(g.id, { tags: prev.tags, active_tag_snapshot: prev.active })");
  });

  it("详情里标签/描述各有一键复制", () => {
    expect(gallery).toContain('label="复制标签"');
    expect(gallery).toContain('label="复制描述"');
    expect(gallery).toContain("CopyButton");
    // 两段式一键复制：标签行＋空行＋描述段，直接贴 ComfyUI 提示词（本功能闭环）
    expect(gallery).toContain('label="复制提示词（两段式）"');
  });

  it("反推模式增加「视觉描述（只写描述）」——只写描述、不动标签", () => {
    expect(gallery).toContain('value: "desc"');
    expect(gallery).toContain("VISION_DESC_PROMPT");
    expect(gallery).toContain("setGenerationDescription(g.id, repoId, desc");
  });
});

describe("图像描述/反推改用视觉大模型 VLM（2026-10-18 用户实测素材描述仍空）", () => {
  it("settings 提供 activeVlmChat：local→ollamaName、remote→baseUrl+modelName，未配置返 null", () => {
    expect(settings).toContain("export function activeVlmChat");
    expect(settings).toContain("vlm.mode === \"local\"");
    expect(settings).toContain("http://localhost:11434/v1");
    // 回环端点必须直连（不走全局代理），否则本地 Ollama describe 502
    expect(settings).toContain("resolveEndpointProxy(vlm.baseUrl, vlm.proxyMode");
    expect(settings).not.toContain("resolveModelProxy(vlm.proxyMode, globalProxyAddress(s), s.proxyEnabled),\n  };\n}\n\n/** activeVlmChat 返 null");
  });

  it("RepoGallery 不再用普通对话模型做描述/反推，改用 vlmChat，并带未配置护栏", () => {
    expect(gallery).toContain("const vlmChat = activeVlmChat(settings);");
    expect(gallery).not.toContain("activeChatModel(settings)");
    expect(gallery).toContain("describeImage([imgForModel], vlmChat!");
    expect(gallery).toContain("if (!vlmChat) { showToast(vlmGapMessage(settings), \"error\"); return; }");
  });

  it("WebMaterialsView 素材描述也改用 activeVlmChat", () => {
    expect(view).toContain("const vlm = activeVlmChat(settings);");
    expect(view).toContain("describeImage([img], vlm,");
    expect(view).not.toContain("describeImage([img], activeChatModel(settings)");
  });
});

describe("反推按钮图标随模式变 + toast 引导（#3/#4，2026-10-18 用户定案补好4缺口）", () => {
  it("desc/wd14/ai/vlm 四模式各有图标，不再共用同一个", () => {
    expect(gallery).toContain('mode === "desc" ? <FileText size={11} />');
    expect(gallery).toContain('mode === "wd14" ? <Tag size={11} />');
    expect(gallery).toContain('mode === "ai" ? <Eye size={11} />');
    expect(gallery).toContain("<Sparkles size={11} />");
  });

  it("生成后 toast 指引去详情里复制（提升可发现性）", () => {
    expect(gallery).toContain("详情里可「复制提示词（两段式）」");
    expect(gallery).toContain("详情里可「复制标签」");
    expect(gallery).toContain("详情里可「复制描述」");
    expect(view).toContain("详情里可「复制描述 / 复制提示词」");
  });
});

describe("VLM 缺口精准提示 vlmGapMessage（2026-10-18 用户配了本地 GGUF 却报未配置）", () => {
  it("本地模式没导入 Ollama / 没选文件 / 真没配，分别给不同指引", () => {
    expect(settings).toContain("export function vlmGapMessage");
    expect(settings).toContain("本地视觉模型（GGUF）还没导入 Ollama");
    expect(settings).toContain("本地视觉模型还没选 GGUF 文件");
  });

  it("三处调用点都用 vlmGapMessage 提示，而不是笼统的未配置", () => {
    expect(gallery).toContain("showToast(vlmGapMessage(settings), \"error\")");
    expect(view).toContain("flashNote(vlmGapMessage(settings), \"error\")");
    expect(gallery).not.toContain("未配置视觉大模型：请到「设置 → 模型」配置「视觉大模型（VLM）」后再反推/描述");
  });
});

describe("孤儿仓库清理（2026-10-18，建议顺序①）", () => {
  it("前端有 API + 工具栏按钮 + handler", () => {
    expect(aiApi).toContain('"/rag/prune-orphan-repos"');
    expect(aiApi).toContain("export function pruneOrphanRepos");
    expect(gallery).toContain("const doOrphanPrune = async () => {");
    expect(gallery).toContain("pruneOrphanRepos(embed)");
    expect(gallery).toContain("清理孤儿仓库");
    expect(gallery).toContain("没有孤儿仓库（所有有记录的仓库都在注册表内）");
  });
});

describe("P2-2 多媒体：音频入库/播放（2026-10-18 建议顺序②）", () => {
  it("Generation.mediaType 支持 audio", () => {
    expect(aiApi).toContain('mediaType?: "image" | "video" | "audio"');
  });

  it("媒体滤镜加「音频」档（全部/图片/视频/音频）", () => {
    expect(gallery).toContain('useState<"all" | "image" | "video" | "audio">("all")');
    expect(gallery).toContain('["all", "image", "video", "audio"]');
    expect(gallery).toContain('m === "video" ? "视频" : "音频"');
  });

  it("音频资产：网格单元格（🎵）+ 详情内嵌 audio 播放器（与视频同构）", () => {
    expect(gallery).toContain('g.mediaType === "video" || g.mediaType === "audio"');
    expect(gallery).toContain("<Music size={22} />");
    expect(gallery).toContain('active.mediaType === "audio" ? (');
    expect(gallery).toContain("<audio src={active.image_url} controls");
    expect(gallery).toContain("🎵 音频资产");
  });
});

describe("P3 键盘导航（2026-10-18 建议顺序③：审 150 张挑图手不离键盘）", () => {
  it("详情弹窗：Esc 关闭 + ←/→ 在结果集里切上一张/下一张", () => {
    expect(gallery).toContain("const navListRef = useRef<Generation[]>([]);");
    expect(gallery).toContain('e.key !== "ArrowLeft" && e.key !== "ArrowRight"');
    expect(gallery).toContain("setActive(list[next])");
    expect(gallery).toContain('if (e.key === "Escape") { setActive(null); return; }');
  });

  it("输入框内的方向键不触发翻页（留给光标移动）", () => {
    expect(gallery).toContain('t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable');
  });

  it("结果集经 ref 同步（filtered 定义在 effect 之后，避免前向引用）", () => {
    expect(gallery).toContain("useEffect(() => { navListRef.current = filtered; });");
    // 层级守卫：全屏 Lightbox 开着时键盘交给它，别连带关掉底层弹窗
    expect(gallery).toContain('document.querySelector(".lightbox-mask")');
  });

  it("弹窗显示「第 X / Y 张（← → 切换）」位置提示", () => {
    expect(gallery).toContain("第 {i + 1} / {filtered.length} 张（← → 切换）");
  });
});

describe("P3 仓库同步（2026-10-18 建议顺序⑤：只读体检 + 一键修复）", () => {
  it("前端有 API（report/fix）+ 工具栏「同步体检」按钮 + 弹窗", () => {
    expect(aiApi).toContain('"/rag/repo-sync-report"');
    expect(aiApi).toContain('"/rag/repo-sync-fix"');
    expect(aiApi).toContain("export function repoSyncReport");
    expect(aiApi).toContain("export function repoSyncFix");
    expect(gallery).toContain("同步体检");
    expect(gallery).toContain("<RepoSyncModal");
  });

  it("弹窗先只读体检、再一键修复（不删磁盘、不覆盖已有索引、软删不重复补录）", () => {
    expect(syncModal).toContain("repoSyncReport(outputDir, embed)");
    expect(syncModal).toContain("repoSyncFix(outputDir, embed)");
    expect(syncModal).toContain("一键修复");
    expect(syncModal).toContain("不删你的磁盘文件、不覆盖已有索引");
  });
});
