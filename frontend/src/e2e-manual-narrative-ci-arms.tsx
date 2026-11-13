/** manual e2e 用入口页（清单 #3／#4 无书诊断口径，2026-11-04）。
 *
 *  用**真实** NarrativeCiPanel + **真实运行中的后端**，验用户在聊天页打开「诊断」那一屏时
 *  真看得见「这一臂本轮没参与」——此前世界书缺席会退化成一句「暂无未处置的一致性诊断」，
 *  与「查过、没问题」在界面上逐字同形（绿是空转出来的）。
 *
 *  参数经 URL query 传入（outputDir / repoId），spec 侧强制隔离仓库根。
 */
import { createRoot } from "react-dom/client";
import { NarrativeCiPanel } from "./components/chat/NarrativeCiPanel";
import "./styles.css";

const q = new URLSearchParams(window.location.search);
const outputDir = q.get("outputDir") || "";
const repoId = q.get("repoId") || "";

createRoot(document.getElementById("root") as HTMLElement).render(
  <div className="app" style={{ padding: 24, minHeight: "100vh" }}>
    <div style={{ maxWidth: 760 }}>
      <h3 style={{ margin: "0 0 6px" }}>清单 #3／#4 · 无书诊断口径 · 端到端可视化验证</h3>
      <p style={{ margin: "0 0 16px", opacity: 0.7, fontSize: 13 }}>
        真实组件 NarrativeCiPanel + 真实后端 GET /narrative/ci。repo={repoId || "（空）"}
        仓库根={outputDir || "（空）"}
      </p>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <NarrativeCiPanel outputDir={outputDir} repoId={repoId} />
      </div>
    </div>
  </div>,
);
