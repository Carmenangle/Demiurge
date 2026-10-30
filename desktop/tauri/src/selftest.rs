//! `--self-test`：前 15 项与 WPF 壳同一份判据，两套壳各跑一遍，前 15 行输出必须一致；
//! 第 16 项是 Tauri 独有的安装版布局，追加在末尾，不影响前 15 行对拍。
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
            maximized: true,
            fullscreen: true,
            port: 90_000,
            geometry_version: 2,
        };
        ctx("save settings", settings.save(Some(settings_path.clone())))?;
        let loaded = ShellSettings::load(Some(settings_path.clone()));
        recorder.check(
            "settings: round-trip clamps bounds",
            if loaded.width.unwrap_or(0.0) >= crate::settings::MIN_WIDTH
                && loaded.height.unwrap_or(0.0) >= crate::settings::MIN_HEIGHT
                && loaded.port == runtime_host::DEFAULT_PORT as i64
                && loaded.left.unwrap_or(-99_999.0) > -20_000.0
                && loaded.maximized
                && loaded.fullscreen
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
                        && layout.data_dir == package_root.join("data").join("userdata")
                        && layout.pid_path == runtime_root.join("runtime.pid") =>
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
        paths::find_repo(&exe_dir).is_some()
            || paths::resolve_package(&exe_dir).is_some()
            || paths::resolve_installed(&exe_dir).is_some(),
    );

    // 15 Job 对象关句柄回收进程树
    recorder.check("job: kill tree when handle closes", job_kill_test());

    // 16 安装版布局（Tauri 独有，WPF 无此形态；追加在末尾，前 15 行仍可与 WPF 逐行比对）
    let install_root = paths::home().join(format!("self-test-install-{}", unique_token()));
    let installed_home = paths::home().join(format!("self-test-home-{}", unique_token()));
    let installed_check = (|| -> Result<(), String> {
        let runtime_root = installed_home.join("runtime");
        ctx(
            "create installed runtime",
            std::fs::create_dir_all(runtime_root.join("base").join("base-installed")),
        )?;
        ctx(
            "write installed current.json",
            std::fs::write(
                runtime_root.join("current.json"),
                "{\"schema_version\": 3, \"base_id\": \"base-installed\", \"application_id\": \"app-test\"}",
            ),
        )?;
        ctx("create install dir", std::fs::create_dir_all(&install_root))?;
        ctx(
            "create bundled git",
            std::fs::create_dir_all(install_root.join("dependencies").join("git").join("cmd")),
        )?;
        ctx(
            "create bundled ffmpeg",
            std::fs::create_dir_all(runtime_root.join("dependencies").join("ffmpeg").join("bin")),
        )?;
        let layout = paths::resolve_installed_in(&installed_home, &install_root)
            .ok_or_else(|| "未识别 installed 布局".to_string())?;
        let deps = paths::dependency_bin_dirs(&layout);
        if layout.package_root == install_root
            && layout.runtime_root == runtime_root
            && layout.state_path == runtime_root.join("current.json")
            && layout.data_dir == installed_home.join("userdata")
            && layout.pid_path == installed_home.join("runtime.pid")
            && layout.runtime_exe
                == runtime_root.join("base").join("base-installed").join("Demiurge-Runtime.exe")
            && deps
                == vec![
                    install_root.join("dependencies").join("git").join("cmd"),
                    runtime_root.join("dependencies").join("ffmpeg").join("bin"),
                ]
        {
            Ok(())
        } else {
            Err(format!(
                "布局或依赖 PATH 不符：{} / {} 个依赖目录",
                layout.runtime_exe.display(),
                deps.len()
            ))
        }
    })();
    let _ = std::fs::remove_dir_all(&install_root);
    let _ = std::fs::remove_dir_all(&installed_home);
    recorder.check("layout: installed runtime under home", installed_check);

    // 17 运行时按需交付（Tauri 独有）：离线 fixture 覆盖分卷下载/合并、runtime-pack、
    //    复用、坏分件、空间不足与 pin 漂移；全程不联网。
    recorder.check("delivery: runtime pack install", delivery_test());

    // 18 自动更新验签（Tauri 独有）：固定夹具覆盖 公钥→签名→载荷、篡改拒绝与版本绑定，
    //    不联网、不用私钥；生产公钥轮换不影响这组独立测试密钥。
    recorder.check("updater: signature verify", crate::updater::verify_test_fixture());

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

