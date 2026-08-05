// 编程模式（作品域）：脚本/插件功能。对应 Harness「破 Doom Loop」在编程场景的落点。
// 骨架版仅占位。
export function CodeMode({ workId }: { workId: string | null }) {
  return (
    <div className="code" data-work={workId ?? undefined}>
      <section className="code-panel">
        <h3>编程模式</h3>
        <small>脚本 / 插件功能（占位）</small>
      </section>
    </div>
  );
}
