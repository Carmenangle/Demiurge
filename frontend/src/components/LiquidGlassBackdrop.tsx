import { useEffect, useRef, useState } from "react";
import { localViewUrl } from "../api/comfyui";
import type { ScenePropertyValues } from "../api/userState";
import { attachSceneBackdrop, type SceneBackdropMount, type SceneBackdropState } from "../lib/scene/sceneBackdrop";
import { publishSceneBindings } from "../lib/scene/sceneBindings";
import { loadScenePresets, scenePresetValues } from "../lib/scene/scenePresets";
import {
  LIQUID_GLASS_REFRESH_EVENT, wallpaperFilter, wallpaperTransform,
} from "../lib/liquidGlass";
import { useWallpaperInteraction } from "../lib/wallpaperActivity";
import { liquidGlassSceneSource } from "../stores/settings";
import type { LiquidGlassSettings, LiquidGlassWallpaper } from "../stores/settings";

const FIT_MAP: Record<LiquidGlassSettings["wallpaperFit"], React.CSSProperties["objectFit"]> = {
  cover: "cover",
  contain: "contain",
  center: "none",
  fill: "fill",
};

/** 全应用壁纸层：图片/视频 + 滤镜 + 暗化；视频按用户条件暂停。 */
export function LiquidGlassBackdrop({
  lg,
  wallpaper,
}: {
  lg: LiquidGlassSettings;
  wallpaper: LiquidGlassWallpaper | null;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [hidden, setHidden] = useState(() => document.visibilityState === "hidden");
  const [focused, setFocused] = useState(() => document.hasFocus());
  const [onBattery, setOnBattery] = useState(false);
  const [revision, setRevision] = useState(0);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const failuresRef = useRef(0);
  const retryTimerRef = useRef(0);

  useEffect(() => {
    const onVisibility = () => setHidden(document.visibilityState === "hidden");
    const onFocus = () => setFocused(true);
    const onBlur = () => setFocused(false);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    type BatteryLike = {
      charging: boolean;
      addEventListener: (type: "chargingchange", fn: () => void) => void;
      removeEventListener: (type: "chargingchange", fn: () => void) => void;
    };
    const nav = navigator as Navigator & { getBattery?: () => Promise<BatteryLike> };
    let battery: BatteryLike | null = null;
    const sync = () => {
      if (alive && battery) setOnBattery(!battery.charging);
    };
    nav.getBattery?.().then((next) => {
      battery = next;
      sync();
      next.addEventListener("chargingchange", sync);
    }).catch(() => { /* 不支持电池 API 的浏览器不启用该条件 */ });
    return () => {
      alive = false;
      battery?.removeEventListener("chargingchange", sync);
    };
  }, []);

  useEffect(() => {
    const onRefresh = () => setRevision((r) => r + 1);
    window.addEventListener(LIQUID_GLASS_REFRESH_EVENT, onRefresh);
    return () => window.removeEventListener(LIQUID_GLASS_REFRESH_EVENT, onRefresh);
  }, []);

  // 换壁纸 / 手动刷新时清零失败计数，并取消未执行的重试
  useEffect(() => {
    failuresRef.current = 0;
    setFailed(false);
    setReload(0);
    if (retryTimerRef.current) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = 0;
    }
  }, [wallpaper?.path, revision]);

  useEffect(() => () => {
    if (retryTimerRef.current) window.clearTimeout(retryTimerRef.current);
  }, []);

  // 一次加载失败可能只是后端/磁盘忙碌；有限次自动重试，避免永久回落极光
  const retryLater = () => {
    if (failuresRef.current >= 2 || retryTimerRef.current) return;
    failuresRef.current += 1;
    retryTimerRef.current = window.setTimeout(() => {
      retryTimerRef.current = 0;
      setFailed(false);
      setReload((n) => n + 1);
    }, 900 * failuresRef.current);
  };

  const onMediaError = () => {
    setFailed(true);
    retryLater();
  };

  const mediaReady = () => { failuresRef.current = 0; };

  // 窗口重新可见 / 聚焦时若仍失败，立刻再试一次（用户切回应用是明确的恢复时机）
  useEffect(() => {
    if (!failed) return;
    const recover = () => {
      if (retryTimerRef.current) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = 0;
      }
      failuresRef.current = 0;
      setFailed(false);
      setReload((n) => n + 1);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") recover();
    };
    window.addEventListener("focus", recover);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", recover);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [failed]);

  const paused = lg.wallpaperPaused
    || (lg.pauseOnHidden && hidden)
    || (lg.pauseOnBlur && !focused)
    || (lg.pauseOnBattery && onBattery);
  // 滚轮/拖动期间停实时渲染：暂停一帧的绘制开销远小于滚动掉帧的观感损失。
  const interacting = useWallpaperInteraction();
  const scenePaused = paused || interacting;

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.playbackRate = lg.playbackRate;
    if (paused) {
      video.pause();
      return;
    }
    video.play().catch(() => { /* 自动播放被拦截时由用户点击播放恢复 */ });
  }, [paused, lg.playbackRate, wallpaper?.path, revision, reload]);

  // 场景就绪后预览层会卸载：媒体元素脱离 DOM 后仍可能继续解码，卸载时显式暂停。
  useEffect(() => {
    const video = videoRef.current;
    return () => { video?.pause(); };
  }, [wallpaper?.path, revision, reload]);

  const mediaStyle: React.CSSProperties = {
    filter: wallpaperFilter(lg),
    transform: wallpaperTransform(lg),
    objectFit: FIT_MAP[lg.wallpaperFit],
  };

  // ---- scene 实时渲染（Wallpaper Engine 创意工坊带 scene.pkg 的作品）----
  // 只让「换作品 / 手动刷新」重建解码会话；fit / 暂停走 update，避免每次切标签页
  // 都重新解一遍包（大包解码实测可达 16 秒）。
  const sceneHostRef = useRef<HTMLDivElement>(null);
  const sceneMountRef = useRef<SceneBackdropMount | null>(null);
  const [sceneStatus, setSceneStatus] = useState<"off" | "loading" | "ready" | "fallback">("off");
  // WE 预设是否已经落到播放器清单上：就绪后先画的是工程烘焙值，等 scene-properties +
  // 热重建这一趟跑完才是用户在 WE 里看到的那一面，中间这段用 data-scene-preset 标出来。
  const [scenePresetApplied, setScenePresetApplied] = useState(false);
  // 场景来源：新条目存 sceneSource（workshop:<id> / local:<路径>）；旧条目只有 workshopId，
  // 由 settings.liquidGlassSceneSource 按工坊口径补出来（与选项面板同源，规则只写一处）。
  const sceneSource = liquidGlassSceneSource(wallpaper);
  const presentRef = useRef({ fit: lg.wallpaperFit, paused: scenePaused });
  presentRef.current = { fit: lg.wallpaperFit, paused: scenePaused };
  // 用户在应用内改过的 WE 预设键；内容签名变化才热重建（切标签页/改滤镜不带它）。
  const presetRef = useRef<ScenePropertyValues>({});
  presetRef.current = wallpaper?.preset ?? {};
  /** 该作品在 Wallpaper Engine 里的生效值，由 scene-properties 接口异步补上。 */
  const basePresetRef = useRef<ScenePropertyValues>({});
  const presetSignature = JSON.stringify(
    Object.entries(scenePresetValues(basePresetRef.current, presetRef.current))
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  );

  useEffect(() => {
    const host = sceneHostRef.current;
    if (!sceneSource || !host) {
      sceneMountRef.current?.dispose();
      sceneMountRef.current = null;
      setSceneStatus("off");
      return undefined;
    }
    let cancelled = false;
    setSceneStatus("loading");
    setScenePresetApplied(false);
    basePresetRef.current = {};
    const mount = attachSceneBackdrop({
      host,
      source: sceneSource,
      fit: presentRef.current.fit,
      paused: presentRef.current.paused,
      properties: { ...presetRef.current },
      onState: (state: SceneBackdropState) => {
        if (state.status === "ready") {
          // 清单实际消费的属性名登记给选项面板：不在这里的 WE 预设改了也不影响画面。
          publishSceneBindings(sceneSource, state.bindings);
          setScenePresetApplied(state.presetApplied);
        }
        if (state.status === "fallback") {
          console.warn("[wallpaper] scene 实时渲染失败，回落静态预览:", state.reason);
        }
        setSceneStatus(state.status);
      },
    });
    sceneMountRef.current = mount;
    // 预设值随 scene-properties 到位后再补一次热重建：先按工程烘焙值出图，避免多等一次
    // HTTP；拿到 WE 生效值后走 rebuild 通道（包体与纹理都还在 Worker 缓存里）。
    loadScenePresets(sceneSource, { force: revision > 0 })
      .then((snapshot) => {
        if (cancelled) return;
        basePresetRef.current = snapshot.base;
        mount.setProperties(scenePresetValues(snapshot.base, presetRef.current));
      })
      .catch(() => { /* 读不到 project.json：按工程烘焙值渲染，面板自会提示 */ });
    return () => {
      cancelled = true;
      mount.dispose();
      if (sceneMountRef.current === mount) sceneMountRef.current = null;
    };
  }, [sceneSource, revision]);

  useEffect(() => {
    sceneMountRef.current?.setProperties(
      scenePresetValues(basePresetRef.current, presetRef.current),
    );
  }, [presetSignature]);

  useEffect(() => {
    sceneMountRef.current?.update({ fit: lg.wallpaperFit, paused: scenePaused });
  }, [lg.wallpaperFit, scenePaused]);

  // 暂停 = 停帧，代价是合成层被重建时画布上没有新帧可贴，壁纸会整块变白（窗口缩放、
  // 切回前台都会重建）。播放器留了「立刻补一帧」的通道，暂停态下在这些时机叫它补一次。
  useEffect(() => {
    if (sceneStatus !== "ready" || !scenePaused) return undefined;
    const repaint = () => sceneMountRef.current?.recover();
    const onVisible = () => {
      if (document.visibilityState === "visible") repaint();
    };
    window.addEventListener("resize", repaint);
    window.addEventListener("focus", repaint);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("resize", repaint);
      window.removeEventListener("focus", repaint);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sceneStatus, scenePaused]);

  // 极光只在真正可见时才挂载/动画：图片视频用 cover/fill 或场景就绪时，画布不透明、
  // 极光被完整遮住，却仍在后台每帧计算 40px 模糊（Intel 核显实测单帧 ~30ms）。
  const auroraCovered = Boolean(wallpaper) && !failed
    && (sceneStatus === "ready" || lg.wallpaperFit === "cover" || lg.wallpaperFit === "fill");
  // 实时场景就绪后画布已是不透明整屏，底下的静态预览不再可见；卸掉它可停掉
  // 预览动图/大图的光栅化与合成（scene 包的预览常是 250×250 的 GIF，被拉满全屏）。
  const sceneCovered = Boolean(sceneSource) && sceneStatus === "ready";

  return (
    <div
      className={`app-backdrop${interacting ? " is-interacting" : ""}${sceneCovered ? " has-scene" : ""}`}
      aria-hidden="true"
    >
      {!auroraCovered && <div className="app-backdrop-aurora" />}
      {wallpaper && !failed && !sceneCovered && (
        wallpaper.kind === "video" ? (
          <video
            key={`${wallpaper.path}:${revision}:${reload}`}
            ref={videoRef}
            className="app-backdrop-media"
            src={localViewUrl(wallpaper.path)}
            style={mediaStyle}
            muted
            loop
            playsInline
            preload="auto"
            onCanPlay={() => {
              mediaReady();
              if (!paused) videoRef.current?.play().catch(() => { /* 等待下次 canplay 再试 */ });
            }}
            onError={onMediaError}
          />
        ) : (
          <img
            key={`${wallpaper.path}:${revision}:${reload}`}
            className="app-backdrop-media"
            src={localViewUrl(wallpaper.path)}
            style={mediaStyle}
            alt=""
            draggable={false}
            onLoad={mediaReady}
            onError={onMediaError}
          />
        )
      )}
      {sceneSource && (
        <div
          ref={sceneHostRef}
          className="app-backdrop-scene-host"
          data-scene-status={sceneStatus}
          data-scene-preset={scenePresetApplied ? "applied" : "pending"}
          style={{
            filter: mediaStyle.filter,
            transform: mediaStyle.transform,
            visibility: sceneStatus === "ready" ? "visible" : "hidden",
          }}
        />
      )}
      <div className="app-backdrop-scrim" />
    </div>
  );
}
