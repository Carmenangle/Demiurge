# 结构化输出、资产语义检索与时序账本（2026-08-10）

- 供应链先做小型硬化：下载按任务隔离 `.part`、拒绝覆盖、记录 SHA-256/来源/格式风险；Smithery 外部技能默认关闭；角色卡/技能提示词标低权限来源。它不是完整 Artifact Trust。
- `structured_output` 已统一 Supervisor、纪要、手动填表的 Pydantic 校验，工作流两处复用统一 JSON 解析；真实 Trace 证明 `legacy_text` 成功和坏 JSON 失败能被识别。生产模型能力 wire 尚未贯通，因此原生 JSON Schema 仍未实际启用，Trace 也尚未覆盖全部迁移点。
- `trace_replay` 第一版只离线复验记录和事件不变量，无模型/存储/ComfyUI 副作用。真实仓库 2 个回合得到 1 通过、1 失败，并指出失败回合缺 `turn.completed`；它不是全链重执行。
- generation 索引分离 `prompt` 与 `description`。专用 Hybrid 搜索已从真实 10 张资产返回结果，不进剧情 RAG、不回灌聊天；但这些资产尚无 VLM 描述，部分旧 prompt 已乱码，当前收益主要来自英文 tags/未损坏文本。
- Qwen3-VL-Embedding-2B 固定官方提交 `9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda`，4,255,140,312 字节 safetensors 的 SHA-256 已实测匹配 `c73fa9caeddeb3ff831d46c085a7a5708343248ca777e90f2d486964464509c1`。图片向量使用独立 collection；当前尚未建索引，GPU 仅余约 1.3 GiB，未强行加载以免影响 ComfyUI，所以视觉向量检索尚无实际收益。
- `temporal_fact_store` 已接 Chronicle 写入与查询 API：只显式 supersede，不猜替代；冲突并列。角色好感/态度/心情/所在/身体/衣着仍由 `character_state` 持有。现有仓库尚无账本数据库，主 Roleplay 尚未召回账本事实，当前叙事影响为零。

## 后续验收顺序

1. 先修复/重建历史 generation 的乱码文本，并用现有 VLM 批量补 `description`。
2. 释放足够显存后构建视觉索引，用同一组自然语言查询对比文本 Hybrid 与文本＋视觉 RRF。
3. Structured Runtime 先贯通显式 provider capability 与全迁移点 Trace，再谈原生 Schema 成功率和重试下降。
4. Replay 增加版本化脱敏 fixture；Temporal Ledger 等真实事实积累后再做只读召回，不得抢占 `character_state` 真源。
