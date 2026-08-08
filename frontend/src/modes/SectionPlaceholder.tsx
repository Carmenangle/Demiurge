import { SECTION_SUBNAV, type NavSection } from "../lib/viewRouting";

// 管理类钻入后的内容区占位。左栏已换成该类子项+返回，这里按当前子项显示占位。
// 生成内容(assets/generations)将来按大/小仓库分层展示。
export function SectionPlaceholder({
  section,
  subView,
}: {
  section: NavSection;
  subView: string | null;
}) {
  if (section === "home") return null;
  const items = SECTION_SUBNAV[section];
  const current = items.find((i) => i.id === subView) ?? items[0];
  return (
    <div className="section-content">
      <div className="section-empty">
        <p>{current.label}</p>
        <small>{hintFor(current.id)}</small>
      </div>
    </div>
  );
}

function hintFor(id: string): string {
  const map: Record<string, string> = {
    works: "小仓库：一部作品含剧情/图片/会话（按大仓库分组）",
    "character-cards": "绑 LoRA / 参考图，管一致性",
    worldbook: "走 RAG",
    generations: "生成的图/视频 + 提示词，按大/小仓库分层",
    templates: "工作流模板库",
    "ai-build": "AI 按需求搭工作流",
    "node-index": "节点知识库",
    models: "模型下载",
    "lora-data": "保存 LoRA 触发词、作者建议提示词与建议权重",
    "node-manager": "节点管理",
    tools: "多功能工具",
  };
  return map[id] ?? "（占位）";
}
