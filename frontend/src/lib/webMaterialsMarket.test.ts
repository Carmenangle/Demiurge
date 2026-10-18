// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// P2-1 切片契约：上网素材页的图站市场入口 + 凭证面板字段 + 市场弹窗落盘链。
// 用源码锁防止「按钮加了但没接保存链」这类半落地（用户 2026-10-15 实锤：落点放错页面）。
const view = readFileSync(new URL("../views/WebMaterialsView.tsx", import.meta.url), "utf8");
const modal = readFileSync(new URL("../components/MaterialMarketModalLive.tsx", import.meta.url), "utf8");
const detail = readFileSync(new URL("../components/WebMaterialDetailModal.tsx", import.meta.url), "utf8");
const appBody = readFileSync(new URL("../AppBody.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/ai.ts", import.meta.url), "utf8");
const tokens = readFileSync(new URL("../views/settings/TokensPanel.tsx", import.meta.url), "utf8");
const settings = readFileSync(new URL("../stores/settings.ts", import.meta.url), "utf8");

describe("上网素材页 · 图站市场入口（P2-1）", () => {
  it("工具栏同一行提供 pixiv / danbooru 市场按钮", () => {
    expect(view).toContain("pixiv 市场");
    expect(view).toContain("danbooru 市场");
    expect(view).toContain('setMarket("pixiv")');
    expect(view).toContain('setMarket("danbooru")');
  });

  it("市场弹窗接线上网素材刷新与提示", () => {
    expect(view).toContain("<MaterialMarketModal");
    expect(view).toContain("onSaved={refresh}");
    expect(view).toContain("effectiveGlobalProxyUrl");
  });

  it("弹窗走既有受控保存链（搜索即登记候选 → save 落盘）", () => {
    expect(modal).toContain("searchWebMaterials");
    expect(modal).toContain("saveWebMaterial");
    expect(modal).toContain("保存到上网素材");
  });

  it("画廊式工具条：排行榜/收藏夹/rating 四档，均把 tag 写进搜索框", () => {
    expect(modal).toContain("排行榜");
    expect(modal).toContain("收藏夹");
    expect(modal).toContain("order:rank");            // 排行榜按钮＝order:rank
    expect(modal).toContain("ordfav:");                // 收藏夹按钮＝ordfav:用户名
    expect(modal).toContain("rating:");                // 全部▾＝rating 语法
    expect(modal).toContain("RATINGS");                // 普通/敏感/可疑/露骨四档
    expect(modal).toContain("withToken");              // 按钮只改输入框内容（可见可改）
    expect(modal).toContain("hasToken");               // 激活态由输入框内容反推
  });

  it("不含「类别 / 格式」按钮（用户明确不要）", () => {
    expect(modal).not.toContain("类别");
    expect(modal).not.toContain("格式");
  });

  it("错误持久显示在弹窗内 + 分页（当前页/跳转）+ 代理可见", () => {
    expect(modal).toContain("{error && (");
    expect(modal).toContain("当前页：");
    expect(modal).toContain("跳转");
    expect(modal).toContain("代理 {proxy}");
  });

  it("收藏夹未配用户名时禁用并提示（ordfav 需要 login）", () => {
    expect(modal).toContain("favReady");
    expect(modal).toContain("收藏夹需要 danbooru 用户名");
  });

  it("瀑布流自然排版（不裁切）+ 每图四个操作按钮", () => {
    expect(modal).toContain("market-masonry");          // CSS 多列瀑布流
    expect(modal).toContain("查看大图");
    expect(modal).toContain("编辑信息");
    expect(modal).toContain("marketImageProxyUrl");     // 大图走后端代理
    expect(modal).toContain("viewing.large_url || viewing.full_url"); // 用 1920px large（原图 20MB+ 太慢）
    expect(modal).toContain("favoriteWebMaterial");     // 收藏到 danbooru 收藏夹
    expect(modal).toContain("downloadOne");             // 单图下载
    expect(modal).not.toContain("aspectRatio: \"1 / 1\"");  // 不再用正方形裁切格
  });

  it("进入即自动搜索；按钮＝写进输入框 + 立即搜索（再点移除并重搜）", () => {
    // 挂载自动加载（空 query = danbooru 最新）
    expect(modal).toContain("useEffect");
    expect(modal).toContain('void runSearch(1, "")');
    // 排行榜：toggle 写入/移除 order:rank 后立刻搜
    expect(modal).toContain('on ? withToken(null, (t) => t === "order:rank") : withToken("order:rank")');
    expect(modal).toContain("void runSearch(1, next)");
    // rating 选择＝替换输入框里的 rating:x 后重搜
    expect(modal).toContain('(t) => t.startsWith("rating:")');
  });

  it("不再显示独立的「当前查询」预览行（tag 直接体现在输入框里）", () => {
    expect(modal).not.toContain("当前查询：");
    expect(modal).not.toContain("（空 → 按最新）");
  });

  it("空关键词＝按默认浏览（不报错）：danbooru 最新 / pixiv 推荐流", () => {
    // pixiv 不再拦截空 query（2026-10-16 用户口径：刚进入就有默认页＝最新/推荐内容）
    expect(modal).not.toContain('if (!q && source === "pixiv")');
    // 进入弹窗即加载 pixiv 推荐流（无需关键词）
    expect(modal).toContain('void runSearch(1, "", "recommended", "")');
  });

  it("pixiv 工具栏对齐 danbooru：最新/排行榜/收藏夹/关注作者 + 每个图片可收藏", () => {
    expect(modal).toContain("pixivMode");            // pixiv 信息流模式态
    expect(modal).toContain('void runSearch(1, "", "ranking", mp)');   // 排行榜
    expect(modal).toContain('void runSearch(1, "", "bookmarks", "")'); // 收藏夹
    expect(modal).toContain("openAuthorWorks");      // 点关注作者看其作品
    expect(modal).toContain("pixivFollowing");       // 关注列表接口
    expect(modal).toContain("marketImageProxyUrl(m.thumb_url || m.full_url, proxy)");  // pximg 缩略图走后端代理
  });

  it("编辑信息面板：分组标签 + 点标签弹「搜索/删除」菜单 + 复制标签", () => {
    expect(modal).toContain("TAG_GROUPS");
    expect(modal).toContain("market-tag-menu");
    expect(modal).toContain("setRemovedTags");
    expect(modal).toContain("复制标签");
    expect(modal).toContain("重置标签");
  });
});

