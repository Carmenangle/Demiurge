//! 自动更新骨架：Tauri updater 插件 + 托盘入口。
//!
//! 安全边界：
//! - 只认 HTTPS 端点与 minisign 签名；`requireSignedVersion` 拒绝「新版本号配旧包」的降级攻击。
//! - 当前嵌入的是**本地测试公钥**，正式发布前必须换成离线保管的生产公钥；私钥不进仓库。
//! - 更新安装只对 `currentUser` 安装版开放：便携包、仓库开发壳与 `perMachine` 安装
//!   一律拒绝——清单指向的是 currentUser 安装器，跑错变体会装出第二份而不是升级。
//!
//! 自检第 18 项用仓库内的固定夹具验证「公钥 → 签名 → 载荷」与篡改拒绝，
//! 不依赖网络或私钥；生产公钥轮换不影响该测试使用的独立测试密钥。
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use base64::Engine as _;
use minisign_verify::{PublicKey, Signature};
use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;

use crate::dialog;
use crate::runtime_host::RuntimeHost;
use crate::shell_log;

pub const TEST_PUBKEY: &str = include_str!("../testdata/updater-test.pub");
pub const TEST_SIGNATURE: &str = include_str!("../testdata/updater-fixture.bin.sig");
pub const TEST_PAYLOAD: &[u8] = include_bytes!("../testdata/updater-fixture.bin");

pub fn plugin<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R, tauri_plugin_updater::Config> {
    tauri_plugin_updater::Builder::new().build()
}

/// 与 tauri-plugin-updater 相同的校验链：base64 解出 minisign 文本，验签后再读
/// trusted comment（全局签名覆盖它），最后核对签名绑定的版本。
pub fn verify_signed_payload(
    pubkey_b64: &str,
    signature_b64: &str,
    payload: &[u8],
    announced_version: &str,
) -> Result<(), String> {
    let engine = base64::engine::general_purpose::STANDARD;
    let pubkey_text = engine
        .decode(pubkey_b64.trim())
        .map_err(|err| format!("公钥不是合法 base64：{err}"))?;
    let pubkey_text =
        String::from_utf8(pubkey_text).map_err(|_| "公钥不是 UTF-8 文本".to_string())?;
    let public_key =
        PublicKey::decode(&pubkey_text).map_err(|err| format!("公钥解析失败：{err}"))?;

    let signature_text = engine
        .decode(signature_b64.trim())
        .map_err(|err| format!("签名不是合法 base64：{err}"))?;
    let signature_text =
        String::from_utf8(signature_text).map_err(|_| "签名不是 UTF-8 文本".to_string())?;
    let signature =
        Signature::decode(&signature_text).map_err(|err| format!("签名解析失败：{err}"))?;

    public_key
        .verify(payload, &signature, true)
        .map_err(|err| format!("签名校验失败：{err}"))?;

    let signed_version = signature
        .trusted_comment()
        .split('\t')
        .find_map(|field| field.strip_prefix("version:"))
        .ok_or_else(|| "签名未绑定版本（缺 version 字段）".to_string())?;
    if signed_version.trim_start_matches('v') != announced_version.trim_start_matches('v') {
        return Err(format!(
            "签名版本不符：签名绑定 {signed_version}，清单声明 {announced_version}"
        ));
    }
    Ok(())
}

pub fn verify_test_fixture() -> Result<(), String> {
    verify_signed_payload(TEST_PUBKEY, TEST_SIGNATURE, TEST_PAYLOAD, "0.1.0")?;
    let mut tampered = TEST_PAYLOAD.to_vec();
    if let Some(first) = tampered.first_mut() {
        *first ^= 0xFF;
    }
    if verify_signed_payload(TEST_PUBKEY, TEST_SIGNATURE, &tampered, "0.1.0").is_ok() {
        return Err("被篡改的夹具仍然通过了签名校验".to_string());
    }
    if verify_signed_payload(TEST_PUBKEY, TEST_SIGNATURE, TEST_PAYLOAD, "9.9.9").is_ok() {
        return Err("版本不符的夹具仍然通过了签名校验".to_string());
    }
    Ok(())
}

