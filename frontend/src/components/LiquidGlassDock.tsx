import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ImagePlus, Images, Play, Pause, RefreshCw, RotateCcw, Trash2, X,
} from "lucide-react";
import {
  getWallpaperStore, migrateWallpaperStore, scanWorkshopWallpapers, uploadWallpaper,
  wallpaperThumbUrl,
  type ScenePropertyItem, type ScenePropertyValue, type ScenePropertyValues,
  type WallpaperStoreInfo, type WallpaperWorkshopPage,
} from "../api/userState";
import { localViewUrl } from "../api/comfyui";
import { LIQUID_GLASS_REFRESH_EVENT } from "../lib/liquidGlass";
import {
  subscribeFloatingPanels,
} from "../lib/floatingPanels";
import { useSceneBindings } from "../lib/scene/sceneBindings";
import {
  filterScenePresetItems, hexToSceneColor, loadScenePresets, roundSceneNumber,
  sceneColorToHex, scenePresetDeviation, scenePresetValues,
  type ScenePresetSnapshot,
} from "../lib/scene/scenePresets";
import {
  DEFAULT_LIQUID_GLASS, liquidGlassSceneSource, liquidGlassWallpaperKind,
  type LiquidGlassRopeForm, type LiquidGlassSettings, type LiquidGlassWallpaper,
} from "../stores/settings";
import { LiquidGlassWallpaperLibrary } from "./LiquidGlassWallpaperLibrary";

const ROPE_POS_KEY = "demiurge_liquid_glass_rope_pos";

// 迁移映射跨盘符/大小写比较：统一分隔符 + 小写（Windows 路径不区分大小写）
function pathKey(path: string): string {
  return path.replace(/\\/g, "/").toLowerCase();
}

const ACCENT_PRESETS = ["#8fb4ff", "#4f8cff", "#67DCE7", "#DD8FAC", "#F3B75F", "#F1717F", "#CBE77D", "#9b8cff"];
const GLASS_PRESETS = ["#ffffff", "#0d1524", "#67DCE7", "#DD8FAC", "#F3B75F", "#F1717F"];
const SIDEBAR_PRESETS = ["#ffffff", "#0d1524", "#101a30", "#1b1030", "#67DCE7", "#DD8FAC"];
const CONTENT_PRESETS = ["#ffffff", "#0d1524", "#101a30", "#1b1030", "#DD8FAC", "#F3B75F"];

type Tab = "glass" | "wallpaper" | "effects" | "rope";

function RangeRow(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  hint?: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="lg-field">
      <span className="lg-field__label">
        {props.label}
        <b>{props.value}{props.suffix || ""}</b>
      </span>
      <input
        type="range"
        min={props.min}
        max={props.max}
        step={props.step ?? 1}
        value={props.value}
        onChange={(e) => props.onChange(Number(e.target.value))}
      />
      {props.hint && <small>{props.hint}</small>}
    </label>
  );
}

function SwitchRow(props: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="lg-switch-row">
      <span>
        <strong>{props.label}</strong>
        {props.hint && <small>{props.hint}</small>}
      </span>
      <input
        type="checkbox"
        checked={props.checked}
        onChange={(e) => props.onChange(e.target.checked)}
      />
    </label>
  );
}

function ColorRow(props: {
  label: string;
  value: string;
  presets: string[];
  allowEmpty?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <div className="lg-field">
      <span className="lg-field__label">{props.label}<b>{props.value || "默认深色"}</b></span>
      <div className="lg-color-row">
        <input
          type="color"
          aria-label={props.label}
          value={props.value || "#ffffff"}
          onChange={(e) => props.onChange(e.target.value)}
        />
        {props.presets.map((c) => (
          <button
            key={c}
            type="button"
            className={`lg-swatch${props.value.toLowerCase() === c.toLowerCase() ? " active" : ""}`}
            style={{ background: c }}
            title={c}
            onClick={() => props.onChange(c)}
          />
        ))}
        {props.allowEmpty && (
          <button
            type="button"
            className={`lg-swatch lg-swatch--none${props.value ? "" : " active"}`}
            title="还原默认深色内容面"
            onClick={() => props.onChange("")}
          >
            <X size={12} />
          </button>
        )}
      </div>
    </div>
  );
}