describe("凭证面板 · 上网素材源鉴权（P2-1）", () => {
  it("TokensPanel 提供 pixiv / danbooru 凭证输入", () => {
    expect(tokens).toContain("上网素材源鉴权");
    expect(tokens).toContain("pixivRefreshToken");
    expect(tokens).toContain("danbooruApiKey");
  });

  it("danbooru 是用户名 + API Key 配对（区别于 pixiv 的单令牌）", () => {
    expect(tokens).toContain("danbooruLogin");
    expect(tokens).toContain("danbooru 用户名");
    expect(tokens).toContain("用户名 + API Key 两个字段配对");
    // pixiv 是 OAuth 令牌，明确不需要用户名
    expect(tokens).toContain("不需要");
    expect(settings).toContain("danbooruLogin?: string");
    expect(settings).toContain('danbooruLogin: ""');
    expect(settings).toContain("out.danbooruLogin = \"\"");
  });

  it("settings 贯通类型/默认值/导出清空（密钥不进导出）", () => {
    expect(settings).toContain("pixivRefreshToken?: string");
    expect(settings).toContain("danbooruApiKey?: string");
    expect(settings).toContain('pixivRefreshToken: ""');
    expect(settings).toContain('danbooruApiKey: ""');
    expect(settings).toContain("out.pixivRefreshToken = \"\"");
    expect(settings).toContain("out.danbooruApiKey = \"\"");
  });
});

