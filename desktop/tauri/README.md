# Demiurge 桌面壳 · Tauri 2 版

WPF 壳（`../Demiurge.Desktop`）的换壳实现。**合同不变**：

> 同源 `http://127.0.0.1:8010` + 无业务 IPC。

壳只干四件事：解析端口、确认服务可用（健康 + 前端文档）、必要时拉起运行时并等就绪、把窗口指过去。
业务全在后端与前端，壳不掺和。

## 状态（2026-10-23 第一刀）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 端口解析 | ✅ | `DEMIURGE_PORT` > `LAF_RUNTIME_PORT` > `desktop.json` 的 `Port` > 8010 |
| 健康探测 | ✅ | `GET /api/health` 判 `{"status":"ok"}`（大小写不敏感） |
| 前端文档探测 | ✅ | `GET /` 必须含 `id="root"`；只有 API 的后端会明确报错而不是静默换端口 |
| 复用已在跑的服务 | ✅ | 端口已就绪就直接用，不重复拉第二个 |
| 拉起运行时 | ✅ | 便携包（`data/runtime/current.json` → `base/<base_id>/Demiurge-Runtime.exe`）与仓库（`backend/.venv` + uvicorn）两种 |
| 就绪超时 | ✅ | 默认 180s，`DEMIURGE_READY_TIMEOUT_SECONDS` 可改（5~900） |
| Job Object + taskkill | ✅ | `KILL_ON_JOB_CLOSE` 兜整棵进程树；退出先 `taskkill /T` 再 `/F` 再 kill |
| 日志 | ✅ | `logs/desktop.log`（壳）+ `logs/runtime.log`（运行时 stdout/stderr），失败时附最近 25 行 |
| 窗口状态 | ✅ | `desktop.json`（PascalCase，与 WPF 双向兼容）+ 夹到虚拟屏幕内 |
| 启动页 | ✅ | **先开窗再拉服务**：`ui/boot.html` 一行一项报进度（检查服务 → 复用/启动后端 → 等健康 → 图像服务 → 打开界面）；失败停在窗口里给原因 |
| 根目录入口 | ✅ | 编译产物拷成仓库根 `Demiurge.exe` 即可双击启动，替代 `start-dev` / `stop-dev` |
| 单实例 + 激活既有窗口 | ✅ | 互斥量判唯一 + 命名事件 `Local\Demiurge.Desktop.Activate`；第二实例 SetEvent 后退出，第一个把窗口 show+置前 |
| WebView2 数据目录 | ✅ 必做 | 见下：默认目录在本机建不出来 |
| `--self-test` 15 项 | ✅ | 与 WPF 同一份判据、同一串名字，输出可逐行比对 |
| 托盘 + 关窗隐藏 | ✅ | 托盘左键唤出、菜单「显示主窗口 / 退出」；**关窗 = 隐藏到托盘**，运行时继续跑（手机端可连），真退出只走托盘菜单 |
| 打包装配 | ✅ | `portable_release.py --desktop-dir` 同时认 WPF `Demiurge.exe` 与 Tauri `demiurge-shell.exe`，包内一律落名 `Demiurge.exe` |

## 🔴 WebView2 数据目录必须显式指定

本机实测（2026-10-23）：不指定时默认目录（exe 同目录 `<exe>.WebView2` / `%LOCALAPPDATA%\<identifier>`）
创建失败，错成 `Failed to setup app: 拒绝访问。 (os error 5)`，窗口根本不出现。所以这里一律
`.data_directory(<home>/webview2)`。诊断手法与完整证据见 `../README.md` 的 Tauri 小节。

## 构建与运行

```powershell
# 依赖：Rust stable + MSVC（VS Build Tools 的 VCTools 工作负载）
cd desktop/tauri
cargo build                 # 调试版带控制台，方便看输出
cargo build --release

# 自检（15 项，与 WPF 壳同一份判据）
.\target\debug\demiurge-shell.exe --self-test

# 正常运行（在仓库内会自动用 backend\.venv 起开发运行时）
.\target\debug\demiurge-shell.exe

# 想双击就启动：把 release 产物拷到仓库根并改名（.gitignore 已忽略该 exe）
copy desktop\tauri\target\release\demiurge-shell.exe Demiurge.exe
```

本机网络备忘：cargo 直连 crates.io 会被系统代理拦，需
`NO_PROXY='*' no_proxy='*'` + crates-io 走 rsproxy 稀疏索引（已写进用户级 `~/.cargo/config.toml`）。

## 关窗语义（已定案）

**关窗 = 隐藏到托盘**，运行时**不回收**（手机端要连就必须常驻）；真正退出只走托盘菜单「退出」，
那条路径会先 `stop()` 运行时再 `app.exit(0)`。

启动失败时例外：窗口停在错误页，关窗即退出（没有需要保活的东西）。

WPF 壳保持旧行为（关窗即退出并回收），不再两边各改一套——决策只在新壳落地。

实跑证据（2026-10-23，release 版）：
`CloseMainWindow()` 返回 true → 4 秒后进程 **alive=True** 且 `MainWindowTitle=''`（窗口已隐藏）；
再起第二个实例 → 它 **立即退出（exit=0）**，第一个窗口标题回到 `Demiurge`（被唤出）。

## 与 WPF 壳对拍

```powershell
# 两套壳各跑一次，逐行 diff —— 谁的输出不一样就是谁没对齐
desktop\Demiurge.Desktop\bin\Release\net8.0-windows\win-x64\Demiurge.exe --self-test
desktop\tauri\target\release\demiurge-shell.exe --self-test
```

`--self-test` 的 stdout 只有 `PASS/FAIL <名字>` + `self-test: N passed, M failed`；
诊断信息（home 与工作目录）走 stderr，不参与比对。

**2026-10-23 实跑结果：两套壳输出逐行一致**（各 16 行，15 PASS + summary，`diff` 为空）。
比对时把 `DEMIURGE_SHELL_HOME` 指到同一个可写目录，否则两边会各读各的 `desktop.json`。

## 代码地图

- `src/paths.rs` — 本地数据布局、便携包/仓库识别（对应 WPF `ShellPaths.cs`）
- `src/settings.rs` — 窗口几何与端口持久化（对应 `ShellSettings.cs`）
- `src/runtime_host.rs` — 探活/复用/拉起/等就绪/回收（对应 `RuntimeHost.cs`）
- `src/job.rs` — Job 对象回收进程树（对应 `ChildProcessJob.cs`）
- `src/http_probe.rs` — 裸 TCP 的最小 HTTP GET（**不走代理**，对应 WPF `UseProxy=false`）
- `src/selftest.rs` — 15 项自检（对应 `SelfTest.cs`）
- `src/shell_log.rs` — 壳日志与日志尾巴（对应 `ShellLog.cs`）
