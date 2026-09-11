import type { Repo } from "../stores/repos";
import type { Settings } from "../stores/settings";
import { saveUserState } from "../api/userState";

// repos 与 settings 是两个独立 store，但后端 user_state.json 整体读写。
// 这里做「最新值缓存 + debounce 合并上传」，任一 store 变更都把当前两块一起 POST 到后端。
// 后端 data 已被 .gitignore 排除、不进打包，含 API Key 也不会泄露。

let latestRepos: Repo[] = [];
let latestSettings: Settings | null = null;
let reposReady = false;
let settingsReady = false;
let timer: ReturnType<typeof setTimeout> | undefined;

function flush() {
  // 两块都就绪才写，避免启动早期用半截数据覆盖后端
  if (!reposReady || !settingsReady || !latestSettings) return;
  saveUserState({ repos: latestRepos, settings: latestSettings }).catch(() => {
    /* 后端离线时静默：localStorage 仍是本地兜底，下次变更再试 */
  });
}

function schedule() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(flush, 600);
}

export function pushRepos(repos: Repo[]) {
  latestRepos = repos;
  reposReady = true;
  schedule();
}

export function pushSettings(settings: Settings) {
  latestSettings = settings;
  settingsReady = true;
  schedule();
}
