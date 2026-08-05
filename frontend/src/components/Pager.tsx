import { ChevronLeft, ChevronRight } from "lucide-react";

// 通用分页条：仓库网格、资产库共用。页码从 1 开始。
export function Pager({
  page,
  pageCount,
  onPage,
  always = false,  // 只有一页时也显示（按钮自然禁用），资产库要求页码常驻
}: {
  page: number;
  pageCount: number;
  onPage: (p: number) => void;
  always?: boolean;
}) {
  if (pageCount <= 1 && !always) return null;
  // 页码窗口：当前页两侧各 2 个，首尾始终显示，省略号补齐
  const nums: (number | "…")[] = [];
  const push = (n: number) => nums.push(n);
  const lo = Math.max(2, page - 2);
  const hi = Math.min(pageCount - 1, page + 2);
  push(1);
  if (lo > 2) nums.push("…");
  for (let i = lo; i <= hi; i++) push(i);
  if (hi < pageCount - 1) nums.push("…");
  if (pageCount > 1) push(pageCount);

  return (
    <div className="pager">
      <button className="page-btn" disabled={page <= 1} onClick={() => onPage(page - 1)} title="上一页">
        <ChevronLeft size={16} />
      </button>
      {nums.map((n, i) =>
        n === "…" ? (
          <span key={`e${i}`} className="page-info">…</span>
        ) : (
          <button
            key={n}
            className={`page-btn${n === page ? " active" : ""}`}
            onClick={() => onPage(n)}
          >
            {n}
          </button>
        ),
      )}
      <button className="page-btn" disabled={page >= pageCount} onClick={() => onPage(page + 1)} title="下一页">
        <ChevronRight size={16} />
      </button>
    </div>
  );
}
