# Demiurge 桌面壳 · Tauri 2 版

WPF 壳（`../Demiurge.Desktop`）的换壳实现。**合同不变**：

> 同源 `http://127.0.0.1:8010` + 无业务 IPC。

壳只干四件事：解析端口、确认服务可用（健康 + 前端文档）、必要时拉起运行时并等就绪、把窗口指过去。
业务全在后端与前端，壳不掺和。

## 状态（更新至 2026-11-03）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 端口解析 | ✅ | `DEMIURGE_PORT` > `LAF_RUNTIME_PORT` > `desktop.json` 的 `Port` > 8010 |
| 健康探测 | ✅ | `GET /api/health` 判 `{"status":"ok"}`（大小写不敏感） |
| 前端文档探测 | ✅ | `GET /` 必须含 `id="root"`；只有 API 的后端会明确报错而不是静默换端口 |
| 复用已在跑的服务 | ✅ | 端口已就绪就直接用，不重复拉第二个 |
| 拉起运行时 | ✅ | 便携包（`data/runtime/current.json` → `base/<base_id>/Demiurge-Runtime.exe`）、仓库（`backend/.venv` + uvicorn）、安装版（`%LOCALAPPDATA%\Demiurge\runtime`）三种 |
| 安装版布局 | ✅ | 程序在安装目录（可能只读）；`runtime/`、`userdata/`、`runtime.pid`、日志、`desktop.json` 全在 `%LOCALAPPDATA%\Demiurge` |
| 依赖 PATH 注入 | ✅ | `dependencies/git/cmd`（MinGit）与可选的 `dependencies/ffmpeg/bin` 前置进运行时子进程 `PATH` |
| 就绪超时 | ✅ | 默认 180s，`DEMIURGE_READY_TIMEOUT_SECONDS` 可改（5~900） |
| Job Object + taskkill | ✅ | `KILL_ON_JOB_CLOSE` 兜整棵进程树；退出先 `taskkill /T` 再 `/F` 再 kill |
| 日志 | ✅ | `logs/desktop.log`（壳）+ `logs/runtime.log`（运行时 stdout/stderr），失败时附最近 25 行 |
| 窗口状态 | ✅ | `desktop.json`（PascalCase，与 WPF 双向兼容）：逻辑像素几何 + 最大化/全屏 + 夹到虚拟屏幕内；移动/缩放防抖落盘 |
| 启动页 | ✅ | **先开窗再拉服务**：`ui/boot.html` 一行一项报进度（检查服务 → 复用/启动后端 → 等健康 → 图像服务 → 打开界面）；失败停在窗口里给原因 |
| 根目录入口 | ✅ | 编译产物拷成仓库根 `Demiurge.exe` 即可双击启动，替代 `start-dev` / `stop-dev` |
| 单实例 + 激活既有窗口 | ✅ | 互斥量判唯一 + 命名事件 `Local\Demiurge.Desktop.Activate`；第二实例 SetEvent 后退出，第一个把窗口 show+置前 |
| WebView2 数据目录 | ✅ 必做 | 见下：默认目录在本机建不出来 |
| `--self-test` 19 项 | ✅ | 前 15 项与 WPF 同一份判据、同一串名字；第 16 项是 Tauri 独有的安装版布局，第 17 项是运行时交付，第 18 项是自动更新验签，第 19 项是交付提交闸与回滚 |
| 运行时按需交付 | ✅ | 首次启动发现安装版缺层 → 按嵌入 pin 在线下载或从 `runtime-pack`/`downloads` 离线装配；必装层只有 `base+application`，`rag` 是**可选层**（2026-10-30 曾定案三层必装，**2026-11-03 P3-a 推翻**）；可选层后台补齐与进度可见 **2026-11-03 P3-b 已落** |
| 交付 CLI | ✅ | `Demiurge.exe --install-runtime`；便携布局下拒绝执行（退出码 2） |
| 托盘 + 关窗询问 | ✅ | 托盘左键唤出，菜单「显示主窗口 / 重启后端 / 补齐 RAG / 检查更新… / 彻底关闭」；关窗时询问「隐藏到托盘 / 彻底关闭 / 取消」，不再直接隐藏 |
| 重启后端 | ✅ | 托盘菜单项：回收**自启**运行时 → 重新拉起 → 等健康 → 整页重载；端口上外部复用的服务明确拒绝，不误杀 |
| 端口占用清理 | ✅ | 启动时端口被占（含「健康但没有前端」的旧后端）→ 弹窗写明占用进程名 / PID / 完整路径，用户点「是」才 `taskkill /T /F` 并等端口释放后继续启动；拒绝、无法识别 PID 或杀不掉都停在失败页。安装版没有 `stop-dev.bat` 时的自救出口 |
| 自动更新骨架 | ✅ 测试密钥 | 托盘「检查更新…」→ 检查 → 下载 → minisign 验签 → 停自启运行时 → NSIS `passive` 安装；**只对 currentUser 安装版开放** |
| 更新清单 | ✅ | `scripts/updater_manifest.py` 把签名原文、版本、资产 URL 绑成 `latest.json`；拒绝签名版本漂移、HTTP 端点与 `-Machine` 变体 |
| 打包装配 | ✅ 非发行 | `portable_release.py --desktop-dir` 同时认 WPF `Demiurge.exe` 与 Tauri `demiurge-shell.exe`，包内一律落名 `Demiurge.exe`；2026-10-30 起便携包退出正式发行，只作本地调试 |
| NSIS 双安装器 | ✅ | `currentUser`（默认，不弹 UAC）+ `perMachine`（覆盖文件产出，需 UAC）；一套代码 `tauri build --config` 两次；装完包内 `--self-test` 18/18（2026-10-24 当时；P2 起 19 项） |
| 安装资源白名单 | ✅ 🔴 | `bundle.resources` 只允许 `installer-staging/dependencies → dependencies`；预设/userdata/`backend/data`/`.env` 有三层守卫（配置断言 → 暂存树审计 → 产物与安装目录扫描） |

