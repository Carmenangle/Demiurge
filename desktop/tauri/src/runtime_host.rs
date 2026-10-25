//! 运行时生命周期：解析端口 → 探活 → 复用已在跑的服务 → 拉起 → 等就绪 → 回收。
//! 语义逐条对齐 WPF 的 `RuntimeHost.cs`（那份是合同，改动要先改它再改这里）。
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use crate::paths::{self, PackageLayout, RepoLayout};
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

    pub fn ensure_ready(&mut self, mut progress: impl FnMut(&str)) -> StartupResult<()> {
        progress(&format!("检查 {} …", self.base_url()));
        if self.is_healthy() {
            if !self.serves_frontend() {
                return Err(RuntimeStartupError(format!(
                    "端口 {} 上已有 Demiurge 后端在运行，但它没有提供前端页面（通常是在开发模式下直接启动的后端）。\n\n\
                     请关闭该后端后重试，或为它设置 LAF_FRONTEND_DIST 指向已构建的 frontend/dist。\n\
                     桌面壳不会擅自改用其他端口。",
                    self.port
                )));
            }
            self.reused_external = true;
            self.mode = None;
            progress(&format!("复用已在运行的后端：{}", self.base_url()));
            crate::shell_log::info(&format!("复用已运行的 Demiurge：{}", self.base_url()));
            return Ok(());
        }

        if self.is_port_open() {
            return Err(RuntimeStartupError(format!(
                "端口 {} 已被其他程序占用，无法启动 Demiurge。\n\n请关闭占用该端口的程序后重试。",
                self.port
            )));
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
        self.early_exit = None;
        self.spawn(launch)?;
        progress(match self.mode {
            Some(RuntimeMode::Packaged) => "启动内置运行时…",
            _ => "启动后端（Python / uvicorn）…",
        });
        match self.wait_until_ready(&mut progress) {
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
                self.early_exit = None;
                self.spawn(launch)?;
                progress("重新启动后端（Python / uvicorn）…");
                self.wait_until_ready(&mut progress)
            }
            Err(err) => Err(err),
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
            return self.packaged_launch(package);
        }
        // 仓库优先于安装版：开发时把便携壳放进仓库根仍应使用 `.venv`，而不是误用本机装的那份运行时。
        let repo = paths::find_repo(&exe_dir).or_else(|| paths::find_repo(&cwd));
        if let Some(repo) = repo {
            return self.development_launch(repo);
        }
        if let Some(installed) = paths::resolve_installed(&exe_dir) {
            return self.packaged_launch(installed);
        }
        Err(RuntimeStartupError(format!(
            "未找到 Demiurge 运行时。\n\n\
             请把桌面壳放在便携包根目录（含 data/runtime/current.json）、在仓库内运行，\
             或先完成安装版的运行时部署（{}）。",
            paths::installed_runtime_root().join("current.json").display()
        )))
    }

    /// 安装版首次启动：自动部署 base + application（RAG 可选，走 `--install-runtime --with-rag`）。
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
        let report = crate::runtime_delivery::ensure_installed_runtime(&exe_dir, false, progress)
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
            report.base_id,
            report.application_id,
            if report.rag_id.is_empty() {
                "未安装"
            } else {
                report.rag_id.as_str()
            }
        ));
        if report.rag_available && report.rag_id.is_empty() {
            progress("提示：未安装可选的 RAG 增强层（可用 Demiurge.exe --install-runtime --with-rag 追加）");
        }
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
        let timeout = ready_timeout();
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
