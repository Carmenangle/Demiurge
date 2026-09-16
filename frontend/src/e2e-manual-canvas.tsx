/** manual e2e 用入口页（画布「运转工作流」占位链路）。
 *
 *  挂载**真实** `views/CanvasStageFlow`（1600+ 行、无 jsdom 根的组件）＋**真实后端**，
 *  用来验证「运转事件 → 画布占位节点出现 → done 后消失」这条**跨组件链路** ——
 *  它正是 2026-09-15 第二轮审计里 P10（编排链单一属主 `runCanvasWorkflow`）、
 *  P12（列表归约单一属主 `wfRunList`，本页覆盖画布侧 `wfRuns` 那一份）、
 *  P19（终态表执行口 → `sink.dispatchDone` 派发 `laf-canvas-wf-done`）的**共同验收面**：
 *  这三条契约都**不进 vitest 渲染面**（组件无 jsdom 测试），只能靠真浏览器。
 *
 *  ⚠ 本页**不真的向 ComfyUI 提交图**（那需要模板 + 模型 + ComfyUI 在跑，且会真出图）。
 *  spec 改为直接派发画布监听的四个事件（`laf-canvas-wf-run` / `-run-graph` /
 *  `-progress` / `-done`）—— 这四个事件正是真实运转链**在提交后**派发的同一批
 *  （派发方 `lib/runCanvasWorkflow.ts` + `sink.dispatchDone`），而**消费方是真组件本体**，
 *  所以「事件 → DOM」这一段是真链路，没有被复刻。
 *
 *  参数经 URL query：`repoId`（决定 threadKey / pending 存储键）、`outputDir`（透传给画布设置）。
 *  手动打开方式：启动真实前后端后访问
 *  `/e2e-manual/canvas-workflow.html?repoId=e2e-canvas-repo`，再在控制台派发上述事件即可观察占位。
 */
import { createRoot } from "react-dom/client";
import { CanvasStageFlow } from "./views/CanvasStageFlow";
import { useSettings } from "./stores/settings";
import type { Repo } from "./stores/repos";
import "./styles.css";
import "./styles/quick-tools.css";

const q = new URLSearchParams(window.location.search);
const repoId = q.get("repoId") || "e2e-canvas-repo";

/** 最小 Repo（画布只用 `repo?.id` 推 threadId）；其余字段在真实数据里都是可选。 */
const repo: Repo = { id: repoId, name: "e2e-canvas", createdAt: 0 };

function App() {
  // 真实 settings hook（挂载时拉后端存档；后端离线则退回 localStorage 默认值，不阻塞渲染）
  const { settings } = useSettings();
  return (
    // 版式必须与生产一致（ChatView：竖向 flex 根 → .chat-layout → .chat-col → 画布）：
    // 画布组件根是 `flex:1; min-height:0; position:relative`，**靠 flex 列父级撑高**；
    // 宿主若不是 flex 列，它高度为 auto → 内部全是绝对定位 → 实测高度 0 → `.react-flow` 不可见。
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <div className="chat-layout">
        <div className="chat-col" id="e2e-canvas-host" data-repo-id={repoId}>
          <CanvasStageFlow
            repo={repo}
            settings={settings}
            messages={[]}
            // 占位链路不走到归档（不真提交 ComfyUI）⇒ 给一个永真桩；
            // 真归档链的验证属链路③（产出与持久化），不在本 spec 的范围。
            onFinalizeWorkflow={async () => true}
          />
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<App />);
