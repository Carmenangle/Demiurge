//! `--self-test`：与 WPF 壳同一份 15 项判据，两套壳各跑一遍，输出必须一致。
//! 名字逐条对齐 `desktop/Demiurge.Desktop/SelfTest.cs`，不要"顺手改名"——
//! 改名的代价是两边输出无法机器比对。
use std::path::{Path, PathBuf};

use crate::paths;
use crate::runtime_host;
use crate::settings::ShellSettings;

struct Recorder {
    report: String,
    passed: u32,
    failed: u32,
}

impl Recorder {
    fn new() -> Self {
        Self {
            report: String::new(),
            passed: 0,
            failed: 0,
        }
    }

    fn check(&mut self, name: &str, outcome: Result<(), String>) {
        match outcome {
            Ok(()) => {
                self.passed += 1;
                self.report.push_str(&format!("PASS {name}\n"));
            }
            Err(error) => {
                self.failed += 1;
                self.report.push_str(&format!("FAIL {name} — {error}\n"));
            }
        }
    }

    /// 断言型检查：失败时给一句可读原因。
    fn expect(&mut self, name: &str, condition: bool) {
        self.check(
            name,
            if condition {
                Ok(())
            } else {
                Err("断言不成立".to_string())
            },
        );
    }
}

pub fn run() -> i32 {
    attach_console();
    let mut recorder = Recorder::new();

    // 1~3 探活判定
    recorder.expect(
        "health: ok payload",
        runtime_host::is_healthy_payload("{\"status\":\"ok\"}"),
    );
    recorder.expect(
        "health: reject other payloads",
        !runtime_host::is_healthy_payload("{\"status\":\"down\"}")
            && !runtime_host::is_healthy_payload("{}")
            && !runtime_host::is_healthy_payload("not json"),
    );
    recorder.expect(
        "frontend: root marker",
        runtime_host::is_frontend_document("<html><body><div id=\"root\"></div></body></html>")
            && !runtime_host::is_frontend_document("{\"detail\":\"Not Found\"}"),
    );

    // 4~5 端口解析
    recorder.expect(
        "port: 65536 rejected",
        runtime_host::parse_port("65536", "DEMIURGE_PORT").is_err(),
    );
    recorder.expect(
        "port: 8010 accepted",
        runtime_host::parse_port("8010", "DEMIURGE_PORT").ok() == Some(8010),
    );

    // 6~10 端口优先级（临时清环境，跑完还原）
    let saved_demiurge_port = std::env::var("DEMIURGE_PORT").ok();
    let saved_runtime_port = std::env::var("LAF_RUNTIME_PORT").ok();
    std::env::remove_var("DEMIURGE_PORT");
    std::env::remove_var("LAF_RUNTIME_PORT");
    recorder.expect(
        "port: default 8010",
        runtime_host::resolve_port(&ShellSettings { port: 0, ..Default::default() }).ok() == Some(8010),
    );
    recorder.expect(
        "port: settings value",
        runtime_host::resolve_port(&ShellSettings { port: 8123, ..Default::default() }).ok() == Some(8123),
    );
    std::env::set_var("LAF_RUNTIME_PORT", "8124");
    recorder.expect(
        "port: LAF_RUNTIME_PORT",
        runtime_host::resolve_port(&ShellSettings { port: 8123, ..Default::default() }).ok() == Some(8124),
    );
    std::env::set_var("DEMIURGE_PORT", "8125");
    recorder.expect(
        "port: DEMIURGE_PORT wins",
        runtime_host::resolve_port(&ShellSettings { port: 8123, ..Default::default() }).ok() == Some(8125),
    );
    std::env::set_var("DEMIURGE_PORT", "0");
    recorder.expect(
        "port: invalid env rejected",
        runtime_host::resolve_port(&ShellSettings { port: 8123, ..Default::default() }).is_err(),
    );
    restore_env("DEMIURGE_PORT", saved_demiurge_port);
    restore_env("LAF_RUNTIME_PORT", saved_runtime_port);

    // 11~13 设置与布局（用临时目录，跑完删）
    let workspace = paths::home().join(format!("self-test-{}", unique_token()));
    let _ = std::io::Write::flush(&mut std::io::stdout());
    eprintln!("[self-test] home={}", paths::home().display());
    eprintln!("[self-test] workspace={}", workspace.display());
    let outcome = (|| -> Result<(), String> {
        ctx("create workspace", std::fs::create_dir_all(&workspace))?;
        let settings_path = workspace.join("desktop.json");
        let settings = ShellSettings {
            width: Some(100.0),
            height: Some(100.0),
            left: Some(-999_999.0),
            top: Some(-999_999.0),
            maximized: false,
            port: 90_000,
        };
        ctx("save settings", settings.save(Some(settings_path.clone())))?;
        let loaded = ShellSettings::load(Some(settings_path.clone()));
        recorder.check(
            "settings: round-trip clamps bounds",
            if loaded.width.unwrap_or(0.0) >= crate::settings::MIN_WIDTH
                && loaded.height.unwrap_or(0.0) >= crate::settings::MIN_HEIGHT
                && loaded.port == runtime_host::DEFAULT_PORT as i64
                && loaded.left.unwrap_or(-99_999.0) > -20_000.0
            {
                Ok(())
            } else {
                Err(format!(
                    "width={:?} height={:?} port={} left={:?}",
                    loaded.width, loaded.height, loaded.port, loaded.left
                ))
            },
        );

        ctx("write broken json", std::fs::write(&settings_path, "{ broken json"))?;
        let fallback = ShellSettings::load(Some(settings_path));
        recorder.expect(
            "settings: malformed file falls back",
            fallback.width.unwrap_or(0.0) == crate::settings::DEFAULT_WIDTH
                && fallback.height.unwrap_or(0.0) == crate::settings::DEFAULT_HEIGHT
                && fallback.port == runtime_host::DEFAULT_PORT as i64,
        );

        let package_root = workspace.join("package");
        let runtime_root = package_root.join("data").join("runtime");
        ctx("create fake runtime", std::fs::create_dir_all(runtime_root.join("base").join("base-test")))?;
        ctx(
            "write current.json",
            std::fs::write(
                runtime_root.join("current.json"),
                "{\"schema_version\": 3, \"base_id\": \"base-test\", \"application_id\": \"app-test\"}",
            ),
        )?;
        let layout = paths::resolve_package(&package_root.join("data"));
        recorder.check(
            "layout: package detected from state file",
            match layout {
                Some(layout)
                    if layout.package_root == package_root
                        && layout.runtime_exe
                            == runtime_root.join("base").join("base-test").join("Demiurge-Runtime.exe")
                        && layout.data_dir == package_root.join("data").join("userdata") =>
                {
                    Ok(())
                }
                Some(layout) => Err(format!("布局不符：{}", layout.runtime_exe.display())),
                None => Err("未识别出便携包".to_string()),
            },
        );
        Ok(())
    })();
    if let Err(error) = outcome {
        recorder.failed += 1;
        recorder.report.push_str(&format!("FAIL self-test workspace — {error}\n"));
    }
    let _ = std::fs::remove_dir_all(&workspace);

    // 14 当前树能认出仓库或便携包
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."));
    recorder.expect(
        "layout: repo or package present",
        paths::find_repo(&exe_dir).is_some() || paths::resolve_package(&exe_dir).is_some(),
    );

    // 15 Job 对象关句柄回收进程树
    recorder.check("job: kill tree when handle closes", job_kill_test());

    let summary = format!("self-test: {} passed, {} failed", recorder.passed, recorder.failed);
    recorder.report.push_str(&summary);
    recorder.report.push('\n');
    let _ = std::fs::create_dir_all(paths::logs_dir());
    let _ = std::fs::write(paths::self_test_log_path(), recorder.report.as_bytes());
    print!("{}", recorder.report);
    let _ = std::io::Write::flush(&mut std::io::stdout());
    if recorder.failed == 0 {
        0
    } else {
        1
    }
}

