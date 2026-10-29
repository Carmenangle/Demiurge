# Changelog

本文件记录 Demiurge 面向用户与发布流程的变更。格式参考 Keep a Changelog，版本号与发布 tag 对齐；
尚未发布的改动放在 `Unreleased` 下。

## [0.30.0] - 2026-10-30

首个「电脑端 + 手机端」同版发布。

### Added

- **Android 手机端（beta）**：`demiurge-spike-0.30.16-beta-debug.apk` —— WebView 壳内嵌 Python 后端，
  可独立运行；「设置 → 离线资源同步 → 联机同库」填入电脑地址与令牌后，手机与电脑读写同一份库，
  「联机检测」逐项体检链路；作品包导入/导出用于换机迁移。
- **云端 ComfyUI**：执行交给另一台机器，存档留在本地；地址簿按 A/B/C/D 四段配置
  （本机 / 局域网 / 云端 / Tailscale）。
- **提示词模版工作台**：自定义生图/视频提示词模版；协议族内置不可改，模版缺失按 kind 降级、不出空稿。
- **壁纸与场景**：内置 wallpaper-engine 场景播放器，场景来源泛化到本地工程；属性预设、鼠标视差、
  文字图层与 2D 粒子。
- **Windows 安装器**：同一份 Tauri 壳产出 `currentUser`（默认，不弹 UAC）与 `perMachine`（需 UAC）两套
  NSIS 安装器，支持自定义安装目录、WebView2 引导与内置 MinGit；预设、`userdata`、`backend/data` 和 `.env`
  由配置断言、暂存树白名单、产物扫描三层机械守卫拦在安装资源之外。
- **自动更新**：托盘「检查更新…」读取 `latest.json`，minisign 验签后以 NSIS `passive` 模式安装；仅对
  `currentUser` 安装版开放，便携包、仓库开发壳与 `perMachine` 安装明确拒绝。清单生成器守卫签名版本绑定、
  HTTPS 端点与 `-Machine` 变体。
- **运行时按需交付**：安装版首次启动按壳内嵌 pin 补齐 `base + application` 运行时层，支持在线 Range 续传
  与离线导入；`rag` 仅由 `--install-runtime --with-rag` 显式追加。层先解压到暂存目录再原子部署，
  `current.json` 最后写入，失败不留半套状态。
- **发布校验和信任链**：每次发布产出 `SHA256SUMS.txt`、其 minisign 签名 `SHA256SUMS.txt.sig` 与公钥
  `SHA256SUMS.pub`，与自动更新共用同一把钥匙；README 补充 SmartScreen 引导、`certutil` 哈希对照与
  `minisign -V` 严格验签方法。
- **壳交互**：关窗三选一（隐藏到托盘 / 彻底关闭 / 取消）；托盘新增「重启后端」「检查更新…」；
  新增 `--install-runtime` 无界面部署入口与高 DPI 感知。

### Changed

- 发布附件改为直白命名 `Demiurge_<种类>_<平台>_<版本>`（层资产 `Demiurge_base/application/rag_windows_v0.30`、
  清单 `Demiurge_update_windows_v0.30.json`、分卷 `Demiurge_rag_windows_v0.30.part01`），不再把内容 ID 写进文件名；
  ID 仍完整保存在更新清单里。桌面壳裸包不再随正式 Release 分发。
- 手机端版式收口：弹窗接入浏览器式返回链（返回键关最上层弹窗）、软键盘让位、浮动按钮收进 Dock、
  横屏形态与设置页头重排；画布等重活改为「PC 端可用」说明卡，不再给白框。
- 全量门禁输出统一 UTF-8，并对不在快照面的非 `.py` 文件给出可见提示。

### Fixed

- **安装版首次启动无法下载运行时**：桌面壳的 HTTPS 请求没有可用 TLS 后端——`ureq` 的 `native-tls` 特性只拉
  依赖、不会成为默认后端，壳又没有在 `AgentBuilder` 上挂 `tls_connector`，于是首次启动补齐运行时与
  `--install-runtime` 一律报 `cannot make HTTPS request because no TLS backend is configured`。现显式接上
  系统 TLS（schannel，不引 OpenSSL），并补回归测试 `https_requests_reach_a_tls_backend`：先让本地 TCP 接住
  再断开，逼出真实的 TLS 握手；端口关闭时 TCP 先失败，那样的用例测不出差别（已验证去掉接线后此测试必然变红）。
- 资产库切到没有素材的分类（视频／音频）时工具栏整条消失，连「全部／图片」都点不回去；
  空分类现在保留完整工具栏与分类按钮，空态文案按当前分类给出。
- 剧情对话里已生成的插画会被旧窗口的过期快照抹掉：服务端合并层把已落定媒体视为不可回滚，
  快照漏报的槽按原段位插回，只有整条消息消失才算删除；渲染层再补一层孤儿槽兜底。
- 壁纸场景渲染贴近 WE 原始画面：雨丝改用 spritetrail 粒子、REFRACT 折射按对象序、实时场景不再叠加暗化。
- 联机同库断线时保持未水合，电脑恢复后再拉真值；空密钥不再覆盖盘上非空值（密钥清单收敛单一属主）。
- 素材搜索链路：代理探活失败降级直连（PC 的 Clash 地址进手机不再拖死搜索）。

### Security

- 发布前强制校验签名私钥与 `desktop/tauri/tauri.conf.json` 内嵌的 `plugins.updater.pubkey` 为同一把钥：
  构建前用私钥签固定挑战并比对 minisign key id，生成 `latest.json` 前再校验真实安装器 `.sig`。
  错配直接失败，避免客户端在「下载完成、验签」阶段才发现全量更新不可用。
- 入站 Origin/Host 闸与出站建连 IP 钉扎；本地媒体与远程图片代理统一 `nosniff` + svg `CSP: sandbox`。
- 上传面文本层脱敏（用户私有作品/角色专名改等长占位）；发布预检按被忽略的真实专名表反查上传面，
  命中即拦。
