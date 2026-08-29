// 对话框文件拖拽/选择：文本文件以「文件参考」语义块插入输入框，图片走既有图片栏。
// 零后端改动：附件内容随用户消息文本进入全部 agent 链路（剧情/对话/委派编译）。

export const FILE_ATTACH_MAX_CHARS = 100_000;

const TEXT_EXT_RE = /\.(md|txt|json|csv|log|ya?ml|xml|html?|ts|tsx|js|jsx|py|css|ini|toml|srt|vtt|ass)$/i;

export function isTextFile(file: { name: string; type: string }): boolean {
  if (file.type.startsWith("text/")) return true;
  if (file.type === "application/json") return true;
  return TEXT_EXT_RE.test(file.name);
}

export function buildFileAttachmentText(name: string, raw: string): string {
  const total = raw.length;
  const content = total > FILE_ATTACH_MAX_CHARS
    ? raw.slice(0, FILE_ATTACH_MAX_CHARS)
    : raw;
  const note = total > FILE_ATTACH_MAX_CHARS
    ? `（共 ${total} 字，已截断至前 ${FILE_ATTACH_MAX_CHARS} 字）`
    : `（共 ${total} 字）`;
  return `【文件参考：${name}】${note}\n${content}\n【文件参考结束：${name}】`;
}

export function readFileAsText(file: File): Promise<string> {
  return file.text();
}

export function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
