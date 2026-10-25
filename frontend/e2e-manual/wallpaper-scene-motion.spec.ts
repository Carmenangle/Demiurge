import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验证（创意工坊 3346715292）：
 * 1) 场景声明 `cameraparallax:false` ⇒ 暂停后移动鼠标，任何图层矩阵都不许变；
 *    修前会随鼠标整体平移。
 * 2) 恢复播放 ⇒ MDLA 待机动画必须推进（蒙皮顶点每帧变化），且头骨旋转要让
 *    挂点子对象（头发）的模型矩阵跟着转 —— 修前是绑死姿态。
 *
 * 直接监听播放器 iframe 里的 WebGL 调用，不靠截图比对（暂停时 u_time 仍会让
 * 粒子/闪烁变化，像素差异无法区分「鼠标位移」与「时间动画」）。
 *
 * 暂停态不能靠「等下一帧」取样：垫片把暂停实现成定格（补帧到内容成型后彻底停帧），
 * 所以①组的每一帧都通过产品的补帧通道取（窗口缩放 → recover），见 pumpSample。
 *
 * 跑法：隔离后端(8011) + 临时 vite(5198)，串行：
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test \
 *     --config playwright.e2e-manual.config.ts e2e-manual/wallpaper-scene-motion.spec.ts --workers=1
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-motion");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "3346715292";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3346715292\preview.gif`;

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

type Frame = {
  matrices: number[][];
  vertices: string[];
  positions: number[][];
};

type Sample = {
  frames: Frame[];
};

const lastFrame = (sample: Sample): Frame => {
  const frames = sample.frames.filter((frame) => frame.matrices.length > 0);
  const frame = frames[frames.length - 1];
  if (!frame) throw new Error("没有采集到任何帧");
  return frame;
};

/** 采样的两种口径：暂停态靠补帧取定格那一帧，播放态直接等 rAF 连续出帧。 */
type TakeSample = (page: Page) => Promise<Sample>;

const settingsWith = (wallpaper: Record<string, unknown>) => ({
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [wallpaper],
    activeWallpaperId: wallpaper.id,
    wallpaperFit: "cover",
    wallpaperPaused: true,
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

/** 给 iframe 内的 WebGL 原型挂监听：按帧收集 u_model 矩阵与蒙皮顶点缓冲哈希。 */
async function instrument(page: Page) {
  await page.evaluate(() => {
    const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    const win = frame?.contentWindow as (Window & {
      WebGLRenderingContext?: typeof WebGLRenderingContext;
      __e2eMotion?: { frames: Array<{ matrices: number[][]; vertices: string[]; positions: number[][] }>; current: { matrices: number[][]; vertices: string[]; positions: number[][] } | null };
    }) | null;
    if (!win) throw new Error("scene iframe 未就绪");
    const proto = win.WebGLRenderingContext?.prototype;
    if (!proto) throw new Error("iframe 里没有 WebGLRenderingContext");

    const names = new WeakMap<object, string>();
    const rec = {
      frames: [] as Array<{ matrices: number[][]; vertices: string[]; positions: number[][] }>,
      current: null as { matrices: number[][]; vertices: string[]; positions: number[][] } | null,
    };
    win.__e2eMotion = rec;
    const begin = () => {
      rec.current = { matrices: [], vertices: [], positions: [] };
      rec.frames.push(rec.current);
      return rec.current;
    };

    // 2D 场景每帧只清 FBO、不清主画布，用 rAF 回调当帧界。
    const originalRaf = win.requestAnimationFrame.bind(win);
    (win as unknown as { requestAnimationFrame: typeof win.requestAnimationFrame })
      .requestAnimationFrame = (callback: FrameRequestCallback) => originalRaf((ts) => {
        begin();
        callback(ts);
      });

    const originalGetUniform = proto.getUniformLocation;
    proto.getUniformLocation = function patchedGetUniform(
      this: WebGLRenderingContext,
      program: WebGLProgram,
      name: string,
    ) {
      const location = originalGetUniform.call(this, program, name);
      if (location) names.set(location, name);
      return location;
    };

    const originalUniformMatrix = proto.uniformMatrix4fv;
    proto.uniformMatrix4fv = function patchedUniformMatrix(
      this: WebGLRenderingContext,
      location: WebGLUniformLocation | null,
      transpose: boolean,
      data: Float32List,
    ) {
      if (location && names.get(location) === "u_model") {
        (rec.current || begin()).matrices.push(Array.from(data as Float32Array));
      }
      return originalUniformMatrix.call(this, location, transpose, data);
    };

    const originalBufferSubData = proto.bufferSubData;
    proto.bufferSubData = function patchedBufferSubData(
      this: WebGLRenderingContext,
      target: GLenum,
      offset: GLintptr,
      data: BufferSource | null,
    ) {
      if (data && (data as Float32Array).length) {
        const values = data as Float32Array;
        let hash = 0;
        for (let i = 0; i < values.length; i++) {
          hash = (hash * 31 + Math.round(values[i] * 1e3)) | 0;
        }
        const frame = rec.current || begin();
        frame.vertices.push(String(hash));
        // 角色蒙皮顶点缓冲：每帧整块重写，保留最后一份用于量位移。
        if (values.length > 300) frame.positions = [Array.from(values)];
      }
      return originalBufferSubData.call(this, target, offset, data);
    };
  });
}

async function sample(page: Page, settleMs = 900): Promise<Sample> {
  return page.evaluate(async (wait) => {
    const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    const win = frame?.contentWindow as (Window & {
      __e2eMotion?: { frames: Array<{ matrices: number[][]; vertices: string[]; positions: number[][] }> };
    }) | null;
    const rec = win?.__e2eMotion;
    if (!rec) throw new Error("监听器未安装");
    rec.frames.length = 0;
    await new Promise((done) => win!.setTimeout(done, wait));
    return { frames: rec.frames.slice() };
  }, settleMs);
}

/**
 * 采「暂停态当前真的画在画布上」的那一帧。
 * 垫片暂停后会一路补帧到内容成型、然后彻底停帧（「暂停＝定格」的实现，见 scenePlayer.ts
 * 的 settle 判据），所以干等等不到下一帧 —— 必须走产品自己的补帧通道：窗口缩放 →
 * 背景层 recover() → dsh-recover-renderer。补出来的帧吃同一个钉死时间戳，内容幂等，
 * 采到的矩阵／顶点就是画布上那一帧，而不是上一秒的残留。
 */
async function pumpSample(page: Page): Promise<Sample> {
  const frames = await page.evaluate(async () => {
    const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    const win = frame?.contentWindow as (Window & {
      __e2eMotion?: { frames: Frame[] };
    }) | null;
    const rec = win?.__e2eMotion;
    if (!rec) throw new Error("监听器未安装");
    rec.frames.length = 0;
    window.dispatchEvent(new Event("resize"));
    await new Promise((done) => window.setTimeout(done, 1_500));
    return rec.frames.filter((each) => each.matrices.length > 0).slice();
  });
  if (frames.length === 0) throw new Error("补帧后画布什么都没画 ⇒ 暂停态补帧通道失效");
  return { frames };
}

/** 宿主转给播放器的视差输入；用来证明「矩阵没变」不是空转出来的。 */
const armPointerSpy = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const win = frame?.contentWindow as (Window & { __e2ePointer?: { x: number; y: number } }) | null;
  if (!win) throw new Error("scene iframe 未就绪");
  win.addEventListener("message", (ev) => {
    const msg = ev.data as { type?: string; x?: number; y?: number } | null;
    if (msg?.type === "dsh-pointer") win.__e2ePointer = { x: msg.x ?? 0, y: msg.y ?? 0 };
  });
});

