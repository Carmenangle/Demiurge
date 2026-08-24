import { useEffect, useState } from "react";
import { Palette, FolderCog, BrainCircuit, Sparkles, Blocks, Puzzle, KeyRound, Bot, Settings2 } from "lucide-react";
import { useRef } from "react";
import { applyTheme, exportSettings, importSettings, type Settings } from "../../stores/settings";
import { saveComfyConfig } from "../../api/comfyui";
import {
  auditOutputPath, migrateOutputPath, type OutputPathAudit,
} from "../../api/assets";
import { AlertModal, ConfirmModal } from "../../components/Modal";
import { GeneralPanel } from "./GeneralPanel";
import { PathsPanel } from "./PathsPanel";
import { ModelsPanel } from "./ModelsPanel";
import { StylePanel } from "./StylePanel";
import { McpPanel } from "./McpPanel";
import { SkillsPanel } from "./SkillsPanel";
import { TokensPanel } from "./TokensPanel";
import { AgentPanel } from "./AgentPanel";
import { MiscPanel } from "./MiscPanel";

interface Props {
  settings: Settings;
  update: (patch: Partial<Settings>) => void;
  onOutputPathMigrated?: (oldDir: string, newDir: string) => void;
}

// 左侧导航项：分组 + 图标。id 对应右侧渲染的面板。
type NavId = "general" | "agent" | "paths" | "models" | "style" | "mcp" | "skills" | "tokens" | "misc";
const NAV: { group: string; items: { id: NavId; label: string; icon: typeof Palette }[] }[] = [
  { group: "常规", items: [
    { id: "general", label: "外观", icon: Palette },
    { id: "agent", label: "智能体", icon: Bot },
    { id: "paths", label: "路径与代理", icon: FolderCog },
    { id: "misc", label: "通用", icon: Settings2 },
  ] },
  { group: "模型与服务", items: [
    { id: "models", label: "模型", icon: BrainCircuit },
    { id: "style", label: "生图风格", icon: Sparkles },
  ] },
  { group: "扩展", items: [
    { id: "mcp", label: "MCP 服务器", icon: Blocks },
    { id: "skills", label: "技能扩展", icon: Puzzle },
  ] },
  { group: "凭证", items: [
    { id: "tokens", label: "下载令牌", icon: KeyRound },
  ] },
];

