from pydantic import BaseModel, Field


class AIProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    provider_type: str = "openai_compatible"
    default_model: str = ""
    enabled: bool = True
    models: list[str] = Field(default_factory=list)


class AIProviderUpdate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    provider_type: str = "openai_compatible"
    default_model: str = ""
    enabled: bool = True
    models: list[str] = Field(default_factory=list)


class AIProviderPublic(BaseModel):
    id: str
    name: str
    base_url: str
    provider_type: str
    default_model: str = ""
    enabled: bool = True
    models: list[str] = Field(default_factory=list)


class DiscoverModelsRequest(BaseModel):
    base_url: str
    api_key: str = ""


class DiscoverModelsResponse(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    source: str = ""
    error: str = ""


class ManualModelCreate(BaseModel):
    model_name: str