//! 壳日志与日志尾巴。写不进去不能拖死壳（与 WPF 同口径）。
use std::io::Write;
use std::path::Path;

pub fn info(message: &str) {
    append("INFO", message);
}

pub fn warn(message: &str) {
    append("WARN", message);
}

fn append(level: &str, message: &str) {
    let _ = std::fs::create_dir_all(crate::paths::logs_dir());
    let stamp = chrono_like_now();
    let line = format!("{stamp} [{level}] {message}\n");
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(crate::paths::shell_log_path())
    {
        let _ = file.write_all(line.as_bytes());
    }
}

/// 追加一条运行时的原始输出行（uvicorn / 便携运行时都往这里灌）。
pub fn append_runtime_line(line: &str) {
    if line.is_empty() {
        return;
    }
    let _ = std::fs::create_dir_all(crate::paths::logs_dir());
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(crate::paths::runtime_log_path())
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
