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

## 明确不做（含一条已反转）

以下条目**默认不进待办表**；已反转的那条单独标注，避免再被当成「不做」。

- **安装器（NSIS/MSI）**：**2026-10-24 决策反转，已重开**。终端用户需要「双击 → 选安装路径 → 正常安装」。
  定案：同一份 Tauri 代码出**两个安装器**——`currentUser`（默认，不弹 UAC）与 `perMachine`（可选，需 UAC），
  不用 Tauri 的 `installMode: both`（它一律提权，且提权后的 `%LOCALAPPDATA%` 可能落到管理员账户）。
  安装版布局与依赖 PATH 注入已在 Tauri 壳落地（见下方 Tauri 小节）；NSIS 双安装器已交付（2026-10-24 第二步），
  运行时按需下载/离线导入（第三步）与 CI 切换（第四步）也已落地，见「安装器路线」。Authenticode 代码签名
  等客户端版完整落地后再做。
- **自动更新**：**2026-10-24 按用户要求落地骨架**（`tauri-plugin-updater` + 托盘「检查更新…」+ `latest.json`
  生成器 + 一次性测试密钥）。版本真源仍是发布 tag 与运行时清单，更新器只搬运安装器；当前**只对 currentUser
  安装版开放**，便携包、仓库开发壳与 perMachine 安装明确拒绝。生产公钥轮换与真实下载/安装端到端回放待
  Release 资产就绪后做，私钥不进仓库。
- **代码签名**：需要证书与长期密钥托管，而当前分发范围是自用与内测（**推迟到客户端版完整落地后**）。
- **随包分发固定版 WebView2**：体积 +150 MB 起，而 Evergreen Runtime 已是 Win10/11 的预装面。
- **macOS/Linux 壳**：先有对应 Runtime 包与 CI 矩阵，再谈壳；现在写壳等于写一份没人能验的代码。

## 唯一剩余待办（本机做不了）

打 tag 时端到端跑一次 `.github/workflows/runtime-release.yml` + `portable-release.yml`，
确认整条发布链在 CI（干净 runner、无本机 NSIS/Rust 缓存）里也成立：

1. `desktop-shell` job 的 Tauri 构建 → 两个 NSIS 安装器 → 自检 → 固定名
   `Demiurge-desktop-windows-x64.zip` 全部通过；
2. `publish` 与 `portable-release` 两个下游都取到该产物，安装器与 `SHA256SUMS.txt` 成为 Release 资产；
3. `portable-release.yml` cleanup 没有误删 Windows `base/application/rag/update` 分层资产
   （它们是安装版在线交付的唯一来源）；
4. 产出的包解开后，包根 `Demiurge.exe --self-test` 18/18。

本机只能验到「构建 → 装配 → 自检」这条链（见下验证记录）；runner 差异只能由一次真实 tag 收口。

## 迁移到 Tauri 2 的入口条件（历史备忘，已落地）

**前置不是代码，是工具链**：本机没有 VS Build Tools 与 Rust，两者不装，`cargo tauri` 一步都走不了。
迁移的第一件事是装完并跑通一个空窗口，不是先写壳。

真正的迁移物只有一份合同：**同源 `http://127.0.0.1:8010` + 无业务 IPC**。WPF 壳已经把这份合同
压得很薄（窗口、单实例、托盘、窗口位置、运行时生命周期），所以 Tauri 是换壳不是重写。要搬的清单：

端口解析 · health/前端文档探测 · 复用已在跑的服务 · 单实例 · 窗口状态 · 托盘 ·
`std::process::Command` 拉起运行时 · Job Object + taskkill 兜底 · WebView2 data directory ·
就绪超时 · 日志尾部。

**验收标准（当时）**：直接用现成的 `--self-test` 前 15 项，两套壳跑同一份，谁的输出不一样就是谁没对齐。
这比写迁移文档有用。

**已实测（2026-10-23，空窗口跑通）**：`tauri 2.12.5` + MSVC 14.44.35207 + WebView2 155.0.4283.62，
探针工程见 `_tmp/tauri-smoke/`（gitignored，不入库）。结果：窗口出现、标题 `TAURI_SMOKE_OK`、
WebView2 子进程 12 个。但踩到一个**必做项**：

