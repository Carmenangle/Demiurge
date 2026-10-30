// 联机同库下的「模型设置归谁」纯逻辑（2026-10-30 用户定案）。
//
// 分工：手机上的操作（剧情/对话/表格/RAG/生图请求）用**手机自己的**模型设置；
// 执行请求的电脑只负责跑 ComfyUI，模型配置随请求体走。故联机时：
// - 水合：电脑快照提供非模型设置，手机本机模型覆盖其上（手机没配过的字段仍跟电脑）；
// - 回写：给电脑的 settings 摘掉模型字段（电脑保留自己那份），手机改过的模型字段
//   另走本机端点落手机。
// 字段面是后端 `services/mobile_local_models.MODEL_KEYS` 的前端投影，两边必须一致
// （跨语言契约锁见 mobileModelSettings.contract.test.ts）。
import type { Settings } from "../stores/settings";

export const MODEL_SETTING_KEYS = [
  "chatModels",
  "activeChatModelId",
  "imageModels",
  "activeImageModelId",
  "videoModels",
  "activeVideoModelId",
  "embedModel",
  "vlmModel",
] as const;

export type ModelSettings = Partial<
  Pick<Settings, (typeof MODEL_SETTING_KEYS)[number]>
>;

type Bag = Record<string, unknown>;

function asBag(value: object): Bag {
  return value as unknown as Bag;
}

/** 只取模型字段（存在才取：选中 id 可为 undefined）。 */
export function pickModelSettings(s: Settings): ModelSettings {
  const src = asBag(s);
  const out: Bag = {};
  for (const key of MODEL_SETTING_KEYS) {
    if (key in src) out[key] = src[key];
  }
  return out as ModelSettings;
}

/** 摘掉模型字段：回写电脑的 settings（电脑侧对缺席字段沿用盘上旧值）。 */
export function omitModelSettings(s: Settings): Settings {
  const out = asBag({ ...s });
  for (const key of MODEL_SETTING_KEYS) delete out[key];
  return out as unknown as Settings;
}

/** 相对上次水合基线变过的模型字段：只有用户真改了才落手机，没改的继续跟电脑。 */
export function changedModelSettings(
  next: Settings,
  baseline: ModelSettings | null,
): ModelSettings {
  if (!baseline) return {};
  const current = asBag(pickModelSettings(next));
  const base = asBag(baseline);
  const out: Bag = {};
  for (const key of MODEL_SETTING_KEYS) {
    if (!(key in current)) continue;
    if (JSON.stringify(current[key]) !== JSON.stringify(base[key])) out[key] = current[key];
  }
  return out as ModelSettings;
}

/** 电脑快照 + 手机本机模型。手机没存过的字段保留电脑那份（首次联机即此形态）。 */
export function overlayModelSettings(
  base: Settings,
  local: ModelSettings | null | undefined,
): Settings {
  if (!local) return base;
  const out = asBag({ ...base });
  const patch = asBag(local);
  for (const key of MODEL_SETTING_KEYS) {
    if (key in patch) out[key] = patch[key];
  }
  return out as unknown as Settings;
}
