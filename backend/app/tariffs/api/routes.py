"""``POST /api/v1/cotizaciones``: same path, operation id and models as the contract (1.5.0)."""

from typing import cast

from fastapi import APIRouter, Request

from app.contracts.api_spec import API_PREFIX
from app.contracts.tariffs import CotizacionPerfilRequest, CotizacionPerfilResponse

router = APIRouter(prefix=API_PREFIX, tags=["cotización"])


@router.post("/cotizaciones", status_code=201, operation_id="createProfileQuotation")
async def create_profile_quotation(
    body: CotizacionPerfilRequest, request: Request
) -> CotizacionPerfilResponse:
    quote = await request.app.state.container.tariff_quotes.quote(body)
    return cast(CotizacionPerfilResponse, quote)
