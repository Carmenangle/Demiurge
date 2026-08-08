import { useEffect, useState } from "react";
import {
  getTableConfig, getTableStatus, manualFillTables, setTableConfig,
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
    try {
      let response = await manualFillTables(
        outputDir, repoId, cardName, selected, recentTurns, batchTurns, null, chat,
      );
      if (response.needs_confirmation) {
        const overwrite = window.confirm(
          `最近 ${recentTurns} 层与已有记录重叠（选中表最少仅 ${response.minimum_unrecorded ?? 0} 层未记录）。\n`
          + "点「确定」局部覆盖这些消息对应的已有记录；点「取消」跳过已有消息，只补未记录部分。",
        );
        response = await manualFillTables(
          outputDir, repoId, cardName, selected, recentTurns, batchTurns, overwrite, chat,
        );
      }
      setResult(`处理 ${response.processed ?? 0} 层；通用表写入 ${response.applied ?? 0} 项；新增纪要 ${response.chronicles ?? 0} 条。`);
      await load();
      reloadAll();
    } catch (error) {
      shared.setError(String((error as Error).message));
    } finally {
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
