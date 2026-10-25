import { expect, test } from "@playwright/test";
import { decode } from "jpeg-js";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验证（创意工坊 3346715292 · Tier B 云层流动）：WE 挂在图像对象上的
 * `effects/waterflow` 效果在应用里真的按流场遮罩扰动了天空层，并且它的两个键都归渲染器消费：
 *   newproperty9 = 「🔘背景云层流动」开关，newproperty10 = 「云层运动速度」滑杆。
 * 本包当前可见的效果层是夜晚天空 `night1`（白天组被时间选择隐藏）。
 *
 * 证据分三层，逐层可分辨（只看像素分不出「清单没发」「遮罩没到 GPU」「程序没跑」）：
 * ① 清单：`dsh-set-manifest` 消息里天空图层带 flow（maskUrl / phaseUrl / speed / strength /
 *    phasescale / enabled），且 newproperty9/newproperty10 落进 `userBindings`（面板据此摆控件，
 *    fail-closed）；
 * ② WebGL：这套播放器里**只有 waterflow 程序**有 u_phasescale（flowimage 用 u_speeds、flag 用
 *    u_strength，都不会误认），且分支要求「本层贴图 + 遮罩都真加载成功」才走扰动（否则落回标准
 *    绘制）⇒ 带 u_phasescale 的 drawArrays 计数就是扰动真在跑；同时抓 u_speed / u_amp 证明参数
 *    确实到了 GPU。**u_amp 必须是 strength 按内容贴图宽归一的像素级位移**——直接喂 strength 原值
 *    会把天空抹平（真机事故，量级判据见下）。
 * ③ 关掉这一路：`wallpaper.preset` 覆盖 newproperty9=false（就是选项面板改 WE 预设写的字段，
 *    不碰用户真实存档）⇒ 清单里 enabled=false，② 的计数必须为 0，而「画了但没扰动」的帧
 *    必须仍 > 0（关掉扰动不等于关掉天空）。
 * ④ 开关只该让云动，不该把天画黑：水flow 是 UV 位移、不是重新着色 ⇒ 同一片天空取样框在
 *    「开 / 关」两态下的平均亮度必须同量级。这一条抓的是 NPOT 陷阱——WebGL1 里非 2 的幂贴图
 *    设成 REPEAT 会让整张纹理 incomplete、取样恒返回 (0,0,0,1)，天空被画成一块不透明白痴黑，
 *    而 ①②③ 三层证据全都照样绿（真机实锤：开/关亮度比 0.23，修完 ≈ 1）。
 *
 * 跑法：5173 归别人的服务，用隔离的临时 vite(5198，/api 反代带新路由的隔离后端 8011)：
 *   MSYS_NO_PATHCONV=1 VITE_API_BASE=/api node frontend/node_modules/vite/bin/vite.js \
 *     --config _tmp/vite.e2e-8011.config.mjs
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test --workers=1 \
 *     --config playwright.e2e-manual.config.ts -g "云层流动" --output=../_tmp/wallpaper-flow-r1
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-flow");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "3346715292";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3346715292\preview.gif`;

/**
 * 天空取样框（1600×900 视口坐标）：只圈夜晚天空那块云，避开桥体、人物与文字——
 * 那些层在两态之间本来就会变，混进来会把「画黑了」稀释成看不出来（全帧亮度比实测 0.87，
 * 而这块框的比是 0.23）。
 */
const SKY_BAND = { x: 517, y: 100, width: 200, height: 250 };
/** 两条用例同文件顺序跑：开态的亮度留给关态比对（单跑关态时跳过这一步）。 */
let skyLumaWithFlow: number | null = null;

function bandLuma(jpeg: Buffer): number {
  const img = decode(jpeg, { useBuffer: true });
  let sum = 0;
  let n = 0;
  for (let i = 0; i < img.data.length; i += 4) {
    sum += 0.299 * img.data[i] + 0.587 * img.data[i + 1] + 0.114 * img.data[i + 2];
    n += 1;
  }
  return sum / Math.max(1, n);
}

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

type FlowFrame = {
  draws: number;
  flowDraws: number;
  speed: number | null;
  amp: number | null;
  phasescale: number | null;
};
type FlowLayer = {
  name?: string;
  texUrl?: string;
  flow?: {
    maskUrl?: string;
    phaseUrl?: string;
    speed?: number;
    strength?: number;
    phasescale?: number;
    enabled?: boolean;
  };
};
type FlowManifest = { layers?: FlowLayer[]; userBindings?: string[] };
type FlowRec = {
  manifests: FlowManifest[];
  frames: FlowFrame[];
};

