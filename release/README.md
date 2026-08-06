# Demiurge Runtime 发布边界

终端用户下载 `USER-DOWNLOAD` 包，解压后直接启动，不需要安装 Python、Node.js、pip 或 npm。

- 仅发布 Full RAG：包含 Python Runtime、后端运行依赖、已构建前端、Chroma/BM25，以及当前平台的 Torch、Transformers 和 SentenceTransformers。
- 发布包不包含 Embedding/Reranker 权重、ComfyUI、用户 API 配置、RAG 索引、会话或生成图片。
- Windows 包含 `start-dev.bat`、`stop-dev.bat` 与 MinGit；macOS/Linux 包含可直接执行的启动脚本。
- ComfyUI 必须继续使用自己的 Python 环境，禁止复用 Demiurge Runtime。

发布文件由 `.github/workflows/runtime-release.yml` 在 `v*` 标签上构建并上传到 GitHub Release。大型 Full RAG 层按 1.9 GB 分片，并附 SHA256 清单。
