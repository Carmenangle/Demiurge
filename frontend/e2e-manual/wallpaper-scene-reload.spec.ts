import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验证（用户实锤 2026-10-25）：场景壁纸首次加载会动，**页面重载后不动**。
 *
 * 用真实存档设置（隔离后端的 user_state 副本），不 mock `/api/user-state`：
 * 这条缺陷只在「有历史 preset / sceneSource 的旧存档 + 页面重载」时出现，
 * 手写 mock 会把要复现的前提条件本身改掉。
 *
 * 采样口径不靠肉眼看截图：
 * - `drawsDelta`：iframe 里 WebGL draw call 的增量（>0 = 播放器还在出帧）；
 * - `videos`：视频纹理 `<video>` 的 `paused / currentTime`（时间纹理冻结会钉在原处）；
 * - `videoUploadsDelta`：**源是 `<video>`** 的 texImage2D 次数（时间推进只证明解码器在跑，
 *   只有这一项证明纹理真的在换帧——2026-10-25 的缺陷正是二者脱钩）；
 * - 宿主 `data-scene-status`：ready / fallback（fallback = 已回落静态预览）。
 *
 * 跑法：隔离后端(8011) + 临时 vite(5198)，串行：
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test \
 *     --config playwright.e2e-manual.config.ts e2e-manual/wallpaper-scene-reload.spec.ts --workers=1
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-scene-reload");
const APP_URL = process.env.E2E_APP_URL || "/";

type Page = import("@playwright/test").Page;

type ProbeState = {
  draws: number;
  uploads: number;
  videoUploads: number;
  raf: number;
};

type Sample = {
  status: string | null;
  preset: string | null;
  drawsDelta: number;
  rafDelta: number;
  uploadsDelta: number;
  videoUploadsDelta: number;
  videos: Array<{ paused: boolean; readyState: number; time: number; duration: number; src: string }>;
  visibility: string;
  hasFocus: boolean;
  canvas: { width: number; height: number } | null;
};

/**
 * 与本次缺陷无关、但生产 CSP 一定会打进 console 的三类噪音（2026-10-25 实测于 dist）：
 * `ws://[::1]:*` / `http://[::1]:*` 不是合法 CSP source（被浏览器忽略），
 * `index.html` 里 Anton 的 Google Fonts 样式表被 `style-src 'self' 'unsafe-inline'` 拦掉。
 * 这三条是**独立的既有偏差**，不属于「壁纸不动」这一刀，先按文案白名单滤掉，
 * 免得把 unrelated 红算到本条验收头上（也不借此掩盖：见交接说明）。
 */
const CSP_NOISE = [
  /invalid source: 'ws:\/\/\[::1\]:\*'/,
  /invalid source: 'http:\/\/\[::1\]:\*'/,
  /fonts\.googleapis\.com\/css2\?family=Anton/,
];

function appErrors(list: string[]): string[] {
  return list.filter((message) => !CSP_NOISE.some((pattern) => pattern.test(message)));
}

