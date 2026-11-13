//! 运行时生命周期：解析端口 → 探活 → 复用已在跑的服务 → 拉起 → 等就绪 → 回收。
//! 语义逐条对齐 WPF 的 `RuntimeHost.cs`（那份是合同，改动要先改它再改这里）。
//! ⚠ 例外：提交闸（`probe_candidate`）、启动失败回退、安全模式与旧层回收是 Tauri 侧新增形态
//! （WPF 壳没有安装版运行时交付），对齐范围只到「启动 / 复用 / 回收」这几条合同为止。
//! 端口占用时的「确认后结束占用进程并重启」（`clear_blocking_holder`）同样是 Tauri 侧新增形态：
//! WPF 壳仍只会报错退出；安装版没有 `stop-dev.bat`，这条是唯一的自救出口。
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use crate::paths::{self, PackageLayout, RepoLayout};
use crate::runtime_delivery::{self, GateRequest, LayerIds};
use crate::settings::ShellSettings;

pub const DEFAULT_PORT: u16 = 8010;
const MAX_LOG_TAIL_LINES: usize = 25;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeMode {
    Packaged,
    Development,
}

#[derive(Debug)]
pub struct RuntimeStartupError(pub String);

impl std::fmt::Display for RuntimeStartupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RuntimeStartupError {}

pub type StartupResult<T> = Result<T, RuntimeStartupError>;

/// 端口占用者：只用于「告诉用户是谁」和「用户确认后结束它」。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PortHolder {
    pub pid: u32,
    /// 完整映像路径；受保护进程读不到时为空串（PID 仍然有效）。
    pub image_path: String,
}

impl PortHolder {
    pub fn image_name(&self) -> String {
        self.image_path
            .rsplit(['\\', '/'])
            .find(|part| !part.is_empty())
            .unwrap_or("未知进程")
            .to_string()
    }

    /// 只决定弹窗里要不要多一句警告，不决定能不能杀——杀不杀由用户在弹窗里点头。
    pub fn looks_like_demiurge(&self) -> bool {
        let name = self.image_name().to_ascii_lowercase();
        if name == "demiurge.exe" || name == "demiurge-runtime.exe" {
            return true;
        }
        if name == "python.exe" || name == "pythonw.exe" {
            let path = self.image_path.to_ascii_lowercase();
            return path.contains("\\backend\\.venv\\") || path.contains("/backend/.venv/");
        }
        false
    }
}

/// 启动被占用挡住时的上下文，交给壳去弹「是否结束并重启」。
#[derive(Debug, Clone)]
pub struct PortConflict {
    pub port: u16,
    /// 端口上是一个健康后端，但拿不到前端页面（多半是手动起的开发后端）。
    pub demiurge_without_frontend: bool,
    pub holder: Option<PortHolder>,
}

impl PortConflict {
    /// 弹窗正文；连 PID 都拿不到时返回 None —— 没有可结束的目标，只能报错。
    pub fn cleanup_prompt(&self) -> Option<String> {
        let holder = self.holder.as_ref()?;
        let reason = if self.demiurge_without_frontend {
            "端口上已有一个 Demiurge 后端，但它没有提供前端页面（多半是手动启动的开发后端）。"
        } else {
            "该端口被其他程序占用，Demiurge 无法启动。"
        };
        let path = if holder.image_path.is_empty() {
            "（读取不到，可能是受保护进程）".to_string()
        } else {
            holder.image_path.clone()
        };
        let caution = if holder.looks_like_demiurge() {
            ""
        } else {
            "\n\n⚠ 它看起来不是 Demiurge 的进程，结束它可能影响别的程序。"
        };
        Some(format!(
            "端口 {port} 被占用：{reason}\n\n\
             占用进程：{name}（PID {pid}）\n\
             路径：{path}{caution}\n\n\
             是：结束该进程并继续启动 Demiurge\n\
             否：不结束，停在失败页面",
            port = self.port,
            name = holder.image_name(),
            pid = holder.pid,
        ))
    }
}

pub fn parse_port(raw: &str, source: &str) -> StartupResult<u16> {
    match raw.trim().parse::<i64>() {
        Ok(port) if (1..=65535).contains(&port) => Ok(port as u16),
        _ => Err(RuntimeStartupError(format!(
            "{source} 不是合法端口：{raw}（应为 1-65535 的整数）"
        ))),
    }
}

/// 端口优先级：`DEMIURGE_PORT` > `LAF_RUNTIME_PORT` > 设置里的值 > 8010。
pub fn resolve_port(settings: &ShellSettings) -> StartupResult<u16> {
    for name in ["DEMIURGE_PORT", "LAF_RUNTIME_PORT"] {
        if let Ok(raw) = std::env::var(name) {
            if !raw.trim().is_empty() {
                return parse_port(&raw, name);
            }
        }
    }
    if (1..=65535).contains(&settings.port) {
        return Ok(settings.port as u16);
    }
    Ok(DEFAULT_PORT)
}

pub fn is_healthy_payload(body: &str) -> bool {
    if body.trim().is_empty() {
        return false;
    }
    match serde_json::from_str::<serde_json::Value>(body) {
        Ok(value) => value
            .get("status")
            .and_then(|status| status.as_str())
            .map(|status| status.eq_ignore_ascii_case("ok"))
            .unwrap_or(false),
        Err(_) => false,
    }
}

pub fn is_frontend_document(html: &str) -> bool {
    !html.trim().is_empty() && html.to_lowercase().contains("id=\"root\"")
}

