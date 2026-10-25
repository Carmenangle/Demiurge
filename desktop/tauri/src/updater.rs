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

/// 托盘「检查更新…」：检查 → 确认 → 下载并验签 → 停自启运行时 → 交给 NSIS 安装器。
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
    tauri::async_runtime::spawn(async move {
        if let Err(err) = run_update(app.clone(), host).await {
            shell_log::warn(&format!("自动更新失败：{err}"));
            dialog::show_error("更新失败", &err);
        }
        updating.store(false, Ordering::SeqCst);
    });
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
