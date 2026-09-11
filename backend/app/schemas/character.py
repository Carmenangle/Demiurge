from pydantic import BaseModel


class EmptyResponse(BaseModel):
    ok: bool = True
