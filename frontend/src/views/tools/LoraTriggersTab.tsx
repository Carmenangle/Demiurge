import { useEffect, useRef, useState } from "react";
import { Pencil, RefreshCw, Trash2 } from "lucide-react";
import { PageShell, StateHint } from "../../components/layout/PageShell";
import { ConfirmModal } from "../../components/Modal";
import { resolvedEmbedModel, useSettings } from "../../stores/settings";
import {
  listLoras, syncLoras, getSyncProgress, saveLoraTriggers, deleteLoraTriggers,
  type LoraTriggerItem,
} from "../../api/loras";

// 来源列文案：让用户一眼看出哪些是自动提的、哪些是自己改过的（改过的同步不会被覆盖）
const SOURCE_TEXT: Record<string, string> = {
  metadata: "模型元数据",
  sidecar: "配套信息文件",
  manual: "手动填写",
};

function LoraDataModal({ item, onConfirm, onCancel }: {
  item: LoraTriggerItem;
  onConfirm: (triggers: string, suggestedWeight: number, suggestedPrompt: string) => void;
  onCancel: () => void;
}) {
  const [triggers, setTriggers] = useState(item.triggers.join(", "));
  const [weight, setWeight] = useState(item.suggested_weight ?? 0.8);
  const [suggestedPrompt, setSuggestedPrompt] = useState(item.suggested_prompt || "");
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="modal-mask" onClick={onCancel}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <h3>{item.lora_name}</h3>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4 }}>触发词</span>
          <input autoFocus value={triggers} onChange={(event) => setTriggers(event.target.value)} />
        </label>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4 }}>建议权重</span>
          <input type="number" min={0} max={2} step={0.05} value={weight}
            onChange={(event) => setWeight(Number(event.target.value))} />
        </label>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4 }}>作者建议提示词</span>
          <textarea rows={12} value={suggestedPrompt}
            style={{ width: "100%", minHeight: 280, resize: "vertical", boxSizing: "border-box" }}
            onChange={(event) => setSuggestedPrompt(event.target.value)} />
        </label>
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>取消</button>
          <button className="btn primary"
            onClick={() => onConfirm(triggers.trim(), weight, suggestedPrompt.trim())}>保存</button>
        </div>
      </div>
    </div>
  );
}

