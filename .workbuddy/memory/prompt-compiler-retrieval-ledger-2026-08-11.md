# Prompt 编译、检索分池与插画字段账本（2026-08-11）

- Trace Replay 的遵从度基线必须读取最终 `model.request`，区分本地未注入、位置错误、模型未遵从、上游拒答和后处理丢失；提示词完整送达只证明本地链路正确，不能保证上游策略接受内容。
- `prompt_compiler` 是最终消息位置属主。`provider_profile` 由设置经 AgentInvocation/RunContext 显式贯通；Roleplay 禁止按模型名猜 Claude。OpenAI 兼容档保留历史后 system，Claude 兼容档把它编译为贴近末轮 user 的本轮执行合同。Trace 保存 profile、最终 messages 与位置 manifest。
- 角色卡 `description/personality/scenario/mes_example` 必须分别填入 ST 对应 marker；只处理本轮实际选中角色，禁止把已绑定卡全量注入或把四字段折成 description。
- 世界书激活只扫描本轮输入与最近一组对话，防止已离场旧角色反复触发。表格读写分离：全部 full 表每轮可读，`fillEvery` 只控制写回；retrieval 行拥有独立候选池和预算，身份列精确优先，无嵌入时仍确定性召回。
- 自动纪要每个频率区间 append 一张独立卡；稳定展示编号为 `T<层级>-<rowid>`，回合范围不是卡 ID。主 Roleplay 最多注入 10 条当前出场角色相关短概览。
- 四种插画 Profile 统一产出外貌、当前服装、动作、地点、镜头、构图、光影、材质、质量字段账本。可验证事实缺失时只局部补齐，保留主模型已经合格的高潮和艺术决策；Profile 完成后才允许按实际 LoRA 元数据机械注入触发词和作者质量建议。
- 验证基线：后端 1269、前端 353，Ruff、17 条 import-linter、mypy 40 文件、硬编码检查和生产构建通过。