/** 在 iframe 里挂一次计数器（draw call / texImage2D / rAF），随后静置一段时间看增量。 */
async function sample(page: Page, waitMs = 1200): Promise<Sample> {
  return page.evaluate(async (wait) => {
    const host = document.querySelector<HTMLElement>(".app-backdrop-scene-host");
    const iframe = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    const win = iframe?.contentWindow as (Window & {
      __reloadProbe?: ProbeState;
      WebGLRenderingContext?: typeof WebGLRenderingContext;
    }) | null;
    const doc = iframe?.contentDocument;

    if (win && !win.__reloadProbe) {
      const probe: ProbeState = { draws: 0, uploads: 0, videoUploads: 0, raf: 0 };
      win.__reloadProbe = probe;
      const proto = win.WebGLRenderingContext?.prototype;
      if (proto) {
        for (const name of ["drawArrays", "drawElements"] as const) {
          const original = proto[name];
          if (typeof original !== "function") continue;
          (proto as unknown as Record<string, unknown>)[name] = function patched(
            this: WebGLRenderingContext, ...args: unknown[]
          ) {
            probe.draws += 1;
            return (original as (...a: unknown[]) => unknown).apply(this, args);
          };
        }
        const originalUpload = proto.texImage2D;
        if (typeof originalUpload === "function") {
          (proto as unknown as Record<string, unknown>).texImage2D = function patched(
            this: WebGLRenderingContext, ...args: unknown[]
          ) {
            // 只数「源是 <video>」的上传：uploads 总量分不清视频逐帧重采与文字位图换字。
            probe.uploads += 1;
            const source = args[args.length - 1];
            if (source && typeof win.HTMLVideoElement === "function" && source instanceof win.HTMLVideoElement) {
              probe.videoUploads += 1;
            }
            return (originalUpload as (...a: unknown[]) => unknown).apply(this, args);
          };
        }
      }
      const winRaf = win.requestAnimationFrame.bind(win);
      (win as unknown as { requestAnimationFrame: typeof win.requestAnimationFrame })
        .requestAnimationFrame = (callback: FrameRequestCallback) => {
        probe.raf += 1;
        return winRaf(callback);
      };
    }

    const probe = win?.__reloadProbe;
    const before = probe ? { ...probe } : null;
    await new Promise((done) => window.setTimeout(done, wait));
    const after = probe ? { ...probe } : null;

    const videos = Array.from(doc?.querySelectorAll("video") ?? []).map((video) => ({
      paused: video.paused,
      readyState: video.readyState,
      time: video.currentTime,
      duration: Number.isFinite(video.duration) ? video.duration : -1,
      src: (video.currentSrc || video.src).slice(0, 60),
    }));
    const canvas = doc?.querySelector("canvas") ?? null;

    return {
      status: host?.dataset.sceneStatus ?? null,
      preset: host?.dataset.scenePreset ?? null,
      drawsDelta: before && after ? after.draws - before.draws : -1,
      rafDelta: before && after ? after.raf - before.raf : -1,
      uploadsDelta: before && after ? after.uploads - before.uploads : -1,
      videoUploadsDelta: before && after ? after.videoUploads - before.videoUploads : -1,
      videos,
      visibility: document.visibilityState,
      hasFocus: document.hasFocus(),
      canvas: canvas ? { width: canvas.width, height: canvas.height } : null,
    };
  }, waitMs);
}

async function waitReady(page: Page, label: string): Promise<Sample> {
  await expect(page.locator(".app-backdrop-scene-host"), `${label}：scene 没有就绪`)
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });
  // 预设热重建（scene-properties → rebuild → setManifest）完成后再采样
  await expect(page.locator(".app-backdrop-scene-host"), `${label}：预设没有落上`)
    .toHaveAttribute("data-scene-preset", "applied", { timeout: 180_000 });
  return sample(page, 1600);
}

test.use({ viewport: { width: 1600, height: 900 } });

