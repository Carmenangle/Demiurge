import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  stack: string;
}

// 全局渲染期错误边界：任何组件渲染抛错 → 显示可见错误卡片（含组件栈），
// 替代 React 默认的「整树卸载 = 白屏/黑屏」，让错误可截图取证、可一键恢复。
// 2026-09-01 对话附件黑屏事故后补的基础设施（此前全项目无 ErrorBoundary）。
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, stack: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 保留组件栈（React 16+ 渲染期错误默认会向上抛，此处仅记录，不吞错误）
    this.setState({ stack: info.componentStack || "" });
    console.error("[ErrorBoundary] 渲染期错误：", error, info.componentStack);
  }

  private handleReload = () => {
    window.location.reload();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    const firstStackLine = (this.state.stack.split("\n").filter((l) => l.trim()).slice(0, 3) || []).join("\n");
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          background: "#f5f6f8",
          fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
          color: "#1f2328",
        }}
      >
        <div
          style={{
            maxWidth: 720,
            width: "100%",
            background: "#fff",
            border: "1px solid #e5e7eb",
            borderRadius: 12,
            padding: 24,
            boxShadow: "0 8px 24px rgba(0,0,0,0.08)",
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8, color: "#c0392b" }}>
            ⚠️ 界面渲染出错（已降级，不再黑屏）
          </div>
          <div style={{ fontSize: 13, color: "#6b7280", marginBottom: 12 }}>
            这是 React 渲染期异常。请把下方错误信息截图发给开发者定位；也可先点「重新加载」恢复界面。
          </div>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              background: "#f8f9fa",
              border: "1px solid #eee",
              borderRadius: 8,
              padding: 12,
              fontSize: 12,
              lineHeight: 1.6,
              maxHeight: 240,
              overflow: "auto",
              marginBottom: 12,
            }}
          >
            {error.message || String(error)}
            {firstStackLine && `\n\n首次渲染位置：\n${firstStackLine}`}
          </pre>
          <details style={{ marginBottom: 16 }}>
            <summary style={{ fontSize: 13, color: "#4b5563", cursor: "pointer" }}>
              展开完整组件栈
            </summary>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                background: "#f8f9fa",
                border: "1px solid #eee",
                borderRadius: 8,
                padding: 12,
                fontSize: 11,
                lineHeight: 1.5,
                maxHeight: 320,
                overflow: "auto",
                marginTop: 8,
              }}
            >
              {this.state.stack || "（无组件栈信息）"}
            </pre>
          </details>
          <button
            type="button"
            onClick={this.handleReload}
            style={{
              padding: "8px 18px",
              borderRadius: 8,
              border: "none",
              background: "#2563eb",
              color: "#fff",
              fontSize: 14,
              cursor: "pointer",
            }}
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }
}