## 🔴 WebView2 数据目录必须显式指定

本机实测（2026-10-23）：不指定时默认目录（exe 同目录 `<exe>.WebView2` / `%LOCALAPPDATA%\<identifier>`）
创建失败，错成 `Failed to setup app: 拒绝访问。 (os error 5)`，窗口根本不出现。所以这里一律
`.data_directory(<home>/webview2)`。诊断手法与完整证据见 `../README.md` 的 Tauri 小节。

## 安装版布局（2026-10-24，第一步）

便携包是「整目录可写」；安装版可能落在 `Program Files`（只读），所以三层必须拆开：

| 位置 | 内容 |
| --- | --- |
| 安装目录（`install_dir`） | `Demiurge.exe`（壳）、`dependencies\git\cmd`（MinGit）、可选 `dependencies\ffmpeg\bin` |
| `%LOCALAPPDATA%\Demiurge\runtime\` | `current.json`、`base\<base_id>\Demiurge-Runtime.exe`、`apps\`、`rag\` |
| `%LOCALAPPDATA%\Demiurge\userdata\` | 用户数据（`LAF_DATA_DIR`） |
| `%LOCALAPPDATA%\Demiurge\runtime.pid` | 运行时 pid（不再写安装目录） |

运行时识别顺序固定为 **便携包 → 仓库 → 安装版**：便携包看包根 `data/runtime/current.json`，安装版看
`%LOCALAPPDATA%\Demiurge\runtime\current.json`；仓库优先于安装版，保证「把 release exe 拷进仓库根」的开发流程
仍然用 `.venv`，不会误用本机装的那份运行时。安装版的 `current.json` 在运行时尚未部署时不存在，此时壳会报错并打印
期望路径，而不是在只读安装目录里乱写。

启动运行时前，壳把 `dependencies\git\cmd` 与 `dependencies\ffmpeg\bin`（存在才加）前置进子进程 `PATH`，
再拼接继承的 `PATH`。否则干净机器上没有系统 git，节点管理/`git` 能力会直接失效；前置也保证自带版本优先于系统版本。
便携包同样走这条注入（它的 MinGit 在包根 `dependencies\git`）。

## 安装器（2026-10-24，第二步）

一套代码出**两个 NSIS 安装器**：`currentUser`（默认，不弹 UAC）+ `perMachine`（可选，需 UAC）。
按定案**不用 `installMode: "both"`**——它一律提权，提权后 `%LOCALAPPDATA%` 与 HKCU 可能落到管理员 profile，
正好把第一步「可写状态全在用户目录」的前提打断。

| 落点 | 作用 |
| --- | --- |
| `tauri.conf.json > bundle`（顶层 `active/targets/icon/resources` + `windows.nsis`） | NSIS 基础档＝`currentUser`：`targets: ["nsis"]`、`compression: lzma`、`languages: ["SimpChinese","English"]`、`displayLanguageSelector: false`、`startMenuFolder: "Demiurge"`、安装/卸载图标、`bundleVCRuntime: true`；`resources` 只映射 `installer-staging/dependencies → dependencies` |
| 顶层 `mainBinaryName: "Demiurge"` | 🔴 安装目录里的主程序必须是 `Demiurge.exe`。该字段属于**顶层 Config**，写进 `bundle` 会被 schema 直接拒（实测 `Additional properties are not allowed ('mainBinaryName' was unexpected)`）；不写则装的是 cargo 产物名 `demiurge-shell.exe` |
| `installer/per-machine.conf.json` | perMachine 覆盖档，只改 `bundle.windows.nsis.installMode`；`tauri build --config <file>` 深合并产出第二个安装器（实测生成脚本 `!define INSTALLMODE "perMachine"` + `RequestExecutionLevel admin`） |
| `scripts/stage_installer_dependencies.py` | 按 `release/portable-windows.json` 的 URL+SHA256 取 MinGit，解到 `installer-staging/dependencies/git` 并写 `MANIFEST.json`；下载**带断点续传**，同时跑配置断言与暂存树审计 |
| `scripts/build_installers.py` | 暂存 → 两套构建 → `7z l` 扫产物清单 → currentUser 静默安装到 `.installer-work\smoke-install\app` → 跑包内 `--self-test` → 静默卸载后 `uninstall_residue` 复查**三处**残留（安装目录 / 开始菜单 `Demiurge` / `HKCU\...\Uninstall\Demiurge`） |

### 🔴 用户的预设不能进安装包

这不是约定，是三层能跑的守卫（回归：`backend/tests/test_installer_release.py`，23 例）：

1. **配置面**：`assert_installer_config()` 要求 `bundle.resources` **逐字等于**
   `{"installer-staging/dependencies": "dependencies"}`，另外钉住 `mainBinaryName == Demiurge`、
   `targets == ["nsis"]`、`nsis.installMode == currentUser`、`webviewInstallMode.type == embedBootstrapper`。
   有人把 `../../presets` 之类塞进资源表 ⇒ 构建直接拒。
2. **暂存树**：`audit_staging()` 只允许顶层 `dependencies/`，其下只允许 `git/` 与 `MANIFEST.json`；
   命中 `presets`/`userdata`/`backend/data`/`.env`/`user_state.json`/`repos.json`/`chat.json`/`desktop.json`/`runtime.pid`
   或符号链接、特殊文件 ⇒ 失败。暂存目录本身被 `desktop/tauri/.gitignore:/installer-staging` 忽略。
3. **产物与真装**：`7z l` 全清单扫禁止项；静默安装后再扫安装目录树。

实测（2026-10-24）：产物清单 385 项、安装目录 378 个文件，禁止项 **0 命中**；本机私有预设
（`presets/GrayWill-*.json`，本来就被 `.gitignore` 挡在源码发布外）根本不在暂存树里。

### WebView2 用 embedBootstrapper，不用 offlineInstaller

先按「依赖全打包」试过 `offlineInstaller`，拆包一看载荷 305.0 MiB 里
`$TEMP\MicrosoftEdgeWebView2RuntimeInstaller.exe` 独占 **204.9 MiB（67%）**，安装器 234.2 MiB ——
一个安装器的三分之二是系统组件，而且和路线第三步「小安装包 + 运行时按需交付」正对着干。
换成 `embedBootstrapper` 后两套安装器各 **28.6 MiB**（接入更新插件并带 `.exe.sig` 后为 **29.6 MiB**）；只在机器缺 WebView2 Evergreen 时需要联网补一次，
Win11 与更新过的 Win10 本来就有。要真做零联网交付，改这一处 `type` 即可（但要先解掉上面那条尺寸断言）。

### 构建与核验

```powershell
python scripts/build_installers.py                 # 暂存依赖 + 两套安装器 + 清单/安装/卸载核验
python scripts/build_installers.py --skip-stage    # 已确认暂存树干净时重跑
python scripts/build_installers.py --skip-machine --skip-smoke
python scripts/build_installers.py --version v0.15 # 覆盖 tauri.conf 版本并归一为 0.15.0（CI 用）
# 产物：release-output/installers/Demiurge-Setup-{User,Machine}-<version>.exe + SHA256SUMS.txt
# 配置了签名密钥时另有各自的 .exe.sig；latest.json 由 scripts/updater_manifest.py 生成
# 中间件：.installer-work/{downloads,nsis/<variant>/installer.nsi,smoke-install}
```

单独重放核验（脚本内同一段逻辑）：

```powershell
$evi = "$env:TEMP\demiurge-installer-evidence"
.\release-output\installers\Demiurge-Setup-User-0.1.0.exe /S "/D=$evi\app"
& "$evi\app\Demiurge.exe" --self-test      # 隔离 DEMIURGE_SHELL_HOME 后 18/18
& "$evi\app\dependencies\git\cmd\git.exe" --version
& "$evi\app\uninstall.exe" /S
```

### 本机踩坑（装一次就会遇到）

- **Tauri 首跑要从 GitHub 取工具链**：NSIS 本体能过，`nsis_tauri_utils.dll` 那步会 `failed to bundle project: timeout: global`。
  DLL 落点固定 `%LOCALAPPDATA%\tauri\NSIS\Plugins\x86-unicode\additional\nsis_tauri_utils.dll`，
  用镜像预置再复跑即可；放进去前对过 SHA1 `75197fee3c6a814fe035788d1c34ead39349b860`（与 tauri-bundler 常量一致）。
- **MinGit 直连会中途断**：`_download_mingit` 那种一次性 urllib 下载在本机 6 分钟只走 2 MB。
  暂存脚本自带 Range 续传 + 多源轮换，`release/portable-windows.json > mingit.mirrors` 增列了 npmmirror CDN
  （主源仍是 git-for-windows 官方，SHA256 不变 ⇒ 镜像只可能省时间，不可能换内容）。
- **生成的 `installer.nsi` 里 `RequestExecutionLevel user` 和 `admin` 两行都在**，靠 `!if "${INSTALLMODE}"` 选；
  按正则抓第一条 `RequestExecutionLevel` 会把 currentUser 误判成 perMachine。判安装模式要读 `!define INSTALLMODE`，
  再取该模式分支里的那一行。
- CLI 用 `npx -y @tauri-apps/cli@2` 即可（本机 `cargo install tauri-cli` 会被代理卡死）；Windows 下解析到的是 `npx.CMD`。
- **别按「卸载后注册表全空」写验收**：Tauri 模板把 `HKCU\Software\Demiurge\Demiurge`（默认值＝上次 `$INSTDIR`）的清理
  放进 `${If} $DeleteAppDataCheckboxState = 1 ${AndIf} $UpdateMode <> 1`，`/S` 读不到勾选框 ⇒ 这条键**必然留着**，
  不含用户数据。`uninstall_residue` 因此只查安装目录、开始菜单与 `...\Uninstall\Demiurge` 三处。

## 运行时按需交付（2026-10-24，第三步）

安装器只带壳 + MinGit，**不带运行时层**。首次启动时若 `%LOCALAPPDATA%\Demiurge\runtime\current.json`
指向的层不在盘上，壳按嵌入的交付 pin 补齐后重新 `build_launch()`。

| 件 | 职责 |
| --- | --- |
| `release/runtime-delivery.json` | 交付 pin：`tag`/`version`/`target`/`release_base`/`manifest`/`required_layers`/`optional_layers`/`mirrors`。用 `python scripts/runtime_delivery.py pin --tag v0.15 --repository <owner/repo>` 渲染，`validate` 自检 |
| `Demiurge_update_<平台>_<tag>.json` | 层清单（体积 + SHA256 + 分卷）**唯一真源**。pin 里不复制这些数字。层资产 `Demiurge_base/application/rag_<平台>_<tag>` 不带内容 ID，分卷名去掉归档扩展名（`..._v0.30.part01`） |
| `src/runtime_delivery.rs` | `include_str!` 嵌入 pin；校验 pin/清单；本地校验 → 在线续传 → 分卷组装 → 安全解压 → 原子部署 |

**两条入口**：

1. **在线**：`release_base` + 清单名拼 URL，`ureq` + 系统 TLS；分卷 Range 续传（206 追加、200 截断重写、
   416 视为已完成），失败按 `mirrors` 轮换，SHA256 不符删档重下。
2. **离线**（双目录）：安装目录 `runtime-pack\` 与 `%LOCALAPPDATA%\Demiurge\downloads\`。同一个资产名
   先在这两处找，校验通过就不再联网；`release_base` 不可达也能装完（自检第 17 项就是拿
   `file:///nonexistent-…` 证明的）。

