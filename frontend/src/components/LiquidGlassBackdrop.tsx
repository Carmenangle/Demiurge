import { useEffect, useRef, useState } from "react";
import { localViewUrl } from "../api/comfyui";
import { attachSceneBackdrop, type SceneBackdropMount, type SceneBackdropState } from "../lib/scene/sceneBackdrop";
import {
  LIQUID_GLASS_REFRESH_EVENT, wallpaperFilter, wallpaperTransform,
} from "../lib/liquidGlass";
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
  // 场景来源：新条目存 sceneSource（workshop:<id> / local:<路径>）；旧条目只有 workshopId，
  // 按工坊口径补出来，避免升级后已应用的工坊壁纸失去实时渲染。
  const sceneSource = wallpaper?.scenePkg
    ? (wallpaper.sceneSource || (wallpaper.workshopId ? `workshop:${wallpaper.workshopId}` : undefined))
    : undefined;
  const presentRef = useRef({ fit: lg.wallpaperFit, paused });
  presentRef.current = { fit: lg.wallpaperFit, paused };

  useEffect(() => {
    const host = sceneHostRef.current;
    if (!sceneSource || !host) {
      sceneMountRef.current?.dispose();
      sceneMountRef.current = null;
      setSceneStatus("off");
      return undefined;
    }
    setSceneStatus("loading");
    const mount = attachSceneBackdrop({
      host,
      source: sceneSource,
      fit: presentRef.current.fit,
      paused: presentRef.current.paused,
      onState: (state: SceneBackdropState) => {
        if (state.status === "fallback") {
          console.warn("[wallpaper] scene 实时渲染失败，回落静态预览:", state.reason);
        }
        setSceneStatus(state.status);
      },
    });
    sceneMountRef.current = mount;
    return () => {
      mount.dispose();
      if (sceneMountRef.current === mount) sceneMountRef.current = null;
    };
  }, [sceneSource, revision]);

  useEffect(() => {
    sceneMountRef.current?.update({ fit: lg.wallpaperFit, paused });
  }, [lg.wallpaperFit, paused]);

  return (
    <div className="app-backdrop" aria-hidden="true">
      <div className="app-backdrop-aurora" />
      {wallpaper && !failed && (
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