function sizeText(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function SettingsView({ settings, update, onOutputPathMigrated }: Props) {
  // 草稿：编辑都改这里，保存才写回，取消则丢弃（与原 Modal 一致）
  const [draft, setDraft] = useState<Settings>(settings);
  const [active, setActive] = useState<NavId>("general");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pathReview, setPathReview] = useState<{
    audit: OutputPathAudit;
    next: Settings;
    oldDir: string;
    newDir: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportKeys, setExportKeys] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const onImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";  // 允许重复选同一文件
    if (!file) return;
    try {
      const next = importSettings(await file.text());
      setDraft(next);  // 回填草稿：用户可复核后再点保存
    } catch (reason) {
      setError(`导入失败：${(reason as Error).message}`);
    }
  };

  const onExport = () => {
    const json = exportSettings(settings, exportKeys);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `demiurge-settings${exportKeys ? "-with-keys" : ""}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // 主题在设置页即时预览；未保存离开时恢复当前已保存主题。
  useEffect(() => {
    applyTheme(draft.theme);
    return () => applyTheme(settings.theme);
  }, [draft.theme, settings.theme]);

  const commitSave = (next: Settings, migrated?: { oldDir: string; newDir: string }) => {
    if (migrated) onOutputPathMigrated?.(migrated.oldDir, migrated.newDir);
    update(next);
    // 同步 ComfyUI 路径/地址到后端，供 start-dev 脚本读取
    saveComfyConfig(next.comfyuiPath, next.comfyuiUrl, next.comfyuiPython).catch(() => {});
    setSaved(true);
    setTimeout(() => setSaved(false), 1800);
  };

  const onSave = async () => {
    if (busy) return;
    const oldDir = settings.outputDir.trim();
    const newDir = draft.outputDir.trim();
    if (oldDir === newDir) {
      commitSave(draft);
      return;
    }
    if (!oldDir) {
      commitSave(draft);
      return;
    }
    setBusy(true);
    try {
      const audit = await auditOutputPath(oldDir, newDir);
      if (!audit.changed) {
        commitSave(draft);
        return;
      }
      if (audit.file_count === 0) {
        // 无可迁移文件（作品文件夹为空/不存在）。仅当还有裂图记录才拦截，否则直接切换。
        if (audit.asset_count === 0 && audit.missing_count > 0) {
          setError(`旧路径下没有可迁移的文件，并有 ${audit.missing_count} 条裂图记录。请先在资产库清理缺失记录。`);
        } else {
          commitSave(draft);
        }
        return;
      }
      if (audit.conflict_count > 0) {
        setError(`新路径中有 ${audit.conflict_count} 个同名但内容不同的文件。为避免覆盖，已取消保存，请更换目录或处理冲突。`);
        return;
      }
      setPathReview({ audit, next: { ...draft }, oldDir, newDir });
    } catch (reason) {
      setError(`输出路径审查失败：${(reason as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const confirmMigration = async () => {
    if (!pathReview || busy) return;
    setBusy(true);
    try {
      const result = await migrateOutputPath(pathReview.oldDir, pathReview.newDir);
      const migrated = { oldDir: pathReview.oldDir, newDir: pathReview.newDir };
      const next = pathReview.next;
      setPathReview(null);
      commitSave(next, migrated);
      if (result.delete_failures > 0) {
        setError(`资产已迁移且引用已更新，但有 ${result.delete_failures} 个旧文件未能删除，可稍后手动清理。`);
      }
    } catch (reason) {
      setPathReview(null);
      setError(`资产迁移失败，输出路径未保存：${(reason as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const dirty = JSON.stringify(draft) !== JSON.stringify(settings);

  return (
    <div className="settings-page">
      <aside className="settings-nav">
        {NAV.map((g) => (
          <div className="settings-nav-group" key={g.group}>
            <div className="settings-nav-title">{g.group}</div>
            {g.items.map((it) => (
              <button
                key={it.id}
                className={active === it.id ? "settings-nav-item active" : "settings-nav-item"}
                onClick={() => setActive(it.id)}
              >
                <it.icon size={16} /> {it.label}
              </button>
            ))}
          </div>
        ))}
      </aside>

      <div className="settings-content">
        <div className="settings-content-body">
          {active === "general" && <GeneralPanel draft={draft} setDraft={setDraft} />}
          {active === "agent" && <AgentPanel draft={draft} setDraft={setDraft} />}
          {active === "paths" && <PathsPanel draft={draft} setDraft={setDraft} />}
          {active === "models" && <ModelsPanel draft={draft} setDraft={setDraft} />}
          {active === "style" && <StylePanel draft={draft} setDraft={setDraft} />}
          {active === "mcp" && <McpPanel />}
          {active === "skills" && <SkillsPanel />}
          {active === "tokens" && <TokensPanel draft={draft} setDraft={setDraft} />}
          {active === "misc" && <MiscPanel draft={draft} setDraft={setDraft} />}
        </div>
        <div className="settings-footer">
          <label className="settings-export-opt" title="勾选后导出文件包含 API 密钥与下载令牌明文，请仅在可信环境保存">
            <input type="checkbox" checked={exportKeys} onChange={(e) => setExportKeys(e.target.checked)} />
            保留密钥
          </label>
          <button className="btn" onClick={onExport} title="导出设置为 JSON 文件">导出设置</button>
          <button className="btn" onClick={() => fileRef.current?.click()} title="从 JSON 文件回填设置（回填到草稿，需再点保存）">导入设置</button>
          <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={onImportFile} />
          {dirty && <span className="settings-dirty">有未保存的更改</span>}
          {saved && <span className="settings-saved">已保存</span>}
          <button className="btn" disabled={!dirty || busy} onClick={onSave}>
            {busy && !pathReview ? "审查中…" : "保存"}
          </button>
        </div>
      </div>
      {pathReview && (
        <ConfirmModal
          title="迁移资产库图片？"
          message={`旧路径下有 ${pathReview.audit.asset_count} 条资产记录，共 ${pathReview.audit.file_count} 个可迁移文件（${sizeText(pathReview.audit.total_bytes)}）。切换路径前将完整复制到新目录，并同步资产库、对话记录和仓库封面。${pathReview.audit.missing_count > 0 ? ` 另有 ${pathReview.audit.missing_count} 个文件已经缺失，将保留原记录并跳过。` : ""}`}
          confirmText="迁移并保存"
          busy={busy}
          onConfirm={confirmMigration}
          onCancel={() => setPathReview(null)}
        />
      )}
      {error && <AlertModal title="设置操作未完成" message={error} onClose={() => setError(null)} />}
    </div>
  );
}
