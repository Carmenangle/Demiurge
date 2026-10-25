# Changelog

本文件记录 Demiurge 面向用户与发布流程的变更。格式参考 Keep a Changelog，版本号与发布 tag 对齐；
尚未发布的改动放在 `Unreleased` 下。

## [Unreleased]

### Added

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

### Security

- 发布前强制校验签名私钥与 `desktop/tauri/tauri.conf.json` 内嵌的 `plugins.updater.pubkey` 为同一把钥：
  构建前用私钥签固定挑战并比对 minisign key id，生成 `latest.json` 前再校验真实安装器 `.sig`。
  错配直接失败，避免客户端在「下载完成、验签」阶段才发现全量更新不可用。
