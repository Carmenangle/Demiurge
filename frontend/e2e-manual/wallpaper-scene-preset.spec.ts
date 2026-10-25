import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { decode } from "jpeg-js";

/**
 * 真实 vite + 真实后端 + 真实浏览器：把壁纸在 Wallpaper Engine 里的预设「部署」到应用内。
 *
 * 验的是用户实锤的那件事：右侧栏选壁纸时，选项要是这张作品在 WE 上的真选项，
 * 改了要立刻反映到背景上，而且不能为此重新解一遍包（大包解码实测 16 秒）。
 *
 * 3346715292 这张作品：WE 生效值 = 夜晚（display=1）、人物1、人物位置 X 已被用户改过。
 * 渲染清单实际消费的键第四刀只有 6 条（display / hrbigb / hrbigbbreath / newproperty2 / x9 / y9）；
 * 2026-10-24 参数分层补齐后新增文字/粒子/云层流动/光雾等绑定（面板自动变多，见 BOUND_LABELS）。
 * 仍未实现的族（音频条、鼠标尾迹、飘动效果、镜头位移…）一条都不许摆出来——摆了就是改了没反应的假控件。
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-scene-preset");
// 取左侧内容区那一段：右侧 430px 是拉绳面板，会盖住画面
const SCENE_CLIP = { x: 80, y: 80, width: 620, height: 620 };
// 桌面壳的 dev vite 会把 VITE_API_BASE 指到壳自己拉起的后端（8011）；壳没跑时那条链路必然
// Failed to fetch。需要单独验壁纸时，另起一个指向隔离后端(8011)的临时 vite(5198)，
// 用 E2E_APP_URL 指过去，并串行跑（`--workers=1`）。
/**
 * ⚠ e2e 环境三坑（2026-10-24 textinput 刀实测踩全，跑前必读）：
 * 1. vite 代理目标必须是**新代码后端**（API 面有新路由时旧进程 404/旧表连锁假红）；
 * 2. 起 vite 必须让页面拿相对 API_BASE（5198/5197 的一次性配置里
 *    `define: { "import.meta.env.VITE_API_BASE": '"/api"' }`）——`api/client.ts` 的默认值是
 *    绝对 http://127.0.0.1:8010/api，会直打用户旧后端并吃 CORS；
 * 3. **Git Bash 下不能用进程 env 传 `VITE_API_BASE=/api`**：MSYS 会把 `/api` 路径转换成
 *    `file:///D:/.../Git/api`（argv 与 export 赋值都逃不掉），只能走 define。
 */
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "3346715292";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3346715292\preview.gif`;

// 清单真正消费的键（渲染侧 userBindings 的实测结果）
const BOUND_LABELS = [
  "时间选择 Timing",
  "人物 character",
  "呼吸效果 Breath",
  "人物比例大小 Character proportion size",
  "人物位置X",
  "人物位置Y",
  // 2026-10-24 参数分层补齐：文字/粒子/云层流动落地后，这些键的渲染器真消费了，面板该自动多出这几条
  "背景云层流动",
  "云层运动速度 Cloud movement speed",
  "光 Shine",
  "雾 Fog",
];
// project.json 里有、但渲染器仍不消费的键：一条都不许出现在面板上
// （音频感应/鼠标尾迹/飘动效果三族尚未实现 ⇒ 面板摆出来就是撒谎）
const UNBOUND_LABELS = [
  "鼠标尾迹Mouse Trail",
  "落叶尾迹Mouse Trail",
  "鼠标尾迹颜色Mouse Trail Color",
  "音频条Audio Bar",
  "音频条颜色 Audio Bar Color",
  "组件随音频感应变化大小",
  "飘动效果 Drifting effect",
  "镜头大小 Lens size",
];

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

const wallpaper = {
  id: "we-preset",
  path: SCENE_PREVIEW,
  name: "【晨夜双模式 4K自定义】八奈见杏菜——夜莺Night",
  kind: "image",
  workshopId: SCENE_ID,
  scenePkg: true,
  sceneSource: `workshop:${SCENE_ID}`,
};

/** 记录 scene-pkg 请求次数与写回的 settings，用来证明「热换预设不重解包」+「预设真的落盘」。 */
async function instrument(page: Page) {
  const state: { pkgRequests: number; saved: Record<string, unknown>[] } = { pkgRequests: 0, saved: [] };
  await page.route("**/api/user-state/wallpapers/scene-pkg*", async (route) => {
    state.pkgRequests += 1;
    await route.continue();
  });
  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      state.saved.push(body);
      await route.fulfill({ json: { ok: true } });
      return;
    }
    await route.fulfill({
      json: {
        repos: null,
        settings: {
          theme: "liquid-glass",
          liquidGlass: {
            wallpapers: [wallpaper],
            activeWallpaperId: wallpaper.id,
            wallpaperFit: "cover",
            // 冻住待机动画再比像素：这张作品有常驻呼吸/云层漂移，实测同一状态连拍两张的
            // 差异高达 55%，任何「换预设前后画面变了」的判据都会被它淹掉。
            // 暂停态下垫片会挂起 rAF，所以本用例同时依赖三条补帧规则：
            // 开局一帧都不画 ⇒ 补帧；换清单 / 换填充方式 ⇒ 补帧；贴图在定格之后才解码完 ⇒ 补帧。
            // 见 scenePlayer.ts 垫片。
            wallpaperPaused: true,
            scrim: 0,
            wallpaperBlur: 0,
            ropeShown: true,
          },
        },
      },
    });
  });
  await page.route("**/api/user-state/proxy-status*", (route) => route.fulfill({
    json: {
      listening: false, address: "", source: "none", system_address: "",
      manual_address: "", candidates: [],
    },
  }));
  return state;
}

const setIsolate = (page: Page, enable: boolean) => page.evaluate(([css, on]: [string, boolean]) => {
  const existing = document.getElementById("e2e-isolate");
  if (on && !existing) {
    const style = document.createElement("style");
    style.id = "e2e-isolate";
    style.textContent = css;
    document.head.appendChild(style);
  }
  if (!on) existing?.remove();
}, [ISOLATE_CSS, enable]);

/**
 * 取一张「真的画了壁纸、而且是定格那一帧」的画面。三道关卡都是真机踩出来的：
 * 1) 隔离：把 UI / 极光 / 预览层摘掉，clip 里剩下的才只有背景——直接对场景宿主做元素截图
 *    取的是合成后的页面矩形，拍到的是盖在上面的界面；
 * 2) 补帧：暂停态下 rAF 是挂起的，而摘 UI 会重建合成层，画布上没有帧可贴 ⇒ 截图整片全白
 *    （实测亮度 255）。所以先走产品自己的通道：窗口缩放 → 背景层请播放器补一帧；
 * 3) 幂等：连拍两张逐像素相同才信这一帧。垫片把暂停期的时间戳钉死了（见 scenePlayer.ts
 *    的 frozenNow），同一状态重画必然同帧；热重建还没落地时这两张会不同 ⇒ 继续等。
 */
const canvasShot = async (page: Page, name?: string) => {
  await setIsolate(page, true);
  try {
    let prev: Buffer | null = null;
    let shot: Buffer | null = null;
    for (let i = 0; i < 12; i += 1) {
      await page.evaluate(() => window.dispatchEvent(new Event("resize")));
      await page.waitForTimeout(600);
      prev = shot;
      shot = await page.screenshot({ type: "jpeg", quality: 92, clip: SCENE_CLIP });
      if (prev && diffRatio(prev, shot) < 0.005) break;
    }
    const out = shot;
    if (!out) throw new Error("一次画面都没截到");
    if (name) writeFileSync(resolve(SHOT_DIR, name), out);
    if (luma(out) > 250) {
      throw new Error(`截出来是空白画布（亮度 ${luma(out).toFixed(0)}）⇒ 暂停态没补上帧，像素判据全部不可信`);
    }
    return out;
  } finally {
    await setIsolate(page, false);
  }
};

/** 画面平均亮度（0~255）。像素差只说「变了」，亮度能说「换成了哪一张」。 */
function luma(buffer: Buffer): number {
  const img = decode(buffer, { useBuffer: true });
  let sum = 0;
  let n = 0;
  for (let i = 0; i < img.data.length; i += 4 * 9) {
    sum += 0.299 * img.data[i] + 0.587 * img.data[i + 1] + 0.114 * img.data[i + 2];
    n += 1;
  }
  return sum / Math.max(1, n);
}

/**
 * 两张画面的差异比例（降采样 + 阈值）。
 * 为什么不用 Buffer.equals：这张作品有常驻待机动画，任意两帧都不相同，
 * 「像素不一样」证明不了预设生效。只有把每通道变化门限抬到 30、再要求成片像素变化，
 * 才能把呼吸/发丝抖动和「整张背景换掉／人物消失」这类真内容切换分开。
 */
function diffRatio(a: Buffer, b: Buffer): number {
  const one = decode(a, { useBuffer: true });
  const two = decode(b, { useBuffer: true });
  if (one.width !== two.width || one.height !== two.height) return 1;
  let changed = 0;
  let total = 0;
  for (let i = 0; i < one.data.length; i += 4 * 9) {
    total += 1;
    const dr = Math.abs(one.data[i] - two.data[i]);
    const dg = Math.abs(one.data[i + 1] - two.data[i + 1]);
    const db = Math.abs(one.data[i + 2] - two.data[i + 2]);
    if (Math.max(dr, dg, db) > 30) changed += 1;
  }
  return changed / Math.max(1, total);
}

const pct = (ratio: number): string => `${(ratio * 100).toFixed(1)}%`;

/**
 * 等「这一组预设真的落到播放器清单上」。面板显示 夜晚 只代表 scene-properties 读到了值，
 * 画布上还差一次 Worker 热重建 —— 不等它就截图，取到的是工程烘焙值那一面（实测过：
 * 第一次拍出来是白天，第二次才是夜晚）。
 */
const sceneHost = (page: Page) => page.locator(".app-backdrop-scene-host");
const waitPresetLanded = async (page: Page) => {
  await expect(sceneHost(page)).toHaveAttribute("data-scene-preset", "pending", { timeout: 10_000 });
  await expect(sceneHost(page)).toHaveAttribute("data-scene-preset", "applied", { timeout: 90_000 });
};

/** 面板里某个 combo 行切到指定选项，并等热重建落到播放器。 */
const pickOption = async (page: Page, label: string, optionText: string) => {
  const row = page.locator(".lg-preset .lg-field", { hasText: label }).first();
  await row.locator("select").selectOption({ label: optionText });
  await waitPresetLanded(page);
};

test("壁纸预设：初值取 WE 生效值，换选项热换清单且不重解包", async ({ page }) => {
  // 这张作品 100MB 上下，首解就可达十几秒；再加两次热重建的等待
  test.setTimeout(300_000);
  const errors: string[] = [];
  // 失败提示只收 error；warn 也留一份，回落静态预览走的是 console.warn，光看 error 会漏掉原因
  const notices: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
    if (msg.type() === "error" || msg.type() === "warning") notices.push(`${msg.type()}: ${msg.text()}`);
  });
  const state = await instrument(page);

  await page.goto(APP_URL);
  // 失败时把控制台原文端出来：回落原因（Failed to fetch / 解码异常）决定了下一步查哪里
  await expect
    .poll(() => page.locator(".app-backdrop-scene-host")
      .getAttribute("data-scene-status")
      .catch(() => null) ?? "还没有壁纸层", {
      timeout: 150_000,
      message: () => `场景没就绪。控制台：\n${notices.join("\n")}`,
    })
    .toBe("ready");
  // dev 下 StrictMode 会双挂载，首帧可能被 dispose 抢在拦截前 abort，所以开局是 1 还是 2 次不定；
  // 本用例要证的是「换预设不再取包」，故记一次基线，只对基线之后的增量负责。
  const pkgAtReady = state.pkgRequests;

  await page.locator(".lg-rope").click();
  await page.getByRole("tab", { name: "壁纸", exact: true }).click();

  const panel = page.locator(".lg-preset");
  await expect(panel.getByRole("heading", { name: /壁纸预设/ })).toBeVisible();
  for (const label of BOUND_LABELS) {
    await expect(panel.locator(".lg-field, .lg-switch-row").filter({ hasText: label }).first())
      .toBeVisible();
  }
  for (const label of UNBOUND_LABELS) {
    await expect(panel.getByText(label, { exact: false })).toHaveCount(0);
  }
  // 计数徽标 = 面板实际摆出的控件数（两者同源于 rows.length；行数只由 userBindings 收窄）
  const rowCount = await panel.locator(".lg-field, .lg-switch-row").count();
  await expect(panel.locator("h4 span")).toHaveText(String(rowCount));
  expect(rowCount, `面板只摆出 ${rowCount} 条，少于点名的已实现键`).toBeGreaterThanOrEqual(BOUND_LABELS.length);

  // 部署证据：初值就是用户 WE 里的设置（夜晚），不是工程烘焙值（白天）
  const timing = panel.locator(".lg-field", { hasText: "时间选择 Timing" }).first();
  await expect(timing.locator("select")).toHaveValue("1");
  await expect(timing.locator(".lg-field__label b")).toHaveText("夜晚 Night");
  const posX = panel.locator(".lg-field", { hasText: "人物位置X" }).first();
  await expect(posX.locator('input[type="range"]')).toHaveValue("0.527");

  mkdirSync(SHOT_DIR, { recursive: true });
  // 画布也得切到 WE 生效值那一面，否则下面拍到的还是工程烘焙值
  await expect(sceneHost(page)).toHaveAttribute("data-scene-preset", "applied", { timeout: 90_000 });
  const night = await canvasShot(page, "canvas-1-night-we-values.jpg");
  // 噪声基线：什么都不改再拍一张。暂停态下垫片钉住了时间戳 ⇒ 必须是同一帧；
  // 一旦这里不为 0，说明动画从暂停里漏出来了，后面的像素判据全都不可信，直接判死。
  const noise = diffRatio(night, await canvasShot(page, "canvas-1b-night-repeat.jpg"));
  expect(noise, `暂停态连拍两张差了 ${pct(noise)} ⇒ 待机动画没被冻住，像素判据不可用`).toBeLessThan(0.005);
  const floor = Math.max(0.02, noise * 3);

  await pickOption(page, "时间选择 Timing", "白天 Day");
  const day = await canvasShot(page, "canvas-2-day-after-switch.jpg");
  const dayDiff = diffRatio(night, day);
  expect(dayDiff, `切晨夜只变了 ${pct(dayDiff)}（噪声 ${pct(noise)}，门槛 ${pct(floor)}）⇒ 预设没换到画面`).toBeGreaterThan(floor);
  // 换对了吗：夜侧贴 星空背景.tex（暗），昼侧贴 蓝天.tex（亮）。
  // 光「变了」不够，方向反了同样是错。
  expect(luma(day), `白天(${luma(day).toFixed(1)}) 没比夜晚(${luma(night).toFixed(1)}) 亮 ⇒ 贴错了天空`).toBeGreaterThan(luma(night) + 20);
  // WE 的条件显示同时生效：「提示框」这一项在 project.json 里挂着 display==0，
  // 白天才出现——面板跟着长出一条，说明 condition 不只是渲染侧在用。
  // 判据是「比切之前正好多一行」，不是「等于 BOUND_LABELS 的长度」：后者是点名的那几把键，
  // 覆盖面随渲染器增长（分层补齐后实际行数早已多于点名列），写死会把对的画面判成红的。
  await expect(panel.locator(".lg-field, .lg-switch-row").filter({ hasText: "提示框" })).toBeVisible();
  await expect(panel.locator("h4 span")).toHaveText(String(rowCount + 1));

  await pickOption(page, "人物 character", "不显示");
  const noCharacter = await canvasShot(page, "canvas-3-character-hidden.jpg");
  const hideDiff = diffRatio(day, noCharacter);
  expect(hideDiff, `隐藏人物只变了 ${pct(hideDiff)}（噪声 ${pct(noise)}，门槛 ${pct(floor)}）⇒ 人物开关没生效`).toBeGreaterThan(floor);

  // 全程没有第二次取包 / 解码：预设走的是热换清单通道
  expect(state.pkgRequests, "换预设不该重新下载 scene.pkg").toBe(pkgAtReady);

  // 偏离量按壁纸各自落盘（改回原样会删键，见单测），且没把整张属性表抄进设置
  const persisted = state.saved
    .map((body) => (((body?.settings as any)?.liquidGlass?.wallpapers || [])[0] || {}).preset)
    .filter((preset) => preset !== undefined);
  expect(persisted.length, "面板改动没有写回设置").toBeGreaterThan(0);
  expect(persisted[persisted.length - 1]).toEqual({ display: "0", hrbigb: "2" });

  expect(errors, errors.join("\n")).toEqual([]);
});

test("textinput 预设：自定义文字内容可编辑，改回原样即从差集清空（真实写回闭环）", async ({ page }) => {
  // 2026-10-24：textinput（「可自定义文字」的正文）进属性表后的端到端。
  // jsdom 用例盖不住「改回原样删键」——面板受控于父组件，单测 spy 不回写时同值二连发
  // 会被 React 值追踪去重；这里走真实的 settings 写回链路。
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
    if (msg.type() === "warning") errors.push(`console-warn: ${msg.text()}`);
  });
  const state = await instrument(page);

  await page.goto(APP_URL);
  await expect
    .poll(() => page.locator(".app-backdrop-scene-host")
      .getAttribute("data-scene-status")
      .catch(() => null) ?? "还没有壁纸层", {
      timeout: 150_000,
      message: () => `场景没就绪。控制台：\n${errors.join("\n")}`,
    })
    .toBe("ready");

  await page.locator(".lg-rope").click();
  await page.getByRole("tab", { name: "壁纸", exact: true }).click();
  const panel = page.locator(".lg-preset");
  await expect(panel.getByRole("heading", { name: /壁纸预设/ })).toBeVisible();

  // 「组件自定义文字」这组的行有两道门：project.json 的 condition 挂在开关
  // newproperty50 上（当前 WE 生效值 false ⇒ 整组行隐藏）；文字对象 day7/day8 的
  // 可见性又绑在 display==0（白天）。完整用户旅程：切白天 → 打开开关 → 内容行长出来。
  await pickOption(page, "时间选择 Timing", "白天 Day");
  const customTextSwitch = panel
    .locator(".lg-switch-row", { hasText: "组件自定义文字" })
    .locator('input[type="checkbox"]');
  if (!(await customTextSwitch.isChecked())) {
    await customTextSwitch.click();
    await waitPresetLanded(page);
  }

  // text input 只存在于 textinput 行（自定义文字 order 139 最靠前 ⇒ first 即 newproperty55）
  const input = panel.locator('input[type="text"]').first();
  await expect(input).toBeVisible({ timeout: 30_000 });
  const initial = await input.inputValue();

  const lastPreset = () => {
    const body = state.saved[state.saved.length - 1];
    const wp = ((body?.settings as any)?.liquidGlass?.wallpapers || [])[0] || {};
    return wp.preset as Record<string, unknown> | undefined;
  };

  await input.fill("夜莺 Night 二号");
  await expect.poll(() => lastPreset()?.newproperty55, { message: "编辑没有写回差集" })
    .toBe("夜莺 Night 二号");
  // 改回原样 ⇒ 键从差集删除（不是存回原值）
  await input.fill(initial);
  await expect
    .poll(() => (lastPreset() ?? {}).newproperty55 ?? "gone", { message: "改回原样没有清掉差集键" })
    .toBe("gone");
  expect(errors, errors.join("\n")).toEqual([]);
});
