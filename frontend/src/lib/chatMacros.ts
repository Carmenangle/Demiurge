// {{char}}/{{user}} 宏替换（与后端 preset_store.substitute_macros 对齐）：
// user 缺省回退「我」；charName 非空才替换 {{char}}（未关联卡的通用对话不误伤）。
// 用于：开场白组装 + 对话记录**显示层**（模型输出/历史里残留的字面宏也统一替换，像全局宏一样生效）。
export function substituteMacros(text: string, charName: string, userName: string): string {
  if (!text) return text;
  const user = userName.trim() || "我";
  let out = text;
  if (charName.trim()) out = out.split("{{char}}").join(charName.trim());
  out = out.split("{{user}}").join(user);
  return out;
}
