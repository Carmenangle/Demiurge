//! Demiurge 桌面壳（Tauri 2 版）。
//!
//! 壳的唯一合同：**同源 `http://127.0.0.1:8010` + 无业务 IPC**。
//! 窗口、单实例、托盘、运行时生命周期、自检都在这份合同里；业务全在后端与前端，壳不掺和。
//!
//! 关窗语义（本刀定死）：**关窗 = 隐藏到托盘**，运行时继续跑（手机端要连就必须常驻）；
//! 真正退出只走托盘菜单「退出」。
//!
//! 用法：
//! - `demiurge-shell`              正常启动
//! - `demiurge-shell --self-test`  跑 15 项自检（与 WPF 壳同一份判据）
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod http_probe;
mod job;
mod paths;
mod runtime_host;
mod selftest;
mod settings;
mod shell_log;
mod single_instance;

use std::sync::{Arc, Mutex};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

use runtime_host::RuntimeHost;
use settings::ShellSettings;

fn main() {
    if std::env::args().any(|arg| arg == "--self-test") {
        std::process::exit(selftest::run());
    }
    std::process::exit(run_app());
}

fn run_app() -> i32 {
    let _guard = match single_instance::acquire() {
        Some(guard) => guard,
        None => {
            // 已有实例：发一次激活信号就退出（对齐 WPF 的「第二实例唤出第一实例」）。
            if single_instance::signal_existing() {
                shell_log::info("已有实例在运行，已请它把窗口拉出来；本次启动退出。");
            } else {
                shell_log::info("已有实例在运行，但没能唤起它（激活事件不存在）；本次启动退出。");
            }
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
    let host_for_state = Arc::clone(&host);

    let app = tauri::Builder::default()
        .manage(AppState {
            host: host_for_state,
        })
        .setup(move |app| {
            build_window(app, &url, &window_settings)?;
            build_tray(app)?;
            let handle = app.handle().clone();
            single_instance::spawn_activation_listener(move || activate(&handle));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // 关窗 = 隐藏：运行时不停，手机端还要连。
                api.prevent_close();
                persist_geometry(window);
                let _ = window.hide();
                shell_log::info("关窗 = 隐藏到托盘（运行时继续跑，退出请走托盘菜单）");
            }
        })
        .build(tauri::generate_context!());

    match app {
        Ok(app) => {
            app.run(|_, _| {});
            if let Ok(mut host) = host.lock() {
                host.stop();
            }
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
    host: Arc<Mutex<RuntimeHost>>,
}

fn build_window(
    app: &mut tauri::App,
    url: &str,
    window_settings: &ShellSettings,
) -> Result<(), Box<dyn std::error::Error>> {
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
}

/// 托盘：左键唤出窗口，菜单「显示主窗口 / 退出」。关窗后壳还活着，全靠这块把入口留住。
fn build_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let show_item = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show_item, &quit_item])?;
    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or("缺少窗口图标，托盘起不来")?;

    TrayIconBuilder::with_id("main-tray")
        .icon(icon)
        .tooltip("Demiurge")
        .menu(&menu)
        // 左键留给「唤出窗口」，菜单走右键（与 Windows 托盘习惯一致）。
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => activate(app),
            "quit" => {
                if let Some(state) = app.try_state::<AppState>() {
                    if let Ok(mut host) = state.host.lock() {
                        host.stop();
                    }
                }
                shell_log::info("托盘菜单退出：已回收自己拉起的运行时");
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                activate(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

/// 把主窗口从隐藏/最小化里拉出来并置前。
fn activate(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        shell_log::info("已唤出主窗口");
    }
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
