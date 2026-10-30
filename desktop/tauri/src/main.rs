//! Demiurge 桌面壳（Tauri 2 版）。
//!
//! 壳的唯一合同：**同源 `http://127.0.0.1:8010` + 无业务 IPC**。
//!
//! 启动顺序是刻意的（2026-10-23 第三刀改）：**先开窗，再拉服务**。
//! 双击后一秒内就该看到一个窗口在逐项报进度——「检查端口 → 复用/拉起后端 → 等健康 →
//! 校验前端 → 图像服务 → 打开界面」。失败也停在窗口里写明原因，而不是静默退出
//! （旧写法是「全部就绪才建窗口」，于是探测失败时用户看到的就是"双击没反应"）。
//!
//! 关窗语义：正常状态下**关窗时询问**「隐藏到托盘 / 彻底关闭 / 取消」；
//! 隐藏时运行时继续跑（手机端要连就得常驻），彻底关闭会回收自己拉起的运行时再退出。
//! 启动失败时关窗即退出（没有需要保活的东西）。
//!
//! 托盘菜单（2026-10-24）：显示主窗口 / 重启后端 / 检查更新 / 彻底关闭。重启与退出都只处理
//! **自己拉起的**运行时；端口上复用的外部服务永不回收，重启项会明确拒绝而不是误杀。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod dialog;
mod http_probe;
mod job;
mod paths;
mod runtime_delivery;
mod runtime_host;
mod selftest;
mod settings;
mod shell_log;
mod single_instance;
mod updater;

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

use runtime_host::RuntimeHost;
use settings::ShellSettings;

fn main() {
    // 必须在读窗口几何之前声明 DPI 感知：Tauri 要等事件循环初始化才设置，
    // 而 DPI 不感知进程拿到的是虚拟化后的 96dpi 屏幕，150% 缩放下会把物理像素
    // 的旧值误判成逻辑像素（窗口被夹到接近全屏且位置偏移）。
    enable_dpi_awareness();
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--self-test") {
        std::process::exit(selftest::run());
    }
    if args.iter().any(|arg| arg == "--install-runtime") {
        std::process::exit(run_install_runtime());
    }
    std::process::exit(run_app());
}

/// `Demiurge.exe --install-runtime`：无界面部署运行时（安装版首启也会走同一模块）。
/// 进度直接打 stdout；这是维护入口，不是业务 IPC。
fn run_install_runtime() -> i32 {
    selftest::attach_console();
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.to_path_buf()))
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    println!("Demiurge 运行时安装：base + application + rag");
    println!("安装目录：{}", exe_dir.display());
    if runtime_delivery::is_portable_layout(&exe_dir) {
        println!("当前是便携包布局，运行时已随包提供，无需按需交付。");
        return 2;
    }
    let mut progress = |message: &str| println!("{message}");
    match runtime_delivery::ensure_installed_runtime(&exe_dir, &mut progress) {
        Ok(report) => {
            if !report.reused {
                println!("已部署层：{}", report.downloaded_layers.join("、"));
            }
            println!(
                "运行时已就绪：base={} application={} rag={}",
                report.base_id,
                report.application_id,
                if report.rag_id.is_empty() {
                    "未安装"
                } else {
                    report.rag_id.as_str()
                }
            );
            0
        }
        Err(err) => {
            println!("运行时安装失败：{err}");
            1
        }
    }
}