fn delivery_test() -> Result<(), String> {
    use serde_json::json;

    let root = paths::home().join(format!("self-test-delivery-{}", unique_token()));
    let outcome = (|| -> Result<(), String> {
        let assets = root.join("assets");
        std::fs::create_dir_all(&assets).map_err(err_text)?;

        // 基础层：两个分卷，覆盖「分件 → 合并 → 归档校验」。
        let base_tree = root.join("trees").join("base");
        write_fixture(&base_tree.join("Demiurge-Runtime.exe"), b"fake-runtime")?;
        write_fixture(
            &base_tree.join("base-manifest.json"),
            br#"{"schema_version":1,"base_id":"base-test"}"#,
        )?;
        let base_zip = root.join("base.zip");
        write_zip(&base_tree, &base_zip, "base-base-test")?;
        let base_bytes = std::fs::read(&base_zip).map_err(err_text)?;
        let base_stem = "Demiurge_base_windows_v0.1.0";
        let base_name = format!("{base_stem}.zip");
        let split = base_bytes.len() / 2;
        let part1 = format!("{base_stem}.part01");
        let part2 = format!("{base_stem}.part02");
        write_fixture(&assets.join(&part1), &base_bytes[..split])?;
        write_fixture(&assets.join(&part2), &base_bytes[split..])?;

        // 应用层：单个归档。
        let app_tree = root.join("trees").join("app");
        write_fixture(
            &app_tree.join("frontend").join("index.html"),
            b"<html><div id=\"root\"></div></html>",
        )?;
        write_fixture(
            &app_tree.join("application-manifest.json"),
            br#"{"schema_version":1,"application_id":"app-test"}"#,
        )?;
        let app_name = "Demiurge_application_windows_v0.1.0.zip";
        let app_zip = assets.join(app_name);
        write_zip(&app_tree, &app_zip, "application-app-test")?;
        let app_bytes = std::fs::read(&app_zip).map_err(err_text)?;

        // RAG 层：必装层，单档归档。
        let rag_tree = root.join("trees").join("rag");
        write_fixture(&rag_tree.join("site-packages").join("marker.txt"), b"fake-rag")?;
        write_fixture(
            &rag_tree.join("rag-manifest.json"),
            br#"{"schema_version":1,"rag_id":"rag-test"}"#,
        )?;
        let rag_name = "Demiurge_rag_windows_v0.1.0.zip";
        let rag_zip = assets.join(rag_name);
        write_zip(&rag_tree, &rag_zip, "rag-rag-test")?;
        let rag_bytes = std::fs::read(&rag_zip).map_err(err_text)?;

        let manifest_name = "Demiurge_update_windows_v0.1.0.json";
        let manifest = json!({
            "schema_version": 2,
            "app_version": "0.1.0",
            "target": "windows-x64-full-rag",
            "edition": "full-rag",
            "base_id": "base-test",
            "application_id": "app-test",
            "rag_id": "rag-test",
            "layers": {
                "base": {
                    "id": "base-test",
                    "archive": &base_name,
                    "size": base_bytes.len(),
                    "sha256": sha256_bytes(&base_bytes),
                    "assets": [
                        {"name": &part1, "size": split, "sha256": sha256_bytes(&base_bytes[..split])},
                        {"name": &part2, "size": base_bytes.len() - split, "sha256": sha256_bytes(&base_bytes[split..])}
                    ]
                },
                "application": {
                    "id": "app-test",
                    "archive": app_name,
                    "size": app_bytes.len(),
                    "sha256": sha256_bytes(&app_bytes),
                    "assets": [
                        {"name": app_name, "size": app_bytes.len(), "sha256": sha256_bytes(&app_bytes)}
                    ]
                },
                "rag": {
                    "id": "rag-test",
                    "archive": rag_name,
                    "size": rag_bytes.len(),
                    "sha256": sha256_bytes(&rag_bytes),
                    "assets": [
                        {"name": rag_name, "size": rag_bytes.len(), "sha256": sha256_bytes(&rag_bytes)}
                    ]
                }
            }
        });
        write_fixture(
            &assets.join(manifest_name),
            serde_json::to_string_pretty(&manifest).map_err(err_text)?.as_bytes(),
        )?;

        let pin_value = json!({
            "schema_version": 1,
            "tag": "v0.1.0",
            "version": "0.1.0",
            "target": "windows-x64-full-rag",
            "release_base": format!("file://{}", assets.display()).replace('\\', "/"),
            "manifest": manifest_name,
            "required_layers": ["base", "application", "rag"],
            "optional_layers": [],
            "mirrors": []
        });
        let pin: crate::runtime_delivery::DeliveryPin =
            serde_json::from_value(pin_value.clone()).map_err(err_text)?;

        let install = root.join("install");
        std::fs::create_dir_all(&install).map_err(err_text)?;
        let mut quiet = |_: &str| {};

        // ① 首次部署（file:// 下载 + 分卷合并），三层都必须落地。
        let home = root.join("home");
        let report = crate::runtime_delivery::deliver(&pin, &home, &install, None, &mut quiet)
            .map_err(|err| format!("首次部署失败：{err}"))?;
        if report.reused
            || report.base_id != "base-test"
            || report.application_id != "app-test"
            || report.rag_id != "rag-test"
            || report.downloaded_layers.len() != 3
        {
            return Err(format!("首次部署结果不符：{report:?}"));
        }
        let runtime = home.join("runtime");
        if !runtime.join("base").join("base-test").join("Demiurge-Runtime.exe").is_file()
            || !runtime
                .join("apps")
                .join("app-test")
                .join("frontend")
                .join("index.html")
                .is_file()
            || !runtime
                .join("rag")
                .join("rag-test")
                .join("site-packages")
                .join("marker.txt")
                .is_file()
        {
            return Err("解压后的运行时文件缺失".to_string());
        }
        let current: serde_json::Value =
            serde_json::from_slice(&std::fs::read(runtime.join("current.json")).map_err(err_text)?)
                .map_err(err_text)?;
        if current.get("base_id").and_then(|value| value.as_str()) != Some("base-test")
            || current.get("application_id").and_then(|value| value.as_str()) != Some("app-test")
            || current.get("rag_id").and_then(|value| value.as_str()) != Some("rag-test")
        {
            return Err(format!("current.json 内容不符：{current}"));
        }

        // ② 再跑一次必须复用，不重复解压。
        let again = crate::runtime_delivery::deliver(&pin, &home, &install, None, &mut quiet)
            .map_err(|err| format!("重复部署失败：{err}"))?;
        if !again.reused || !again.downloaded_layers.is_empty() {
            return Err("重复部署没有走复用路径".to_string());
        }

        // ③ 离线 runtime-pack：源地址不存在，成功即证明完全没联网。
        let offline_install = root.join("install-offline");
        let pack = offline_install.join("runtime-pack");
        std::fs::create_dir_all(&pack).map_err(err_text)?;
        for name in [manifest_name, part1.as_str(), part2.as_str(), app_name, rag_name] {
            std::fs::copy(assets.join(name), pack.join(name)).map_err(err_text)?;
        }
        let mut offline_value = pin_value;
        offline_value["release_base"] = json!("file:///nonexistent-demiurge-assets");
        let offline_pin: crate::runtime_delivery::DeliveryPin =
            serde_json::from_value(offline_value).map_err(err_text)?;
        let offline_home = root.join("home-offline");
        let offline = crate::runtime_delivery::deliver(
            &offline_pin,
            &offline_home,
            &offline_install,
            None,
            &mut quiet,
        )
        .map_err(|err| format!("离线 runtime-pack 部署失败：{err}"))?;
        if offline.reused
            || !offline_home
                .join("runtime")
                .join("base")
                .join("base-test")
                .join("Demiurge-Runtime.exe")
                .is_file()
        {
            return Err("离线 runtime-pack 没有完成部署".to_string());
        }

        // ④ 坏分件必须被拒，且失败路径不能写出 current.json。
        //    保持字节数不变、只翻转一个字节，确保命中的是哈希校验而不是大小校验。
        let mut corrupted = std::fs::read(assets.join(&part2)).map_err(err_text)?;
        corrupted[0] ^= 0xFF;
        write_fixture(&assets.join(&part2), &corrupted)?;
        let broken_home = root.join("home-broken");
        match crate::runtime_delivery::deliver(&pin, &broken_home, &install, None, &mut quiet) {
            Ok(_) => return Err("坏分件没有被拒绝".to_string()),
            Err(err)
                if !err.to_string().contains("哈希") && !err.to_string().contains("校验") =>
            {
                return Err(format!("坏分件报错信息不符：{err}"));
            }
            Err(_) => {}
        }
        if broken_home.join("runtime").join("current.json").is_file() {
            return Err("失败路径写出了 current.json".to_string());
        }

        // ⑤ 空间不足在下载前就被拦住。
        let small_home = root.join("home-small");
        match crate::runtime_delivery::deliver(&pin, &small_home, &install, Some(1), &mut quiet) {
            Err(err) if err.to_string().contains("磁盘空间不足") => {}
            other => return Err(format!("空间不足没有被拦截：{other:?}")),
        }

        // ⑥ pin 自身的清单名漂移必须拒绝。
        let mut drifted = serde_json::to_value(&pin).map_err(err_text)?;
        drifted["manifest"] = json!("Demiurge_update_windows_v9.9.9.json");
        let drifted_pin: crate::runtime_delivery::DeliveryPin =
            serde_json::from_value(drifted).map_err(err_text)?;
        match crate::runtime_delivery::deliver(
            &drifted_pin,
            &root.join("home-drift"),
            &install,
            Some(u64::MAX),
            &mut quiet,
        ) {
            Err(err) if err.to_string().contains("清单名") => {}
            other => return Err(format!("漂移 pin 没有被拒绝：{other:?}")),
        }

        // ⑦ 分卷名 → URL 推导；成功路径不能留下临时目录。
        if crate::runtime_delivery::url_join(
            "https://example.test/releases/",
            "base.zip.part01",
        ) != "https://example.test/releases/base.zip.part01"
        {
            return Err("分件 URL 推导不符".to_string());
        }
        let leftovers: Vec<String> = std::fs::read_dir(&runtime)
            .map_err(err_text)?
            .flatten()
            .map(|entry| entry.file_name().to_string_lossy().to_string())
            .filter(|name| name.starts_with(".staging-") || name.starts_with(".current-"))
            .collect();
        if !leftovers.is_empty() {
            return Err(format!("运行时目录遗留临时项：{leftovers:?}"));
        }
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&root);
    outcome
}

fn write_fixture(path: &Path, bytes: &[u8]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(err_text)?;
    }
    std::fs::write(path, bytes).map_err(err_text)
}

fn write_zip(tree: &Path, target: &Path, root_name: &str) -> Result<(), String> {
    use std::io::Write as _;

    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(err_text)?;
    }
    let file = std::fs::File::create(target).map_err(err_text)?;
    let mut writer = zip::ZipWriter::new(file);
    for path in collect_files(tree) {
        let relative = path
            .strip_prefix(tree)
            .map_err(err_text)?
            .to_string_lossy()
            .replace('\\', "/");
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        writer
            .start_file(format!("{root_name}/{relative}"), options)
            .map_err(err_text)?;
        writer
            .write_all(&std::fs::read(&path).map_err(err_text)?)
            .map_err(err_text)?;
    }
    writer.finish().map_err(err_text)?;
    Ok(())
}

fn collect_files(dir: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(current) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else {
                files.push(path);
            }
        }
    }
    files.sort();
    files
}

fn sha256_bytes(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn err_text(err: impl std::fmt::Display) -> String {
    err.to_string()
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
pub(crate) fn attach_console() {
    unsafe {
        windows_sys::Win32::System::Console::AttachConsole(u32::MAX);
    }
}