**层策略**（2026-11-03 P3-a 改写）：必装层只有 `base + application`（≈208 MB，装上壳就能对话、建作品、走向量库检索），
`rag`（≈2 GB，torch / sentence-transformers 本地推理栈）是**可选层**——首次启动不再拉它，`current.json` 的 `rag_id` 留空。
2026-10-30「三层全部必装、`optional_layers` 已删除、清单缺 `rag` 层两侧判失败」的定案**已被推翻**：现在取层循环由 pin 驱动，
`runtime-delivery.json` 与 `scripts/runtime_delivery.py` 的 `REQUIRED_LAYERS`/`OPTIONAL_LAYERS` 是两侧唯一真源，
清单**可以没有** `rag` 层（有则照常校验分卷与逐卷续传）。未就绪时的功能级降级（P3-c）未做；
**后台补齐与进度可见已落（2026-11-03 P3-b）**，见下节。

**落盘顺序**（失败绝不写 `current.json`）：

```
校验 pin/清单 → 空间预检(size×3+256MiB) → 逐层：本地或下载 → 校验分卷 → 组装归档 → 校验归档
→ 解到 runtime/.staging-<token> → 改名部署（同 id 旧层挪进 .staging-<token>/stale，失败时放回）
→ 提交闸：候选层起在隔离目录 runtime/.gate-<token>（空 userdata + 现抢端口 + 去令牌）
   探 GET /api/health 的 status=="ok" 且 GET / 含 id="root"（240 s × 2 次；首装没有回退点 ⇒ 跳过）
→ 过闸才写 current.json（临时文件 + 改名替换；三层 id 变了顺带把上一份写进 previous.json 作回退点）
```