pub struct RuntimeHost {
    port: u16,
    child: Option<Child>,
    job: Option<crate::job::ChildProcessJob>,
    pid_path: Option<PathBuf>,
    mode: Option<RuntimeMode>,
    reused_external: bool,
    early_exit: Option<i32>,
    /// 运行时层目录的根（仅 packaged 模式有值）：回退、回收与安全模式都以它为落点。
    runtime_root: Option<PathBuf>,
}

impl RuntimeHost {
    pub fn new(port: u16) -> Self {
        Self {
            port,
            child: None,
            job: None,
            pid_path: None,
            mode: None,
            reused_external: false,
            early_exit: None,
            runtime_root: None,
        }
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn base_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    pub fn mode(&self) -> Option<RuntimeMode> {
        self.mode
    }

    pub fn reused_external(&self) -> bool {
        self.reused_external
    }

    pub fn owns_process(&self) -> bool {
        self.child.is_some()
    }

    /// `confirm_cleanup` 只在端口被占时调用一次：返回 true 表示用户同意结束占用进程。
    pub fn ensure_ready(
        &mut self,
        mut progress: impl FnMut(&str),
        mut confirm_cleanup: impl FnMut(&PortConflict) -> bool,
    ) -> StartupResult<()> {
        progress(&format!("检查 {} …", self.base_url()));
        if self.is_healthy() {
            if self.serves_frontend() {
                self.reused_external = true;
                self.mode = None;
                progress(&format!("复用已在运行的后端：{}", self.base_url()));
                crate::shell_log::info(&format!("复用已运行的 Demiurge：{}", self.base_url()));
                return Ok(());
            }
            // 健康但没前端：复用只会得到空白页，所以按「占用」处理。
            self.clear_blocking_holder(
                &mut progress,
                &mut confirm_cleanup,
                PortConflict {
                    port: self.port,
                    demiurge_without_frontend: true,
                    holder: port_holder(self.port),
                },
            )?;
        } else if self.is_port_open() {
            self.clear_blocking_holder(
                &mut progress,
                &mut confirm_cleanup,
                PortConflict {
                    port: self.port,
                    demiurge_without_frontend: false,
                    holder: port_holder(self.port),
                },
            )?;
        }

        let launch = match self.build_launch() {
            Ok(launch) => launch,
            Err(original) => {
                // 安装版刚装好时 home/runtime 还是空的：先按 pin 交付运行时，再重新解析布局。
                if self.try_install_runtime(&mut progress)? {
                    self.build_launch()?
                } else {
                    return Err(original);
                }
            }
        };
        self.mode = Some(launch.mode);
        self.runtime_root = launch.runtime_root.clone();
        self.early_exit = None;
        self.spawn(launch)?;
        progress(match self.mode {
            Some(RuntimeMode::Packaged) => "启动内置运行时…",
            _ => "启动后端（Python / uvicorn）…",
        });
        match self.start_and_wait(&mut progress) {
            Ok(()) => {
                // 「下次成功后再删」：只有这一代真的起得来，才回收更老的孤儿代际。
                self.prune_superseded_layers();
                Ok(())
            }
            Err(first) => {
                if self.mode != Some(RuntimeMode::Packaged) {
                    return Err(first);
                }
                match self.rollback_to_previous(&mut progress) {
                    Ok((broken, used)) => {
                        progress(&format!(
                            "已回退到上一版运行时（base {} → {}）",
                            broken.base_id, used.base_id
                        ));
                        Ok(())
                    }
                    Err(reason) => {
                        let detail = format!("{}\n\n回退上一版：{reason}", first.0);
                        self.offer_safe_mode(&detail);
                        Err(RuntimeStartupError(detail))
                    }
                }
            }
        }
    }

    /// 端口被占：问用户 → 结束占用进程 → 等端口释放。拒绝或杀不掉都返回可读错误。
    /// 这是安装版没有 `stop-dev.bat` 时的唯一自救出口，所以必须把「杀的是谁」摆到用户面前。
    fn clear_blocking_holder(
        &mut self,
        progress: &mut impl FnMut(&str),
        confirm_cleanup: &mut impl FnMut(&PortConflict) -> bool,
        conflict: PortConflict,
    ) -> StartupResult<()> {
        let Some(holder) = conflict.holder.clone() else {
            return Err(RuntimeStartupError(self.port_busy_message(&conflict)));
        };
        if !confirm_cleanup(&conflict) {
            crate::shell_log::info(&format!(
                "端口被占用：用户选择不结束 pid={} path={}",
                holder.pid, holder.image_path
            ));
            return Err(RuntimeStartupError(self.port_busy_message(&conflict)));
        }
        crate::shell_log::info(&format!(
            "用户确认清理端口占用：pid={} path={}",
            holder.pid, holder.image_path
        ));
        progress(&format!(
            "正在结束占用端口的进程：{}（PID {}）…",
            holder.image_name(),
            holder.pid
        ));
        if !taskkill(holder.pid, true) {
            return Err(RuntimeStartupError(format!(
                "无法结束占用端口 {} 的进程 {}（PID {}），可能权限不足或它已经退出。\n\n\
                 请用管理员身份重新启动 Demiurge，或手动结束该进程后重试。",
                self.port,
                holder.image_name(),
                holder.pid
            )));
        }
        if !wait_port_closed(self.port, Duration::from_secs(10)) {
            return Err(RuntimeStartupError(format!(
                "已请求结束 {}（PID {}），但端口 {} 仍被占用（可能有子进程还在监听）。\n\n\
                 请手动检查后重试。",
                holder.image_name(),
                holder.pid,
                self.port
            )));
        }
        progress("占用进程已结束，继续启动 Demiurge …");
        Ok(())
    }

    fn port_busy_message(&self, conflict: &PortConflict) -> String {
        let mut text = if conflict.demiurge_without_frontend {
            format!(
                "端口 {} 上已有 Demiurge 后端在运行，但它没有提供前端页面（通常是在开发模式下直接启动的后端）。\n\n",
                self.port
            )
        } else {
            format!("端口 {} 已被其他程序占用，无法启动 Demiurge。\n\n", self.port)
        };
        if let Some(holder) = conflict.holder.as_ref() {
            let path = if holder.image_path.is_empty() {
                "（读取不到，可能是受保护进程）".to_string()
            } else {
                holder.image_path.clone()
            };
            text.push_str(&format!(
                "占用进程：{}（PID {}）\n路径：{}\n\n",
                holder.image_name(),
                holder.pid,
                path
            ));
        }
        if conflict.demiurge_without_frontend {
            text.push_str(
                "请关闭该后端后重试，或为它设置 LAF_FRONTEND_DIST 指向已构建的 frontend/dist。\n\
                 桌面壳不会擅自改用其他端口。",
            );
        } else {
            text.push_str("请关闭占用该端口的程序后重试。");
        }
        text
    }

    /// 起进程并等就绪：沿用「崩一次重试一次、超时不重试」的既有口径。
    fn start_and_wait(&mut self, progress: &mut impl FnMut(&str)) -> StartupResult<()> {
        match self.wait_until_ready(progress) {
            Ok(()) => Ok(()),
            Err(first) if self.early_exit.is_some() => {
                // Python 在 Windows 上偶发启动瞬间崩溃（0xC0000005），重试一次通常就好；
                // 超时不重试：那多半是配置/依赖问题，重试只会把等待时间翻倍。
                crate::shell_log::warn(&format!("运行时首次启动异常退出，自动重试一次：{first}"));
                progress("后端异常退出，正在重试…");
                self.stop();
                wait_port_closed(self.port, Duration::from_secs(5));
                let launch = self.build_launch()?;
                self.mode = Some(launch.mode);
                self.runtime_root = launch.runtime_root.clone();
                self.early_exit = None;
                self.spawn(launch)?;
                progress("重新启动后端（Python / uvicorn）…");
                self.wait_until_ready(progress)
            }
            Err(err) => Err(err),
        }
    }

    /// 提交后的 commit 起不来 ⇒ 把 `current.json` 改指前一代，重新解析布局再起一次。
    /// 返回 `Err(原因)` 表示没回退成（没有前一代 / 前一代文件不全 / 回退后仍然起不来）。
    fn rollback_to_previous(
        &mut self,
        progress: &mut impl FnMut(&str),
    ) -> Result<(LayerIds, LayerIds), String> {
        let root = self
            .runtime_root
            .clone()
            .ok_or_else(|| "未取得运行时目录，无法回退".to_string())?;
        // 先只判据不动盘：没有可用回退点时直接给出原因，不去停本来就没起来的进程。
        runtime_delivery::rollback_target(&root)?;
        self.stop();
        wait_port_closed(self.port, Duration::from_secs(8));
        let (broken, used) = runtime_delivery::commit_previous(&root)?;
        progress(&format!(
            "新版本启动失败，正在回退到上一版（base {}）…",
            used.base_id
        ));
        let launch = self
            .build_launch()
            .map_err(|err| format!("回退后重新解析启动参数失败：{err}"))?;
        self.mode = Some(launch.mode);
        self.runtime_root = launch.runtime_root.clone();
        self.early_exit = None;
        self.spawn(launch)
            .map_err(|err| format!("回退后启动失败：{err}"))?;
        self.wait_until_ready(progress)
            .map_err(|err| format!("回退到的上一版也起不来：{err}"))?;
        crate::shell_log::info(&format!(
            "回退成功：上一版运行时已就绪（坏的那份记在 previous.json：base {}）",
            broken.base_id
        ));
        Ok((broken, used))
    }

    /// 连上一版都起不来 ⇒ 给出「只清运行时、保留作品数据」的出口，避免卡在失败弹窗的死循环。
    /// 只对安装版开放：便携包的 `data/runtime` 是随包交付的，删了就再也装不回来。
    fn offer_safe_mode(&self, detail: &str) {
        let Some(root) = self.runtime_root.as_ref() else {
            return;
        };
        if *root != crate::paths::installed_runtime_root() {
            crate::shell_log::info("运行时目录不是安装版布局，不提供「只清运行时」");
            return;
        }
        let clipped: String = detail.chars().take(1200).collect();
        let text = format!(
            "Demiurge 的运行时已经坏到「当前版本和上一版都起不来」。\n\n\
             是：只清除运行时（作品、预设、角色卡、世界书、日志、已下载的分件都保留），\n\
             然后重新启动 Demiurge，它会用本地缓存重新装配运行时（不重复下载）。\n\
             否：什么都不动，留在失败页面自己处理。\n\n\
             日志目录：{}\n\n错误详情：\n{}",
            crate::shell_log::dir().display(),
            clipped
        );
        if !crate::dialog::ask_confirm("运行时无法启动", &text) {
            crate::shell_log::info("安全模式：用户选择不清除运行时");
            return;
        }
        let failures = runtime_delivery::wipe_runtime_layers(root);
        let tail = if failures.is_empty() {
            String::new()
        } else {
            format!("\n\n以下项目删除失败，需要手动处理：\n{}", failures.join("\n"))
        };
        crate::dialog::show_info(
            "已清除运行时",
            &format!(
                "运行时目录已清除。请重新启动 Demiurge，它会自动重新装配运行时。\n\
                 作品数据与下载缓存都在，不会丢。{tail}"
            ),
        );
    }

    /// 正常启动成功之后回收旧代际（两档引用的目录绝不进名单，另加 24 小时年龄护栏）。
    fn prune_superseded_layers(&self) {
        let Some(root) = self.runtime_root.as_ref() else {
            return;
        };
        if std::env::var("DEMIURGE_PRUNE_PREVIOUS")
            .map(|value| value.trim() == "0")
            .unwrap_or(false)
        {
            crate::shell_log::info("旧层回收已禁用（DEMIURGE_PRUNE_PREVIOUS=0）");
            return;
        }
        let (removed, failures) =
            runtime_delivery::prune_old_layers(root, Duration::from_secs(24 * 3600));
        if !removed.is_empty() {
            crate::shell_log::info(&format!(
                "回收不再引用的旧运行时层：{}",
                removed.join("、")
            ));
        }
        for failure in &failures {
            crate::shell_log::warn(&format!("旧层回收失败：{failure}"));
        }
    }

    pub fn stop(&mut self) {
        let Some(mut child) = self.child.take() else {
            self.job = None;
            return;
        };
        self.reused_external = false;
        self.mode = None;
        let pid = child.id();
        crate::shell_log::info(&format!("停止运行时 pid={pid}"));
        if !taskkill(pid, false) {
            crate::shell_log::warn(&format!("非强制 taskkill 未成功（pid={pid}）"));
        }
        if !wait_exit(&mut child, Duration::from_secs(3)) {
            if !taskkill(pid, true) {
                crate::shell_log::warn(&format!("强制 taskkill 未成功（pid={pid}）"));
            }
            if !wait_exit(&mut child, Duration::from_secs(5)) {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        // 先杀进程再关 Job 句柄：否则正常退出路径会被 Job 抢走。
        self.job = None;
        self.delete_pid_file();
    }

    // ── 探活 ────────────────────────────────────────────────────────────────
    fn is_healthy(&self) -> bool {
        match crate::http_probe::get(self.port, "/api/health", Duration::from_millis(1500)) {
            Some(response) if (200..300).contains(&response.status) => is_healthy_payload(&response.body),
            _ => false,
        }
    }

    fn serves_frontend(&self) -> bool {
        match crate::http_probe::get(self.port, "/", Duration::from_millis(1500)) {
            Some(response) if (200..300).contains(&response.status) => is_frontend_document(&response.body),
            _ => false,
        }
    }

    fn is_port_open(&self) -> bool {
        port_open(self.port)
    }

    // ── 拉起 ────────────────────────────────────────────────────────────────
    fn build_launch(&self) -> StartupResult<LaunchSpec> {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(|dir| dir.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));

        if let Some(package) = paths::resolve_package(&exe_dir).or_else(|| paths::resolve_package(&cwd)) {
            sweep_orphans(&package.runtime_root);
            return self.packaged_launch(package);
        }
        // 仓库优先于安装版：开发时把便携壳放进仓库根仍应使用 `.venv`，而不是误用本机装的那份运行时。
        let repo = paths::find_repo(&exe_dir).or_else(|| paths::find_repo(&cwd));
        if let Some(repo) = repo {
            return self.development_launch(repo);
        }
        if let Some(installed) = paths::resolve_installed(&exe_dir) {
            sweep_orphans(&installed.runtime_root);
            return self.packaged_launch(installed);
        }
        Err(RuntimeStartupError(format!(
            "未找到 Demiurge 运行时。\n\n\
             请把桌面壳放在便携包根目录（含 data/runtime/current.json）、在仓库内运行，\
             或先完成安装版的运行时部署（{}）。",
            paths::installed_runtime_root().join("current.json").display()
        )))
    }

    /// 安装版首次启动：自动部署必装层（当前是 base + application；`rag` 是可后补的可选层）。
    /// 便携包 / 仓库布局不适用，返回 false 让原始错误照常抛出。
    fn try_install_runtime(&self, progress: &mut dyn FnMut(&str)) -> StartupResult<bool> {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(|dir| dir.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        if paths::resolve_package(&exe_dir).is_some()
            || paths::resolve_package(&cwd).is_some()
            || paths::find_repo(&exe_dir).is_some()
            || paths::find_repo(&cwd).is_some()
            || !paths::looks_installed(&exe_dir)
        {
            return Ok(false);
        }
        progress("首次启动：准备下载运行时…");
        crate::shell_log::info("安装版缺少运行时，开始按交付 pin 部署");
        let report = crate::runtime_delivery::ensure_installed_runtime(&exe_dir, progress)
            .map_err(|err| {
                RuntimeStartupError(format!(
                    "运行时尚未部署，自动下载失败：{err}\n\n\
                     也可以手动把运行时发布资产（更新清单 + 分件）放到以下任一目录后重试：\n\
                     - {}\n- {}",
                    exe_dir.join("runtime-pack").display(),
                    paths::home().join("downloads").display()
                ))
            })?;
        crate::shell_log::info(&format!(
            "运行时部署完成：base={} application={} rag={}",
            report.base_id, report.application_id, report.rag_id
        ));
        Ok(true)
    }

    fn packaged_launch(&self, package: PackageLayout) -> StartupResult<LaunchSpec> {
        if !package.runtime_exe.is_file() {
            return Err(RuntimeStartupError(format!(
                "运行时文件缺失：{}",
                package.runtime_exe.display()
            )));
        }
        let mut command = Command::new(&package.runtime_exe);
        command
            .current_dir(&package.runtime_root)
            .env("LAF_RUNTIME_ROOT", &package.runtime_root)
            .env("LAF_RUNTIME_STATE", &package.state_path)
            .env("LAF_DATA_DIR", &package.data_dir)
            .env("LAF_NO_BROWSER", "1")
            .env("LAF_RUNTIME_PORT", self.port.to_string());
        inject_dependency_path(&mut command, &package);
        Ok(LaunchSpec {
            mode: RuntimeMode::Packaged,
            command,
            pid_path: Some(package.pid_path),
            header: package.runtime_exe.display().to_string(),
            runtime_root: Some(package.runtime_root),
        })
    }

    fn development_launch(&self, repo: RepoLayout) -> StartupResult<LaunchSpec> {
        let python = repo.python_exe();
        if !python.is_file() {
            return Err(RuntimeStartupError(format!(
                "未找到 Python 环境：{}\n\n请先在 backend 下创建 .venv。",
                python.display()
            )));
        }
        let frontend_dist = match std::env::var("DEMIURGE_FRONTEND_DIST") {
            Ok(raw) if !raw.trim().is_empty() => PathBuf::from(raw.trim()),
            _ => repo.frontend_dist(),
        };
        if !frontend_dist.join("index.html").is_file() {
            return Err(RuntimeStartupError(format!(
                "前端尚未构建：{}\n\n请先在 frontend 下运行 npm run build，或使用打包后的便携版。",
                frontend_dist.display()
            )));
        }
        let mut command = Command::new(&python);
        command
            .current_dir(repo.backend_dir())
            .args([
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                &self.port.to_string(),
            ])
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            // 段错误时把 Python 栈打到 stderr；崩溃后日志里才不是一片空白。
            .env("PYTHONFAULTHANDLER", "1")
            .env("PYTHONUNBUFFERED", "1")
            .env("LAF_NO_BROWSER", "1")
            .env("LAF_FRONTEND_DIST", &frontend_dist)
            // 壳的端口是唯一真源：清掉继承来的层目录，避免误用旧便携包。
            .env_remove("LAF_RUNTIME_ROOT")
            .env_remove("LAF_RUNTIME_STATE")
            .env_remove("LAF_RUNTIME_EDITION");
        Ok(LaunchSpec {
            mode: RuntimeMode::Development,
            command,
            pid_path: None,
            header: python.display().to_string(),
            runtime_root: None,
        })
    }

    fn spawn(&mut self, launch: LaunchSpec) -> StartupResult<()> {
        let _ = std::fs::create_dir_all(paths::logs_dir());
        crate::shell_log::append_runtime_line(&format!(
            "\n===== {} 启动 [{:?}] {} =====",
            crate::shell_log::now_stamp(),
            launch.mode,
            launch.header
        ));

        let mut command = launch.command;
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = command
            .spawn()
            .map_err(|err| RuntimeStartupError(format!("无法启动 Demiurge 运行时进程：{err}")))?;

        if let Some(stdout) = child.stdout.take() {
            std::thread::spawn(move || {
                for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                    crate::shell_log::append_runtime_line(&line);
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            std::thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    crate::shell_log::append_runtime_line(&format!("[stderr] {line}"));
                }
            });
        }

        let pid = child.id();
        self.pid_path = launch.pid_path;
        self.write_pid_file(pid);
        match crate::job::ChildProcessJob::create() {
            Some(job) if job.assign_pid(pid) => self.job = Some(job),
            _ => {
                crate::shell_log::warn("无法把运行时绑定到 Job 对象，退出时改用 taskkill 回收。");
                self.job = None;
            }
        }
        self.child = Some(child);
        crate::shell_log::info(&format!(
            "已启动运行时 pid={pid} mode={:?} port={}",
            launch.mode, self.port
        ));
        Ok(())
    }

    fn wait_until_ready(&mut self, progress: &mut impl FnMut(&str)) -> StartupResult<()> {
        self.wait_until_ready_within(ready_timeout(), progress)
    }

    /// 等就绪（可指定预算）：提交闸要用比正常启动更宽的超时，见 `gate_timeout`。
    fn wait_until_ready_within(
        &mut self,
        timeout: Duration,
        progress: &mut impl FnMut(&str),
    ) -> StartupResult<()> {
        let started = Instant::now();
        let deadline = started + timeout;
        let mut next_notice = started;
        while Instant::now() < deadline {
            if let Some(child) = self.child.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    let code = status.code().unwrap_or(-1);
                    self.early_exit = Some(code);
                    return Err(RuntimeStartupError(format!(
                        "运行时启动失败，退出码 {code}（0x{:08X}）。{}",
                        code as u32,
                        self.log_hint()
                    )));
                }
            }
            if self.is_healthy() {
                progress("后端健康检查通过");
                crate::shell_log::info(&format!("运行时已就绪：{}", self.base_url()));
                return Ok(());
            }
            if Instant::now() >= next_notice {
                next_notice = Instant::now() + Duration::from_secs(3);
                let waited = started.elapsed().as_secs();
                progress(&format!("等待后端健康检查…（已等待 {waited}s）"));
            }
            std::thread::sleep(Duration::from_millis(300));
        }
        Err(RuntimeStartupError(format!(
            "服务在 {} 秒内未就绪。{}",
            timeout.as_secs(),
            self.log_hint()
        )))
    }

