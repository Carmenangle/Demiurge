// 对话附件（原 api/ai.ts 附件段，2026-10-24 按资源域拆分）：file_id 真源；文件字节在会话级附件存储，前端只持元信息。
import { apiUpload, apiUrl } from "../client";

// 对话附件元信息（file_id 真源；文件字节在会话级附件存储，前端只持元信息）。
export interface FileAttachmentMeta {
  fileId: string;
  name: string;
  mime: string;
  size: number;
}

// 上传文件为对话附件（会话级存储），返回元信息供渲染卡片与随消息透传。
// 后端响应是 snake_case {file_id,...}，这里显式映射为前端 camelCase——apiUpload 原样透传，
// 不映射会导致 meta.fileId === undefined，占位卡替换后渲染 `fileId.startsWith` 崩溃黑屏（2026-09-01 实锤）。
export async function uploadAttachment(threadId: string, file: File): Promise<FileAttachmentMeta> {
  const form = new FormData();
  form.append("thread_id", threadId);
  form.append("file", file);
  const res = await apiUpload<{ ok: boolean; file_id: string; name: string; mime: string; size: number }>(
    "/attachments/upload",
    form,
    120_000,
  );
  return { fileId: res.file_id, name: res.name, mime: res.mime, size: res.size };
}

// 附件下载 URL（历史回放只读卡片点击下载 / 媒体栏预览）。
export function attachmentUrl(fileId: string): string {
  return apiUrl(`/attachments/${encodeURIComponent(fileId)}`);
}
