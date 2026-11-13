//! `--self-test`：前 15 项与 WPF 壳同一份判据，两套壳各跑一遍，前 15 行输出必须一致；
//! 第 16—19 项是 Tauri 独有（安装版布局、按需交付、更新验签、提交闸与回滚），
//! 一律追加在末尾，不影响前 15 行对拍。
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
            // 其余字段取默认：新增设置项不该让这条夹具跟着改（它验的是几何与端口的夹取）。
            ..ShellSettings::default()
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

    // 19 提交闸与回滚（Tauri 独有）：脚本化闸替掉真探活，验「闸不过绝不提交」「过闸才记前态」
    //    「回退只翻指向」「回收只删孤儿」「安全模式只清运行时」。全程离线、不起真进程。
    recorder.check("delivery: commit gate and rollback", commit_gate_test());

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

        // RAG 层：P3 起是**可选层**，仍照常出现在清单里——留着它才能证明
        // 「清单有这一层、但 required_layers 不含 ⇒ 首启既不下载也不解压」。
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
            "required_layers": ["base", "application"],
            "optional_layers": ["rag"],
            "mirrors": []
        });
        let pin: crate::runtime_delivery::DeliveryPin =
            serde_json::from_value(pin_value.clone()).map_err(err_text)?;

        let install = root.join("install");
        std::fs::create_dir_all(&install).map_err(err_text)?;
        let mut quiet = |_: &str| {};

        // ① 首次部署（file:// 下载 + 分卷合并）：只落地 required 两层。
        //    清单里明明有 rag 层、分件也在同一个 file:// 目录里，但 required 不含它
        //    ⇒ 一次字节都不许取、一个文件都不许解，`current.json` 的 rag_id 必须留空。
        let home = root.join("home");
        let report = crate::runtime_delivery::deliver(&pin, &home, &install, None, &mut quiet)
            .map_err(|err| format!("首次部署失败：{err}"))?;
        if report.reused
            || report.base_id != "base-test"
            || report.application_id != "app-test"
            || !report.rag_id.is_empty()
            || report.downloaded_layers != vec!["base".to_string(), "application".to_string()]
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
            || runtime.join("rag").exists()
        {
            return Err("解压后的运行时文件不符（可选层不该落地）".to_string());
        }
        let current: serde_json::Value =
            serde_json::from_slice(&std::fs::read(runtime.join("current.json")).map_err(err_text)?)
                .map_err(err_text)?;
        if current.get("base_id").and_then(|value| value.as_str()) != Some("base-test")
            || current.get("application_id").and_then(|value| value.as_str()) != Some("app-test")
            || current.get("rag_id").and_then(|value| value.as_str()) != Some("")
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
            || !offline.rag_id.is_empty()
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

        // ⑧ 清单里**根本没有 rag 层**：2026-10-30 那版契约在这里直接判死
        //    （「运行时清单缺少 rag 层」）。P3 起 required 只有两层 ⇒ 必须照样交付成功，
        //    并且这个资产目录里没有 rag 分件也完全不缺件。
        let norag_assets = root.join("assets-norag");
        std::fs::create_dir_all(&norag_assets).map_err(err_text)?;
        // 分件要从原始字节重写：④ 为了验哈希，已经把 `assets/` 里的 part2 就地改坏了，
        // 从那里复制会拿到坏档，报出来的就不是「缺 rag」这件事了。
        write_fixture(&norag_assets.join(&part1), &base_bytes[..split])?;
        write_fixture(&norag_assets.join(&part2), &base_bytes[split..])?;
        std::fs::copy(assets.join(app_name), norag_assets.join(app_name)).map_err(err_text)?;
        let mut norag_manifest = manifest.clone();
        if let Some(layers) = norag_manifest["layers"].as_object_mut() {
            layers.remove("rag");
        }
        norag_manifest["rag_id"] = json!("");
        write_fixture(
            &norag_assets.join(manifest_name),
            serde_json::to_string_pretty(&norag_manifest)
                .map_err(err_text)?
                .as_bytes(),
        )?;
        let mut norag_value = serde_json::to_value(&pin).map_err(err_text)?;
        norag_value["release_base"] = json!(
            format!("file://{}", norag_assets.display()).replace('\\', "/")
        );
        let norag_pin: crate::runtime_delivery::DeliveryPin =
            serde_json::from_value(norag_value).map_err(err_text)?;
        let norag_home = root.join("home-norag");
        let norag = crate::runtime_delivery::deliver(
            &norag_pin,
            &norag_home,
            &install,
            None,
            &mut quiet,
        )
        .map_err(|err| format!("清单缺 rag 层时交付不该失败：{err}"))?;
        if !norag.rag_id.is_empty()
            || !norag_home
                .join("runtime")
                .join("base")
                .join("base-test")
                .join("Demiurge-Runtime.exe")
                .is_file()
        {
            return Err(format!("清单缺 rag 层的交付结果不符：{norag:?}"));
        }

        // ⑨ 后台补齐（P3-b）：① 那个 home 上「清单有 rag 层、盘上没有」，现在把它补上。
        //    走的是必装层同一条主体 ⇒ 这里要看的是**只有** rag 被下、`current.json` 的 rag_id
        //    由空变真、以及账本落到了后端读得到的位置（`userdata/delivery.json`）。
        if !crate::runtime_delivery::optional_layer_missing(&home) {
            return Err("⑨ 开始前应当判定可选层缺席（current.json 的 rag_id 是空的）".to_string());
        }
        let backfill =
            crate::runtime_delivery::deliver_optional(&pin, &home, &install, None, None, &mut quiet)
                .map_err(|err| format!("后台补齐失败：{err}"))?;
        if backfill.reused
            || backfill.rag_id != "rag-test"
            || backfill.downloaded_layers != vec!["rag".to_string()]
        {
            return Err(format!("后台补齐结果不符：{backfill:?}"));
        }
        if !runtime
            .join("rag")
            .join("rag-test")
            .join("site-packages")
            .join("marker.txt")
            .is_file()
        {
            return Err("补齐后盘上没有 rag 层的实际内容".to_string());
        }
        let committed: serde_json::Value =
            serde_json::from_slice(&std::fs::read(runtime.join("current.json")).map_err(err_text)?)
                .map_err(err_text)?;
        if committed.get("rag_id").and_then(|value| value.as_str()) != Some("rag-test") {
            return Err(format!("补齐后 current.json 的 rag_id 没翻过来：{committed}"));
        }
        if crate::runtime_delivery::optional_layer_missing(&home) {
            return Err("补齐后仍判定可选层缺席".to_string());
        }
        // 账本字段名是跨层合同：读侧＝`backend/app/services/shell_delivery.py`（有单测盯同一组名字）。
        let ledger_path = home.join("userdata").join("delivery.json");
        let ledger: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&ledger_path).map_err(err_text)?).map_err(err_text)?;
        let total = ledger.get("total_bytes").and_then(|value| value.as_u64()).unwrap_or(0);
        let done = ledger.get("done_bytes").and_then(|value| value.as_u64()).unwrap_or(0);
        if ledger.get("state").and_then(|value| value.as_str()) != Some("done")
            || ledger.get("layer").and_then(|value| value.as_str()) != Some("rag")
            || ledger.get("error").and_then(|value| value.as_str()) != Some("")
            || ledger.get("schema_version").and_then(|value| value.as_u64()) != Some(1)
        {
            return Err(format!("补齐账本的终态不符：{ledger}"));
        }
        if total != rag_bytes.len() as u64 || done != total {
            return Err(format!(
                "补齐账本的字节账不符：done={done} total={total}，期望 {}",
                rag_bytes.len()
            ));
        }

        // ⑩ 清单里一条可选层都没有（⑧ 那份 norag 夹具）⇒ 补齐是「无事可做」，
        //    既不失败也不许留下一条没发生过的进度（否则面板会挂出假的已完成）。
        let nothing = crate::runtime_delivery::deliver_optional(
            &norag_pin,
            &norag_home,
            &install,
            None,
            None,
            &mut quiet,
        )
        .map_err(|err| format!("清单缺可选层时补齐不该失败：{err}"))?;
        if !nothing.reused || !nothing.downloaded_layers.is_empty() || !nothing.rag_id.is_empty() {
            return Err(format!("清单缺可选层时的补齐结果不符：{nothing:?}"));
        }
        if norag_home.join("userdata").join("delivery.json").exists() {
            return Err("无事可做的补齐把账本留在了盘上".to_string());
        }
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&root);
    outcome
}