fn enable_dpi_awareness() {
    use windows_sys::Win32::UI::HiDpi::{
        SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
    };
    // 返回失败通常表示清单或宿主已设置过，后续 Tauri 会沿用现有感知级别。
    unsafe {
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    }
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

    let state = AppState {
        host: Arc::new(Mutex::new(None)),
        startup_failed: Arc::new(AtomicBool::new(false)),
        booting: Arc::new(AtomicBool::new(false)),
        restarting: Arc::new(AtomicBool::new(false)),
        quitting: Arc::new(AtomicBool::new(false)),
        updating: Arc::new(AtomicBool::new(false)),
        port,
    };
    let host_slot = Arc::clone(&state.host);
    let window_settings = settings.clone();
    let boot_state = state.clone();

    let app = tauri::Builder::default()
        .plugin(updater::plugin())
        .manage(state)
        .setup(move |app| {
            // ① 先把窗口亮出来（加载 boot 页），后面每一步都往这个页面里报。
            build_window(app, &window_settings)?;
            build_tray(app)?;

            let handle = app.handle().clone();
            let activation_handle = handle.clone();
            single_instance::spawn_activation_listener(move || activate(&activation_handle));

            // ② 启动链放后台线程：主线程要留在事件循环里，否则窗口画不出来。
            boot_state.booting.store(true, Ordering::SeqCst);
            std::thread::spawn(move || boot(handle, boot_state));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                save_main_geometry(window.app_handle());
                let app = window.app_handle().clone();
                let state = (*app.state::<AppState>()).clone();
                if state.quitting.load(Ordering::SeqCst) {
                    return;
                }
                if state.startup_failed.load(Ordering::SeqCst) {
                    // 启动就没成功：没有需要保活的运行时，关窗即退出。
                    shell_log::info("启动未成功，关窗即退出");
                    app.exit(0);
                    return;
                }
                api.prevent_close();
                match dialog::ask_close_action() {
                    dialog::CloseAction::HideToTray => {
                        let _ = window.hide();
                        shell_log::info("关窗：用户选择隐藏到托盘（运行时继续跑）");
                    }
                    dialog::CloseAction::Quit => {
                        shell_log::info("关窗：用户选择彻底关闭");
                        begin_quit(&app);
                    }
                    dialog::CloseAction::Cancel => {
                        shell_log::info("关窗：用户取消");
                    }
                }
            } else if matches!(event, WindowEvent::Moved(_) | WindowEvent::Resized(_)) {
                // 拖动/缩放也落盘（防抖），否则托盘退出或异常结束时最后一次几何丢失。
                schedule_geometry_save(window.app_handle());
                // 原生标题栏拖动收不到页内 mousemove：由壳转告页面，让壁纸实时渲染停帧。
                signal_window_motion(window.app_handle());
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

#[derive(Clone)]
struct AppState {
    host: Arc<Mutex<Option<RuntimeHost>>>,
    startup_failed: Arc<AtomicBool>,
    booting: Arc<AtomicBool>,
    restarting: Arc<AtomicBool>,
    quitting: Arc<AtomicBool>,
    updating: Arc<AtomicBool>,
    port: u16,
}

/// 启动链：逐项推给 boot 页，一项做完换下一项。
fn boot(handle: AppHandle, state: AppState) {
    let outcome = start_runtime(&handle, state.port);
    if state.quitting.load(Ordering::SeqCst) {
        if let Ok(mut host) = outcome {
            host.stop();
        }
        state.booting.store(false, Ordering::SeqCst);
        return;
    }
    match outcome {
        Ok(host) => {
            if let Ok(mut slot) = state.host.lock() {
                *slot = Some(host);
            }
            state.startup_failed.store(false, Ordering::SeqCst);
            step(&handle, "正在打开界面…");
            if let Some(window) = handle.get_webview_window("main") {
                let url = format!("http://127.0.0.1:{}/", state.port);
                let _ = window.eval(&format!("location.replace({});", json_string(&url)));
            }
        }
        Err(detail) => {
            shell_log::warn(&format!("运行时未能就绪：{detail}"));
            state.startup_failed.store(true, Ordering::SeqCst);
            if let Some(window) = handle.get_webview_window("main") {
                let _ = window.eval(&format!(
                    "if (typeof window.__dsh_fail === 'function') window.__dsh_fail({}, {});",
                    json_string("启动失败"),
                    json_string(&detail)
                ));
            }
        }
    }
    state.booting.store(false, Ordering::SeqCst);
}

/// 拉起并等就绪。启动与托盘「重启后端」共用同一条路径，避免两套启动逻辑漂移。
fn start_runtime(handle: &AppHandle, port: u16) -> Result<RuntimeHost, String> {
    let mut host = RuntimeHost::new(port);
    let push = |message: &str| step(handle, message);
    host.ensure_ready(push).map_err(|err| err.to_string())?;

    // 图像服务不阻塞进入界面：ComfyUI 冷启 8~10 分钟，这里只报状态。
    step(handle, "检查图像服务（ComfyUI）…");
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
    step(handle, comfy);
    Ok(host)
}

/// 推进一步：写壳日志 + 更新 boot 页那一行。
fn step(handle: &AppHandle, message: &str) {
    shell_log::info(message);
    if let Some(window) = handle.get_webview_window("main") {
        // 托盘重启时当前页面已是业务界面，没有 boot 页的钩子；有才调，避免控制台报错。
        let _ = window.eval(&format!(
            "if (typeof window.__dsh_step === 'function') window.__dsh_step({});",
            json_string(message)
        ));
    }
}

fn json_string(text: &str) -> String {
    serde_json::to_string(text).unwrap_or_else(|_| "\"\"".to_string())
}

/// WebView2 数据目录候选：`%LOCALAPPDATA%` → exe 同级 → temp。
///
/// 🔴 两条实测约束：
/// ① 默认目录（exe 同目录 / `%LOCALAPPDATA%\<identifier>`）在本机建不出来 ⇒ 必须显式指定；
/// ② **只 `create_dir_all` 成功不算可用**：低完整性进程能"看到"Medium 标签的目录却打不开
///    WebView2（报 `0x800700AA 请求的资源在使用中`），所以必须真写一个探针文件才算可用，
///    并且建窗失败要换下一个候选重试 —— 否则就是"双击一瞬间关闭"。
fn candidate_data_dirs() -> Vec<std::path::PathBuf> {
    let mut candidates = vec![paths::webview_data_dir()];
    if let Some(dir) = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|parent| parent.join("webview-data")))
    {
        candidates.push(dir);
    }
    candidates.push(std::env::temp_dir().join("demiurge-webview2"));
    candidates
}

fn writable(dir: &std::path::Path) -> bool {
    if !dir.is_dir() && std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let probe = dir.join(".write-probe");
    let ok = std::fs::write(&probe, b"x").is_ok();
    let _ = std::fs::remove_file(&probe);
    ok
}

fn build_window(
    app: &mut tauri::App,
    window_settings: &ShellSettings,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut last_error: Option<String> = None;
    for data_dir in candidate_data_dirs() {
        if !writable(&data_dir) {
            shell_log::warn(&format!("WebView2 数据目录不可写，跳过：{}", data_dir.display()));
            continue;
        }
        match build_window_with(app, window_settings, data_dir.clone()) {
            Ok(()) => {
                shell_log::info(&format!("WebView2 数据目录：{}", data_dir.display()));
                return Ok(());
            }
            Err(err) => {
                shell_log::warn(&format!(
                    "用数据目录 {} 建窗口失败：{err}",
                    data_dir.display()
                ));
                last_error = Some(err.to_string());
            }
        }
    }
    return Err(last_error.unwrap_or_else(|| "所有候选 WebView2 数据目录都不可用".to_string()).into());
}

fn build_window_with(
    app: &mut tauri::App,
    window_settings: &ShellSettings,
    data_dir: std::path::PathBuf,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("boot.html".into()))
        .title("Demiurge")
        .inner_size(
            window_settings.width.unwrap_or(settings::DEFAULT_WIDTH),
            window_settings.height.unwrap_or(settings::DEFAULT_HEIGHT),
        )
        .data_directory(data_dir)
        // 导航白名单（对齐 WPF 壳 MainWindow 的 OnNavigationStarting/OnNewWindowRequested）：
        // 主窗口只留本应用自己的页面，其余导航取消并转系统浏览器——外链不应把壳带往外站。
        .on_navigation(|url| {
            if navigation_allowed(url) {
                true
            } else {
                open_in_system_browser(url.as_str());
                false
            }
        })
        // 新窗口（target=_blank）一律不开应用内窗：外链交系统浏览器（WPF 壳同口径）。
        .on_new_window(|url, _features| {
            open_in_system_browser(url.as_str());
            tauri::webview::NewWindowResponse::Deny
        });
    if let (Some(left), Some(top)) = (window_settings.left, window_settings.top) {
        builder = builder.position(left, top);
    }
    let window = builder.build()?;
    if window_settings.fullscreen {
        let _ = window.set_fullscreen(true);
    } else if window_settings.maximized {
        let _ = window.maximize();
    }
    Ok(())
}

