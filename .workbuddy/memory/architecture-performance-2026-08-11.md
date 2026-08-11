# 架构与性能深化（2026-08-11）

- 新增根 `CONTEXT.md` 作为领域语言真源，AGENTS 先读 CONTEXT/ARCHITECTURE/memory。
- Structured Runtime 新增统一 `invoke`：native 成功零 legacy，失败最多一次 legacy；Supervisor 真实调用接入并统一 Trace。
- Scenario manifest v2：staging 原子发布、SQLite 显式关闭、未变化媒体复用 SHA、fork/多分支失败回滚文件与 Chroma；前端编排进入 `scenarioBranchRuntime`。
- RAG 重试/批量导入下沉 `rag_store`；纪要 replace import 变成单 SQLite 事务，失败保留旧数据。
- import-linter 13→17；后端 1212、前端 338、Playwright 3，Ruff/mypy39/硬编码/wire/build 全绿。
- 当前架构评分 9.1/10。OpenCC 516 KiB gzip 为按需 chunk，不进首屏，不用改变转换语义换警告消失。
- 完整证据见 `docs/memory/architecture-performance-2026-08-11.md`。
