// @vitest-environment jsdom
// 第 92 刀 · 多元数据插入面板的「一键定妆」按钮（角色底图缺位时按绑定的生图工作流补一张）。
//
// 用户规格原话：「角色底图在没有的情况下，但是有绑定多元数据插入的生图工作流模版时，
// 可以利用该工作流一键生成，生成时会参考角色条目外貌，使用绑定的角色lora和风格lora去生成」。
//
// 本文件只锁**面板这一层**的四件事（链路口径本身在 `lib/portraitBaseImage.test.ts`）：
// ① 按钮真能走完「出提示词 → 提交 → 轮询 → 落 portraits/ → 写回该行底图」这条真实 wire 链；
// ② 判据不过（没绑模板）时按钮禁用，并把原因摆在面板上，不放任一次注定失败的提交；
// ③ 提交被后端判死（422 的 LoRA 槽原因）时原样透出，且**底图不被写回**；
// ④ 定妆期间按钮显示「定妆中…」并禁用，不让人连点起两个 ComfyUI 任务；
// ⑤ 定妆成功即自动写入作品预设（不依赖再点「保存」），批量补齐时后一次落盘必须带上前几张
//    —— 否则「一次批量补完反而把先补的从预设里抹掉」（2026-11-07 埃吉尔底图实锤）。
// ⑥ 点底图缩略图派发 lightbox 事件（根部 <Lightbox> 负责全屏看图；2026-11-08 缩略图此前是死图）。
// ⑦ 定妆产物同时进资产库（用户 2026-11-08 口径）——入库失败只降级回显，不回滚底图绑定。
// ⑧ 已有底图再定妆 ⇒ 两张候选并列、可看大图、保存被禁用；右侧小 × 删到一张才落成底图并自动落盘。
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";

vi.mock("../lib/comfyReadiness", () => ({
  ensureComfyReady: vi.fn(async () => true),
}));

import { MediaInsertModal } from "./MediaInsertModal";
import { ensureComfyReady } from "../lib/comfyReadiness";
import type { ExposedField, Template } from "../api/workflows";
import type { MediaInsertPreset } from "../stores/settings";
import type { PortraitRuntime } from "../lib/portraitBaseImage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const exposed: ExposedField[] = [
  { node_id: "3", field: "text", label: "提示词", control: "textarea", semantic: "prompt", binding: "prompt", default: "" },
  { node_id: "4", field: "neg", label: "负面", control: "textarea", semantic: "negative", binding: "negative_prompt", default: "" },
  { node_id: "5", field: "name", label: "LoRA", control: "text", semantic: "lora", binding: "lora_name", default: "" },
  { node_id: "6", field: "weight", label: "权重", control: "number", semantic: "lora_weight", binding: "lora_weight", default: 0.8 },
  { node_id: "7", field: "image", label: "底图", control: "image", semantic: "base_image", binding: "base_image", default: "" },
  { node_id: "8", field: "w", label: "宽", control: "number", semantic: "latent_width", binding: "latent_width", default: 768 },
  { node_id: "9", field: "h", label: "高", control: "number", semantic: "latent_height", binding: "latent_height", default: 1024 },
];

const TEMPLATE: Template = {
  id: "tpl-1", name: "测试生图模板", source_path: "", exposed, node_order: [],
  input_node_ids: [], output_node_ids: [], created_at: 0, updated_at: 0,
};

const VIDEO_TEMPLATE: Template = {
  id: "vid-1", name: "测试视频模板", source_path: "", node_order: [],
  exposed: [
    { node_id: "329", field: "prompt", label: "提示词", control: "textarea", semantic: "prompt", binding: "prompt", default: "" },
    { node_id: "81", field: "image", label: "参考图1", control: "image", semantic: "image", binding: "reference_image", default: "" },
  ],
  input_node_ids: [], output_node_ids: [], created_at: 0, updated_at: 0,
};

