import { useEffect, useState } from "react";
import {
  getManualFillProgress, getTableConfig, getTableStatus, manualFillTables, setTableConfig,
  type ChatModelInput, type TableConfig, type TableStatus,
} from "../api/tables";

interface SharedState {
  busy: boolean;
  setBusy: (value: boolean) => void;
  setError: (value: string) => void;
}

export function useManualTableFill(
  outputDir: string,
  repoId: string,
  cardName: string,
  chat: ChatModelInput,
  shared: SharedState,
  reloadAll: () => void,
) {
  const [status, setStatus] = useState<TableStatus | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [recentTurns, setRecentTurns] = useState(10);
  const [batchTurns, setBatchTurns] = useState(3);
  const [result, setResult] = useState("");

  const load = async () => {
    const next = await getTableStatus(outputDir, repoId, cardName);
    setStatus(next);
    setSelected((current) => current.length
      ? current
      : next.items.filter((item) => item.selectable).map((item) => item.uid));
  };

  useEffect(() => {
    void load().catch((error) => shared.setError(String((error as Error).message)));
  }, [outputDir, repoId, cardName]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async () => {
    if (!selected.length) return;
    shared.setBusy(true); shared.setError(""); setResult("");
    // 2026-09-13 用户实锤：十几批要跑几分钟且 UI 全程无反馈——run 期间轮询批次进度。
    const timer = window.setInterval(() => {
      void getManualFillProgress(outputDir, repoId)
        .then((p) => {
          if (p.running && p.batch_total > 0) {
            setResult(`填表进行中：第 ${p.batch_done}/${p.batch_total} 批…`);
          }
        })
        .catch(() => { /* 轮询失败不打扰主流程 */ });
    }, 1500);
    try {
      let response = await manualFillTables(
        outputDir, repoId, cardName, selected, recentTurns, batchTurns, null, chat,
      );
      if (response.needs_confirmation) {
        // 2026-09-13：明示每张表的重叠层数与覆盖后果（用户实锤旧文案含糊——
        // 「最少仅 X 层未记录」看不出哪些表会被重算、纪要会不会被删）。
        const names = new Map((status?.items ?? []).map((item) => [item.uid, item.name]));
        const detail = Object.entries(response.overlap_turns ?? {})
          .map(([uid, count]) => `${names.get(uid) ?? uid} 重叠 ${count} 层`)
          .join("、") || "部分表与已处理范围重叠";
        const overwrite = window.confirm(
          `最近 ${recentTurns} 层与已处理范围重叠（${detail}）。\n\n`
          + "「确定」覆盖重算：通用表按身份行覆盖更新（不产生重复行，数值以最后处理的批次为准）；"
          + "纪要表只追加新纪要、不删除旧条（可能与现有纪要并存）。\n"
          + "「取消」跳过已处理层，只补未记录部分。",
        );
        response = await manualFillTables(
          outputDir, repoId, cardName, selected, recentTurns, batchTurns, overwrite, chat,
        );
      }
      const failed = response.failed_batches ?? [];
      const failNote = failed.length
        ? `；⚠ ${failed.length} 批解析失败已跳过（${failed.join("；")}）——可调小「每 N 层合并一次」后对失败层重跑`
        : "";
      setResult(`处理 ${response.processed ?? 0} 层；通用表写入 ${response.applied ?? 0} 项；新增纪要 ${response.chronicles ?? 0} 条${failNote}。`);
      await load();
      reloadAll();
    } catch (error) {
      shared.setError(String((error as Error).message));
    } finally {
      window.clearInterval(timer);
      shared.setBusy(false);
    }
  };

  const toggle = (uid: string, checked: boolean) => setSelected((current) => checked
    ? [...current, uid]
    : current.filter((value) => value !== uid));

  return {
    status, selected, recentTurns, batchTurns, result, run, toggle,
    setRecentTurns, setBatchTurns,
  };
}

export function useTableConfig(outputDir: string, repoId: string, shared: SharedState) {
  const [config, setConfig] = useState<TableConfig | null>(null);
  useEffect(() => {
    getTableConfig(outputDir, repoId).then((data) => setConfig(data.config))
      .catch((error) => shared.setError(String((error as Error).message)));
  }, [outputDir, repoId]); // eslint-disable-line react-hooks/exhaustive-deps

  const commit = async (key: keyof TableConfig, value: number) => {
    if (!config) return;
    const previous = config;
    setConfig({ ...config, [key]: value });
    shared.setBusy(true); shared.setError("");
    try { setConfig((await setTableConfig(outputDir, repoId, { [key]: value })).config); }
    catch (error) {
      setConfig(previous);
      shared.setError(String((error as Error).message));
    } finally { shared.setBusy(false); }
  };
  return { config, commit };
}
