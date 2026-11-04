"""Shipment creation: deterministic validation, the existing quotation engine, persistence.

The same service creates single and bulk shipments; batches only orchestrate it.
"""

import hashlib
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import Clock
from app.contracts.quotation import CotizacionRequest
from app.contracts.shipment_batches import EnvioLoteItem, ItemError
from app.modules.quotation.data import Coverage, TariffData
from app.modules.quotation.service import QuotationService
from app.shipments.domain.models import CreatedShipment, ShipmentDraft
from app.shipments.infrastructure.repositories import ShipmentRepository

# Quotation errors as item errors: same codes where they exist, the B2B code otherwise.
_QUOTE_ERRORS: dict[ErrorCode, ItemError] = {
    ErrorCode.RUTA_SIN_TARIFA: ItemError(code=ErrorCode.TARIFA_NO_DISPONIBLE, field="destino"),
    ErrorCode.SERVICIO_NO_DISPONIBLE: ItemError(
        code=ErrorCode.SERVICIO_NO_DISPONIBLE, field="requiere_cadena_frio"
    ),
}


def tariff_version(tariff_csv: Path) -> str:
    """Identifies the tariff matrix used for a price (content hash of the CSV)."""
    digest = hashlib.sha256(tariff_csv.read_bytes()).hexdigest()
    return f"{tariff_csv.name}@sha256:{digest[:16]}"


class ShipmentService:
    def __init__(
        self,
        tariffs: TariffData,
        quotation: QuotationService,
        *,
        tarifa_version: str,
        clock: Clock,
    ) -> None:
        self._tariffs = tariffs
        self._quotation = quotation
        self._tarifa_version = tarifa_version
        self._clock = clock

    def prepare(self, client_id: UUID, item: EnvioLoteItem) -> ShipmentDraft | list[ItemError]:
        """Validates cities, coverage and tariff, and quotes. Pure: no database access."""
        errors: list[ItemError] = []
        places: dict[str, Coverage] = {}
        for field in ("origen", "destino"):
            canonical = self._tariffs.canonical_city(getattr(item, field))
            place = self._tariffs.coverage_for(canonical) if canonical else None
            if canonical is None:
                errors.append(ItemError(code=ErrorCode.CIUDAD_NO_RECONOCIDA, field=field))
            elif place is None:
                errors.append(ItemError(code=ErrorCode.CIUDAD_NO_CUBIERTA, field=field))
            else:
                places[field] = place
        if errors:
            return errors

        request = CotizacionRequest.model_validate(
            item.model_dump(include=set(CotizacionRequest.model_fields))
            | {"origen": places["origen"].municipio, "destino": places["destino"].municipio}
        )
        try:
            quote = self._quotation.quote(request)
        except ContractError as error:
            mapped = _QUOTE_ERRORS.get(error.code)
            return [mapped or ItemError(code=error.code, field=None)]

        return ShipmentDraft(
            client_id=client_id,
            referencia_cliente=item.referencia_cliente,
            origen_codigo_dane=places["origen"].codigo_dane,
            destino_codigo_dane=places["destino"].codigo_dane,
            request=request,
            quote=quote,
            tarifa_version=self._tarifa_version,
            cotizado_en=self._clock.now(),
            recipient=item.destinatario,
        )

    async def create(
        self, session: AsyncSession, draft: ShipmentDraft, *, batch_item_id: UUID | None
    ) -> CreatedShipment:
        """Writes in the caller's transaction: guía, quote snapshot, shipment and recipient.

        Raises ``ContractError(REFERENCIA_DUPLICADA)`` when the reference is already used;
        the UNIQUE constraint stays the final guarantee under concurrency.
        """
        repo = ShipmentRepository(session)
        if await repo.reference_exists(draft.client_id, draft.referencia_cliente):
            raise ContractError(ErrorCode.REFERENCIA_DUPLICADA)
        numero_guia = await repo.next_guia()
        shipment_id = await repo.add(draft, numero_guia, batch_item_id, self._clock.now())
        return CreatedShipment(
            shipment_id=shipment_id,
            numero_guia=numero_guia,
            flete_total_cop=draft.quote.calculo.flete_total_cop,
            formula_version=draft.quote.formula_version,
        )
