import type { Repo } from "../stores/repos";
import type { Settings } from "../stores/settings";
import type { LastSelection } from "./lastSelection";
import { saveUserState } from "../api/userState";
import { saveMobileLocalModels } from "../api/mobileRemote";
import {
  changedModelSettings, omitModelSettings, pickModelSettings, type ModelSettings,
} from "./mobileModelSettings";

// repos 与 settings 是两个独立 store，但后端 user_state.json 整体读写。
// 这里做「最新值缓存 + debounce 合并上传」，任一 store 变更都把当前两块一起 POST 到后端。
// 后端 data 已被 .gitignore 排除、不进打包，含 API Key 也不会泄露。
//
// selection（上次选中的仓库/作品）也走同一条合并上传：只在 App 完成启动合并后才有值，
// 之前的上传不带该字段（后端保留盘上旧值，见 routers/user_state.set_state）。
//
// 联机同库（手机）走**分流保存**：电脑只收非模型设置，模型字段另落手机本机端点。
// 手机上的操作要用手机自己的模型设置（2026-10-30 用户定案），但电脑自己的模型配置
// 不该被手机的整对象回写覆盖（电脑侧对缺省模型字段沿用盘上旧值）。

let latestRepos: Repo[] = [];
let latestSettings: Settings | null = null;
let latestSelection: LastSelection | null = null;
let reposReady = false;
let settingsReady = false;
let timer: ReturnType<typeof setTimeout> | undefined;

// 分流保存开关：水合时按「是否联机」置位。基线＝本次水合呈现的模型设置，
// 只有用户真改过的模型字段才写手机本机（没改的继续跟电脑那份）。
let splitModels = false;
let modelBaseline: ModelSettings | null = null;

/** 水合完成后由 useSettings 调用：`remote`=联机同库；`hydrated`=本次水合后的设置。 */
export function markRemoteModelSync(remote: boolean, hydrated: Settings | null) {
  splitModels = remote;
  modelBaseline = remote && hydrated ? pickModelSettings(hydrated) : null;
}

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
  const settings = latestSettings;
  const selection = latestSelection ? { selection: latestSelection } : {};
  if (splitModels) {
    // 联机：电脑收非模型设置（模型字段缺席＝电脑保留自己那份），手机改过的模型落本机
    saveUserState({ repos: latestRepos, settings: omitModelSettings(settings), ...selection })
      .catch(() => { /* 后端离线时静默：localStorage 仍是本地兜底，下次变更再试 */ });
    if (!modelBaseline) {
      modelBaseline = pickModelSettings(settings); // 电脑没有可读存档：以当前值为基线，之后只存改动
      return;
    }
    const patch = changedModelSettings(settings, modelBaseline);
    if (Object.keys(patch).length === 0) return;
    saveMobileLocalModels(patch)
      .then(() => { modelBaseline = { ...(modelBaseline || {}), ...patch }; })
      .catch(() => { /* 本机端点离线：基线不动，下次变更再试 */ });
    return;
  }
  saveUserState({ repos: latestRepos, settings, ...selection }).catch(() => {
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
