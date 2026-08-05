import { useState } from "react";
import { ArrowLeftRight, Braces, CaseSensitive, Eraser, Film, Grid3x3, Palette, Scaling, Sparkles, Tags, TextQuote, Wrench } from "lucide-react";
import { PageShell, EmptyState } from "../components/layout/PageShell";
import { LoraTriggersTab } from "./tools/LoraTriggersTab";
import { GifToSpriteTab } from "./tools/GifToSpriteTab";
import { SpriteToGifTab } from "./tools/SpriteToGifTab";
import { PaletteTab } from "./tools/PaletteTab";
import { ResizeTab } from "./tools/ResizeTab";
import {
  ChineseConvertTab, TextCleanTab, TextEscapeTab, TextInsertTab, TextJoinTab, TextStatsTab,
} from "./tools/TextToolsTabs";

// 多功能工具：非核心但偶尔要用的小工具集中在此，避免各自占一个侧栏栏目。
// 新增工具 = 往 TOOLS 里加一项 + 在下方 tab 渲染处加一行条件渲染。
type Tool = "lora-triggers" | "gif-to-sprite" | "sprite-to-gif" | "palette" | "resize"
  | "text-clean" | "text-join" | "text-insert" | "text-stats" | "text-escape" | "chinese-convert";
const TOOLS: { key: Tool; label: string; desc: string; icon: typeof Wrench }[] = [
  {
    key: "lora-triggers",
    label: "LoRA 数据保存",
    desc: "保存 LoRA 触发词与建议权重，自动生成时随模型切换",
    icon: Tags,
  },
  {
    key: "gif-to-sprite",
    label: "GIF 转精灵图",
    desc: "拆开 GIF 逐帧，剔掉不要的帧后按网格拼成一张精灵图",
    icon: Grid3x3,
  },
  {
    key: "sprite-to-gif",
    label: "精灵图转 GIF",
    desc: "按行列切开精灵图，挑帧、调帧率后合成 GIF",
    icon: Film,
  },
  {
    key: "palette",
    label: "调色盘",
    desc: "从图片提取主色，可设为当前配色让 AI 生图自动沿用",
    icon: Palette,
  },
  {
    key: "resize",
    label: "分辨率缩放",
    desc: "2K 转 1K 这类等比缩放，Lanczos 重采样尽量不糊",
    icon: Scaling,
  },
  { key: "text-clean", label: "文本清理", desc: "移除 Markdown 标记、空行并保留可见正文", icon: Eraser },
  { key: "text-join", label: "文本拼接", desc: "按自定义分隔符拼接多行文本", icon: ArrowLeftRight },
  { key: "text-insert", label: "文本加料", desc: "在每两个字符之间插入指定字符串", icon: Sparkles },
  { key: "text-stats", label: "字数统计", desc: "实时统计中日文、英文单词、标点和字符", icon: CaseSensitive },
  { key: "text-escape", label: "文本转义", desc: "UTF-8 字符串、Python 字节、Hex 与 JSON 转换", icon: Braces },
  { key: "chinese-convert", label: "简繁切换", desc: "繁简中文双向转换与引号替换", icon: TextQuote },
];

export function ToolsView({ repoId }: { repoId: string }) {
  const [tool, setTool] = useState<Tool | null>(null);

  // 选中某个工具后进入该工具自己的页面（自带 PageShell 与返回）。
  // 加新工具时这里也要加一行，否则会渲染成别的工具。
  const back = () => setTool(null);
  if (tool === "lora-triggers") return <LoraTriggersTab onBack={back} />;
  if (tool === "gif-to-sprite") return <GifToSpriteTab onBack={back} />;
  if (tool === "sprite-to-gif") return <SpriteToGifTab onBack={back} />;
  if (tool === "palette") return <PaletteTab onBack={back} repoId={repoId} />;
  if (tool === "resize") return <ResizeTab onBack={back} />;
  if (tool === "text-clean") return <TextCleanTab onBack={back} />;
  if (tool === "text-join") return <TextJoinTab onBack={back} />;
  if (tool === "text-insert") return <TextInsertTab onBack={back} />;
  if (tool === "text-stats") return <TextStatsTab onBack={back} />;
  if (tool === "text-escape") return <TextEscapeTab onBack={back} />;
  if (tool === "chinese-convert") return <ChineseConvertTab onBack={back} />;

  return (
    <PageShell title="多功能工具">
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
        平时用不到、但需要时很省事的小工具。
      </p>
      <div className="market-grid">
        {TOOLS.map((t) => (
          <button key={t.key} className="tool-card" onClick={() => setTool(t.key)}>
            <t.icon size={20} />
            <strong>{t.label}</strong>
            <span>{t.desc}</span>
          </button>
        ))}
      </div>
      {TOOLS.length === 0 && (
        <EmptyState icon={<Wrench size={28} />}>还没有可用的工具。</EmptyState>
      )}
    </PageShell>
  );
}
