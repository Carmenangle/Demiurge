import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

function useEsc(onCancel: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
}

import type { ReactNode } from "react";

interface ConfirmProps {
  title: string;
  message?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  busy?: boolean;
  closeOnBackdrop?: boolean;
  portal?: boolean;
  overlayClassName?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function shouldCancelConfirmFromBackdrop(closeOnBackdrop: boolean, busy: boolean): boolean {
  return closeOnBackdrop && !busy;
}

export function ConfirmModal({
  title, message, confirmText = "确认", cancelText = "取消", danger, busy = false,
  closeOnBackdrop = true, portal = false, overlayClassName = "", onConfirm, onCancel,
}: ConfirmProps) {
  useEsc(() => { if (!busy) onCancel(); });
  const content = (
    <div
      className={`modal-mask ${overlayClassName}`.trim()}
      onClick={() => { if (shouldCancelConfirmFromBackdrop(closeOnBackdrop, busy)) onCancel(); }}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {message && <p style={{ color: "#666", marginTop: 0 }}>{message}</p>}
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onCancel}>
            {cancelText}
          </button>
          <button
            className={`btn ${danger ? "danger" : "primary"}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "处理中…" : confirmText}
          </button>
        </div>
      </div>
    </div>
  );
  return portal && typeof document !== "undefined" ? createPortal(content, document.body) : content;
}

interface AlertProps {
  title: string;
  message?: string;
  /** 自定义动作按钮（可选）。给了就**替换**默认的「知道了」——用于「提示 + 一键跳转」类弹窗，
   *  如「未配置仓库文件夹 → 去配置」（2026-09-15 P15）。不传时行为与改前逐字一致。 */
  actions?: { label: string; onClick: () => void; primary?: boolean }[];
  onClose: () => void;
}

export function AlertModal({ title, message, actions, onClose }: AlertProps) {
  useEsc(onClose);
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {message && <p style={{ color: "#666", marginTop: 0 }}>{message}</p>}
        <div className="modal-actions">
          {actions && actions.length > 0 ? (
            actions.map((a) => (
              <button
                key={a.label}
                className={`btn ${a.primary ? "primary" : ""}`}
                autoFocus={a.primary}
                onClick={a.onClick}
              >
                {a.label}
              </button>
            ))
          ) : (
            <button className="btn primary" autoFocus onClick={onClose}>
              知道了
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

interface PromptProps {
  title: string;
  defaultValue?: string;
  confirmText?: string;
  /** 多行（textarea）：用于图像提示词这类长文本。多行下 Enter 换行、Ctrl/⌘+Enter 提交。 */
  multiline?: boolean;
  rows?: number;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function PromptModal({ title, defaultValue = "", confirmText = "确认", multiline = false, rows = 12, onConfirm, onCancel }: PromptProps) {
  const [value, setValue] = useState(defaultValue);
  useEsc(onCancel);
  const submit = () => { if (value.trim()) onConfirm(value.trim()); };
  return (
    <div className="modal-mask" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {multiline ? (
          <textarea
            autoFocus
            className="prompt-modal-textarea"
            rows={rows}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(); }}
          />
        ) : (
          <input
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        )}
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>
            取消
          </button>
          <button className="btn primary" disabled={!value.trim()} onClick={submit}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
