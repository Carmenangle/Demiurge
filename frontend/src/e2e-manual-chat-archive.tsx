/** manual e2e 用入口页（① 对话删除软删层「恢复对话」，2026-11-05）。
 *
 *  挂**真实 ChatView**（不是复刻的假界面）+ 真实隔离后端：用户在对话页头顶工具条上点
 *  「清缓存」→「恢复」这两下，走的就是这个组件里的真按钮、真端点、真二次确认闸。
 *  单测只能证弹窗组件自己的逻辑，证不了「那颗按钮真挂在用户能看到的那一屏上」。
 *
 *  参数经 URL query 传入（repoId / repoName）。仓库根与状态目录都由后端实例自己拿着
 *  （LAF_DATA_DIR / user_state.settings.outputDir），页面不接收路径参数，避免把本机路径写进源码。
 */
import { createRoot } from "react-dom/client";
import { ChatView } from "./views/ChatView";
import { useSettings } from "./stores/settings";
import type { Repo } from "./stores/repos";
import "./styles.css";
import "./styles/quick-tools.css";

const q = new URLSearchParams(window.location.search);
// 会话线 id＝仓库 id（ChatView 用 threadKey(repo.id)）。合成名，且 spec 每次跑换新 id。
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
