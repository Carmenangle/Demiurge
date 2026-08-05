import { useEffect, useRef, useState } from "react";
import { Ratio } from "lucide-react";
import {
  ASPECTS, CUSTOM_SIZE_MAX, CUSTOM_SIZE_MIN, IMAGE_QUALITIES, IMAGE_SIZE_STEP, RES_TIERS,
  resolveImageSize, type ImageQuality,
} from "../../lib/viewRouting";

function useDismissibleMenu() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside, true);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside, true);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return { open, setOpen, rootRef };
}

// 对话框模型切换器：一个图标按钮，hover 显示当前模型名，点击弹小菜单切换。
// items 为 {id,name} 列表；空列表时禁用并提示去设置配置。
export function ModelSwitcher({
  icon,
  label,
  items,
  activeId,
  emptyHint,
  onPick,
}: {
  icon: React.ReactNode;
  label: string;
  items: { id: string; name: string }[];
  activeId: string;
  emptyHint: string;
  onPick: (id: string) => void;
}) {
  const { open, setOpen, rootRef } = useDismissibleMenu();
  const current = items.find((i) => i.id === activeId) || items[0];
  const title = current ? `${label}：${current.name}` : emptyHint;
  return (
    <div ref={rootRef} className="model-switch" style={{ position: "relative" }}>
      <button
        type="button"
        className="icon-btn model-switch-btn"
        title={title}
        onClick={() => items.length && setOpen((v) => !v)}
        disabled={items.length === 0}
      >
        {icon}
      </button>
      {open && items.length > 0 && (
        <div className="model-switch-menu">
          <div className="model-switch-head">{label}</div>
          {items.map((i) => (
            <button
              key={i.id}
              type="button"
              className={`model-switch-item ${i.id === (current?.id || "") ? "active" : ""}`}
              onClick={() => { onPick(i.id); setOpen(false); }}
            >
              {i.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// 生图尺寸选择器：比例 + 分辨率档，样式对齐 ModelSwitcher。
export function SizeSwitcher({
  aspect, resTier, quality, qualitySupported,
  customEnabled, customWidth, customHeight, customSizeSupported,
  onPick, onCustomChange,
}: {
  aspect: string;
  resTier: string;
  quality: ImageQuality;
  qualitySupported: boolean;
  customEnabled: boolean;
  customWidth: number;
  customHeight: number;
  customSizeSupported: boolean;
  onPick: (aspect: string, resTier: string, quality: ImageQuality) => void;
  onCustomChange: (enabled: boolean, width: number, height: number) => void;
}) {
  const { open, setOpen, rootRef } = useDismissibleMenu();
  const [widthText, setWidthText] = useState(String(customWidth));
  const [heightText, setHeightText] = useState(String(customHeight));
  useEffect(() => setWidthText(String(customWidth)), [customWidth]);
  useEffect(() => setHeightText(String(customHeight)), [customHeight]);
  const qualityText = qualitySupported ? IMAGE_QUALITIES[quality] : "当前模型不发送";
  const resolved = resolveImageSize(
    aspect, resTier, customEnabled, customWidth, customHeight, customSizeSupported,
  );
  const commitCustom = () => onCustomChange(true, Number(widthText), Number(heightText));
  return (
    <div ref={rootRef} className="model-switch" style={{ position: "relative" }}>
      <button
        type="button"
        className="icon-btn model-switch-btn"
        title={`生图尺寸：${customEnabled ? `自定义 ${customWidth}x${customHeight}` : `${aspect} · ${resTier}`}（发送 ${resolved.size}）· 质量：${qualityText}`}
        onClick={() => setOpen((v) => !v)}
      >
        <Ratio size={18} />
      </button>
      {open && (
        <div className="model-switch-menu">
          <div className="model-switch-head">画面比例</div>
          {ASPECTS.map((a) => (
            <button
              key={a}
              type="button"
              className={`model-switch-item ${!customEnabled && a === aspect ? "active" : ""}`}
              onClick={() => { onCustomChange(false, customWidth, customHeight); onPick(a, resTier, quality); }}
            >
              {a}
            </button>
          ))}
          <button
            type="button"
            className={`model-switch-item ${customEnabled ? "active" : ""}`}
            onClick={() => onCustomChange(true, customWidth, customHeight)}
          >
            自定义尺寸
          </button>
          {customEnabled ? (
            <>
              <div className="custom-size-inputs">
                <input
                  type="number"
                  min={CUSTOM_SIZE_MIN}
                  max={CUSTOM_SIZE_MAX}
                  step={IMAGE_SIZE_STEP}
                  value={widthText}
                  aria-label="自定义图片宽度"
                  onChange={(event) => setWidthText(event.target.value)}
                  onBlur={commitCustom}
                />
                <span>×</span>
                <input
                  type="number"
                  min={CUSTOM_SIZE_MIN}
                  max={CUSTOM_SIZE_MAX}
                  step={IMAGE_SIZE_STEP}
                  value={heightText}
                  aria-label="自定义图片高度"
                  onChange={(event) => setHeightText(event.target.value)}
                  onBlur={commitCustom}
                />
              </div>
              <div className="model-switch-note">
                {customSizeSupported
                  ? `直接发送 ${resolved.size}`
                  : `当前上游未声明任意尺寸，将按最近的 ${resolved.aspect} · ${resolved.resTier} 发送 ${resolved.size}`}
              </div>
            </>
          ) : (
            <>
              <div className="model-switch-head" style={{ marginTop: 6 }}>分辨率</div>
              {Object.keys(RES_TIERS).map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`model-switch-item ${t === resTier ? "active" : ""}`}
                  onClick={() => onPick(aspect, t, quality)}
                >
                  {t}
                </button>
              ))}
            </>
          )}
          <div className="model-switch-head" style={{ marginTop: 6 }}>生成质量</div>
          {Object.entries(IMAGE_QUALITIES).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`model-switch-item ${value === quality ? "active" : ""}`}
              disabled={!qualitySupported}
              onClick={() => onPick(aspect, resTier, value as ImageQuality)}
            >
              {label}
            </button>
          ))}
          {!qualitySupported && (
            <div className="model-switch-note">当前模型不支持，发送请求时会省略 quality</div>
          )}
        </div>
      )}
    </div>
  );
}
