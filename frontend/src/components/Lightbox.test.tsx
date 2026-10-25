// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { Lightbox, openLightbox } from "./Lightbox";

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function mount() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root!.render(<Lightbox />));
  return container;
}

afterEach(() => {
  if (root) act(() => root!.unmount());
  if (container) container.remove();
  root = null;
  container = null;
});

describe("Lightbox 全屏看图（2026-10-16 增强）", () => {
  it("openLightbox 派发事件 → 渲染大图；ESC 关闭", () => {
    const el = mount();
    expect(el.querySelector(".lightbox-mask")).toBeNull();
    act(() => openLightbox("http://x/a.png"));
    const img = el.querySelector(".lightbox-img") as HTMLImageElement;
    expect(img?.getAttribute("src")).toBe("http://x/a.png");
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(el.querySelector(".lightbox-mask")).toBeNull();
  });

  it("滚轮缩放（以鼠标为焦点）改变 transform；双击复位", () => {
    const el = mount();
    act(() => openLightbox("http://x/a.png"));
    const mask = el.querySelector(".lightbox-mask") as HTMLDivElement;
    const img = el.querySelector(".lightbox-img") as HTMLImageElement;
    act(() => {
      mask.dispatchEvent(new WheelEvent("wheel", {
        deltaY: -120, clientX: 300, clientY: 200, bubbles: true, cancelable: true,
      }));
    });
    expect(img.style.transform).toMatch(/scale\(1\.12/);
    act(() => {
      mask.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    });
    expect(img.style.transform).toMatch(/scale\(1\)/);
  });

  it("点背景关闭；点图片本身不关闭（拖拽结束的 click 也不关）", () => {
    const el = mount();
    act(() => openLightbox("http://x/a.png"));
    const mask = el.querySelector(".lightbox-mask") as HTMLDivElement;
    const img = el.querySelector(".lightbox-img") as HTMLImageElement;
    act(() => {
      img.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(el.querySelector(".lightbox-mask")).not.toBeNull();
    act(() => {
      mask.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(el.querySelector(".lightbox-mask")).toBeNull();
  });
});

describe("上网素材详情（左图右信息，与生成内容一致）", () => {
  it("图片卡点击（非选择模式）→ 打开素材详情面板；选择模式仍勾选", () => {
    // ⚠ jsdom 下 import.meta.url 不是 file: 协议（fs shim 会拒）⇒ 用相对 cwd 的路径读源码
    const view = readFileSync("src/views/WebMaterialsView.tsx", "utf-8");
    expect(view).toContain('import { WebMaterialDetailModal } from "../components/WebMaterialDetailModal"');
    expect(view).toContain("if (selMode) { toggleSel(cell.m.filename); return; }");
    expect(view).toContain("setDetailMaterial(cell.m)");
    expect(view).toContain("<WebMaterialDetailModal");
  });

  it("详情面板：左图右信息 + 图内点击进全屏 Lightbox + 标签可复制", () => {
    const panel = readFileSync("src/components/WebMaterialDetailModal.tsx", "utf-8");
    expect(panel).toContain("openLightbox(item.url)");
    expect(panel).toContain("素材详情");

    // 2026-10-18 修竖长图裁切：左列 0 0 52% 竖排 + 内层 flex:1 盒 + 右栏 1 1 0%
    // （对齐资产库详情 RepoGallery 结构；旧 1 1 56%/1 1 38% + flexWrap 会裁图底）
    expect(panel).toContain('flex: "0 0 52%"');     // 左侧完整图片列
    expect(panel).toContain('flex: "1 1 0%"');      // 右侧信息（来源/作者/评级/标签）
    expect(panel).not.toContain('flexWrap: "wrap"'); // 行容器禁换行（撑破 maxHeight）
    // 同款分层：来源 / 文件 / 标签三区（分区容器带分隔线）
    expect(panel).toContain('<Section title="来源" first>');
    expect(panel).toContain('<Section title="文件">');
    expect(panel).toContain('borderTop: first ? "none" : "1px solid var(--border)"');
  });

  it("上网素材与资产库对齐：有「发送至对话框」与「发送至对话」两个入口", () => {
    const panel = readFileSync("src/components/WebMaterialDetailModal.tsx", "utf-8");
    expect(panel).toContain("发送至对话框");
    expect(panel).toContain("发送至对话");
    expect(panel).toContain("onSendToChat(item)");
    expect(panel).toContain("onSendAsRecipe(item)");

    const view = readFileSync("src/views/WebMaterialsView.tsx", "utf-8");
    expect(view).toContain("onSendToChat?: (item: WebMaterial) => void");
    expect(view).toContain("onSendAsRecipe={onSendAsRecipe}");

    const app = readFileSync("src/AppBody.tsx", "utf-8");
    expect(app).toContain('title: "发送至对话框", payload: { text: "", images: [m.url] }');
    expect(app).toContain('title: "发送至对话", payload: { text: recipe, images: [m.url] }');
  });
});

describe("资产库详情重排 + 画布节点缩略图（2026-10-16 建议落地）", () => {
  it("资产库「生成详情」＝左图右信息（对齐画布），图点击进共享 Lightbox，底部有发送/关闭", () => {
    const src = readFileSync("src/components/RepoGallery.tsx", "utf-8");
    expect(src).toContain("左图右信息（2026-10-16 重排");
    expect(src).toContain("openLightbox(active.image_url)");
    expect(src).toContain('flex: "0 0 52%"');     // 左：固定比例（不被内容顶开）
    expect(src).toContain('flex: "1 1 0%"');      // 右：basis 0 才有确定高度可滚
    expect(src).toContain("发送至对话框");
    expect(src).toContain("发送至对话");
    expect(src).toContain("查看原图");             // 原有入口保留
  });

  it("详情面板滚动修复：覆盖 .modal 基线的 overflow-y:auto + 原图加载失败回退缩略图", () => {
    const src = readFileSync("src/components/RepoGallery.tsx", "utf-8");
    // 根因：`.modal` 基线自带 overflow-y:auto，外层滚动吃掉高度约束 ⇒ 右栏不滚、按钮被穿过
    expect(src).toContain('overflow: "hidden"');
    expect(src).toContain("不要 flexWrap");        // 换行会把容器高度需求翻倍
    expect(src).toContain("el.dataset.fb");        // onError 只回退一次，避免死循环
  });

  it("画布节点优先用缩略图（复用 P0-2 派生层），缺项回退原图；详情仍用原图", () => {
    const proj = readFileSync("src/lib/canvasProjection.ts", "utf-8");
    // ⚠ 后端两个分支键名不一致（thumb_url / thumbUrl）⇒ 两个都要认，否则静默失效
    expect(proj).toContain('thumbUrls: gensFor.map((g) => g.thumbUrl || g.thumb_url || "")');
    const card = readFileSync("src/components/canvas/CardNodeComponent.tsx", "utf-8");
    expect(card).toContain("thumbs?.[i] || full");
    expect(card).toContain("sameThumbs");          // memo 必须比较，否则 thumb 后到不重渲
  });
});

describe("生成溯源元数据链路（模板 / 模型 / LoRA 能真的显示出来）", () => {
  it("入库端点请求模型与前端 API 都带三个字段（否则经此链路的图永久为空）", () => {
    const api = readFileSync("src/api/ai/rag.ts", "utf-8"); // 2026-10-24 api/ai 拆分：溯源元数据链在 rag 域
    expect(api).toContain("template_name?: string; model_name?: string; lora_names?: string;");
    expect(api).toContain('template_name: data.template_name || ""');
  });

  it("发送至对话/对话框把元数据带进 payload 并随图入库", () => {
    const modal = readFileSync("src/components/SendToChatModal.tsx", "utf-8");
    expect(modal).toContain("templateName?: string;");
    expect(modal).toContain('template_name: payload.templateName || ""');
    const app = readFileSync("src/AppBody.tsx", "utf-8");
    expect(app).toContain('templateName: g.templateName || g.template_name || ""');
  });

  it("资产库详情右栏分层：6 项参数走「分区 + 分隔线 + 两列键值」而非平铺", () => {
    const src = readFileSync("src/components/RepoGallery.tsx", "utf-8");
    // 分区容器（小标题 + 上分隔线 + 留白）
    expect(src).toContain('<DetailSection title="生成参数" first>');
    expect(src).toContain('borderTop: first ? "none" : "1px solid var(--border)"');
    // 六个键值行用统一两列组件（label 定宽 → 对齐稳，不靠全角空格）
    expect(src).toContain('<DetailKV label="模板">');
    expect(src).toContain('<DetailKV label="模型">');
    expect(src).toContain('<DetailKV label="LoRA">');
    expect(src).toContain('<DetailKV label="尺寸">{detailImgSize.w} × {detailImgSize.h}</DetailKV>');
    expect(src).toContain('<DetailKV label="生成时间">');
    expect(src).toContain('<DetailKV label="作品">');
    // 其余区块也各成分区
    expect(src).toContain('title="提示词"');
    expect(src).toContain('<DetailSection title="视觉描述">');
    expect(src).toContain('<DetailSection title="标签">');
    expect(src).toContain('<DetailSection title="派生来源">');
    expect(src).toContain('<DetailSection title="偏好比较">');
  });
});
