// 暂停后等待后端真正释放 thread 准入（2026-10-14 A 方案）。
//
// 实锤背景：「暂停」只发协作取消信号（request_cancel → set event），admission 的
// release 要等后台运行自己走到 finally；若它卡在同步阻塞（历史压缩摘要 LLM 调用、
// 参考图上传）里，取消信号无人消费，用户暂停后立即重发就撞 409「该对话已有生成
// 任务正在运行」（换模型也没用——409 按 thread_id 判，与模型无关）。
//
// 本模块把「取消后轮询 /image-agent/running 到 false」收成可单测的纯逻辑：
// hardCancel  await 它之后才返回，调用方（stopGenerating / guideQueued）自然安全。
import { fetchAgentRunning } from "../api/ai";

export interface WaitAgentReleaseOptions {
  /** 轮询间隔（毫秒）。默认 1000。 */
  intervalMs?: number;
  /** 总等待上限（毫秒）。默认 60000——超过仍未释放就放行并告警，避免前端永久卡死。 */
  timeoutMs?: number;
  /** 探测函数（测试注入假实现）。 */
  fetchRunning?: (threadId: string) => Promise<{ running: boolean }>;
  /** 每次轮询后的回调（测试/UI 提示用）。 */
  onPoll?: (attempt: number) => void;
}

/**
 * 轮询直到该 thread 的后台生成任务释放。
 * @returns true=已确认释放；false=超时仍未释放（调用方应放行但提示用户稍候）。
 */
export async function waitForAgentRelease(
  threadId: string,
  options: WaitAgentReleaseOptions = {},
): Promise<boolean> {
  const intervalMs = options.intervalMs ?? 1_000;
  const timeoutMs = options.timeoutMs ?? 60_000;
  const probe = options.fetchRunning ?? fetchAgentRunning;
  const deadline = Date.now() + timeoutMs;
  let attempt = 0;
  for (;;) {
    let released = false;
    try {
      const { running } = await probe(threadId);
      released = !running;
    } catch {
      // 单次探测失败（网络抖动）不放弃，继续轮到超时——与「未释放」同处理。
      released = false;
    }
    if (released) return true;
    if (Date.now() >= deadline) return false;
    attempt += 1;
    options.onPoll?.(attempt);
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
