from fastapi import APIRouter, HTTPException

from app.schemas.ai_provider import (
    AIProviderCreate,
    AIProviderPublic,
    AIProviderUpdate,
    DiscoverModelsRequest,
    DiscoverModelsResponse,
    ManualModelCreate,
)
from app.services import ai_provider_service

router = APIRouter()


@router.get("/", response_model=list[AIProviderPublic])
def list_providers() -> list[AIProviderPublic]:
    return ai_provider_service.list_providers()


@router.post("/", response_model=AIProviderPublic)
def create_provider(payload: AIProviderCreate) -> AIProviderPublic:
    return ai_provider_service.create_provider(payload)


@router.get("/{provider_id}", response_model=AIProviderPublic)
def get_provider(provider_id: str) -> AIProviderPublic:
    provider = ai_provider_service.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return provider


@router.put("/{provider_id}", response_model=AIProviderPublic)
def update_provider(provider_id: str, payload: AIProviderUpdate) -> AIProviderPublic:
    provider = ai_provider_service.update_provider(provider_id, payload)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return provider


@router.delete("/{provider_id}")
def delete_provider(provider_id: str) -> dict[str, bool]:
    return {"ok": ai_provider_service.delete_provider(provider_id)}


@router.post("/discover-models", response_model=DiscoverModelsResponse)
def discover_provider_models(payload: DiscoverModelsRequest) -> DiscoverModelsResponse:
    result = ai_provider_service.discover_models(payload.base_url, payload.api_key)
    return DiscoverModelsResponse(**result)


@router.post("/{provider_id}/test")
def test_provider(provider_id: str) -> dict[str, object]:
    provider = ai_provider_service.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    result = ai_provider_service.discover_models(provider.base_url, "")
    return {"provider_id": provider_id, "ok": result["ok"], "message": result.get("error", "")}


@router.post("/{provider_id}/models")
def add_manual_model(provider_id: str, payload: ManualModelCreate) -> dict[str, object]:
    ok = ai_provider_service.add_manual_model(provider_id, payload.model_name)
    if not ok:
        raise HTTPException(status_code=404, detail="provider not found")
    return {"ok": True, "provider_id": provider_id, "model_name": payload.model_name}