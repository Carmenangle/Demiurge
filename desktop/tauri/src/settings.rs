//! 窗口几何与端口的持久化。语义对齐 WPF 的 `ShellSettings.cs`：
//! 读坏文件回默认、越界值夹取、位置要夹到虚拟屏幕内（多显示器也不会跑到看不见的地方）。
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

pub const DEFAULT_WIDTH: f64 = 1280.0;
pub const DEFAULT_HEIGHT: f64 = 860.0;
pub const MIN_WIDTH: f64 = 720.0;
pub const MIN_HEIGHT: f64 = 520.0;
pub const DEFAULT_PORT: u16 = 8010;

/// ⚠ 字段名保持 PascalCase：与既有 WPF 写的 `desktop.json` 双向兼容。
#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "PascalCase", default)]
pub struct ShellSettings {
    pub width: Option<f64>,
    pub height: Option<f64>,
    pub left: Option<f64>,
    pub top: Option<f64>,
    pub maximized: bool,
    pub port: i64,
}

impl Default for ShellSettings {
    fn default() -> Self {
        Self {
            width: None,
            height: None,
            left: None,
            top: None,
            maximized: false,
            port: DEFAULT_PORT as i64,
        }
    }
}

impl ShellSettings {
    pub fn load(path: Option<PathBuf>) -> Self {
        let actual = path.unwrap_or_else(crate::paths::settings_path);
        let mut settings = match std::fs::read_to_string(&actual) {
            Ok(text) => serde_json::from_str::<ShellSettings>(&text).unwrap_or_else(|err| {
                crate::shell_log::warn(&format!("读取桌面设置失败，改用默认值：{} ({err})", actual.display()));
                ShellSettings::default()
            }),
            Err(_) => ShellSettings::default(),
        };
        settings.normalize();
        settings
    }

    pub fn save(&self, path: Option<PathBuf>) -> std::io::Result<()> {
        let actual = path.unwrap_or_else(crate::paths::settings_path);
        let mut copy = self.clone();
        copy.normalize();
        if let Some(dir) = actual.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let temporary = actual.with_extension("json.tmp");
        let text = serde_json::to_string_pretty(&copy).unwrap_or_else(|_| "{}".to_string());
        std::fs::write(&temporary, text.as_bytes())?;
        std::fs::rename(&temporary, &actual)
    }

    pub fn normalize(&mut self) {
        self.width = Some(finite(self.width, DEFAULT_WIDTH).clamp(MIN_WIDTH, 12000.0));
        self.height = Some(finite(self.height, DEFAULT_HEIGHT).clamp(MIN_HEIGHT, 12000.0));
        if self.port < 1 || self.port > 65535 {
            self.port = DEFAULT_PORT as i64;
        }
        match (self.left, self.top) {
            (Some(left), Some(top)) if left.is_finite() && top.is_finite() => {
                self.left = Some(left);
                self.top = Some(top);
                self.clamp_to_virtual_screen();
            }
            _ => {
                self.left = None;
                self.top = None;
            }
        }
    }

    /// 夹到虚拟屏幕内：与 WPF 版同一套边距（宽 ≤ 屏宽-40、高 ≤ 屏高-80，左/上不越界）。
    fn clamp_to_virtual_screen(&mut self) {
        let Some((screen_left, screen_top, screen_width, screen_height)) = virtual_screen() else {
            return;
        };
        if screen_width <= 0.0 || screen_height <= 0.0 {
            return;
        }
        let max_width = (screen_width - 40.0).max(MIN_WIDTH);
        let max_height = (screen_height - 80.0).max(MIN_HEIGHT);
        let width = self.width.unwrap_or(DEFAULT_WIDTH).min(max_width);
        let height = self.height.unwrap_or(DEFAULT_HEIGHT).min(max_height);
        self.width = Some(width);
        self.height = Some(height);

        let left_max = (screen_left + screen_width - width).max(screen_left);
        self.left = Some(self.left.unwrap_or(screen_left).clamp(screen_left, left_max));
        let top_max = (screen_top + screen_height - height).max(screen_top);
        self.top = Some(self.top.unwrap_or(screen_top).clamp(screen_top, top_max));
    }
}

fn finite(value: Option<f64>, fallback: f64) -> f64 {
    match value {
        Some(actual) if actual.is_finite() => actual,
        _ => fallback,
    }
}

/// 虚拟屏幕（全部显示器并集）的左/上/宽/高，单位像素。
pub fn virtual_screen() -> Option<(f64, f64, f64, f64)> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    };
    unsafe {
        let left = GetSystemMetrics(SM_XVIRTUALSCREEN) as f64;
        let top = GetSystemMetrics(SM_YVIRTUALSCREEN) as f64;
        let width = GetSystemMetrics(SM_CXVIRTUALSCREEN) as f64;
        let height = GetSystemMetrics(SM_CYVIRTUALSCREEN) as f64;
        if width <= 0.0 || height <= 0.0 {
            return None;
        }
        Some((left, top, width, height))
    }
}

#[allow(dead_code)]
pub fn settings_dir_hint() -> Option<&'static Path> {
    None
}
