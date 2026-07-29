"""AI 路由共用：对话模型请求基类 + 建模/单轮对话封装（委托 services.llm）。
各 ai_* 子路由共享，避免 base_url/api_key/model 三元组与错误映射到处复制。
"""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel


class ChatModelReq(BaseModel):
    """对话模型三元组基类：所有需要调对话模型的请求继承它。"""
    base_url: str = ""             # OpenAI 兼容接口地址
    api_key: str = ""
    model: str = ""
    proxy: str = ""                # 外网模型走代理（前端从设置透传，空=本地直连）


class EmbedModelReq(ChatModelReq):
    """对话 + RAG 嵌入模型基类：需要检索知识库的请求继承它。"""
    embed_base_url: str = ""       # RAG 嵌入接口（可与对话不同家）
    embed_api_key: str = ""
    embed_model: str = "embedding-3"
    embed_mode: Literal["remote", "local"] = "remote"
    embed_model_dir: str = ""      # 可选：本地嵌入模型目录（不随发布包提供）
    reranker_model_dir: str = ""   # 可选：自定义 Cross-Encoder 目录；完整 RAG 版留空使用内置模型

    def embed_cfg(self):
        """收成 rag_backend.EmbedConfig 单一属主对象（避免三元组散着传）。"""
        from app.services.rag_backend import EmbedConfig
        return EmbedConfig(
            base_url=self.embed_base_url,
            api_key=self.embed_api_key,
            embed_model=self.embed_model,
            model_dir=self.embed_model_dir,
            reranker_dir=self.reranker_model_dir,
            mode=self.embed_mode,
        )


def build_chat_model(base_url: str, api_key: str, model: str,
                     temperature: float = 0.7, streaming: bool = False):
    """构建对话模型（委托 services.llm，把缺配置的 ValueError 包成 400）。"""
    from app.services import llm as _llm
    try:
        return _llm.build_model(base_url, api_key, model, temperature, streaming)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def chat(base_url: str, api_key: str, model: str, system: str, user: str,
         temperature: float = 0.7, proxy: str = "", retries: int = 2) -> str:
    """非流式单轮对话，返回回复文本（委托 services.llm，错误包成 4xx/5xx）。proxy 透传；retries 控重试次数。"""
    from app.services import llm as _llm
    try:
        return _llm.chat(base_url, api_key, model, system, user, temperature, proxy=proxy, retries=retries)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
