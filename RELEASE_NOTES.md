# Demiurge

本地优先的多智能体交互叙事与 ComfyUI 创作工作台——剧情对话、角色/世界书、自动图文音、
画布创作、AI 搭工作流，全部跑在你自己的机器上，数据不出本机。

## 怎么装

### Windows · 安装版（推荐）

| 包 | 适用 |
|---|---|
| `Demiurge-Setup-Machine-<版本>.exe` | 机器级：需要管理员，装进 `Program Files`，本机所有用户可用 |
| `Demiurge-Setup-User-<版本>.exe` | 用户级：**不需要管理员**，装进当前用户目录，可自选安装路径 |

- 壳是 Tauri 2（Rust），**不再需要 .NET 8 Desktop Runtime**；只依赖 Microsoft Edge WebView2
  Runtime（Win10/11 多数已自带，安装版缺失时会静默补装）。
- **程序与数据分离**：程序体在安装目录，作品、预设、日志、下载与运行时一律在
  `%LOCALAPPDATA%\Demiurge`。卸载/升级不动你的数据；你的角色卡与对话也不会被写进安装目录。
- 首次启动按交付清单**在线下载**运行时（base + application 两层），启动页逐条可见进度。
  Full RAG 层是可选增强，需要时执行 `Demiurge.exe --install-runtime --with-rag` 追加。
  离线机器：把 `runtime-pack\` 放到安装目录，或把下载件放进
  `%LOCALAPPDATA%\Demiurge\downloads\`，再启动即可离线安装。

### Windows · 便携版

`Demiurge_windows_<版本>.7z` —— 解压即用，运行时已内嵌，数据留在包内 `data\userdata\`。
同样是 Tauri 2 壳（不需要 .NET 8，只需 WebView2 Runtime）。
双击 `Demiurge.exe` 启动；`install-shortcut.bat` 生成桌面/开始菜单快捷方式；
仍想用浏览器就 `start-dev.bat`，关闭用 `stop-dev.bat`（与原生窗口同端口，二者只能跑一个）。

### macOS / Linux · 便携版（浏览器界面，无原生壳）

- `Demiurge_macos_<版本>.tar.gz` → 运行 `Start-Demiurge.command`
- `Demiurge_linux_<版本>.7z.001`…（分卷）→ 先合并再运行 `start-demiurge.sh`
- 分卷运行时资产用包内 `join-runtime.ps1` / `join-runtime.sh` 合并（校验 `parts.json` 里的 SHA-256）。

## 这一版有什么

**桌面化**：WPF 壳换成 Tauri 2 壳（`Demiurge.exe --self-test` 共 18 项自检，前 15 项与 WPF 壳
逐条对齐）；托盘常驻、关窗可隐藏到托盘、第二实例自动唤出；窗口几何/最大化/全屏与后端端口都会
记住（含高 DPI 换算）；NSIS 机器级与用户级双安装器；运行时改为按需交付（在线下载 + 离线双
入口）；自动更新骨架与签名信任链。

**壁纸与场景**：内置 wallpaper-engine 场景播放器，场景来源泛化到本地工程；壁纸库扫描与工坊
封面；文字图层（竖排日期/星期、问候语、帧率）、2D 粒子、云层与光雾参数；视频纹理改由渲染
循环逐帧重采（修「重载后壁纸不动」）；鼠标视差。

**资产库**：资产主表 + 两段式反推/tag-text；均列瀑布流 + 无限滚动 + 分页直达；图站市场与
「素材落盘即资产记录」；WD14 可插拔打标与 VLM 修复；缩略图派生层；后台活动面板统一为「任务
注册表 + 六类外部长操作只读投影」（下载/节点安装更新/LoRA 同步/后置维护/表格整理/ComfyUI
守护；反推、批量描述、生图与在途任务都在注册表一侧）。

**剧情与上下文**：世界书召回白名单准入 + 绿灯决策通道 + 重复闸；小说增量定位
（`index`/`diff`，更新版不再整本重读）；累积型字段长度治理与填表代理掩码；状态表/叙事分页
下推；外貌提取去锚点化 + 第一人称身高差机位；流式请求可中断取消 + usage 统计；灵感卡多图
按序插入。

**ComfyUI**：托管进程看门狗自动重启、提交接单宽限、断流不再静默；UI 工作流子图展平；
自定义节点「保存图片（序号续排）」与画布图片预览。

**安全与门禁**：入站 Origin/Host 闸；出站建连 IP 钉扎（关 DNS rebinding 时间窗）；本地媒体
与远程图片代理统一 `nosniff` + svg `CSP: sandbox`（防同源脚本直航）；前端设置不再把密钥写进
localStorage；应用页 CSP 单一属主；CI 十二道门禁（后端 9 道 + 前端 3 道，含依赖漏洞审计与
UTF-8 口径）。

## 关于「检查更新」

托盘里的「检查更新」需要仓库配置 updater 签名密钥后才会产出更新清单（`latest.json`）。
该密钥尚未配置时，请手动下载新版安装包升级。

## 不包含什么

模型权重、ComfyUI 本体与自定义节点、角色卡、预设、会话、图片与 RAG 索引**都不随包分发**。
安装器与便携包都有守卫：用户预设与运行态数据不会被卷进发布产物。
