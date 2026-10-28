import type { Repo } from "../stores/repos";
import type { Settings } from "../stores/settings";
import type { LastSelection } from "./lastSelection";
import { saveUserState } from "../api/userState";

// repos 与 settings 是两个独立 store，但后端 user_state.json 整体读写。
// 这里做「最新值缓存 + debounce 合并上传」，任一 store 变更都把当前两块一起 POST 到后端。
// 后端 data 已被 .gitignore 排除、不进打包，含 API Key 也不会泄露。
//
// selection（上次选中的仓库/作品）也走同一条合并上传：只在 App 完成启动合并后才有值，
// 之前的上传不带该字段（后端保留盘上旧值，见 routers/user_state.set_state）。

let latestRepos: Repo[] = [];
let latestSettings: Settings | null = null;
let latestSelection: LastSelection | null = null;
let reposReady = false;
let settingsReady = false;
let timer: ReturnType<typeof setTimeout> | undefined;

// 联机同库下电脑不可达（/api/user-state 被代理成 502）时，两 store 保持「未水合」：
// 界面跑在本机缓存上，但绝不能把这份快照当成电脑的真值上传（后端 set_state 整体替换
// repos，会把电脑的仓库/模型列表抹掉）。恢复后由 MobileOnlineWatch 播事件重新水合。
let fellBack = false;

export function markUserStateFallback() {
  fellBack = true;
}

export function clearUserStateFallback() {
  fellBack = false;
}

export function userStateFellBack() {
  return fellBack;
}

export const USER_STATE_RECONNECT_EVENT = "laf-user-state-reconnect";

/** 联机恢复（或退出联机）时广播：两个 store 各自重新拉一次后端存档。 */
export function notifyUserStateReconnect() {
  fellBack = false;
  window.dispatchEvent(new Event(USER_STATE_RECONNECT_EVENT));
}

function flush() {
  // 两块都就绪才写，避免启动早期用半截数据覆盖后端
  if (!reposReady || !settingsReady || !latestSettings) return;
  saveUserState({
    repos: latestRepos,
    settings: latestSettings,
    ...(latestSelection ? { selection: latestSelection } : {}),
  }).catch(() => {
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

export function pushSelection(selection: LastSelection) {
  latestSelection = selection;
  schedule();
}