🔴 **必须显式指定 WebView2 数据目录**。不指定时默认目录（exe 同目录 `<exe>.WebView2` 或
`%LOCALAPPDATA%\<identifier>`）创建失败，错成 `Failed to setup app: 拒绝访问。 (os error 5)`
（底层 `Io(Os { code: 5, PermissionDenied })`），窗口根本不出现；换成
`WebviewWindowBuilder::data_directory(<可写目录>)` 立刻正常。⇒ 迁移清单里的
「WebView2 data directory」不是可选项；WPF 壳那边已有的 data directory 口径要原样带过去。

## Tauri 2 壳（`desktop/tauri/`）：已落地（2026-10-24 更新）

WPF 壳的换壳实现，合同不变（同源 8010 + 无业务 IPC）。当前状态：端口解析、健康/前端文档探测、
复用已在跑的服务、拉起运行时（便携包 / 仓库 / 安装版三种）、就绪超时、Job Object + taskkill 回收、
窗口状态持久化、单实例（基础版）、18 项 `--self-test`（前 15 项对齐 WPF 判据，第 16 项为安装版布局，
第 17 项为运行时交付，第 18 项为自动更新验签）。

安装版（2026-10-24 第一步）：程序与 `dependencies\git` 留在安装目录，`runtime/`、`userdata/`、`runtime.pid`、
日志、`desktop.json`、`webview2/` 全部在 `%LOCALAPPDATA%\Demiurge`；识别顺序＝便携包 → 仓库 → 安装版。
拉起运行时前把 `dependencies\git\cmd`（MinGit）与可选的 `dependencies\ffmpeg\bin` 前置进子进程 `PATH`，
修掉「便携包带了 MinGit 却没人挂上去、干净机器上 git 能力失效」的缺口。

运行时交付（2026-10-24 第三步）：安装版首次启动发现 `runtime/current.json` 缺层时，按壳内嵌的
`release/runtime-delivery.json` pin 补齐 `base+application`——在线走 `ureq` + 显式挂载的系统 TLS（schannel）+ Range 续传，
离线认安装目录 `runtime-pack\` 与 `%LOCALAPPDATA%\Demiurge\downloads\` 两个入口；`rag` 仅
`--install-runtime --with-rag` 显式追加。解压到 `.staging-<token>` 再改名部署，`current.json` 最后原子写，
失败不留半套状态；进度走启动页文案，不新增业务 IPC。细节见 `desktop/tauri/README.md`。

已含：托盘（左键唤出 / 菜单「显示主窗口 · 重启后端 · 检查更新… · 彻底关闭」）、关窗三选一（隐藏 / 彻底关闭 / 取消）、
托盘重启后端（只处理自启运行时，外部复用明确拒绝）、第二实例激活既有窗口、
`portable_release.py --desktop-dir` 同时接受两套壳产物（包内统一落名 `Demiurge.exe`）。

启动体验（2026-10-23 第三刀）：**先开窗、再拉服务**。双击后立刻看到窗口逐项报进度
（检查服务 → 复用/启动后端 → 等健康 → 图像服务 → 打开界面）；失败就停在窗口里写原因，
不再出现「双击没反应」。产物拷成仓库根 `Demiurge.exe` 即可双击启动，替代 `start-dev` / `stop-dev`。

```
cd desktop/tauri && cargo build
target\debug\demiurge-shell.exe --self-test    # 18 项：前 15 项与 WPF 同一份判据
```

细节见 `desktop/tauri/README.md`。

## 安装器路线（2026-10-24 定案，进行中）

目标：双击 → 选安装路径 → 正常安装；依赖（不含本地模型）随包或按需获取，目标机不需要 Python/Node。

1. ✅ **安装版布局**（第一步，已落）：程序与 MinGit 留安装目录；`runtime/`、`userdata/`、`runtime.pid`、日志、
   `desktop.json`、`webview2/` 全在 `%LOCALAPPDATA%\Demiurge`；依赖前置进运行时 `PATH`；自检 16/16（当时；现为 18 项）。
2. ✅ **NSIS 双安装器**（第二步，已落）：`currentUser`（默认，不弹 UAC）+ `perMachine`（可选，需 UAC），用
   `tauri build --config` 覆盖同一份代码；WebView2 用 `embedBootstrapper`；语言 `SimpChinese`；自定义安装路径页。
   两个安装器各 29.6 MiB（含更新签名），装了 → 包内 `--self-test` 18/18 → 静默卸载清掉安装目录/开始菜单/「添加删除程序」登记项；
   预设/userdata/`backend/data`/`.env` 由三层机械守卫拦在安装资源之外。
3. ✅ **运行时按需交付**（第三步，已落）：安装器只带壳 + MinGit；首次启动按嵌入 pin（`release/runtime-delivery.json`）
   补齐 `base+application`，在线下载与离线双入口（安装目录 `runtime-pack\`、`%LOCALAPPDATA%\Demiurge\downloads\`）并存；
   `rag` 仅 `--install-runtime --with-rag` 显式追加。层体积/哈希只认 `Demiurge_update_<平台>_<tag>.json`，pin 不复制；
   失败不写 `current.json`；自检第 17 项用纯 `file://` 夹具覆盖组装/复用/离线/坏档/磁盘不足/清单漂移。
