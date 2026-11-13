/** manual e2e 用入口页（④ 世界书整本删除「确认闸 + 先备份再删」，2026-11-05）。
 *
 *  挂**真实 WorldBook 页**（不是复刻的假界面）：用户在「世界书」页书单行尾点垃圾桶、
 *  在确认弹窗点「删除」，走的就是这个组件里的真按钮、真 /worldbook/delete 端点、真备份。
 *  单测能证 service 先备份再删，证不了「备份文件名真的显示在用户眼前那一行上」。
 *
 *  目录经 URL query 传入（worldbookDir / characterDir），由 spec 运行时从后端 `/api/state`
 *  取「配置的世界书文件夹」再拼——本机绝对路径不进源码。characterDir 留空 ⇒ 只验独立世界书，
 *  不碰用户角色卡目录。
 */
import { createRoot } from "react-dom/client";
import { WorldBook } from "./pages/WorldBook";
import "./styles.css";
import "./styles/quick-tools.css";

const q = new URLSearchParams(window.location.search);
const worldbookDir = q.get("worldbookDir") || "";
const characterDir = q.get("characterDir") || "";

createRoot(document.getElementById("root") as HTMLElement).render(
  <div style={{ height: "100vh", overflow: "auto" }}>
    <WorldBook characterDir={characterDir} worldbookDir={worldbookDir} />
  </div>,
);
