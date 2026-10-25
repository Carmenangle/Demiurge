//! 壳的路径真源：本地数据布局、便携包识别、仓库布局识别。
//!
//! 语义逐条对齐 WPF 壳的 `ShellPaths.cs`（那份是合同）：
//! - 本地根：`DEMIURGE_SHELL_HOME` 优先，否则 `%LOCALAPPDATA%\Demiurge`
//! - 便携包：向上找到含 `data/runtime/current.json` 的目录，读 `base_id` 定位运行时 exe
//! - 仓库：向上找到同时含 `backend/app/main.py` 与 `frontend/package.json` 的目录
use std::path::{Path, PathBuf};

pub fn home() -> PathBuf {
    if let Some(raw) = std::env::var_os("DEMIURGE_SHELL_HOME") {
        let text = raw.to_string_lossy().trim().to_string();
        if !text.is_empty() {
            return PathBuf::from(text);
        }
    }
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("Demiurge")
}

pub fn logs_dir() -> PathBuf {
    home().join("logs")
}

pub fn shell_log_path() -> PathBuf {
    logs_dir().join("desktop.log")
}

pub fn runtime_log_path() -> PathBuf {
    logs_dir().join("runtime.log")
}

pub fn self_test_log_path() -> PathBuf {
    logs_dir().join("self-test.log")
}

pub fn settings_path() -> PathBuf {
    home().join("desktop.json")
}

pub fn webview_data_dir() -> PathBuf {
    home().join("webview2")
}

pub const SINGLE_INSTANCE_MUTEX: &str = "Local\\Demiurge.Desktop.SingleInstance";

/// 便携包 / 安装版布局。字段名与 WPF 的 `PackageLayout` 一一对应；
/// `pid_path` 是 Tauri 侧新增（安装版把 pid 落在 home，便携版落在 runtime_root）。
pub struct PackageLayout {
    pub package_root: PathBuf,
    pub runtime_root: PathBuf,
    pub state_path: PathBuf,
    pub runtime_exe: PathBuf,
    pub data_dir: PathBuf,
    pub pid_path: PathBuf,
}

/// 仓库布局（开发模式）。
pub struct RepoLayout {
    pub repo_root: PathBuf,
}

impl RepoLayout {
    pub fn backend_dir(&self) -> PathBuf {
        self.repo_root.join("backend")
    }

    pub fn python_exe(&self) -> PathBuf {
        self.backend_dir().join(".venv").join("Scripts").join("python.exe")
    }

    pub fn frontend_dist(&self) -> PathBuf {
        self.repo_root.join("frontend").join("dist")
    }
}

pub fn resolve_package(start: &Path) -> Option<PackageLayout> {
    let root = find_ancestor(start, |dir| dir.join("data").join("runtime").join("current.json").is_file())?;
    let runtime_root = root.join("data").join("runtime");
    let state_path = runtime_root.join("current.json");
    let base_id = read_base_id(&state_path)?;
    if base_id.trim().is_empty() {
        return None;
    }
    Some(PackageLayout {
        runtime_exe: runtime_root.join("base").join(&base_id).join("Demiurge-Runtime.exe"),
        data_dir: root.join("data").join("userdata"),
        pid_path: runtime_root.join("runtime.pid"),
        package_root: root,
        runtime_root,
        state_path,
    })
}

/// 安装版的运行时层：程序在安装目录（可能只读），运行时与数据都在 `home()`。
pub fn installed_runtime_root() -> PathBuf {
    home().join("runtime")
}

/// 是不是安装版壳（即使运行时还没部署）。安装目录只有壳与 `dependencies/`，没有 `data/runtime`。
pub fn looks_installed(install_dir: &Path) -> bool {
    install_dir.join("dependencies").join("MANIFEST.json").is_file()
        || install_dir.join("uninstall.exe").is_file()
}

/// 安装版布局：`install_dir` 放壳与依赖，`home/runtime` 放运行时，`home/userdata` 放数据。
/// 只有 `home/runtime/current.json` 存在时才认（首次安装、运行时还没落地时返回 None）。
pub fn resolve_installed(install_dir: &Path) -> Option<PackageLayout> {
    resolve_installed_in(&home(), install_dir)
}

pub(crate) fn resolve_installed_in(home_dir: &Path, install_dir: &Path) -> Option<PackageLayout> {
    let runtime_root = home_dir.join("runtime");
    let state_path = runtime_root.join("current.json");
    let base_id = read_base_id(&state_path)?;
    if base_id.trim().is_empty() {
        return None;
    }
    Some(PackageLayout {
        runtime_exe: runtime_root.join("base").join(&base_id).join("Demiurge-Runtime.exe"),
        data_dir: home_dir.join("userdata"),
        pid_path: home_dir.join("runtime.pid"),
        package_root: install_dir.to_path_buf(),
        runtime_root,
        state_path,
    })
}

/// 壳自带的依赖目录（安装目录与运行时层都看），要前置进运行时子进程的 PATH。
/// 现在有 MinGit（`dependencies/git/cmd`）与可选的 ffmpeg（`dependencies/ffmpeg/bin`）。
pub fn dependency_bin_dirs(layout: &PackageLayout) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    for root in [&layout.package_root, &layout.runtime_root] {
        let dependencies = root.join("dependencies");
        for candidate in [
            dependencies.join("git").join("cmd"),
            dependencies.join("ffmpeg").join("bin"),
        ] {
            if candidate.is_dir() && !dirs.contains(&candidate) {
                dirs.push(candidate);
            }
        }
    }
    dirs
}

pub fn find_repo(start: &Path) -> Option<RepoLayout> {
    let root = find_ancestor(start, |dir| {
        dir.join("backend").join("app").join("main.py").is_file()
            && dir.join("frontend").join("package.json").is_file()
    })?;
    Some(RepoLayout { repo_root: root })
}

fn find_ancestor(start: &Path, predicate: impl Fn(&Path) -> bool) -> Option<PathBuf> {
    let absolute = if start.is_absolute() {
        start.to_path_buf()
    } else {
        std::env::current_dir().ok()?.join(start)
    };
    let mut current: Option<&Path> = Some(absolute.as_path());
    while let Some(dir) = current {
        if predicate(dir) {
            return Some(dir.to_path_buf());
        }
        current = dir.parent();
    }
    None
}

fn read_base_id(state_path: &Path) -> Option<String> {
    let text = std::fs::read_to_string(state_path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value.get("base_id")?.as_str().map(|s| s.to_string())
}