describe("画廊修复（2026-10-16）", () => {
  it("编辑信息面板用 portal 置顶（修「点了没反应」＝被市场弹窗盖住）", () => {
    expect(modal).toContain("createPortal");
    expect(modal).toContain("document.body");
    expect(modal).toContain("zIndex: 1500");
    expect(modal).toContain("zIndex: 1600");   // 标签菜单同层置顶
  });

  it("收藏实心区分：已收藏（含 danbooru 返回的 is_favorited）显示实心 ♥", () => {
    expect(modal).toContain("is_favorited");   // 搜索即带回已收藏态
    expect(modal).toContain('fill={m.post_id && favIds.has(m.post_id) ? "currentColor" : "none"}');
  });

  it("单图下载走既有受控保存链（后端图站 host 已改带 Referer 的 curl 通路修 403）", () => {
    expect(modal).toContain("downloadOne");
    expect(modal).toContain("saveWithRetry(item.full_url");  // 非 pixiv 单张带重试走受控保存链
  });
});

describe("画廊修复二（2026-10-16）", () => {
  it("多 token 用「, 」连接（用户口径：ordfav:X, order:rank）+ 分词兼容逗号", () => {
    expect(modal).toContain('arr.join(", ")');
    expect(modal).toContain("split(/[,\\s]+/)");
  });

  it("点遮罩不关闭（复制粘贴友好）：ESC 快捷关闭 + 高度自适应底部不被切", () => {
    expect(modal).toContain('<div className="modal-mask">');
    expect(modal).toContain('e.key !== "Escape"');
    expect(modal).toContain('maxHeight: "90vh"');
    expect(modal).toContain("flexShrink: 0");
  });
});

describe("查看大图改页内弹窗（2026-10-16 用户口径）", () => {
  it("不再新开标签页，改为本页弹窗展示（保留「原图新标签」兜底）", () => {
    expect(modal).toContain("const openFull = async (m: WebMaterialSearchItem, page");
    expect(modal).toContain("setViewing(m)");
    expect(modal).toContain("原图新标签");                 // 兜底按钮仍在
    expect(modal).toContain("加载大图（1920px）");           // 加载态可见（大图慢）
    expect(modal).toContain("zIndex: 1700");               // 高于详情面板/市场弹窗
  });

  it("大图弹窗有独立 ESC 层级（viewing → tagMenu → detail → 关市场）", () => {
    expect(modal).toContain("if (viewing) { setViewing(null); return; }");
  });
});

describe("大图缩放 + 收藏实心兜底（2026-10-16）", () => {
  it("大图弹窗支持滚轮缩放（鼠标为焦点）+ 拖拽平移 + 双击复位", () => {
    expect(modal).toContain('addEventListener("wheel"');
    expect(modal).toContain("passive: false");        // 不加就 preventDefault 无效
    expect(modal).toContain("onDoubleClick");         // 双击复位
    expect(modal).toContain("拖拽平移 · 双击复位");
  });

  it("收藏夹浏览时结果直接标实心（不依赖 is_favorited——该字段需 danbooru 认得凭据才返回）", () => {
    expect(modal).toContain("ordfav:/.test(q)");
  });
});

