/** manual e2e 用入口页（链路③ 端到端可视化验证）。
 *
 *  用**真实** CollectionArtifacts 组件 + **真实运行中的后端**，验证
 *  「文档产物 → 预览弹层 → Markdown 渲染 → 相对插图解析成可访问素材 URL → 图片真的显示」。
 *
 *  参数经 URL query 传入（outputDir / repoId / docPath / docName / docSize），便于 e2e 动态造 fixture。
 *  `repoId`（2026-09-10 B3/A）：产物域已收窄到**作品域**（仓库/小仓库文件夹），预览/插图/下载
 *  端点都要带它才能解析出正确的域——spec 把 fixture 造在 `<outputDir>/<作品文件夹>/docs/` 下。
 *  手动运行方式见 docs/tech-manual/F-frontend/15-frontend-views-tools.md。
 */
import { createRoot } from "react-dom/client";
import { CollectionArtifacts } from "./components/chat/CollectionArtifacts";
import type { ArtifactMeta } from "./types/chat";
import "./styles.css";
import "./styles/quick-tools.css";

const q = new URLSearchParams(window.location.search);
const outputDir = q.get("outputDir") || "";
const repoId = q.get("repoId") || "";
const docPath = q.get("docPath") || "";

// 与后端 `artifact_display_name` 同口径（文档 · <文件名主名>），便于人工核对卡片标题
const stem = (docPath.split(/[\\/]/).pop() || "").replace(/\.[^.]+$/, "");
const items: ArtifactMeta[] = docPath
  ? [{ kind: "doc", name: `文档 · ${stem}`, path: docPath, size: Number(q.get("docSize") || 0) }]
  : [];

createRoot(document.getElementById("root") as HTMLElement).render(
  <div style={{ padding: 24, minHeight: "100vh" }}>
    <div style={{ maxWidth: 760 }}>
      <h3 style={{ margin: "0 0 6px" }}>链路③ 端到端可视化验证</h3>
      <p style={{ margin: "0 0 16px", opacity: 0.7, fontSize: 13 }}>
        真实组件 CollectionArtifacts + 真实后端。点击卡片查看 Markdown 预览与插图。
      </p>
      <CollectionArtifacts artifacts={items} outputDir={outputDir} repoId={repoId} />
    </div>
  </div>,
);
