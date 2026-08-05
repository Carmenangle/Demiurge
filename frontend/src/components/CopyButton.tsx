import { useState } from "react";
import { Check, Copy } from "lucide-react";

// 一键复制文本按钮：点后短暂显示「已复制」反馈。
export function CopyButton({
  text,
  className,
  label = "复制",
}: {
  text: string;
  className?: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用（非 https 等）时静默 */
    }
  };
  return (
    <button
      type="button"
      className={className}
      title={label}
      aria-label={label}
      onClick={copy}
      disabled={!text}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "已复制" : label}
    </button>
  );
}