    fn log_hint(&self) -> String {
        let path = crate::shell_log::runtime_log_file();
        let tail = crate::shell_log::tail(&path, MAX_LOG_TAIL_LINES);
        if tail.trim().is_empty() {
            format!("\n\n日志：{}", path.display())
        } else {
            format!("\n\n最近日志（{}）：\n{tail}", path.display())
        }
    }

    fn write_pid_file(&self, pid: u32) {
        let Some(path) = self.pid_path.as_ref() else {
            return;
        };
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Err(err) = std::fs::write(path, pid.to_string()) {
            crate::shell_log::warn(&format!("写入 runtime.pid 失败：{} ({err})", path.display()));
        }
    }

    fn delete_pid_file(&mut self) {
        let Some(path) = self.pid_path.take() else {
            return;
        };
        if path.exists() {
            if let Err(err) = std::fs::remove_file(&path) {
                crate::shell_log::warn(&format!("删除 runtime.pid 失败：{} ({err})", path.display()));
            }
        }
    }
}

impl Drop for RuntimeHost {
    fn drop(&mut self) {
        self.stop();
    }
}

struct LaunchSpec {
    mode: RuntimeMode,
    command: Command,
    pid_path: Option<PathBuf>,
    header: String,
    /// packaged 模式下的运行时层根目录；开发模式没有（`None` ⇒ 回退/回收/安全模式全部不启用）。
    runtime_root: Option<PathBuf>,
}

