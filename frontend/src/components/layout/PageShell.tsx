import type { ReactNode } from "react";

// 统一页面外壳：所有一级页面复用，保证页头/工具栏/内容区结构一致。
// - title：页面主标题
// - back：可选返回回调（有则渲染「← 返回」）
// - actions：页头右侧操作区（按钮组等）
// - toolbar：标题下方的工具栏（搜索/筛选/tab），可选
// - children：内容区
export function PageShell({
  title,
  back,
  actions,
  toolbar,
  children,
}: {
  title: ReactNode;
  back?: () => void;
  actions?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="page">
      <div className="page-head">
        <div className="page-title-row">
          {back && <button className="back-btn" onClick={back}>← 返回</button>}
          <h1>{title}</h1>
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
      {toolbar && <div className="page-toolbar">{toolbar}</div>}
      {children}
    </div>
  );
}

// 统一空态：图标 + 文案，各页复用（原来各页各写 empty-state）。
export function EmptyState({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="empty-state">
      {icon}
      <p style={{ margin: 0 }}>{children}</p>
    </div>
  );
}

// 统一加载/错误占位（简单文案型，复杂页可自定义）。
export function StateHint({ kind, children }: { kind?: "loading" | "error"; children: ReactNode }) {
  const color = kind === "error" ? "#d9534f" : "var(--text-muted)";
  return <p style={{ color, fontSize: 13, textAlign: "center", padding: "20px 0" }}>{children}</p>;
}
