/** manual e2e 用入口页（#9 正文外貌锚审核区，2026-11-04）。
 *
 *  用**真实** MediaInsertModal + **真实运行中的后端**，验证用户在浏览器里真能走完这条确认闸：
 *  打开多元数据插入弹窗 → 展开「正文外貌锚」→ 重新扫描正文（抽候选）→ 采纳（入锚）→
 *  锚块进成图资料。这一段只在 SSR 单测里证过结构，真实 fetch／渲染／点击链必须真机走一遍。
 *
 *  参数经 URL query 传入（outputDir / repoId / cardNames），spec 自己造会话快照 fixture。
 *  ⚠ outputDir 必须是**隔离仓库根**（spec 侧强制要求显式传入），否则会往用户真实作品库写锚库文件。
 */
import { createRoot } from "react-dom/client";
import { MediaInsertModal } from "./components/MediaInsertModal";
import "./styles.css";
import "./styles/quick-tools.css";

const q = new URLSearchParams(window.location.search);
const outputDir = q.get("outputDir") || "";
const repoId = q.get("repoId") || "";
const cardNames = (q.get("cardNames") || "")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);

createRoot(document.getElementById("root") as HTMLElement).render(
  <div style={{ padding: 24, minHeight: "100vh" }}>
    <div style={{ maxWidth: 760 }}>
      <h3 style={{ margin: "0 0 6px" }}>#9 正文外貌锚 · 端到端可视化验证</h3>
      <p style={{ margin: "0 0 16px", opacity: 0.7, fontSize: 13 }}>
        真实组件 MediaInsertModal + 真实后端。repo={repoId || "（空）"} 仓库根={outputDir || "（空）"}
      </p>
      <MediaInsertModal
        templates={[]}
        modelsDir=""
        repoId={repoId}
        outputDir={outputDir}
        cardNames={cardNames}
        preset={{ templateId: "", appearanceSource: "worldbook" }}
        onSave={() => {}}
        onClose={() => {}}
      />
    </div>
  </div>,
);