闸不过 ⇒ 放回旧层、只删本次新建的层、保留 `downloads\` 分件缓存，`current.json` 逐字不变。
失败只清理自己创建的 `.staging-*`、`.work-*`、`.gate-*`、坏档；`current.json` 保持原样（没有就仍然没有）。
磁盘不足的报错会直接列出 `runtime-pack` 与 `downloads` 两个离线目录。
启动侧还有第二道网：新版本起不来时 `rollback_to_previous()` 与 `previous.json` 两档互换后重试一次，
连上一版也起不来才给「只清运行时、保留作品数据」的安全模式（便携包与仓库布局不提供该选项）。
层目录最多留**两代**（当前 + 回退点），正常启动成功后回收更老的代际（24 小时护栏，`DEMIURGE_PRUNE_PREVIOUS=0` 关闭）。

**自动化边界**：`try_install_runtime` 只在**安装版布局**触发；便携包、仓库、非安装布局都不碰。
进度复用启动页既有文案通道（不是业务 IPC）。`--install-runtime` 在便携布局下拒绝执行（退出码 2），
避免把层写进包目录。

## RAG 后台补齐（2026-11-03，P3-b）

P3-a 把首启降到 ≈208 MB，这一节负责「那 2 GB 什么时候来、用户凭什么看得见、来了怎么接上」。
入口三条，出口一个：**进应用后问一次 → 拒绝则托盘「补齐 RAG」常驻 → 补齐成功只建议重启，绝不自动重启**。

| 件 | 职责 |
| --- | --- |
| `src/optional_layer.rs` | 编排：启动后那一次询问、托盘入口、单飞护栏、成功后的重启建议。不含交付逻辑 |
| `src/runtime_delivery.rs` `deliver_optional` / `backfill_optional_runtime` / `optional_layer_missing` | 只交付「清单真提供、盘上还缺」的可选层，与必装层同一具主体、**同一个提交闸** |
| `src/runtime_delivery.rs` `DeliveryLedger` | 进度账本**写入方**（唯一）：`userdata\delivery.json`，约每秒一次 tick，临时文件 + 改名 |
| `backend/app/services/shell_delivery.py` | 同一本账的**读取方**（唯一）：陈旧判定与宽容解析都在这，坏档返回 `None` 不抛 |
| `backend/app/services/activity_feed.py` `_runtime_delivery_items` | 投影成「后台活动」面板的 `runtime_delivery` 一条（标题「运行时补齐」），**前端零改动** |

- **为什么是文件而不是 IPC**：壳与后端此前只有启动时单向注入的 5 个环境变量，壳的进度只写在启动页文案里，
  进应用后就再也看不见。`userdata\delivery.json` 是两侧第一条跨进程通道，仍在「壳不承载业务」的合同内
  （它只装字节数与阶段名，登记在卸载分档的**缓存档**，卸载随缓存清）。
- **陈旧口径**：`running` 且 30 秒没更新 ⇒ 判死不再显示（壳崩掉时没人改终态，判据放读侧，写侧不需要
  「先收掉上次的僵尸账」）；`done` / `failed` 沿用面板统一的 120 秒保留期。速率由壳算（`speed_bps`）。
- **闸不许跳过**：可选层同样先探活再提交——torch 的 DLL 与 base 层 ABI 绑死，坏 `rag` 层的失败形状是
  **进程起不来**，不是少个功能。闸不过 ⇒ 保持原版本、`current.json` 逐字不变、账本写 `failed` 带拒因。
- **不自动重启**：后端读的是启动时注入的运行时目录，不重启接不上新层；但重启会打断正在进行的生成，
  所以只弹「RAG 组件已就绪 / 重启后端后生效，现在重启吗」，点头才调既有「重启后端」（自带外部复用拒绝等四条护栏）。
  不加设置项、不加启用开关。
- **只问一回**：拒绝（或这个版本的清单根本不提供 `rag`）才写 `desktop.json` 的 `OptionalLayerOfferDone`；
  接受但下载失败**不写**，下次启动照旧再问。开发态仓库布局读不到 `current.json` ⇒ 一句话都不会弹。
- **单飞**：`AppState.delivering` 期间托盘「重启后端」「检查更新」都拒绝（三者都改写 `current.json`）；
  退出不拦——分件落在 `downloads\` 且 Range 续传，中途关掉是接着下。

## 自动更新（2026-10-24，骨架 + 测试密钥）

`tauri-plugin-updater` 已接入，托盘「检查更新…」走完整链路：检查 → 用户确认 → 下载并验签 →
停止自启运行时 → 交给 NSIS 安装器（`passive`）→ 安装器重启新壳后重新拉起运行时。清单地址固定在
`https://github.com/Carmenangle/Demiurge/releases/latest/download/latest.json`，配置在
`tauri.conf.json > plugins.updater`。

