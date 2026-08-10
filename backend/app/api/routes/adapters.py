from fastapi import APIRouter

from app.core.config import get_settings
from app.errors import AppError
from app.schemas.adapters import AdapterManifest
from app.services.adapter_registry import adapter_manifests

router = APIRouter(prefix="/adapters", tags=["adapters"])


@router.get("", response_model=list[AdapterManifest])
async def get_adapters() -> list[AdapterManifest]:
    return adapter_manifests(get_settings())


@router.get("/{adapter_id}/capabilities", response_model=AdapterManifest)
async def get_adapter_capabilities(adapter_id: str) -> AdapterManifest:
    manifests = adapter_manifests(get_settings())
    for manifest in manifests:
        if manifest.id == adapter_id:
            return manifest
    raise AppError("ADAPTER_NOT_FOUND", "适配器不存在", status_code=404)
