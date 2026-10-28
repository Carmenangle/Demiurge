// 手机键盘探测：Android WebView / Chrome 弹键盘时 visualViewport 高度塌陷，
// 给 <html> 打 data-kb-open，CSS 据此收起底栏与会话头，让输入框贴住键盘。
// 阈值 120px 排除浏览器地址栏伸缩与横竖屏切换的小幅变化。
export function watchMobileKeyboard(): () => void {
  const viewport = window.visualViewport;
  if (!viewport) return () => {};
  const root = document.documentElement;
  const sync = () => {
    const occluded = window.innerHeight - viewport.height;
    root.toggleAttribute("data-kb-open", occluded > 120);
  };
  viewport.addEventListener("resize", sync);
  viewport.addEventListener("scroll", sync);
  sync();
  return () => {
    viewport.removeEventListener("resize", sync);
    viewport.removeEventListener("scroll", sync);
    root.removeAttribute("data-kb-open");
  };
}
