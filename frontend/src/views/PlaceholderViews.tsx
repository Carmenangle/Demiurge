import { PageShell, EmptyState } from "../components/layout/PageShell";
import { useRepos } from "../stores/repos";
import { resolvedEmbedModel, useSettings } from "../stores/settings";
import { RepoGallery, type GenWithRepo } from "../components/RepoGallery";

// 资产库：全站聚合。列出所有仓库(含小仓库)的生成图，支持按标签/仓库名搜索、从新到旧、
// 批量删除、标签统计排序、发送至对话(选仓库)。
// ⚠ 2026-10-18 用户定案：上网素材**不并入本页**——素材归「上网素材」页管（文件/
// provenance/市场搜索）；其 RAG 资产记录仍在合成库 `repo_web-materials`（source=
// web/pixiv/danbooru），留作后续「上网素材页接检索」时复用，不进本聚合列表。
export function AssetsView({ onSendToChat, onSendAsRecipe, onBatchSendToCanvas }: {
  /** 传完整生成记录（此前只传 image_url ⇒ 溯源元数据在发送链路上丢失） */
  onSendToChat: (g: GenWithRepo) => void;
  onSendAsRecipe?: (g: GenWithRepo) => void;
  onBatchSendToCanvas?: (items: GenWithRepo[]) => void;
}) {
  const { repos } = useRepos();
  const { settings } = useSettings();
  const repoIds = repos.map((r) => r.id);
  const repoNames = Object.fromEntries(repos.map((r) => [r.id, r.name]));
  return (
    <PageShell title="资产库">
      {/* 空态只看真实仓库（repoIds 即全部真实仓库） */}
      {repos.length === 0 ? (
        <EmptyState>还没有任何仓库，生成的图片会自动出现在这里。</EmptyState>
      ) : (
        <RepoGallery repoIds={repoIds} embed={resolvedEmbedModel(settings)} repoNames={repoNames} hideTitle
          enhanced onSendToChat={onSendToChat}
          onSendAsRecipe={onSendAsRecipe}
          onBatchSendToCanvas={onBatchSendToCanvas} />
      )}
    </PageShell>
  );
}
