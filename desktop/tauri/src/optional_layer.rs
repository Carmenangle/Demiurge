//! 可选层（RAG）的后台补齐编排（P3-b）。
//!
//! 三条入口，一个出口：进应用后的那一次询问、托盘「补齐 RAG」、询问被拒后托盘那条常驻入口。
//! 交付本体在 `runtime_delivery::backfill_optional_runtime`，这里只管「什么时候允许开跑、
//! 跑完怎么收场」，因此**不新增设置项、不加启用开关**：不补齐就一直不补齐，功能级降级由 P3-c 管。
//!
//! 两条硬纪律：
//! - **绝不自动重启后端**。补齐成功只弹一条「重启后生效，现在重启吗」的建议，用户点头才调
//!   既有的 `restart_backend`（它自己带四条护栏和 `location.replace`）。正在生成时被打断
//!   是用户能看见的代价，必须由用户决定。
//! - **单飞**。同一时间只允许一条补齐在跑；补齐期间拒绝重启后端与检查更新，避免两个写入者
//!   同时改 `current.json`。托盘与启动两条入口共用同一个 `delivering` 标志。
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use tauri::{AppHandle, Manager};

use crate::dialog;
use crate::paths;
use crate::restart_backend;
use crate::runtime_delivery;
use crate::settings::ShellSettings;
use crate::shell_log;
use crate::AppState;

/// 进应用后的那一次询问（每次安装只问一回）。
///
/// 只在启动链成功、且这台机器真缺可选层时才问；开发态仓库布局读不到 `current.json`，
/// `optional_layer_missing` 返回 `false`，于是一句话都不会弹。
pub fn offer_after_boot(handle: &AppHandle) {
    let state = (*handle.state::<AppState>()).clone();
    if state.quitting.load(Ordering::SeqCst) || state.startup_failed.load(Ordering::SeqCst) {
        return;
    }
    let settings = ShellSettings::load(None);
    if settings.optional_layer_offer_done {
        return;
    }
    if !runtime_delivery::optional_layer_missing(&paths::home()) {
        // 这个版本的运行时已经带着 RAG 挂上了：把「问过了」钉死，免得每次启动都白读一遍档。
        mark_offer_done();
        return;
    }
    let accepted = dialog::ask_confirm(
        "补齐 RAG 组件",
        "Demiurge 可以在后台下载 RAG 组件（约 2 GB），用于语义检索与增强生成。\n\
         下载在后台进行，不挡当前界面，进度会出现在「后台活动」面板里。\n\n\
         要现在开始下载吗？（也可以以后从托盘菜单「补齐 RAG」再开始）",
    );
    if !accepted {
        mark_offer_done();
        shell_log::info("用户选择暂不补齐 RAG 组件；托盘菜单「补齐 RAG」可随时开始。");
        return;
    }
    start(handle);
}

/// 托盘「补齐 RAG」。已在补齐、正在启动/更新/退出时都不抢跑。
pub fn start(handle: &AppHandle) {
    let state = (*handle.state::<AppState>()).clone();
    if state.quitting.load(Ordering::SeqCst) {
        return;
    }
    if state.booting.load(Ordering::SeqCst) {
        dialog::show_info("正在启动", "Demiurge 仍在启动，请等启动完成后再补齐 RAG 组件。");
        return;
    }
    if state.updating.load(Ordering::SeqCst) {
        dialog::show_info("正在更新", "更新正在下载或安装，请等完成后再补齐 RAG 组件。");
        return;
    }
    if !runtime_delivery::optional_layer_missing(&paths::home()) {
        shell_log::info("托盘菜单：补齐 RAG——当前没有需要补齐的可选组件");
        dialog::show_info(
            "无需补齐",
            "当前运行时已经包含 RAG 组件，或这台机器不是按需交付的安装版布局。",
        );
        return;
    }
    if state.delivering.swap(true, Ordering::SeqCst) {
        shell_log::info("RAG 组件正在补齐中，忽略重复请求");
        dialog::show_info("正在补齐", "RAG 组件已在后台下载，进度见「后台活动」面板。");
        return;
    }
    shell_log::info("开始后台补齐 RAG 组件");
    let handle = handle.clone();
    std::thread::spawn(move || {
        let mut progress = |message: &str| shell_log::info(&format!("[补齐 RAG] {message}"));
        let outcome = runtime_delivery::backfill_optional_runtime(&install_dir(), &mut progress);
        // 交付已经收尾（成功或失败），先放开单飞标志，再决定要不要请用户重启。
        state.delivering.store(false, Ordering::SeqCst);
        match outcome {
            Ok(report)
                if report.downloaded_layers.is_empty() && report.rag_id.trim().is_empty() =>
            {
                // 这个版本的清单根本不提供可选组件：别再问了，也别再留一条托盘幻觉。
                mark_offer_done();
                shell_log::info("这个版本没有提供 RAG 组件，无需补齐。");
                dialog::show_info(
                    "无需补齐",
                    "当前版本没有提供 RAG 组件，无需下载。\n需要它时会在检查更新里一起给出。",
                );
            }
            Ok(report) => {
                shell_log::info(&format!(
                    "RAG 组件补齐完成：base={} application={} rag={}",
                    report.base_id, report.application_id, report.rag_id
                ));
                // 后端读的是启动时注入的运行时目录，不重启就接不上刚下好的层——
                // 但重启会打断正在进行的生成，所以只建议、不擅自执行。
                let restart = dialog::ask_confirm(
                    "RAG 组件已就绪",
                    "RAG 组件已下载并自检通过，重启后端后生效。\n\
                     现在重启会打断正在进行的生成。\n\n要现在重启后端吗？",
                );
                if restart {
                    restart_backend(&handle);
                } else {
                    shell_log::info("用户选择暂不重启后端；RAG 组件将在下次启动后端时生效。");
                }
            }
            Err(err) => {
                shell_log::warn(&format!("RAG 组件补齐失败：{err}"));
                dialog::show_error(
                    "RAG 组件补齐失败",
                    &format!(
                        "{err}\n\n已下载的临时内容只清理了本次自己的部分，\n\
                         已有的运行时与作品数据不受影响。可以从托盘菜单「补齐 RAG」再试一次。"
                    ),
                );
            }
        }
    });
}

/// 「问过了」落盘：只在用户明确拒绝、或这个版本压根没有可选组件时写。
/// 接受但下载失败**不写**——下次启动还会再问一次，那是对的（用户没说不想要）。
fn mark_offer_done() {
    let mut settings = ShellSettings::load(None);
    if settings.optional_layer_offer_done {
        return;
    }
    settings.optional_layer_offer_done = true;
    if let Err(err) = settings.save(None) {
        shell_log::warn(&format!("记录 RAG 补齐询问结果失败：{err}"));
    }
}

/// 交付要用的安装目录：只为把壳自带的 MinGit / ffmpeg 前置进 PATH，与首启交付同一份口径。
fn install_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|dir| dir.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."))
}