describe("pixiv 市场三修（2026-10-17 用户实测反馈）", () => {
  it("弹窗标题栏有 X 关闭按钮（应用口径「关闭走 X/ESC」；修「收藏夹/关注作者点开后关不掉」）", () => {
    // 市场弹窗此前只有底部「关闭」+ ESC，缺标题栏 X ⇒ 内容一高就够不着底部按钮、也点不到遮罩关闭
    expect(modal).toContain('title="关闭（ESC 亦可）"');
    expect(modal).toContain("<X size={16} />");
    // X 与底部「关闭」都走 onClose
    expect(modal).toContain("onClick={onClose}");
  });

  it("关注作者卡片名字单行不重影（.market-card-meta 是 absolute bottom，两个会叠成重影）", () => {
    // 只用「一个」market-card-meta，name 与 @account 并到同一行（内联 span）
    expect(modal).toContain('u.account !== (u.name || "")');
    expect(modal).not.toContain('className="market-card-meta" style={{ opacity: 0.6 }}');  // 旧的第二个 meta 已删
  });

  it("收藏实心只按「本次实际 mode」标（修闭包陈旧 pixivMode 把排行榜误标实心）", () => {
    // markFavPixivBookmarks 收 mode 参数，runSearch 传本次解析出的 mode——不能用闭包里陈旧的 pixivMode
    expect(modal).toContain("markFavPixivBookmarks(r.items || [], mode)");
    expect(modal).toContain("const markFavPixivBookmarks = (items: WebMaterialSearchItem[], mode: PixivMode)");
  });

  it("收藏夹用 max_bookmark_id 游标接续（SDK 无 offset；无游标即没有更多）", () => {
    expect(modal).toContain('"bookmarks", pixivCursor');     // loadMore 传游标
    expect(modal).toContain("r.next_cursor");                // 收下一页游标
    expect(modal).toContain("? !!pixivCursor");              // hasMore 看游标
  });

  it("pixiv 已收藏再点＝取消收藏（toggle → illust_bookmark_delete）", () => {
    expect(modal).toContain('const canToggle = source === "pixiv" && wasFav');
    expect(modal).toContain("favoriteWebMaterial(m.post_id, proxy, source, canToggle)");
    expect(modal).toContain("已从 pixiv 收藏夹移除");
  });

  it("多页作品：卡片 N页 角标 + 大图上一页/下一页 + 下载全部页", () => {
    expect(modal).toContain("m.page_count && m.page_count > 1");   // 卡片「N页」角标
    expect(modal).toContain("gotoViewPage");                       // 大图翻页
    expect(modal).toContain("viewPages[viewPage]");                // 用当前页 URL
    expect(modal).toContain("下载全部");                            // 下载全部页
  });

  it("pixiv 下载一律先经 illust_detail 拉全页（修 23/99 页只存封面 1 张）", () => {
    expect(modal).toContain('if (source === "pixiv" && item.post_id)');  // pixiv 走后端整册保存
    expect(modal).toContain("saveWebSeries(outputDir, item.post_id");
  });

  it("多页保存走「后端整册保存」saveWebSeries（前端崩溃/断连也不影响完成）", () => {
    expect(modal).toContain("saveItemToLibrary");                            // 共享条目保存
    expect(modal).toContain("saveWebSeries(outputDir, item.post_id");        // pixiv 走后端整册
    expect(modal).toContain('if (source === "pixiv" && item.post_id)');
  });

  it("非 pixiv 单张保存带重试（danbooru 等，网络抖动不丢图）", () => {
    expect(modal).toContain("saveWithRetry");
    expect(modal).toContain("retries = 2");
    expect(modal).toContain("await saveWithRetry(item.full_url");
  });
});

describe("凭证面板：danbooru Key 格式提示", () => {
  it("Key 非 32 位十六进制时给出可读告警（用户实测 24 字符导致收藏/实心失效）", () => {
    expect(tokens).toContain("[0-9a-f]{32}");
    expect(tokens).toContain("不像 danbooru API Key");
  });
});

describe("无限滚动接续（2026-10-16 用户口径）", () => {
  it("滚到底自动接续下一页（追加而非替换），不再有上下页按钮", () => {
    expect(modal).toContain("const loadMore = async () => {");
    expect(modal).toContain("onGridScroll");
    expect(modal).toContain("setResults((prev) => [...prev, ...(r.items || [])])");  // 追加
    expect(modal).not.toContain(">上一页");   // 已移除翻页按钮（只留接续 + 跳转重载）
    expect(modal).not.toContain(">下一页");
  });

  it("「当前页」＝视角所在区间的页数（随滚动估算），并显示已加载页数", () => {
    expect(modal).toContain("已加载 {loadedPages} 页");
    expect(modal).toContain("scrollTop + clientHeight) / scrollHeight");
  });

  it("跳转＝从该页重新加载（清空重来），并提示「重载」", () => {
    expect(modal).toContain("跳转（重载）");
  });
});

