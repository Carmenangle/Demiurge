from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def list_runs() -> dict[str, object]:
    return {"items": []}