/// 把壳自带依赖（MinGit / 可选 ffmpeg）前置到运行时 PATH。
/// 干净机器上没有系统 git，节点管理这类能力会直接失效；前置也能让自带版本优先于系统版本。
fn inject_dependency_path(command: &mut Command, package: &PackageLayout) {
    let dirs = paths::dependency_bin_dirs(package);
    if dirs.is_empty() {
        return;
    }
    let mut merged = dirs.clone();
    if let Some(existing) = std::env::var_os("PATH") {
        merged.extend(std::env::split_paths(&existing));
    }
    match std::env::join_paths(&merged) {
        Ok(value) => {
            command.env("PATH", value);
            let shown = dirs
                .iter()
                .map(|dir| dir.display().to_string())
                .collect::<Vec<_>>()
                .join(";");
            crate::shell_log::info(&format!("运行时 PATH 前置：{shown}"));
        }
        Err(err) => crate::shell_log::warn(&format!("拼接运行时 PATH 失败：{err}")),
    }
}

/// 装配前扫一次无主残留：交付中途崩过时 `current.json` 仍是旧值，下次启动就不再走 deliver()，
/// 几 GB 的 `.staging-*` 会永久留在盘上（2026-11-02 读码确认的漏点）。
/// 30 分钟年龄护栏防误杀正在写的临时目录（并行的 `--install-runtime`，或慢盘上的长装配）。
/// 只在启动解析布局时调用；提交闸的探测**不能**扫——它跑在 deliver 内部，`staging` 正被使用。
fn sweep_orphans(runtime_root: &std::path::Path) {
    let downloads = runtime_delivery::downloads_dir(&paths::home());
    for failure in runtime_delivery::sweep_temp(
        runtime_root,
        &downloads,
        Some(Duration::from_secs(1800)),
    ) {
        crate::shell_log::warn(&format!("清理残留临时目录失败：{failure}"));
    }
}

