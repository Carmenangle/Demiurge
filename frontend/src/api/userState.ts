import { apiGet, apiPost } from "./client";
import type { Repo } from "../stores/repos";
import type { Settings } from "../stores/settings";

// 后端 data/user_state.json 的形状（repos/settings 任一可为空）
export interface UserState {
  repos: Repo[] | null;
  settings: Settings | null;
}

export function getUserState() {
  return apiGet<UserState>("/user-state");
}

// 整体覆盖写。repos 与 settings 分头变更，故这里合并当前两块一起 POST。
export function saveUserState(state: { repos: Repo[]; settings: Settings }) {
  return apiPost<{ ok: boolean }>("/user-state", state);
}

// 仓库改名：后端重命名输出文件夹 + 重写快照/RAG 里的图片路径
export function renameRepoFolder(args: {
  repo_id: string; old_name: string; new_name: string; output_dir: string;
}) {
  return apiPost<{ folder: string; snapshot?: number; rag?: number }>(
    "/user-state/rename-folder", args,
  );
}

// 上传对话背景图，返回后端保存的本地路径（填进 chatBgPath）
export async function uploadChatBg(file: File): Promise<{ ok: boolean; path: string }> {
  const fd = new FormData();
  fd.append("file", file);
  const resp = await fetch("http://127.0.0.1:8010/api/user-state/upload-bg", { method: "POST", body: fd });
  if (!resp.ok) throw new Error(`上传失败: ${resp.status}`);
  return resp.json();
}