/// 托盘「检查更新…」：先看运行时层的版本漂移，再检查 → 确认 → 下载并验签 → 停自启运行时 → 交给 NSIS 安装器。
/// Windows 上安装成功会由插件直接退出进程，安装器随后重启新版本。
pub fn check_for_updates(
    app: AppHandle,
    host: Arc<Mutex<Option<RuntimeHost>>>,
    updating: Arc<AtomicBool>,
) {
    if updating.swap(true, Ordering::SeqCst) {
        shell_log::info("更新检查已在运行，忽略重复请求");
        return;
    }
    if let Err(reason) = updater_supported() {
        shell_log::info(&format!("自动更新不可用：{reason}"));
        dialog::show_info("检查更新", &reason);
        updating.store(false, Ordering::SeqCst);
        return;
    }
    // 运行时层（base/application/rag）与壳的 pin 不一致时，这一步才是大头：2.2 GB 的交付
    // 不能在事件循环线程上跑，所以问过用户之后交给独立线程；这次点击到此结束，不叠 shell 更新。
    if let Some(exe_dir) = ask_runtime_drift() {
        std::thread::spawn(move || deliver_runtime(exe_dir, updating));
        return;
    }
    tauri::async_runtime::spawn(async move {
        if let Err(err) = run_update(app.clone(), host).await {
            shell_log::warn(&format!("自动更新失败：{err}"));
            dialog::show_error("更新失败", &err);
        }
        updating.store(false, Ordering::SeqCst);
    });
}

/// 已装运行时的 `app_version` 与本壳内置 pin 不一致 ⇒ 问一句要不要现在交付。
///
/// 为什么不自动下：一是「不要浪费我的流量」是用户定的默认，二是启动线程上同步跑 2.2 GB
/// 会把首启阻塞翻倍（非阻塞首启是 P3 的范围）。判定本身是纯本地读一个 json，零流量。
/// 返回 `Some(exe_dir)` 表示「有漂移且用户同意交付」。
fn ask_runtime_drift() -> Option<std::path::PathBuf> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.to_path_buf()))?;
    let pin = match crate::runtime_delivery::embedded_pin() {
        Ok(pin) => pin,
        Err(err) => {
            shell_log::warn(&format!("读取内置交付 pin 失败：{err}"));
            return None;
        }
    };
    let (installed, wanted) = crate::runtime_delivery::runtime_drift(&crate::paths::home(), &pin)?;
    shell_log::info(&format!("运行时版本漂移：已装 {installed}，本壳期望 {wanted}"));
    if !dialog::ask_confirm(
        "运行时更新",
        &format!(
            "已安装的运行时是 {installed}，本程序期望 {wanted}。\n\n\
             是：现在下载并自检新运行时（约 2.2 GB；已下载过的分件会复用，不重复下载）。\n\
             自检不过会自动保持原版本，不会把现在能用的那套换掉。\n\
             否：这次不动，继续检查程序本体的更新。\n\n\
             现在跑着的版本在下次启动前保持可用。"
        ),
    ) {
        shell_log::info("用户选择暂不更新运行时");
        return None;
    }
    Some(exe_dir)
}

/// 后台交付运行时：过提交闸才翻 `current.json`，所以失败也不会弄坏现在能用的那一套。
fn deliver_runtime(exe_dir: std::path::PathBuf, updating: Arc<AtomicBool>) {
    let mut progress = |message: &str| shell_log::info(&format!("运行时交付：{message}"));
    let outcome = crate::runtime_delivery::ensure_installed_runtime(&exe_dir, &mut progress);
    let (caption, text) = match outcome {
        Ok(report) => {
            // rag 是可选层（P3）：没落地时这里必须是空的，不能念出一个不存在的 id。
            let rag = if report.rag_id.is_empty() {
                "未安装"
            } else {
                report.rag_id.as_str()
            };
            shell_log::info(&format!(
                "运行时交付完成：base={} application={} rag={}",
                report.base_id, report.application_id, rag
            ));
            (
                "运行时已就绪",
                format!(
                    "新运行时已通过启动自检并提交（base={}、app={}、rag={}）。\n\
                     下次启动 Demiurge 时生效；现在跑着的版本继续可用。\n\
                     旧版本保留着，新版真起不来会自动退回。",
                    report.base_id, report.application_id, rag
                ),
            )
        }
        Err(err) => {
            shell_log::warn(&format!("运行时交付失败：{err}"));
            (
                "运行时未更新",
                format!(
                    "{err}\n\n日志：{}",
                    crate::shell_log::dir().display()
                ),
            )
        }
    };
    dialog::show_info(caption, &text);
    updating.store(false, Ordering::SeqCst);
}

