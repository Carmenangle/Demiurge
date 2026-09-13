"""ComfyUI-Manager 封装：节点管理（已装列表/市场/装/更新/卸载/开关/重启）。

Manager 的 HTTP API 怪癖只此一处。前端「节点管理」页四个 tab 都走这里。
- getlist?mode=installed 返回全库(几千条)，state ∈ {enabled,disabled,not-installed}：
  enabled/disabled = 本机已装；not-installed = 市场未装。
- 装/更新/卸载是异步队列操作，靠 /api/manager/queue/status 查进度。
安装/更新/卸载/重启为写操作（改环境），路由层需确认后再调。
"""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.services.comfyui_client import ComfyError


def _base(url: str) -> str:
    return url.rstrip("/")


def _get(url: str, path: str, timeout: float = 20) -> dict:
    try:
        with urlopen(_base(url) + path, timeout=timeout) as r:
            return json.loads(r.read())
    except HTTPError as e:
        raise ComfyError(f"ComfyUI-Manager 请求失败：{e}", 502)
    except Exception as e:
        raise ComfyError(f"ComfyUI-Manager 不可达（确认已装 Manager 且 ComfyUI 运行中）：{e}", 502)


def _post(url: str, path: str, body: dict, timeout: float = 30) -> dict:
    data = json.dumps(body).encode("utf-8")
    rq = Request(_base(url) + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(rq, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {"ok": True}
    except HTTPError as e:
        raise ComfyError(f"ComfyUI-Manager 操作失败：{e}", 502)
    except Exception as e:
        raise ComfyError(f"ComfyUI-Manager 操作失败：{e}", 502)


def _norm_pack(pid: str, p: dict) -> dict:
    """归一一个节点包记录为前端要的字段。"""
    return {
        "id": pid,
        "title": p.get("title", "") or pid,
        "author": p.get("author", ""),
        "repository": p.get("repository", "") or p.get("reference", ""),
        "description": p.get("description", ""),
        "install_type": p.get("install_type", ""),
        "state": p.get("state", ""),          # enabled/disabled/not-installed
        "updatable": bool(p.get("updatable", False)),
        "version": str(p.get("version", "") or ""),
        # Manager 对无数据的包用 -1 占位；归一成 0 / 空串，前端不必再防类型
        "stars": (lambda s: s if isinstance(s, int) and s >= 0 else 0)(p.get("stars", 0)),
        "last_update": (lambda u: u if isinstance(u, str) else "")(p.get("last_update", "")),
        "trust": bool(p.get("trust", False)),
    }


def list_installed(comfy_url: str, comfy_path: str = "") -> list[dict]:
    """本机已装节点包。供「插件节点更新」tab。
    以 ComfyUI-Manager 收录列表为主，comfy_path 给出时再扫磁盘：
    补每个包的本地 git 信息，并把 Manager 未收录但磁盘存在的目录也补进来
    （手动 clone/未知来源），避免数量少于实际。结果按名称排序。"""
    d = _get(comfy_url, "/customnode/getlist?mode=installed&skip_update=true")
    packs = d.get("node_packs", {}) or {}
    items = [_norm_pack(pid, p) for pid, p in packs.items()
             if p.get("state") in ("enabled", "disabled")]
    if comfy_path:
        git = local_git_info(comfy_path)
        _merge_git_info(items, git)
        _append_unmanaged_dirs(items, git)
    items.sort(key=lambda it: (it.get("title") or it.get("id") or "").lower())
    return items


def _append_unmanaged_dirs(items: list[dict], git: dict) -> None:
    """把磁盘 custom_nodes 里存在、但 Manager 列表未覆盖的目录补进 items。
    这些是手动 clone 或未收录的包，只靠 getlist 会漏掉。"""
    covered = {str(it.get("dir", "")).lower() for it in items if it.get("dir")}
    # Manager 包名也纳入去重（有的包匹配不到 git 目录，dir 为空但确实是同一个）
    covered |= {str(it.get("id", "")).lower() for it in items}
    covered |= {str(it.get("title", "")).lower() for it in items}
    for dirname, info in git.get("by_dir", {}).items():
        if dirname in covered:
            continue
        items.append({
            "id": info["dir"],
            "title": info["dir"],
            "author": "",
            "repository": info.get("remote", ""),
            "description": "本机目录（ComfyUI-Manager 未收录）",
            "install_type": "git-clone" if info.get("is_git") else "unknown",
            "state": "enabled",
            "updatable": False,
            "version": "",
            "stars": 0,
            "last_update": "",
            "trust": False,
            "commit": info.get("commit", ""),
            "git_date": info.get("date", ""),
            "is_git": info.get("is_git"),
            "dir": info["dir"],
        })


def _merge_git_info(items: list[dict], git: dict) -> None:
    """把本地 git 信息（commit/date/is_git）合并进已装包列表。
    先按归一 repository 匹配，未命中再按目录名（id/title）兜底。"""
    by_repo = git.get("by_repo", {})
    by_dir = git.get("by_dir", {})
    for it in items:
        g = by_repo.get(_norm_repo(it.get("repository", "")))
        if g is None:
            for key in (it.get("id", ""), it.get("title", "")):
                g = by_dir.get(str(key).lower())
                if g:
                    break
        if g:
            it["commit"] = g["commit"]
            it["git_date"] = g["date"]
            it["is_git"] = g["is_git"]
            it["dir"] = g["dir"]          # 本地目录名，供自建检查更新按目录匹配 updatable
        else:
            it["commit"] = ""
            it["git_date"] = ""
            it["is_git"] = None  # 磁盘上找不到对应目录
            it["dir"] = ""


def list_market(comfy_url: str) -> list[dict]:
    """全部节点包（市场）。供「官方插件市场」tab，前端做搜索/分页。"""
    d = _get(comfy_url, "/customnode/getlist?mode=installed&skip_update=true")
    packs = d.get("node_packs", {}) or {}
    return [_norm_pack(pid, p) for pid, p in packs.items()]


def queue_status(comfy_url: str) -> dict:
    """装/更新队列进度。{total_count, done_count, in_progress_count, is_processing}。"""
    return _get(comfy_url, "/api/manager/queue/status", timeout=8)


# —— 写操作（改环境，路由层须确认后调）——
# Manager 的装/更新/卸载是"入队 + start 执行"两步；请求体要整个节点包对象(含 id/version/files)。
# 前端从 list_installed/list_market 拿到的 pack 原样回传即可。

def enqueue_install(comfy_url: str, pack: dict, selected_version: str = "") -> dict:
    body = dict(pack)
    if selected_version:
        body["selected_version"] = selected_version
    return _post(comfy_url, "/manager/queue/install", body)


def enqueue_update(comfy_url: str, pack: dict) -> dict:
    return _post(comfy_url, "/manager/queue/update", dict(pack))


def enqueue_uninstall(comfy_url: str, pack: dict) -> dict:
    return _post(comfy_url, "/manager/queue/uninstall", dict(pack))


def enqueue_disable(comfy_url: str, pack: dict) -> dict:
    return _post(comfy_url, "/manager/queue/disable", dict(pack))


def install_git_url(comfy_url: str, git_url: str) -> dict:
    """用 GitHub 链接直接安装插件（自动 clone + 装 requirements.txt 依赖）。
    受 Manager 的 allow_git_url_install 安全开关限制，未开启会 403。"""
    return _post(comfy_url, "/customnode/install/git_url", {"url": git_url})


def start_queue(comfy_url: str) -> dict:
    """执行已入队的装/更新/卸载任务。入队后必须调这个才真正开始。"""
    return _post(comfy_url, "/manager/queue/start", {})


def update_comfyui(comfy_url: str) -> dict:
    """更新 ComfyUI 本体（入队），随后需 start_queue。"""
    return _post(comfy_url, "/manager/queue/update_comfyui", {})


def comfyui_versions(comfy_url: str) -> dict:
    """ComfyUI 可切换版本列表。返回 {versions:[...], current}。
    versions 里 'nightly'=开发版，'vX.Y.Z'=稳定版。"""
    return _get(comfy_url, "/comfyui_manager/comfyui_versions", timeout=15)


def git_versions(comfy_path: str) -> dict:
    """直接读 ComfyUI git 仓库的全部 tag（比 Manager 端点只给最近5个更全，对齐图1）。
    返回 {versions:[{version,date}...], current}。current 为当前所在版本(tag 或短哈希)。
    非 git 仓库 / git 不可用时抛 ComfyError。"""
    import subprocess
    from pathlib import Path
    base = Path(comfy_path or "")
    if not (base / ".git").exists():
        raise ComfyError("ComfyUI 目录不是 git 仓库，无法列出历史版本", 400)
    try:
        out = subprocess.run(
            ["git", "-C", str(base), "tag", "--sort=-creatordate",
             "--format=%(refname:short)|%(creatordate:short)"],
            capture_output=True, text=True, timeout=20,
        ).stdout
        cur = subprocess.run(
            ["git", "-C", str(base), "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception as e:
        raise ComfyError(f"读取 git 版本失败：{e}", 500)
    versions = []
    for line in out.splitlines():
        if "|" in line:
            ver, date = line.split("|", 1)
            versions.append({"version": ver.strip(), "date": date.strip()})
    return {"versions": versions, "current": cur}


def _norm_repo(u: str) -> str:
    """归一 git remote / repository url 为匹配键：去协议、去 .git、去尾斜杠、小写。"""
    s = (u or "").strip().lower()
    for pre in ("https://", "http://", "git@", "ssh://git@"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    s = s.replace(":", "/")  # git@github.com:owner/repo → github.com/owner/repo
    if s.endswith(".git"):
        s = s[:-4]
    return s.rstrip("/")


def local_git_info(comfy_path: str) -> dict:
    """扫 custom_nodes 下每个插件目录的本地 git 信息（短哈希+最后提交日期+remote）。
    对齐图1的启动器：直接读磁盘 git，不经 Manager。
    返回 {by_repo:{归一remote: {...}}, by_dir:{目录名小写: {...}}}，供前端按 repository 匹配。
    每项：{dir, commit, date, remote, is_git}。非 git 目录 is_git=False。"""
    import subprocess
    from pathlib import Path
    base = Path(comfy_path or "") / "custom_nodes"
    by_repo: dict[str, dict] = {}
    by_dir: dict[str, dict] = {}
    if not base.is_dir():
        return {"by_repo": by_repo, "by_dir": by_dir}

    def _run(args: list[str]) -> str:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""

    for entry in base.iterdir():
        if not entry.is_dir() or entry.name.startswith(".") or entry.name.endswith(".disabled"):
            continue
        if entry.name in ("__pycache__", "__init__") or entry.name.startswith("__"):
            continue  # Python 缓存等非节点目录，不计入已装列表
        d = str(entry)
        is_git = (entry / ".git").exists()
        info = {"dir": entry.name, "commit": "", "date": "", "remote": "", "is_git": is_git}
        if is_git:
            info["commit"] = _run(["git", "-C", d, "rev-parse", "--short", "HEAD"])
            info["date"] = _run(["git", "-C", d, "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d"])
            info["remote"] = _run(["git", "-C", d, "config", "--get", "remote.origin.url"])
            if info["remote"]:
                by_repo[_norm_repo(info["remote"])] = info
        by_dir[entry.name.lower()] = info
    return {"by_repo": by_repo, "by_dir": by_dir}


def find_pack_dir(comfy_path: str, pack: dict):
    """在 custom_nodes 下定位某节点包的本地 git 目录。
    先按 repository/id/title 猜目录名，未命中再遍历按 remote url 匹配。

    公开给路由与 node_update 复用 —— 「包对象 → 本地目录」的规则只该有一份。"""
    import subprocess
    from pathlib import Path
    base = Path(comfy_path or "") / "custom_nodes"
    if not base.is_dir():
        return None
    repo = pack.get("repository", "") or ""
    cands = []
    if repo:
        cands.append(_norm_repo(repo).split("/")[-1])
    cands += [pack.get("id", ""), pack.get("title", "")]
    for c in cands:
        if not c:
            continue
        p = base / str(c)
        if (p / ".git").exists():
            return p
    if repo:
        target = _norm_repo(repo)
        for entry in base.iterdir():
            if (entry / ".git").exists():
                try:
                    r = subprocess.run(["git", "-C", str(entry), "config", "--get", "remote.origin.url"],
                                       capture_output=True, text=True, timeout=10).stdout.strip()
                except Exception:
                    r = ""
                if r and _norm_repo(r) == target:
                    return entry
    return None


def git_update(comfy_path: str, pack: dict) -> dict:
    """直连 git 更新一个 git-HEAD（nightly）安装的插件：git pull --ff-only。
    绕开 Manager 队列（其对 nightly 包的 GitPython pull 不可靠）。
    返回 {ok, dir, old, new, updated}。找不到目录/pull 失败抛 ComfyError。"""
    import subprocess
    d = find_pack_dir(comfy_path, pack)
    if d is None:
        raise ComfyError(f"找不到「{pack.get('title') or pack.get('id')}」的本地 git 目录", 404)
    ds = str(d)

    def _run(args: list[str], timeout: float = 60):
        return subprocess.run(["git", "-C", ds] + args, capture_output=True, text=True, timeout=timeout)

    old = _run(["rev-parse", "--short", "HEAD"], 10).stdout.strip()
    r = _run(["pull", "--ff-only"])
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()[:400]
        raise ComfyError(f"git pull 失败：{msg}", 500)
    new = _run(["rev-parse", "--short", "HEAD"], 10).stdout.strip()
    return {"ok": True, "dir": d.name, "old": old, "new": new, "updated": old != new}


def check_updates_git(comfy_path: str, proxy_url: str = "") -> dict:
    """自建检查更新：绕开 Manager 的 fetch_updates（其对每个包 git fetch、不走本工具代理、
    国内访问 GitHub 常 timed out）。直接遍历 custom_nodes 每个 git 目录，用
    `git -c http.proxy=代理 fetch` 拉远程，比对本地 HEAD 与上游是否落后 → 判定 updatable。

    proxy_url 非空时给每条 fetch 注入 http.proxy/https.proxy（临时 -c，不写仓库配置）。
    返回 {updatable: {目录名小写: bool}, checked: N, failed: [目录名...]}。
    不改任何仓库、不 pull，只 fetch 比对。找不到目录返回空。"""
    import subprocess
    from pathlib import Path
    base = Path(comfy_path or "") / "custom_nodes"
    out: dict = {"updatable": {}, "checked": 0, "failed": []}
    if not base.is_dir():
        return out
    proxy_args: list[str] = []
    if (proxy_url or "").strip():
        proxy_args = ["-c", f"http.proxy={proxy_url}", "-c", f"https.proxy={proxy_url}"]

    def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", d] + args, capture_output=True, text=True, timeout=timeout)

    for entry in base.iterdir():
        if not entry.is_dir() or entry.name.startswith(".") or entry.name.endswith(".disabled"):
            continue
        if not (entry / ".git").exists():
            continue
        d = str(entry)
        out["checked"] += 1
        try:
            # 拉远程（带代理）；失败（网络/无远程）记为 failed，不阻断其它包
            fr = _run(proxy_args + ["fetch", "--quiet"], timeout=40)
            if fr.returncode != 0:
                out["failed"].append(entry.name)
                continue
            # 落后上游几个提交：只有 behind>0 才算「有更新」（本地领先/分叉不算，避免误报）
            br = _run(["rev-list", "--count", "HEAD..@{u}"], 10)
            if br.returncode != 0:
                out["failed"].append(entry.name)  # 无上游追踪分支等 → 判不了，记失败别误判"最新"
                continue
            behind = int((br.stdout or "0").strip() or "0")
            out["updatable"][entry.name.lower()] = behind > 0
        except Exception:
            out["failed"].append(entry.name)
    return out


def switch_comfyui_version(comfy_url: str, ver: str) -> dict:
    """切换 ComfyUI 到指定版本（入队），随后需 start_queue + 重启。"""
    return _post(comfy_url, "/comfyui_manager/comfyui_switch_version", {"ver": ver})


def reboot(comfy_url: str) -> dict:
    """重启 ComfyUI（装/更新/卸载后生效）。"""
    return _post(comfy_url, "/manager/reboot", {}, timeout=10)


# —— 工作流识别安装 ——

def _iter_nodes(workflow: dict):
    """遍历工作流节点，兼容 UI 格式(nodes 列表)与 API 格式(id→节点)。"""
    nodes = workflow.get("nodes")
    if isinstance(nodes, list):
        for n in nodes:
            if isinstance(n, dict):
                yield n
    else:
        for v in workflow.values():
            if isinstance(v, dict):
                yield v


# comfy-core / 空 = 内置节点，不是插件
_CORE_IDS = {"comfy-core", "", None}


# 不参与依赖判定的节点类型（前端虚拟节点/注释/中转）
_SKIP_NODE_TYPES = {"Note", "Reroute", "PrimitiveNode", "MarkdownNote", "Reroute (rgthree)"}


def _node_type_to_pack(workflow: dict) -> dict[str, str]:
    """遍历工作流，返回 {class_type: pack_id}。
    pack_id 取该节点 properties 的 cnr_id（非内置时）或 aux_id，拿不到则空串。
    只是"该节点自称属于哪个包"，是否真装以 object_info 为准。"""
    type_to_pack: dict[str, str] = {}
    for n in _iter_nodes(workflow):
        ntype = n.get("type") or n.get("class_type") or ""
        if not ntype or ntype in _SKIP_NODE_TYPES:
            continue
        props = n.get("properties", {}) or {}
        cnr = props.get("cnr_id")
        aux = props.get("aux_id")
        pack = ""
        if cnr is not None and cnr not in _CORE_IDS:
            pack = str(cnr)
        elif aux:  # aux_id 是 GitHub owner/repo 形式
            pack = str(aux)
        # 已记录过非空包 id 的类型不覆盖为空
        if ntype not in type_to_pack or (pack and not type_to_pack[ntype]):
            type_to_pack[ntype] = pack
    return type_to_pack


def analyze_workflow(comfy_url: str, workflow: dict) -> dict:
    """识别工作流依赖但本机未装的插件包。
    唯一的"已安装"真相源是 ComfyUI 实时 object_info（实际加载的节点类型）——
    只有 object_info 里没有的 class_type 才算缺失，避免包 id 命名不一致导致的误判。
    再对缺失节点用 cnr_id/aux_id 和 getmappings 反查安装包。
    返回 {missing_packs:[包id...], packs:[可装的包对象...], unresolved:[无法定位的节点名]}。"""
    from app.services import comfyui_client as _cc

    type_to_pack = _node_type_to_pack(workflow)

    # 已加载节点类型 = 已安装的唯一判据
    try:
        installed_types = set(_cc.fetch_object_info(comfy_url).keys())
    except ComfyError:
        installed_types = set()

    # 工作流里、object_info 中不存在的 class_type = 真正缺失的节点
    missing_types = {t for t in type_to_pack if t not in installed_types}
    if not missing_types:
        return {"missing_packs": [], "packs": [], "unresolved": []}

    market_list = list_market(comfy_url)
    market = {p["id"]: p for p in market_list}

    # getmappings：class_type → pack_id 反查表（兜底解析）
    node_to_pack: dict[str, str] = {}
    try:
        mappings = _get(comfy_url, "/customnode/getmappings?mode=local")
        for pk, val in mappings.items():
            names = val[0] if isinstance(val, list) and val else []
            for nm in names:
                node_to_pack.setdefault(nm, pk)
    except ComfyError:
        pass

    missing_ids: set[str] = set()
    unresolved: list[str] = []
    for ntype in sorted(missing_types):
        pid = type_to_pack.get(ntype) or node_to_pack.get(ntype) or ""
        if pid:
            missing_ids.add(pid)
        else:
            unresolved.append(ntype)

    # 缺失包 id → market 里的包对象；只有解析到真实 repository 的才可一键安装
    packs: list[dict] = []
    for pid in sorted(missing_ids):
        p = market.get(pid)
        if not p:
            for cand in market_list:
                if pid in (cand.get("repository", ""), cand.get("id", "")):
                    p = cand
                    break
        if p and p.get("repository"):
            packs.append(p)
        else:
            # market 里找不到有效仓库地址：不放进可一键安装列表，交给 unresolved 提示手动查找
            unresolved.append(pid)

    return {
        "missing_packs": sorted(missing_ids),
        "packs": packs,
        "unresolved": sorted(set(unresolved)),
    }

