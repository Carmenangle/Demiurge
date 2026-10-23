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

/// 便携包布局。字段名与 WPF 的 `PackageLayout` 一一对应。
pub struct PackageLayout {
    pub package_root: PathBuf,
    pub runtime_root: PathBuf,
    pub state_path: PathBuf,
    pub runtime_exe: PathBuf,
    pub data_dir: PathBuf,
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
        package_root: root,
        runtime_root,
        state_path,
    })
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