/// 导航白名单判定：只放行本应用自己的页面——tauri 协议页（boot.html 兜底页，Windows 上
/// 表现为 http(s)://tauri.localhost）与回环 http(s)（后端送达的应用页、ComfyUI 等本机服务，
/// 端口由协商决定故不限）。其余 scheme（file:/data:/javascript:）与外站主机一律拒绝。
fn navigation_allowed(url: &tauri::Url) -> bool {
    match url.scheme() {
        "tauri" | "about" => true,
        "http" | "https" => {
            let host = url.host_str().unwrap_or("");
            host.eq_ignore_ascii_case("tauri.localhost") || is_loopback_host(host)
        }
        _ => false,
    }
}

fn is_loopback_host(host: &str) -> bool {
    if host.eq_ignore_ascii_case("localhost") {
        return true;
    }
    // url crate 的 host_str() 对 IPv6 返回带方括号的 "[::1]"
    let bare = host.trim_start_matches('[').trim_end_matches(']');
    bare.parse::<std::net::IpAddr>()
        .map(|ip| ip.is_loopback())
        .unwrap_or(false)
}

/// 被拒导航/新窗口转交系统默认浏览器。explorer 直接接 URL，不经 cmd 元字符解析；
/// 壳是 Windows-only，非 Windows 仅留痕（编译可过）。
#[cfg(target_os = "windows")]
fn open_in_system_browser(url: &str) {
    shell_log::warn(&format!("已拦截非本机导航，转交系统浏览器：{url}"));
    let _ = std::process::Command::new("explorer").arg(url).spawn();
}

