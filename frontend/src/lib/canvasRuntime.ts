// canvasRuntime.ts — 画布模式纯逻辑（无 React、无 I/O，可单测）
//
// 职责：
//   1. projectNodes：把 generation_store 的记录投影为画布节点（P0 只读真源）
//   2. autoLayout：把节点按时间顺序排成网格（列优先，新节点追加到尾部）
//   3. 节点详情数据组装：图组 → 详情面板需要的内容
//
// 单一属主：generation 记录是节点内容的真源；本模块只做投影/排版，不写任何存储。

export type CanvasNodeType = "image-group" | "video" | "audio" | "input" | "workflow-tool" | "group" | "inspiration-card" | "reference-image" | "story";

/** 灵感卡来源类型：角色卡 / 世界书条目 / 预设 / 表格行（驱动卡片头部色条 + 插入对话时的标签） */
export type InspirationKind = "character" | "worldbook-entry" | "preset" | "table-row";

export interface CanvasNode {
  id: string;
  type: CanvasNodeType;
  x: number;
  y: number;
  w: number;
  h: number;
  /** 图组节点：聚合的 generation id 列表（同一 prompt 的多次结果） */
  generationIds: string[];
  /** 视频/音频节点：对应的 generation id */
  generationId?: string;
  /** 输入节点专用：无 generation 关联 */
  input?: boolean;
  /** 输入节点专用：用户输入的提示词文本 */
  prompt?: string;
  /** 输入节点专用：draft=双击新建待输入（可编辑）；generating=已提交生成中（占位） */
  inputStatus?: "draft" | "generating";
  /** 输入节点（generating）专用：调度主管委派/专家执行的过程行（对齐对话模式气泡内的 trace 行） */
  traceText?: string;
  /** 聚合键：图组用 prompt 归一化后的键 */
  groupKey?: string;
  // ===== workflow-tool 专用字段（/w 选模板后画布生成工具卡） =====
  /** 模板 id（对应 listTemplates 返回的 t.id） */
  templateId?: string;
  /** 模板展示名（用户标签） */
  templateName?: string;
  /** 用户在画布编辑中的完整 draftGraph */
  wfDraft?: unknown;
  /** 「选择完毕」后 graphToPrompt 抓取的结果 */
  wfCaptured?: unknown;
  /** 是否已「选择完毕」 */
  wfConfirmed?: boolean;
  /** 工作流运转中（画布「生成中」占位节点，产出入库后被生成内容节点替换） */
  wfGenerating?: boolean;
  /** 运转任务对应的 ComfyUI prompt_id（占位节点用） */
  wfPromptId?: string;
  /** 模板暴露的节点 id 列表（laf_lock LOCK 节点 /） */
  wfExposedIds?: string[];
  /** /w 派发的模板预估节点数（node_order 长度）——选择完毕前卡片据此显示节点数与高度 */
  wfEstimatedNodeCount?: number;
  /** 剧情节点专用：该楼层正文（显示时跑显示层正则） */
  storyText?: string;
  /** 剧情节点专用：对应消息 id */
  storyMessageId?: string;
  /** 剧情节点专用：剧情顺序（1-based 显示序号 / 总楼层数，来自消息顺序投影；画布序号徽章用） */
  storyIndex?: number;
  storyTotal?: number;
  /** 剧情节点专用：封面图（剧情自动插画，无则空） */
  storyImage?: string;
  /** 剧情节点专用：视频（有则节点左侧展示，支持全屏） */
  storyVideo?: string;
  /** 剧情节点专用：音频（有则节点左侧展示） */
  storyAudio?: string;
  /** 剧情节点专用：音频分条（角色名 + URL，按台词顺序；楼层节点逐条播放） */
  storyAudioLines?: Array<{ speaker: string; url: string }>;
  /** 剧情节点专用：思考块（详情面板展示，与对话模式 think 同源） */
  storyThinking?: string;
  // ===== 灵感卡专用字段（角色卡 / 世界书条目 / 预设 / 表格行 各自一张） =====
  /** 灵感卡来源类型 */
  inspirationKind?: InspirationKind;
  /** 灵感卡标题（显示在卡片顶部） */
  inspirationTitle?: string;
  /** 灵感卡正文（插入对话时调 chatAppend 的 text） */
  inspirationContent?: string;
  /** 原始素材引用（character 名 / worldbook entry index / preset 名 / table id，可选） */
  inspirationSourceRef?: string;
  /** 拖放/下载素材图片的本地 URL（可选，有值时节点渲染该图片） */
  imageUrl?: string;
  // ===== 参考图专用字段（从文件夹拖入画布的图片节点） =====
  /** 参考图本地 URL */
  referenceImageUrl?: string;
  /** 参考图标题（文件名，可编辑） */
  referenceImageTitle?: string;
}

