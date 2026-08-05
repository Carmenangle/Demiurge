# Demiurge

Demiurge 是面向本地 ComfyUI 与 OpenAI 兼容模型的多智能体创作工作台。项目包含剧情会话、角色卡与世界书、RAG、状态表、工作流搭建、自动插画、模型和节点管理，以及本地文本工具。

## 本地启动

要求：Windows、Python 3.11、Node.js/npm，以及独立安装的 ComfyUI。

1. 在 `backend` 创建 `.venv`，安装 `requirements.txt`。
2. 在 `frontend` 运行 `npm ci`。
3. 运行根目录 `start-dev.bat`，浏览器打开 `http://127.0.0.1:5173`。
4. 在设置中填写 ComfyUI 路径、模型接口和本地仓库目录。

开发测试依赖位于 `backend/requirements-dev.txt`。项目默认只允许本机访问后端；不要在没有额外鉴权的情况下暴露到公网。

## 数据边界

以下内容只保存在本机，不属于源码，也不会进入发布包：

- `backend/data/`：API 配置、日志、任务状态、RAG 索引和数据库。
- `userdata/`：角色卡、世界书、会话、生成图片和作品仓库。
- `presets/`：默认排除用户导入的第三方预设及私有提示词；仅提交 `presets/Demiurge-presets-regex/` 中经脚本清洗的发布资源包。
- `docs/memory/`：本机 Agent 记忆、调试路径和测试记录。
- Python/Node 依赖、构建产物、ComfyUI 模型权重与缓存。

`frontend/public/` 是界面运行所需的主题资产，`comfyui-ext/` 是配套 ComfyUI 扩展源码，两者属于项目源码。

## 上传前检查

在项目根目录运行：

```powershell
$env:PYTHONUTF8 = "1"
backend\.venv\Scripts\python.exe scripts\release_preflight.py
git status --short
git diff --check
```

检查通过只代表候选内容未命中已知红线，不会自动暂存、提交或推送。源码归档应在确认提交后使用 `git archive HEAD` 生成，避免把本机未追踪数据打入压缩包。

## 许可证

当前仓库尚未选择开源许可证。在明确授权范围前，不应把项目标记为可自由复制、修改或再分发；第三方角色卡、预设、模型和 LoRA 始终遵循各自许可证。
