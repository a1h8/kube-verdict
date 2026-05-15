from fastapi import APIRouter
from api.models import HealthResponse

router = APIRouter()

@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse()
