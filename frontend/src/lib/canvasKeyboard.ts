// canvasKeyboard.ts — 画布快捷键与撤销栈纯逻辑（与 React 解耦，可单测）

export interface Snapshot<T> {
  past: T[];
  present: T;
  future: T[];
}

export function createUndoStack<T>(initial: T): Snapshot<T> {
  return { past: [], present: initial, future: [] };
}

export function pushSnapshot<T>(stack: Snapshot<T>, next: T): Snapshot<T> {
  if (next === stack.present) return stack;
  const past = [...stack.past, stack.present];
  if (past.length > 50) past.shift(); // 限制历史栈深度
  return { past, present: next, future: [] };
}

export function undo<T>(stack: Snapshot<T>): Snapshot<T> {
  if (stack.past.length === 0) return stack;
  const previous = stack.past[stack.past.length - 1];
  return {
    past: stack.past.slice(0, -1),
    present: previous,
    future: [stack.present, ...stack.future],
  };
}

export function redo<T>(stack: Snapshot<T>): Snapshot<T> {
  if (stack.future.length === 0) return stack;
  const next = stack.future[0];
  return {
    past: [...stack.past, stack.present],
    present: next,
    future: stack.future.slice(1),
  };
}

export interface CanvasShortcutHandlers {
  onDelete?: () => void;
  onUndo?: () => void;
  onRedo?: () => void;
  onSelectAll?: () => void;
}

/** 画布快捷键处理（纯函数，由组件的 onKeyDown 调用）。 */
export function handleCanvasKeyDown(
  e: React.KeyboardEvent,
  handlers: CanvasShortcutHandlers,
): void {
  const { key, ctrlKey, metaKey, shiftKey } = e;
  const mod = ctrlKey || metaKey;

  if (key === "Delete" || key === "Backspace") {
    e.preventDefault();
    handlers.onDelete?.();
    return;
  }
  if (mod && (key === "z" || key === "Z")) {
    e.preventDefault();
    if (shiftKey) handlers.onRedo?.();
    else handlers.onUndo?.();
    return;
  }
  if (mod && (key === "y" || key === "Y")) {
    e.preventDefault();
    handlers.onRedo?.();
    return;
  }
  if (mod && (key === "a" || key === "A")) {
    e.preventDefault();
    handlers.onSelectAll?.();
  }
}