function ScenePresetRow(props: {
  item: ScenePropertyItem;
  value: ScenePropertyValue;
  onChange: (value: ScenePropertyValue) => void;
}) {
  const { item, value } = props;
  if (item.type === "bool") {
    return (
      <SwitchRow
        label={item.label}
        checked={value === true || value === 1 || value === "1"}
        onChange={props.onChange}
      />
    );
  }
  if (item.type === "combo") {
    const options = item.options || [];
    const current = String(value ?? "");
    const picked = options.find((o) => o.value === current);
    return (
      <div className="lg-field">
        <span className="lg-field__label">
          {item.label}
          <b>{picked?.label || current || "-"}</b>
        </span>
        <select
          className="lg-select"
          value={current}
          onChange={(e) => props.onChange(e.target.value)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
    );
  }
  if (item.type === "textinput") {
    // 文字内容（「可自定义文字」的正文）：单行输入，值原样进预设差集。
    return (
      <div className="lg-field">
        <span className="lg-field__label">{item.label}</span>
        <input
          className="lg-input"
          type="text"
          value={typeof value === "string" ? value : String(value ?? "")}
          onChange={(e) => props.onChange(e.target.value)}
        />
      </div>
    );
  }
  if (item.type === "color") {
    return (
      <ColorRow
        label={item.label}
        value={sceneColorToHex(value)}
        presets={[]}
        // 参照值带上：WE 里 tint 用 0~1、粒子色用 0~255，回写必须按原刻度，否则语义翻转
        onChange={(hex) => props.onChange(hexToSceneColor(hex, value))}
      />
    );
  }
  const current = Number.isFinite(Number(value)) ? Number(value) : Number(item.default) || 0;
  const lo = Number.isFinite(item.min) ? Math.min(Number(item.min), current) : Math.min(0, current);
  const hi = Number.isFinite(item.max) ? Math.max(Number(item.max), current) : Math.max(1, current);
  const step = Number(item.step) > 0 ? Number(item.step) : 1;
  return (
    <RangeRow
      label={item.label}
      value={current}
      min={lo}
      max={hi}
      step={step}
      onChange={(v) => props.onChange(roundSceneNumber(item, v))}
    />
  );
}

/**
 * 当前壁纸在 Wallpaper Engine 里的预设面板。
 *
 * 两道收窄缺一不可：`items` 来自作品 project.json（作者留下的全部属性，含渲染器不消费的），
 * `bindings` 来自解码清单的 userBindings（改了真的会改变画面的键）。只摆交集——
 * 用户改每一项都能立刻在背景上看到效果，这才是「选项」应然的样子。
 */
export function ScenePresetPanel(props: {
  wallpaper: LiquidGlassWallpaper;
  onChange: (preset: ScenePropertyValues) => void;
}) {
  const source = liquidGlassSceneSource(props.wallpaper);
  const bindings = useSceneBindings(source);
  const [snapshot, setSnapshot] = useState<ScenePresetSnapshot | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!source) return undefined;
    let alive = true;
    setError("");
    loadScenePresets(source)
      .then((next) => { if (alive) setSnapshot(next); })
      .catch((err: unknown) => {
        if (!alive) return;
        setSnapshot(null);
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => { alive = false; };
  }, [source]);

  const deviations = props.wallpaper.preset || {};
  const values = snapshot ? scenePresetValues(snapshot.base, deviations) : deviations;
  const rows = filterScenePresetItems(snapshot?.items || [], bindings, values);

  const reload = () => {
    if (!source) return;
    setError("");
    loadScenePresets(source, { force: true })
      .then(setSnapshot)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  };

  const setRow = (item: ScenePropertyItem, next: ScenePropertyValue) => {
    props.onChange(scenePresetDeviation(deviations, item, next, snapshot?.base || {}));
  };

  return (
    <section className="lg-section lg-preset">
      <h4>壁纸预设 <span>{rows.length}</span></h4>
      <p className="lg-empty">
        这些是当前壁纸在 Wallpaper Engine 里的选项，初值取你在 WE 中的设置
        {Object.keys(deviations).length > 0 ? "，已含应用内的改动" : ""}。
      </p>
      {error && (
        <div className="lg-preset__error">
          <p className="lg-error">预设读取失败：{error}</p>
          <button type="button" className="lg-btn lg-btn--tiny" onClick={reload}>
            <RefreshCw size={13} />重试
          </button>
        </div>
      )}
      {!error && !snapshot && <p className="lg-empty">正在读取该作品的预设…</p>}
      {!error && snapshot && bindings === null && (
        <p className="lg-empty">场景实时渲染就绪后，这里只显示这份作品真正能改的选项。</p>
      )}
      {!error && snapshot && bindings !== null && rows.length === 0 && (
        <p className="lg-empty">
          {snapshot.items.length === 0
            ? "这份作品没有自带可调整的预设。"
            : "这份作品的预设项当前渲染器都不消费（文本 / 音频 / 鼠标特效等），改动不会反映到画面上。"}
        </p>
      )}
      {rows.map((item) => (
        <ScenePresetRow
          key={item.key}
          item={item}
          value={values[item.key] ?? item.value}
          onChange={(v) => setRow(item, v)}
        />
      ))}
      {Object.keys(deviations).length > 0 && (
        <div className="lg-btn-row">
          <button type="button" className="lg-btn lg-btn--tiny" onClick={() => props.onChange({})}>
            <RotateCcw size={13} />还原 WE 原样
          </button>
        </div>
      )}
    </section>
  );
}

