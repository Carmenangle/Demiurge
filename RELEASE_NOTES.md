# Demiurge

下载名称中带 `00-USER-DOWNLOAD` 的对应平台压缩包，解压后直接启动。

- Windows x64：运行 `start-dev.bat`，关闭时运行 `stop-dev.bat`。
- macOS ARM64：运行 `Start-Demiurge.command`。
- Linux x64：运行 `start-demiurge.sh`。
- 仅发布 Full RAG 版，包含本地 Embedding/Reranker 的运行依赖，但不包含模型权重。

模型权重、ComfyUI、角色卡、预设、会话、图片和 RAG 索引不随 Runtime 分发。
