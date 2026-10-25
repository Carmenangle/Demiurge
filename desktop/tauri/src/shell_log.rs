//! 壳日志与日志尾巴。写不进去不能拖死壳（与 WPF 同口径）。
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// 日志目录：`%LOCALAPPDATA%\Demiurge\logs` 写不进去时（受限上下文 / 低完整性）
/// 回退到 exe 同级 `logs`，再回退到 temp —— **日志写不进去等于没有可诊断性**，
/// 这一条是 2026-10-23 双击静默退出的直接教训。
fn log_dir() -> PathBuf {
    static CACHED: OnceLock<PathBuf> = OnceLock::new();
    CACHED
        .get_or_init(|| {
            let mut candidates = vec![crate::paths::logs_dir()];
            if let Some(dir) = std::env::current_exe()
                .ok()
                .and_then(|exe| exe.parent().map(|parent| parent.join("logs")))
            {
                candidates.push(dir);
            }
            candidates.push(std::env::temp_dir().join("demiurge-logs"));
            for dir in &candidates {
                if dir.is_dir() || std::fs::create_dir_all(dir).is_ok() {
                    let probe = dir.join(".write-probe");
                    if std::fs::write(&probe, b"x").is_ok() {
                        let _ = std::fs::remove_file(&probe);
                        return dir.clone();
                    }
                }
            }
            std::env::temp_dir()
        })
        .clone()
}

pub fn info(message: &str) {
    append("INFO", message);
}

pub fn warn(message: &str) {
    append("WARN", message);
}

fn append(level: &str, message: &str) {
    let dir = log_dir();
    let _ = std::fs::create_dir_all(&dir);
    let stamp = chrono_like_now();
    let line = format!("{stamp} [{level}] {message}\n");
    if append_to(&dir, "desktop.log", &line) {
        return;
    }
    // 追加失败（典型：目标文件是别的完整性标签建的，本进程只能读不能写）⇒ 换自己建的目录。
    let fallback = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|parent| parent.join("logs")))
        .unwrap_or_else(std::env::temp_dir);
    let _ = std::fs::create_dir_all(&fallback);
    if append_to(&fallback, "desktop.log", &line) {
        return;
    }
    let _ = append_to(&std::env::temp_dir().join("demiurge-logs"), "desktop.log", &line);
}

fn append_to(dir: &Path, name: &str, line: &str) -> bool {
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    rotate_if_large(dir, name);
    match std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join(name))
    {
        Ok(mut file) => file.write_all(line.as_bytes()).is_ok(),
        Err(_) => false,
    }
}

/// 单文件上限：超过即滚到 `<name>.1`（顶掉旧 .1）。5MB ≈ 数月壳日志量；
/// 只保留一代历史——日志是诊断手段不是存档，无界增长反而稀释 tail 读速。
const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;

fn rotate_if_large(dir: &Path, name: &str) {
    let path = dir.join(name);
    let Ok(meta) = std::fs::metadata(&path) else {
        return; // 还不存在：无需轮转
    };
    if meta.len() < MAX_LOG_BYTES {
        return;
    }
    let backup = dir.join(format!("{name}.1"));
    let _ = std::fs::remove_file(&backup);
    let _ = std::fs::rename(&path, backup); // 失败（如被占用）⇒ 本轮照常追加，下轮再试
}

/// 日志目录（供其它模块定位同一份日志）。
pub fn dir() -> PathBuf {
    log_dir()
}

/// 运行时日志文件（与 `append_runtime_line` 写同一个文件）。
pub fn runtime_log_file() -> PathBuf {
    log_dir().join("runtime.log")
}

/// 追加一条运行时的原始输出行（uvicorn / 便携运行时都往这里灌）。
pub fn append_runtime_line(line: &str) {
    if line.is_empty() {
        return;
    }
    let dir = log_dir();
    let _ = std::fs::create_dir_all(&dir);
    rotate_if_large(&dir, "runtime.log");
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("runtime.log"))
    {
        let _ = file.write_all(format!("{line}\n").as_bytes());
    }
}

/// 取日志尾部 N 行（失败返回空串，调用方拼进错误信息）。
pub fn tail(path: &Path, lines: usize) -> String {
    let Ok(text) = std::fs::read_to_string(path) else {
        return String::new();
    };
    let collected: Vec<&str> = text.lines().collect();
    let start = collected.len().saturating_sub(lines);
    collected[start..].join("\n")
}

pub fn now_stamp() -> String {
    chrono_like_now()
}

/// 本机不引 chrono：用系统时间手搓 `YYYY-MM-DD HH:MM:SS`（日志可读性够用）。
fn chrono_like_now() -> String {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;
    let mut st: SYSTEMTIME = unsafe { std::mem::zeroed() };
    unsafe { GetLocalTime(&mut st) };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn small_file_not_rotated() {
        let dir = std::env::temp_dir().join(format!("dsh-log-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        assert!(append_to(&dir, "test.log", "hello\n"));
        assert!(dir.join("test.log").exists());
        assert!(!dir.join("test.log.1").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn large_file_rotates_to_dot1() {
        let dir = std::env::temp_dir().join(format!("dsh-log-test-big-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::create_dir_all(&dir);
        // 直接灌一个超上限的文件（5MB+1），触发轮转
        let big = dir.join("big.log");
        std::fs::write(&big, vec![b'x'; MAX_LOG_BYTES as usize + 1]).unwrap();
        assert!(append_to(&dir, "big.log", "after-rotate\n"));
        let rotated = dir.join("big.log.1");
        assert!(rotated.exists(), "超限文件应滚到 .1");
        assert_eq!(std::fs::metadata(&rotated).unwrap().len(), MAX_LOG_BYTES + 1);
        // 当前文件只含新行
        assert_eq!(std::fs::read_to_string(&big).unwrap(), "after-rotate\n");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_file_is_noop() {
        let dir = std::env::temp_dir().join(format!("dsh-log-test-none-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        rotate_if_large(&dir, "absent.log"); // 不应 panic
        let _ = std::fs::remove_dir_all(&dir);
    }
}
