# Demiurge Desktop Shell

只做一层薄壳：窗口、单实例、托盘、窗口位置与 Runtime 生命周期。业务、数据、鉴权与壁纸渲染仍全部由后端同源 HTTP 提供，壳不新增业务 IPC。

## 运行前提

- Windows 10/11 x64。
- .NET 8 Desktop Runtime（开发机也可用 .NET 8 SDK）。
- Microsoft Edge WebView2 Runtime（Evergreen）。
- 前端已构建：`cd frontend && npm run build`。
- 开发模式需要 `backend/.venv`；便携包模式需要包根含 `data/runtime/current.json` 与 `data/runtime/base/<base_id>/Demiurge-Runtime.exe`。

## 开发运行

```powershell
dotnet run --project desktop/Demiurge.Desktop
```

或先构建再直接运行 exe：

```powershell
dotnet build desktop/Demiurge.Desktop -c Debug
desktop\Demiurge.Desktop\bin\Debug\net8.0-windows\win-x64\Demiurge.exe
```

启动流程：

1. 解析端口：`DEMIURGE_PORT` → `LAF_RUNTIME_PORT` → 上次持久化值 → `8010`。
2. 若该端口已有同时满足「`/api/health` 返回 ok」与「`/` 返回含 `id="root"` 的前端文档」的服务，直接复用，不接管、不回收。
3. 否则从便携包 `data/runtime` 或仓库 `backend/.venv` 启动 Runtime；开发模式设置 `LAF_FRONTEND_DIST=frontend/dist`。
4. WebView2 在同源地址打开界面。
5. 关窗时按 PID/进程树回收**自己启动**的 Runtime。

端口不会静默切换：前端 `API_BASE` 默认钉在 `http://127.0.0.1:8010/api`（`frontend/src/api/client.ts`），改端口必须同时用匹配的 `VITE_API_BASE` 重新构建前端。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `DEMIURGE_PORT` / `LAF_RUNTIME_PORT` | 端口；前者优先，默认 8010 |
| `DEMIURGE_FRONTEND_DIST` | 覆盖开发模式使用的前端目录（默认 `frontend/dist`） |
| `DEMIURGE_SHELL_HOME` | 覆盖壳状态目录（默认 `%LOCALAPPDATA%\Demiurge`） |
| `DEMIURGE_WEBVIEW2_ARGS` | 追加 WebView2 启动参数，例如 `--remote-debugging-port=9333` |
| `DEMIURGE_READY_TIMEOUT_SECONDS` | 就绪等待上限，默认 180 秒，范围 5–900 |

## 状态与日志

默认位于 `%LOCALAPPDATA%\Demiurge`：