/** 外部传入的 generation 记录（与 api/ai.ts 的 Generation 兼容，但只依赖必要字段） */
export interface GenLike {
  id: string;
  prompt: string;
  image_url: string;
  /** P0-2 缩略图派生层：画布节点用它渲染（原图动辄数 MB，多节点会把画布拖垮）；
      详情/全屏仍用 image_url。历史记录可能为空 ⇒ 消费侧必须兜底回原图。
      ⚠ 后端两个返回分支键名不一致（默认路径 `thumb_url`、分页路径 `thumbUrl`）⇒ 两个都要认。 */
  thumb_url?: string;
  thumbUrl?: string;
  tags?: string[];
  description?: string;
  created_at?: number;
  repo_id?: string;
  /** 视频/音频节点：media_type + 对应 URL（video_url / audio_url）
      ⚠ 后端真源是 camelCase `mediaType`，且视频/音频地址就写在 `image_url` 里
      （RAG 生成记录只有一个地址字段）⇒ 判定/播放一律走 `generationNodeType` / `genMediaUrl`，
      不要在组件里直接读 `video_url`。 */
  mediaType?: "video" | "audio" | "image";
  media_type?: "video" | "audio" | "image";
  video_url?: string;
  audio_url?: string;
  /** 卡片展示元数据（画布卡片右侧信息栏） */
  templateName?: string;     // 工作流模板名
  modelName?: string;        // UNet/Checkpoint 主模型名
  loraName?: string;         // LoRA 名（第一个，无则"无"）
  loraNames?: string[];      // 全部 LoRA 名列表
  dimensions?: string;       // 图片尺寸（如 "1024×1024"）
  resolution?: string;      // 视频清晰度（如 "1080p"）
  duration?: string;        // 视频/音频时长
  emotionVectors?: string;  // 音频情感向量（Happy,Angry,Sad,Fear,Hate,Low）
  referenceContent?: string; // 音频参考内容
}

/**
 * 判断一条 generation 的节点类型：视频 > 音频 > 图片。
 * 判定真源是后端 `list_generations` 的 camelCase `mediaType`（视频/音频记录**没有**
 * `video_url`/`audio_url` 字段，地址在 `image_url`）；`media_type` 与 URL 只作历史兼容。
 */
export function generationNodeType(g: GenLike): CanvasNodeType {
  const mt = g.mediaType || g.media_type;
  if (mt === "video") return "video";
  if (mt === "audio") return "audio";
  if (mt === "image") return "image-group";
  if ((g.video_url || "").trim()) return "video";
  if ((g.audio_url || "").trim()) return "audio";
  return "image-group";
}

/**
 * 生成记录的播放地址（视频/音频节点唯一出口）。
 * 后端把媒体地址统一写进 `image_url`（RAG generation 只有一个地址字段），
 * `video_url`/`audio_url` 只是旧调用方/云端链的别名 ⇒ 优先别名，兜底 image_url。
 */
export function genMediaUrl(g: GenLike | undefined): string {
  if (!g) return "";
  const type = generationNodeType(g);
  if (type === "video") return (g.video_url || g.image_url || "").trim();
  if (type === "audio") return (g.audio_url || g.image_url || "").trim();
  return g.image_url || "";
}

