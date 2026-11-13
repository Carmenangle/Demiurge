import { expect, test } from "@playwright/test";

/**
 * 聊天视频卡的尺寸规格回归（问题 2，2026-11-01 用户截图）。
 *
 * 立项原因：`styles.css` 只给 `.img-card img` 写了 `width:100%; max-width:360px`，
 * 而视频卡渲染的是裸 `<video>`（ChatMessages.tsx 的 `part.type === "video"` 分支）。
 * 缺规则时 `<video>` 按**原始像素**铺开（本机产物 864×1536 ⇒ 864 CSS px），
 * 比上方成图宽一倍多、比视口还高 ⇒ 「插入的比例不对，总是无法呈现所有的初始画面」。
 *
 * 判别口径（必须能失败）：先断言真实视频的 `videoWidth > 360`，
 * 否则「宽 ≤360」是空断言。
 *
 * 跑法（需真实后端 8010 与 vite 5173 已在运行，令牌由 config 统一带头）：
 *   npx playwright test --config playwright.e2e-manual.config.ts \
 *     --output=../_tmp/e2e-video-card-size -g video-card-size
 * 自清：注入节点量完即摘；不改用户选中的作品，也不写任何数据链路。
 *
 * 产物视频路径一律**运行时取**（env 覆盖，否则扫 userdata 下最近的 mp4）：
 * 源码里写死仓库绝对路径与作品名会被发布面隐私闸（`release_preflight.private_name_leaks`）
 * 当成「用户真实专名出现在上传面」拦红后端全量。
 */

import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

function newestRealVideo(): string {
  const fromEnv = process.env.E2E_VIDEO_ABS_PATH;
  if (fromEnv && existsSync(fromEnv)) return fromEnv;
  const root = path.resolve(__dirname, "..", "..", "userdata", "pictures");
  if (!existsSync(root)) return "";
  const readDir = (dir: string) => {
    try {
      return readdirSync(dir, { withFileTypes: true });
    } catch {
      return [];
    }
  };
  let newest = "";
  let newestMtime = -1;
  const stack: string[] = [root];
  while (stack.length > 0) {
    const dir = stack.pop() as string;
    for (const entry of readDir(dir)) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile() && entry.name.toLowerCase().endsWith(".mp4")) {
        const mtime = statSync(full).mtimeMs;
        if (mtime > newestMtime) {
          newestMtime = mtime;
          newest = full;
        }
      }
    }
  }
  return newest;
}

const VIDEO_FILE = newestRealVideo();
const VIDEO_URL = VIDEO_FILE
  ? "/api/comfyui/local-view?path=" + encodeURIComponent(VIDEO_FILE)
  : "";

type Box = {
  w: number; h: number; intrinsicW: number; intrinsicH: number;
  maxW: string; display: string;
};

async function measureVideo(page: import("@playwright/test").Page, sel: string): Promise<Box> {
  return page.evaluate((css) => {
    const el = document.querySelector<HTMLVideoElement>(css)!;
    const b = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      w: Math.round(b.width), h: Math.round(b.height),
      intrinsicW: el.videoWidth, intrinsicH: el.videoHeight,
      maxW: cs.maxWidth, display: cs.display,
    };
  }, sel);
}

test.describe("chat video card sizing", () => {
  test("视频卡与图片卡同规格且首屏装得下 @video-card-size", async ({ page }) => {
    test.setTimeout(90_000);
    await page.goto("/", { waitUntil: "networkidle" });

    // 真实数据优先：当前对话里已有视频卡就直接量它
    let sel = ".img-card video";
    const realCards = await page.locator(sel).count();
    console.log("path", realCards > 0 ? `真实对话卡 ×${realCards}` : "注入同形态卡");
    if (realCards === 0) {
      // 没有则按 ChatMessages 的 video 分支同形态注入（同一份真实 CSS、同一个真实产物）
      test.skip(VIDEO_URL === "", "userdata 下找不到真实产物视频，且未设 E2E_VIDEO_ABS_PATH");
      test.skip((await page.locator(".msg.assistant, .message, .chat-body").count()) === 0,
        "对话页未挂载，跳过注入式验证");
      await page.evaluate(async ({ url }) => {
        const host = document.querySelector(".msg.assistant .msg-body")
          || document.querySelector(".message") || document.querySelector(".chat-body")
          || document.body;
        const card = document.createElement("div");
        card.className = "img-card";
        card.setAttribute("data-probe", "video-card");
        const video = document.createElement("video");
        video.src = url;
        video.controls = true; video.loop = true; video.playsInline = true;
        video.setAttribute("data-probe", "video-el");
        card.appendChild(video);
        const tools = document.createElement("div");
        tools.className = "img-tools";
        tools.innerHTML = '<a class="img-tool">查看原文件</a><button class="img-tool">下载</button>';
        card.appendChild(tools);
        host.appendChild(card);
        await new Promise<void>((resolve) => {
          if (video.readyState >= 1) return resolve();
          video.addEventListener("loadedmetadata", () => resolve(), { once: true });
          setTimeout(resolve, 15_000);
        });
      }, { url: VIDEO_URL });
      sel = "[data-probe='video-el']";
    }

    const box = await measureVideo(page, sel);
    console.log("video box", JSON.stringify(box));

    // ① 判别力前提：产物本身宽于 360，否则下面两条是空断言 ⇒ 换宽幅产物再判
    test.skip(
      box.intrinsicW <= 360,
      `产物原生宽 ${box.intrinsicW} ≤360，规则无从判别；用 E2E_VIDEO_ABS_PATH 指向宽幅产物`,
    );
    // ② 规则命中：与 .img-card img 同规格
    expect(box.maxW, "max-width 必须是 360px").toBe("360px");
    expect(box.display, "video 必须是 block（与 img 同口径，不留行内基线缝）").toBe("block");
    expect(box.w, `渲染宽 ${box.w} 不得越过 360`).toBeLessThanOrEqual(361);
    // ③ 初始画面装得进一屏（9:16 下 360 宽 ⇒ 640 高，视口 900）
    const vh = await page.evaluate(() => window.innerHeight);
    expect(box.h, `渲染高 ${box.h} 必须不超过视口 ${vh}`).toBeLessThanOrEqual(vh);
    // ④ 宽高比守恒：不是被拉扁/裁切
    const ratio = box.w / box.h;
    expect(Math.abs(ratio - box.intrinsicW / box.intrinsicH), `比例 ${ratio.toFixed(3)} 走样`).toBeLessThan(0.02);

    await page.evaluate(() => document.querySelector("[data-probe='video-card']")?.remove());
  });
});
