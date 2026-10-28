import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 720px), (pointer: coarse) and (hover: none) and (max-width: 1024px)";

/** 与 styles.css 手机断点同一口径：窄屏或触屏且不悬浮。 */
export function useMobileViewport(): boolean {
  const [mobile, setMobile] = useState(
    () => typeof window !== "undefined"
      && typeof window.matchMedia === "function"
      && window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setMobile(query.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);

  return mobile;
}