4. ✅ **CI 切换**（第四步，已落）：`desktop-shell` job 从 `dotnet publish` 切到 Tauri（Rust stable + Node 22 +
   Python 3.13.11），先渲染 pin，再装配依赖 → `build_installers.py --skip-stage --version <tag>` → 自检 →
   归档 `Demiurge-desktop-windows-x64.zip`，并上传两个安装器与 `SHA256SUMS.txt`。
   `portable-release.yml` cleanup 收窄为只删 mac/linux 分层中间件与桌面壳裸包 `Demiurge-desktop-windows-x64.zip`，
   保留 Windows 分层资产供在线交付；重跑该 workflow 前需重新上传裸包。
5. ✅ **自动更新骨架**（第五步，已落）：`tauri-plugin-updater` + 托盘「检查更新…」→ 下载 → minisign 验签 →
   停自启运行时 → NSIS `passive` 安装；`scripts/updater_manifest.py` 生成 `latest.json` 并守卫签名版本绑定/HTTPS/非
   `-Machine`；CI 预留签名 secrets。当前用一次性测试密钥，**生产公钥轮换与真实下载安装回放未做**。
6. ⏳ **Authenticode 代码签名**：需要证书与长期密钥托管，按用户定案推迟到客户端版完整落地。

## 关窗语义：已定案（2026-10-24）

新壳（`desktop/tauri/`）关窗时弹三选一：**是 = 隐藏到托盘**（运行时继续跑，手机端可连）；
**否 = 彻底关闭**（先回收自己拉起的运行时，再退出壳，前端窗口一并消失）；**取消 = 留在窗口**。
启动失败时例外：关窗即退出。

托盘菜单＝显示主窗口 / 重启后端 / 检查更新… / 彻底关闭。重启与退出都只处理**自启**运行时；端口上外部复用的
健康服务永不回收，重启项会弹窗拒绝而不是误杀。

WPF 壳保持旧行为（关窗即退出并回收），决策只在新壳落地。
实跑证据（2026-10-24）：三种关窗选择均按上述语义生效；托盘重启后新旧后端 PID 不同、页面整页重载；
外部复用时重启被拒绝且服务存活（壳退出后仍在）；彻底关闭后后端进程树消失、8010 释放；release 产物 `--self-test` 当时 15/15（现 18/18）。

## 验证记录（2026-10-23）

第一刀（壳本体）：

- `--self-test`：15 项全部通过。
- 真机：壳拉起开发 Runtime 约 7 秒就绪，WebView2 页面标题/正文/API 正常；第二实例 344 ms 退出；关闭主窗口 4 秒内回收 venv 重定向器与实际解释器两层进程并释放端口；复用外部 Runtime 时退出不误杀；仅 API 的 8010 开发后端被拒绝并显示可操作错误。

第二刀（装进便携包）：

- 真实 `dotnet publish` + 真实 `portable_release.py --desktop-dir` 产出 7z，解包后包根 `Demiurge.exe --self-test` 15/15 通过。
- 解包后的壳连「健康且含前端文档」的服务并经 CDP 读到页面 target，确认包内壳真的渲染界面。
- 关闭主窗口：壳退出码 0，被复用的外部服务进程存活。
- `install-shortcut.ps1` 生成的 `.lnk` 回读 TargetPath/WorkingDirectory/IconLocation 均正确。

