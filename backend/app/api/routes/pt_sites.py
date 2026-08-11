from fastapi import APIRouter

from app.api.dependencies import PtCatalog, ViewerPrincipal
from app.schemas.adapters import PtSiteCatalogResponse

router = APIRouter(prefix="/pt-sites", tags=["pt-sites"])


@router.get("/catalog", response_model=PtSiteCatalogResponse)
async def get_pt_site_catalog(
    catalog: PtCatalog,
    _principal: ViewerPrincipal,
) -> PtSiteCatalogResponse:
    return catalog.public_response()