const settingsWith = (wallpaper: Record<string, unknown>) => ({
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [wallpaper],
    activeWallpaperId: wallpaper.id,
    wallpaperFit: "cover",
    wallpaperPaused: false,
    pauseOnHidden: false,
    pauseOnBlur: false,
    pauseOnBattery: false,
    scrim: 0,
    wallpaperBlur: 0,
    wallpaperBrightness: 100,
    wallpaperContrast: 100,
    wallpaperSaturate: 100,
  },
});

async function mockSettings(page: Page, wallpaper: Record<string, unknown>) {
  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { repos: null, settings: settingsWith(wallpaper) } });
      return;
    }
    await route.fulfill({ json: { ok: true } });
  });
  await page.route("**/api/user-state/proxy-status*", (route) => route.fulfill({
    json: {
      listening: false, address: "", source: "none", system_address: "",
      manual_address: "", candidates: [],
    },
  }));
}

/**
 * 在**任何一帧之前**挂监听：播放器 iframe 是 blob/srcdoc 文档，addInitScript 会在它的
 * 脚本执行前跑一次，主页面那次直接跳过。
 */
async function instrumentFlowChannel(page: Page) {
  await page.addInitScript(() => {
    const host = window as unknown as { __e2eFlow?: FlowRec; __e2eFlowHooked?: boolean };
    if (host.__e2eFlowHooked) return;
    host.__e2eFlowHooked = true;
    const rec: FlowRec = { manifests: [], frames: [] };
    host.__e2eFlow = rec;
    const blank = (): FlowFrame => ({
      draws: 0, flowDraws: 0, speed: null, amp: null, phasescale: null,
    });
    rec.frames.push(blank());
    const currentFrame = () => rec.frames[rec.frames.length - 1];

    window.addEventListener("message", (ev) => {
      const msg = ev.data as { type?: string; manifest?: FlowManifest } | null;
      if (msg && msg.type === "dsh-set-manifest" && msg.manifest) rec.manifests.push(msg.manifest);
    });

    // rAF 回调当帧界（2D 场景每帧只清 FBO，不清主画布，没法靠 clear 分段）。
    const originalRaf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = ((callback: FrameRequestCallback) => originalRaf((ts) => {
      rec.frames.push(blank());
      return callback(ts);
    })) as typeof window.requestAnimationFrame;

    const proto = window.WebGLRenderingContext?.prototype;
    if (!proto) return;
    const names = new WeakMap<WebGLUniformLocation, string>();
    const programs = new WeakMap<WebGLProgram, { flow: boolean }>();

    const originalGetUniform = proto.getUniformLocation;
    proto.getUniformLocation = function patched(
      this: WebGLRenderingContext, program: WebGLProgram, name: string,
    ) {
      const location = originalGetUniform.call(this, program, name);
      if (location) names.set(location, name);
      // u_phasescale 只有 waterflow 程序有（flowimage 用的是 u_speeds，flag 用的是 u_strength）
      // ⇒ 拿它认「这一帧走了云层扰动」不会认错程序。
      if (name === "u_phasescale") {
        const state = programs.get(program);
        if (state) state.flow = true;
        else programs.set(program, { flow: true });
      } else if (!programs.has(program)) programs.set(program, { flow: false });
      return location;
    };

    let active: { flow: boolean } | null = null;
    const originalUseProgram = proto.useProgram;
    proto.useProgram = function patched(this: WebGLRenderingContext, program: WebGLProgram | null) {
      active = program ? programs.get(program) ?? null : null;
      return originalUseProgram.call(this, program);
    };

    const originalUniform1 = proto.uniform1f;
    proto.uniform1f = function patched(
      this: WebGLRenderingContext, location: WebGLUniformLocation | null, x: number,
    ) {
      if (active?.flow && location) {
        const name = names.get(location);
        if (name === "u_amp") currentFrame().amp = x;
        else if (name === "u_speed") currentFrame().speed = x;
        else if (name === "u_phasescale") currentFrame().phasescale = x;
      }
      return originalUniform1.call(this, location, x);
    };

    const originalDraw = proto.drawArrays;
    proto.drawArrays = function patched(
      this: WebGLRenderingContext, mode: GLenum, first: GLintptr, count: GLsizei,
    ) {
      const frame = currentFrame();
      frame.draws += 1;
      if (active?.flow) frame.flowDraws += 1;
      return originalDraw.call(this, mode, first, count);
    };
  });
}

