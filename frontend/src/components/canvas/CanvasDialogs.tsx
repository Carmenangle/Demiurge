// 画布「小型模态覆盖层」集合（T2.1-D1：从 views/CanvasStageFlow 返回区外移）。
//
// ▍搬移原则：只搬收口
//   本文件全部 JSX 体与搬移前**逐字一致**（仅整体去缩进）；需要的外部符号一律以
//   **同名 props** 解构注入（不重命名、不重组、不合并、不提前求值）⇒「行为面 = 搬移前」
//   由「逐字搬移锁 + 直断言行为网」两件互补证据承担。
//
// ▍红线（防「顺手统一」，改动即破行为面）
//   ① 挂载判定留在视图（`{cond && <Xxx ... />}`），组件自身**恒返回**覆盖层本体；
//      不把 `cond` 收进组件 `return null`——那会改变「挂载/卸载时机」这一行为面。
//   ② `DeletePendingDialog` 的文案三分支（story>0 / projected>0 / 两者皆 0）与 `onConfirm`
//      副作用顺序（pushUndo → deleteNodes → setDeletePending → showToast）逐字保留。
//   ③ props 的可空态由**视图守卫**收窄：传进来的都是非空值（组件内不得补 `?.` 兜底，
//      那等于给判定多加一条分支）。
//   ④ `EdgeInfoDialog` 的 `promptOf` 取词顺序（`data.prompt` → `data.node.prompt` →
//      「（无提示词）」）逐字保留。

import type { Dispatch, SetStateAction } from "react";
import type { Node } from "@xyflow/react";
import { ConfirmModal } from "../Modal";
import type { InspirationKind } from "../../lib/canvasRuntime";
import type { CardNodeData } from "./CanvasTypes";

/** 连线引用关系的数据面（结构同视图 `useState` 声明；可空态已由视图守卫收窄为非空）。 */
export type CanvasEdgeInfo = { source: string; target: string; storyFrom?: number; storyTo?: number };
/** 新建灵感卡的数据面。 */
export type CanvasNewInspiration = { kind: InspirationKind; title: string; content: string };
/** 删除确认的数据面。 */
export type CanvasDeletePending = { ids: string[]; total: number; projected: number; story: number };

/** 编辑卡片介绍文字（视图守卫 `{editLabelId && ...}`）。 */
export interface EditLabelDialogProps {
  editLabelVal: string;
  setEditLabelVal: Dispatch<SetStateAction<string>>;
  setEditLabelId: Dispatch<SetStateAction<string | null>>;
  saveEditLabel: () => void;
}
export function EditLabelDialog({ editLabelVal, setEditLabelVal, setEditLabelId, saveEditLabel }: EditLabelDialogProps) {
  return (
    <div className="modal-mask" onClick={() => setEditLabelId(null)}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <h3>编辑卡片介绍文字</h3>
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 8px" }}>
          显示在卡片底部的一行文字（留空则恢复默认：提示词 / 图组张数）
        </p>
        <input
          value={editLabelVal}
          onChange={(e) => setEditLabelVal(e.target.value)}
          autoFocus
          placeholder="例如：主角初登场（金发红裙）"
          style={{
            width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
            border: "1px solid var(--border, #333)",
            background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
            fontSize: 13, fontFamily: "inherit",
          }}
        />
        <div className="modal-actions">
          <button className="btn" onClick={() => setEditLabelId(null)}>取消</button>
          <button className="btn primary" onClick={saveEditLabel}>保存</button>
        </div>
      </div>
    </div>
  );
}

/** 新建灵感卡（视图守卫 `{newInspiration && ...}`）。 */
export interface NewInspirationDialogProps {
  newInspiration: CanvasNewInspiration;
  setNewInspiration: Dispatch<SetStateAction<CanvasNewInspiration | null>>;
  createInspiration: () => void;
}
export function NewInspirationDialog({ newInspiration, setNewInspiration, createInspiration }: NewInspirationDialogProps) {
  return (
    <div className="modal-mask" onClick={() => setNewInspiration(null)}>
      <div className="modal" style={{ maxWidth: 560 }} onClick={(e) => e.stopPropagation()}>
        <h3>新建灵感卡</h3>
        <input
          value={newInspiration.title}
          onChange={(e) => setNewInspiration({ ...newInspiration, title: e.target.value })}
          autoFocus
          placeholder="标题（显示在卡片头部）"
          style={{
            width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
            border: "1px solid var(--border, #333)", marginBottom: 8,
            background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
            fontSize: 13, fontFamily: "inherit",
          }}
        />
        <textarea
          value={newInspiration.content}
          onChange={(e) => setNewInspiration({ ...newInspiration, content: e.target.value })}
          placeholder="内容（右键卡片 → 插入对话：把灵感卡插到输入框图片栏，发送时图文拆分）"
          rows={10}
          style={{
            width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
            border: "1px solid var(--border, #333)", resize: "vertical",
            background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
            fontSize: 13, fontFamily: "inherit", lineHeight: 1.5,
          }}
        />
        <div className="modal-actions">
          <button className="btn" onClick={() => setNewInspiration(null)}>取消</button>
          <button className="btn primary" onClick={createInspiration}>创建</button>
        </div>
      </div>
    </div>
  );
}