- `desktop.json`：端口与窗口位置/大小/最大化状态。
- `logs\desktop.log`：壳日志。
- `logs\runtime.log`：Runtime 标准输出/错误。
- `logs\self-test.log`：最近一次自检报告。
- `webview2\`：WebView2 用户数据目录。

`DEMIURGE_SHELL_HOME` 可整体改到其他目录（便携模式或受限环境测试）。

## 自检

```powershell
desktop\Demiurge.Desktop\bin\Debug\net8.0-windows\win-x64\Demiurge.exe --self-test
```

覆盖端口解析、设置夹取与坏文件回退、便携/仓库布局识别、Job 对象进程树回收、健康/前端文档判定；输出 `PASS/FAIL` 并以退出码表示结果，同时写 `logs\self-test.log`。

## 发布

```powershell
dotnet publish desktop/Demiurge.Desktop -c Release -r win-x64 --self-contained false -o desktop/publish
desktop\publish\Demiurge.exe --self-test
```

产物（`desktop/publish/`）：`Demiurge.exe`（单文件，含托管程序集）+ `WebView2Loader.dll` 平铺在旁 + `runtimes/win-x64/native/WebView2Loader.dll`，另有 `Demiurge.pdb`（不进用户包）。

- **原生库不自解包**：`IncludeNativeLibrariesForSelfExtract=false`。设为 `true` 时启动会往 `%TEMP%\.net\Demiurge\` 解包，
  临时目录不可写（受限策略账户、部分沙箱）即 `Failed to create directory ... for extracting bundled files`、退出码 160，界面根本不出现。
  便携包本身就是目录，平铺没有额外代价。
- 框架依赖发布：目标机需要 .NET 8 Desktop Runtime；完全离线可改用 `--self-contained true`（体积更大）。
- 目标机需要 WebView2 Runtime；后续可改为随包分发固定版本 WebView2。
- **便携包装配已完成**：`python scripts/portable_release.py build --target windows-x64-full-rag ... --desktop-dir desktop/publish`
  把上述产物平铺到包根（与 `data\` 同级），壳因此能识别 `data/runtime/current.json` 并接管 Runtime。`--desktop-dir` 必填，
  缺 `Demiurge.exe`／目录不存在／与包内既有文件冲突一律构建失败。包根同时提供 `install-shortcut.bat`（在桌面与开始菜单建 `.lnk`）。
- CI：`runtime-release.yml` 的 `desktop-shell` job 做发布 + `--self-test` + 归档 `Demiurge-desktop-windows-x64.zip`，
  `publish` 与 `portable-release.yml` 都以该固定名产物作为 `--desktop-dir` 来源。
- 浏览器入口 `start-dev.bat` 继续保留，两种启动方式并存。

构建备注：`desktop/NuGet.Config` 清空继承源并只保留 nuget.org，用于绕开本机已失效的 Visual Studio 离线回退目录。

## 已知限制

- **scene 壁纸**：工坊作品与本地 WE 工程都能实时渲染（`scene.pkg` → Worker 解码 → 隔离 iframe
  播放器，见 `frontend/src/lib/scene/`）；列表封面仍用作品自带 preview，尚未改成实时缩略图。
- 复用仅支持「健康且提供前端」的同端口服务；仅提供 API 的开发后端会明确报错而不是静默换端口。
- 仅 Windows x64。

## 明确不做（不是待办）

以下五条**不进待办表**——列在这里是为了不再被反复提起。要重开，先改本节。

- **安装器（NSIS/MSI）**：便携目录 + `install-shortcut` 已覆盖「装一下」的需求；引入安装器就要
  连带写升级/卸载/回滚/静默参数，成本远超收益。
- **自动更新**：壳只是「打开本机正在跑的服务」，版本由代码与 Runtime 包决定；壳内再加更新器
  只会造出第二条版本真源。
- **代码签名**：需要证书与长期密钥托管，而当前分发范围是自用与内测。
- **随包分发固定版 WebView2**：体积 +150 MB 起，而 Evergreen Runtime 已是 Win10/11 的预装面。
- **macOS/Linux 壳**：先有对应 Runtime 包与 CI 矩阵，再谈壳；现在写壳等于写一份没人能验的代码。

## 唯一剩余待办（本机做不了）

打 tag 时端到端跑一次 `.github/workflows/runtime-release.yml` + `portable-release.yml`，
确认 `portable_release.py --desktop-dir` 这条链在 CI（干净 runner、无本机 NuGet 缓存）里也成立：

1. `desktop-shell` job 的 `dotnet publish` + `--self-test` 通过，并产出固定名
   `Demiurge-desktop-windows-x64.zip`；
2. `publish` 与 `portable-release` 两个下游都取到该产物；
3. 产出的包解开后，包根 `Demiurge.exe --self-test` 15/15。

本机只能验到「发布 → 装配 → 自检」这三步（见下验证记录）；runner 差异只能由一次真实 tag 收口。

## 迁移到 Tauri 2 的入口条件（备忘，不是当前任务）

**前置不是代码，是工具链**：本机没有 VS Build Tools 与 Rust，两者不装，`cargo tauri` 一步都走不了。
迁移的第一件事是装完并跑通一个空窗口，不是先写壳。

真正的迁移物只有一份合同：**同源 `http://127.0.0.1:8010` + 无业务 IPC**。WPF 壳已经把这份合同
压得很薄（窗口、单实例、托盘、窗口位置、运行时生命周期），所以 Tauri 是换壳不是重写。要搬的清单：