**安全口径**（`assert_updater_config()` 机械守卫 + 回归测试）：

- `createUpdaterArtifacts: true`；`requireSignedVersion: true`，签名可信注释必须带
  `version:<x.y.z>`，拒绝「新版本号配旧包」的降级；
- `allowDowngrades` 与三个 `dangerous*` 传输旗标必须全部关闭；endpoints 必须 HTTPS；
- `windows.installMode: "passive"`：显示进度但不弹交互页。

**NSIS 更新载荷就是安装器 exe**（插件按 PE 头识别后直接执行），签名是 `<exe>.sig`；
`latest.json` 的 `signature` 字段就是该文件内容。`scripts/updater_manifest.py` 把
「安装器名 + 签名绑定的版本 + 清单版本」绑死：签名版本漂移、HTTP 端点、非 `.exe`、
`-Machine` 变体与路径穿越一律拒绝。`build_installers.py` 在检测到
`TAURI_SIGNING_PRIVATE_KEY(_PATH)` 时保留两套 `.exe.sig`；没有密钥时临时关闭
`createUpdaterArtifacts`，让本机与未配置 secret 的 CI 仍能出安装器。

**适用面**：只对 **currentUser 安装版**开放（判据：有安装标记 + 安装目录可写）。便携包、仓库开发壳与
**装在不可写目录的 perMachine** 会明确拒绝并提示手动下载——`latest.json` 指向 currentUser 安装器，
跑错变体会装出第二份而不是升级。⚠ 已知边界：perMachine 若被管理员**故意装到用户可写目录**，
壳无法仅凭可写性区分，仍会走 currentUser 更新；不建议这样安装。