const readPointer = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const win = frame?.contentWindow as (Window & { __e2ePointer?: { x: number; y: number } }) | null;
  return win?.__e2ePointer ?? null;
});

/** 两帧之间蒙皮顶点的最大位移（像素）。 */
const maxVertexShift = (a: Frame, b: Frame) => {
  const left = a.positions[0];
  const right = b.positions[0];
  if (!left || !right || left.length !== right.length) return -1;
  let max = 0;
  for (let i = 0; i + 2 < left.length; i += 3) {
    const dx = left[i] - right[i];
    const dy = left[i + 1] - right[i + 1];
    max = Math.max(max, Math.hypot(dx, dy));
  }
  return max;
};

const sameMatrixLists = (a: number[][], b: number[][]) => {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    for (let k = 0; k < a[i].length; k++) {
      if (Math.abs(a[i][k] - b[i][k]) > 1e-9) return false;
    }
  }
  return true;
};

/** 纹理是边解码边出层的，先等「相邻两次采样的末帧完全一致」再开始对比。 */
async function waitStable(
  page: Page,
  take: TakeSample = (target) => sample(target, 600),
  limitMs = 30_000,
): Promise<Sample> {
  let previous = await take(page);
  const deadline = Date.now() + limitMs;
  while (Date.now() < deadline) {
    const next = await take(page);
    const a = lastFrame(previous);
    const b = lastFrame(next);
    const stable = sameMatrixLists(a.matrices, b.matrices)
      && a.vertices.join(",") === b.vertices.join(",");
    previous = next;
    if (stable) return next;
  }
  return previous;
}

const matrixAngle = (matrix: number[]) => Math.atan2(matrix[1], matrix[0]) * (180 / Math.PI);

test.use({ viewport: { width: 1600, height: 900 } });

