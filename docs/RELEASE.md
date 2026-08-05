# 源码上传边界

## 必须上传

- `backend/app`、`frontend/src`、`comfyui-ext` 的源码。
- 后端与前端依赖清单、锁文件、测试和静态检查配置。
- `frontend/public` 中被界面引用的主题资产。
- `AGENTS.md`、`ARCHITECTURE.md`、根 README 与启动脚本。

## 可选上传

- `.github` 工作流、示例配置和不含用户数据的示例工作流。
- 经权利确认、清洗且明确用于演示的资产或预设。默认不从用户目录取样。

## 禁止上传

- API key、访问令牌、代理鉴权、私钥和真实设置导出。
- `backend/data`、`userdata`、未清洗的 `presets` 内容、`docs/memory`。
- 会话、角色卡、世界书、生成图片、作品数据库、Trace 和日志。
- Chroma/RAG 索引、Embedding/Reranker 权重、ComfyUI 模型和 LoRA。
- `.venv`、`node_modules`、`frontend/dist`、缓存、临时下载和发布归档。

## 流程

1. 运行 `scripts/release_preflight.py`。
2. 查看 `git status --short`，逐项确认未跟踪文件；禁止使用未经审计的全量暂存。
3. 运行后端和前端门禁以及 `git diff --check`。
4. 明确许可证与远程仓库可见性。
5. 用户确认后再提交；推送、标签和 Release 上传不由检查脚本执行。
6. 从确认后的提交生成源码包：`git archive --format=zip --output=Demiurge-<version>.zip HEAD`。
