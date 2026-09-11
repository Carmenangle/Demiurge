# Demiurge 预设与正则资源包

## 内容

- `preset/GrayWill-0.46-demiurge.json`：Demiurge 当前适配预设，保留 33 条内嵌 ST 正则。
- `regex/GrayWill-0.46-embedded-regex.json`：上述 33 条正则的独立导出，供只导入正则时使用。
- `regex/Demiurge-global-regex.json`：Demiurge 当前使用的 24 条全局正则。

## 使用

在 Demiurge 中导入预设后，再按需要导入全局正则。预设内嵌正则导出主要用于 SillyTavern 或手工恢复；不要把内嵌正则和同名全局正则重复启用，否则文本可能被处理两次。

本包不包含角色卡专属正则、会话、世界书、图片、API 配置或原始 `GrayWill-0.46-ex` 参考文件。所有连接与鉴权字段已递归移除。

公开分发前仍需确认 GrayWill 预设及其正则的上游授权；清洗密钥不等于取得再分发许可。