#[cfg(not(target_os = "windows"))]
fn open_in_system_browser(url: &str) {
    shell_log::warn(&format!("已拦截非本机导航（平台无系统浏览器回调，仅记录）：{url}"));
}

/// 托盘：左键唤出窗口，菜单「显示主窗口 / 重启后端 / 彻底关闭」。
/// 关窗后壳还活着，全靠这块把入口留住；彻底关闭会先回收运行时再退出。
fn build_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let show_item = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
    let restart_item = MenuItem::with_id(app, "restart", "重启后端", true, None::<&str>)?;
    let update_item = MenuItem::with_id(app, "update", "检查更新…", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "彻底关闭", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show_item, &restart_item, &update_item, &quit_item])?;
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
            "restart" => restart_backend(app),
            "update" => check_updates(app),
            "quit" => begin_quit(app),
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

/// 托盘「检查更新…」：启动/重启过程中拒绝，避免更新安装器与后端启动互相踩。
fn check_updates(app: &AppHandle) {
    let state = (*app.state::<AppState>()).clone();
    if state.quitting.load(Ordering::SeqCst) {
        return;
    }
    if state.booting.load(Ordering::SeqCst) || state.restarting.load(Ordering::SeqCst) {
        dialog::show_info(
            "请稍候",
            "Demiurge 正在启动或重启后端，请等完成后再检查更新。",
        );
        return;
    }
    shell_log::info("托盘菜单：检查更新");
    updater::check_for_updates(app.clone(), state.host, state.updating);
}

/// 彻底退出：先回收自己拉起的运行时，再退出壳（前端窗口随进程关闭）。
/// 复用的外部服务不在 `child` 里，`stop()` 不会碰它。
fn begin_quit(app: &AppHandle) {
    let state = (*app.state::<AppState>()).clone();
    if state.updating.load(Ordering::SeqCst) {
        dialog::show_info(
            "正在更新",
            "更新正在下载或安装。请等它完成后再关闭 Demiurge（安装器会自动重启）。",
        );
        return;
    }
    if state.quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    shell_log::info("彻底关闭：回收运行时并退出桌面壳");
    if let Some(window) = app.get_webview_window("main") {
        persist_geometry(&window);
        let _ = window.hide();
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        let host = state.host.lock().ok().and_then(|mut slot| slot.take());
        if let Some(mut host) = host {
            host.stop();
        }
        shell_log::info("运行时已回收，退出桌面壳");
        handle.exit(0);
    });
}