/// 第 19 项：提交闸与回滚（P2）。全程离线、不起真进程——夹具里的
/// `Demiurge-Runtime.exe` 是假字节，所以这里用**脚本化闸**替掉真探活，
/// 验的是「闸不过 ⇒ 绝不提交」「闸过 ⇒ 记前态」「回退只翻指向」「回收只删孤儿」。
fn commit_gate_test() -> Result<(), String> {
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::Mutex;
    use std::time::Duration;

    static GATE_CALLS: AtomicUsize = AtomicUsize::new(0);
    static GATE_ALLOW: AtomicBool = AtomicBool::new(false);
    static GATE_STATE_OK: AtomicBool = AtomicBool::new(false);
    static GATE_BASE: Mutex<Option<String>> = Mutex::new(None);

    // 脚本化闸：核对交付侧真的写好了隔离状态档与空数据目录，再按开关放行/拒绝。
    fn scripted_gate(request: &crate::runtime_delivery::GateRequest) -> Result<(), String> {
        use std::sync::atomic::Ordering as OrderingType;
        GATE_CALLS.fetch_add(1, OrderingType::SeqCst);
        *GATE_BASE.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) =
            Some(request.base_id.clone());
        GATE_STATE_OK.store(
            request.state_path.is_file()
                && request.data_dir.is_dir()
                && request.runtime_root.join("base").join(&request.base_id).is_dir(),
            OrderingType::SeqCst,
        );
        if GATE_ALLOW.load(OrderingType::SeqCst) {
            Ok(())
        } else {
            Err("测试用坏层：进程一起来就退".to_string())
        }
    }

    // 造一套「版本 v＋三层 id」的离线发布物，返回可直接喂给 deliver 的 pin。
    fn build_release(
        root: &Path,
        assets: &Path,
        version: &str,
        base_id: &str,
        application_id: &str,
        rag_id: &str,
    ) -> Result<crate::runtime_delivery::DeliveryPin, String> {
        use serde_json::json;

        let mut layers = serde_json::Map::new();
        for (name, id, dir, marker) in [
            ("base", base_id, "base", "base-manifest.json"),
            ("application", application_id, "app", "application-manifest.json"),
            ("rag", rag_id, "rag", "rag-manifest.json"),
        ] {
            let tree = root.join("trees").join(dir).join(version);
            match name {
                "base" => {
                    write_fixture(&tree.join("Demiurge-Runtime.exe"), b"fake-runtime")?;
                }
                "application" => {
                    write_fixture(
                        &tree.join("frontend").join("index.html"),
                        b"<html><div id=\"root\"></div></html>",
                    )?;
                }
                _ => {
                    write_fixture(&tree.join("site-packages").join("marker.txt"), b"fake-rag")?;
                }
            }
            write_fixture(
                &tree.join(marker),
                format!("{{\"schema_version\":1,\"{name}_id\":\"{id}\"}}").as_bytes(),
            )?;
            let archive_name = format!("Demiurge_{name}_windows_v{version}.zip");
            let archive = assets.join(&archive_name);
            write_zip(&tree, &archive, &format!("{dir}-{id}"))?;
            let bytes = std::fs::read(&archive).map_err(err_text)?;
            layers.insert(
                name.to_string(),
                json!({
                    "id": id,
                    "archive": &archive_name,
                    "size": bytes.len(),
                    "sha256": sha256_bytes(&bytes),
                    "assets": [{"name": &archive_name, "size": bytes.len(), "sha256": sha256_bytes(&bytes)}]
                }),
            );
        }
        let manifest_name = format!("Demiurge_update_windows_v{version}.json");
        let manifest = json!({
            "schema_version": 2,
            "app_version": version,
            "target": "windows-x64-full-rag",
            "edition": "full-rag",
            "base_id": base_id,
            "application_id": application_id,
            "rag_id": rag_id,
            "layers": layers,
        });
        write_fixture(
            &assets.join(&manifest_name),
            serde_json::to_string_pretty(&manifest).map_err(err_text)?.as_bytes(),
        )?;
        let pin_value = json!({
            "schema_version": 1,
            "tag": format!("v{version}"),
            "version": version,
            "target": "windows-x64-full-rag",
            "release_base": format!("file://{}", assets.display()).replace('\\', "/"),
            "manifest": manifest_name,
            "required_layers": ["base", "application"],
            "optional_layers": ["rag"],
            "mirrors": []
        });
        serde_json::from_value(pin_value).map_err(err_text)
    }

    let root = paths::home().join(format!("self-test-gate-{}", unique_token()));
    let outcome = (|| -> Result<(), String> {
        let assets = root.join("assets");
        let install = root.join("install");
        let home = root.join("home");
        let runtime = home.join("runtime");
        let downloads = home.join("downloads");
        std::fs::create_dir_all(&install).map_err(err_text)?;
        let mut quiet = |_: &str| {};

        // ① 已提交的旧一代：三层都在盘上，current.json 指着它们（这就是「可回退的前态」）。
        write_fixture(
            &runtime.join("base").join("base-old").join("Demiurge-Runtime.exe"),
            b"fake-runtime",
        )?;
        write_fixture(
            &runtime.join("base").join("base-old").join("base-manifest.json"),
            br#"{"schema_version":1,"base_id":"base-old"}"#,
        )?;
        write_fixture(
            &runtime.join("apps").join("app-old").join("frontend").join("index.html"),
            b"<html><div id=\"root\"></div></html>",
        )?;
        write_fixture(
            &runtime.join("apps").join("app-old").join("application-manifest.json"),
            br#"{"schema_version":1,"application_id":"app-old"}"#,
        )?;
        write_fixture(
            &runtime.join("rag").join("rag-old").join("rag-manifest.json"),
            br#"{"schema_version":1,"rag_id":"rag-old"}"#,
        )?;
        let committed = "{\"schema_version\":2,\"app_version\":\"0.1.0\",\"target\":\"windows-x64-full-rag\",\"edition\":\"full-rag\",\"base_id\":\"base-old\",\"application_id\":\"app-old\",\"rag_id\":\"rag-old\"}";
        write_fixture(&runtime.join("current.json"), committed.as_bytes())?;

        let pin = build_release(&root, &assets, "0.2.0", "base-new", "app-new", "rag-new")?;

        // ② 闸拒绝 ⇒ 不提交：旧 `current.json` 逐字不动、只删本次新层、旧层不动、分件缓存保留。
        GATE_ALLOW.store(false, Ordering::SeqCst);
        GATE_CALLS.store(0, Ordering::SeqCst);
        GATE_STATE_OK.store(false, Ordering::SeqCst);
        let rejected = crate::runtime_delivery::deliver_with_gate(
            &pin,
            &home,
            &install,
            None,
            Some(scripted_gate),
            &mut quiet,
        )
        .err()
        .ok_or("提交闸明确拒绝，交付却成功了")?;
        if !rejected.0.contains("未通过启动自检") {
            return Err(format!("拒绝信息里没写拒因：{rejected}"));
        }
        if GATE_CALLS.load(Ordering::SeqCst) == 0 {
            return Err("提交闸一次都没被调用".to_string());
        }
        if !GATE_STATE_OK.load(Ordering::SeqCst) {
            return Err("闸拿到的隔离状态档/空数据目录/候选层不完整".to_string());
        }
        if GATE_BASE
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_deref()
            != Some("base-new")
        {
            return Err("闸探的不是候选 base 层".to_string());
        }
        if std::fs::read_to_string(runtime.join("current.json")).map_err(err_text)? != committed {
            return Err("拒绝提交却改写了 current.json".to_string());
        }
        if runtime.join("previous.json").exists() {
            return Err("拒绝提交却写了 previous.json".to_string());
        }
        if runtime.join("base").join("base-new").exists()
            || runtime.join("apps").join("app-new").exists()
            || runtime.join("rag").join("rag-new").exists()
        {
            return Err("拒绝提交后新层目录没删干净".to_string());
        }
        if !runtime.join("base").join("base-old").join("Demiurge-Runtime.exe").is_file() {
            return Err("拒绝提交误删了旧一代的层".to_string());
        }
        if !downloads
            .join("Demiurge_base_windows_v0.2.0.zip")
            .is_file()
        {
            return Err("拒绝提交把下载缓存也清了（重试会重新花流量）".to_string());
        }
        if let Some(leftover) = first_temp_leftover(&runtime, &downloads) {
            return Err(format!("拒绝提交后仍有临时目录残留：{leftover}"));
        }

        // ③ 同一次交付重试（闸放行）⇒ 只用本地缓存重装配，然后提交并记下前态。
        GATE_ALLOW.store(true, Ordering::SeqCst);
        GATE_CALLS.store(0, Ordering::SeqCst);
        let report = crate::runtime_delivery::deliver_with_gate(
            &pin, &home, &install, None, Some(scripted_gate), &mut quiet,
        )
        .map_err(|err| format!("闸放行后仍然交付失败：{err}"))?;
        if report.reused || report.downloaded_layers.len() != 2 {
            return Err(format!("提交结果不符：{report:?}"));
        }
        if GATE_CALLS.load(Ordering::SeqCst) != 1 {
            return Err(format!("提交闸跑了 {} 次，应为 1 次", GATE_CALLS.load(Ordering::SeqCst)));
        }
        let ids = crate::runtime_delivery::read_layer_ids(&runtime.join("current.json"))
            .ok_or("提交后 current.json 读不出来")?;
        // rag 是可选层：候选清单广告了 `rag-new` 但这次没交付，盘上只有旧一代 `rag-old`。
        // 状态档必须留空而不是「接着挂旧的」——torch 的 DLL 跟 base 层 ABI 绑死，
        // 拿旧一代配新一代 base 是起不来，不是少个功能。
        if ids.base_id != "base-new"
            || ids.application_id != "app-new"
            || !ids.rag_id.is_empty()
        {
            return Err(format!("current.json 没指向候选层：{ids:?}"));
        }
        let previous = crate::runtime_delivery::read_layer_ids(&runtime.join("previous.json"))
            .ok_or("提交时没有记录 previous.json")?;
        if previous.base_id != "base-old" || previous.application_id != "app-old" {
            return Err(format!("previous.json 记的不是上一个被提交的 commit：{previous:?}"));
        }

        // ④ 首装（没有已提交的前态）⇒ 闸必须被跳过，否则每台机器首启白等一次冷启动。
        let fresh_home = root.join("home-fresh");
        GATE_ALLOW.store(false, Ordering::SeqCst);
        GATE_CALLS.store(0, Ordering::SeqCst);
        let fresh = crate::runtime_delivery::deliver_with_gate(
            &pin, &fresh_home, &install, None, Some(scripted_gate), &mut quiet,
        )
        .map_err(|err| format!("首装被提交闸拦住了：{err}"))?;
        if GATE_CALLS.load(Ordering::SeqCst) != 0 {
            return Err("首装没有可回退的前态，却跑了提交闸".to_string());
        }
        if fresh.base_id != "base-new" {
            return Err(format!("首装结果不符：{fresh:?}"));
        }

        // ⑤ 回退：两档互换 ⇒ current 回到旧一代，坏的那份记进 previous.json。
        let broken_bytes = std::fs::read(runtime.join("current.json")).map_err(err_text)?;
        let (broken, used) = crate::runtime_delivery::commit_previous(&runtime)?;
        if broken.base_id != "base-new" || used.base_id != "base-old" {
            return Err(format!("回退换错了代际：{broken:?} -> {used:?}"));
        }
        let current = crate::runtime_delivery::read_layer_ids(&runtime.join("current.json"))
            .ok_or("回退后 current.json 读不出来")?;
        if current.base_id != "base-old"
            || current.application_id != "app-old"
            || current.rag_id != "rag-old"
        {
            return Err(format!("回退后 current.json 不是旧一代：{current:?}"));
        }
        if crate::runtime_delivery::read_layer_ids(&runtime.join("previous.json")).as_ref()
            != Some(&broken)
        {
            return Err("回退后 previous.json 没记下坏的那一代".to_string());
        }
        // 两档相同 ⇒ 拒绝再翻（否则会在两代之间来回翻档）。
        let current_bytes = std::fs::read(runtime.join("current.json")).map_err(err_text)?;
        write_fixture(&runtime.join("previous.json"), &current_bytes)?;
        if crate::runtime_delivery::rollback_target(&runtime).is_ok() {
            return Err("两档指向同一代时仍然允许回退".to_string());
        }
        // 复位：previous.json 重新指回坏的那一代 ⇒ ⑥ 才验得出「回退点不可被回收」。
        write_fixture(&runtime.join("previous.json"), &broken_bytes)?;

        // ⑥ 回收：只删两档都不引用的孤儿代际，且年龄护栏要先挡住刚建的目录。
        for id in ["base-orphan"] {
            write_fixture(
                &runtime.join("base").join(id).join("Demiurge-Runtime.exe"),
                b"fake-runtime",
            )?;
            write_fixture(
                &runtime.join("base").join(id).join("base-manifest.json"),
                format!("{{\"schema_version\":1,\"base_id\":\"{id}\"}}").as_bytes(),
            )?;
        }
        let (removed_too_soon, _) =
            crate::runtime_delivery::prune_old_layers(&runtime, Duration::from_secs(3600));
        if !removed_too_soon.is_empty() {
            return Err(format!("年龄护栏没生效，刚建的目录被回收了：{removed_too_soon:?}"));
        }
        let (removed, failures) = crate::runtime_delivery::prune_old_layers(&runtime, Duration::ZERO);
        if !failures.is_empty() {
            return Err(format!("回收失败：{failures:?}"));
        }
        if removed != vec!["base/base-orphan".to_string()] {
            return Err(format!("回收名单不是「只有孤儿」：{removed:?}"));
        }
        for path in [
            runtime.join("base").join("base-old"),
            runtime.join("apps").join("app-old"),
            runtime.join("rag").join("rag-old"),
            runtime.join("base").join("base-new"),
        ] {
            if !path.is_dir() {
                return Err(format!("被两档引用的层（含回退点）被回收了：{}", path.display()));
            }
        }

        // ⑦ 孤儿临时项扫描：护栏内不动，护栏外清掉；`.gate-*` 也在扫描面里。
        std::fs::create_dir_all(runtime.join(".staging-leftover")).map_err(err_text)?;
        std::fs::create_dir_all(downloads.join(".work-leftover")).map_err(err_text)?;
        std::fs::create_dir_all(runtime.join(".gate-leftover")).map_err(err_text)?;
        if let Some(err) = first_delete_failure(
            crate::runtime_delivery::sweep_temp(&runtime, &downloads, Some(Duration::from_secs(3600))),
        ) {
            return Err(format!("年龄护栏内的清理失败：{err}"));
        }
        if !runtime.join(".staging-leftover").exists() {
            return Err("年龄护栏没生效：刚建的临时目录被当成孤儿删了".to_string());
        }
        if let Some(err) = first_delete_failure(crate::runtime_delivery::sweep_temp(
            &runtime,
            &downloads,
            Some(Duration::ZERO),
        )) {
            return Err(format!("孤儿临时项清理失败：{err}"));
        }
        for path in [
            runtime.join(".staging-leftover"),
            runtime.join(".gate-leftover"),
            downloads.join(".work-leftover"),
        ] {
            if path.exists() {
                return Err(format!("孤儿临时项没被清掉：{}", path.display()));
            }
        }

        // ⑧ 安全模式「只清运行时」：三层与两档都没了，但 userdata 与下载缓存必须原样保留。
        write_fixture(&home.join("userdata").join("keep.txt"), b"keep")?;
        let failures = crate::runtime_delivery::wipe_runtime_layers(&runtime);
        if !failures.is_empty() {
            return Err(format!("清除运行时失败：{failures:?}"));
        }
        for path in [
            runtime.join("base"),
            runtime.join("apps"),
            runtime.join("rag"),
            runtime.join("current.json"),
            runtime.join("previous.json"),
        ] {
            if path.exists() {
                return Err(format!("安全模式没清干净：{}", path.display()));
            }
        }
        if !home.join("userdata").join("keep.txt").is_file() {
            return Err("安全模式误删了作品数据".to_string());
        }
        if !downloads.join("Demiurge_base_windows_v0.2.0.zip").is_file() {
            return Err("安全模式误删了下载缓存".to_string());
        }

        // ⑨ 版本漂移判定（层升级的唯一触发点）：纯本地读档，不一致才报。
        let drifted = crate::runtime_delivery::runtime_drift(&home, &pin);
        if drifted.is_some() {
            return Err(format!("干净目录里不该有漂移：{drifted:?}"));
        }
        write_fixture(
            &runtime.join("current.json"),
            b"{\"schema_version\":2,\"app_version\":\"0.1.0\",\"base_id\":\"b\",\"application_id\":\"a\",\"rag_id\":\"r\"}",
        )?;
        match crate::runtime_delivery::runtime_drift(&home, &pin) {
            Some((installed, wanted)) if installed == "0.1.0" && wanted == "0.2.0" => {}
            other => return Err(format!("漂移判定不符：{other:?}")),
        }

        // ⑩ 后台补齐也走提交闸（P3-b）：可选层不能因为「只是可选」就跳过探活——torch 的 DLL
        //    与 base 层 ABI 绑死，坏的一代的失败形状是进程起不来，不是少个功能。
        let opt_home = root.join("home-opt");
        let opt_runtime = opt_home.join("runtime");
        GATE_ALLOW.store(true, Ordering::SeqCst);
        GATE_CALLS.store(0, Ordering::SeqCst);
        // 前态：必装层首装（闸跳过），此时 rag 按诚实合同留空。
        crate::runtime_delivery::deliver_with_gate(
            &pin,
            &opt_home,
            &install,
            None,
            Some(scripted_gate),
            &mut quiet,
        )
        .map_err(|err| format!("补齐用例的必装层首装失败：{err}"))?;
        if GATE_CALLS.load(Ordering::SeqCst) != 0
            || !crate::runtime_delivery::optional_layer_missing(&opt_home)
        {
            return Err("补齐用例的前态不符：首装应跳过闸，且可选层应判定为缺席".to_string());
        }

        // 闸拒绝 ⇒ 不提交：层不留在盘上、`current.json` 的 rag_id 仍为空、账本记下拒因。
        GATE_ALLOW.store(false, Ordering::SeqCst);
        GATE_CALLS.store(0, Ordering::SeqCst);
        let rejected_opt = crate::runtime_delivery::deliver_optional(
            &pin,
            &opt_home,
            &install,
            None,
            Some(scripted_gate),
            &mut quiet,
        )
        .err()
        .ok_or("可选层交付绕过了提交闸")?;
        if GATE_CALLS.load(Ordering::SeqCst) != 1 {
            return Err(format!(
                "可选层补齐跑了 {} 次闸，应为 1 次",
                GATE_CALLS.load(Ordering::SeqCst)
            ));
        }
        if !rejected_opt.0.contains("未通过启动自检") {
            return Err(format!("可选层拒绝信息里没写拒因：{rejected_opt}"));
        }
        if opt_runtime.join("rag").join("rag-new").exists() {
            return Err("闸拒绝后新的 rag 层留在了盘上".to_string());
        }
        if crate::runtime_delivery::read_layer_ids(&opt_runtime.join("current.json"))
            .map(|ids| ids.rag_id)
            .as_deref()
            != Some("")
        {
            return Err("闸拒绝后 current.json 的 rag_id 被写上了".to_string());
        }
        let failed_ledger: serde_json::Value = serde_json::from_slice(
            &std::fs::read(opt_home.join("userdata").join("delivery.json")).map_err(err_text)?,
        )
        .map_err(err_text)?;
        if failed_ledger.get("state").and_then(|value| value.as_str()) != Some("failed")
            || !failed_ledger
                .get("error")
                .and_then(|value| value.as_str())
                .unwrap_or("")
                .contains("测试用坏层")
        {
            return Err(format!("闸拒绝后账本没记下失败：{failed_ledger}"));
        }

        // 闸放行 ⇒ 提交并翻 rag_id、把「补之前」记成回退点、账本转 done。
        GATE_ALLOW.store(true, Ordering::SeqCst);
        GATE_CALLS.store(0, Ordering::SeqCst);
        let filled = crate::runtime_delivery::deliver_optional(
            &pin,
            &opt_home,
            &install,
            None,
            Some(scripted_gate),
            &mut quiet,
        )
        .map_err(|err| format!("闸放行后补齐仍失败：{err}"))?;
        if GATE_CALLS.load(Ordering::SeqCst) != 1 || filled.reused || filled.rag_id != "rag-new" {
            return Err(format!(
                "补齐结果不符：{filled:?}，闸跑 {} 次",
                GATE_CALLS.load(Ordering::SeqCst)
            ));
        }
        if !opt_runtime
            .join("rag")
            .join("rag-new")
            .join("rag-manifest.json")
            .is_file()
        {
            return Err("闸放行后 rag 层没有落位".to_string());
        }
        if crate::runtime_delivery::optional_layer_missing(&opt_home) {
            return Err("补齐提交后仍判定可选层缺席".to_string());
        }
        if crate::runtime_delivery::read_layer_ids(&opt_runtime.join("previous.json")).as_ref()
            != Some(&crate::runtime_delivery::LayerIds {
                base_id: "base-new".to_string(),
                application_id: "app-new".to_string(),
                rag_id: String::new(),
            })
        {
            return Err("补齐时没有把「补之前」记成回退点".to_string());
        }
        let done_ledger: serde_json::Value = serde_json::from_slice(
            &std::fs::read(opt_home.join("userdata").join("delivery.json")).map_err(err_text)?,
        )
        .map_err(err_text)?;
        if done_ledger.get("state").and_then(|value| value.as_str()) != Some("done")
            || done_ledger.get("stage").and_then(|value| value.as_str()) != Some("已提交")
        {
            return Err(format!("补齐成功但账本终态不符：{done_ledger}"));
        }
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&root);
    outcome
}

fn first_temp_leftover(runtime: &Path, downloads: &Path) -> Option<String> {
    let prefixes = [".staging-", ".work-", ".gate-", ".current-"];
    for dir in [runtime, downloads] {
        let Ok(entries) = std::fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if prefixes.iter().any(|prefix| name.starts_with(prefix)) {
                return Some(entry.path().display().to_string());
            }
        }
    }
    None
}

fn first_delete_failure(failures: Vec<String>) -> Option<String> {
    failures.into_iter().next()
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