第三刀（Tauri 安装版布局，2026-10-24）：

- `cargo build` 与 `cargo build --release` 均通过；隔离 `DEMIURGE_SHELL_HOME` 下 debug / release `--self-test` 都是 **16/16**（当时；现为 18 项）。
- 与 WPF 壳对拍：前 15 行文本一致；比较时需归一换行（WPF CRLF / Tauri LF），否则裸 `diff` 会全行不同。
- 第 16 项覆盖：安装目录只放程序、`runtime`/`userdata`/`runtime.pid` 全在 home、运行时 exe 从 `runtime/base/<base_id>` 解析、
  MinGit 与可选 ffmpeg 的依赖目录被正确收集并去重。
- **未验证**：真正安装到 `Program Files` 后启动（要等 NSIS 安装器落地）；运行时按需下载 / 离线导入同样在后续步骤。
  第四刀已补上「装到目录 → 包内自检」，但 `Program Files`（只读 + 需 UAC）本身仍未实测。

第四刀（NSIS 双安装器，2026-10-24）：

- 一套代码出两个安装器：`tauri build --bundles nsis`（currentUser）与 `--config installer/per-machine.conf.json`
  （perMachine）；产物 `release-output/installers/Demiurge-Setup-{User,Machine}-0.1.0.exe` 各 **28.4 MiB**，
  SHA256 记在同目录 `SHA256SUMS.txt`。
- 生成的 `installer.nsi` 核对：currentUser 分支 `RequestExecutionLevel user`、perMachine 分支 `admin`，
  两者都含 `!insertmacro MUI_PAGE_DIRECTORY`（自定义安装路径页）。
- 安装内容：`Demiurge.exe`（`mainBinaryName` 顶层配置，产物不再叫 `demiurge-shell.exe`）+
  `dependencies\git\cmd\git.exe`（MinGit 2.55.0.3，SHA256 与 `release/portable-windows.json` 的 pin 一致）+
  `dependencies\MANIFEST.json` + VC 运行库 + `uninstall.exe`；打包的 git 实测 `git version 2.55.0.windows.3`。
- **预设不泄露（用户硬要求）三层守卫实测**：安装包 385 条目、真实安装树 378 文件， forbidden 命中 **0**；
  仓库里的 `presets\GrayWill-*.json` 从未进过暂存树。守卫脚本对注入的 `presets/`、`userdata/`、
  `backend/data/`、`.env` 一律抛错，回归测试 12 项覆盖（`backend/tests/test_installer_release.py`；现 23 项）。
- 真机静默装 → 隔离 `DEMIURGE_SHELL_HOME` 下包内 `Demiurge.exe --self-test` **16/16**（当时；现 18/18）→ 静默卸载，
  `uninstall_residue` 核验三处全清：安装目录、开始菜单 `Demiurge` 与「添加/删除程序」登记项 `HKCU\...\Uninstall\Demiurge`；
  `HKCU\Software\Demiurge\Demiurge` 里那条「上次安装路径」默认值按 Tauri 模板**只在勾选「删除应用数据」时**
  （`${DeleteAppDataCheckboxState}=1` 分支）才清，`/S` 静默卸载有意留着给重装恢复路径，它不含用户数据。
- WebView2 选 `embedBootstrapper`：`offlineInstaller` 时安装包 234.2 MiB，实测 305.0 MiB 载荷里
  `MicrosoftEdgeWebView2RuntimeInstaller.exe` 独占 204.9 MiB；换引导器后回到 28.4 MiB，并把体积口径写成守卫。
- **未验证**（当时）：perMachine 只核对了生成的 NSI（提权安装会写 `Program Files`，本机不做破坏性实测）；
  运行时包（第三步）仍未交付，干净机器装完壳在 `%LOCALAPPDATA%\Demiurge\runtime\current.json` 缺失时会明确报错。
  **2026-10-24 第六刀已补 perMachine 提权实测（自定义可写安装目录 + HKLM/机器级开始菜单），见下。**

第五刀（运行时按需交付 + CI 切换，2026-10-24）：

- `release/runtime-delivery.json`（pin，schema 1）+ `scripts/runtime_delivery.py pin|validate`，层的体积与
  SHA256 仍只由 `Demiurge_update_<平台>_<tag>.json` 提供；`backend/tests/test_installer_release.py` 增 4 条
  `runtime_delivery_*` 回归（pin 与发布合同一致、非法 tag/repository 被拒、清单漂移被拒、交付资产不得混入安装暂存树）。
