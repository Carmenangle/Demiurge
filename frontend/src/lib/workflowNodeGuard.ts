// 节点卡（NodeCard）画布内容守卫：判定 iframe 里画的到底是不是我们要的那个节点。
//
// 背景：工作流卡每个节点一个独立 ComfyUI iframe（?laf_lock=1），载入时 keepOnly([nodeId])
// 只保留目标节点。但 ComfyUI 前端会把「裁剪后的单节点图」持久化到同源存储
// （localStorage + IndexedDB，见 laf_lock.js 的 clearComfyStorage 注释）：所有兄弟 iframe
// 同源，谁最后写谁赢；某个 iframe 迟到恢复会话时，可能把别人裁剪出来的节点读回自己画布。
// 表现为「节点 3 · #20 的框里画的是节点 2 · #18，而比例还是 #20 的」（比例来自污染前
// 那次 node_size，状态不会被覆盖）。
//
// 旧校验只看「画布里是否恰好 1 个节点」——被污染的画布同样是 1 个节点 → 误判成功、
// 永不自愈。所以必须连节点 id 一起校验，并在载入后做多次延迟复检（会话恢复是迟到的）。
//
// 只放纯判定，组件负责调度：前端逻辑进 lib、组件只展示（架构合同）。

// 复检节奏（相对上一次校验的间隔，ms）。首检在 loaded 后 2.2s（等 DOM widget 撑开），
// 之后逐步拉长，总覆盖约 35s——ComfyUI 的会话恢复/自动保存是异步且可能很慢，
// 只校验一次会被「先对后错」漏掉。
export const NODE_VERIFY_GAPS_MS = [2200, 3000, 5000, 8000, 10000, 15000];

// 校验「单节点画布里是不是 nodeId 这个节点」。
// payload 为 laf_lock 的 graph 消息载荷：{ workflow: { nodes: [...] } }。
export function isSoloNodeGraph(payload: unknown, nodeId: string): boolean {
  const nodes = (payload as any)?.workflow?.nodes;
  if (!Array.isArray(nodes) || nodes.length !== 1) return false;
  return String(nodes[0]?.id) === String(nodeId);
}

// 校验 node_size 回传的尺寸是不是本节点的（防止拿别的节点尺寸改本卡比例）。
// 老版本扩展不回传 id → 放行，避免守卫把正常流程卡死。
export function isNodeSizeFor(payload: unknown, nodeId: string): boolean {
  const p = payload as any;
  if (!p || p.id == null) return true;
  return String(p.id) === String(nodeId);
}
