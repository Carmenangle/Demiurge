//! Demiurge 桌面壳（Tauri 2 版）。
//!
//! 壳的唯一合同：**同源 `http://127.0.0.1:8010` + 无业务 IPC**。
//!
//! 启动顺序是刻意的（2026-10-23 第三刀改）：**先开窗，再拉服务**。
//! 双击后一秒内就该看到一个窗口在逐项报进度——「检查端口 → 复用/拉起后端 → 等健康 →
//! 校验前端 → 图像服务 → 打开界面」。失败也停在窗口里写明原因，而不是静默退出
//! （旧写法是「全部就绪才建窗口」，于是探测失败时用户看到的就是"双击没反应"）。
//!
//! 关窗语义：正常状态下**关窗 = 隐藏到托盘**（运行时继续跑，手机端要连就得常驻）；
//! 启动失败时关窗即退出（没有需要保活的东西）。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod http_probe;
mod job;
mod paths;
mod runtime_host;
mod selftest;
mod settings;
mod shell_log;
mod single_instance;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

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

    let startup_failed = Arc::new(AtomicBool::new(false));
    let host_slot: Arc<Mutex<Option<RuntimeHost>>> = Arc::new(Mutex::new(None));
    let window_settings = settings.clone();
    let failed_for_event = Arc::clone(&startup_failed);
    let host_for_state = Arc::clone(&host_slot);

    let app = tauri::Builder::default()
        .manage(AppState {
            host: host_for_state,
            startup_failed: Arc::clone(&startup_failed),
        })
        .setup(move |app| {
            // ① 先把窗口亮出来（加载 boot 页），后面每一步都往这个页面里报。
            build_window(app, &window_settings)?;
            build_tray(app)?;

            let handle = app.handle().clone();
            let activation_handle = handle.clone();
            single_instance::spawn_activation_listener(move || activate(&activation_handle));

            // ② 启动链放后台线程：主线程要留在事件循环里，否则窗口画不出来。
            std::thread::spawn(move || boot(handle, port));
            Ok(())
        })
        .on_window_event(move |window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                persist_geometry(window);
                if failed_for_event.load(Ordering::Relaxed) {
                    // 启动就没成功：没有需要保活的运行时，关窗即退出。
                    shell_log::info("启动未成功，关窗即退出");
                    window.app_handle().exit(0);
                } else {
                    api.prevent_close();
                    let _ = window.hide();
                    shell_log::info("关窗 = 隐藏到托盘（运行时继续跑，退出请走托盘菜单）");
                }
            }
        })
        .build(tauri::generate_context!());

    match app {
        Ok(app) => {
            app.run(|_, _| {});
            if let Ok(mut slot) = host_slot.lock() {
                if let Some(host) = slot.as_mut() {
                    host.stop();
                }
            }
            0
        }
        Err(err) => {
            shell_log::warn(&format!("窗口初始化失败：{err}"));
            4
        }
    }
}

struct AppState {
    host: Arc<Mutex<Option<RuntimeHost>>>,
    startup_failed: Arc<AtomicBool>,
}

/// 启动链：逐项推给 boot 页，一项做完换下一项。
fn boot(handle: AppHandle, port: u16) {
    step(&handle, &format!("检查 127.0.0.1:{port} 上的服务…"));
    let mut host = RuntimeHost::new(port);
    let push = |message: &str| step(&handle, message);

    match host.ensure_ready(push) {
        Ok(()) => {
            // 图像服务不阻塞进入界面：ComfyUI 冷启 8~10 分钟，这里只报状态。
            step(&handle, "检查图像服务（ComfyUI）…");
            let comfy = match http_probe::get(port, "/api/comfyui/status", std::time::Duration::from_millis(2000)) {
                Some(response) if response.status == 200 => {
                    let running = response.body.contains("\"running\":true")
                        || response.body.contains("\"running\": true");
                    if running {
                        "图像服务：运行中"
                    } else {
                        "图像服务：未运行（后端会在后台拉起，首次冷启较慢）"
                    }
                }
                _ => "图像服务：状态未知（不影响使用）",
            };
            step(&handle, comfy);

            step(&handle, "正在打开界面…");
            if let Some(window) = handle.get_webview_window("main") {
                let url = format!("http://127.0.0.1:{port}/");
                let _ = window.eval(&format!("location.replace({});", json_string(&url)));
            }
            if let Some(state) = handle.try_state::<AppState>() {
                if let Ok(mut slot) = state.host.lock() {
                    *slot = Some(host);
                }
            }
        }
        Err(err) => {
            let detail = err.to_string();
            shell_log::warn(&format!("运行时未能就绪：{detail}"));
            if let Some(state) = handle.try_state::<AppState>() {
                state.startup_failed.store(true, Ordering::Relaxed);
            }
            if let Some(window) = handle.get_webview_window("main") {
                let _ = window.eval(&format!(
                    "window.__dsh_fail({}, {});",
                    json_string("启动失败"),
                    json_string(&detail)
                ));
            }
        }
    }
}

/// 推进一步：写壳日志 + 更新 boot 页那一行。
fn step(handle: &AppHandle, message: &str) {
    shell_log::info(message);
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(&format!("window.__dsh_step({});", json_string(message)));
    }
}

fn json_string(text: &str) -> String {
    serde_json::to_string(text).unwrap_or_else(|_| "\"\"".to_string())
}

/// WebView2 数据目录：三级回退。
///
/// 🔴 两条实测约束：
/// ① 默认目录（exe 同目录 / `%LOCALAPPDATA%\<identifier>`）在本机建不出来 ⇒ 必须显式指定；
/// ② 受限上下文（低完整性 / 沙箱派生的进程）写不了 `%LOCALAPPDATA%` ⇒ 建目录失败不能直接
///    panic 掉整个窗口，回退到 exe 同级的 `webview-data` 再回退到 temp，并把选中路径写进日志。
fn pick_webview_data_dir() -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
    let mut candidates = vec![paths::webview_data_dir()];
    if let Some(dir) = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|parent| parent.join("webview-data")))
    {
        candidates.push(dir);
    }
    candidates.push(std::env::temp_dir().join("demiurge-webview2"));

    for dir in &candidates {
        match std::fs::create_dir_all(dir) {
            Ok(()) => {
                shell_log::info(&format!("WebView2 数据目录：{}", dir.display()));
                return Ok(dir.clone());
            }
            Err(err) => shell_log::warn(&format!(
                "WebView2 数据目录不可用：{}（{err}）",
                dir.display()
            )),
        }
    }
    Err("所有候选 WebView2 数据目录都不可用".into())
}

fn build_window(
    app: &mut tauri::App,
    window_settings: &ShellSettings,
) -> Result<(), Box<dyn std::error::Error>> {
    let data_dir = pick_webview_data_dir()?;
    let mut builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("boot.html".into()))
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
                    if let Ok(mut slot) = state.host.lock() {
                        if let Some(host) = slot.as_mut() {
                            host.stop();
                        }
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
