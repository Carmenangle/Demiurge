"""技能扩展端点：列表/保存。前端设置页「技能扩展」用。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import skills_store

router = APIRouter()


class Skill(BaseModel):
    id: str = ""
    name: str
    enabled: bool = True
    prompt_fragment: str = ""


@router.get("")
def list_skills() -> list[Skill]:
    return [Skill(**s) for s in skills_store.load_skills()]


@router.post("")
def save_skills(skills: list[Skill]) -> list[Skill]:
    saved = skills_store.save_skills([s.model_dump() for s in skills])
    return [Skill(**s) for s in saved]


# ===== Smithery 技能市场：浏览/一键添加 =====

def _skey() -> str:
    import json
    from app.config import DATA_DIR
    p = DATA_DIR / "user_state.json"
    if p.is_file():
        try:
            return (json.loads(p.read_text(encoding="utf-8")).get("settings") or {}).get("smitheryKey", "") or ""
        except Exception:
            pass
    return ""


@router.get("/smithery/search")
def smithery_search(q: str = "", page: int = 1, page_size: int = 20) -> dict:
    """搜索 Smithery 技能市场。registry 直连可达，不走代理。"""
    from app.services import smithery_client
    return smithery_client.search_skills(q, page, page_size, api_key=_skey())


class AddSkillRequest(BaseModel):
    namespace: str
    slug: str
    display_name: str = ""


@router.post("/smithery/add")
def smithery_add(req: AddSkillRequest) -> dict:
    """取技能详情的 prompt 存为本地技能。返回 {skills, dependsServers:[名字]}——
    若该技能依赖 MCP 服务器，dependsServers 供前端提示用户一并添加。"""
    from app.services import smithery_client
    d = smithery_client.get_skill(req.namespace, req.slug, api_key=_skey())
    if not d.get("ok") or not d.get("skill"):
        return {"ok": False, "skills": [], "dependsServers": [], "error": d.get("error", "获取技能失败")}
    sk = d["skill"]
    prompt = (sk.get("prompt") or "").strip()
    name = req.display_name or sk.get("displayName") or f"{req.namespace}/{req.slug}"
    if not prompt:
        return {"ok": False, "skills": [], "dependsServers": [], "error": "该技能无提示词内容"}
    skills = skills_store.load_skills()
    skills.append({"name": name, "enabled": True, "prompt_fragment": prompt})
    saved = skills_store.save_skills(skills)
    # 依赖的 MCP 服务器：字符串或对象列表都兼容，抽出 qualifiedName
    depends = []
    for s in (sk.get("servers") or []):
        if isinstance(s, str):
            depends.append(s)
        elif isinstance(s, dict):
            depends.append(s.get("qualifiedName") or s.get("displayName") or "")
    depends = [x for x in depends if x]
    return {"ok": True, "skills": saved, "dependsServers": depends, "error": ""}
