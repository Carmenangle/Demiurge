// SSE 流式请求的深模块：拥有传输、分帧、[DONE]、中止、错误四件事。
// 三个流式接口（chat / image-agent / support）此前把这套逐字节复制了 3 遍，
// 现在统一走这里，调用方只给请求体和 onEvent（认识自己关心的字段）。
//
// wire 格式（与后端 sse_response 对齐）：每个事件一行 `data: <json>\n\n`，
// 收尾一行 `data: [DONE]`。payload 语义由 chatStreamProtocol 的版本化协议拥有。

import { apiUrl } from "./client";

// 连接层断开（fetch 被拒 / 流读到一半终止）的统一文案：浏览器原生 message
// （"Failed to fetch"/"terminated"）对用户不可读，且要说清「进度不丢、去哪看」。
// HTTP 错误响应不走这里（上面已解析 detail）；AbortError 是用户主动停止，无文案。
const STREAM_DROP_MESSAGE =
  "连接中断：与后端的流式连接已断开（网络波动或后端重启）。" +
  "本轮进度不丢，可在后台活动面板查看；重新发送即可继续。";

// 打开一条 SSE 流：POST body 到 path，逐事件回调 onEvent。
// onEvent 返回 "stop" 可提前结束（如收到 error 后不再继续）。
// onDone(err?) 在流正常结束、[DONE]、中止、或出错时调用一次。返回中止函数。
export function openSSE(
  path: string,
  body: unknown,
  onEvent: (obj: Record<string, unknown>) => void | "stop",
  onDone: (err?: string) => void,
): () => void {
  const ctrl = new AbortController();
  (async () => {
    try {
      const resp = await fetch(apiUrl(path), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });
      if (!resp.ok || !resp.body) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json())?.detail || detail; } catch { /* ignore */ }
        onDone(detail);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n"); // 按 SSE 空行分隔事件；尾段可能是半包，留到下轮
        buf = events.pop() || "";
        for (const ev of events) {
          const line = ev.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const data = line.slice(5).trim();
          if (data === "[DONE]") { onDone(); return; }
          let obj: Record<string, unknown>;
          try { obj = JSON.parse(data); } catch { continue; }  // 半包/脏行忽略
          if (onEvent(obj) === "stop") { onDone(); return; }
        }
      }
      onDone();
    } catch (e) {
      if ((e as Error).name === "AbortError") onDone();
      else onDone(STREAM_DROP_MESSAGE);
    }
  })();
  return () => ctrl.abort();
}