test("场景壁纸重载后仍然在动（draw call / 视频纹理时间）", async ({ page }) => {
  test.setTimeout(420_000);
  const errors: string[] = [];
  const warnings: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
    if (msg.type() === "warning" && msg.text().includes("wallpaper")) warnings.push(msg.text());
  });
  const failedRequests: string[] = [];
  page.on("response", (response) => {
    if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`);
  });

  mkdirSync(SHOT_DIR, { recursive: true });

  await page.goto(APP_URL);
  const first = await waitReady(page, "首次加载");
  console.log("[reload-e2e] first:", JSON.stringify(first));
  await page.screenshot({ path: resolve(SHOT_DIR, "first.png") });

  const firstAgain = await sample(page, 1600);
  console.log("[reload-e2e] firstAgain:", JSON.stringify(firstAgain));

  await page.reload();
  const after = await waitReady(page, "重载后");
  console.log("[reload-e2e] afterReload:", JSON.stringify(after));
  await page.screenshot({ path: resolve(SHOT_DIR, "after-reload.png") });

  const afterAgain = await sample(page, 1600);
  console.log("[reload-e2e] afterReloadAgain:", JSON.stringify(afterAgain));

  console.log("[reload-e2e] warnings:", JSON.stringify(warnings));
  console.log("[reload-e2e] failedRequests:", JSON.stringify(failedRequests.slice(0, 20)));
  console.log("[reload-e2e] errors:", JSON.stringify(errors.slice(0, 20)));

  // 首次加载必须真的在动（否则说明测试前提不成立，不是重载缺陷）
  expect(first.drawsDelta + firstAgain.drawsDelta, "首次加载就没有出帧").toBeGreaterThan(0);
  // 缺陷断言：重载后仍要出帧；视频纹理的时间也要继续推进
  expect(
    after.drawsDelta + afterAgain.drawsDelta,
    `重载后播放器停止出帧（drawsDelta=${after.drawsDelta}/${afterAgain.drawsDelta}）`,
  ).toBeGreaterThan(0);
  if (after.videos.length > 0) {
    expect(
      Math.abs(afterAgain.videos[0].time - after.videos[0].time),
      `重载后视频纹理时间冻结（${after.videos[0].time} → ${afterAgain.videos[0].time}）`,
    ).toBeGreaterThan(0.05);
    expect(afterAgain.videos[0].paused, "重载后视频纹理仍处于 paused").toBe(false);
    // 时间推进只证明「解码器在跑」，不证明「纹理在换」。用户实锤的形态是二者脱钩：
    // 媒体照常走、纹理永久停在同一帧 ⇒ 美术层定格。判据必须是视频源的 texImage2D 次数。
    expect(
      after.videoUploadsDelta + afterAgain.videoUploadsDelta,
      `重载后视频纹理没有再被上传（${after.videoUploadsDelta}/${afterAgain.videoUploadsDelta}）`,
    ).toBeGreaterThan(0);
  }
  expect(warnings, warnings.join("\n")).toEqual([]);
});

/**
 * 用户说的「重新加载」在界面上有两种入口，第一条只覆盖了整页 reload：
 * 1. 壁纸面板的「刷新」按钮 → LIQUID_GLASS_REFRESH_EVENT → revision++ → 卸载重挂场景；
 * 2. 重载期间用户还在滚动/拖动 → 场景以 paused 状态挂载，交互结束后必须能恢复。
 * 暂停＝定格是垫片实现的，恢复走「补跑挂起帧」，这两条路径任何一处断掉都会永久冻结。
 */
test("刷新壁纸 / 加载中滚动之后，动画必须恢复", async ({ page }) => {
  test.setTimeout(420_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await page.goto(APP_URL);
  const first = await waitReady(page, "首次加载");
  expect(first.drawsDelta + (await sample(page, 1200)).drawsDelta, "首次加载就没有出帧").toBeGreaterThan(0);

  // ── ① 壁纸面板「刷新」＝ revision++，场景整个重挂 ─────────────────────
  const refresh = async (label: string) => {
    await page.evaluate(() => {
      window.dispatchEvent(new Event("demiurge:liquid-glass-refresh"));
    });
    // 等状态真正回到 loading：否则 waitReady 会命中旧挂载残留的 ready 属性。
    await expect(page.locator(".app-backdrop-scene-host"), `${label}：没有重新挂载`)
      .toHaveAttribute("data-scene-status", "loading", { timeout: 15_000 });
  };
  for (let round = 0; round < 2; round++) {
    await refresh(`第 ${round + 1} 次刷新`);
    const sampleNow = await waitReady(page, `第 ${round + 1} 次刷新后`);
    console.log(`[reload-e2e] refresh#${round + 1}:`, JSON.stringify(sampleNow));
    await page.screenshot({ path: resolve(SHOT_DIR, `refresh-${round + 1}.png`) });
    expect(
      sampleNow.drawsDelta + (await sample(page, 1200)).drawsDelta,
      `第 ${round + 1} 次刷新后播放器停止出帧`,
    ).toBeGreaterThan(0);
    if (sampleNow.videos.length > 0) {
      expect(sampleNow.videos[0].paused, `第 ${round + 1} 次刷新后视频纹理 paused`).toBe(false);
    }
  }

  // ── ② 重载进行中持续滚动：场景以 paused 挂载，交互停止后必须恢复 ──────
  await refresh("加载中滚动前");
  // 从 loading 一直滚到 ready：覆盖「以 paused 状态挂载 + 预设热重建也在 paused 期间」。
  const readyDeadline = Date.now() + 180_000;
  while (Date.now() < readyDeadline) {
    const host = page.locator(".app-backdrop-scene-host");
    const [status, preset] = await Promise.all([
      host.getAttribute("data-scene-status"),
      host.getAttribute("data-scene-preset"),
    ]);
    if (status === "ready" && preset === "applied") break;
    await page.mouse.wheel(0, 120);
    await page.waitForTimeout(120);
  }
  const afterRefresh = await waitReady(page, "加载中滚动后");
  await page.waitForTimeout(2400); // 等 interacting 归零、垫片恢复帧循环
  const resumed = await sample(page, 1600);
  console.log("[reload-e2e] after-scroll:", JSON.stringify(afterRefresh), "resumed:", JSON.stringify(resumed));
  await page.screenshot({ path: resolve(SHOT_DIR, "after-scroll.png") });
  expect(resumed.drawsDelta + (await sample(page, 1200)).drawsDelta, "加载中滚动后动画没有恢复").toBeGreaterThan(0);
  if (resumed.videos.length > 0) {
    expect(resumed.videos[0].paused, "加载中滚动后视频纹理仍 paused").toBe(false);
  }

  expect(appErrors(errors), appErrors(errors).join("\n")).toEqual([]);
});

