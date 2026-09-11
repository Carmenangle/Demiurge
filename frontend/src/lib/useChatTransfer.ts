import { useRef, type ChangeEvent } from "react";
import { exportSnapshot, importSnapshot } from "../api/ai";

export function useChatTransfer(
  threadId: string,
  repoName: string,
  reload: () => Promise<void>,
  report: (message: string) => void,
) {
  const snapshotFileRef = useRef<HTMLInputElement>(null);
  const exportChat = async () => {
    try {
      const data = await exportSnapshot(threadId);
      const blob = new Blob([JSON.stringify(data.messages ?? [], null, 2)], { type: "application/json" });
      const anchor = document.createElement("a");
      anchor.href = URL.createObjectURL(blob);
      anchor.download = `${repoName || "会话"}-${threadId}.json`;
      anchor.click();
      URL.revokeObjectURL(anchor.href);
    } catch { report("导出会话失败，请重试。"); }
  };
  const importChat = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const messages = Array.isArray(parsed) ? parsed : parsed?.messages;
      if (!Array.isArray(messages)) { report("导入失败：文件格式不是会话记录数组。"); return; }
      await importSnapshot(threadId, messages, true);
      await reload();
    } catch { report("导入会话失败：文件无法解析或写入失败。"); }
  };
  return { snapshotFileRef, handleExportChat: exportChat, handleImportChatFile: importChat };
}