const PRESET: MediaInsertPreset = {
  templateId: "tpl-1",
  loraMode: "multi",
  appearanceSource: "worldbook",
  characterLoras: { 甲: { loraName: "role_a.safetensors", loraWeight: 0.9 } },
  styleLora: "style.safetensors",
  styleLoraWeight: 0.6,
};

const RUNTIME: PortraitRuntime = {
  chat: { baseUrl: "http://x/v1", apiKey: "k", modelName: "m" },
  comfyUrl: "http://127.0.0.1:8188",
  presetDir: "", presetName: "", userName: "",
  embed: { baseUrl: "http://e/v1", apiKey: "ek", modelName: "em" },
};

const SAVED_PATH = "D:/works/repo-1/portraits/00001_.png";

let container: HTMLDivElement;
let root: Root;

async function mount(el: ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(el); });
  await settle();
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.unstubAllGlobals();
});

/** 定时器级联的 microtask 链要靠宏任务排空（一次 setTimeout(0) ≈ 一整轮链）。 */
async function settle(rounds = 3) {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0); }); });
  }
}

interface NetworkOptions {
  holdPrompt?: boolean;      // 提示词请求挂起（用于断言「定妆中…」）
  submitDetail?: string;     // 非空 ⇒ /comfyui/submit 返 422 + detail
  savedPath?: string;
  savedPaths?: string[];     // 逐次落盘路径（批量补齐时分辨每张图）
  indexFail?: boolean;       // 资产库入库失败（502）
}

/** 一条链四个端点全部走 fetch：端点、请求体、返回形状都是真实 wire 合同，不经任何注入。 */
function stubNetwork(opts: NetworkOptions = {}) {
  const seen: { path: string; body: Record<string, unknown> }[] = [];
  let releaseHold: () => void = () => {};
  const hold = new Promise<void>((resolve) => { releaseHold = resolve; });
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input);
    const body = typeof init?.body === "string"
      ? JSON.parse(init.body) as Record<string, unknown> : {};
    seen.push({ path, body });
    if (path.includes("/ai/prompt/portrait")) {
      if (opts.holdPrompt) await hold;
      return json({ prompt: "best quality\n1girl, solo", appearance: "甲：银发", gender: "female", appearance_origin: "entry", pose: "双手背在身后、手指自然交叠" });
    }
    if (path.includes("/loras") && !path.includes("/loras/available")) {
      // 触发词表：定妆链按生效 LoRA 的文件名精确查表后机械前置（与自动插画同一条真源）。
      return json({ items: [
        { lora_name: "style.safetensors", triggers: ["style_x"], missing: false, suggested_prompt: "masterpiece" },
        { lora_name: "role_a.safetensors", triggers: ["char_a"], missing: false, suggested_prompt: "" },
      ] });
    }
    if (path.includes("/comfyui/submit")) {
      if (opts.submitDetail) return json({ detail: opts.submitDetail }, 422);
      return json({ ok: true, prompt_id: "pid-1" });
    }
    if (path.includes("/comfyui/result")) {
      return json({
        status: "completed",
        images: [{ filename: "00001_.png", subfolder: "portraits", type: "output" }],
        videos: [], audios: [], texts: [],
      });
    }
    if (path.includes("/comfyui/save-local")) {
      const nth = seen.filter((call) => call.path.includes("/comfyui/save-local")).length - 1;
      return json({ ok: true, path: opts.savedPaths?.[nth] ?? opts.savedPath ?? SAVED_PATH });
    }
    if (path.includes("/rag/index-generation")) {
      // 定妆产物入库与自动插画同一个端点；失败时后端返 502，前端只降级回显。
      return opts.indexFail ? json({ detail: "embedding 不可用" }, 502) : json({ ok: true });
    }
    if (path.includes("/appearance/")) {
      // 面板挂载时会扫「正文外貌锚」；本用例不关心其内容，给一份合法空账本即可。
      return json({ repo_id: "repo-1", entries: [], grouped: {}, counts: {} });
    }
    if (path.includes("/ai/prompt/profile/defaults")) {
      // 挂载时预填 Anima 质量／负面行：后端恒返回这两个键，空串＝不预填。
      return json({ quality_prompt: "", negative_prompt: "" });
    }
    return json({});
  }));
  return { seen, release: () => releaseHold() };
}

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button"))
    .find((b) => (b.textContent || "").includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found as HTMLButtonElement;
}

