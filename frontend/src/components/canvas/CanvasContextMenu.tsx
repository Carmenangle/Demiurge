// 画布右键功能栏（T2.1-D2：从 views/CanvasStageFlow 返回区外移）。
//
// ▍搬移原则：只搬收口 —— 本文件 JSX 体与搬移前**逐字一致**（仅整体去缩进）；需要的外部符号一律以
//   **同名 props** 解构注入（不重命名、不重组、不合并、不提前求值）⇒「行为面 = 搬移前」由
//   「逐字搬移锁 + 直断言行为网」两件互补证据承担。
//
// ▍红线（防「顺手统一」，改动即破行为面）
//   ① 挂载判定留在视图（`{ctxMenu && ...}`），组件恒返回菜单本体；不把 `cond` 收进组件 `return null`。
//   ② 五分支结构（空白多选 / 单选灵感卡 / 单选参考图 / 单选组 / 其它节点兜底）与各 onClick 内联副作用
//      逐字保留——尤其「删除组（保留内容）」的 `setLayoutNodes` updater 内 persist（★ persist 必须在
//      updater 内拿 next，注释随体保留）与参考图删除的 `persistNow({ refs: next })`。
//   ③ props 可空态由**视图守卫**收窄（`ctxMenu` 非空）；组件内不得补 `?.` 兜底。

import type { Dispatch, RefObject, SetStateAction } from "react";
import type { Node } from "@xyflow/react";
import { Edit3, FolderPlus, MessageSquarePlus, Send, Sparkles, Trash2, X } from "lucide-react";
import {
  type CanvasPersistOverrides, type CtxMenuState,
} from "../../lib/useCanvasEventBridge";
import type { InspirationCardStored, NodeLayout, ReferenceImageStored } from "../../lib/canvasLayout";
import type { CardNodeData } from "./CanvasTypes";
import type { CanvasNewInspiration } from "./CanvasDialogs";
import type { InspirationKind } from "../../lib/canvasRuntime";