端口解析 · health/前端文档探测 · 复用已在跑的服务 · 单实例 · 窗口状态 · 托盘 ·
`std::process::Command` 拉起运行时 · Job Object + taskkill 兜底 · WebView2 data directory ·
就绪超时 · 日志尾部。

**验收标准**：直接用现成的 `--self-test` 那 15 项，两套壳跑同一份，谁的输出不一样就是谁没对齐。
这比写迁移文档有用。

**已实测（2026-10-23，空窗口跑通）**：`tauri 2.12.5` + MSVC 14.44.35207 + WebView2 155.0.4283.62，
探针工程见 `_tmp/tauri-smoke/`（gitignored，不入库）。结果：窗口出现、标题 `TAURI_SMOKE_OK`、
WebView2 子进程 12 个。但踩到一个**必做项**：

🔴 **必须显式指定 WebView2 数据目录**。不指定时默认目录（exe 同目录 `<exe>.WebView2` 或
`%LOCALAPPDATA%\<identifier>`）创建失败，错成 `Failed to setup app: 拒绝访问。 (os error 5)`
（底层 `Io(Os { code: 5, PermissionDenied })`），窗口根本不出现；换成
`WebviewWindowBuilder::data_directory(<可写目录>)` 立刻正常。⇒ 迁移清单里的
「WebView2 data directory」不是可选项；WPF 壳那边已有的 data directory 口径要原样带过去。

## Tauri 2 壳（`desktop/tauri/`）：第一刀已落地

WPF 壳的换壳实现，合同不变（同源 8010 + 无业务 IPC）。当前状态：端口解析、健康/前端文档探测、
复用已在跑的服务、拉起运行时（便携包 / 仓库两种）、就绪超时、Job Object + taskkill 回收、
窗口状态持久化、单实例（基础版）、15 项 `--self-test` 全部对齐 WPF 判据。

未做：托盘 + 「关窗 = 隐藏到托盘」、激活既有窗口、并入 `portable_release.py --desktop-dir`。

```
cd desktop/tauri && cargo build
target\debug\demiurge-shell.exe --self-test    # 15 项，与 WPF 同一份
```

细节见 `desktop/tauri/README.md`。

## 待决：关窗语义（迁移时一并定死）

现状：关窗会回收自己拉起的运行时（见「验证记录 · 第一刀」）。手机端要连就必须常驻 ⇒
Tauri 那次把「**关窗 = 隐藏到托盘**」定死，不要两边各写一套。

## 验证记录（2026-10-23）

第一刀（壳本体）：

- `--self-test`：15 项全部通过。
- 真机：壳拉起开发 Runtime 约 7 秒就绪，WebView2 页面标题/正文/API 正常；第二实例 344 ms 退出；关闭主窗口 4 秒内回收 venv 重定向器与实际解释器两层进程并释放端口；复用外部 Runtime 时退出不误杀；仅 API 的 8010 开发后端被拒绝并显示可操作错误。

第二刀（装进便携包）：

- 真实 `dotnet publish` + 真实 `portable_release.py --desktop-dir` 产出 7z，解包后包根 `Demiurge.exe --self-test` 15/15 通过。
- 解包后的壳连「健康且含前端文档」的服务并经 CDP 读到页面 target，确认包内壳真的渲染界面。
- 关闭主窗口：壳退出码 0，被复用的外部服务进程存活。
- `install-shortcut.ps1` 生成的 `.lnk` 回读 TargetPath/WorkingDirectory/IconLocation 均正确。

