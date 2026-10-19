import { apiGet } from "./client";

/**
 * 后台活动「外部长操作」（2026-10-19）：模型/工作流下载、节点安装更新切内核、
 * LoRA 触发词同步、对话后置维护。
 *
 * 后端只做只读投影（`services/activity_feed.py`），真源仍在各子系统自己的进度快照里。
 * `title` 由后端按 KIND_LABEL 下发，前端不维护第二份 kind→标题映射。
 */
export interface BackgroundActivityItem {
  id: string;
  kind: string;
  title: string;
  label: string;
  status: "running" | "done" | "failed" | "cancelled" | string;
  progress: string;
  detail?: string;
  error?: string;
  /** 点击跳转的设置子视图 id（models / node-manager / lora-data），空=无跳转。 */
  target?: string;
  /** 关联仓库（对话后置维护用），前端据 localStorage 补显示名。 */
  repo_id?: string;
  started_at: number;
  updated_at: number;
}

// 轮询类接口必须带超时：后端挂起时否则 await 永不返回，轮询链会静默断掉（面板停在旧快照）。
const POLL_TIMEOUT_MS = 8000;

export function listBackgroundActivity() {
  return apiGet<{ items: BackgroundActivityItem[] }>("/activity/background", POLL_TIMEOUT_MS);
}
