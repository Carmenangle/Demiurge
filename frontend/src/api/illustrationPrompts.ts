import { apiGet, apiPost } from "./client";

// 多元数据插入 → 提示词设置（属主：后端 illustration_prompt_store）。
//
// 当前只开放「高潮点定位判据」一项：开放的是**想让模型怎么想**（判据是创作意图，
// 用户比代码更清楚要哪一帧）。「程序怎么读」——输出字段名与 anchor 逐字落文校验——
// 是程序解析合同（output_contract），后端固定拼接、只读展示，前端只管把后端给的
// 文本原样显示，**不得在本地复制或改写它**（改了会导致判定结果无法被解析）。

export interface ClimaxAnchorPromptConfig {
  // 无「启用」开关（2026-09-18 用户定案）：判据档常开，失败自动退回中文词表档，
  // 所以没有「用不用它」这个选择，用户真正要调的是 rubric 本身。
  rubric: string;
}

export interface IllustrationPromptSettings {
  climax_anchor: ClimaxAnchorPromptConfig;
  /** 缺省判据全文（由后端给，供「恢复默认」用；前端不得复制真源） */
  default_rubric: string;
  /** 只读：输出字段名与 anchor 校验规则，不可编辑 */
  output_contract: string;
}

export function getIllustrationPromptSettings() {
  return apiGet<IllustrationPromptSettings>("/ai/prompt/settings");
}

export function saveIllustrationPromptSettings(climaxAnchor: ClimaxAnchorPromptConfig) {
  return apiPost<IllustrationPromptSettings>("/ai/prompt/settings", {
    climax_anchor: climaxAnchor,
  });
}