describe("多页作品文件夹聚合（2026-10-17 用户需求：98 张别平铺占满素材库）", () => {
  it("按 source_url 聚合成文件夹卡片（多页不再平铺）", () => {
    expect(view).toContain("buildImageCells");          // 聚合逻辑
    expect(view).toContain('kind: "folder"');           // 文件夹单元
    expect(view).toContain("m.provider && m.source_url"); // 只聚图站搜索保存的
  });

  it("文件夹可钻进看各页 + 面包屑返回 + 整夹删除 + 选本页含夹内全部页", () => {
    expect(view).toContain("setFolderView(cell.key)");      // 点文件夹钻进去
    expect(view).toContain("← 返回素材库");                  // 面包屑返回
    expect(view).toContain("deleteFolder");                 // 整夹删除
    expect(view).toContain("m.source_url === folderView");  // 夹内页筛选
    expect(view).toContain("cell.pages.forEach((p) => names.add(p.filename))"); // 选本页含夹内全部页
  });
});

describe("素材详情弹窗 · 竖长图不裁切（2026-10-18 用户实测：图底被截断）", () => {
  // 根因：详情弹窗行容器 flexWrap:"wrap" + 左图盒是直接 flex 项（高度由内容推导）
  // ⇒ 图片 maxHeight:"100%" 循环解析失效，被 overflow:"hidden" 裁底。
  // 修法＝对齐资产库详情 RepoGallery 的可行结构（其源码注释即警告）。
  it("行容器不 flexWrap + overflow hidden（换行⇒高度需求翻倍⇒撑破 maxHeight）", () => {
    expect(detail).not.toContain('flexWrap: "wrap"');
    expect(detail).toContain('display: "flex", gap: 14, overflow: "hidden"');
  });

  it("左图列 0 0 52% 竖排 + 内层 flex:1 盒（maxHeight:100% 才有确定高度可解析）", () => {
    expect(detail).toContain('flex: "0 0 52%"');
    expect(detail).toContain('flexDirection: "column"');
    expect(detail).toContain("flex: 1, minHeight: 0");
  });

  it("右栏 1 1 0% + 独立滚动（basis 0 才有确定高度，内容高不被顶开）", () => {
    expect(detail).toContain('flex: "1 1 0%"');
    expect(detail).toContain('overflowY: "auto"');
  });

  it("图片 contain 完整显示 + 点击进共享 Lightbox（不裁切契约）", () => {
    expect(detail).toContain('objectFit: "contain"');
    expect(detail).toContain("openLightbox(item.url)");
  });

  it("适配语义是「以绑定边为准」的完整展示：竖长图按高、宽长图按宽，⛔ 禁 cover", () => {
    // contain＝scale=min(盒宽/图宽, 盒高/图高)：相对更长的那条边成为绑定边，
    // 另一方向留白 ⇒ 完整展示；cover 会裁切＝本刀要修的 bug 本身。
    expect(detail).not.toContain('objectFit: "cover"');
    expect(detail).toContain('maxWidth: "100%", maxHeight: "100%"');
  });
});

