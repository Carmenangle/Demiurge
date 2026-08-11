# 自动插画提交耐久性与 PC 界面合同（2026-08-11）

## 故障证据与根因

- 最新回合并非没有调用 ComfyUI：Trace 先记录 `illustration.request`，随后记录 `illustration.submitted`；ComfyUI history 也包含同一 `prompt_id` 的成功输出。
- 提交端点过去只写 Trace，没有把 `prompt_id` 写回后端会话快照。页面刷新后，权威快照仍是无 `promptId` 的 pending 槽，前端把它当作“未提交孤儿槽”删除，所以用户看到像是从未生图。
- 修复后，提交成功必须经 `generation_store.persist_illustration_submission` 调用 `chat_snapshot.bind_media_slot_prompt`，按 `messageId + slotId` 持久化 `prompt_id`。前端加载快照时先用本地 `WorkflowGenerationRuntime` pending 恢复同目标 `promptId`，再删除真正的预提交孤儿槽。
- 本次已把 ComfyUI 实际完成的最新图片恢复到原消息媒体槽，并确认 generation 资产索引可按同一图片 URL 命中；不得新增一条图片消息，也不得因刷新复活已删除消息。
- 后续真实 Trace 又证明一次对话可同时提交旧消息槽和当前消息槽：旧实现先调用 ComfyUI，之后才用 `slot_bound=false` 发现旧目标无效，已经无法阻止额外任务。自动路径现在必须在任何 Profile、LoRA 元数据和 ComfyUI 工序前，经服务端原子认领 `messageId + slotId`；已认领、已提交、已完成或已删除槽直接拒绝。
- 前端重复收到同 `slotId` 的插画事件时，已有 pending 或 ready 图片/视频都不得再追加槽。服务端 `save_if_newer` 还要保留前端仍存在同槽的认领、promptId 与完成结果，防止旧完整快照清掉幂等状态；用户真正删除槽时仍尊重删除。

## PC 信息架构与交互合同

- 顶部选择父仓库后，首页展示该父仓库的全部子作品；只有一个子作品时自动进入它。选择具体作品直接进入对话。
- 未选择父仓库和作品时，首页展示按 `lastUsedAt` 排序的最近 3–5 个作品；不增加“继续最近作品”按钮。
- 仓库卡片仍双击打开；绑定、重命名、删除保持可见且单击可用。卡片辅助信息展示绑定角色、作品数、资产数和最后使用时间；资产数以 generation 索引为真源。
- 小仓库页保留返回大仓库列表的按钮。
- 对话顶部六个常用动作继续保持一键图标按钮；只降低边框、阴影和体量。输入区减少装饰线，快捷浮标降低视觉权重；不缩窄对话/用户详情主区域。
- 后台活动逐项展示真实任务，不把剧情生成和多个 ComfyUI 任务合并成一个数量摘要；一条对话正常应只有一条剧情活动和至多一个对应插画任务。名称使用“父仓库 · SAVE01”避免不同作品同名。后台活动与快捷工具圆球保持 56px 点击区。
- 设置模型页按对话、生图、视频、Embedding 四张状态卡组织；已有字段保留，高级配置默认折叠。
- 世界书等三栏页面用对称列宽让中间条目区居中；左侧主导航只增加项目间距，不放大按钮。
- 产品只承诺 PC：验证 1280×720、1920×1080、2560×1440 与 Windows 125%/150% 缩放等效视口无横向溢出。

## 回归保护

- 后端测试覆盖提交前原子认领、旧快照保留服务端媒体状态、删除不复活、提交端点写入 store 与 prompt_id 持久化。
- 前端测试覆盖重复事件不得在 ready 图片旁追加同名 pending 槽，以及认领 API 必须发生在 ComfyUI 提交前。
- Playwright 验证仓库、对话、世界书、模型设置在目标 PC 视口的无横向溢出；模型页必须恰有四张状态卡，且高级配置默认关闭。

最新门禁：后端 `1216 passed`；前端 `343 passed`；Playwright 会话恢复/后台/ComfyUI 原位回填 `3 passed`；生产构建、Ruff、mypy 39 文件、17 条 import-linter 与硬编码检查通过。

## 慢生图只留在 ComfyUI 的二次根因与修复

- 后台活动面板过去在 10 分钟后直接删除 `laf_pending_gen_*`，但 `WorkflowGenerationRuntime` 又以该记录存在作为 finalize 门闩。慢工作流完成后因此跳过原图落盘，图只留在 ComfyUI output。
- 后台活动现在只读 pending；生命周期只归 Runtime 拥有。展示层误清存储不再等于取消，只有用户显式停止才禁止迟到结果归档。
- pending 固化提交时的 `threadId/repoId/outputDir`，切换或重建同名仓库不得把旧任务写入当前仓库。原图或会话槽未真正持久化时不移除 pending，而是自动重试。
- 删除仓库前必须同时检查前端 ComfyUI pending 和后端 Agent 运行状态；任务在途时阻止删除。仓库暂时不在 user_state 时，`repo_meta` 仍按 `_repo.json.id` 找回原目录。
- 生成图仍直接落在 `<outputDir>/<父仓库>/<小仓库>/workflow_*.png`，与 `chat.json` / `_repo.json` 同级；不新建 `pictures/` 子目录。

本轮门禁：后端 `1254 passed`；前端 `351 passed`；生产构建、Ruff、mypy 39 文件、17 条 import-linter 和硬编码检查通过。
