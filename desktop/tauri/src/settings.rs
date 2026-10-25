//! 窗口几何与端口的持久化。语义对齐 WPF 的 `ShellSettings.cs`：
//! 读坏文件回默认、越界值夹取、位置要夹到虚拟屏幕内（多显示器也不会跑到看不见的地方）。
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

pub const DEFAULT_WIDTH: f64 = 1280.0;
pub const DEFAULT_HEIGHT: f64 = 860.0;
pub const MIN_WIDTH: f64 = 720.0;
pub const MIN_HEIGHT: f64 = 520.0;
pub const DEFAULT_PORT: u16 = 8010;
/// 2 = 几何单位已声明为逻辑像素。旧 Tauri 壳（无此字段）存的是物理像素。
const GEOMETRY_VERSION: u32 = 2;

/// ⚠ 字段名保持 PascalCase：与既有 WPF 写的 `desktop.json` 双向兼容。
/// ⚠ 几何值一律是**逻辑像素**（与 WPF 的 Window.Left/Top/Width/Height 同口径）：
/// 存物理像素会在 150% 缩放下每次重启都把窗口放大 1.5 倍。
#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "PascalCase", default)]
pub struct ShellSettings {
    pub width: Option<f64>,
    pub height: Option<f64>,
    pub left: Option<f64>,
    pub top: Option<f64>,
    pub maximized: bool,
    pub fullscreen: bool,
    pub port: i64,
    pub geometry_version: u32,
}

impl Default for ShellSettings {
    fn default() -> Self {
        Self {
            width: None,
            height: None,
            left: None,
            top: None,
            maximized: false,
            fullscreen: false,
            port: DEFAULT_PORT as i64,
            geometry_version: 0,
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
        settings.migrate_legacy_units();
        settings.normalize();
        settings
    }

    pub fn save(&self, path: Option<PathBuf>) -> std::io::Result<()> {
        let actual = path.unwrap_or_else(crate::paths::settings_path);
        let mut copy = self.clone();
        copy.geometry_version = GEOMETRY_VERSION;
        copy.normalize();
        if let Some(dir) = actual.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let temporary = actual.with_extension("json.tmp");
        let text = serde_json::to_string_pretty(&copy).unwrap_or_else(|_| "{}".to_string());
        std::fs::write(&temporary, text.as_bytes())?;
        std::fs::rename(&temporary, &actual)
    }

    /// 旧 Tauri 壳把物理像素写进了同一份文件，WPF 写的是逻辑像素，文件里没有单位标记。
    /// 用「逻辑屏装不下、按当前 DPI 换算后装得下」判定旧物理值并换算一次，
    /// 避免升级首启被 normalize 夹到接近全屏（WPF 的值本身装得下，不会被误换算）。
    fn migrate_legacy_units(&mut self) {
        if self.geometry_version >= GEOMETRY_VERSION {
            return;
        }
        if let Some(screen) = virtual_screen() {
            self.convert_physical_to_logical(system_scale(), screen);
        }
        self.geometry_version = GEOMETRY_VERSION;
    }

    fn convert_physical_to_logical(
        &mut self,
        scale: f64,
        screen: (f64, f64, f64, f64),
    ) -> bool {
        let (_, _, screen_width, screen_height) = screen;
        let (Some(width), Some(height)) = (self.width, self.height) else {
            return false;
        };
        if !scale.is_finite() || scale <= 1.0 || width <= 0.0 || height <= 0.0 {
            return false;
        }
        let fits_logical = width <= screen_width && height <= screen_height;
        let fits_physical = width / scale <= screen_width && height / scale <= screen_height;
        if fits_logical || !fits_physical {
            return false;
        }
        self.width = Some(width / scale);
        self.height = Some(height / scale);
        self.left = self.left.map(|value| value / scale);
        self.top = self.top.map(|value| value / scale);
        true
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

/// 虚拟屏幕（全部显示器并集）的左/上/宽/高，单位**逻辑像素**（与几何字段同口径）。
pub fn virtual_screen() -> Option<(f64, f64, f64, f64)> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    };
    let scale = system_scale();
    unsafe {
        let left = GetSystemMetrics(SM_XVIRTUALSCREEN) as f64 / scale;
        let top = GetSystemMetrics(SM_YVIRTUALSCREEN) as f64 / scale;
        let width = GetSystemMetrics(SM_CXVIRTUALSCREEN) as f64 / scale;
        let height = GetSystemMetrics(SM_CYVIRTUALSCREEN) as f64 / scale;
        if width <= 0.0 || height <= 0.0 {
            return None;
        }
        Some((left, top, width, height))
    }
}

/// 系统 DPI 缩放（150% → 1.5）。GetSystemMetrics 在 DPI 感知进程返回物理像素，
/// 在 DPI 不感知进程返回虚拟化后的逻辑像素；两边都除以 GetDpiForSystem()/96 即逻辑屏。
fn system_scale() -> f64 {
    use windows_sys::Win32::UI::HiDpi::GetDpiForSystem;
    let dpi = unsafe { GetDpiForSystem() };
    if dpi == 0 {
        1.0
    } else {
        dpi as f64 / 96.0
    }
}

#[allow(dead_code)]
pub fn settings_dir_hint() -> Option<&'static Path> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    const SCREEN_150: (f64, f64, f64, f64) = (0.0, 0.0, 1706.0, 960.0);

    fn legacy(width: f64, height: f64, left: f64, top: f64) -> ShellSettings {
        ShellSettings {
            width: Some(width),
            height: Some(height),
            left: Some(left),
            top: Some(top),
            ..Default::default()
        }
    }

    #[test]
    fn legacy_physical_geometry_is_converted() {
        let mut settings = legacy(1942.0, 1346.0, 342.0, 94.0);

        assert!(settings.convert_physical_to_logical(1.5, SCREEN_150));
        assert!((settings.width.unwrap() - 1942.0 / 1.5).abs() < 1e-9);
        assert!((settings.height.unwrap() - 1346.0 / 1.5).abs() < 1e-9);
        assert!((settings.left.unwrap() - 342.0 / 1.5).abs() < 1e-9);
        assert!((settings.top.unwrap() - 94.0 / 1.5).abs() < 1e-9);
    }

    #[test]
    fn logical_geometry_is_kept() {
        let mut settings = legacy(1200.0, 800.0, 100.0, 60.0);

        assert!(!settings.convert_physical_to_logical(1.5, SCREEN_150));
        assert_eq!(settings.width, Some(1200.0));
        assert_eq!(settings.left, Some(100.0));
    }

    #[test]
    fn conversion_is_skipped_at_100_percent_or_without_geometry() {
        let mut settings = legacy(1942.0, 1346.0, 342.0, 94.0);
        assert!(!settings.convert_physical_to_logical(1.0, SCREEN_150));

        let mut empty = ShellSettings::default();
        assert!(!empty.convert_physical_to_logical(1.5, SCREEN_150));
    }

    #[test]
    fn migration_stamps_version_and_save_roundtrips_it() {
        let mut settings = legacy(1200.0, 800.0, 100.0, 60.0);
        settings.migrate_legacy_units();
        assert_eq!(settings.geometry_version, GEOMETRY_VERSION);

        let dir = std::env::temp_dir().join(format!("demiurge-settings-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("desktop.json");
        settings.save(Some(path.clone())).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("\"GeometryVersion\": 2"));
        assert_eq!(ShellSettings::load(Some(path.clone())).geometry_version, GEOMETRY_VERSION);
        let _ = std::fs::remove_dir_all(dir);
    }
}