/** 组重命名（视图守卫 `{groupRenameId && ...}`）。 */
export interface GroupRenameDialogProps {
  groupRenameTitle: string;
  setGroupRenameTitle: Dispatch<SetStateAction<string>>;
  setGroupRenameId: Dispatch<SetStateAction<string | null>>;
  saveGroupRename: () => void;
}
export function GroupRenameDialog({ groupRenameTitle, setGroupRenameTitle, setGroupRenameId, saveGroupRename }: GroupRenameDialogProps) {
  return (
    <div className="modal-mask" onClick={() => setGroupRenameId(null)}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <h3>编辑组名</h3>
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 8px" }}>
          类似 ComfyUI 组的标题（左键拖标题栏移动整组，右下角可缩放）
        </p>
        <input
          value={groupRenameTitle}
          onChange={(e) => setGroupRenameTitle(e.target.value)}
          autoFocus
          placeholder="组名"
          style={{
            width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
            border: "1px solid var(--border, #333)",
            background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
            fontSize: 13, fontFamily: "inherit",
          }}
        />
        <div className="modal-actions">
          <button className="btn" onClick={() => setGroupRenameId(null)}>取消</button>
          <button className="btn primary" onClick={saveGroupRename}>保存</button>
        </div>
      </div>
    </div>
  );
}

/** 删除确认（视图守卫 `{deletePending && ...}`）。文案三分支与 onConfirm 副作用顺序是红线。 */
export interface DeletePendingDialogProps {
  deletePending: CanvasDeletePending;
  pushUndo: () => void;
  deleteNodes: (ids: string[]) => void;
  setDeletePending: Dispatch<SetStateAction<CanvasDeletePending | null>>;
  showToast: (msg: string, kind?: "info" | "error" | "success") => void;
}
export function DeletePendingDialog({ deletePending, pushUndo, deleteNodes, setDeletePending, showToast }: DeletePendingDialogProps) {
  return (
    <ConfirmModal
      title="从画布删除节点"
      message={`将 ${deletePending.total} 个节点从画布移除${
        deletePending.story > 0
          ? `；其中 ${deletePending.story} 个为剧情楼层，删除后对话中对应消息也会一并删除（不可撤销）`
          : ""
      }${deletePending.projected > 0
          ? `；其中 ${deletePending.projected} 个为生成内容/工作流节点（资产库中的图片不会删除，Ctrl+Z 可恢复）`
          : ""
      }。`}
      confirmText="删除"
      danger
      onConfirm={() => {
        pushUndo();
        deleteNodes(deletePending.ids);
        setDeletePending(null);
        showToast(
          deletePending.story > 0
            ? `已删除 ${deletePending.total} 个节点（含 ${deletePending.story} 个剧情楼层，对应消息已删除）`
            : `已从画布移除 ${deletePending.total} 个节点（Ctrl+Z 可撤销）`,
          "success",
        );
      }}
      onCancel={() => setDeletePending(null)}
    />
  );
}

/** 连线引用关系（视图守卫 `{edgeInfo && ...}`）。`promptOf` 取词顺序是红线。 */
export interface EdgeInfoDialogProps {
  edgeInfo: CanvasEdgeInfo;
  setEdgeInfo: Dispatch<SetStateAction<CanvasEdgeInfo | null>>;
  rfNodes: Node<CardNodeData>[];
}
export function EdgeInfoDialog({ edgeInfo, setEdgeInfo, rfNodes }: EdgeInfoDialogProps) {
  return (
    <div className="modal-mask" onClick={() => setEdgeInfo(null)}>
      <div className="modal" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
        {edgeInfo.storyFrom !== undefined ? (
          <>
            <h3>剧情顺序线</h3>
            <p style={{ fontSize: 13, lineHeight: 1.6, margin: 0 }}>
              第 <b>{edgeInfo.storyFrom}</b> 段 → 第 <b>{edgeInfo.storyTo}</b> 段
            </p>
            <p style={{ margin: "10px 0 0", fontSize: 11, color: "var(--text-muted)" }}>
              按对话剧情顺序自动派生（楼层增删后自动更新），非手动连线，不可删除。
            </p>
          </>
        ) : (
          (() => {
            const src = rfNodes.find((n) => n.id === edgeInfo.source);
            const tgt = rfNodes.find((n) => n.id === edgeInfo.target);
            const promptOf = (n?: Node<CardNodeData>) => n?.data?.prompt || n?.data?.node?.prompt || "（无提示词）";
            return (
              <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                <p style={{ margin: 0, color: "var(--text-muted)", fontSize: 12 }}>
                  {edgeInfo.target} → 引用 → {edgeInfo.source}
                </p>
                <p style={{ margin: "10px 0 2px" }}>◀ source（被引用）：</p>
                <p style={{ margin: 0, color: "var(--text-muted)", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 120, overflowY: "auto" }}>
                  {src ? promptOf(src) : "（节点已不存在）"}
                </p>
                <p style={{ margin: "10px 0 2px" }}>▶ target（引用者）：</p>
                <p style={{ margin: 0, color: "var(--text-muted)", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 120, overflowY: "auto" }}>
                  {tgt ? promptOf(tgt) : "（节点已不存在）"}
                </p>
                <p style={{ margin: "10px 0 0", fontSize: 11, color: "var(--text-muted)" }}>
                  语义：如视频引用底图、图生图引用参考图。当前为视觉连线，references 数据模型未与生成链路打通。
                </p>
              </div>
            );
          })()
        )}
        <div className="modal-actions">
          <button className="btn primary" onClick={() => setEdgeInfo(null)}>知道了</button>
        </div>
      </div>
    </div>
  );
}
