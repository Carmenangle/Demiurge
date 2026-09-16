// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { Node } from "@xyflow/react";
import {
  DeletePendingDialog, EdgeInfoDialog, EditLabelDialog, GroupRenameDialog, NewInspirationDialog,
  type CanvasEdgeInfo, type CanvasDeletePending, type CanvasNewInspiration,
} from "./CanvasDialogs";
import type { CardNodeData } from "./CanvasTypes";

// T2.1-D1：返回区 5 个小型模态覆盖层外移后的回归网。
// 三件证据互补：① 直断言行为网（渲染 + 回调接线，30 例）② 逐字搬移锁（判定语句按次数钉在新属主、
// 且在视图缺席）③ 源码形态锁（视图侧守卫/调用块/import 逐字 + 搬移文案确已离开视图）。
//
// 硬要求（沿用 T2.1 教训）：
//   · 每个用例必须卸载（afterEach 统一 unmount）——否则残留 root 的 window 监听会吃掉后续用例事件；
//   · 源码只能在 it 回调内读：jsdom 下模块求值期 `import.meta.url` 不是 file: 协议。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SEAM = "./CanvasDialogs.tsx";
const VIEW = "../../views/CanvasStageFlow.tsx";

/** jsdom 下 `import.meta.url` 在模块求值期不是 file: 协议 ⇒ 只能在 it 回调内读。 */
function read(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
}

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((l) => l.replace(/(?<!:)\/\/.*$/, ""))
    .join("\n");
}

/** 非重叠计数（needle 用 substring 形态，需自身足够独特；比对前先去注释）。 */
function count(hay: string, needle: string): number {
  let n = 0;
  let i = 0;
  for (;;) {
    const at = hay.indexOf(needle, i);
    if (at === -1) return n;
    n += 1;
    i = at + needle.length;
  }
}

let container: HTMLDivElement;
let root: Root;

async function mount(el: ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(el);
  });
}

async function click(el: Element | null | undefined) {
  await act(async () => {
    (el as HTMLElement | null)?.click();
  });
}

