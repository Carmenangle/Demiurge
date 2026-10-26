// 生产默认同源相对 "/api"（L2 联机同库 + LAN 浏览器访问 PC 的前提：origin 不烧进产物，
// 与 view_urls 根相对同一条教义）；dev（vite 5173 站点）保持绝对回环（vite 代理也配了
// /api 双保险）；手机端构建仍由 VITE_API_BASE=/api 显式覆盖（build-frontend-mobile.sh）。
export const API_BASE = (import.meta.env.VITE_API_BASE
  || (import.meta.env.DEV ? "http://127.0.0.1:8010/api" : "/api")).replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

/** 「仓库文件夹未配置」的后端文案（真源 = `backend/app/services/repo_meta.py` 的
 *  `WORKS_ROOT_NOT_CONFIGURED_MSG`，两处**必须同时改**：跨语言契约锁见 `client.contract.test.ts`）。
 *
 *  2026-09-15 用户定案：仓库文件夹没配时作品根校验**拒绝**（原来放行）——命中这条即代表
 *  「不是路径不匹配，而是压根没配置」，前端据此弹窗并引导跳转「设置 → 路径与代理」。
 */
export const WORKS_ROOT_UNCONFIGURED_MARK = "尚未配置「仓库文件夹」";

/** 前端全局事件：作品根校验因「未配置仓库文件夹」被拒（消费方 = App 的引导弹窗）。 */
export const WORKS_ROOT_UNCONFIGURED_EVENT = "laf-works-root-unconfigured";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    // 透出后端 detail（FastAPI 错误体 {detail: ...}），否则只能看到状态码
    let detail = "";
    try {
      const data = await response.json();
      detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? data);
    } catch {
      try { detail = await response.text(); } catch { /* ignore */ }
    }
    // 未配置仓库文件夹：抛错照旧（调用方的 catch 不受影响），额外广播一次全局事件让 App 弹窗引导。
    if (detail.includes(WORKS_ROOT_UNCONFIGURED_MARK)) {
      try {
        window.dispatchEvent(new CustomEvent(WORKS_ROOT_UNCONFIGURED_EVENT, {
          detail: { message: detail },
        }));
      } catch { /* 非浏览器环境（无 window）忽略 */ }
    }
    throw new Error(detail || `API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function apiGet<T>(path: string, timeoutMs?: number): Promise<T> {
  // 可选超时：传 timeoutMs 时用 AbortController 限时，超时抛可读错误。
  // 轮询类接口（如 /comfyui/status）必须传超时——否则后端慢/挂起时 await 永不返回，
  // 调用方的轮询链就断了（表现为「等待中…」后不再重试）。默认不传保持原有行为。
  let signal: AbortSignal | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  if (timeoutMs && timeoutMs > 0) {
    const ac = new AbortController();
    signal = ac.signal;
    timer = setTimeout(() => ac.abort(), timeoutMs);
  }
  try {
    return await parseResponse<T>(await fetch(apiUrl(path), { signal }));
  } catch (e) {
    if ((e as Error)?.name === "AbortError") {
      throw new Error(`请求超时（超过 ${Math.round((timeoutMs || 0) / 1000)} 秒）。`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * GET 一个二进制响应（scene.pkg 这类「后端只给字节、由前端解析」的端点）。
 *
 * 不走 parseResponse：那是 JSON 语义。非 2xx 仍尽量透出后端 detail，
 * 否则用户只能看到「API request failed: 404」这种没法行动的文案。
 */
export async function apiGetBuffer(path: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(apiUrl(path), { signal });
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? data);
    } catch {
      try { detail = await response.text(); } catch { /* ignore */ }
    }
    throw new Error(detail || `API request failed: ${response.status}`);
  }
  return response.arrayBuffer();
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  timeoutMs?: number,
  externalSignal?: AbortSignal,
  timeoutHint?: string,
): Promise<T> {
  // 可选超时：传 timeoutMs 时用 AbortController 限时，超时抛可读错误（避免前端永久“思考中…”死等）。
  // externalSignal：调用方（如“停止”按钮）主动中止；与超时区分，抛不同文案。
  let signal: AbortSignal | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let onExtAbort: (() => void) | undefined;
  if ((timeoutMs && timeoutMs > 0) || externalSignal) {
    const ac = new AbortController();
    signal = ac.signal;
    if (timeoutMs && timeoutMs > 0) timer = setTimeout(() => ac.abort(), timeoutMs);
    if (externalSignal) {
      if (externalSignal.aborted) ac.abort();
      else {
        onExtAbort = () => ac.abort();
        externalSignal.addEventListener("abort", onExtAbort);
      }
    }
  }
  try {
    return await parseResponse<T>(
      await fetch(apiUrl(path), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
      }),
    );
  } catch (e) {
    if ((e as Error)?.name === "AbortError") {
      if (externalSignal?.aborted) throw new Error("已停止本次请求。");
      // timeoutHint：调用方给场景化建议。默认文案在**生图提交**等非 LLM 场景是误导的
      // （2026-10-19 实锤：ComfyUI 提交超时报「需求太复杂，建议把需求拆小」）。
      throw new Error(
        `请求超时（超过 ${Math.round((timeoutMs || 0) / 1000)} 秒）。`
        + (timeoutHint ?? "可能是模型/中转较慢或需求太复杂，建议把需求拆小、分几轮发，或稍后重试。"),
      );
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
    if (externalSignal && onExtAbort) externalSignal.removeEventListener("abort", onExtAbort);
  }
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  return parseResponse<T>(
    await fetch(apiUrl(path), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// multipart/form-data 上传（附件等）：不手动设 Content-Type，交给浏览器带 boundary。
export async function apiUpload<T>(
  path: string,
  formData: FormData,
  timeoutMs?: number,
): Promise<T> {
  let signal: AbortSignal | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  if (timeoutMs && timeoutMs > 0) {
    const ac = new AbortController();
    signal = ac.signal;
    timer = setTimeout(() => ac.abort(), timeoutMs);
  }
  try {
    return await parseResponse<T>(
      await fetch(apiUrl(path), {
        method: "POST",
        body: formData,
        signal,
      }),
    );
  } catch (e) {
    if ((e as Error)?.name === "AbortError") {
      throw new Error(`上传超时（超过 ${Math.round((timeoutMs || 0) / 1000)} 秒）。`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return parseResponse<T>(
    await fetch(apiUrl(path), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function apiDelete<T>(path: string): Promise<T> {
  return parseResponse<T>(await fetch(apiUrl(path), { method: "DELETE" }));
}