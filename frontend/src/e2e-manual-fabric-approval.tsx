/** manual e2e 用入口页（智能编造审批卡，2026-11-05 T3 验收）。
 *
 *  挂**真实 ChatView**（不是复刻的假界面）+ 真实隔离后端：用户在对话页看到的那条审批提示、
 *  那颗「✅ 批准继续 / ✖ 取消中止」就是本组件里的真 DOM 与真回调。
 *  单测（jsdom + renderToStaticMarkup）证不了三件只在真浏览器成立的事：
 *  ① 审批条的 CSS 真的把它画出来（`toBeVisible`）；② 模型写的参数被当**文本**渲染，
 *  注入的 `<img onerror>` 不生成元素也不执行；③ 内容里的 ``` 不会提前闭合围栏。
 *
 *  参数经 URL query 传入（repoId / repoName）。会话文本由 spec 侧经真实快照端点播种，
 *  内容是**后端生产接缝**产出的待批句子（不是手抄），因此本入口页不接收任何路径参数。
 */
import { createRoot } from "react-dom/client";
import { ChatView } from "./views/ChatView";
import { useSettings } from "./stores/settings";
import type { Repo } from "./stores/repos";
import "./styles.css";
import "./styles/quick-tools.css";

const q = new URLSearchParams(window.location.search);
// 会话线 id＝仓库 id（ChatView 用 threadKey(repo.id)）。合成名，spec 每次跑换新 id。
const repoId = q.get("repoId") || "";
const repoName = q.get("repoName") || "会话甲";

function Harness() {
  const { settings, update, addStylePreset, updateStylePreset, removeStylePreset } = useSettings();
  const repo: Repo = { id: repoId, name: repoName, createdAt: 0 };
  const noop = () => {};
  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <ChatView
        repo={repo}
        settings={settings}
        update={update}
        presets={{ addStylePreset, updateStylePreset, removeStylePreset }}
        setCover={noop}
        setGeneratedCover={noop}
      />
    </div>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<Harness />);