**本机验证（2026-10-24）**：一次性测试密钥（私钥在
`%LOCALAPPDATA%\Demiurge\updater-test.key`，不进仓库；公钥在
`desktop/tauri/testdata/updater-test.pub`）签出两套安装器；独立 Ed25519/blake2b 校验器
（不经过 Tauri）对两个 exe 验签通过、绑定版本 0.1.0；`latest.json` 由真实产物生成，
`signature` 与 `.exe.sig` 逐字一致、URL 指向 `Demiurge-Setup-User-0.1.0.exe`；自检第 18 项用
仓库夹具覆盖「验签通过 / 篡改拒绝 / 版本错配拒绝」。

**未做**：生产密钥轮换（当前嵌入测试公钥，正式发布前必须替换）、Authenticode 代码签名、
真实下载与安装的端到端回放（需要先发布上一版本资产；本轮按定案不做 tag/Release）。CI 已预留
`TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`，未配置时跳过清单生成。

## 窗口几何与选择记忆（2026-10-24）

**几何单位固定为逻辑像素**：保存时 `inner_size`/`outer_position` 都除以 `scale_factor`，恢复时
`.inner_size()`/`.position()` 直接吃同一组逻辑值——存物理像素会在 150% 缩放下每次重启放大 1.5 倍，
尺寸取 inner、位置取 outer 才是同一口径（混用会让窗口每次开关长一圈边框）。最大化/全屏时**保留上一次
常态几何**，`Moved/Resized` 700ms 防抖落盘，托盘「彻底关闭」前再存一次。

`desktop.json` 新增 `Fullscreen` 与 `GeometryVersion: 2`（写文件时自动盖章）。没有版本号的旧文件按
「逻辑屏装不下、÷ 当前 DPI 后装得下」判定为旧 Tauri 写的物理值并一次性换算；WPF 写的逻辑值装得下，
不会被误换算。🔴 **读设置必须早于 Tauri 的 DPI 初始化**：`main()` 最前面就调
`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`，否则 150% 机器上读到的是虚拟化的
1707×960@96dpi，旧物理值会被误判成逻辑值、再被夹成贴满屏。

