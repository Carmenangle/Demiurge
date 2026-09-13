import { isLafMessageFromStrict, lockUrl, postToFrame } from "./lafLock";

export interface WorkflowCaptureResult {
  prompt: unknown | null;
  workflow: unknown;
  opResults: unknown[];
}

export function requestFrameMessage<T>({
  frameWindow, comfyUrl, requestType, expectedType, payload, timeoutMs = 6000,
}: {
  frameWindow: Window | null | undefined;
  comfyUrl: string;
  requestType: string;
  expectedType: string;
  payload?: unknown;
  timeoutMs?: number;
}): Promise<T | null> {
  return new Promise((resolve) => {
    if (!frameWindow) return resolve(null);
    let settled = false;
    let timeout: ReturnType<typeof setTimeout>;
    const finish = (value: T | null) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      clearTimeout(timeout);
      resolve(value);
    };
    const onMessage = (event: MessageEvent) => {
      if (!isLafMessageFromStrict(event, frameWindow, comfyUrl, expectedType)) return;
      finish(event.data.payload as T);
    };
    window.addEventListener("message", onMessage);
    postToFrame(frameWindow, requestType, payload, comfyUrl);
    timeout = setTimeout(() => finish(null), timeoutMs);
  });
}

export function captureWorkflowApiPrompt({
  workflow, comfyUrl, ops = [],
}: {
  workflow: unknown;
  comfyUrl: string;
  ops?: unknown[];
}): Promise<WorkflowCaptureResult> {
  return new Promise((resolve) => {
    const frame = document.createElement("iframe");
    frame.style.cssText = "position:fixed;width:1200px;height:800px;left:-9999px;top:0;border:0;";
    frame.src = lockUrl(comfyUrl);
    let settled = false;
    let loadSent = false;
    let retries = 0;
    let opResults: unknown[] = [];
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const later = (fn: () => void, delay: number) => {
      const timer = setTimeout(() => {
        timers.delete(timer);
        fn();
      }, delay);
      timers.add(timer);
    };
    const finish = (prompt: unknown | null, capturedWorkflow: unknown = workflow) => {
      if (settled) return;
      settled = true;
      timers.forEach(clearTimeout);
      timers.clear();
      window.removeEventListener("message", onMessage);
      frame.remove();
      resolve({ prompt, workflow: capturedWorkflow, opResults });
    };
    const sendLoad = () => {
      if (loadSent) return;
      loadSent = true;
      postToFrame(frame.contentWindow, "load", { workflow, exposedIds: [] }, comfyUrl);
    };
    const requestPrompt = () =>
      postToFrame(frame.contentWindow, "request_api_prompt", undefined, comfyUrl);
    const onMessage = (event: MessageEvent) => {
      if (!isLafMessageFromStrict(event, frame.contentWindow, comfyUrl)) return;
      const data = event.data;
      if (data.type === "ready") {
        sendLoad();
      } else if (data.type === "loaded") {
        later(() => {
          if (ops.length > 0) {
            postToFrame(frame.contentWindow, "apply_ops", { ops }, comfyUrl);
          } else {
            requestPrompt();
          }
        }, 300);
      } else if (data.type === "ops_applied") {
        opResults = data.payload?.results || [];
        later(requestPrompt, 300);
      } else if (data.type === "api_prompt") {
        if (data.payload?.ok && data.payload.output) {
          finish(data.payload.output, data.payload.workflow || workflow);
        } else if (retries++ < 3) {
          later(requestPrompt, 600);
        } else {
          finish(null, data.payload?.workflow || workflow);
        }
      }
    };
    window.addEventListener("message", onMessage);
    frame.addEventListener("load", () => {
      later(() => {
        if (!loadSent) postToFrame(frame.contentWindow, "ping_ready", undefined, comfyUrl);
      }, 8000);
    });
    document.body.appendChild(frame);
    later(() => finish(null), 30000);
  });
}
