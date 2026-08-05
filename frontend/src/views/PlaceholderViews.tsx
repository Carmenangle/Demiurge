import { PageShell, EmptyState } from "../components/layout/PageShell";
import { useRepos } from "../stores/repos";
import { resolvedEmbedModel, useSettings } from "../stores/settings";
import { RepoGallery } from "../components/RepoGallery";

// 资产库：全站聚合。列出所有仓库(含小仓库)的生成图，支持按标签/仓库名搜索、从新到旧、
// 批量删除、标签统计排序、发送至对话(选仓库)。
export function AssetsView({ onSendToChat }: { onSendToChat: (url: string) => void }) {
  const { repos } = useRepos();
  const { settings } = useSettings();
  const repoIds = repos.map((r) => r.id);
  const repoNames = Object.fromEntries(repos.map((r) => [r.id, r.name]));
  return (
    <PageShell title="资产库">
      {repoIds.length === 0 ? (
        <EmptyState>还没有任何仓库，生成的图片会自动出现在这里。</EmptyState>
      ) : (
        <RepoGallery repoIds={repoIds} embed={resolvedEmbedModel(settings)} repoNames={repoNames} hideTitle
          enhanced onSendToChat={(g) => onSendToChat(g.image_url)} />
      )}
    </PageShell>
  );
}