export interface MediaLayoutMigration {
  nodes: Record<string, PlacedRect>;
  deletedIds: string[];
  changed: boolean;
}

/**
 * 兼容迁移：视频/音频记录此前被误判成图组（`generationNodeType` 只认 snake_case），
 * 画布把它们的布局与删除黑名单记在 `img-gen-<id>` 下。类型判定修好后节点 id 变成
 * `video-gen-` / `audio-gen-`，旧键必须跟着改名，否则**位置丢失**、被删过的节点**复活**。
 *
 * 幂等：旧键不存在或新键已存在时不动。
 */
export function migrateMediaLayoutKeys(
  gens: ReadonlyArray<GenLike>,
  nodes: Readonly<Record<string, PlacedRect>>,
  deletedIds: ReadonlyArray<string>,
): MediaLayoutMigration {
  const nextNodes: Record<string, PlacedRect> = { ...nodes };
  const nextDeleted = [...deletedIds];
  let changed = false;
  for (const g of gens) {
    const type = generationNodeType(g);
    if (type !== "video" && type !== "audio") continue;
    const oldId = `img-gen-${g.id}`;
    const newId = `${type}-gen-${g.id}`;
    const rect = nextNodes[oldId];
    if (rect && !nextNodes[newId]) {
      nextNodes[newId] = rect;
      delete nextNodes[oldId];
      changed = true;
    }
    const di = nextDeleted.indexOf(oldId);
    if (di >= 0) {
      nextDeleted[di] = newId;
      changed = true;
    }
  }
  return { nodes: nextNodes, deletedIds: nextDeleted, changed };
}

// ---- 投影 ----