const readFlow = (page: Page) => page.evaluate(() => {
  // 数据在播放器 iframe 那一侧：清单由宿主 post 给 iframe，绘制调用发生在 iframe 的 GL 上下文。
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const rec = (frame?.contentWindow as (Window & { __e2eFlow?: FlowRec }) | null)?.__e2eFlow;
  if (!rec) throw new Error("播放器 iframe 里没有 __e2eFlow（监听器没装上或场景未起）");
  const last = rec.manifests[rec.manifests.length - 1] ?? null;
  const flowFrames = rec.frames.filter((each) => each.flowDraws > 0);
  const tail = flowFrames[flowFrames.length - 1] ?? null;
  return {
    manifestCount: rec.manifests.length,
    // 诚实闸门的落点：只有清单登记过的键才会出现在选项面板里。
    userBindings: last?.userBindings ?? [],
    flowLayers: (last?.layers ?? []).filter((layer) => layer.flow).map((layer) => ({
      name: layer.name, texUrl: layer.texUrl, ...layer.flow,
    })),
    // 走流场扰动的绘制次数与帧数。
    flowDraws: flowFrames.reduce((total, each) => total + each.flowDraws, 0),
    flowFrames: flowFrames.length,
    // 画了但没走流场的帧：证明「关掉扰动 ≠ 关掉这一层」，天照旧在。
    stdFrames: rec.frames.filter((each) => each.draws > 0 && each.flowDraws === 0).length,
    speed: tail?.speed ?? null,
    amp: tail?.amp ?? null,
    phasescale: tail?.phasescale ?? null,
  };
});

const openScene = async (page: Page, wallpaper: Record<string, unknown>) => {
  await mockSettings(page, wallpaper);
  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });
  await page.evaluate((css) => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }, ISOLATE_CSS);
};

test.use({ viewport: { width: 1600, height: 900 } });

test("云层流动开着：清单带 flow，流场程序按 WE 的 strength/speed 真画这一层", async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await instrumentFlowChannel(page);
  await openScene(page, {
    id: "scene-flow",
    path: SCENE_PREVIEW,
    name: "八奈见杏菜（夜莺Night · 云层流动）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  });

  // 等「清单里真有一个开着的效果层，且它真走过流场程序」：遮罩要解码、WE 生效值要等
  // scene-properties 那一趟 HTTP，一次性断言会把「还没到」误判成「坏了」。
  await expect.poll(async () => {
    const state = await readFlow(page);
    const on = state.flowLayers.filter((layer) => layer.enabled === true).length;
    return on > 0 && state.flowFrames > 0
      ? "ready"
      : `清单 ${state.manifestCount} 份 / 效果层 ${state.flowLayers.length} 个 / 开着 ${on} 个 / 流场帧 ${state.flowFrames}`;
  }, { timeout: 90_000 }).toBe("ready");
  await page.waitForTimeout(1_500);

  const seen = await readFlow(page);
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SHOT_DIR, "flow-on.png") });
  skyLumaWithFlow = bandLuma(await page.screenshot({ type: "jpeg", quality: 92, clip: SKY_BAND }));
  expect(skyLumaWithFlow, "开着扰动时天空取样框接近全黑 ⇒ 内容贴图压根没画出来")
    .toBeGreaterThan(15);

  const sky = seen.flowLayers.filter((layer) => layer.enabled === true);
  expect(sky.length, "清单里没有开着的水流效果层").toBeGreaterThan(0);
  const flow = sky[0];
  // 「遮罩真在包内取得到」不需要在这里另行证明：清单里出现 flow 本身就是闸门的产物
  // （包内读不到遮罩就整条不发，见 vendor/we-scene-manifest 的 waterflowOf）。
  // 这里要确认的是遮罩与内容是两张不同的贴图，都被解成了 GPU 能用的 blob。
  expect(String(flow.maskUrl), "遮罩不是可加载的 blob 资源").toMatch(/^blob:/);
  expect(String(flow.texUrl), "本层内容贴图不是可加载的 blob 资源").toMatch(/^blob:/);
  expect(flow.maskUrl, "遮罩与内容贴图是同一张 ⇒ 取错槽位了（g_Texture1 才是流场遮罩）").not.toBe(flow.texUrl);
  expect(flow.speed, "速度取自 WE 预设 newproperty10").toBeGreaterThan(0);
  // 5 是效果在工程里写的 strength；代码兜底值是 1 ⇒ 等于 5 才证明参数真从包里读出来。
  expect(flow.strength, "强度没落到 GPU 侧的兜底常量 ⇒ 效果的 strength 没被读出来").toBe(5);
  // 这两个键必须同时出现在清单的 userBindings 里：面板据此摆开关，闸门是 fail-closed 的。
  expect(seen.userBindings).toEqual(expect.arrayContaining(["newproperty9", "newproperty10"]));

  expect(seen.flowFrames, "没有任何一帧走流场扰动 ⇒ 天空层始终按标准绘制，开关节掉了也没画面").toBeGreaterThan(0);
  expect(seen.speed, "u_speed 没被设过 ⇒ 扰动分支没跑到取样那一步").not.toBeNull();
  expect(Number(seen.speed), "扰动速度不是 WE 预设 newproperty10 的值").toBeCloseTo(Number(flow.speed), 6);
  expect(Number(seen.phasescale), "u_phasescale 没落到效果里的 phasescale").toBeCloseTo(
    Number(flow.phasescale ?? 1), 6,
  );
  // u_amp 必须是「strength 像素按内容贴图宽归一」后的 UV 位移。直接喂 strength 是这次的
  // 真机事故：5 进了 flowimage 的 `amp*0.1*cycle` 就是半屏位移，天空被抹平、星点整片消失，
  // 白天反而比夜晚暗（preset 用例的亮度方向判据就是被它打红的）。
  const contentWidth = await page.evaluate((url) => new Promise<number>((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img.naturalWidth);
    img.onerror = () => reject(new Error("内容贴图取不到"));
    img.src = url;
  }), String(flow.texUrl));
  expect(contentWidth, "内容贴图宽为 0 ⇒ 无法核对位移量纲").toBeGreaterThan(0);
  expect(Number(seen.amp), `u_amp 应等于 strength(${flow.strength}px) / 贴图宽(${contentWidth}px)`)
    .toBeCloseTo(Number(flow.strength) / contentWidth, 8);
  expect(Number(seen.amp) * contentWidth, "位移必须真的是像素级（1–64px），不是半屏级")
    .toBeGreaterThanOrEqual(1);
  expect(Number(seen.amp), "位移超过画面的 2% ⇒ 会把这一层抹成另一张图").toBeLessThanOrEqual(0.02);

  expect(errors, errors.join("\n")).toEqual([]);
});

