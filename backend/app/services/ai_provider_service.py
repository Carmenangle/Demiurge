from datetime import datetime, timezone
from urllib.parse import urljoin
from uuid import uuid4

import requests

from app.db import get_connection
from app.schemas.ai_provider import AIProviderCreate, AIProviderPublic, AIProviderUpdate


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_model_urls(base_url: str) -> list[str]:
    normalized = base_url.rstrip("/")
    for suffix in (
        "/chat/completions", "/images/generations", "/images/edits",
        "/video/generations", "/videos/generations", "/embeddings",
    ):
        if normalized.lower().endswith(suffix):
            normalized = normalized[:-len(suffix)].rstrip("/")
            break
    normalized += "/"
    if normalized.rstrip("/").endswith("/v1"):
        return [urljoin(normalized, "models")]
    return list(dict.fromkeys([
        urljoin(normalized, "v1/models"),
        urljoin(normalized, "models"),
    ]))


def discover_models(base_url: str, api_key: str = "") -> dict[str, object]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last_error = ""
    for url in candidate_model_urls(base_url):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if not response.ok:
                last_error = f"{url} returned {response.status_code}"
                continue
            payload = response.json()
            data = payload.get("data", payload if isinstance(payload, list) else [])
            models = []
            for item in data:
                if isinstance(item, str):
                    models.append(item)
                elif isinstance(item, dict) and item.get("id"):
                    models.append(str(item["id"]))
            return {"ok": True, "models": models, "source": url, "error": ""}
        except requests.RequestException as exc:
            last_error = str(exc)
        except ValueError as exc:
            last_error = f"invalid json: {exc}"

    return {"ok": False, "models": [], "source": "", "error": last_error or "model discovery failed"}


def row_to_provider(row, models: list[str]) -> AIProviderPublic:
    return AIProviderPublic(
        id=row["id"],
        name=row["name"],
        base_url=row["base_url"],
        provider_type=row["provider_type"],
        default_model=row["default_model"],
        enabled=bool(row["enabled"]),
        models=models,
    )


def get_provider_models(provider_id: str) -> list[str]:
    with get_connection() as connection:
        rows = connection.execute(
            "select model_name from ai_provider_models where provider_id = ? order by model_name",
            (provider_id,),
        ).fetchall()
    return [row["model_name"] for row in rows]


def list_providers() -> list[AIProviderPublic]:
    with get_connection() as connection:
        rows = connection.execute("select * from ai_providers order by created_at desc").fetchall()
    return [row_to_provider(row, get_provider_models(row["id"])) for row in rows]


def get_provider(provider_id: str) -> AIProviderPublic | None:
    with get_connection() as connection:
        row = connection.execute("select * from ai_providers where id = ?", (provider_id,)).fetchone()
    if row is None:
        return None
    return row_to_provider(row, get_provider_models(provider_id))


def replace_models(provider_id: str, models: list[str], source: str) -> None:
    created_at = now_iso()
    with get_connection() as connection:
        connection.execute("delete from ai_provider_models where provider_id = ? and source = ?", (provider_id, source))
        for model in models:
            if model.strip():
                connection.execute(
                    """
                    insert or ignore into ai_provider_models (id, provider_id, model_name, source, supports_image, created_at)
                    values (?, ?, ?, ?, null, ?)
                    """,
                    (str(uuid4()), provider_id, model.strip(), source, created_at),
                )


def create_provider(payload: AIProviderCreate) -> AIProviderPublic:
    provider_id = str(uuid4())
    created_at = now_iso()
    default_model = payload.default_model or (payload.models[0] if payload.models else "")
    with get_connection() as connection:
        connection.execute(
            """
            insert into ai_providers (id, name, provider_type, base_url, api_key, default_model, enabled, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider_id, payload.name, payload.provider_type, payload.base_url, payload.api_key, default_model, int(payload.enabled), created_at, created_at),
        )
    replace_models(provider_id, payload.models, "discovered")
    provider = get_provider(provider_id)
    if provider is None:
        raise RuntimeError("provider create failed")
    return provider


def update_provider(provider_id: str, payload: AIProviderUpdate) -> AIProviderPublic | None:
    updated_at = now_iso()
    default_model = payload.default_model or (payload.models[0] if payload.models else "")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            update ai_providers
            set name = ?, provider_type = ?, base_url = ?, api_key = ?, default_model = ?, enabled = ?, updated_at = ?
            where id = ?
            """,
            (payload.name, payload.provider_type, payload.base_url, payload.api_key, default_model, int(payload.enabled), updated_at, provider_id),
        )
        if cursor.rowcount == 0:
            return None
    replace_models(provider_id, payload.models, "discovered")
    return get_provider(provider_id)


def delete_provider(provider_id: str) -> bool:
    with get_connection() as connection:
        connection.execute("delete from ai_provider_models where provider_id = ?", (provider_id,))
        cursor = connection.execute("delete from ai_providers where id = ?", (provider_id,))
    return cursor.rowcount > 0


def add_manual_model(provider_id: str, model_name: str) -> bool:
    if get_provider(provider_id) is None:
        return False
    with get_connection() as connection:
        connection.execute(
            """
            insert or ignore into ai_provider_models (id, provider_id, model_name, source, supports_image, created_at)
            values (?, ?, ?, 'manual', null, ?)
            """,
            (str(uuid4()), provider_id, model_name.strip(), now_iso()),
        )
    return True