/**
 * 「壁纸不动」的真实机理（2026-10-25 真机 WebView2 实锤，样张 `3723257973`）：视频纹理是这张
 * 作品唯一的美术层，而它过去**只由 requestVideoFrameCallback 驱动**。rVFC 的触发前提是解码帧
 * 真的被合成器呈现，这里的 `<video>` 是 1px / opacity:0 挂在 body 外的纯纹理源，WebView2 不呈现它
 * ⇒ 实测连续 12 秒 rVFC 0 次回调、视频源 texImage2D 0 次，而 currentTime 照常 1:1 推进、
 * totalVideoFrames 每秒 +30（探针日志未入库，数字逐字记在 vendor `NOTICE.md` 改动⑭）⇒ 画面定格在最后一次 loadeddata 的那一帧，
 * 文字层照常走 = 用户看到的「艺术字还在、壁纸不动了」。修复＝把重采搬进渲染循环（`pumpVideoTextures`）。
 *
 * 判据为什么用「上传次数 / 渲染帧数」的比值而不是「把 rVFC 掐死看上传还在不在」：
 * 后者试过两次都不成立（2026-10-25 实测记录）——① 把 `HTMLVideoElement.prototype
 * .requestVideoFrameCallback` 换成 no-op ⇒ currentTime 立刻冻在 3.161137、readyState 从 4 掉回 2；
 * ② 自己续挂一条「永不执行播放器回调」的 rVFC 链 ⇒ 同样冻在 3.120837。即在 Chromium 里
 * 「有没有活着的 rVFC 订阅」反过来决定了隐形 `<video>` 的媒体时钟要不要推进，钩子改掉的
 * 正是被测前提本身，此时「0 次上传」不能归因给纹理通道。真机那个「rVFC 恒 0 而时钟照走」
 * 的状态在 Playwright 里造不出来，所以这里只做行为学断言；形态锁与受控红见
 * `src/lib/scene/decodeScenePackage.test.ts` 的「视频纹理由渲染循环每帧重采」。
 *
 * 两条网的**分工不同，别把这条当泵的唯一判据**（2026-10-25 受控红实测）：
 * 只删模板里的 `pumpVideoTextures(now);` 一行、保留 rVFC 通道 ⇒ 本条**不红**：
 * Edge/Playwright 这边隐形 `<video>` 仍会被呈现，rVFC 自己就贡献约每三帧一次上传
 * （实测 `videoUploadsDelta/rafDelta` = 10/33 与 9/29，聚合 19 对阈值 floor(62×0.3)=18，
 * 以一次上传通过；同一份修复代码实测 35/24 与 31/25，聚合 66/49 ≈1.35）。
 * 阈值不能再抬高来「抓」它：媒体 30fps 而 rAF 60fps 时，健康的泵也只有一半帧能拿到新帧
 * （比值 ≈0.5），任何落在 0.31 与 0.5 之间的线都会在同一台机器上随机红。
 * ⇒ 这条是**行为学防护网**（守住用户实锤的失败形态：视频纹理上传归零 ⇒ 美术层定格），
 *   泵的**确定性判据**是 `src/lib/scene/decodeScenePackage.test.ts` 的形态锁
 *   「视频纹理由渲染循环每帧重采」——同一个单行改动下它实测**红**；
 *   真机侧的前后对照见 vendor `NOTICE.md` 改动⑭（WebView2：修复前连续 12s rVFC 0 次 /
 *   视频上传 0 次而 currentTime 1:1 推进；修复后 videoUploadsPerSec 15 对 rafPerSec 22.3）。
 */