- `desktop/tauri/src/runtime_delivery.rs`（约 700 行）：pin/清单校验、`file://` 与 HTTPS 续传、分卷组装、
  安全解压（拒绝绝对路径与 `..`、剥单根）、原子部署 + 回滚、磁盘预检、`current.json` 最后写。
- 自检第 17 项 `delivery: runtime pack install` 用纯 `file://` 夹具实测通过：两分卷 base + 单档 application 组装；
  二次运行复用不重复下载；`release_base` 不可达时从 `runtime-pack` 离线装配；**同字节数**损坏分卷被哈希拒绝且
  不写 `current.json`；`free_bytes_override` 触发磁盘不足；清单名漂移被拒；URL 推导正确且无 `.staging-*` 残留。
  debug / release 均 **17/17**。
- 版本覆盖路径实测：`build_installers.py --skip-stage --skip-machine --skip-smoke --version v0.15` →
  `Demiurge-Setup-User-0.15.0.exe`（28.6 MiB，385 条目，`RequestExecutionLevel=user`），随后 `Demiurge.exe --self-test` 17/17。
- 全量复核（`build_installers.py --skip-stage`）：两个安装器各 **28.6 MiB**、385 条目干净，安装树 `--self-test` **17/17**，
  静默卸载后安装目录/开始菜单/登记项三处全清。
- 门禁：`test_installer_release.py` 17/17；`ruff check` 干净；后端全量 **4319/0F/0E/0S**（junitxml）。
- CI：`runtime-release.yml` 的 `desktop-shell` 已切 Tauri + 安装器 + 自检 + 固定名 zip；`portable-release.yml` cleanup
  收窄为只删 mac/linux 分层中间件（保留 Windows 分层资产供在线交付）。
- **未验证**：真实 HTTPS 在线下载（要等 Release 资产存在）、tag 端到端发布、perMachine 提权安装。

第六刀（自动更新骨架 + perMachine 提权实测，2026-10-24）：

- **自动更新**：`tauri-plugin-updater` 接入；托盘「检查更新…」→ 确认 → 下载 → minisign 验签 → 停自启运行时 →
  NSIS `passive` 安装 → 新壳重启后重拉运行时。**只对 currentUser 安装版开放**；便携包、仓库开发壳与
  **安装目录不可写**的 perMachine 安装明确拒绝并提示手动下载（perMachine 若刻意装到用户可写目录，
  壳无法仅凭可写性区分，属已知边界）。配置守卫：`requireSignedVersion: true`、禁降级/不安全传输、endpoint 必须 HTTPS。
- **NSIS 更新载荷＝安装器 exe**：Tauri 2 产出 `<installer>.exe.sig`（不是 `.nsis.zip`），`latest.json` 的
  `signature` 取 `.sig` 原文。`scripts/updater_manifest.py` 拒绝签名版本漂移、HTTP 端点、非 `.exe` 与 `-Machine`。
- 实测（一次性测试密钥；私钥在 `%LOCALAPPDATA%\Demiurge\updater-test.key`，不进仓库；公钥
  `desktop/tauri/testdata/updater-test.pub`）：两套安装器各 29.6 MiB、385 条目；独立 Ed25519/blake2b 校验器对
  两个 `.exe.sig` 验签通过、绑定版本 0.1.0；`latest.json` 由真实产物生成、URL 指向 `Demiurge-Setup-User-0.1.0.exe`；
  包内自检 **18/18**（含 `updater: signature verify`）。
- **perMachine 提权实测**：以管理员权限静默安装到自定义目录（`/S /D=...`），`HKLM\...\Uninstall\Demiurge`
  与 `ProgramData\...\Programs\Demiurge` 均出现；包内自检 18/18；静默卸载后安装目录/HKLM/开始菜单三处清零。
  仍未在 `Program Files` 做破坏性写入实测。
- 门禁：`test_installer_release.py` **23/23**、`cargo test --release` 6/6、`ruff check` 干净。
- **未验证**：生产密钥轮换、真实下载与安装的端到端回放、tag/Release 端到端（本轮按定案全部推迟）。
