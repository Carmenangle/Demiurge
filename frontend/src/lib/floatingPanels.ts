export type FloatingPanelId = "support" | "quick-tools";

const EVENT_NAME = "laf-floating-panel-open";

export function announceFloatingPanel(panel: FloatingPanelId): void {
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: panel }));
}

export function subscribeFloatingPanels(
  current: FloatingPanelId,
  close: () => void,
): () => void {
  const listener = (event: Event) => {
    if ((event as CustomEvent<FloatingPanelId>).detail !== current) close();
  };
  window.addEventListener(EVENT_NAME, listener);
  return () => window.removeEventListener(EVENT_NAME, listener);
}