test("云层流动关掉：清单如实标 enabled=false，流场程序一次都不跑", async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await instrumentFlowChannel(page);
  await openScene(page, {
    id: "scene-flow-off",
    path: SCENE_PREVIEW,
    name: "八奈见杏菜（夜莺Night · 云层流动关）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
    preset: { newproperty9: false },
  });

  // 本包真正带水流效果且当前可见的图层是夜晚天空（night1）：白天组被时间选择隐藏，
  // 所以断言只认「清单报了效果层」，不写死层名（写死会把「换到白天」这类真实变化误判成坏）。
  await expect.poll(async () => {
    const state = await readFlow(page);
    return state.flowLayers.length > 0 && state.flowLayers.every((layer) => layer.enabled === false)
      ? "off"
      : `效果层 ${state.flowLayers.length} 个：${state.flowLayers.map((layer) => `${layer.name}:${layer.enabled}`).join(" ")}`;
  }, { timeout: 90_000 }).toBe("off");
  // 留出若干帧，好证明「没跑」不是取样太早。
  await page.waitForTimeout(3_000);

  const seen = await readFlow(page);
  await page.screenshot({ path: resolve(SHOT_DIR, "flow-off.png") });
  const offLuma = bandLuma(await page.screenshot({ type: "jpeg", quality: 92, clip: SKY_BAND }));
  expect(offLuma, "关掉扰动后天空取样框接近全黑 ⇒ 标准绘制也没接住这一层").toBeGreaterThan(15);
  if (skyLumaWithFlow !== null) {
    // 扰动是 UV 位移，不改变明暗 ⇒ 开态不该比关态暗一个量级。
    expect(
      skyLumaWithFlow,
      `开着扰动天空 ${skyLumaWithFlow.toFixed(1)}、关着 ${offLuma.toFixed(1)} ⇒ ` +
        "开关把这一层画黑了（NPOT 贴图设 REPEAT 会让纹理 incomplete，取样恒为不透明黑）",
    ).toBeGreaterThanOrEqual(offLuma * 0.6);
  }

  expect(seen.flowLayers.length, "关掉的只是效果本身：图层仍要在清单里").toBeGreaterThan(0);
  expect(
    seen.flowLayers.filter((layer) => layer.enabled !== false).length,
    "newproperty9=false 没有如实落到 flow.enabled",
  ).toBe(0);
  expect(seen.flowDraws, "开关关了仍有流场绘制 ⇒ 开关在撒谎").toBe(0);
  // 图层本身照旧要画：关掉扰动不等于关掉天空。
  expect(seen.stdFrames, "关掉扰动后连画面都没了 ⇒ 效果层没落回标准绘制").toBeGreaterThan(0);
  // 关掉效果也不撤键：用户还得靠这个开关把它打开。
  expect(seen.userBindings).toEqual(expect.arrayContaining(["newproperty9", "newproperty10"]));

  expect(errors, errors.join("\n")).toEqual([]);
});
