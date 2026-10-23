//! Demiurge 桌面壳（Tauri 2 版）。
//!
//! 壳的唯一合同：**同源 `http://127.0.0.1:8010` + 无业务 IPC**。
//! 窗口、单实例、运行时生命周期、自检都在这份合同里；业务全在后端与前端，壳不掺和。
//!
//! 用法：
//! - `demiurge-shell`              正常启动（找不到运行时会把原因写进 logs\desktop.log）
//! - `demiurge-shell --self-test`  跑 15 项自检（与 WPF 壳同一份判据）
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod http_probe;
mod job;
mod paths;
mod runtime_host;
mod selftest;
mod settings;
mod shell_log;

use std::sync::{Arc, Mutex};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

use runtime_host::RuntimeHost;
use settings::ShellSettings;

fn main() {
    if std::env::args().any(|arg| arg == "--self-test") {
        std::process::exit(selftest::run());
    }
    std::process::exit(run_app());
}

fn run_app() -> i32 {
    let _guard = match single_instance_guard() {
        Some(guard) => guard,
        None => {
            shell_log::info("已有实例在运行，本次启动退出（激活既有窗口留给托盘/单实例增强刀）。");
            return 0;
        }
    };

    let settings = ShellSettings::load(None);
    let port = match runtime_host::resolve_port(&settings) {
        Ok(port) => port,
        Err(err) => {
            shell_log::warn(&format!("端口解析失败：{err}"));
            return 2;
        }
    };

    let mut host = RuntimeHost::new(port);
    if let Err(err) = host.ensure_ready(|message| shell_log::info(message)) {
        shell_log::warn(&format!("运行时未能就绪：{err}"));
        return 3;
    }

    let host = Arc::new(Mutex::new(host));
    let url = format!("http://127.0.0.1:{port}");
    let window_settings = settings.clone();
    let host_for_exit = Arc::clone(&host);

    let app = tauri::Builder::default()
        .manage(AppState {
            host: Arc::clone(&host),
        })
        .setup(move |app| {
            let data_dir = paths::webview_data_dir();
            // 🔴 本机实测：WebView2 默认数据目录建不出来（Io os error 5），必须显式指定。
            std::fs::create_dir_all(&data_dir)?;
            let mut builder = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(url.parse().expect("合法 URL")),
            )
            .title("Demiurge")
            .inner_size(
                window_settings.width.unwrap_or(settings::DEFAULT_WIDTH),
                window_settings.height.unwrap_or(settings::DEFAULT_HEIGHT),
            )
            .data_directory(data_dir);
            if let (Some(left), Some(top)) = (window_settings.left, window_settings.top) {
                builder = builder.position(left, top);
            }
            let window = builder.build()?;
            if window_settings.maximized {
                let _ = window.maximize();
            }
            Ok(())
        })
        .on_window_event(move |window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                persist_geometry(window);
                if let Ok(mut host) = host_for_exit.lock() {
                    host.stop();
                }
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!());

    match app {
        Ok(app) => {
            app.run(|_, _| {});
            0
        }
        Err(err) => {
            shell_log::warn(&format!("窗口初始化失败：{err}"));
            if let Ok(mut host) = host.lock() {
                host.stop();
            }
            4
        }
    }
}

struct AppState {
    #[allow(dead_code)]
    host: Arc<Mutex<RuntimeHost>>,
}

fn persist_geometry(window: &tauri::Window) {
    let mut settings = ShellSettings::load(None);
    if let Ok(size) = window.outer_size() {
        settings.width = Some(size.width as f64);
        settings.height = Some(size.height as f64);
    }
    if let Ok(position) = window.outer_position() {
        settings.left = Some(position.x as f64);
        settings.top = Some(position.y as f64);
    }
    settings.maximized = window.is_maximized().unwrap_or(false);
    if let Err(err) = settings.save(None) {
        shell_log::warn(&format!("保存窗口几何失败：{err}"));
    }
}

/// 单实例：命名互斥量（`Local\` 命名空间＝同用户会话内唯一）。
/// 返回的句柄要活到进程结束 —— 否则互斥量释放，第二个实例就能起来。
fn single_instance_guard() -> Option<isize> {
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows_sys::Win32::System::Threading::CreateMutexW;

    let wide: Vec<u16> = paths::SINGLE_INSTANCE_MUTEX
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let handle = CreateMutexW(std::ptr::null(), 0, wide.as_ptr());
        if handle.is_null() {
            return None;
        }
        if GetLastError() == ERROR_ALREADY_EXISTS {
            return None;
        }
        Some(handle as isize)
    }
}