async function click(el: HTMLButtonElement) {
  await act(async () => { el.click(); });
}

function mountModal(
  preset: MediaInsertPreset = PRESET,
  runtime: PortraitRuntime | undefined = RUNTIME,
  templates: Template[] = [TEMPLATE],
  onPersist?: (preset: MediaInsertPreset) => void,
  onPortraitReady?: (name: string, preset: MediaInsertPreset, path: string) => void,
) {
  return mount(
    <MediaInsertModal
      templates={templates}
      modelsDir=""
      repoId="repo-1"
      outputDir="D:/works"
      preset={preset}
      portraitRuntime={runtime}
      onPersist={onPersist}
      onPortraitReady={onPortraitReady}
      onSave={() => {}}
      onClose={() => {}}
    />,
  );
}

describe("多元数据插入 · 一键定妆", () => {
  it("ComfyUI 启动超时 ⇒ 任务不提交，提示可重试", async () => {
    const { seen } = stubNetwork();
    vi.mocked(ensureComfyReady).mockResolvedValueOnce(false);
    await mountModal();
    await click(button("一键定妆"));
    await settle();

    expect(container.textContent).toContain("ComfyUI 启动超时");
    expect(seen.filter((c) => c.path.includes("/comfyui/submit"))).toEqual([]);
  });

  it("点一次把底图链跑完并写回该行：出提示词→提交 tpl-1→落 portraits/→回显来源", async () => {
    const { seen } = stubNetwork();
    const persisted: MediaInsertPreset[] = [];
    await mountModal(PRESET, RUNTIME, [TEMPLATE], (p) => persisted.push(p));
    await click(button("一键定妆"));
    await settle();

    const portraitCall = seen.find((c) => c.path.includes("/ai/prompt/portrait"));
    expect(portraitCall?.body).toMatchObject({
      name: "甲", repo_id: "repo-1", output_dir: "D:/works",
      rating: "sfw", aspect_ratio: "9:16", appearance_source: "worldbook",
    });
    const submitCall = seen.find((c) => c.path.includes("/comfyui/submit"));
    expect(submitCall?.body).toMatchObject({ template_id: "tpl-1", lora_mode: "multi" });
    // 触发词由程序前置后才提交（角色那条落在最前面）：没有这一步，挂上的 LoRA 不会真生效。
    expect(String(submitCall?.body.prompt)).toContain("char_a, style_x, masterpiece, best quality");
    expect(String(JSON.stringify(submitCall?.body.values))).toContain("char_a");
    const saveCall = seen.find((c) => c.path.includes("/comfyui/save-local"));
    expect(saveCall?.body).toMatchObject({ subdir: "portraits", repo_id: "repo-1" });
    // 底图产物也要进资产库（用户 2026-11-08 口径），且带模板名与生效 LoRA 栈。
    const indexCall = seen.find((c) => c.path.includes("/rag/index-generation"));
    expect(indexCall?.body).toMatchObject({ thread_id: "repo-1", template_name: "测试生图模板" });
    expect(String(indexCall?.body.image_url)).toContain(encodeURIComponent(SAVED_PATH));
    expect(String(indexCall?.body.lora_names)).toContain("role_a.safetensors");
    expect(String(indexCall?.body.lora_names)).toContain("style.safetensors");

    // 底图真被写回该行：预览图与右上角删除钮都是 baseImage 非空才渲染的。
    expect(container.querySelector("img")?.getAttribute("src")).toContain(encodeURIComponent(SAVED_PATH));
    expect(container.textContent).toContain("底图已生成");
    expect(container.textContent).toContain("产物已入资产库");
    expect(container.querySelector('button[title="删除这张底图"]')).toBeTruthy();
    expect(container.textContent).toContain("角色条目");
    expect(container.textContent).toContain("双手背在身后");
    // 成功即自动落预设：绑定真进了 characterLoras，且没要求再点「保存」。
    expect(persisted).toHaveLength(1);
    expect(persisted[0].characterLoras?.甲?.baseImage).toBe(SAVED_PATH);
    expect(persisted[0]).toMatchObject({ templateId: "tpl-1", loraMode: "multi", styleLora: "style.safetensors" });
    expect(container.textContent).toContain("已自动写入本作品预设");
    expect(container.textContent).not.toContain("点「保存」才会写进本作品预设");
  });

  it("点底图缩略图派发 lightbox 事件（全屏看图），地址与缩略图同源", async () => {
    stubNetwork();
    await mountModal();
    await click(button("一键定妆"));
    await settle();

    const thumb = Array.from(container.querySelectorAll("img"))
      .find((el) => (el.getAttribute("src") || "").includes(encodeURIComponent(SAVED_PATH)));
    expect(thumb).toBeTruthy();
    expect(thumb!.style.cursor).toBe("zoom-in");

    // 面板只负责发事件，真正的全屏看图由根部 <Lightbox> 渲染（App.tsx 常驻）。
    const opened: string[] = [];
    const onLightbox = (e: Event) => opened.push(String((e as CustomEvent).detail));
    window.addEventListener("lightbox", onLightbox);
    await act(async () => { thumb!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    window.removeEventListener("lightbox", onLightbox);

    expect(opened).toEqual([thumb!.getAttribute("src")]);
  });

  it("生成中关闭面板：生成链继续，成功仍回调 onPersist（宿主据此弹确认窗）", async () => {
    const { release } = stubNetwork({ holdPrompt: true });
    const persisted: MediaInsertPreset[] = [];
    const ready: { name: string; path: string }[] = [];
    await mountModal(
      PRESET, RUNTIME, [TEMPLATE],
      (p) => persisted.push(p),
      (name, _preset, path) => ready.push({ name, path }),
    );
    await click(button("一键定妆"));
    await settle(1);

    // 模拟用户直接关掉面板：组件卸载，但生成 Promise 不得被取消。
    act(() => { root.unmount(); });
    root = null as unknown as Root;
    release();
    await settle(5);

    expect(persisted).toHaveLength(1);
    expect(persisted[0].characterLoras?.甲?.baseImage).toContain("portraits");
    // 宿主靠这个回调弹「底图生成完毕」确认窗并定位角色。
    expect(ready).toEqual([{ name: "甲", path: SAVED_PATH }]);
  });

  it("没绑定生图模板 ⇒ 按钮禁用并给出原因，不发任何请求", async () => {
    const { seen } = stubNetwork();
    await mountModal({ ...PRESET, templateId: "" });

    const gateHint = container.textContent || "";
    expect(gateHint).toContain("未选择生图工作流模板");
    expect(button("一键定妆").disabled).toBe(true);
    await click(button("一键定妆"));
    await settle();
    expect(seen.filter((c) => c.path.includes("/ai/prompt/portrait"))).toEqual([]);
    expect(seen.filter((c) => c.path.includes("/comfyui/submit"))).toEqual([]);
  });

  it("后端 422 判死（模板无 LoRA 槽）⇒ 原因原样透出，底图不写回", async () => {
    const { seen } = stubNetwork({
      submitDetail: "模板没有已标注且受支持的 LoRA 加载器，无法应用所选 LoRA 模式",
    });
    await mountModal();
    await click(button("一键定妆"));
    await settle();

    expect(container.textContent).toContain("LoRA 加载器");
    expect(container.textContent).not.toContain("底图已生成");
    expect(seen.filter((c) => c.path.includes("/comfyui/save-local"))).toEqual([]);
  });

  it("定妆进行中：按钮文案切成「定妆中…」并禁用，落地后底图补位、按钮转为「重新定妆」", async () => {
    const { release } = stubNetwork({ holdPrompt: true });
    await mountModal();
    await click(button("一键定妆"));
    await settle(1);

    expect(button("定妆中…").disabled).toBe(true);
    expect(container.textContent).not.toContain("底图已生成");
    release();
    await settle();
    expect(container.textContent).toContain("底图已生成");
    expect(button("重新定妆")).toBeTruthy();
    expect(container.querySelector('button[title="删除这张底图"]')).toBeTruthy();
  });
});

// 第 8 刀（2026-11-08 用户口径）：「如果已经有底图的情况下还要一键生成底图,那就是有两个底图
// 列出来可以观看但是不能保存,必须删掉一个才行,所以说底图右上角要有删除小按钮」。
describe("多元数据插入 · 重定妆候选与删除", () => {
  const EXISTING: MediaInsertPreset = {
    ...PRESET,
    characterLoras: { 甲: { loraName: "role_a.safetensors", loraWeight: 0.9, baseImage: "D:/works/old.png" } },
  };

  it("已有底图再定妆 ⇒ 两张候选并列、保存被禁用、不自动改预设；删到一张即定稿并落盘", async () => {
    stubNetwork();
    const persisted: MediaInsertPreset[] = [];
    await mountModal(EXISTING, RUNTIME, [TEMPLATE], (p) => persisted.push(p));

    await click(button("重新定妆"));
    await settle();

    // 两张候选都渲染得出来，且都能点开大图。
    const sources = Array.from(container.querySelectorAll("img"))
      .map((el) => el.getAttribute("src") || "");
    expect(sources.some((s) => s.includes(encodeURIComponent("D:/works/old.png")))).toBe(true);
    expect(sources.some((s) => s.includes(encodeURIComponent(SAVED_PATH)))).toBe(true);
    const opened: string[] = [];
    const onLightbox = (e: Event) => opened.push(String((e as CustomEvent).detail));
    window.addEventListener("lightbox", onLightbox);
    const newThumb = Array.from(container.querySelectorAll("img"))
      .find((el) => (el.getAttribute("src") || "").includes(encodeURIComponent(SAVED_PATH)))!;
    await act(async () => { newThumb.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    window.removeEventListener("lightbox", onLightbox);
    expect(opened).toEqual([newThumb.getAttribute("src")]);

    // 没做出选择前：保存被禁用、预设里也没被静默改成新图。
    expect(container.textContent).toContain("两张候选底图");
    expect(button("保存").disabled).toBe(true);
    expect(button("重新定妆").disabled).toBe(true);
    expect(persisted).toEqual([]);

    // 右上角小 × 删掉旧图 ⇒ 剩下一张定为底图并自动落盘。
    const deletes = container.querySelectorAll('button[title="删掉这张候选底图（剩一张后才能保存）"]');
    expect(deletes).toHaveLength(2);
    const oldDelete = Array.from(deletes).find((el) => {
      const wrapper = el.parentElement;
      return (wrapper?.querySelector("img")?.getAttribute("src") || "")
        .includes(encodeURIComponent("D:/works/old.png"));
    }) as HTMLButtonElement;
    await click(oldDelete);
    await settle();

    expect(container.textContent).not.toContain("两张候选底图");
    expect(container.textContent).toContain("已保留这张底图并写入本作品预设");
    expect(button("保存").disabled).toBe(false);
    expect(persisted).toHaveLength(1);
    expect(persisted[0].characterLoras?.甲?.baseImage).toBe(SAVED_PATH);
  });

  it("入库失败 ⇒ 底图照常写入，只把「资产库入库失败」如实回显", async () => {
    stubNetwork({ indexFail: true });
    const persisted: MediaInsertPreset[] = [];
    await mountModal(PRESET, RUNTIME, [TEMPLATE], (p) => persisted.push(p));

    await click(button("一键定妆"));
    await settle();

    expect(container.textContent).toContain("底图已生成");
    expect(container.textContent).toContain("资产库入库失败");
    expect(persisted[0].characterLoras?.甲?.baseImage).toBe(SAVED_PATH);
  });
});

// 第 94 刀：首尾帧视频＝参考生成，保存前必须把「缺底图 / 模板无参考槽」摆在明面上，
// 并允许把缺的底图一次性补齐（生成仍走 92/93 的同一条定妆链）。
describe("多元数据插入 · 首尾帧参考生成确认", () => {
  const FIRSTLAST: MediaInsertPreset = {
    ...PRESET, firstlast: true, videoMode: "firstlast", videoTemplateId: "vid-1",
  };

  it("缺底图 + 模板有参考槽 ⇒ 明确列出缺图角色，一键补齐后写回该行", async () => {
    const { seen } = stubNetwork();
    await mountModal(FIRSTLAST, RUNTIME, [TEMPLATE, VIDEO_TEMPLATE]);

    expect(container.textContent).toContain("图片3、4、5…");
    expect(container.textContent).toContain("缺失底图：甲");
    const bulk = button("一键补齐缺失底图（1）");
    expect(bulk.disabled).toBe(false);

    await click(bulk);
    await settle();

    expect(seen.filter((call) => call.path.includes("/ai/prompt/portrait"))).toHaveLength(1);
    expect(seen.filter((call) => call.path.includes("/comfyui/submit"))).toHaveLength(1);
    expect(seen.filter((call) => call.path.includes("/comfyui/save-local"))).toHaveLength(1);
    expect(container.querySelector("img")?.getAttribute("src")).toContain(encodeURIComponent(SAVED_PATH));
    // 补齐后回显消失：不再吓唬用户「视频模型只能猜外貌」
    expect(container.textContent).not.toContain("缺失底图：甲");
  });

  it("批量补齐两张：后一次自动落盘必须带上前一张（不能闭包点击那一刻的旧行集）", async () => {
    stubNetwork({ savedPaths: ["D:/works/甲.png", "D:/works/乙.png"] });
    const persisted: MediaInsertPreset[] = [];
    await mountModal(
      { ...FIRSTLAST, characterLoras: { 甲: { loraName: "role_a.safetensors" }, 乙: {} } },
      RUNTIME, [TEMPLATE, VIDEO_TEMPLATE], (p) => persisted.push(p),
    );
    expect(container.textContent).toContain("缺失底图：甲、乙");

    await click(button("一键补齐缺失底图（2）"));
    await settle(8);

    expect(persisted).toHaveLength(2);
    // 第二次落盘若只看 state 快照，甲的新底图会在这份预设里消失——这正是要防的「补完反而丢」。
    expect(persisted[1].characterLoras?.甲?.baseImage).toBe("D:/works/甲.png");
    expect(persisted[1].characterLoras?.乙?.baseImage).toBe("D:/works/乙.png");
  });

  it("视频模板没有参考槽 ⇒ 明说参考图无法上传（不假装已锁定外貌）", async () => {
    stubNetwork();
    await mountModal(FIRSTLAST, RUNTIME, [TEMPLATE]);
    expect(container.textContent).toContain("没有参考图槽");
    expect(container.textContent).toContain("视频模型只能猜外貌");
  });

  it("角色底图齐全 ⇒ 不出现缺失告警与补齐按钮", async () => {
    stubNetwork();
    await mountModal(
      { ...FIRSTLAST, characterLoras: { 甲: { baseImage: "D:/works/甲.png" } } },
      RUNTIME, [TEMPLATE, VIDEO_TEMPLATE],
    );
    expect(container.textContent).toContain("首尾帧视频按参考生成");
    expect(container.textContent).not.toContain("缺失底图");
    expect(container.textContent).not.toContain("补齐缺失底图");
  });

  it("角色卡模式没有角色底图 ⇒ 直接判死参考生成，不给补齐按钮", async () => {
    stubNetwork();
    await mountModal(
      { ...FIRSTLAST, appearanceSource: "character_card" },
      RUNTIME, [TEMPLATE, VIDEO_TEMPLATE],
    );
    expect(container.textContent).toContain("角色卡模式不读取角色底图");
    expect(container.textContent).not.toContain("补齐缺失底图");
  });
});
