import { apiGet } from "./client";

/** 仓库 docs/ 下的一篇 Markdown（只读接口 /api/docs/doc 返回体）。 */
export interface GuideDoc {
  /** 相对仓库根的 md 路径，如 docs/guide/workflow-template-import.md */
  path: string;
  /** 首个 # 标题；无则回退文件名 */
  title: string;
  content: string;
}

// 超时必带：本地读文件通常毫秒级，但挂起会让引导页永远停在「加载中」。
export function fetchGuideDoc(docPath: string): Promise<GuideDoc> {
  return apiGet<GuideDoc>(`/docs/doc?path=${encodeURIComponent(docPath)}`, 10_000);
}
