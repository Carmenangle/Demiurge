import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";

// 通用分页条：仓库网格、资产库共用。页码从 1 开始。
export function Pager({
  page,
  pageCount,
  onPage,
  always = false,  // 只有一页时也显示（按钮自然禁用），资产库要求页码常驻
  onFirst,        // 「首页」；不传则不渲染该按钮（调用方自行决定能不能跳）
  onLast,         // 「末页」；同上
}: {
  page: number;
  pageCount: number;
  onPage: (p: number) => void;
  always?: boolean;
  onFirst?: () => void;
  onLast?: () => void;
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

  // 页码输入框：回车/失焦提交。本地态 ⇒ 输入中途不打扰父组件，
  // 也避免每次击键都把整页网格重渲染一遍。
  const [draft, setDraft] = useState("");
  useEffect(() => { setDraft(""); }, [page]);
  const commit = () => {
    const n = Math.floor(Number(draft));
    if (!Number.isFinite(n) || n < 1) { setDraft(""); return; }
    setDraft("");
    onPage(Math.min(pageCount, n));
  };

  return (
    <div className="pager">
      {onFirst && (
        <button className="page-btn" disabled={page <= 1} onClick={onFirst} title="首页">
          <ChevronsLeft size={16} />
        </button>
      )}
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
      {onLast && (
        <button className="page-btn" disabled={page >= pageCount} onClick={onLast} title="末页">
          <ChevronsRight size={16} />
        </button>
      )}
      <input
        className="page-jump"
        type="text"
        inputMode="numeric"
        placeholder={`第 ${page} / ${pageCount} 页`}
        value={draft}
        onChange={(e) => setDraft(e.target.value.replace(/[^\d]/g, ""))}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
        onBlur={commit}
        title="输入页码后回车跳转"
        aria-label="跳转到页码"
      />
    </div>
  );
}
