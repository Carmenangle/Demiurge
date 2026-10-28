/** 手机端专用：ComfyUI 原生画布（节点面板 / AI 搭工作流）的收口占位。
 *
 *  画布是 iframe 直连 ComfyUI 页面：手机上无论联机同库（地址是电脑回环，指向手机自己）
 *  还是云端地址（后端 CSP frame-src 只放行回环），WebView 里都只能得到白屏。
 *  故手机端不再渲染死 iframe，改为说清能力边界——生成本身全走本机后端后转，不受影响。
 *  PC 构建不引用本组件。 */
export function MobileCanvasNotice(props: {
  title: string;
  hint?: string;
  /** 传入即在说明底部给一颗主按钮（手机上「返回对话」），关闭承载它的抽屉/面板。 */
  onClose?: () => void;
  closeLabel?: string;
}) {
  return (
    <div
      className="mobile-canvas-notice"
      style={{
        border: "1px solid var(--border, #2a2a32)",
        borderRadius: 10,
        padding: "16px 18px",
        lineHeight: 1.75,
        fontSize: 13,
        color: "var(--text)",
        background: "var(--panel, transparent)",
      }}
    >
      <strong style={{ display: "block", fontSize: 15, marginBottom: 8 }}>{props.title}</strong>
      <p style={{ margin: "0 0 8px", color: "var(--text-muted)" }}>
        {props.hint || "ComfyUI 原生画布需要页面直连 ComfyUI，手机上（联机同库 / 云端地址）都连不到，故此处不提供画布。"}
      </p>
      <p style={{ margin: "0 0 8px" }}>
        生成任务仍然可用：手机点生成，工作流由电脑上的 ComfyUI 执行，进度和图片会回到手机会话。
      </p>
      <p style={{ margin: 0, color: "var(--text-muted)" }}>
        需要拖节点、拼工作流、选骨架，请在电脑上打开「AI 搭工作流」。
      </p>
      {props.onClose && (
        <button type="button" className="btn primary mobile-canvas-notice-back" onClick={props.onClose}>
          {props.closeLabel || "返回对话"}
        </button>
      )}
    </div>
  );
}
