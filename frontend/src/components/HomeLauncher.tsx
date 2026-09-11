// HomeLauncher.tsx — 创作平台首页：三张入口卡（角色卡 / 世界书 / 预设）。
// 取代旧「最近使用的作品」存档列表；作品入口统一走顶部 仓库▾/作品▾ 与「资产管理 → 作品」。
import { BookOpen, SlidersHorizontal, Users } from "lucide-react";
import { PageShell } from "./layout/PageShell";

export function HomeLauncher({
  onCharacterCards,
  onWorldbook,
  onPreset,
}: {
  onCharacterCards: () => void;
  onWorldbook: () => void;
  onPreset: () => void;
}) {
  const cards = [
    {
      key: "character",
      title: "角色卡",
      desc: "角色卡源库：导入 / 预览 / 用卡新建作品",
      icon: <Users size={26} />,
      color: "#a855f7",
      onClick: onCharacterCards,
    },
    {
      key: "worldbook",
      title: "世界书",
      desc: "独立世界书与卡内嵌世界书，走 RAG 检索",
      icon: <BookOpen size={26} />,
      color: "#22c55e",
      onClick: onWorldbook,
    },
    {
      key: "preset",
      title: "预设",
      desc: "偏置预设：导入 / 查看 / 编辑 / 激活",
      icon: <SlidersHorizontal size={26} />,
      color: "#3b82f6",
      onClick: onPreset,
    },
  ];
  return (
    <PageShell title="创作平台">
      <p className="field-hint home-launcher-tip">
        选择要管理的创作素材；仓库与作品仍可在顶部直接切换。
      </p>
      <div className="home-launcher-cards">
        {cards.map((c) => (
          <button
            key={c.key}
            type="button"
            className="home-launcher-card"
            onClick={c.onClick}
            title={c.desc}
          >
            <span className="home-launcher-icon" style={{ background: c.color }}>
              {c.icon}
            </span>
            <span className="home-launcher-name">{c.title}</span>
            <span className="home-launcher-desc">{c.desc}</span>
          </button>
        ))}
      </div>
    </PageShell>
  );
}