/// 托盘「重启后端」：停掉自启运行时 → 重新拉起 → 等健康 → 重新载入界面。
/// 外部复用的服务不是壳拉起的，明确拒绝而不是误杀（见 runtime_host 的复用合同）。
fn restart_backend(app: &AppHandle) {
    let state = (*app.state::<AppState>()).clone();
    if state.quitting.load(Ordering::SeqCst) {
        return;
    }
    if state.booting.load(Ordering::SeqCst) {
        dialog::show_info("正在启动", "Demiurge 仍在启动，请等启动完成后再重启后端。");
        return;
    }
    if state.updating.load(Ordering::SeqCst) {
        dialog::show_info("正在更新", "更新正在下载或安装，请等完成后再重启后端。");
        return;
    }
    if state.restarting.swap(true, Ordering::SeqCst) {
        shell_log::info("后端正在重启，忽略重复请求");
        return;
    }
    shell_log::info("托盘菜单：重启后端");
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_title("Demiurge · 正在重启后端…");
    }

    let handle = app.clone();
    std::thread::spawn(move || {
        let old = state.host.lock().ok().and_then(|mut slot| slot.take());
        if let Some(mut host) = old {
            if host.reused_external() {
                if let Ok(mut slot) = state.host.lock() {
                    *slot = Some(host);
                }
                restore_title(&handle);
                state.restarting.store(false, Ordering::SeqCst);
                shell_log::info("重启后端被拒绝：当前后端是外部复用的服务");
                dialog::show_warning(
                    "无法重启后端",
                    "当前后端不是由桌面壳启动的（端口上原本已有服务），\n\
                     桌面壳不会终止外部进程。请手动重启该后端。",
                );
                return;
            }
            host.stop();
        }
        if state.quitting.load(Ordering::SeqCst) {
            restore_title(&handle);
            state.restarting.store(false, Ordering::SeqCst);
            return;
        }

        match start_runtime(&handle, state.port) {
            Ok(host) => {
                if state.quitting.load(Ordering::SeqCst) {
                    let mut host = host;
                    host.stop();
                } else {
                    if let Ok(mut slot) = state.host.lock() {
                        *slot = Some(host);
                    }
                    state.startup_failed.store(false, Ordering::SeqCst);
                    if let Some(window) = handle.get_webview_window("main") {
                        let _ = window.set_title("Demiurge");
                        let url = format!("http://127.0.0.1:{}/", state.port);
                        let _ = window.eval(&format!("location.replace({});", json_string(&url)));
                    }
                    shell_log::info("后端已重启并就绪，界面已重新载入");
                }
            }
            Err(detail) => {
                shell_log::warn(&format!("重启后端失败：{detail}"));
                state.startup_failed.store(true, Ordering::SeqCst);
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.set_title("Demiurge · 后端未就绪");
                }
                dialog::show_error("重启后端失败", &detail);
            }
        }
        state.restarting.store(false, Ordering::SeqCst);
    });
}

fn restore_title(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_title("Demiurge");
    }
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

fn save_main_geometry(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        persist_geometry(&window);
    }
}

/// 移动/缩放事件非常密集：合并成一次落盘（700ms 内最后一次生效）。
static GEOMETRY_SEQ: AtomicU64 = AtomicU64::new(0);

/// 同一次窗口拖动里，通知页面「正在移动」的节流窗口。
static WINDOW_MOTION_AT: AtomicU64 = AtomicU64::new(0);

/// 拖动/缩放原生窗口时，页面收不到指针事件；用节流后的自定义事件让壁纸层停帧。
/// 页面侧按 180ms 空闲恢复，所以这里 120ms 一次的节拍足以维持「运动中」状态。
fn signal_window_motion(app: &AppHandle) {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    let last = WINDOW_MOTION_AT.load(Ordering::Relaxed);
    if now.saturating_sub(last) < 120 {
        return;
    }
    WINDOW_MOTION_AT.store(now, Ordering::Relaxed);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval("window.dispatchEvent(new Event('demiurge:window-move'))");
    }
}