// ── 提交闸（P2）─────────────────────────────────────────────────────────────
// 合同（探什么 / 超时多久 / 试几次），交付侧与手册都按这段写：
// - 探什么：`GET /api/health` 返回 `status=="ok"`，**且** `GET /` 带 `id="root"`。
//   判据复用壳判定「就绪」用的同一对函数（`is_healthy_payload` / `is_frontend_document`），
//   不另发明一套「健康」定义。
// - 拿什么跑：`GateRequest` 给的隔离状态档（候选三层 id）与空 `userdata` 目录、
//   一个从 `127.0.0.1:0` 抢来的临时端口，并且显式 `env_remove(LAF_LOCAL_TOKEN)`
//   （带令牌时 `/api/health` 会 401，探活会被误判成「起不来」）。
// - 超时多久：单次 240 秒，`DEMIURGE_GATE_TIMEOUT_SECONDS` 可在 30~900 之间覆盖。
//   比 `ready_timeout` 的 180 秒宽，是因为候选层要冷启 torch/RAG 依赖，慢机器上 180 秒会假红。
// - 试几次：2 次，每次换进程换端口（与 `start_and_wait` 的「崩一次重试一次」同口径）。
//   两次都不过就判失败——不把等待时间继续翻倍，那在启动路径上等同于卡死。
// - 副作用边界：探测进程只读候选层，写只写 `runtime/.gate-<token>/`（交付侧负责删）。
//   它读不到也写不动用户的真实作品与设置，所以探的是「这一层能不能起」，
//   不是「带着你的数据能不能起」——后者由 `rollback_to_previous` 兜第二道网。
pub fn probe_candidate(request: &GateRequest) -> Result<(), String> {
    let exe = request
        .runtime_root
        .join("base")
        .join(&request.base_id)
        .join("Demiurge-Runtime.exe");
    if !exe.is_file() {
        return Err(format!("候选运行时文件缺失：{}", exe.display()));
    }
    let timeout = gate_timeout();
    let mut notes = Vec::new();
    for attempt in 1..=GATE_ATTEMPTS {
        let port = match free_loopback_port() {
            Ok(port) => port,
            Err(err) => return Err(err),
        };
        crate::shell_log::info(&format!(
            "提交自检第 {attempt}/{GATE_ATTEMPTS} 次：端口 {port}，单次预算 {} 秒",
            timeout.as_secs()
        ));
        let mut host = RuntimeHost::new(port);
        let layout = PackageLayout {
            package_root: request.install_dir.clone(),
            runtime_root: request.runtime_root.clone(),
            state_path: request.state_path.clone(),
            runtime_exe: exe.clone(),
            data_dir: request.data_dir.clone(),
            pid_path: request.data_dir.join("probe.pid"),
        };
        let launch = match host.packaged_launch(layout) {
            Ok(launch) => launch,
            Err(err) => return Err(err.to_string()),
        };
        host.mode = Some(RuntimeMode::Packaged);
        host.runtime_root = launch.runtime_root.clone();
        if let Err(err) = host.spawn(launch) {
            return Err(err.to_string());
        }
        let mut quiet = |_: &str| {};
        match host.wait_until_ready_within(timeout, &mut quiet) {
            Ok(()) if host.serves_frontend() => {
                host.stop();
                return Ok(());
            }
            Ok(()) => {
                host.stop();
                notes.push(format!(
                    "第 {attempt} 次：健康检查通过，但 `GET /` 没有返回带 id=\"root\" 的前端页面"
                ));
            }
            Err(err) => {
                notes.push(format!("第 {attempt} 次：{err}"));
                host.stop();
            }
        }
    }
    Err(format!(
        "{GATE_ATTEMPTS} 次尝试都没把新运行时起到就绪（单次预算 {} 秒）：\n{}",
        timeout.as_secs(),
        notes.join("\n")
    ))
}

