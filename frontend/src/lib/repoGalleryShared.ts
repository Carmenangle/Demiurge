// RepoGallery 模块级共享：反推模式表、VLM prompt 常量与纯 helper（2026-10-24 自 RepoGallery.tsx 逐字搬出）。
// RepoGallery 仍是消费方属主；本文件只放无 React 依赖的纯数据/纯函数（接口即测试面）。
// 反推模式（2026-10-18 用户定案：WD14 / 视觉模型 / VLM 两段式共用一个按钮 + 模式选择）。
// vlm 与 ComfyUI「反推动漫」工作流同构：第一段＝质量+内容 tag 行，空行后第二段＝自然语言描述。
export type TagMode = "vlm" | "wd14" | "ai" | "desc";
export const TAG_MODES: { value: string; label: string }[] = [
  { value: "vlm", label: "VLM 两段式（标签+描述）" },
  { value: "wd14", label: "WD14 本地（纯标签）" },
  { value: "ai", label: "视觉模型（纯标签）" },
  { value: "desc", label: "视觉描述（只写描述）" },
];
export const TAG_MODE_HINT: Record<string, string> = {
  vlm: "反推（两段式）：质量+内容 tag 行 + 自然语言描述——标签存快照，描述写回资产",
  wd14: "反推（WD14 本地）：只出 danbooru 标签，快、不动描述",
  ai: "反推（视觉模型）：只出标签，不动描述",
  desc: "视觉描述：只生成一段自然语言描述（不碰标签），可用于补素材/资产的描述",
};
// 纯描述模式（与「自动描述缺失资产」同一口径：只写描述，不产标签）
export const VISION_DESC_PROMPT =
  "用一段简洁中文客观描述图片中的人物外观、服装、动作、场景、构图和画风；不要写提示词，不要解释。";
export const VLM_TWO_PARA_PROMPT = [
  "你是 Danbooru 标签反推专家。分析这张图，严格输出两段，中间用一个空行分隔：",
  "第一段：一行英文标签，逗号+空格分隔。顺序＝质量标签（masterpiece, score_9, year 2025, highres, safe 或 nsfw 等）→ 人物数量/类型（1girl/1boy…）→ 能识别则写画师名（并紧接用 @//画师名/ 形式再写一次）→ 外观/服装/动作/配饰/构图/画风细节（danbooru 风格：全小写、多词用下划线）。",
  "第二段：1-2 句英文自然语言，客观描述人物、服装、动作、场景与构图。",
  "只输出这两段正文，禁止标题、编号、解释或代码块。",
].join("\n");
export const AI_TAG_PROMPT =
  "只输出 8-16 个描述画面内容的英文标签（质量/人物数量/外观/服装/动作/场景/画风），"
  + "danbooru 风格（全小写、多词用下划线），英文逗号分隔，不要解释、不要句子";
/** 生成时间（入库毫秒时间戳）→ 可读文本；缺失返回空串（调用处据此不渲染该行）。 */
export function fmtCreatedAt(ms?: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
// 从 image_url 里解析顺序编号做时间序：每仓库顺序命名 000001.png…（越大越新）；
// 旧图 anima1_00073_.png 取尾部数字兜底；都取不到返回 0。
export function seqOf(url: string): number {
  const name = decodeURIComponent(url.split("path=").pop() || url).replace(/\\/g, "/").split("/").pop() || "";
  const m = name.match(/(\d+)(?=\D*$)/);  // 文件名里最后一段数字
  return m ? parseInt(m[1], 10) : 0;
}