fn schedule_geometry_save(app: &AppHandle) {
    let seq = GEOMETRY_SEQ.fetch_add(1, Ordering::SeqCst) + 1;
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(700));
        if GEOMETRY_SEQ.load(Ordering::SeqCst) == seq {
            save_main_geometry(&app);
        }
    });
}

/// 保存窗口几何与窗口状态。三条实测约束：
/// ① 几何存**逻辑像素**（除以 scale_factor），否则 150% 缩放下每次重启放大 1.5 倍；
/// ② 尺寸取 `inner_size`（与建窗的 `.inner_size()` 同口径），位置取 `outer_position`（与 `.position()` 同口径），
///    混用 outer/inner 会让窗口每次开关都长一圈边框；
/// ③ 最大化/全屏时保留上一次常态几何（否则取消最大化后回不到原尺寸），最小化时整体跳过。
fn persist_geometry(window: &tauri::WebviewWindow) {
    if window.is_minimized().unwrap_or(false) {
        return;
    }
    let fullscreen = window.is_fullscreen().unwrap_or(false);
    let maximized = window.is_maximized().unwrap_or(false);
    let scale = window.scale_factor().unwrap_or(1.0);
    let mut settings = ShellSettings::load(None);
    if !fullscreen && !maximized && scale.is_finite() && scale > 0.0 {
        if let Ok(size) = window.inner_size() {
            if size.width > 0 && size.height > 0 {
                settings.width = Some(size.width as f64 / scale);
                settings.height = Some(size.height as f64 / scale);
            }
        }
        if let Ok(position) = window.outer_position() {
            settings.left = Some(position.x as f64 / scale);
            settings.top = Some(position.y as f64 / scale);
        }
    }
    settings.maximized = maximized;
    settings.fullscreen = fullscreen;
    if let Err(err) = settings.save(None) {
        shell_log::warn(&format!("保存窗口几何失败：{err}"));
    }
}

#[cfg(test)]
mod navigation_tests {
    use super::{is_loopback_host, navigation_allowed};

    fn url(s: &str) -> tauri::Url {
        tauri::Url::parse(s).unwrap()
    }

    #[test]
    fn 本应用页面放行() {
        // 后端送达的应用页（端口协商，不限）与 tauri 协议兜底页
        assert!(navigation_allowed(&url("http://127.0.0.1:8010/")));
        assert!(navigation_allowed(&url("http://127.0.0.1:8010/api/user-state")));
        assert!(navigation_allowed(&url("http://localhost:8021/index.html")));
        assert!(navigation_allowed(&url("http://[::1]:8010/")));
        assert!(navigation_allowed(&url("https://127.0.0.1:8010/")));
        assert!(navigation_allowed(&url("http://tauri.localhost/boot.html")));
        assert!(navigation_allowed(&url("tauri://localhost/boot.html")));
        assert!(navigation_allowed(&url("about:blank")));
    }

    #[test]
    fn 外站与异常scheme拒绝() {
        assert!(!navigation_allowed(&url("http://evil.com/")));
        assert!(!navigation_allowed(&url("https://192.168.1.5:8010/")));
        // 前缀伪装不是回环：主机名判定按整体，不做子串
        assert!(!navigation_allowed(&url("http://127.0.0.1.evil.com/")));
        assert!(!navigation_allowed(&url("file:///C:/Windows/win.ini")));
        assert!(!navigation_allowed(&url("data:text/html,<script>alert(1)</script>")));
    }

    #[test]
    fn loopback判定() {
        assert!(is_loopback_host("127.0.0.1"));
        assert!(is_loopback_host("127.8.8.8"));
        assert!(is_loopback_host("LOCALHOST"));
        assert!(is_loopback_host("::1"));
        assert!(!is_loopback_host("192.168.1.5"));
        assert!(!is_loopback_host("localhost.attacker.com"));
        assert!(!is_loopback_host(""));
    }
}