const GATE_ATTEMPTS: u32 = 2;

fn gate_timeout() -> Duration {
    match std::env::var("DEMIURGE_GATE_TIMEOUT_SECONDS")
        .ok()
        .and_then(|raw| raw.trim().parse::<i64>().ok())
    {
        Some(seconds) if (30..=900).contains(&seconds) => Duration::from_secs(seconds as u64),
        _ => Duration::from_secs(240),
    }
}

/// 绑 0 号端口让内核分配，立刻释放后交给探测进程。
fn free_loopback_port() -> Result<u16, String> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0")
        .map_err(|err| format!("取临时端口失败：{err}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|err| format!("读临时端口失败：{err}"))
}

fn ready_timeout() -> Duration {
    match std::env::var("DEMIURGE_READY_TIMEOUT_SECONDS")
        .ok()
        .and_then(|raw| raw.trim().parse::<i64>().ok())
    {
        Some(seconds) if (5..=900).contains(&seconds) => Duration::from_secs(seconds as u64),
        _ => Duration::from_secs(180),
    }
}

fn port_open(port: u16) -> bool {
    use std::net::{SocketAddr, TcpStream};
    let address: SocketAddr = ([127, 0, 0, 1], port).into();
    TcpStream::connect_timeout(&address, Duration::from_millis(600)).is_ok()
}

/// 查「谁在监听这个端口」：`GetExtendedTcpTable` 拿 PID，再读映像路径。
/// 只解析监听项；路径读不到（受保护进程）也算拿到 PID，弹窗里如实写。
pub fn port_holder(port: u16) -> Option<PortHolder> {
    let pid = listener_pid(port)?;
    Some(PortHolder {
        pid,
        image_path: process_image_path(pid).unwrap_or_default(),
    })
}

/// `dwLocalPort` 的低 16 位是网络字节序的端口号。
fn decode_port(raw: u32) -> u16 {
    u16::from_be(raw as u16)
}

fn listener_pid(port: u16) -> Option<u32> {
    use windows_sys::Win32::NetworkManagement::IpHelper::{
        MIB_TCP6ROW_OWNER_PID, MIB_TCP6TABLE_OWNER_PID, MIB_TCPROW_OWNER_PID,
        MIB_TCPTABLE_OWNER_PID,
    };
    const AF_INET: u32 = 2;
    const AF_INET6: u32 = 23;
    unsafe {
        if let Some(table) = tcp_listener_table(AF_INET) {
            let table = table.as_ptr() as *const MIB_TCPTABLE_OWNER_PID;
            let rows = std::slice::from_raw_parts(
                (*table).table.as_ptr() as *const MIB_TCPROW_OWNER_PID,
                (*table).dwNumEntries as usize,
            );
            if let Some(row) = rows.iter().find(|row| decode_port(row.dwLocalPort) == port) {
                return Some(row.dwOwningPid);
            }
        }
        // 双栈 [::] 监听在 IPv4 表里查不到，但 127.0.0.1 能连上。
        if let Some(table) = tcp_listener_table(AF_INET6) {
            let table = table.as_ptr() as *const MIB_TCP6TABLE_OWNER_PID;
            let rows = std::slice::from_raw_parts(
                (*table).table.as_ptr() as *const MIB_TCP6ROW_OWNER_PID,
                (*table).dwNumEntries as usize,
            );
            if let Some(row) = rows.iter().find(|row| decode_port(row.dwLocalPort) == port) {
                return Some(row.dwOwningPid);
            }
        }
    }
    None
}

/// 用 `Vec<u32>` 兜底对齐：`GetExtendedTcpTable` 写的是原始字节表，
/// 而 `Vec<u8>` 只保证 1 字节对齐，转结构体指针会有未定义行为。
unsafe fn tcp_listener_table(family: u32) -> Option<Vec<u32>> {
    use windows_sys::Win32::NetworkManagement::IpHelper::{
        GetExtendedTcpTable, TCP_TABLE_OWNER_PID_LISTENER,
    };
    let mut size: u32 = 0;
    GetExtendedTcpTable(
        std::ptr::null_mut(),
        &mut size,
        0,
        family,
        TCP_TABLE_OWNER_PID_LISTENER,
        0,
    );
    if size == 0 {
        return None;
    }
    let words = (size as usize).div_ceil(std::mem::size_of::<u32>());
    let mut buffer: Vec<u32> = vec![0; words];
    let mut size = (words * std::mem::size_of::<u32>()) as u32;
    let status = GetExtendedTcpTable(
        buffer.as_mut_ptr().cast(),
        &mut size,
        0,
        family,
        TCP_TABLE_OWNER_PID_LISTENER,
        0,
    );
    (status == 0).then_some(buffer)
}

fn process_image_path(pid: u32) -> Option<String> {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return None;
    }
    let mut buffer = vec![0u16; 1024];
    let mut size = buffer.len() as u32;
    let ok = unsafe { QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut size) };
    unsafe { CloseHandle(handle) };
    if ok == 0 {
        return None;
    }
    Some(String::from_utf16_lossy(&buffer[..size as usize]))
}