describe("素材卡片 · 发送按钮与资产库对齐（2026-10-18 用户实测：卡片只有删除）", () => {
  // 诉求：上网素材图片卡片与资产库卡片同构——hover 出现「发送至对话 / 发送至对话框 /
  // 删除」三钮（此前只有 wm-del，发送入口只剩详情弹窗与批量选择，用户找不到）。
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  it("图片卡片提供两个发送按钮（onSendAsRecipe/onSendToChat 均贯传到卡片）", () => {
    expect(view).toContain('className="wm-send-recipe"');
    expect(view).toContain('className="wm-send"');
    expect(view).toContain("onSendAsRecipe(cell.m)");
    expect(view).toContain("onSendToChat(cell.m)");
  });

  it("按钮 stopPropagation（不冒泡触发卡片点击＝误开详情）", () => {
    expect(view).toContain("e.stopPropagation(); onSendAsRecipe(cell.m)");
    expect(view).toContain("e.stopPropagation(); onSendToChat(cell.m)");
  });

  it("发送钮只在非选择模式分支渲染，且顺序＝发送至对话→发送至对话框→删除（对齐资产库）", () => {
    const selIdx = view.indexOf("selMode ? (");
    const elseIdx = view.indexOf(") : (", view.indexOf("wm-check", selIdx));
    const recipeIdx = view.indexOf("wm-send-recipe", elseIdx);
    const sendIdx = view.indexOf('className="wm-send"', recipeIdx);
    const delIdx = view.indexOf("wm-del", sendIdx);
    expect(recipeIdx).toBeGreaterThan(elseIdx);   // 在非 selMode 分支内
    expect(sendIdx).toBeGreaterThan(recipeIdx);
    expect(delIdx).toBeGreaterThan(sendIdx);
  });

  it("样式：hover 才显示，定位于删除钮左侧（right 38/70，删除 6）", () => {
    expect(css).toContain(".web-materials-cell:hover .wm-send {");
    expect(css).toContain(".web-materials-cell:hover .wm-send-recipe {");
    expect(css).toContain("top: 6px; right: 38px");
    expect(css).toContain("top: 6px; right: 70px");
  });

  it("详情弹窗底部同样保留两个发送按钮（卡片+详情双入口，不裁一）", () => {
    expect(detail).toContain("发送至对话框");
    expect(detail).toContain("发送至对话");
  });
});

describe("删除确认 + 详情 X/ESC（2026-10-18 用户定案）", () => {
  it("素材单删/整夹/灵感卡删除都先弹确认（此前点删即 unlink 磁盘文件）", () => {
    expect(view).toContain('setConfirmDel({ kind: "image"');
    expect(view).toContain('setConfirmDel({ kind: "folder"');
    expect(view).toContain('setConfirmDel({ kind: "card"');
    expect(view).toContain("文件会从磁盘删除，此操作不可恢复。");
  });

  it("详情弹窗有 ESC 关闭 + 标题栏 X（应用口径「关闭走 X/ESC」）", () => {
    expect(detail).toContain('e.key === "Escape"');
    expect(detail).toContain("关闭（ESC 亦可）");
    expect(detail).toContain("<X size={18} />");
  });
});

describe("素材标签搜索 + 批量发送（2026-10-18）", () => {
  it("搜索接标签/作者/搜索词（原站 tags 可搜），多词 AND", () => {
    expect(view).toContain("...(i.tags || [])");
    expect(view).toContain("terms.every((t) => haystack.includes(t))");
    expect(view).toContain("搜索标题/来源/标签");
  });

  it("批量选择提供 发送至对话框 + 发送至对话（此前只有发送至画布）", () => {
    expect(view).toContain("onBatchSendToChat(picked)");
    expect(view).toContain("onBatchSendAsRecipe(picked)");
    expect(appBody).toContain("onBatchSendToChat={");
    expect(appBody).toContain("onBatchSendAsRecipe={");
  });
});

describe("素材视觉描述（2026-10-18 用户建议）", () => {
  it("素材详情可单独生成视觉描述（原站只有 tag，没有描述）", () => {
    expect(detail).toContain("生成视觉描述");
    expect(detail).toContain("onDescribe");
    expect(view).toContain("describeMaterial");
    expect(view).toContain("WEB_MATERIALS_REPO_ID");
    expect(view).toContain("setGenerationDescription(m.assetId, WEB_MATERIALS_REPO_ID");
  });

  it("素材列表带 assetId/description（后端从主表投影，供描述落库与回显）", () => {
    expect(api).toContain("assetId?: string");
    expect(api).toContain("description?: string");
  });
});
