import { useEffect, useState } from "react";

function useEsc(onCancel: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
}

interface ConfirmProps {
  title: string;
  message?: string;
  confirmText?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmModal({
  title, message, confirmText = "确认", danger, busy = false, onConfirm, onCancel,
}: ConfirmProps) {
  useEsc(() => { if (!busy) onCancel(); });
  return (
    <div className="modal-mask" onClick={() => !busy && onCancel()}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {message && <p style={{ color: "#666", marginTop: 0 }}>{message}</p>}
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onCancel}>
            取消
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
}

interface AlertProps {
  title: string;
  message?: string;
  onClose: () => void;
}

export function AlertModal({ title, message, onClose }: AlertProps) {
  useEsc(onClose);
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {message && <p style={{ color: "#666", marginTop: 0 }}>{message}</p>}
        <div className="modal-actions">
          <button className="btn primary" autoFocus onClick={onClose}>
            知道了
          </button>
        </div>
      </div>
    </div>
  );
}

interface PromptProps {
  title: string;
  defaultValue?: string;
  confirmText?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function PromptModal({ title, defaultValue = "", confirmText = "确认", onConfirm, onCancel }: PromptProps) {
  const [value, setValue] = useState(defaultValue);
  useEsc(onCancel);
  return (
    <div className="modal-mask" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && value.trim() && onConfirm(value.trim())}
        />
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>
            取消
          </button>
          <button className="btn primary" disabled={!value.trim()} onClick={() => onConfirm(value.trim())}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
