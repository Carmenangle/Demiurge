import { useEffect, useState } from "react";

// 全屏看图：点击任意缩略图触发 'lightbox' 事件，由根部的 Lightbox 监听显示。
export function openLightbox(url: string) {
  window.dispatchEvent(new CustomEvent("lightbox", { detail: url }));
}

export function Lightbox() {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const h = (e: Event) => setUrl((e as CustomEvent).detail as string);
    window.addEventListener("lightbox", h);
    return () => window.removeEventListener("lightbox", h);
  }, []);
  useEffect(() => {
    if (!url) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setUrl(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [url]);
  if (!url) return null;
  return (
    <div className="lightbox-mask" onClick={() => setUrl(null)}>
      <img src={url} alt="大图" className="lightbox-img" onClick={(e) => e.stopPropagation()} />
    </div>
  );
}