/** 右键功能栏（视图守卫 `{ctxMenu && ...}`）。 */
export interface CanvasContextMenuProps {
  ctxMenu: CtxMenuState;
  ctxMenuRef: RefObject<HTMLDivElement | null>;
  setCtxMenu: Dispatch<SetStateAction<CtxMenuState | null>>;
  setRfNodes: Dispatch<SetStateAction<Node<CardNodeData>[]>>;
  rfNodesRef: RefObject<Node<CardNodeData>[]>;
  createGroup: (ids: string[]) => void;
  setNewInspiration: Dispatch<SetStateAction<CanvasNewInspiration | null>>;
  importInspirationFromLibrary: (silent?: boolean, existingLayout?: Record<string, NodeLayout>) => Promise<void>;
  importBusy: boolean;
  inspirationCardsRef: RefObject<InspirationCardStored[]>;
  insertInspirationToChat: (card: InspirationCardStored) => Promise<void>;
  showToast: (msg: string, kind?: "info" | "error" | "success") => void;
  setInspirationEditId: Dispatch<SetStateAction<string | null>>;
  setInspirationEditKind: Dispatch<SetStateAction<InspirationKind>>;
  setInspirationEditTitle: Dispatch<SetStateAction<string>>;
  setInspirationEditContent: Dispatch<SetStateAction<string>>;
  removeInspiration: (id: string) => void;
  referenceImagesRef: RefObject<ReferenceImageStored[]>;
  setReferenceImages: Dispatch<SetStateAction<ReferenceImageStored[]>>;
  persistNow: (overrides?: CanvasPersistOverrides) => void;
  layoutNodesRef: RefObject<Record<string, NodeLayout>>;
  setGroupRenameId: Dispatch<SetStateAction<string | null>>;
  setGroupRenameTitle: Dispatch<SetStateAction<string>>;
  setLayoutNodes: Dispatch<SetStateAction<Record<string, NodeLayout>>>;
  requestDelete: (ids: string[]) => void;
  openEditLabel: (id: string, current: string) => void;
}
export function CanvasContextMenu({ ctxMenu, ctxMenuRef, setCtxMenu, setRfNodes, rfNodesRef, createGroup,
  setNewInspiration, importInspirationFromLibrary, importBusy, inspirationCardsRef, insertInspirationToChat,
  showToast, setInspirationEditId, setInspirationEditKind, setInspirationEditTitle, setInspirationEditContent,
  removeInspiration, referenceImagesRef, setReferenceImages, persistNow, layoutNodesRef, setGroupRenameId,
  setGroupRenameTitle, setLayoutNodes, requestDelete, openEditLabel }: CanvasContextMenuProps) {
  return (
    <div
      ref={ctxMenuRef}
      style={{
        position: "absolute", left: ctxMenu.x, top: ctxMenu.y, zIndex: 40,
        background: "var(--surface, #1e2126)", border: "1px solid var(--border, #333)",
        borderRadius: 8, boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
        padding: 4, minWidth: 172,
      }}
      onClick={(e) => { e.stopPropagation(); setCtxMenu(null); }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {ctxMenu.nodeIds.length === 0 ? (
        <>
          <div
            className="ctx-item"
            onClick={() => {
              setRfNodes((ns) => ns.map((n) => (n.selected ? { ...n, selected: false } : n)));
            }}
          >
            <X size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
            全部取消选择
          </div>
          <div className="ctx-item" onClick={() => createGroup(rfNodesRef.current.filter((n) => n.selected).map((n) => n.id))}>
            <FolderPlus size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
            选中节点建立组
          </div>
          <div className="ctx-sep" />
          <div
            className="ctx-item"
            onClick={() => setNewInspiration({ kind: "preset", title: "", content: "" })}
          >
            <MessageSquarePlus size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
            新建灵感卡
          </div>
          <div className="ctx-item" onClick={() => void importInspirationFromLibrary(false)}>
            <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
            {importBusy ? "导入中…" : "从素材库导入灵感卡"}
          </div>
        </>
      ) : ctxMenu.nodeIds.length === 1 ? (
        (() => {
          const n = rfNodesRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
          if (n?.data.node.type === "inspiration-card") {
            return (
              <>
                <div className="ctx-item" onClick={() => {
                  const c = inspirationCardsRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                  if (c) void insertInspirationToChat(c);
                  setCtxMenu(null);
                }}>
                  <Send size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  插入到对话
                </div>
                <div className="ctx-item" onClick={() => {
                  const c = inspirationCardsRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                  if (c) { try { void navigator.clipboard.writeText(c.content); showToast("内容已复制", "success"); } catch { showToast("复制失败", "error"); } }
                  setCtxMenu(null);
                }}>
                  <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  复制文本
                </div>
                <div className="ctx-item" onClick={() => {
                  const c = inspirationCardsRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                  if (c) {
                    setInspirationEditId(c.id);
                    setInspirationEditKind(c.kind);
                    setInspirationEditTitle(c.title);
                    setInspirationEditContent(c.content);
                  }
                  setCtxMenu(null);
                }}>
                  <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  编辑标题/内容
                </div>
                <div className="ctx-item danger" onClick={() => removeInspiration(ctxMenu.nodeIds[0])}>
                  <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  删除灵感卡
                </div>
              </>
            );
          }
          if (n?.data.node.type === "reference-image") {
            return (
              <>
                <div className="ctx-item" onClick={() => {
                  const ref = referenceImagesRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                  if (ref) {
                    setInspirationEditId(ref.id);
                    setInspirationEditKind("preset");
                    setInspirationEditTitle(ref.title);
                  }
                  setCtxMenu(null);
                }}>
                  <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  编辑标题
                </div>
                <div className="ctx-item danger" onClick={() => {
                  const next = referenceImagesRef.current.filter((c) => c.id !== ctxMenu.nodeIds[0]);
                  setReferenceImages(next);
                  referenceImagesRef.current = next;
                  persistNow({ refs: next });
                  setCtxMenu(null);
                }}>
                  <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  删除参考图
                </div>
              </>
            );
          }
          if (n?.data.node.type === "group") {
            return (
              <>
                <div className="ctx-item" onClick={() => {
                  const lyt = layoutNodesRef.current[n.id];
                  setGroupRenameId(n.id);
                  setGroupRenameTitle(lyt?.label || "组");
                  setCtxMenu(null);
                }}>
                  <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  编辑组名
                </div>
                <div className="ctx-item" onClick={() => {
                    setCtxMenu(null);
                    // 删除组但保留子节点：父组链断开，子节点坐标转绝对
                    const groupId = ctxMenu.nodeIds[0];
                    const childIds = rfNodesRef.current
                      .filter((n) => n.parentId === groupId)
                      .map((n) => n.id);
                    setRfNodes((ns) => ns.map((n) => {
                      if (n.id === groupId) return n;
                      if (childIds.includes(n.id)) {
                        const parent = ns.find((pn) => pn.id === groupId);
                        const px = parent?.position?.x ?? 0;
                        const py = parent?.position?.y ?? 0;
                        return { ...n, parentId: undefined, position: { x: n.position.x + px, y: n.position.y + py } };
                      }
                      return n;
                    }));
                    // 也从 layoutNodes 移除组容器，且子节点同步 parentId 置空 + 坐标转绝对
                    // （否则刷新后投影按 layoutNodes 恢复会把子节点挂回已删的组 → 崩坏）
                    setLayoutNodes((prev) => {
                      const next = { ...prev };
                      const group = prev[groupId];
                      delete next[groupId];
                      if (group) {
                        for (const [id, lyt] of Object.entries(prev)) {
                          if (lyt.parentId === groupId) {
                            next[id] = {
                              ...lyt,
                              x: lyt.x + group.x,
                              y: lyt.y + group.y,
                              parentId: undefined,
                            };
                          }
                        }
                      }
                      // ★ persist 必须在 updater 内拿 next：外层同步读 layoutNodesRef 是旧值
                      //   （ref 仅在渲染时更新）→ 会把删除前的布局存回去，刷新后组复活。
                      //   StrictMode 下 updater 双跑 → persist 幂等重复无害。
                      persistNow({ nodes: next });
                      return next;
                    });
                  }}>
                    <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                    删除组（保留内容）
                  </div>
                  <div className="ctx-item danger" onClick={() => { requestDelete(ctxMenu.nodeIds); }}>
                    <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                    删除组（连带子节点）
                  </div>
              </>
            );
          }
          return (
            <>
              <div
                className="ctx-item"
                onClick={() => {
                  if (n && n.data.node.type !== "group") {
                    openEditLabel(n.id, n.data.customLabel || n.data.prompt || "");
                  }
                }}
              >
                <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                编辑介绍文字
              </div>
              <div className="ctx-item danger" onClick={() => { requestDelete(ctxMenu.nodeIds); }}>
                <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                删除节点
              </div>
            </>
          );
        })()
      ) : (
        <>
          <div className="ctx-item" onClick={() => createGroup(ctxMenu.nodeIds)}>
            <FolderPlus size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
            建立组（{ctxMenu.nodeIds.length} 个节点）
          </div>
          <div className="ctx-item danger" onClick={() => { requestDelete(ctxMenu.nodeIds); }}>
            <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
            一起删除（{ctxMenu.nodeIds.length}）
          </div>
        </>
      )}
    </div>
  );
}
