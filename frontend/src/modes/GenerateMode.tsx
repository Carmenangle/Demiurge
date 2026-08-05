// 多元数据生成（作品域）：资产驱动、主动生成——脱离剧情主线，针对角色/场景手动批量
// 出图/视频/gif（做一致性参考图、场景变体）。产物进资产库。骨架版仅占位。
export function GenerateMode({ workId }: { workId: string | null }) {
  return (
    <div className="generate" data-work={workId ?? undefined}>
      <section className="generate-panel">
        <h3>多元数据生成</h3>
        <small>手动批量出图/视频/gif（占位）；产物入资产库</small>
      </section>
    </div>
  );
}
