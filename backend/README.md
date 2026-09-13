# Backend

本地 FastAPI 后端骨架。

## 职责

1. 管理工作流模板。
2. 解析和写回 ComfyUI workflow JSON。
3. 调用本地 ComfyUI API。
4. 管理图片资产、角色、LoRA 和生成历史。
5. 通过 LangChain 做提示词增强和参数提取。

## 计划启动命令

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```
## AI 供应商与模型发现

`/api/ai/providers/discover-models` 会根据 URL 和 Key 尝试调用 OpenAI 兼容模型列表接口。失败时前端仍可保存手动模型名。