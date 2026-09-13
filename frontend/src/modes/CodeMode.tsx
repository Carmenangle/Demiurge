// 编辑模式（作品域）：角色卡、作品脚本与排错。当前实际工作区复用 ChatView。
export function CodeMode({ workId }: { workId: string | null }) {
  return (
    <div className="code" data-work={workId ?? undefined}>
      <section className="code-panel">
        <h3>编辑模式</h3>
        <small>角色卡 / 作品脚本 / 排错</small>
      </section>
    </div>
  );
}