export function LoraTriggersTab({ onBack }: { onBack: () => void }) {
  const { settings } = useSettings();
  // 设置里没单独填 models 目录时回退 comfyuiPath/models（对齐 ModelDownload 的回退）
  const modelsDir = settings.modelsDir || (settings.comfyuiPath ? `${settings.comfyuiPath}/models` : "");

  const [items, setItems] = useState<LoraTriggerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<LoraTriggerItem | null>(null);
  const [confirm, setConfirm] = useState<LoraTriggerItem | null>(null);
  const timer = useRef<number | null>(null);

  const load = () => {
    setLoading(true);
    listLoras()
      .then((r) => setItems(r.items))
      .catch((e) => setBusy(`读取失败：${(e as Error).message}`))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  // 卸载时停掉同步进度轮询，避免切页后继续打请求
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  const poll = () => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      let p;
      try { p = await getSyncProgress(); } catch { return; }
      if (p.error) {
        setBusy(`同步失败：${p.error}`);
      } else if (p.running) {
        setBusy(`正在同步 ${p.done}/${p.total}：${p.current}`);
        return;
      } else if (p.finished) {
        setBusy(`同步完成：新增 ${p.added}，更新 ${p.updated}，保留手填 ${p.kept}`
          + (p.missing ? `，${p.missing} 个文件已不在磁盘` : ""));
      }
      if (timer.current) window.clearInterval(timer.current);
      load();
    }, 600);
  };

  const doSync = async (full: boolean) => {
    if (!modelsDir) {
      setBusy("未配置 ComfyUI 路径，无法定位 loras 目录（设置 → 路径）。");
      return;
    }
    setBusy("正在扫描 loras 目录…");
    try {
      const r = await syncLoras(resolvedEmbedModel(settings), modelsDir, full);
      if (r.total === 0) {
        setBusy(`在 ${modelsDir}/loras 下没找到模型文件，请确认路径。`);
        return;
      }
      poll();
    } catch (e) {
      setBusy(`同步失败：${(e as Error).message}`);
    }
  };

  const doSave = async (text: string, suggestedWeight: number, suggestedPrompt: string) => {
    const target = editing;
    setEditing(null);
    if (!target) return;
    // 原样交后端切分：分隔符规则（逗号/顿号/分号，以及 CJK 的空格）只在
    // lora_service.normalize_trigger_words 一处定义，前端不再各切一套。
    try {
      const saved = await saveLoraTriggers(
        resolvedEmbedModel(settings), target.lora_name, [text], target.note,
        suggestedWeight, suggestedPrompt,
      );
      // 局部改 state 而非整表重拉
      setItems((prev) => prev.map((i) => (i.lora_name === saved.lora_name ? saved : i)));
      setBusy(text.trim()
        ? `已保存 ${target.lora_name} 的触发词（此后同步不会覆盖）。`
        : `已将 ${target.lora_name} 标记为通用 LoRA（无需触发词，此后同步不会覆盖）。`);
    } catch (e) {
      setBusy(`保存失败：${(e as Error).message}`);
    }
  };

  const doDelete = async () => {
    const target = confirm;
    setConfirm(null);
    if (!target) return;
    try {
      await deleteLoraTriggers(resolvedEmbedModel(settings), target.lora_name);
      setItems((prev) => prev.filter((i) => i.lora_name !== target.lora_name));
      setBusy(`已删除 ${target.lora_name} 的记录（磁盘上的模型文件未动）。`);
    } catch (e) {
      setBusy(`删除失败：${(e as Error).message}`);
    }
  };

  const noTrigger = items.filter((i) => !i.missing && i.triggers.length === 0 && i.source !== "manual").length;

  return (
    <PageShell
      title="LoRA 数据保存"
      back={onBack}
      actions={
        <>
          <button className="btn" onClick={() => doSync(false)}>
            <RefreshCw size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />智能同步
          </button>
          <button className="btn" onClick={() => doSync(true)} title="连手填的条目也一并重新提取">
            全量重建
          </button>
        </>
      }
    >
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
        同步会扫描 {modelsDir ? `${modelsDir}/loras` : "loras 目录"} 并提取触发词。
        作者建议提示词会筛出质量、风格、镜头与作者签名后合并到正向提示词首段；人物、服装和动作内容不会带入。建议权重会在角色或风格 LoRA 切换时自动填入。
      </p>
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}
      {noTrigger > 0 && (
        <p style={{ fontSize: 12, color: "var(--warning)" }}>
          有 {noTrigger} 个 LoRA 的触发词尚未确认：需要触发词就填写；属于通用 LoRA 则留空保存并标记为无需触发词。
        </p>
      )}

      {loading ? (
        <StateHint>正在读取…</StateHint>
      ) : (
        <div className="node-table lora-table">
          <div className="node-row node-row-head">
            <span>LoRA</span><span>触发词</span><span>建议提示词</span><span>建议权重</span><span>来源</span><span>状态</span><span>操作</span>
          </div>
          {items.map((i) => (
            <div className="node-row" key={i.lora_name}>
              <span className="node-name" title={i.lora_name}>{i.lora_name}</span>
              <span title={i.triggers.join(", ")}>
                {i.triggers.length
                  ? i.triggers.join(", ")
                  : <em style={{ color: "var(--text-muted)" }}>{i.source === "manual" ? "无需触发词" : "未确认"}</em>}
              </span>
              <span title={i.suggested_prompt}>{i.suggested_prompt || "—"}</span>
              <span>{i.suggested_weight.toFixed(2)}</span>
              <span>{SOURCE_TEXT[i.source] || "—"}</span>
              <span className={i.missing ? "node-state-warn" : "node-state-ok"}>
                {i.missing ? "文件已移除" : "正常"}
              </span>
              <span className="node-ops">
                <button className="icon-btn" title="编辑 LoRA 数据" onClick={() => setEditing(i)}>
                  <Pencil size={15} />
                </button>
                <button className="icon-btn" title="删除记录" onClick={() => setConfirm(i)}>
                  <Trash2 size={15} />
                </button>
              </span>
            </div>
          ))}
          {items.length === 0 && <StateHint>还没有记录，点右上角「智能同步」开始扫描。</StateHint>}
        </div>
      )}

      {editing && (
        <LoraDataModal
          item={editing}
          onConfirm={doSave}
          onCancel={() => setEditing(null)}
        />
      )}
      {confirm && (
        <ConfirmModal
          title="删除触发词记录"
          message={`将删除 ${confirm.lora_name} 的触发词记录（磁盘上的模型文件不受影响）。下次同步会重新自动提取。确认删除？`}
          confirmText="删除" danger
          onConfirm={doDelete} onCancel={() => setConfirm(null)}
        />
      )}
    </PageShell>
  );
}
