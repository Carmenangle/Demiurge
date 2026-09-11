"""插件更新：git 直连 + 依赖预检 + 真实进度 + 结果校验。

**为什么不用 ComfyUI-Manager 的更新队列。** 它的 `/queue/status` 只回
{total_count, done_count, in_progress_count, is_processing} —— 不报每个任务成败。
原先前端在队列转空时就直接写「已更新」并把 updatable 置 false，于是
什么都没变也显示更新成功，即所谓「假更新」。

这里改成：先记 HEAD，pull 完再记 HEAD，**变了才算更新成功**，没变就明说没变。

**依赖是踩坏环境的主要来源。** 插件的 requirements.txt 常写 `transformers>=x`
之类的宽松约束，装下去会把整个环境里的共享库升级掉，进而出现「CLIP 加载不了
量化数据」这类与被更新插件毫不相干的故障。所以：
1. 先用 `pip install --dry-run --report -` 拿到 pip 的完整计划（不动环境）；
2. 计划里若要改动 SENSITIVE 里的共享库，默认**不装**，把清单交给用户决定；
3. 用户点了确认才真装。

进度沿用本项目既有约定：进程内单例进度 dict + daemon 线程，前端轮询。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from app.services import update_progress as up
from app.services import task_progress_store

# 动这些包极易连带弄坏别的插件（推理栈共享依赖），一律先问过用户。
# 命名按 pip 规范化后比较（小写、-/_ 归一）。
SENSITIVE = {
    "torch", "torchvision", "torchaudio", "xformers",
    "transformers", "tokenizers", "safetensors", "accelerate",
    "numpy", "pillow", "opencv-python", "opencv-python-headless",
    "diffusers", "huggingface-hub", "sentencepiece", "protobuf",
    "gguf", "bitsandbytes", "triton", "flash-attn",
}

_LOCK = threading.Lock()
_RESTORED = task_progress_store.load("node-update").get("current", {})
_PROGRESS: dict = dict(_RESTORED)
_LAST_PERSIST = 0.0

task_progress_store.mark_interrupted(
    {"current": _PROGRESS}, running_statuses={"pending", "running"},
)


def _blank(note: str = "") -> dict:
    return {
        "running": False, "finished": False, "note": note,
        "phase": "", "percent": 0,
        "received_bytes": 0, "speed_bps": 0,
        "objects_done": 0, "objects_total": 0,
        "deps": [], "deps_total_bytes": 0,
        "old": "", "new": "", "changed": False,
        "error": "", "message": "",
        "pending_sensitive": [],
        "task_kind": "", "subject": "", "target_path": "",
        "repository": "", "commit": "", "requirements_sha256": "",
    }


def progress() -> dict:
    with _LOCK:
        return dict(_PROGRESS) if _PROGRESS else _blank()


def _set(**kw) -> None:
    with _LOCK:
        _PROGRESS.update(kw)
        _persist_locked(force=bool(kw.get("finished")))


def _persist_locked(force: bool = False) -> None:
    global _LAST_PERSIST
    now = time.monotonic()
    if not force and now - _LAST_PERSIST < 0.5:
        return
    task_progress_store.save("node-update", {"current": _PROGRESS}, limit=1)
    _LAST_PERSIST = now


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _git(cwd: str, args: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", cwd] + args,
                          capture_output=True, text=True, timeout=timeout)


def head(cwd: str) -> str:
    r = _git(cwd, ["rev-parse", "--short", "HEAD"], 15)
    return (r.stdout or "").strip()


def repository_provenance(path: Path) -> dict[str, str]:
    """读取已检出节点包的可审计来源、完整 commit 与依赖清单哈希。"""
    root = path.resolve()
    commit = (_git(str(root), ["rev-parse", "HEAD"], 15).stdout or "").strip()
    repository = (_git(str(root), ["remote", "get-url", "origin"], 15).stdout or "").strip()
    req = root / "requirements.txt"
    req_hash = hashlib.sha256(req.read_bytes()).hexdigest() if req.is_file() else ""
    return {
        "repository": repository,
        "commit": commit,
        "requirements_sha256": req_hash,
    }


def validate_requirements_sources(path: Path) -> None:
    """拒绝 requirements 通过 URL/VCS/editable/索引选项执行未审查来源。"""
    if not path.is_file():
        return
    unsafe = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        lowered = line.lower()
        if not line or line.startswith("#"):
            continue
        if (lowered.startswith(("-e ", "--editable", "--index-url", "--extra-index-url", "--find-links"))
                or "git+" in lowered or " @ http://" in lowered or " @ https://" in lowered
                or lowered.startswith(("http://", "https://"))):
            unsafe.append(line)
    if unsafe:
        raise ValueError("requirements.txt 含未审查的外部来源或 pip 选项：" + "; ".join(unsafe[:3]))


def has_upstream(cwd: str) -> bool:
    """没有上游追踪分支时 pull 必然失败（你环境里 easyanimate 就是这种）。"""
    return _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD@{u}"], 15).returncode == 0


def _pull_with_progress(cwd: str, proxy: str = "") -> tuple[bool, str]:
    """git pull --ff-only，边读 stderr 边更新进度。返回 (成功, 输出)。"""
    args = ["git", "-C", cwd]
    if proxy.strip():
        args += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    args += ["pull", "--ff-only", "--progress"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    collected: list[str] = []
    assert proc.stdout is not None
    for chunk in iter(proc.stdout.readline, ""):
        collected.append(chunk)
        for line in up.split_progress_stream(chunk):
            ev = up.parse_git_line(line)
            if ev:
                _set(**ev)
    proc.wait(timeout=600)
    return proc.returncode == 0, "".join(collected)[-2000:]


def plan_requirements(python_exe: str, req: Path,
                      proxy: str = "") -> tuple[list[dict], list[str]]:
    """跑 pip --dry-run --report，返回 (将安装清单, 命中 SENSITIVE 的包名)。

    不动环境。pip 解析失败时返回空清单 —— 宁可不装也不要盲装。
    """
    cmd = [python_exe, "-m", "pip", "install", "--dry-run", "--quiet",
           "--report", "-", "--no-input", "-r", str(req)]
    if proxy.strip():
        cmd += ["--proxy", proxy]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "pip 预检失败")[-500:])
    try:
        report = json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"pip 预检输出无法解析：{e}") from e
    planned: list[dict] = []
    sensitive: list[str] = []
    for item in report.get("install") or []:
        md = item.get("metadata") or {}
        name = str(md.get("name") or "")
        planned.append({"name": name, "version": str(md.get("version") or "")})
        if _norm(name) in {_norm(s) for s in SENSITIVE}:
            sensitive.append(f"{name}=={md.get('version')}")
    return planned, sensitive


def _install_requirements(python_exe: str, req: Path, proxy: str = "") -> tuple[bool, str]:
    """真装依赖，边读输出边把每个包的体积记进进度。"""
    cmd = [python_exe, "-m", "pip", "install", "--no-input",
           "--progress-bar", "off", "-r", str(req)]
    if proxy.strip():
        cmd += ["--proxy", proxy]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    tail: list[str] = []
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        tail.append(line)
        if len(tail) > 200:
            tail.pop(0)
        ev = up.parse_pip_line(line)
        if not ev:
            continue
        if ev["kind"] in ("download", "cached"):
            with _LOCK:
                deps = list(_PROGRESS.get("deps") or [])
                deps.append({"file": ev["file"], "bytes": ev["bytes"],
                             "cached": ev["kind"] == "cached"})
                _PROGRESS["deps"] = deps
                _PROGRESS["deps_total_bytes"] = sum(d["bytes"] for d in deps)
                _PROGRESS["phase"] = "deps"
                _PROGRESS["note"] = f"下载依赖 {ev['file']}（{up.human_bytes(ev['bytes'])}）"
                _persist_locked()
        elif ev["kind"] == "installing":
            _set(phase="deps-install",
                 note="安装依赖：" + ", ".join(ev["packages"])[:160])
    proc.wait(timeout=3600)
    return proc.returncode == 0, "".join(tail)[-2000:]


def check_core_requirements(comfy_path: str, python_exe: str,
                            proxy: str = "") -> dict:
    """核对 ComfyUI 本体的 requirements.txt 是否已被满足。

    切版本只动代码、不装依赖，于是会出现「新代码 + 旧依赖」的半更新状态 ——
    ComfyUI 的 requirements 里有 `transformers>=4.50.3`、`safetensors>=0.4.2`
    这类下限和 `comfyui-frontend-package==x.y.z` 这类精确锁定，代码换了而依赖没换，
    典型表现就是量化权重/CLIP 突然加载失败。所以切完版本必须提示这件事。

    只预检不安装。返回 {satisfied, missing:[{name,version}], sensitive:[...]}。
    """
    req = Path(comfy_path or "") / "requirements.txt"
    if not req.is_file():
        return {"satisfied": True, "missing": [], "sensitive": [],
                "note": "未找到 ComfyUI 的 requirements.txt，跳过依赖核对。"}
    if not python_exe:
        return {"satisfied": False, "missing": [], "sensitive": [],
                "note": "没找到 ComfyUI 的 Python，无法核对依赖。"
                        "请在设置里指定 ComfyUI 解释器。"}
    planned, sensitive = plan_requirements(python_exe, req, proxy)
    return {
        "satisfied": not planned,
        "missing": planned,
        "sensitive": sensitive,
        "note": "" if not planned else
                f"ComfyUI 本体有 {len(planned)} 个依赖未满足。"
                "代码已是新版但依赖还是旧的，可能导致量化模型/CLIP 加载失败。",
    }


def start(pack_dir: str, python_exe: str = "", proxy: str = "",
          allow_sensitive: bool = False, skip_deps: bool = False,
          task_kind: str = "node-update", subject: str = "") -> dict:
    """启动一次更新（后台线程）。已有任务在跑时拒绝。"""
    with _LOCK:
        if _PROGRESS.get("running"):
            return {"already_running": True}
        _PROGRESS.clear()
        _PROGRESS.update(_blank("准备更新…"))
        _PROGRESS.update(running=True, task_kind=task_kind,
                         subject=subject or Path(pack_dir).name,
                         target_path=str(Path(pack_dir)))
        _persist_locked(force=True)

    d = Path(pack_dir)
    name = d.name

    def run() -> None:
        try:
            if not (d / ".git").exists():
                raise RuntimeError(f"「{name}」不是 git 安装的仓库，无法更新。")
            if not has_upstream(str(d)):
                raise RuntimeError(
                    f"「{name}」当前分支没有上游追踪分支（可能是 detached HEAD），"
                    "git 无法自动更新。请手动处理这个仓库。")
            old = head(str(d))
            _set(old=old, phase="download", note=f"正在拉取 {name}…")
            ok, out = _pull_with_progress(str(d), proxy)
            if not ok:
                raise RuntimeError(f"git pull 失败：{out[-400:]}")
            new = head(str(d))
            changed = bool(old and new and old != new)
            _set(new=new, changed=changed, **repository_provenance(d))

            # 代码没变就不折腾依赖 —— 这是「假更新」的另一半：明确告诉用户没变
            if not changed and not allow_sensitive:
                _set(running=False, finished=True, phase="done",
                     message=f"「{name}」已是最新（{new}），本次没有任何改动。")
                return

            req = d / "requirements.txt"
            if skip_deps or not req.is_file():
                _set(running=False, finished=True, phase="done",
                     message=f"「{name}」已更新（{old} → {new}）。"
                             + ("" if req.is_file() else "该插件没有 requirements.txt。")
                             + "重启 ComfyUI 后生效。")
                return

            if not python_exe:
                _set(running=False, finished=True, phase="done",
                     message=f"「{name}」代码已更新（{old} → {new}），"
                             "但没找到 ComfyUI 的 Python，依赖未处理。"
                             "请在设置里指定 ComfyUI 解释器后重试依赖安装。")
                return

            _set(phase="preflight", note="正在预检依赖改动（不会改动环境）…")
            planned, sensitive = plan_requirements(python_exe, req, proxy)
            if not planned:
                _set(running=False, finished=True, phase="done",
                     message=f"「{name}」已更新（{old} → {new}），依赖都已满足，无需安装。"
                             "重启 ComfyUI 后生效。")
                return
            if sensitive and not allow_sensitive:
                _set(running=False, finished=True, phase="needs-confirm",
                     pending_sensitive=sensitive,
                     message=f"「{name}」代码已更新（{old} → {new}），但它的依赖要改动 "
                             f"{len(sensitive)} 个共享库，可能影响其他插件（如量化模型加载）。"
                             "已暂停安装，请确认后再继续。")
                return

            _set(phase="deps", note=f"开始安装 {len(planned)} 个依赖…")
            ok, tail = _install_requirements(python_exe, req, proxy)
            if not ok:
                _set(running=False, finished=True, phase="done",
                     error=f"依赖安装失败：{tail[-400:]}",
                     message=f"「{name}」代码已更新（{old} → {new}），但依赖安装失败。"
                             "插件可能无法正常工作。")
                return
            with _LOCK:
                total = _PROGRESS.get("deps_total_bytes", 0)
            _set(running=False, finished=True, phase="done", changed=changed or allow_sensitive,
                 message=(f"「{name}」依赖已处理，" if allow_sensitive and old == new
                          else f"「{name}」已更新（{old} → {new}），")
                         + f"依赖 {len(planned)} 个、共 {up.human_bytes(total)}。"
                         "重启 ComfyUI 后生效。")
        except Exception as e:
            _set(running=False, finished=True, phase="done", error=str(e),
                 message=f"更新「{name}」失败：{e}")

    threading.Thread(target=run, daemon=True).start()
    return {"already_running": False}


_GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}


def install_target(comfy_path: str, repository: str) -> Path:
    """校验仓库地址并解析 custom_nodes 内的唯一目标目录。"""
    parsed = urlparse(repository.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _GIT_HOSTS:
        raise ValueError("节点仓库只允许 GitHub、GitLab 或 Bitbucket 的 HTTPS 地址。")
    name = Path(parsed.path.rstrip("/")).name
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not name or name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("无法从仓库地址解析安全的节点目录名。")
    root = (Path(comfy_path) / "custom_nodes").resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError("节点目标目录越界。")
    return target


def _clone_with_progress(repository: str, target: Path, proxy: str = "") -> tuple[bool, str]:
    args = ["git"]
    if proxy.strip():
        args += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    args += ["clone", "--progress", repository, str(target)]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    collected: list[str] = []
    assert proc.stdout is not None
    for chunk in iter(proc.stdout.readline, ""):
        collected.append(chunk)
        for line in up.split_progress_stream(chunk):
            ev = up.parse_git_line(line)
            if ev:
                _set(**ev)
    proc.wait(timeout=1200)
    return proc.returncode == 0, "".join(collected)[-2000:]


def _git_command_with_progress(cwd: str, command: list[str], proxy: str = "",
                               timeout: int = 1200) -> tuple[bool, str]:
    args = ["git", "-C", cwd]
    if proxy.strip():
        args += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    args += command
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    collected: list[str] = []
    assert proc.stdout is not None
    for chunk in iter(proc.stdout.readline, ""):
        collected.append(chunk)
        for line in up.split_progress_stream(chunk):
            ev = up.parse_git_line(line)
            if ev:
                _set(**ev)
    proc.wait(timeout=timeout)
    return proc.returncode == 0, "".join(collected)[-2000:]


def start_install(comfy_path: str, repository: str, python_exe: str = "", proxy: str = "",
                  allow_sensitive: bool = False, skip_deps: bool = False) -> dict:
    """安装 Git 节点包并公开 clone/依赖下载进度。"""
    target = install_target(comfy_path, repository)
    with _LOCK:
        if _PROGRESS.get("running"):
            return {"already_running": True}
        _PROGRESS.clear()
        _PROGRESS.update(_blank("准备安装节点包…"))
        _PROGRESS.update(running=True, task_kind="node-install", subject=target.name,
                         target_path=str(target), repository=repository)
        _persist_locked(force=True)

    def run() -> None:
        try:
            # 敏感依赖确认后的第二次请求延续已完成的 clone，只处理依赖。
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                _set(phase="download", note=f"正在下载 {target.name}…")
                ok, out = _clone_with_progress(repository, target, proxy)
                if not ok:
                    raise RuntimeError(f"git clone 失败：{out[-400:]}")
            elif not ((allow_sensitive or skip_deps) and (target / ".git").is_dir()):
                raise RuntimeError(f"目标目录已存在：{target}")

            req = target / "requirements.txt"
            _set(**repository_provenance(target))
            if skip_deps or not req.is_file():
                _set(running=False, finished=True, phase="done", changed=True,
                     message=f"「{target.name}」安装完成。"
                             + ("该插件没有 requirements.txt。" if not req.is_file() else "已跳过依赖。")
                             + "重启 ComfyUI 后生效。")
                return
            if not python_exe:
                _set(running=False, finished=True, phase="done", changed=True,
                     message=f"「{target.name}」代码已安装，但没找到 ComfyUI 的 Python，依赖未处理。")
                return
            validate_requirements_sources(req)
            _set(phase="preflight", note="正在预检依赖改动（不会改动环境）…")
            planned, sensitive = plan_requirements(python_exe, req, proxy)
            if sensitive and not allow_sensitive:
                _set(running=False, finished=True, phase="needs-confirm", changed=True,
                     pending_sensitive=sensitive,
                     message=f"「{target.name}」代码已下载，依赖涉及共享库，等待确认。")
                return
            if planned:
                _set(phase="deps", note=f"开始安装 {len(planned)} 个依赖…")
                ok, tail = _install_requirements(python_exe, req, proxy)
                if not ok:
                    raise RuntimeError(f"依赖安装失败：{tail[-400:]}")
            _set(running=False, finished=True, phase="done", changed=True,
                 message=f"「{target.name}」安装完成，重启 ComfyUI 后生效。")
        except Exception as e:
            _set(running=False, finished=True, phase="done", error=str(e),
                 message=f"安装「{target.name}」失败：{e}")

    threading.Thread(target=run, daemon=True).start()
    return {"already_running": False}


def start_core_switch(comfy_path: str, version: str, proxy: str = "") -> dict:
    """拉取远端并切换 ComfyUI tag/开发分支，公开 Git 传输进度。"""
    target_version = version.strip()
    if target_version != "nightly" and not re.fullmatch(r"v\d+(?:\.\d+){1,3}(?:[-+._A-Za-z0-9]*)?", target_version):
        raise ValueError("无效的 ComfyUI 版本。")
    root = Path(comfy_path).resolve()
    with _LOCK:
        if _PROGRESS.get("running"):
            return {"already_running": True}
        _PROGRESS.clear()
        _PROGRESS.update(_blank("准备切换 ComfyUI 版本…"))
        _PROGRESS.update(running=True, task_kind="comfyui-update", subject="ComfyUI",
                         target_path=str(root))
        _persist_locked(force=True)

    def run() -> None:
        try:
            if not (root / ".git").is_dir():
                raise RuntimeError("ComfyUI 目录不是 git 仓库，无法切换版本。")
            old = head(str(root))
            _set(old=old, phase="download", note=f"正在获取 {target_version} 的代码…")
            ok, out = _git_command_with_progress(str(root), ["fetch", "origin", "--tags", "--prune", "--progress"], proxy)
            if not ok:
                raise RuntimeError(f"git fetch 失败：{out[-400:]}")
            _set(phase="resolve", percent=0, note=f"正在切换到 {target_version}…")
            if target_version == "nightly":
                remote_head = _git(str(root), ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], 15)
                branch = (remote_head.stdout or "origin/master").strip().removeprefix("origin/")
                checkout = _git(str(root), ["checkout", branch], 120)
                if checkout.returncode != 0:
                    checkout = _git(str(root), ["checkout", "-b", branch, "--track", f"origin/{branch}"], 120)
                if checkout.returncode != 0:
                    raise RuntimeError(f"切换开发分支失败：{(checkout.stderr or checkout.stdout)[-400:]}")
                ok, out = _pull_with_progress(str(root), proxy)
                if not ok:
                    raise RuntimeError(f"更新开发分支失败：{out[-400:]}")
            else:
                checkout = _git(str(root), ["checkout", "--detach", target_version], 120)
                if checkout.returncode != 0:
                    raise RuntimeError(f"切换版本失败：{(checkout.stderr or checkout.stdout)[-400:]}")
            new = head(str(root))
            _set(running=False, finished=True, phase="done", percent=100,
                 new=new, changed=bool(old and new and old != new),
                 message=f"ComfyUI 已切换到 {target_version}（{old} → {new}），重启后生效。")
        except Exception as e:
            _set(running=False, finished=True, phase="done", error=str(e),
                 message=f"ComfyUI 版本切换失败：{e}")

    threading.Thread(target=run, daemon=True).start()
    return {"already_running": False}
