// 兼容桩（2026-10-17）：实现已迁到 MaterialMarketModalLive.tsx。
// 旧版 WebMaterialsView（被浏览器缓存的）仍 import "./MaterialMarketModal" ⇒ 这里 re-export，
// 保证无论加载到新旧哪版 WebMaterialsView，最终都用同一份最新实现（含全页原图下载）。
// 目的：绕开「浏览器把 MaterialMarketModal 模块缓存住、即使 dev server 已是新代码也不更新」的坑。
export { MaterialMarketModal } from "./MaterialMarketModalLive";