fn job_kill_test() -> Result<(), String> {
    use std::process::{Command, Stdio};
    let job = crate::job::ChildProcessJob::create().ok_or_else(|| "CreateJobObject 失败".to_string())?;
    let mut child = Command::new("cmd.exe")
        .args(["/c", "ping -n 60 127.0.0.1"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("起测试进程失败：{err}"))?;
    if !job.assign_pid(child.id()) {
        let _ = child.kill();
        return Err("AssignProcessToJobObject 失败".to_string());
    }
    drop(job); // 关句柄 ⇒ 内核终止整棵树
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) => return Ok(()),
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(120)),
            Err(err) => return Err(format!("等待退出失败：{err}")),
        }
    }
    let _ = child.kill();
    Err("5 秒内未被 Job 回收".to_string())
}

fn ctx<T>(label: &str, result: std::io::Result<T>) -> Result<T, String> {
    result.map_err(|err| format!("{label}: {err}"))
}

fn restore_env(name: &str, value: Option<String>) {
    match value {
        Some(value) => std::env::set_var(name, value),
        None => std::env::remove_var(name),
    }
}

fn unique_token() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{nanos:x}")
}

/// 发布版是 windows 子系统，没有控制台；挂到父控制台才能把报告打到 stdout。
fn attach_console() {
    unsafe {
        windows_sys::Win32::System::Console::AttachConsole(u32::MAX);
    }
}