test("视频纹理由渲染循环逐帧重采：上传次数跟着渲染帧数走", async ({ page }) => {
  test.setTimeout(420_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await page.goto(APP_URL);
  const ready = await waitReady(page, "首次加载");
  test.skip(ready.videos.length === 0, "当前激活壁纸没有视频纹理，本条判据不适用");

  // 只取就绪后的前两个窗口：Playwright/Edge 这边隐形 `<video>` 在 ~3.1s 后会被引擎自己挂起
  // （readyState 4→2、currentTime 钉死在 3.120994，与本仓库代码无关），真机 WebView2 连续 12s+
  // 解码照常（真机探针里 totalVideoFrames 持续增长，见 vendor `NOTICE.md` 改动⑭）。判据不跟引擎行为较劲。
  const windows = [ready, await sample(page, 1600)];
  console.log("[reload-e2e] video pump windows:", JSON.stringify(windows));
  await page.screenshot({ path: resolve(SHOT_DIR, "video-pump.png") });

  for (const snapshot of windows) {
    expect(snapshot.rafDelta, "播放器帧循环没在跑，采样窗口无效").toBeGreaterThan(0);
    expect(
      snapshot.videoUploadsDelta,
      `窗口内一次视频纹理上传都没有（uploads=0, raf=${snapshot.rafDelta}）⇒ 美术层定格在旧帧`,
    ).toBeGreaterThan(0);
  }

  const frames = windows.reduce((sum, snapshot) => sum + snapshot.rafDelta, 0);
  const uploads = windows.reduce((sum, snapshot) => sum + snapshot.videoUploadsDelta, 0);
  // 「每秒漏一帧」不算修复：上传总量要跟着渲染帧数走。
  // 0.3 是「防护网」口径的下限（真机媒体 30fps / rAF 60fps 时每两帧一次），不是泵的判别线；
  // 为什么不能再抬高，见上方块注释里的受控红实测。
  expect(
    uploads,
    `视频纹理没有逐帧重采（uploads=${uploads} 对 raf=${frames}，要求 >0.3×帧数）⇒ 美术层定格在旧帧`,
  ).toBeGreaterThan(Math.floor(frames * 0.3));
  expect(Math.abs(windows[1].videos[0].time - windows[0].videos[0].time), "媒体时钟没推进")
    .toBeGreaterThan(0.1);
  expect(appErrors(errors), appErrors(errors).join("\n")).toEqual([]);
});
