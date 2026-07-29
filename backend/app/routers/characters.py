from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def list_characters() -> dict[str, object]:
    return {"items": []}
