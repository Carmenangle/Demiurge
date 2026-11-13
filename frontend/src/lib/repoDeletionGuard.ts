// 删仓库前的三道闸 + 删文件夹的失败收集（2026-11-05 防误删 ③）。
//
// 从 App.tsx 的 `onConfirm` 里抽出来只有一个理由：**让「查不到就不删」这条政策有用例可钉**。
// 内联在 JSX 事件里时，任何一处 `catch { /* 继续 */ }` 被改回「静默放行」都不会让测试变红，
// 而那正是这次要治的缺陷——任务状态/资产清单查询失败时继续删，等于把正在生成的作品删掉。
//
// 探针（probes）由调用方注入，本模块不 import api/stores：策略层保持可单测，接线留在 App.tsx。

export type DeletionProbes = {
  /** 本浏览器登记的 ComfyUI 未归档任务（同步，零延迟）。 */
  hasLocalPending: (ids: string[]) => boolean;
  /** 服务端登记的同任务（换浏览器/清缓存后仍能挡住删除）。 */
  hasServerPending: (ids: string[]) => Promise<boolean>;
  /** 该会话是否有剧情/插画任务在跑；**抛错＝状态未知**。 */
  isAgentRunning: (id: string) => Promise<boolean>;
  /** 该会话资产库里是否已有生成图；**抛错＝资产未知**。 */
  hasAssets: (id: string) => Promise<boolean>;
};

/** 返回阻断文案（给用户的「无法删除」弹窗）；空串 = 三道闸全绿，可以删。 */
export async function repoDeletionBlockReason(
  name: string, ids: string[], probes: DeletionProbes,
): Promise<string> {
  if (probes.hasLocalPending(ids) || await probes.hasServerPending(ids)) {
    return `「${name}」还有生图任务未完成，已阻止删除。请等待归档完成或先显式停止任务。`;
  }
  for (const id of ids) {
    let running = false;
    try {
      running = await probes.isAgentRunning(id);
    } catch (error) {
      return `「${name}」的任务状态查不到，已阻止删除：${(error as Error).message}`;
    }
    if (running) return `「${name}」还有剧情或插画任务在运行，已阻止删除。`;
  }
  for (const id of ids) {
    let occupied = false;
    try {
      occupied = await probes.hasAssets(id);
    } catch (error) {
      return `「${name}」的资产清单查不到，已阻止删除：${(error as Error).message}`;
    }
    if (occupied) {
      return `「${name}」里还有生成图（资产），已阻止删除。请先在资产库删除这些图片，再删仓库。`;
    }
  }
  return "";
}

/** 后端 `/user-state/delete-folder` 的单条结果（只取判定需要的两项）。 */
export type FolderRemoval = { name: string; error?: string };

/** 没删干净的文件夹清单；`error` 空串＝本来就不存在，不算失败。 */
export function folderRemovalFailures(results: FolderRemoval[]): string[] {
  return results.filter((r) => r.error).map((r) => `${r.name}：${r.error}`);
}