test("cameraparallax=false：鼠标不动背景；播放后角色与头发自动动", async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await mockSettings(page, {
    id: "scene-motion",
    path: SCENE_PREVIEW,
    name: "八奈见杏菜（夜莺Night · 动效验证）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  });

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });

  await page.evaluate((css) => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }, ISOLATE_CSS);

  await instrument(page);
  mkdirSync(SHOT_DIR, { recursive: true });

  const diag = await page.evaluate(async () => {
    const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    const win = frame?.contentWindow as (Window & {
      __e2eMotion?: { frames: unknown[] };
      __weRenderErr?: number;
      WebGLRenderingContext?: typeof WebGLRenderingContext;
    }) | null;
    await new Promise((done) => win!.setTimeout(done, 800));
    return {
      renderError: win?.__weRenderErr ?? 0,
      frames: win?.__e2eMotion?.frames.length ?? -1,
    };
  });
  console.log("instrument diag:", JSON.stringify(diag), "（暂停＝定格，稳态后帧数为 0 是正常的）");
  expect(diag.renderError, "播放器渲染出错（监听器可能破坏了 WebGL 调用）").toBe(0);

  // ── ① 暂停态：鼠标移动不得改变任何图层矩阵 ────────────────────────────
  // 暂停＝定格（垫片不再起帧），所以这里的每一帧都由产品自己的补帧通道产生。
  await armPointerSpy(page);
  await page.mouse.move(120, 120);
  await waitStable(page, pumpSample);
  const pausedA = await pumpSample(page);
  const pointerA = await readPointer(page);
  await page.screenshot({ path: resolve(SHOT_DIR, "paused-mouse-a.png") });

  await page.mouse.move(1480, 780);
  const pausedB = await pumpSample(page);
  const pointerB = await readPointer(page);
  await page.screenshot({ path: resolve(SHOT_DIR, "paused-mouse-b.png") });

  // 前置条件：两次定格之间鼠标输入确实换了位置，否则下面的「矩阵没变」是空转出来的
  expect(pointerA, "宿主没把鼠标坐标转给播放器 ⇒ 视差通道断了").not.toBeNull();
  expect(pointerB?.x, "两次采样之间宿主没送新的鼠标坐标").not.toBe(pointerA?.x);
  expect(pausedA.frames.length, "没有采集到任何渲染帧 ⇒ 播放器没在画").toBeGreaterThan(0);
  const pausedFrameA = lastFrame(pausedA);
  const pausedFrameB = lastFrame(pausedB);
  expect(pausedFrameA.matrices.length, "没有采集到 u_model 矩阵 ⇒ 播放器没在画").toBeGreaterThan(0);
  expect(
    sameMatrixLists(pausedFrameA.matrices, pausedFrameB.matrices),
    `暂停时移动鼠标改变了图层矩阵 ⇒ cameraparallax:false 没有生效（${pausedFrameA.matrices.length} vs ${pausedFrameB.matrices.length} 层）`,
  ).toBe(true);
  expect(
    pausedFrameA.vertices,
    "暂停时蒙皮顶点仍在变 ⇒ 暂停没有冻结待机动画",
  ).toEqual(pausedFrameB.vertices);
  expect(maxVertexShift(pausedFrameA, pausedFrameB), "暂停时角色仍在动").toBe(0);

  // ── ② 播放态：蒙皮顶点随时间变化，头骨带动头发矩阵旋转 ────────────────
  const unpaused = await page.evaluate(() => {
    const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    if (!frame?.contentWindow) return false;
    frame.contentWindow.postMessage({ type: "dsh-set-pause", paused: false }, "*");
    return true;
  });
  expect(unpaused).toBe(true);

  const motionA = await sample(page, 320);
  const motionB = await sample(page, 1400);
  const motionC = await sample(page, 1400);
  const motionFrameA = lastFrame(motionA);
  const motionFrameB = lastFrame(motionB);
  const motionFrameC = lastFrame(motionC);
  await page.screenshot({ path: resolve(SHOT_DIR, "playing.png") });
  for (let i = 0; i < 5; i++) {
    await page.screenshot({ path: resolve(SHOT_DIR, `phase-${i}.png`) });
    await page.waitForTimeout(700);
  }

  expect(
    motionFrameB.vertices.length,
    "播放后没有蒙皮顶点更新 ⇒ LBS 待机动画没跑",
  ).toBeGreaterThan(0);
  expect(
    motionFrameA.vertices.join(",") !== motionFrameC.vertices.join(","),
    "播放后蒙皮顶点始终不变 ⇒ 待机动画没有推进",
  ).toBe(true);

  const shift = maxVertexShift(motionFrameA, motionFrameC);
  console.log("max skinned vertex shift(px):", shift);
  expect(shift, "播放后角色蒙皮位移过小，肉眼仍是静态站姿").toBeGreaterThan(2);

  const anglesB = motionFrameB.matrices.map(matrixAngle);
  const anglesC = motionFrameC.matrices.map(matrixAngle);
  const turned = anglesB.reduce((count, angle, index) => {
    const other = anglesC[index];
    return count + (other != null && Math.abs(other - angle) > 0.5 ? 1 : 0);
  }, 0);
  expect(turned, "没有哪个图层的角度随时间变化 ⇒ 头发没有跟随头骨").toBeGreaterThan(0);

  expect(errors, errors.join("\n")).toEqual([]);
});