**上次仓库/作品**不归壳管：`user_state.selection={repoId,workId,savedAt}` 由前端写入/读取（localStorage
只是先手缓存），跨 WebView2 数据目录生效。壳只负责把页面指到同源地址；选择恢复失败属于前端/后端合同，
不要往壳里加业务 IPC。

## 构建与运行

```powershell
# 依赖：Rust stable + MSVC（VS Build Tools 的 VCTools 工作负载）
cd desktop/tauri
cargo build                 # 调试版带控制台，方便看输出
cargo build --release

# 自检（19 项：前 15 项与 WPF 壳同一份判据，第 16 项安装版布局，第 17 项运行时交付，第 18 项自动更新验签，第 19 项提交闸与回滚）
.\target\debug\demiurge-shell.exe --self-test

# 手动补运行时（安装版；base + application + rag 三层全装）
.\target\debug\demiurge-shell.exe --install-runtime

# 正常运行（在仓库内会自动用 backend\.venv 起开发运行时）
.\target\debug\demiurge-shell.exe

# 想双击就启动：把 release 产物拷到仓库根（.gitignore 已忽略该 exe）
copy desktop\tauri\target\release\Demiurge.exe Demiurge.exe
```

本机网络备忘：cargo 直连 crates.io 会被系统代理拦，需
`NO_PROXY='*' no_proxy='*'` + crates-io 走 rsproxy 稀疏索引（已写进用户级 `~/.cargo/config.toml`）。

## 关窗语义（2026-10-24 定案）

**关窗不是直接隐藏，而是询问三选一**（Win32 `MessageBoxW`，是 / 否 / 取消）：

- 是：隐藏到托盘，运行时继续跑（手机端要连就必须常驻）。
- 否：彻底关闭——先回收**自己拉起的**运行时，再退出壳；前端窗口随进程退出。
- 取消：留在当前窗口。

启动失败时例外：窗口停在错误页，关窗即退出（没有需要保活的东西）。

托盘菜单：显示主窗口 / 重启后端 / 补齐 RAG / 检查更新… / 彻底关闭。「重启后端」「彻底关闭」都只处理自启运行时；
端口上原本就有、且健康且带前端的服务属于外部复用，**永不回收**，重启项会弹窗明确拒绝。
WPF 壳保持旧行为（关窗即退出并回收），决策只在新壳落地。

端口被占用时的自救（2026-11-08）：启动链发现端口上不是「健康且带前端」的服务时（典型是被旧后端占着、
或那个后端没带 `LAF_FRONTEND_DIST`），先弹「Demiurge 启动被阻塞」——写明占用进程的映像名、PID、完整路径。
用户点「是」才结束它（`taskkill /T /F`）并等端口释放，然后继续正常启动；点「否」就停在失败页并附同样的占用信息。
目的：安装版没有 `stop-dev.bat` 时不被旧进程永久锁死。占用者不是已知 Demiurge 映像（`Demiurge.exe` /
`Demiurge-Runtime.exe` / 仓库 `backend\.venv` 的 python）时，弹窗额外警告一句，但杀不杀仍由用户决定；
连 PID 都识别不到（受保护进程）时只报错，不猜也不杀。

实跑证据（2026-10-24，debug + release）：

- 关窗弹 `#32770`「关闭 Demiurge」；选是 → 窗口隐藏、壳存活、8010 仍健康；
  选取消 → 窗口留存、8010 仍健康；选否 → 壳退出、后端进程树 3 秒内消失、8010 释放。
- 托盘菜单实测：显示主窗口生效；重启后端日志为
  「停止运行时 pid=… → 已启动运行时 pid=… → 后端健康检查通过 → 后端已重启并就绪，界面已重新载入」，
  新旧 PID 不同、窗口标题恢复 `Demiurge`；彻底关闭复用同一条回收路径。
- 外部复用拒绝：手动起外部后端后壳体日志「复用已在运行的后端」；触发重启 → 日志
  「重启后端被拒绝：当前后端是外部复用的服务」并弹警告；关掉警告后外部服务仍健康，
  托盘彻底关闭后外部进程继续存活。