fn wait_port_closed(port: u16, limit: Duration) -> bool {
    let deadline = Instant::now() + limit;
    while Instant::now() < deadline {
        if !port_open(port) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    false
}

fn taskkill(pid: u32, force: bool) -> bool {
    let mut command = Command::new("taskkill");
    command.args(["/PID", &pid.to_string(), "/T"]);
    if force {
        command.arg("/F");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    match command.status() {
        Ok(status) => status.success(),
        Err(err) => {
            crate::shell_log::warn(&format!("taskkill 失败（pid={pid}, force={force}）：{err}"));
            false
        }
    }
}

fn wait_exit(child: &mut Child, limit: Duration) -> bool {
    let deadline = Instant::now() + limit;
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Ok(None) => std::thread::sleep(Duration::from_millis(120)),
            Err(_) => return true,
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_port_reads_network_order() {
        assert_eq!(decode_port(0x0000_4A1F), 8010);
        assert_eq!(decode_port(0x0000_5000), 80);
    }

    /// 真实系统 API 自证：自己监听一个临时端口，占用者必须解析回自己的 PID。
    #[test]
    fn port_holder_resolves_own_listener() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("绑定临时端口");
        let port = listener.local_addr().expect("读临时端口").port();
        let holder = port_holder(port).expect("本进程的监听应能被识别");
        assert_eq!(holder.pid, std::process::id());
        assert!(!holder.image_path.is_empty(), "应能读到测试进程的映像路径");
        assert!(!holder.looks_like_demiurge(), "测试进程不该被误判成 Demiurge");
    }

    #[test]
    fn cleanup_prompt_names_holder_and_warns_on_foreign_process() {
        let conflict = PortConflict {
            port: 8010,
            demiurge_without_frontend: false,
            holder: Some(PortHolder {
                pid: 44376,
                image_path: r"D:\tool\develop\Python\pythoncore-3.13-64\python.exe".to_string(),
            }),
        };
        let text = conflict.cleanup_prompt().expect("有 PID 就该有正文");
        assert!(text.contains("python.exe（PID 44376）"), "{text}");
        assert!(text.contains("看起来不是 Demiurge 的进程"), "{text}");
        assert!(text.contains("是：结束该进程并继续启动 Demiurge"), "{text}");
    }

    #[test]
    fn cleanup_prompt_for_demiurge_backend_without_frontend() {
        let conflict = PortConflict {
            port: 8010,
            demiurge_without_frontend: true,
            holder: Some(PortHolder {
                pid: 12,
                image_path: r"D:\tool\Demiurge\backend\.venv\Scripts\python.exe".to_string(),
            }),
        };
        let text = conflict.cleanup_prompt().expect("有 PID 就该有正文");
        assert!(text.contains("没有提供前端页面"), "{text}");
        assert!(!text.contains("看起来不是 Demiurge 的进程"), "{text}");
    }

    #[test]
    fn cleanup_prompt_without_pid_offers_nothing_to_kill() {
        let conflict = PortConflict {
            port: 8010,
            demiurge_without_frontend: false,
            holder: None,
        };
        assert!(conflict.cleanup_prompt().is_none());
    }

    #[test]
    fn holder_classification_is_exact() {
        let holder = |path: &str| PortHolder {
            pid: 1,
            image_path: path.to_string(),
        };
        assert!(holder(r"D:\tool\Demiurge\Demiurge.exe").looks_like_demiurge());
        assert!(holder(r"C:\Program Files\Demiurge\Demiurge-Runtime.exe").looks_like_demiurge());
        assert!(holder(r"D:\tool\Demiurge\backend\.venv\Scripts\python.exe").looks_like_demiurge());
        assert!(!holder(r"D:\tool\develop\Python\pythoncore-3.13-64\python.exe")
            .looks_like_demiurge());
        assert!(!holder(r"C:\Windows\System32\svchost.exe").looks_like_demiurge());
    }
}