function parentDir(path: string): string {
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return index > 0 ? path.slice(0, index) : "";
}

/** 旧存档没有 preview 字段：按工坊标准文件名猜一次，猜错由 onError 回落到抽帧缩略图。 */
function workshopPreviewGuess(path: string): string {
  const match = /^(.*[\\/]steamapps[\\/]workshop[\\/]content[\\/]431960[\\/][^\\/]+)[\\/][^\\/]+$/i.exec(path);
  return match ? `${match[1]}\\preview.gif` : "";
}

/** 列表封面候选：工坊动图封面 → 按工坊路径猜 preview.gif → 后端抽帧缩略图（5s 处，避开黑场首帧）→ 原图。 */
function wallpaperPosterCandidates(item: LiquidGlassWallpaper): string[] {
  const list: string[] = [];
  if (item.preview) list.push(localViewUrl(item.preview));
  const guessed = workshopPreviewGuess(item.path);
  if (guessed && guessed !== item.path) list.push(localViewUrl(guessed));
  const dir = parentDir(item.path);
  if (dir) list.push(wallpaperThumbUrl(dir, item.path));
  if (item.kind === "image") list.push(localViewUrl(item.path));
  return Array.from(new Set(list));
}

function WallpaperThumb({ item, externalHover = false }: {
  item: LiquidGlassWallpaper;
  externalHover?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const [posterIndex, setPosterIndex] = useState(0);
  const [posterFailed, setPosterFailed] = useState(false);
  const [liveFailed, setLiveFailed] = useState(false);
  const candidates = wallpaperPosterCandidates(item);
  const poster = candidates[Math.min(posterIndex, candidates.length - 1)] || "";
  // 悬停才挂视频，避免一打开面板就同时解码多个 4K 视频（会拖垮当前壁纸加载）
  const live = (hover || externalHover) && item.kind === "video" && !liveFailed;

  useEffect(() => {
    setPosterIndex(0);
    setPosterFailed(false);
    setLiveFailed(false);
  }, [item.path, item.preview]);

  return (
    <span
      className="lg-thumb-box"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {poster && !posterFailed ? (
        <img
          className="lg-thumb"
          src={poster}
          alt=""
          loading="lazy"
          draggable={false}
          onError={() => {
            if (posterIndex < candidates.length - 1) setPosterIndex((i) => i + 1);
            else setPosterFailed(true);
          }}
        />
      ) : (
        <span className="lg-thumb lg-thumb--empty" aria-hidden="true"><Images size={15} /></span>
      )}
      {live && (
        <video
          className="lg-thumb lg-thumb--live"
          src={localViewUrl(item.path)}
          poster={poster || undefined}
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          onError={() => setLiveFailed(true)}
        />
      )}
    </span>
  );
}

export function LiquidGlassDock({
  lg,
  update,
}: {
  lg: LiquidGlassSettings;
  update: (patch: Partial<LiquidGlassSettings>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [libOpen, setLibOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("glass");
  const [pos, setPos] = useState(() => {
    const raw = Number(localStorage.getItem(ROPE_POS_KEY));
    return Number.isFinite(raw) && raw > 0 && raw < 100 ? raw : 52;
  });
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [storeInfo, setStoreInfo] = useState<WallpaperStoreInfo | null>(null);
  const [storeLoading, setStoreLoading] = useState(false);
  const [storeEditing, setStoreEditing] = useState(false);
  const [storeDraft, setStoreDraft] = useState("");
  const [storeSaving, setStoreSaving] = useState(false);
  const [storeError, setStoreError] = useState("");
  const [pickedName, setPickedName] = useState("");
  const [storeNonce, setStoreNonce] = useState(0);
  const [workshopInfo, setWorkshopInfo] = useState<WallpaperWorkshopPage | null>(null);
  const [hoveredWallpaper, setHoveredWallpaper] = useState<string | null>(null);
  const dragRef = useRef<{
    startY: number; startX: number; startPos: number; moved: boolean; left: number;
  } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const active = lg.wallpapers.find((w) => w.id === lg.activeWallpaperId) || null;

  useEffect(() => subscribeFloatingPanels("glass", () => setOpen(false)), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (libOpen) {
        setLibOpen(false);
        return;
      }
      if (storeEditing) {
        setStoreEditing(false);
        setStoreError("");
        return;
      }
      setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [libOpen, storeEditing]);

  useEffect(() => {
    if (tab !== "wallpaper") return;
    let alive = true;
    setStoreLoading(true);
    getWallpaperStore(lg.wallpaperStoreDir)
      .then((info) => {
        if (!alive) return;
        setStoreInfo(info);
        setStoreError("");
      })
      .catch((err) => {
        if (!alive) return;
        setStoreError(err instanceof Error ? err.message : "读取存储位置失败");
      })
      .finally(() => { if (alive) setStoreLoading(false); });
    return () => { alive = false; };
  }, [tab, lg.wallpaperStoreDir, storeNonce]);

  useEffect(() => {
    if (tab !== "wallpaper") return;
    let alive = true;
    scanWorkshopWallpapers({ pageSize: 1 })
      .then((res) => { if (alive) setWorkshopInfo(res); })
      .catch(() => { if (alive) setWorkshopInfo(null); });
    return () => { alive = false; };
  }, [tab]);

  useEffect(() => {
    const onResize = () => setPos((p) => Math.min(94, Math.max(6, p)));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const persistPos = (next: number) => {
    setPos(next);
    try { localStorage.setItem(ROPE_POS_KEY, String(next)); } catch { /* 隐私模式忽略 */ }
  };

  const onPointerDown = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = {
      startY: e.clientY, startX: e.clientX, startPos: pos, moved: false, left: 0,
    };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dy = e.clientY - drag.startY;
    const dx = drag.startX - e.clientX;
    drag.left = Math.max(0, dx);
    if (Math.abs(dy) > 3 || drag.left > 3) drag.moved = true;
    if (Math.abs(dy) > 2) {
      const next = Math.min(94, Math.max(6, drag.startPos + (dy / window.innerHeight) * 100));
      setPos(next);
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    dragRef.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* 已释放 */ }
    if (!drag) return;
    if (drag.left > 56) {
      persistPos(pos);
      setOpen(true);
      return;
    }
    if (!drag.moved) {
      setOpen((v) => !v);
      return;
    }
    persistPos(pos);
  };

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadError("");
    try {
      const result = await uploadWallpaper(file, lg.wallpaperStoreDir);
      const item: LiquidGlassWallpaper = {
        id: crypto.randomUUID(),
        path: result.path,
        name: result.name || file.name || (result.path.split(/[\\/]/).pop() || "壁纸"),
        kind: liquidGlassWallpaperKind(result.path),
      };
      const next = [...lg.wallpapers, item];
      update({ wallpapers: next, activeWallpaperId: item.id, wallpaperPaused: false });
      setPickedName(item.name);
      setStoreNonce((n) => n + 1);
      setTab("wallpaper");
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "上传失败，请检查后端是否在线");
    } finally {
      setUploading(false);
    }
  };

  const saveStoreDir = async () => {
    const nextDir = storeDraft.trim();
    setStoreSaving(true);
    setStoreError("");
    try {
      const result = await migrateWallpaperStore({
        fromDir: storeInfo?.dir || lg.wallpaperStoreDir,
        toDir: nextDir,
      });
      const lookup = new Map(
        Object.entries(result.mapping).map(([oldPath, newPath]) => [pathKey(oldPath), newPath]),
      );
      const nextWallpapers = lookup.size
        ? lg.wallpapers.map((item) => {
          const moved = lookup.get(pathKey(item.path));
          return moved ? { ...item, path: moved } : item;
        })
        : lg.wallpapers;
      update({ wallpapers: nextWallpapers, wallpaperStoreDir: nextDir });
      setStoreEditing(false);
      setPickedName("");
      setStoreNonce((n) => n + 1);
    } catch (err) {
      setStoreError(err instanceof Error ? err.message : "更改存储位置失败");
    } finally {
      setStoreSaving(false);
    }
  };

  const setActive = (id: string) => update({ activeWallpaperId: id, wallpaperPaused: false });

  const removeWallpaper = (id: string) => {
    const next = lg.wallpapers.filter((w) => w.id !== id);
    const nextActive = lg.activeWallpaperId === id
      ? (next[0]?.id || "")
      : lg.activeWallpaperId;
    update({ wallpapers: next, activeWallpaperId: nextActive });
  };

  const closeWallpaper = () => update({ activeWallpaperId: "", wallpaperPaused: false });
  const isVideo = active?.kind === "video";

  // 预设按壁纸各自存：切作品各用各的，删作品跟着走（持久化在 wallpapers[].preset）
  const updateWallpaperPreset = (id: string, preset: ScenePropertyValues) => {
    update({ wallpapers: lg.wallpapers.map((w) => (w.id === id ? { ...w, preset } : w)) });
  };

  if (!lg.ropeShown && !open) {
    return (
      <button
        type="button"
        className="lg-rope-mini"
        title="打开液态玻璃控制台"
        aria-label="打开液态玻璃控制台"
        onClick={() => setOpen(true)}
      >
        玻璃
      </button>
    );
  }

  return (
    <>
      {!open && (
        <div
          className="lg-rope"
          style={{ top: `${pos}%`, "--lg-rope-scale": lg.ropeScale } as React.CSSProperties}
          role="button"
          tabIndex={0}
          aria-label="液态玻璃控制台拉绳：点击或向左拉打开参数面板"
          title="液态玻璃控制台 · 点击或向左拉打开"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen(true);
            }
          }}
        >
          <span className="lg-rope__grip" aria-hidden="true" />
          <span className="lg-rope__art" aria-hidden="true">
            {lg.ropeForm === "icon"
              ? <img src="/demiurge-icon.png" alt="" draggable={false} />
              : <span className="lg-rope__orb" />}
          </span>
          <span className="lg-rope__hint">玻璃</span>
        </div>
      )}

      {/* 浮层必须挂到 body：桌面 .app-dock 是 position:fixed，Blink 视其为层叠上下文，
         面板留在里面时 z-index 再高（1260/1300）也会被输入区（.chat-input-wrap z-2）压住。 */}
      {open && createPortal(
        <>
          <div className="lg-dock-scrim" onClick={() => setOpen(false)} />
          <aside className="lg-panel" aria-label="液态玻璃控制台">
            <header className="lg-panel__head">
              <div>
                <h3>液态玻璃控制台</h3>
                <p>右侧拉绳 · 参数即时生效</p>
              </div>
              <button type="button" className="lg-icon-btn" onClick={() => setOpen(false)} aria-label="收起控制台">
                <X size={18} />
              </button>
            </header>

            <nav className="lg-tabs" role="tablist">
              {([
                ["glass", "玻璃"],
                ["wallpaper", "壁纸"],
                ["effects", "效果"],
                ["rope", "拉绳"],
              ] as [Tab, string][]).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={tab === id}
                  className={tab === id ? "active" : ""}
                  onClick={() => setTab(id)}
                >
                  {label}
                </button>
              ))}
            </nav>

            <div className="lg-panel__body">
              {tab === "glass" && (
                <>
                  <section className="lg-section">
                    <h4>配色</h4>
                    <ColorRow label="主色" value={lg.accent} presets={ACCENT_PRESETS} onChange={(v) => update({ accent: v })} />
                  </section>

                  <section className="lg-section">
                    <h4>玻璃</h4>
                    <ColorRow label="玻璃颜色" value={lg.glassColor} presets={GLASS_PRESETS} onChange={(v) => update({ glassColor: v })} />
                    <RangeRow label="玻璃透明度" value={lg.glassAlpha} min={0} max={60} step={5} suffix="%" hint="越大越透，越大越能看见壁纸" onChange={(v) => update({ glassAlpha: v })} />
                    <RangeRow label="玻璃模糊" value={lg.glassBlur} min={0} max={60} suffix="px" onChange={(v) => update({ glassBlur: v })} />
                    <RangeRow label="边框" value={lg.border} min={0} max={100} suffix="%" onChange={(v) => update({ border: v })} />
                    <SwitchRow label="设置窗口玻璃" hint="关闭后设置页恢复深色实底" checked={lg.glassWindow} onChange={(v) => update({ glassWindow: v })} />
                  </section>

                  <section className="lg-section">
                    <h4>侧栏</h4>
                    <SwitchRow label="侧栏玻璃" hint="关闭后左侧导航恢复实底" checked={lg.sidebarGlass} onChange={(v) => update({ sidebarGlass: v })} />
                    <ColorRow label="侧栏玻璃颜色" value={lg.sidebarColor} presets={SIDEBAR_PRESETS} onChange={(v) => update({ sidebarColor: v })} />
                    <RangeRow label="侧栏模糊" value={lg.sidebarBlur} min={0} max={60} suffix="px" onChange={(v) => update({ sidebarBlur: v })} />
                    <RangeRow label="侧栏透明度" value={lg.sidebarAlpha} min={0} max={200} step={10} suffix="%" hint="越大越透" onChange={(v) => update({ sidebarAlpha: v })} />
                  </section>

                  <section className="lg-section">
                    <h4>内容面</h4>
                    <ColorRow label="内容面颜色" value={lg.contentColor} presets={CONTENT_PRESETS} allowEmpty onChange={(v) => update({ contentColor: v })} />
                    <RangeRow label="内容面透明度" value={lg.contentAlpha} min={0} max={80} suffix="%" hint="越大越透，下限保留 20% 底色保证可读" onChange={(v) => update({ contentAlpha: v })} />
                  </section>
                </>
              )}

              {tab === "wallpaper" && (
                <>
                  <section className="lg-section">
                    <h4>壁纸库</h4>
                    <p className="lg-empty">
                      {lg.wallpaperDir
                        ? <>存放地址：<code className="lg-path">{lg.wallpaperDir}</code></>
                        : "填写壁纸存放地址后，可按文件夹浏览全部图片/视频壁纸。"}
                    </p>
                    <div className="lg-btn-row">
                      <button
                        type="button"
                        className="lg-btn lg-btn--primary"
                        onClick={() => setLibOpen(true)}
                      >
                        <Images size={15} />打开壁纸库
                      </button>
                    </div>
                    {workshopInfo && (
                      <p className="lg-empty">
                        {workshopInfo.found ? (
                          <>
                            Wallpaper Engine：已识别 <b>{workshopInfo.total}</b> 个壁纸
                            <code className="lg-path" title={workshopInfo.roots.join("\n")}>
                              {workshopInfo.roots[0]}
                            </code>
                          </>
                        ) : (
                          "未检测到 Steam 创意工坊壁纸（需已安装 Wallpaper Engine）。"
                        )}
                      </p>
                    )}
                  </section>

                  <section className="lg-section">
                    <h4>自定义壁纸</h4>
                    <div className="lg-store__row">
                      <span className="lg-store__label">存储位置</span>
                      {storeEditing ? (
                        <input
                          className="lg-store__input"
                          value={storeDraft}
                          autoFocus
                          spellCheck={false}
                          placeholder="例如 ~/Demiurge/wallpapers"
                          onChange={(e) => setStoreDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              void saveStoreDir();
                            }
                          }}
                        />
                      ) : (
                        <code className="lg-store__path" title={storeInfo?.dir || ""}>
                          {storeInfo?.dir || (storeLoading ? "读取中…" : "未读取到存储位置")}
                        </code>
                      )}
                      {!storeEditing && (
                        <button
                          type="button"
                          className="lg-btn"
                          onClick={() => {
                            setStoreDraft(lg.wallpaperStoreDir || storeInfo?.dir || "");
                            setStoreEditing(true);
                            setStoreError("");
                          }}
                        >
                          更改
                        </button>
                      )}
                    </div>
                    {storeEditing && (
                      <div className="lg-store__actions">
                        <button
                          type="button"
                          className="lg-btn lg-btn--primary"
                          onClick={() => void saveStoreDir()}
                          disabled={storeSaving}
                        >
                          {storeSaving ? "迁移中…" : "保存"}
                        </button>
                        <button
                          type="button"
                          className="lg-btn"
                          disabled={storeSaving}
                          onClick={() => { setStoreEditing(false); setStoreError(""); }}
                        >
                          取消
                        </button>
                      </div>
                    )}
                    <p className="lg-empty">
                      已有文件会迁移到新位置 · 支持 <code className="lg-store__code">~</code> 表示用户主目录
                    </p>
                    <div className="lg-store__row">
                      <span className="lg-store__label">自定义</span>
                      <button
                        type="button"
                        className="lg-btn lg-btn--primary"
                        onClick={() => fileRef.current?.click()}
                        disabled={uploading || storeSaving}
                      >
                        <ImagePlus size={15} />{uploading ? "上传中…" : "选择文件"}
                      </button>
                      <span className="lg-store__value" title={pickedName || undefined}>
                        {pickedName || "未选择文件"}
                      </span>
                    </div>
                    <input
                      ref={fileRef}
                      type="file"
                      accept=".jpg,.jpeg,.png,.mp4,image/jpeg,image/png,video/mp4"
                      style={{ display: "none" }}
                      onChange={onPickFile}
                    />
                    <p className="lg-empty">已上传 {storeInfo?.count ?? 0} 个 · 格式仅限 JPG / PNG / MP4</p>
                    {uploadError && <p className="lg-error">{uploadError}</p>}
                    {storeError && <p className="lg-error">{storeError}</p>}
                  </section>

                  <section className="lg-section">
                    <h4>当前壁纸</h4>
                    <div className="lg-current">
                      {active ? <WallpaperThumb item={active} /> : <div className="lg-current__empty">内置流彩</div>}
                      <div className="lg-current__meta">
                        <strong>{active?.name || "未选择壁纸"}</strong>
                        <small>{active ? (active.scenePkg ? "场景壁纸 · 页面内实时渲染" : isVideo ? "视频壁纸" : "图片壁纸") : "使用内置流动极光"}</small>
                      </div>
                    </div>
                    {active?.lowRes && !active?.scenePkg && (
                      <p className="lg-empty lg-empty--warn">
                        低清预览：该作品只有自带的小尺寸预览图，浏览器渲染不了它的场景 / 网页，
                        全屏会发糊；原版效果请在 Wallpaper Engine 中查看。
                      </p>
                    )}
                    <div className="lg-btn-row">
                      <button
                        type="button"
                        className="lg-btn"
                        disabled={!isVideo}
                        onClick={() => update({ wallpaperPaused: !lg.wallpaperPaused })}
                      >
                        {lg.wallpaperPaused ? <Play size={15} /> : <Pause size={15} />}
                        {lg.wallpaperPaused ? "播放" : "暂停"}
                      </button>
                      <button
                        type="button"
                        className="lg-btn"
                        onClick={() => window.dispatchEvent(new Event(LIQUID_GLASS_REFRESH_EVENT))}
                      >
                        <RefreshCw size={15} />刷新
                      </button>
                      <button type="button" className="lg-btn" disabled={!active} onClick={closeWallpaper}>
                        <X size={15} />关闭
                      </button>
                    </div>
                  </section>

                  {active && liquidGlassSceneSource(active) && (
                    <ScenePresetPanel
                      key={active.id}
                      wallpaper={active}
                      onChange={(preset) => updateWallpaperPreset(active.id, preset)}
                    />
                  )}

                  <section className="lg-section">
                    <h4>壁纸列表 <span>{lg.wallpapers.length}</span></h4>
                    {lg.wallpapers.length === 0 ? (
                      <p className="lg-empty">还没有壁纸。导入图片或视频后，会出现在这里，可随时切换。</p>
                    ) : (
                      <p className="lg-empty">封面优先用作品自带的动态预览图；视频项悬停可播放。</p>
                    )}
                    <div className="lg-wallpaper-list">
                      {lg.wallpapers.map((item) => (
                        <div
                          key={item.id}
                          className={`lg-wallpaper${item.id === lg.activeWallpaperId ? " active" : ""}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => setActive(item.id)}
                          onMouseEnter={() => setHoveredWallpaper(item.id)}
                          onMouseLeave={() => setHoveredWallpaper(null)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setActive(item.id);
                            }
                          }}
                        >
                          <WallpaperThumb item={item} externalHover={hoveredWallpaper === item.id} />
                          <span className="lg-wallpaper__name">{item.name}</span>
                          <span className="lg-wallpaper__kind">
                            {item.scenePkg ? "实时渲染" : item.lowRes ? "低清预览" : item.kind === "video" ? "视频" : "图片"}
                          </span>
                          <button
                            type="button"
                            className="lg-icon-btn lg-icon-btn--danger"
                            aria-label={`移除 ${item.name}`}
                            onClick={(e) => { e.stopPropagation(); removeWallpaper(item.id); }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </section>

                  <section className="lg-section">
                    <h4>自动轮换</h4>
                    <SwitchRow
                      label="按间隔轮换壁纸"
                      hint={lg.wallpapers.length < 2 ? "至少需要两张壁纸" : `共 ${lg.wallpapers.length} 张，循环播放`}
                      checked={lg.rotationEnabled}
                      onChange={(v) => update({ rotationEnabled: v })}
                    />
                    <RangeRow label="轮换间隔" value={lg.rotationInterval} min={10} max={300} step={5} suffix=" 秒" onChange={(v) => update({ rotationInterval: v })} />
                  </section>
                </>
              )}

              {tab === "effects" && (
                <>
                  <section className="lg-section">
                    <h4>壁纸</h4>
                    <RangeRow label="暗化" value={lg.scrim} min={0} max={100} suffix="%" hint="提高暗化可压低花哨壁纸，保证文字可读；实时渲染场景按作品原样，不受此项影响" onChange={(v) => update({ scrim: v })} />
                    <RangeRow label="模糊" value={lg.wallpaperBlur} min={0} max={60} suffix="px" onChange={(v) => update({ wallpaperBlur: v })} />
                    <RangeRow label="亮度" value={lg.wallpaperBrightness} min={20} max={160} suffix="%" onChange={(v) => update({ wallpaperBrightness: v })} />
                    <RangeRow label="对比度" value={lg.wallpaperContrast} min={20} max={160} suffix="%" onChange={(v) => update({ wallpaperContrast: v })} />
                    <RangeRow label="饱和度" value={lg.wallpaperSaturate} min={0} max={200} suffix="%" onChange={(v) => update({ wallpaperSaturate: v })} />
                    <SwitchRow label="水平翻转" checked={lg.flip} onChange={(v) => update({ flip: v })} />
                    <div className="lg-field">
                      <span className="lg-field__label">填充方式</span>
                      <div className="lg-segmented">
                        {([
                          ["cover", "覆盖"],
                          ["contain", "完整"],
                          ["center", "居中"],
                          ["fill", "拉伸"],
                        ] as const).map(([value, label]) => (
                          <button
                            key={value}
                            type="button"
                            className={lg.wallpaperFit === value ? "active" : ""}
                            onClick={() => update({ wallpaperFit: value })}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </section>

                  <section className="lg-section">
                    <h4>视频播放</h4>
                    <RangeRow label="播放倍速" value={lg.playbackRate} min={0.5} max={2} step={0.1} suffix="×" onChange={(v) => update({ playbackRate: v })} />
                    <SwitchRow label="最小化 / 切标签页时暂停" checked={lg.pauseOnHidden} onChange={(v) => update({ pauseOnHidden: v })} />
                    <SwitchRow label="窗口失焦时暂停" hint="切到其它应用时停止解码，省电" checked={lg.pauseOnBlur} onChange={(v) => update({ pauseOnBlur: v })} />
                    <SwitchRow label="电池供电时暂停" hint="浏览器不支持电池 API 时自动忽略" checked={lg.pauseOnBattery} onChange={(v) => update({ pauseOnBattery: v })} />
                  </section>
                </>
              )}

              {tab === "rope" && (
                <>
                  <section className="lg-section">
                    <h4>吉祥物拉绳</h4>
                    <SwitchRow label="显示吉祥物拉绳" hint="关闭后右缘只保留一条细窄入口，仍可打开控制台" checked={lg.ropeShown} onChange={(v) => update({ ropeShown: v })} />
                    <div className="lg-field">
                      <span className="lg-field__label">形态</span>
                      <div className="lg-segmented">
                        {([
                          ["icon", "徽记"],
                          ["orb", "光球"],
                        ] as [LiquidGlassRopeForm, string][]).map(([value, label]) => (
                          <button
                            key={value}
                            type="button"
                            className={lg.ropeForm === value ? "active" : ""}
                            onClick={() => update({ ropeForm: value })}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                    <RangeRow label="大小" value={lg.ropeScale} min={0.5} max={2.5} step={0.05} suffix="×" onChange={(v) => update({ ropeScale: v })} />
                  </section>

                  <section className="lg-section">
                    <h4>操作</h4>
                    <p className="lg-empty">
                      上下拖动拉绳可移动位置；向左拉或点击打开此面板；Esc 收起。
                      面板关闭后，参数立即生效。
                    </p>
                    <button
                      type="button"
                      className="lg-btn"
                      onClick={() => update({ ...DEFAULT_LIQUID_GLASS, wallpapers: lg.wallpapers, activeWallpaperId: lg.activeWallpaperId })}
                    >
                      恢复默认外观（保留壁纸）
                    </button>
                  </section>
                </>
              )}
            </div>
          </aside>
        </>,
        document.body,
      )}

      {libOpen && createPortal(
        <LiquidGlassWallpaperLibrary
          lg={lg}
          update={update}
          onClose={() => setLibOpen(false)}
        />,
        document.body,
      )}
    </>
  );
}