/** 同一 prompt 的多次生成归为一个图组（空 prompt 各自独立成组，避免全堆一起） */
export function groupKeyOf(prompt: string): string {
  const p = (prompt || "").trim();
  return p ? `p:${p}` : `id:${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 把 generation 列表投影为节点。
 * - 图片 → image-group（每条生成独立一节点，不再按 prompt 聚合）
 * - 视频 → video 节点（每条生成独立）
 * - 音频 → audio 节点（每条生成独立）
 * - 保留输入节点（由调用方决定是否添加）
 * 返回 { nodes, byGroup }，byGroup 供详情面板快速取某组内容。
 */
export function projectNodes(gens: GenLike[]): {
  nodes: CanvasNode[];
  byGroup: Map<string, GenLike[]>;
} {
  // 每条 generation 独立成节点（新在前）：用户每次生成都应有自己的节点，
  // 不再按 prompt 聚合（同一模板/同一提示词的多次生成也要各自一节点）。
  const nodes: CanvasNode[] = [];
  const byGroup = new Map<string, GenLike[]>();
  const sorted = [...gens].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  sorted.forEach((g, idx) => {
    const type = generationNodeType(g);
    // id 稳定锚定 generation id，避免列表增删导致下标漂移（保存的布局不会错位）
    const id = `${type === "image-group" ? "img" : type}-${g.id}`;
    nodes.push({
      id,
      type,
      x: 0,
      y: 0,
      w: type === "image-group" ? 240 : 320,
      h: type === "video" ? 200 : type === "audio" ? 96 : 300,
      generationIds: [g.id],
      groupKey: `id:${g.id}`,
    });
    byGroup.set(id, [g]);
    void idx;
  });
  return { nodes, byGroup };
}

// ---- 吸附计算 ----

/**
 * 单轴中心对中心吸附 + 接近提示：
 * - dist < snapPx：贴住（snap=true），返回 delta 用于位置修正
 * - dist < hintPx 但 >= snapPx：仅显示（snap=false），不修正——用户能看到"快对齐了"的提示线
 * - dist >= hintPx：不显示
 * - 滞回：已吸附后以「拖动中心对已吸附位置的偏移 < releasePx」为保持条件
 * 返回 pos（候选位置，绝对坐标）、snap（是否贴住）、delta（局部 position 位移）
 */
export function computeAxisSnap(
  dragCenter: number,
  targetCenters: readonly number[],
  prevPos: number | null,
  snapPx: number,
  releasePx: number,
  hintPx: number = snapPx,
): { pos: number | null; snap: boolean; delta: number } {
  if (prevPos !== null && Math.abs(dragCenter - prevPos) < releasePx) {
    return { pos: prevPos, snap: true, delta: prevPos - dragCenter };
  }
  let best = { center: 0, dist: Infinity };
  for (const tc of targetCenters) {
    const d = Math.abs(dragCenter - tc);
    if (d < best.dist) best = { center: tc, dist: d };
  }
  if (best.dist >= hintPx) return { pos: null, snap: false, delta: 0 };
  return {
    pos: best.center,
    snap: best.dist < snapPx,
    delta: best.center - dragCenter,
  };
}

// ---- 自动排版 ----

export interface LayoutConfig {
  cols?: number;      // 每行列数（默认 4）
  cellW?: number;     // 节点宽（默认 240）
  cellH?: number;     // 节点高（默认 300）
  gapX?: number;      // 水平间距（默认 24）
  gapY?: number;      // 垂直间距（默认 24）
  originX?: number;   // 起始 X（默认 24）
  originY?: number;   // 起始 Y（默认 24）
}

/**
 * 把无坐标节点排成网格。已带坐标的节点保持原位（P1 拖拽后重排时用）。
 * 新增节点追加到网格尾部（按传入顺序 = 时间序）。
 */
export function autoLayout(nodes: CanvasNode[], cfg: LayoutConfig = {}): CanvasNode[] {
  const {
    cols = 4, cellW = 240, cellH = 300, gapX = 24, gapY = 24, originX = 24, originY = 24,
  } = cfg;
  const placed = nodes.filter((n) => n.x !== 0 || n.y !== 0);
  const unplaced = nodes.filter((n) => n.x === 0 && n.y === 0);
  let cursor = placed.length;
  const laid = unplaced.map((n, i) => {
    const col = (cursor + i) % cols;
    const row = Math.floor((cursor + i) / cols);
    return { ...n, x: originX + col * (cellW + gapX), y: originY + row * (cellH + gapY) };
  });
  return [...placed, ...laid];
}

// ---- 新节点锚点网格放置 ----

/** 一个已占用/已放置的矩形（绝对坐标） */
export interface PlacedRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PlaceNewNodesOptions {
  /** 每行节点数（小网格列数，默认 3） */
  cols?: number;
  /** 水平间距（默认 24） */
  gapX?: number;
  /** 垂直间距（默认 24） */
  gapY?: number;
  /** 无锚点时的起点（默认 24,24） */
  originX?: number;
  originY?: number;
  /** true：第一个节点落在锚点原位（垂直居中对齐）；false：从锚点右侧第 1 列开始展开 */
  replace?: boolean;
}

/**
 * 以「锚点 rect」为起点，把一批新节点排成小网格（列优先：向右展开、排满 cols 列换行向下）。
 * - 第一个节点与锚点垂直居中对齐（replace=true 时落在锚点原位，其余从锚点右侧第 1 列开始）。
 * - 所有落点与 occupied（已有节点 + 本批已放节点）做重叠检测：重叠则逐格向右、换行向下，直到不重叠。
 * - anchor 为 null 时从 originX/originY 开始（第一个节点落在原点）。
 * 返回与 sizes 等长的落点数组（每个 {x, y} 为节点左上角）。
 */
export function placeNewNodes(
  sizes: Array<{ w: number; h: number }>,
  anchor: PlacedRect | null,
  occupied: PlacedRect[],
  opts: PlaceNewNodesOptions = {},
): Array<{ x: number; y: number }> {
  const { cols = 3, gapX = 24, gapY = 24, originX = 24, originY = 24, replace = false } = opts;
  const maxW = sizes.reduce((m, s) => Math.max(m, s.w), 0);
  const maxH = sizes.reduce((m, s) => Math.max(m, s.h), 0);
  const colW = maxW + gapX;
  const rowH = maxH + gapY;

  // 网格原点：有锚点 → 锚点右侧第 1 列、行 0 与锚点垂直居中；无锚点 → 原点
  const col0X = anchor ? anchor.x + anchor.w + gapX : originX;
  const row0Y = anchor ? anchor.y + (anchor.h - maxH) / 2 : originY;
  const firstInPlace = !!anchor && replace;

  const all: PlacedRect[] = [...occupied];
  const overlaps = (x: number, y: number, w: number, h: number) =>
    all.some((r) => x < r.x + r.w + gapX && x + w + gapX > r.x && y < r.y + r.h + gapY && y + h + gapY > r.y);

  const out: Array<{ x: number; y: number }> = [];
  sizes.forEach((s, i) => {
    let x: number;
    let y: number;
    if (i === 0 && firstInPlace && anchor) {
      // 原位替换：垂直居中对齐到锚点（占位即将消失，不做重叠避让）
      x = anchor.x;
      y = anchor.y + (anchor.h - s.h) / 2;
    } else {
      const k = firstInPlace ? i - 1 : i;
      const col = k % cols;
      const row = Math.floor(k / cols);
      x = col0X + col * colW;
      y = row0Y + row * rowH;
      // 重叠避让：逐格向右，越界换行向下（平行 → 垂直展开），保证不重叠
      let guard = 0;
      while (overlaps(x, y, s.w, s.h) && guard < 10000) {
        x += colW;
        if (x - col0X >= cols * colW) {
          x = col0X;
          y += rowH;
        }
        guard++;
      }
    }
    out.push({ x, y });
    all.push({ x, y, w: s.w, h: s.h });
  });
  return out;
}

/**
 * 整理分组判定：该节点是否属于「废案组」（生成产物）。
 *
 * 用户定案（2026-11-12 A 口径）：画布上**所有**生成产物节点都算废案——
 * 每次重新生成留下的旧图组/视频/音频不再参与剧情主网格避让，整理时整组
 * 另起一块排到主网格右侧，剧情才能连续排满、不被顶出上千像素空洞。
 *
 * 只认投影节点 id 前缀（`img-gen-` / `video-gen-` / `audio-gen-`，见 `projectNodes`）。
 * 灵感卡、参考图、输入框、工作流卡是素材/工具，不算废案，仍留在原地。
 */
export function isArchiveProductNode(id: string): boolean {
  return /^(img|video|audio)-gen-/.test(id);
}

/**
 * 整理分组：把一批节点排成「行优先」网格（左→右、上→下）。
 *
 * 排的是**一整块**：组内行与行之间严格连续（行高取该行最高卡），只有整块
 * 与既有节点（`occupied`）重叠时才整体下移。
 * - 不能逐行各自避让：障碍夹在组中间时，逐行会让第 2 行掉到障碍下面、第 3 行
 *   再回到原基线，组内出现上百像素空洞（2026-11-12 用户实锤「整理剧情乱七八糟」）。
 * - `occupied` 只应传**不被本组整理**的节点包围盒；被整理的节点自己不能参与避让，
 *   否则每个目标格都会撞到自己的旧位置，整批被推到远处。
 * - 调用侧用它排两组（剧情主网格 / 废案组）时，把先排好的一组矩形并入 `occupied`，
 *   保证两组永不重叠。
 */
export function arrangeStoryGrid(
  sizes: ReadonlyArray<{ w: number; h: number }>,
  occupied: ReadonlyArray<PlacedRect> = [],
  opts: StoryGridOptions = {},
): Array<{ x: number; y: number }> {
  const { cols = 4, gapX = 24, gapY = 24, originX = 24, originY = 24 } = opts;
  if (sizes.length === 0) return [];
  const colW = Math.max(...sizes.map((s) => s.w)) + gapX;

  // 组内相对行位：每行高度取该行最高卡，行间只隔 gapY（不避让）
  const rows: Array<{ count: number; h: number }> = [];
  for (let start = 0; start < sizes.length; start += cols) {
    const row = sizes.slice(start, start + cols);
    rows.push({ count: row.length, h: Math.max(...row.map((s) => s.h)) });
  }
  const blockW = Math.max(...rows.map((r) => r.count)) * colW - gapX;
  const blockH = rows.reduce((sum, r) => sum + r.h, 0) + gapY * (rows.length - 1);

  // 整块避让：整块包围盒与障碍重叠就整体下移，组内结构不变
  let y = originY;
  let guard = 0;
  while (overlapsAny(originX, y, blockW, blockH, occupied, gapX, gapY) && guard < 1000) {
    y += gapY;
    guard += 1;
  }

  const out: Array<{ x: number; y: number }> = [];
  let rowY = y;
  for (const r of rows) {
    for (let col = 0; col < r.count; col += 1) {
      out.push({ x: originX + col * colW, y: rowY });
    }
    rowY += r.h + gapY;
  }
  return out;
}

function overlapsAny(
  x: number, y: number, w: number, h: number,
  occupied: ReadonlyArray<PlacedRect>, gapX: number, gapY: number,
): boolean {
  return occupied.some((r) =>
    x < r.x + r.w + gapX && x + w + gapX > r.x
    && y < r.y + r.h + gapY && y + h + gapY > r.y);
}

export interface StoryGridOptions {
  cols?: number;
  gapX?: number;
  gapY?: number;
  originX?: number;
  originY?: number;
}

/** 剧情主网格与废案组之间的横向留白：足够肉眼分成两组，又不至于要横滚半屏 */
export const ARCHIVE_GAP_X = 96;

export interface ArrangeSize {
  id: string;
  w: number;
  h: number;
}

export interface ArrangePlacement {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * 整理编排（纯计算）：剧情主网格 + 废案组。
 *
 * 两组各排一整块（`arrangeStoryGrid`），废案组从剧情网格实际右边界 + `ARCHIVE_GAP_X`
 * 起排，剧情矩形并入障碍 ⇒ 两组永不重叠、互不避让出洞。废案为空时只返回剧情组。
 */
export function planStoryArrange(
  story: ReadonlyArray<ArrangeSize>,
  archive: ReadonlyArray<ArrangeSize> = [],
  obstacles: ReadonlyArray<PlacedRect> = [],
  opts: StoryGridOptions = {},
): { story: ArrangePlacement[]; archive: ArrangePlacement[] } {
  const storyPos = arrangeStoryGrid(story, obstacles, opts);
  const storyPlaced: ArrangePlacement[] = story.map((s, i) => ({ id: s.id, ...storyPos[i], w: s.w, h: s.h }));
  if (archive.length === 0) return { story: storyPlaced, archive: [] };
  const right = storyPlaced.length > 0
    ? Math.max(...storyPlaced.map((p) => p.x + p.w))
    : (opts.originX ?? 24);
  const archivePos = arrangeStoryGrid(
    archive,
    [...obstacles, ...storyPlaced],
    { ...opts, originX: right + ARCHIVE_GAP_X },
  );
  return {
    story: storyPlaced,
    archive: archive.map((s, i) => ({ id: s.id, ...archivePos[i], w: s.w, h: s.h })),
  };
}

/**
 * 选中节点中心对齐：选中节点中心 vs 所有未选中节点中心，分别按 x/y 找最近 1 个匹配。
 * - 中心 y 相同（距离 < hintPx）→ 出水平贯穿辅助线（线在选中节点中心 y 处）
 * - 中心 x 相同（距离 < hintPx）→ 出竖向贯穿辅助线（线在选中节点中心 x 处）
 * - snap = 距离 < snapPx（吸附，此时其他节点中心已被吸附到选中节点中心）
 * - 用户拍板：线从「选中节点的中心点」延伸贯穿画布；接近时半透明虚线提示，吸附时变实。
 */
export function findCenterAlignments(
  selCenter: { x: number; y: number },
  others: Array<{ id: string; center: { x: number; y: number } }>,
  snapPx: number,
  hintPx: number,
): { x: number | null; snapX: boolean; y: number | null; snapY: boolean } {
  let nearestX = Infinity;
  let nearestY = Infinity;
  let snapXHit = false;
  let snapYHit = false;
  for (const o of others) {
    const dx = Math.abs(o.center.x - selCenter.x);
    if (dx < nearestX) { nearestX = dx; snapXHit = dx < snapPx; }
    const dy = Math.abs(o.center.y - selCenter.y);
    if (dy < nearestY) { nearestY = dy; snapYHit = dy < snapPx; }
  }
  const xOut: number | null = nearestX < hintPx ? selCenter.x : null;
  const yOut: number | null = nearestY < hintPx ? selCenter.y : null;
  return {
    x: xOut, snapX: xOut !== null ? snapXHit : false,
    y: yOut, snapY: yOut !== null ? snapYHit : false,
  };
}

// ---- 详情面板 ----

/** 最小节点形态：只取吸附计算需要的字段（ReactFlow Node 的结构子集，便于单测） */
export interface SnapNodeLike {
  id: string;
  position: { x: number; y: number };
  parentId?: string;
}

/**
 * 计算节点在画布坐标系中的绝对位置（父组链逐级累加局部坐标）。
 * ReactFlow 中 parentId 存在时 position 是相对父组的局部坐标，直接跨节点比较会错位；
 * 吸附辅助线（guides）必须基于绝对坐标，修正时再换算回局部。
 */
export function nodeAbsolutePosition<T extends SnapNodeLike>(
  node: T,
  byId: ReadonlyMap<string, T>,
): { x: number; y: number } {
  let x = node.position.x;
  let y = node.position.y;
  let pid = node.parentId;
  const seen = new Set<string>([node.id]);
  while (pid && !seen.has(pid)) {
    const parent = byId.get(pid);
    if (!parent) break;
    x += parent.position.x;
    y += parent.position.y;
    seen.add(pid);
    pid = parent.parentId;
  }
  return { x, y };
}


export interface NodeDetailData {
  id: string;
  type: CanvasNodeType;
  title: string;
  gens: GenLike[];       // 该节点包含的全部 generation（图组按时间倒序）
  prompt: string;        // 主提示词（第一张图）
  imageUrls: string[];   // 全部图片 URL
}

/** 组装详情面板数据；找不到时返回 null */
export function nodeDetail(node: CanvasNode, byGroup: Map<string, GenLike[]>): NodeDetailData | null {
  if (node.type === "input") {
    return {
      id: node.id,
      type: "input",
      title: "输入节点",
      gens: [],
      prompt: node.prompt || "",
      imageUrls: [],
    };
  }
  if (node.type === "reference-image") {
    return {
      id: node.id,
      type: "reference-image",
      title: node.referenceImageTitle || "参考图",
      gens: [],
      prompt: node.referenceImageTitle || "",
      imageUrls: node.referenceImageUrl ? [node.referenceImageUrl] : [],
    };
  }
  if (node.type === "story") {
    return {
      id: node.id,
      type: "story",
      title: "剧情楼层",
      gens: [],
      prompt: node.storyText || "",
      imageUrls: node.storyImage ? [node.storyImage] : [],
    };
  }
  const gens = byGroup.get(node.id) || [];
  if (gens.length === 0) return null;
  const first = gens[0];
  if (node.type === "video" || node.type === "audio") {
    return {
      id: node.id,
      type: node.type,
      title: node.type === "video" ? `视频 · ${gens.length} 段` : `音频 · ${gens.length} 段`,
      gens,
      prompt: first.prompt || "",
      // 视频/音频节点这里存的是**媒体地址**（后端写在 image_url 里），消费方按 type 决定用
      // <video>/<audio> 还是 <img>；不能假设它是图片。
      imageUrls: gens.map((g) => genMediaUrl(g)).filter(Boolean),
    };
  }
  if (node.type === "image-group") {
    return {
      id: node.id,
      type: node.type,
      title: `图组 · ${gens.length} 张`,
      gens,
      prompt: first.prompt,
      imageUrls: gens.map((g) => g.image_url),
    };
  }
  return null;
}