/// 自动更新只对 currentUser 安装版开放。
fn updater_supported() -> Result<(), String> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.to_path_buf()))
        .ok_or_else(|| "无法定位当前程序目录。".to_string())?;
    if crate::runtime_delivery::is_portable_layout(&exe_dir)
        || crate::paths::find_repo(&exe_dir).is_some()
    {
        return Err(
            "当前是便携包或开发模式，应用内更新只支持安装版。\n请从发布页手动下载新版本。"
                .to_string(),
        );
    }
    if !crate::paths::looks_installed(&exe_dir) {
        return Err("当前不是安装版（找不到安装标记），应用内更新不可用。".to_string());
    }
    if !install_dir_writable() {
        return Err(
            "当前是 perMachine 安装（安装目录需要管理员权限），应用内更新只支持 currentUser 安装。\n\
             请从发布页手动下载新版本安装包覆盖安装。"
                .to_string(),
        );
    }
    Ok(())
}

/// 当前用户能否直接替换 exe：不能就说明装在了 `perMachine` 目录。
fn install_dir_writable() -> bool {
    let Some(dir) = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.to_path_buf()))
    else {
        return false;
    };
    let probe = dir.join(".update-write-probe");
    let writable = std::fs::write(&probe, b"x").is_ok();
    let _ = std::fs::remove_file(&probe);
    writable
}

async fn run_update(
    app: AppHandle,
    host: Arc<Mutex<Option<RuntimeHost>>>,
) -> Result<(), String> {
    let updater = app
        .updater()
        .map_err(|err| format!("更新器不可用：{err}"))?;
    let current = app.package_info().version.to_string();
    shell_log::info(&format!("检查更新：当前版本 {current}"));

    let update = updater
        .check()
        .await
        .map_err(|err| format!("检查更新失败：{err}"))?;
    let Some(update) = update else {
        shell_log::info("检查更新：已是最新版本");
        dialog::show_info(
            "检查更新",
            &format!("当前已是最新版本（{current}）。"),
        );
        return Ok(());
    };

    let mut detail = format!(
        "发现新版本 {}（当前 {current}）。\n\n是否现在下载并安装？",
        update.version
    );
    if let Some(body) = update.body.as_deref().map(str::trim).filter(|text| !text.is_empty()) {
        let notes: String = body.chars().take(500).collect();
        detail.push_str("\n\n更新说明：\n");
        detail.push_str(&notes);
    }
    if !dialog::ask_confirm("发现新版本", &detail) {
        shell_log::info("用户取消更新");
        return Ok(());
    }

    shell_log::info(&format!(
        "开始下载更新 {}（{}）",
        update.version,
        update.download_url
    ));
    let mut downloaded: u64 = 0;
    let mut reported = 0u64;
    let bytes = update
        .download(
            |chunk, total| {
                downloaded += chunk as u64;
                if let Some(total) = total.filter(|value| *value > 0) {
                    let percent = downloaded.saturating_mul(100) / total;
                    if percent >= reported + 10 {
                        reported = percent - percent % 10;
                        shell_log::info(&format!("更新下载进度：{reported}%"));
                    }
                }
            },
            || shell_log::info("更新包下载完成，正在校验签名"),
        )
        .await
        .map_err(|err| format!("下载或校验更新失败：{err}"))?;

    // 安装器会替换当前 exe；先收掉自启运行时，安装器重启新壳后会重新拉起。
    if let Some(mut runtime) = host.lock().ok().and_then(|mut slot| slot.take()) {
        shell_log::info("安装更新前停止自启运行时");
        runtime.stop();
    }

    shell_log::info("更新包校验通过，启动安装器");
    if let Err(err) = update.install(&bytes) {
        shell_log::warn(&format!("启动更新安装器失败：{err}"));
        #[cfg(not(windows))]
        app.restart();
        return Err(format!(
            "启动安装器失败：{err}\n运行时已停止，请重新启动 Demiurge。"
        ));
    }

    // Windows 上 install 成功不会返回（插件直接退出进程），其余平台在此重启。
    #[cfg(not(windows))]
    app.restart();
    Ok(())
}