- release 产物 `--self-test` 当时 15/15（自检扩容后为 18/18）；根 `Demiurge.exe` 启动、关窗选是、托盘彻底关闭均复跑通过。
- 端口占用清理（2026-11-08）：`cargo test --release` **20 例全绿**；其中 `port_holder_resolves_own_listener`
  用真实 `GetExtendedTcpTable` 把自己监听的临时端口解析回本进程 PID（同时验证 IPv4 表与映像路径可读），
  `cleanup_prompt_*` 三例锁住弹窗正文（PID/路径、外来进程警告、有无前端的不同措辞、拿不到 PID 时不给清理入口）。
  重建后的壳 `--self-test` **19/19**。⚠ 真机弹窗未在开发机上点过：用户当前的 8010 是「健康且带前端」的外部
  后端，走的是复用分支；要验清理路径得先制造一个不带前端的 8010（或在别的端口起一个占位进程，设 `DEMIURGE_PORT`）。

> Win11 提示：新托盘图标默认进「隐藏的图标」浮出层，壳无法强制提升；首次若看不到 Demiurge，
> 请在任务栏溢出区把它拖到常显位置。

## 与 WPF 壳对拍

```powershell
# 两套壳各跑一次，逐行 diff —— 谁的输出不一样就是谁没对齐
desktop\Demiurge.Desktop\bin\Release\net8.0-windows\win-x64\Demiurge.exe --self-test
desktop\tauri\target\release\Demiurge.exe --self-test
```

`--self-test` 的 stdout 只有 `PASS/FAIL <名字>` + `self-test: N passed, M failed`；
诊断信息（home 与工作目录）走 stderr，不参与比对。

**对拍口径（2026-10-24 起）**：只比前 15 行——WPF 输出 15 PASS + summary，Tauri 输出 18 PASS + summary；
第 16 项 `layout: installed runtime under home`、第 17 项 `delivery: runtime pack install` 与第 18 项
`updater: signature verify` 是 Tauri 独有的形态，WPF 没有。
所以整份 `diff` 必然差四行，不是漂移。比对时把 `DEMIURGE_SHELL_HOME` 指到同一个可写目录，否则两边会各读各的 `desktop.json`。

**2026-10-23 实跑结果：两套壳前 15 项逐行一致**；2026-10-24 安装版布局 + 运行时交付 + 自动更新验签落地后 Tauri 为 **18/18**。
注意 PowerShell/Git Bash 的换行口径不同：WPF 输出 CRLF、Tauri 输出 LF，裸 `diff` 会显示前 15 行「全不同」；
比对前先归一（Git Bash：`tr -d '\r'`，或只按行文本比），不要把这个当成判据漂移。

## 代码地图

- `src/paths.rs` — 本地数据布局、便携包/仓库/安装版识别、自带依赖目录（对应 WPF `ShellPaths.cs`）
- `src/settings.rs` — 窗口几何与端口持久化（对应 `ShellSettings.cs`）
- `src/runtime_host.rs` — 探活/复用/拉起/等就绪/回收 + 依赖 PATH 前置 + 端口占用者定位与清理（对应 `RuntimeHost.cs`；清理是 Tauri 侧新增形态）
- `src/job.rs` — Job 对象回收进程树（对应 `ChildProcessJob.cs`）
- `src/http_probe.rs` — 裸 TCP 的最小 HTTP GET（**不走代理**，对应 WPF `UseProxy=false`）
- `src/selftest.rs` — 19 项自检（前 15 项对应 `SelfTest.cs`，第 16 项安装版布局，第 17 项运行时交付，第 18 项自动更新验签，第 19 项提交闸与回滚）
- `src/runtime_delivery.rs` — pin/清单校验、在线续传、离线双入口、分卷组装、安全解压与原子部署；必装/可选两档共享同一具主体（`LayerScope`），`DeliveryLedger` 是 `userdata\delivery.json` 的唯一写入方
- `src/optional_layer.rs` — 可选层（RAG）后台补齐的编排：启动后那一次询问、托盘入口、单飞护栏、成功后只**建议**重启后端
- `src/updater.rs` — 托盘更新入口、安装版适用面判定、minisign 验签夹具（测试公钥在 `testdata/`）
- `scripts/updater_manifest.py` — `latest.json` 渲染与更新配置守卫（签名版本绑定、HTTPS、拒绝 `-Machine`）
- `Cargo.toml` — `ureq`（`native-tls` 特性）+ 直接依赖 `native-tls`（`Fetcher::new()` 里显式 `tls_connector` 接线，**漏接等于没有 TLS 后端**）+ `zip`（deflate）+ `sha2` + `tauri-plugin-updater`（含 `minisign-verify`）；`windows-sys` 供磁盘空间/Job 等 Win32 调用
- `src/shell_log.rs` — 壳日志与日志尾巴（对应 `ShellLog.cs`）
- `src/dialog.rs` — 关窗三选一与壳级提示（Win32 `MessageBoxW`，不引 Tauri dialog 插件）