/** 触发 React 受控输入的 onChange（走原型 setter，绕过 React 的 value tracker）。 */
async function typeInto(el: Element | null, value: string) {
  const node = el as HTMLInputElement | HTMLTextAreaElement | null;
  if (!node) throw new Error("typeInto: element missing");
  const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value");
  await act(async () => {
    desc?.set?.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function inputAt(i: number): HTMLInputElement | null {
  return container.querySelectorAll("input")[i] as HTMLInputElement | null;
}
function textareaAt(i: number): HTMLTextAreaElement | null {
  return container.querySelectorAll("textarea")[i] as HTMLTextAreaElement | null;
}
function byText(sel: string, text: string): Element | null {
  return Array.from(container.querySelectorAll(sel)).find((el) => (el.textContent || "").includes(text)) || null;
}
function btn(label: string): HTMLButtonElement | null {
  return (Array.from(container.querySelectorAll("button")).find(
    (b) => (b.textContent || "").trim() === label,
  ) || null) as HTMLButtonElement | null;
}
const mask = () => container.querySelector(".modal-mask");
const modal = () => container.querySelector(".modal");

function nodeOf(id: string, data: unknown): Node<CardNodeData> {
  return { id, position: { x: 0, y: 0 }, data } as unknown as Node<CardNodeData>;
}

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container?.remove();
});

// ───────────────────────── ① EditLabelDialog ─────────────────────────
describe("EditLabelDialog", () => {
  const render = (over: Partial<Record<string, unknown>> = {}) => {
    const setEditLabelVal = vi.fn();
    const setEditLabelId = vi.fn();
    const saveEditLabel = vi.fn();
    return mount(
      <EditLabelDialog
        editLabelVal={(over.editLabelVal as string) ?? "主角初登场"}
        setEditLabelVal={setEditLabelVal}
        setEditLabelId={setEditLabelId}
        saveEditLabel={saveEditLabel}
      />,
    ).then(() => ({ setEditLabelVal, setEditLabelId, saveEditLabel }));
  };

  it("用户可见文案逐字（标题 / 说明 / 占位符）", async () => {
    await render();
    expect(byText("h3", "编辑卡片介绍文字")).toBeTruthy();
    expect(container.textContent).toContain("显示在卡片底部的一行文字（留空则恢复默认：提示词 / 图组张数）");
    expect(container.querySelector("input")?.getAttribute("placeholder")).toBe("例如：主角初登场（金发红裙）");
  });

  it("输入框 value 绑定 editLabelVal", async () => {
    await render({ editLabelVal: "金发红裙" });
    expect(inputAt(0)?.value).toBe("金发红裙");
  });

  it("输入 → setEditLabelVal 收到新值", async () => {
    const api = await render();
    await typeInto(inputAt(0), "改后的介绍");
    expect(api.setEditLabelVal).toHaveBeenCalledTimes(1);
    expect(api.setEditLabelVal).toHaveBeenCalledWith("改后的介绍");
  });

  it("「保存」→ saveEditLabel 恰 1 次", async () => {
    const api = await render();
    await click(btn("保存"));
    expect(api.saveEditLabel).toHaveBeenCalledTimes(1);
    expect(api.setEditLabelId).not.toHaveBeenCalled();
  });

  it("「取消」→ setEditLabelId(null)", async () => {
    const api = await render();
    await click(btn("取消"));
    expect(api.setEditLabelId).toHaveBeenCalledWith(null);
    expect(api.saveEditLabel).not.toHaveBeenCalled();
  });

  it("点遮罩关闭；点内层 modal 不冒泡（stopPropagation 保留）", async () => {
    const api = await render();
    await click(modal());
    expect(api.setEditLabelId).not.toHaveBeenCalled();
    await click(mask());
    expect(api.setEditLabelId).toHaveBeenCalledWith(null);
  });
});

// ───────────────────────── ② NewInspirationDialog ─────────────────────────
describe("NewInspirationDialog", () => {
  const base: CanvasNewInspiration = { kind: "preset", title: "卡一", content: "内容一" };
  const render = () => {
    const setNewInspiration = vi.fn();
    const createInspiration = vi.fn();
    return mount(
      <NewInspirationDialog
        newInspiration={base}
        setNewInspiration={setNewInspiration}
        createInspiration={createInspiration}
      />,
    ).then(() => ({ setNewInspiration, createInspiration }));
  };

  it("用户可见文案逐字 + 两输入绑定", async () => {
    await render();
    expect(byText("h3", "新建灵感卡")).toBeTruthy();
    expect(inputAt(0)?.value).toBe("卡一");
    expect(textareaAt(0)?.value).toBe("内容一");
    expect(textareaAt(0)?.getAttribute("rows")).toBe("10");
  });

  it("改标题 → 合并式 setNewInspiration（title 换新、其余保留）", async () => {
    const api = await render();
    await typeInto(inputAt(0), "新标题");
    expect(api.setNewInspiration).toHaveBeenCalledWith({ ...base, title: "新标题" });
  });

  it("改内容 → 合并式 setNewInspiration（content 换新、其余保留）", async () => {
    const api = await render();
    await typeInto(textareaAt(0), "新内容");
    expect(api.setNewInspiration).toHaveBeenCalledWith({ ...base, content: "新内容" });
  });

  it("「创建」→ createInspiration", async () => {
    const api = await render();
    await click(btn("创建"));
    expect(api.createInspiration).toHaveBeenCalledTimes(1);
    expect(api.setNewInspiration).not.toHaveBeenCalled();
  });

  it("「取消」与遮罩 → setNewInspiration(null)", async () => {
    const api = await render();
    await click(btn("取消"));
    expect(api.setNewInspiration).toHaveBeenCalledWith(null);
    api.setNewInspiration.mockClear();
    await click(modal());
    expect(api.setNewInspiration).not.toHaveBeenCalled();
    await click(mask());
    expect(api.setNewInspiration).toHaveBeenCalledWith(null);
  });
});

// ───────────────────────── ③ GroupRenameDialog ─────────────────────────
describe("GroupRenameDialog", () => {
  const render = () => {
    const setGroupRenameTitle = vi.fn();
    const setGroupRenameId = vi.fn();
    const saveGroupRename = vi.fn();
    return mount(
      <GroupRenameDialog
        groupRenameTitle="第一幕"
        setGroupRenameTitle={setGroupRenameTitle}
        setGroupRenameId={setGroupRenameId}
        saveGroupRename={saveGroupRename}
      />,
    ).then(() => ({ setGroupRenameTitle, setGroupRenameId, saveGroupRename }));
  };

  it("用户可见文案逐字 + value 绑定", async () => {
    await render();
    expect(byText("h3", "编辑组名")).toBeTruthy();
    expect(container.textContent).toContain("类似 ComfyUI 组的标题（左键拖标题栏移动整组，右下角可缩放）");
    expect(inputAt(0)?.value).toBe("第一幕");
    expect(inputAt(0)?.getAttribute("placeholder")).toBe("组名");
  });

  it("输入 → setGroupRenameTitle 收到新值", async () => {
    const api = await render();
    await typeInto(inputAt(0), "第二幕");
    expect(api.setGroupRenameTitle).toHaveBeenCalledWith("第二幕");
  });

  it("「保存」→ saveGroupRename 恰 1 次", async () => {
    const api = await render();
    await click(btn("保存"));
    expect(api.saveGroupRename).toHaveBeenCalledTimes(1);
  });

  it("「取消」→ setGroupRenameId(null)", async () => {
    const api = await render();
    await click(btn("取消"));
    expect(api.setGroupRenameId).toHaveBeenCalledWith(null);
    expect(api.saveGroupRename).not.toHaveBeenCalled();
  });
});

// ───────────────────────── ④ DeletePendingDialog ─────────────────────────
describe("DeletePendingDialog", () => {
  const render = (pending: CanvasDeletePending, opts: { confirm?: boolean } = {}) => {
    const calls: string[] = [];
    const pushUndo = vi.fn(() => calls.push("undo"));
    const deleteNodes = vi.fn((ids: string[]) => calls.push("delete:" + ids.join(",")));
    const setDeletePending = vi.fn((v: unknown) => calls.push("set:" + String(v)));
    const showToast = vi.fn((msg: string, kind?: string) => calls.push("toast:" + String(kind) + ":" + msg));
    return mount(
      <DeletePendingDialog
        deletePending={pending}
        pushUndo={pushUndo}
        deleteNodes={deleteNodes}
        setDeletePending={setDeletePending}
        showToast={showToast}
      />,
    ).then(() => ({ calls, pushUndo, deleteNodes, setDeletePending, showToast }));
  };
  const msg = () => (container.querySelector(".modal p")?.textContent || "");
  const full = { ids: ["a", "b", "c", "d", "e"], total: 5, projected: 1, story: 2 };

  it("文案：story>0 且 projected>0 时两段说明都带", async () => {
    await render(full);
    expect(msg()).toBe(
      "将 5 个节点从画布移除；其中 2 个为剧情楼层，删除后对话中对应消息也会一并删除（不可撤销）"
      + "；其中 1 个为生成内容/工作流节点（资产库中的图片不会删除，Ctrl+Z 可恢复）。",
    );
  });

  it("文案：仅 story>0（不带 projected 段）", async () => {
    await render({ ids: ["a", "b", "c"], total: 3, projected: 0, story: 3 });
    expect(msg()).toBe("将 3 个节点从画布移除；其中 3 个为剧情楼层，删除后对话中对应消息也会一并删除（不可撤销）。");
  });

  it("文案：仅 projected>0（不带 story 段）", async () => {
    await render({ ids: ["a", "b", "c", "d"], total: 4, projected: 2, story: 0 });
    expect(msg()).toBe("将 4 个节点从画布移除；其中 2 个为生成内容/工作流节点（资产库中的图片不会删除，Ctrl+Z 可恢复）。");
  });

  it("文案：两者皆 0 时只剩主句", async () => {
    await render({ ids: ["a", "b"], total: 2, projected: 0, story: 0 });
    expect(msg()).toBe("将 2 个节点从画布移除。");
  });

  it("确认按钮文案「删除」+ danger 样式", async () => {
    await render(full);
    const b = btn("删除");
    expect(b).toBeTruthy();
    expect(b?.className).toBe("btn danger");
    expect(byText("h3", "从画布删除节点")).toBeTruthy();
  });

  it("onConfirm 副作用顺序：pushUndo → deleteNodes(ids) → setDeletePending(null) → showToast(success)", async () => {
    const api = await render(full);
    await click(btn("删除"));
    expect(api.calls).toEqual([
      "undo",
      "delete:a,b,c,d,e",
      "set:null",
      "toast:success:已删除 5 个节点（含 2 个剧情楼层，对应消息已删除）",
    ]);
  });

  it("onConfirm 的 toast 兜底随 story 分支二选一", async () => {
    const api = await render({ ids: ["a", "b"], total: 2, projected: 0, story: 0 });
    await click(btn("删除"));
    expect(api.calls[3]).toBe("toast:success:已从画布移除 2 个节点（Ctrl+Z 可撤销）");
  });

  it("onCancel → setDeletePending(null)，且不触发删除", async () => {
    const api = await render(full);
    await click(btn("取消"));
    expect(api.setDeletePending).toHaveBeenCalledWith(null);
    expect(api.deleteNodes).not.toHaveBeenCalled();
    expect(api.pushUndo).not.toHaveBeenCalled();
  });
});

// ───────────────────────── ⑤ EdgeInfoDialog ─────────────────────────
describe("EdgeInfoDialog", () => {
  const render = (info: CanvasEdgeInfo, rfNodes: Node<CardNodeData>[]) => {
    const setEdgeInfo = vi.fn();
    return mount(<EdgeInfoDialog edgeInfo={info} setEdgeInfo={setEdgeInfo} rfNodes={rfNodes} />).then(() => ({ setEdgeInfo }));
  };

  it("剧情顺序线分支：标题 + 段号（storyFrom 存在即走此分支）", async () => {
    await render({ source: "s", target: "t", storyFrom: 2, storyTo: 3 }, []);
    expect(byText("h3", "剧情顺序线")).toBeTruthy();
    expect(container.textContent).toContain("第 2 段 → 第 3 段");
    expect(container.textContent).toContain("按对话剧情顺序自动派生（楼层增删后自动更新），非手动连线，不可删除。");
    expect(container.textContent).not.toContain("引用");
  });

  it("引用分支：方向文案 + 两侧标题", async () => {
    await render({ source: "s", target: "t" }, []);
    expect(container.textContent).toContain("t → 引用 → s");
    expect(container.textContent).toContain("◀ source（被引用）：");
    expect(container.textContent).toContain("▶ target（引用者）：");
    expect(container.textContent).toContain("语义：如视频引用底图、图生图引用参考图。当前为视觉连线，references 数据模型未与生成链路打通。");
  });

  it("promptOf：data.prompt 优先", async () => {
    await render({ source: "s", target: "t" }, [
      nodeOf("s", { prompt: "源提示词", node: { prompt: "内层源" } }),
      nodeOf("t", { prompt: "目标提示词", node: { prompt: "内层目标" } }),
    ]);
    expect(container.textContent).toContain("源提示词");
    expect(container.textContent).toContain("目标提示词");
    expect(container.textContent).not.toContain("内层源");
    expect(container.textContent).not.toContain("内层目标");
  });

  it("promptOf：data.prompt 缺失时回退 data.node.prompt", async () => {
    await render({ source: "s", target: "t" }, [
      nodeOf("s", { node: { prompt: "内层源" } }),
      nodeOf("t", { node: { prompt: "内层目标" } }),
    ]);
    expect(container.textContent).toContain("内层源");
    expect(container.textContent).toContain("内层目标");
    expect(container.textContent).not.toContain("（无提示词）");
  });

  it("promptOf：两处都缺 → 「（无提示词）」", async () => {
    await render({ source: "s", target: "t" }, [nodeOf("s", {}), nodeOf("t", {})]);
    expect(count(container.textContent || "", "（无提示词）")).toBe(2);
  });

  it("节点不存在 → 「（节点已不存在）」", async () => {
    await render({ source: "s", target: "t" }, []);
    expect(count(container.textContent || "", "（节点已不存在）")).toBe(2);
  });

  it("「知道了」与遮罩 → setEdgeInfo(null)；内层不冒泡", async () => {
    const api = await render({ source: "s", target: "t" }, []);
    await click(modal());
    expect(api.setEdgeInfo).not.toHaveBeenCalled();
    await click(btn("知道了"));
    expect(api.setEdgeInfo).toHaveBeenCalledWith(null);
    api.setEdgeInfo.mockClear();
    await click(mask());
    expect(api.setEdgeInfo).toHaveBeenCalledWith(null);
  });
});

// ───────────────────────── ⑥ 逐字搬移锁 ─────────────────────────
describe("逐字搬移锁（T2.1-D1）", () => {
  const MOVED: [string, number][] = [
    ["deletePending.story > 0", 2],                 // message 分支 + onConfirm toast 分支
    ["deletePending.projected > 0", 1],             // message 第二段分支
    ["edgeInfo.storyFrom !== undefined", 1],        // 两分支判定
    ['n?.data?.prompt || n?.data?.node?.prompt || "（无提示词）"', 1],
    ["pushUndo();", 1],
    ["deleteNodes(deletePending.ids);", 1],
    ["setDeletePending(null)", 2],                 // onConfirm + onCancel
    ['title="从画布删除节点"', 1],
    ['confirmText="删除"', 1],
    ["setEditLabelId(null)", 2],                    // 遮罩 + 取消
    ["setNewInspiration(null)", 2],                 // 遮罩 + 取消
    ["setGroupRenameId(null)", 2],                  // 遮罩 + 取消
    ["setEdgeInfo(null)", 2],                       // 遮罩 + 知道了
    ["rfNodes.find((n) => n.id === edgeInfo.source)", 1],
    ["<h3>剧情顺序线</h3>", 1],
  ];

  it("15 条判定/副作用语句在新属主按次数出现", () => {
    const seam = stripComments(read(SEAM));
    for (const [needle, n] of MOVED) {
      expect([needle, count(seam, needle)]).toEqual([needle, n]);
    }
  });

  it("同样的语句在视图缺席（确属搬移，而非复制）", () => {
    const view = stripComments(read(VIEW));
    const gone = [
      "deletePending.story > 0", "deletePending.projected > 0", "edgeInfo.storyFrom !== undefined",
      'n?.data?.prompt || n?.data?.node?.prompt || "（无提示词）"',
      "deleteNodes(deletePending.ids);", 'title="从画布删除节点"', 'confirmText="删除"',
      "setEditLabelVal(e.target.value)", "setGroupRenameTitle(e.target.value)",
      "setNewInspiration({ ...newInspiration, title: e.target.value })",
      "setNewInspiration({ ...newInspiration, content: e.target.value })",
      "onClick={saveEditLabel}", "onClick={saveGroupRename}", "onClick={createInspiration}",
      "<h3>编辑卡片介绍文字</h3>", "<h3>新建灵感卡</h3>", "<h3>编辑组名</h3>", "<h3>剧情顺序线</h3>",
    ];
    for (const needle of gone) {
      expect([needle, count(view, needle)]).toEqual([needle, 0]);
    }
  });
});

// ───────────────────────── ⑦ 源码形态锁 ─────────────────────────
describe("源码形态锁（T2.1-D1）", () => {
  it("5 个组件在视图各恰 1 次（守卫留在视图，组件恒返回本体）", () => {
    const view = read(VIEW);
    for (const c of ["EditLabelDialog", "NewInspirationDialog", "GroupRenameDialog", "DeletePendingDialog", "EdgeInfoDialog"]) {
      expect([c, count(view, "<" + c)]).toEqual([c, 1]);
      // 自闭合调用点 ⇒ 不应出现闭合标签
      expect([c, count(view, "</" + c + ">")]).toEqual([c, 0]);
    }
  });

  it("5 条守卫 + 5 个调用块逐字保留", () => {
    const view = read(VIEW);
    const blocks = [
      [
        "      {editLabelId && (",
        "        <EditLabelDialog",
        "          editLabelVal={editLabelVal}",
        "          setEditLabelVal={setEditLabelVal}",
        "          setEditLabelId={setEditLabelId}",
        "          saveEditLabel={saveEditLabel}",
        "        />",
        "      )}",
      ],
      [
        "      {newInspiration && (",
        "        <NewInspirationDialog",
        "          newInspiration={newInspiration}",
        "          setNewInspiration={setNewInspiration}",
        "          createInspiration={createInspiration}",
        "        />",
        "      )}",
      ],
      [
        "      {groupRenameId && (",
        "        <GroupRenameDialog",
        "          groupRenameTitle={groupRenameTitle}",
        "          setGroupRenameTitle={setGroupRenameTitle}",
        "          setGroupRenameId={setGroupRenameId}",
        "          saveGroupRename={saveGroupRename}",
        "        />",
        "      )}",
      ],
      [
        "      {deletePending && (",
        "        <DeletePendingDialog",
        "          deletePending={deletePending}",
        "          pushUndo={pushUndo}",
        "          deleteNodes={deleteNodes}",
        "          setDeletePending={setDeletePending}",
        "          showToast={showToast}",
        "        />",
        "      )}",
      ],
      [
        "      {edgeInfo && (",
        "        <EdgeInfoDialog",
        "          edgeInfo={edgeInfo}",
        "          setEdgeInfo={setEdgeInfo}",
        "          rfNodes={rfNodes}",
        "        />",
        "      )}",
      ],
    ];
    for (const b of blocks) {
      expect(view.includes(b.join("\n"))).toBe(true);
    }
  });

  it("5 个覆盖层的内联体已离开视图", () => {
    const view = stripComments(read(VIEW));
    for (const needle of [
      '<div className="modal-mask" onClick={() => setEditLabelId(null)}>',
      '<div className="modal-mask" onClick={() => setNewInspiration(null)}>',
      '<div className="modal-mask" onClick={() => setGroupRenameId(null)}>',
      '<div className="modal-mask" onClick={() => setEdgeInfo(null)}>',
    ]) {
      expect([needle, count(view, needle)]).toEqual([needle, 0]);
    }
  });

  it("import 行逐字（5 个组件从新属主引入）", () => {
    const view = read(VIEW);
    expect(view.includes(
      'import {\n  DeletePendingDialog, EdgeInfoDialog, EditLabelDialog, GroupRenameDialog, NewInspirationDialog,\n} from "../components/canvas/CanvasDialogs";',
    )).toBe(true);
  });

  it("ConfirmModal 的死 import 已随搬移清除", () => {
    const view = read(VIEW);
    expect(count(view, "ConfirmModal")).toBe(0);
    expect(count(read(SEAM), 'import { ConfirmModal } from "../Modal";')).toBe(1);
  });

  it("新属主导出面 = 5 组件 + 3 数据面类型", () => {
    const seam = read(SEAM);
    for (const n of [
      "export function EditLabelDialog", "export function NewInspirationDialog", "export function GroupRenameDialog",
      "export function DeletePendingDialog", "export function EdgeInfoDialog",
      "export type CanvasEdgeInfo", "export type CanvasNewInspiration", "export type CanvasDeletePending",
    ]) {
      expect([n, count(seam, n)]).toEqual([n, 1]);
    }
    expect(count(seam, "export function")).toBe(5);
  });
});
